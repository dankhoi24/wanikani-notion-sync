"""Per-subject WaniKani inventory and review counter arithmetic.

GET /reviews is deprecated (always empty), so review totals are calculated
from changes in cumulative review_statistics across scheduled syncs.
"""
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta

TYPES = ("radical", "kanji", "vocabulary", "kana_vocabulary")
DEFAULT_INTERVAL = {1:14400, 2:28800, 3:82800, 4:169200}

def dt(value):
    return datetime.fromisoformat(value.replace("Z","+00:00")) if value else None

def subjects_by_id(rows):
    return {x["id"]:dict(x["data"], type=x["object"]) for x in rows
            if x.get("object") in TYPES and not x.get("data",{}).get("hidden_at")
            and isinstance(x.get("data",{}).get("level"),int)}

def assignments_by_id(rows):
    return {x["data"]["subject_id"]:x["data"] for x in rows
            if x.get("data",{}).get("subject_id") is not None
            and not x.get("data",{}).get("hidden")}

def passed(a):
    return bool(a and (a.get("passed_at") or (a.get("srs_stage") or 0)>=5))

def reviews_to_guru(a):
    if passed(a):
        return 0
    return max(0,5-max(1,(a or {}).get("srs_stage") or 0))

def inventory(subjects, assigned, current, last):
    levels=defaultdict(list)
    for sid,s in subjects.items():
        if current<=s["level"]<=last:
            levels[s["level"]].append((sid,s))
    result=[]
    for level in range(current,last+1):
        entries=levels[level]
        totals=Counter(s["type"] for sid,s in entries)
        started=Counter()
        available=Counter()
        guru=0
        for sid,s in entries:
            a=assigned.get(sid,{})
            if a.get("started_at"):
                started[s["type"]]+=1
            elif a.get("unlocked_at"):
                available[s["type"]]+=1
            if s["type"]=="kanji" and passed(a):
                guru+=1
        needed=math.ceil(totals["kanji"]*0.9) if totals["kanji"] else 0
        left=max(0,needed-guru)
        candidates=sorted(((0 if assigned.get(sid,{}).get("started_at") else 1,
                            reviews_to_guru(assigned.get(sid)),sid,s)
                           for sid,s in entries if s["type"]=="kanji" and not passed(assigned.get(sid))),
                          key=lambda x:(x[0],x[1],x[2]))[:left]
        prereq=set()
        for _,_,sid,s in candidates:
            for rid in s.get("component_subject_ids",[]):
                if (subjects.get(rid,{}).get("type")=="radical" and not passed(assigned.get(rid))):
                    prereq.add(rid)
        gate_lessons=(sum(not assigned.get(sid,{}).get("started_at") for _,_,sid,_ in candidates)
                      +sum(not assigned.get(rid,{}).get("started_at") for rid in prereq))
        min_reviews=sum(reviews for _,reviews,sid,s in candidates)+sum(reviews_to_guru(assigned.get(rid)) for rid in prereq)
        all_total=sum(totals.values())
        all_started=sum(started.values())
        all_available=sum(available.values())
        result.append({
            "Level":level, "Radicals Total":totals["radical"],
            "Kanji Total":totals["kanji"],
            "Vocab Total":totals["vocabulary"]+totals["kana_vocabulary"],
            "Lessons Total":all_total,"Lessons Started":all_started,
            "Lessons Remaining":all_total-all_started,"Lessons Available":all_available,
            "Lessons Locked":all_total-all_started-all_available,
            "Radicals Remaining":totals["radical"]-started["radical"],
            "Kanji Lessons Remaining":totals["kanji"]-started["kanji"],
            "Vocab Lessons Remaining":totals["vocabulary"]+totals["kana_vocabulary"]-started["vocabulary"]-started["kana_vocabulary"],
            "Kanji Guru Passed":guru,"Kanji Guru Required":needed,"Kanji To Guru":left,
            "Gate Lessons Remaining":gate_lessons,"Min Reviews to Gate":min_reviews})
    return result

