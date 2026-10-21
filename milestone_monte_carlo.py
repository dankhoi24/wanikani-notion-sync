"""WaniKani multi-level stochastic review simulation.

Derived observations: current SRS stages, assignment.available_at,
subject component dependencies, API SRS intervals, review_statistics,
observed lessons already started today, configured lesson cap.

Monte Carlo future review outcomes: sample number of wrong answers before
answering the meaning/reading correctly, using subject-specific empirical
accuracy when adequately measured and a same-type empirical fallback for
new subjects. WaniKani SRS incorrect penalty is ceil(errors / 2) times 1
below Guru, 2 at/above Guru (per WaniKani SRS documentation).

THE SIMULATION IS CONDITIONAL, NOT AN ACTUAL ETA:
reviews are completed immediately at their due time; the user immediately
starts unlocked lessons up to their configured daily maximum; future kanji
performance is exchangeable with historical kanji performance; all future
items follow the API subject graph. Current review due times are facts;
everything after the next review is modeled, not an observed schedule.
P10/P50/P90 quantify only stochastic answer-outcome variation under these
conditions, not a calibrated likelihood of the actual completion date.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, time, timezone
from heapq import heappop, heappush
from math import ceil
import random
from zoneinfo import ZoneInfo
from wk_detailed import dt, passed

TYPES=("radical","kanji","vocabulary","kana_vocabulary")
CRITICAL=("radical","kanji")


def empirical_accuracy(subjects,stats):
    """Return evidence per subject type and per subject; never mix vocab into kanji."""
    sums=defaultdict(Counter)
    individual={}
    for entry in stats:
        d=entry.get("data",{})
        sid=d.get("subject_id")
        subj=subjects.get(sid)
        typ=d.get("subject_type")
        if d.get("hidden") or not subj or subj.get("type")!=typ:
            continue
        if typ not in TYPES:
            continue
        vals={}
        for q in ("meaning","reading"):
            # Radical reviews have no reading question in WaniKani.
            vals[q]=((0,0) if typ=="radical" and q=="reading" else
                     (int(d.get(q+"_correct") or 0),
                      int(d.get(q+"_incorrect") or 0)))
            sums[typ][q+"c"]+=vals[q][0]
            sums[typ][q+"i"]+=vals[q][1]
        individual[sid]=vals
    global_rates={}
    samples={}
    for typ in CRITICAL:
        c=sums[typ]
        m_total=c["meaningc"]+c["meaningi"]
        r_total=c["readingc"]+c["readingi"]
        # Radical has meaning only, Kanji must have BOTH question types.
        if m_total<20 or (typ=="kanji" and r_total<20):
            global_rates[typ]=None
        else:
            global_rates[typ]=(c["meaningc"]/m_total,
                               c["readingc"]/r_total if r_total else None)
        samples[typ]={"meaning":m_total,"reading":r_total}
    return global_rates,individual,samples


def item_rates(sid,typ,global_rates,individual):
    base=global_rates[typ]
    if base is None:
        return None
    values=individual.get(sid,{})
    # At least 10 actual attempts for a question before trusting its local
    # frequency; otherwise use observed same-type history, not a fake 100%.
    rates=[]
    for i,q in enumerate(("meaning","reading")):
        if typ=="radical" and q=="reading":
            rates.append(None)
            continue
        c,w=values.get(q,(0,0))
        rates.append(c/(c+w) if c+w>=10 else base[i])
    if any(p is not None and not (0<p<=1) for p in rates):
        return None
    return tuple(rates)


def next_hour(when):
    """API uses top-of-hour review availability after intervals."""
    utc=when.astimezone(timezone.utc)
    top=utc.replace(minute=0,second=0,microsecond=0)
    return top if utc==top else top+timedelta(hours=1)


def wrong_answers(rng,p):
    """Failures before the first correct answer, i.e. geometric trials."""
    wrong=0
    while rng.random()>p:
        wrong+=1
        if wrong>=30:
            return 30
    return wrong


def stage_after_review(stage,typ,rates,rng):
    """Apply documented WaniKani mistake-stage formula per review session."""
    m,r=rates
    errors=wrong_answers(rng,m)
    if typ=="kanji":
        errors+=wrong_answers(rng,r)
    return min(5,stage+1) if not errors else max(
        1,stage-ceil(errors/2)*(2 if stage>=5 else 1))


def _simulate_once(subjects,assigned,current_level,targets,now,tz,limit,
                   lessons_today,intervals,item_p,rng,max_days):
    """Event-driven SRS simulation; returns gate and full dates for each target."""
    max_target=max(targets)
    scoped={sid:s for sid,s in subjects.items()
            if current_level<=s.get("level",0)<max_target
            and s.get("type") in TYPES}
    level_ids=defaultdict(list)
    level_kanji=defaultdict(list)
    dependents=defaultdict(list)
    all_passed={sid for sid,a in assigned.items() if passed(a)}
    local_passed=set(all_passed)
    started={sid for sid in scoped if assigned.get(sid,{}).get("started_at")}
    stages={sid:(assigned.get(sid,{}).get("srs_stage") or 0)
            for sid in scoped}
    pending={}
    for sid,s in scoped.items():
        lvl=s["level"]
        level_ids[lvl].append(sid)
        if s["type"]=="kanji":
            level_kanji[lvl].append(sid)
        dep=[p for p in s.get("component_subject_ids",[])
             if p not in local_passed]
        pending[sid]=len(dep)
        for parent in dep:
            dependents[parent].append(sid)
    required={l:ceil(.9*len(level_kanji[l])) for l in range(current_level,max_target)}
    if any(x==0 for x in required.values()):
        raise ValueError("Missing Kanji at a future milestone level")
    gate_pcount=Counter()
    for sid,s in scoped.items():
        if s["type"]=="kanji" and sid in local_passed:
            gate_pcount[s["level"]]+=1
    remaining_full={target:sum(
        sid not in started for sid,s in scoped.items() if s["level"]<target)
        for target in targets}
    level=current_level
    now=now.astimezone(timezone.utc)
    t=now
    end=now+timedelta(days=max_days)
    events=[]
    priority={"radical":0,"kanji":1,"vocabulary":2,"kana_vocabulary":3}
    queue=[]
    queued=set()
    seq=0
    lessons_count=Counter({now.astimezone(tz).date():max(0,int(lessons_today))})
    gate_dates={}
    full_dates={}

    def enqueue(sid):
        if (sid not in started and sid not in queued and
            scoped[sid]["level"]<=level and pending[sid]==0):
            s=scoped[sid]
            # Prefer clearing older-level lessons before taking on a newer
            # level; inside the same level, Radical/Kanji are gate-critical.
            # This is an EXPLICIT study-order scenario, not observed behavior.
            heappush(queue,(s["level"],priority[s["type"]],sid))
            queued.add(sid)

    def activate_level(l):
        for sid in level_ids[l]:
            enqueue(sid)

    def pass_subject(sid):
        if sid in local_passed:
            return
        local_passed.add(sid)
        if scoped[sid]["type"]=="kanji":
            gate_pcount[scoped[sid]["level"]]+=1
        for child in dependents.get(sid,()):
            pending[child]-=1
            if pending[child]==0:
                enqueue(child)

    def ascend():
        nonlocal level
        while level<max_target and gate_pcount[level]>=required[level]:
            level+=1
            if level in targets and level not in gate_dates:
                gate_dates[level]=t
            if level<max_target:
                activate_level(level)

    def check_full():
        for target in targets:
            if (target not in full_dates and level>=target and
                remaining_full[target]==0):
                full_dates[target]=t

    def start_subject(sid):
        nonlocal seq
        started.add(sid)
        lessons_count[t.astimezone(tz).date()]+=1
        s=scoped[sid]
        for target in targets:
            if s["level"]<target:
                remaining_full[target]-=1
        if s["type"] not in CRITICAL:
            return
        stages[sid]=1
        seq+=1
        nxt=next_hour(t+timedelta(
            seconds=intervals[s["spaced_repetition_system_id"]][1]))
        heappush(events,(nxt,seq,sid))

    # Current assignments are authoritative for their *next* due time.
    for sid in started:
        s=scoped[sid]
        if s["type"] not in CRITICAL or sid in local_passed:
            continue
        if stages[sid] not in (1,2,3,4):
            raise ValueError("Started item has unusable SRS stage")
        nxt=dt(assigned[sid].get("available_at"))
        if nxt is None:
            raise ValueError("Active item missing real available_at")
        seq+=1
        heappush(events,(max(now,nxt),seq,sid))
    activate_level(level)
    ascend()
    check_full()
    for _ in range(210000):
        if t>end:
            raise ValueError("Simulation horizon exceeded")
        # Scheduled reviews (at time t) are processed before new lessons.
        while events and events[0][0]<=t:
            _,_,sid=heappop(events)
            if sid in local_passed:
                continue
            stage=stages[sid]
            new_stage=stage_after_review(
                stage,scoped[sid]["type"],item_p[sid],rng)
            stages[sid]=new_stage
            if new_stage>=5:
                pass_subject(sid)
            else:
                seq+=1
                nxt=next_hour(t+timedelta(
                    seconds=intervals[scoped[sid]["spaced_repetition_system_id"]][new_stage]))
                heappush(events,(nxt,seq,sid))
        ascend()
        check_full()
        if len(gate_dates)==len(targets) and len(full_dates)==len(targets):
            return {"gate":gate_dates,"full":full_dates,
                    "new_lessons":sum(lessons_count.values())-int(lessons_today)}
        day=t.astimezone(tz).date()
        available=max(0,int(limit)-lessons_count[day])
        while available and queue:
            _,_,sid=heappop(queue)
            queued.discard(sid)
            if sid in started or scoped[sid]["level"]>level or pending[sid]:
                continue
            start_subject(sid)
            available-=1
        check_full()
        if len(gate_dates)==len(targets) and len(full_dates)==len(targets):
            return {"gate":gate_dates,"full":full_dates,
                    "new_lessons":sum(lessons_count.values())-int(lessons_today)}
        future=[events[0][0]] if events else []
        if queue and not available:
            midnight=datetime.combine(day+timedelta(days=1),time.min,
                                      tzinfo=tz).astimezone(timezone.utc)
            future.append(midnight)
        if not future:
            raise ValueError("No scheduled reviews or unlocked lessons remain")
        t=min(q for q in future if q>t)
    raise ValueError("Simulation iteration cap exceeded")


def simulate_milestones(subjects,assigned,review_stats,current_level,targets,
                        now,timezone_name,lesson_limit,intervals,lessons_today=0,
                        trials=160,seed=261021):
    """P10/P50/P90 dates for joint review-error and unlock SRS simulation."""
    targets=tuple(sorted(set(targets)))
    result={"valid":False,"trials":trials,"targets":{},
            "reason":None,"conditions":(
                "Simulation conditional on ALL reviews being completed at due time "
                "and unlocked lessons started immediately, capped per VN day at "
                f"{lesson_limit}; Radical then Kanji then Vocabulary priority. "
                "Wrong-answer counts sampled from per-item empirical question "
                "accuracy (>=10 samples), otherwise observed same-type history; "
                "WaniKani documented SRS error penalties and hourly boundaries. "
                "Older WaniKani levels are studied before newer levels, with "
                "Radical/Kanji prioritized within each level. "
                "Outcomes P10/P50/P90 describe model variation, NOT empirically "
                "calibrated probabilities for future attendance/difficulty.")}
    if not targets or any(x<=current_level for x in targets):
        result["reason"]="Milestone must be above current WaniKani level."
        return result
    global_rates,individual,samples=empirical_accuracy(subjects,review_stats)
    result["accuracy_samples"]=samples
    result["accuracy_rates"]=global_rates
    if any(global_rates[t] is None for t in CRITICAL):
        result["reason"]="Too little real Radical or Kanji review-statistic accuracy data."
        return result
    tz=ZoneInfo(timezone_name)
    max_target=max(targets)
    critical={sid:s for sid,s in subjects.items()
              if current_level<=s.get("level",0)<max_target
              and s.get("type") in CRITICAL and not passed(assigned.get(sid))}
    item_p={}
    for sid,s in critical.items():
        interval=intervals.get(s.get("spaced_repetition_system_id"))
        if not interval or any(i not in interval for i in (1,2,3,4)):
            result["reason"]="Missing a complete API-provided SRS review interval."
            return result
        p=item_rates(sid,s["type"],global_rates,individual)
        if p is None:
            result["reason"]="Unavailable Kanji or Radical answer accuracy."
            return result
        item_p[sid]=p
    rng=random.Random(seed)
    observed={t:{"gate":[],"full":[]} for t in targets}
    for run in range(trials):
        try:
            result_run=_simulate_once(
                subjects,assigned,current_level,targets,now,tz,lesson_limit,
                lessons_today,intervals,item_p,rng,2400)
        except ValueError as err:
            result["reason"]=f"Scenario #{run+1} cannot complete: {err}"
            return result
        for target in targets:
            observed[target]["gate"].append(result_run["gate"][target])
            observed[target]["full"].append(result_run["full"][target])
    def quantiles(values):
        values.sort()
        return {"P10":values[round(.1*(trials-1))],
                "P50":values[round(.5*(trials-1))],
                "P90":values[round(.9*(trials-1))]}
    result["targets"]={t:{k:quantiles(v) for k,v in pairs.items()}
                       for t,pairs in observed.items()}
    result["valid"]=True
    return result
