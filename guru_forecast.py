"""Review-accuracy-adjusted WaniKani Guru projections for the CURRENT level.

Simulate Kanji reviews on each item's assignment.available_at schedule.
Sample a Kanji item review as correct only when BOTH meaning and reading
are correct. Rates are inferred from cumulative kanji-only review statistics,
with per-item smoothing to the user's overall Kanji history.

Outcomes are scenario percentiles (10/50/90), NOT calibrated confidence
intervals. Assumptions: on-time reviews; independent meaning/reading errors;
a failed Apprentice review drops one SRS stage; no multiple mistakes in a
single review; future accuracy matches historical Kanji responses.
"""
import math
import random
from collections import Counter
from datetime import timedelta
from wk_detailed import dt, passed, DEFAULT_INTERVAL


def accuracy_profile(subjects, review_stats, level, prior_weight=12):
    """Collect kanji-only meaning and reading data (NEVER Vocabulary).

    All-level history serves as an informative prior for each individual
    Kanji, since newest Kanji have few review answers.
    """
    per_item={}
    all_count=Counter()
    level_count=Counter()
    for item in review_stats:
        data=item.get("data", {})
        if data.get("subject_type")!="kanji" or data.get("hidden"):
            continue
        sid=data.get("subject_id")
        subj=subjects.get(sid)
        if not subj or subj.get("type")!="kanji":
            continue
        vals=tuple(int(data.get(k) or 0) for k in (
            "meaning_correct","meaning_incorrect","reading_correct","reading_incorrect"))
        per_item[sid]=vals
        for key,val in zip(("mc","mi","rc","ri"),vals):
            all_count[key]+=val
            if subj.get("level")==level:
                level_count[key]+=val
    def fraction(data,prefix):
        correct=data[prefix+"c"]
        attempts=correct+data[prefix+"i"]
        return 100*correct/attempts if attempts else None
    m=fraction(all_count,"m")
    r=fraction(all_count,"r")
    m_level=fraction(level_count,"m")
    r_level=fraction(level_count,"r")
    attempts=sum(all_count.values())
    return {
       "Kanji Meaning Accuracy":round(m,2) if m is not None else None,
       "Kanji Reading Accuracy":round(r,2) if r is not None else None,
       "Kanji Level Meaning Accuracy":round(m_level,2) if m_level is not None else None,
       "Kanji Level Reading Accuracy":round(r_level,2) if r_level is not None else None,
       "Kanji Accuracy Answers":attempts,
       "Kanji Level Accuracy Answers":sum(level_count.values()),
       "per_item":per_item,
       "global_m":m/100 if m is not None else None,
       "global_r":r/100 if r is not None else None,
       "prior_weight":prior_weight
    }


def item_success_probability(sid, profile):
    """Estimated chance of an all-correct Kanji review (both question types)."""
    gm,gr=profile["global_m"],profile["global_r"]
    if gm is None or gr is None:
        return None
    mc,mi,rc,ri=profile["per_item"].get(sid,(0,0,0,0))
    w=profile["prior_weight"]
    pm=(mc+w*gm)/(mc+mi+w)
    pr=(rc+w*gr)/(rc+ri+w)
    return pm*pr


def guru_scenarios(subjects, assigned, review_stats, level, now, intervals=None,
                   trials=420, seed=20261021):
    """Return on-time correct-review probability scenario quantiles for 90/100% Guru."""
    intervals=intervals or {}
    profile=accuracy_profile(subjects,review_stats,level)
    ks=sorted((sid,s) for sid,s in subjects.items()
              if s.get("type")=="kanji" and s.get("level")==level)
    result={k:None for k in (
        "Gate P10","Gate P50","Gate P90",
        "All P10","All P50","All P90",
        "Kanji Review Success Pct")}
    result.update({k:profile[k] for k in (
        "Kanji Meaning Accuracy","Kanji Reading Accuracy",
        "Kanji Level Meaning Accuracy","Kanji Level Reading Accuracy",
        "Kanji Accuracy Answers","Kanji Level Accuracy Answers")})
    result["Kanji Count"]=len(ks)
    result["Guru Passed"]=sum(passed(assigned.get(sid)) for sid,_ in ks)
    result["Guru Required"]=math.ceil(.9*len(ks)) if ks else 0
    result["Simulation Valid"]=False
    result["Simulation Note"]="Insufficient review accuracy or current SRS schedules."
    if not ks:
        result["Simulation Note"]="No Kanji subjects in this level."
        return result
    gm,gr=profile["global_m"],profile["global_r"]
    if gm is None or gr is None or profile["Kanji Accuracy Answers"]<20:
        return result
    result["Kanji Review Success Pct"]=round(100*gm*gr,2)
    active=[]
    for sid,s in ks:
        a=assigned.get(sid)
        if passed(a):
            continue
        if not a:
            result["Simulation Note"]="At least one Kanji has no assignment."
            return result
        stage=a.get("srs_stage") or 0
        delay=intervals.get(s.get("spaced_repetition_system_id")) or DEFAULT_INTERVAL
        if stage==0:
            if not a.get("unlocked_at"):
                result["Simulation Note"]="Some current-level Kanji remain locked."
                return result
            # Assumes a newly available lesson is started immediately.
            start=now+timedelta(seconds=delay.get(1,DEFAULT_INTERVAL[1]))
            stage=1
        elif 1<=stage<=4:
            at=dt(a.get("available_at"))
            if at is None:
                result["Simulation Note"]="Missing a Kanji next-review time."
                return result
            start=max(now,at)
        else:
            result["Simulation Note"]="Unexpected Kanji SRS stage."
            return result
        p=item_success_probability(sid,profile)
        if p is None or p<=0:
            return result
        active.append((stage,start,delay,p))
    if not active:
        for name in ("Gate P10","Gate P50","Gate P90","All P10","All P50","All P90"):
            result[name]=now
        result["Simulation Valid"]=True
        result["Simulation Note"]="Kanji Guru conditions already achieved."
        return result
    rng=random.Random(seed)
    gates=[]
    all_dates=[]
    for trial in range(trials):
        passed_dates=[now]*(len(ks)-len(active))
        for start_stage,start_at,delay,p in active:
            stage,when=start_stage,start_at
            for _ in range(240):
                if rng.random()<p:
                    stage+=1
                else:
                    stage=max(1,stage-1)
                if stage>=5:
                    passed_dates.append(when)
                    break
                when+=timedelta(seconds=delay.get(stage,DEFAULT_INTERVAL.get(stage,86400)))
            else:
                result["Simulation Note"]="Exceeded review simulation horizon."
                return result
        passed_dates.sort()
        gates.append(passed_dates[result["Guru Required"]-1])
        all_dates.append(passed_dates[-1])
    def quantiles(arr):
        arr.sort()
        return arr[round(.1*(trials-1))],arr[round(.5*(trials-1))],arr[round(.9*(trials-1))]
    (result["Gate P10"],result["Gate P50"],result["Gate P90"])=quantiles(gates)
    (result["All P10"],result["All P50"],result["All P90"])=quantiles(all_dates)
    result["Simulation Valid"]=True
    result["Simulation Note"]=(
       "Conditional scenarios P10/P50/P90: all reviews done on time, "
       "Kanji lifetime meaning x reading accuracy (with per-item smoothing), "
       "one-stage Apprentice penalty after a failed session; "
       "assumes no unlock delays beyond current assignments. Not a guarantee.")
    return result