def milestone_inventory(levels,current,target):
    scope=[x for x in levels if current<=x["Level"]<target]
    first=scope[0] if scope else None
    return {"Remaining Lessons (Full)":sum(x["Lessons Remaining"] for x in scope),
            "Gate Lessons Remaining":sum(x["Gate Lessons Remaining"] for x in scope),
            "Kanji To Guru Next":first["Kanji To Guru"] if first else 0,
            "Min Reviews Next Gate":first["Min Reviews to Gate"] if first else 0,
            "Next Gate Passed":first["Kanji Guru Passed"] if first else 0,
            "Next Gate Required":first["Kanji Guru Required"] if first else 0}

def intervals_from_srs(rows):
    units={"milliseconds":.001,"seconds":1,"minutes":60,"hours":3600,"days":86400,"weeks":604800}
    result={}
    for x in rows:
        result[x["id"]]={d["position"]:round(d["interval"]*units[d["interval_unit"]])
                         for d in x.get("data",{}).get("stages",[])
                         if d.get("interval") is not None and d.get("interval_unit") in units}
    return result

def earliest_next_gate(subjects,assigned,level,now,intervals=None):
    """Optimistic bound: earliest time 90% of Kanji could reach Guru if all
    lessons/reviews are done immediately and perfectly. Not a forecast."""
    intervals=intervals or {}
    memo={}
    def reach(sid,visiting):
        if sid in memo:
            return memo[sid]
        if sid in visiting or sid not in subjects:
            return None
        a=assigned.get(sid,{})
        if passed(a):
            return now
        s=subjects[sid]
        start=now
        if not a.get("unlocked_at"):
            deps=[reach(r,visiting|{sid}) for r in s.get("component_subject_ids",[])]
            if any(x is None for x in deps):
                return None
            if deps:
                start=max(start,max(deps))
        stage=a.get("srs_stage") or 0
        if stage>=5:
            return now
        delays=intervals.get(s.get("spaced_repetition_system_id")) or DEFAULT_INTERVAL
        if stage==0:
            start+=timedelta(seconds=delays.get(1,DEFAULT_INTERVAL[1]))
            stage=1
        else:
            start=max(start,dt(a.get("available_at")) or start)
        for position in range(stage+1,5):
            start+=timedelta(seconds=delays.get(position,DEFAULT_INTERVAL.get(position,86400)))
        memo[sid]=start
        return start
    ks=[sid for sid,s in subjects.items() if s["level"]==level and s["type"]=="kanji"]
    if not ks:
        return None
    times=sorted(t for sid in ks if (t:=reach(sid,set())) is not None)
    gate=math.ceil(len(ks)*0.9)
    return times[gate-1] if len(times)>=gate else None

def review_counters(rows):
    keys=("meaning_correct","meaning_incorrect","reading_correct","reading_incorrect")
    return {key:sum(int(x.get("data",{}).get(key) or 0) for x in rows) for key in keys}

def review_delta(current,base,previous):
    since={key:current[key]-base[key] for key in current}
    interval={key:current[key]-previous[key] for key in current}
    if min(list(since.values())+list(interval.values()))<0:
        return {"Reviews Since Baseline":None,"Reviews Since Last Sync":None,
                "Meaning Accuracy New":None,"Reading Accuracy New":None,
                "Review Accuracy New":None,"Counter Valid":False}
    meaning=since["meaning_correct"]+since["meaning_incorrect"]
    reading=since["reading_correct"]+since["reading_incorrect"]
    total=meaning+reading
    return {"Reviews Since Baseline":since["meaning_correct"],
            "Reviews Since Last Sync":interval["meaning_correct"],
            "Meaning Accuracy New":round(100*since["meaning_correct"]/meaning,2) if meaning else None,
            "Reading Accuracy New":round(100*since["reading_correct"]/reading,2) if reading else None,
            "Review Accuracy New":round(100*(since["meaning_correct"]+since["reading_correct"])/total,2) if total else None,
            "Counter Valid":True}
