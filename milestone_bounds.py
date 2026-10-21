"""Milestone *earliest feasible* timelines from the WaniKani subject graph.

This deliberately DOES NOT predict actual completion dates. It models a
conditional lower-bound scenario:
 - currently due reviews use assignments.available_at (per-item real timestamps)
 - future reviews use each subject's API-provided SRS stages/intervals
 - ALL future reviews are answered correctly and performed exactly when due
 - lessons are started when unlocked, subject to the configured daily cap
 - Radical/Kanji take priority for gate milestones; vocabulary is NOT gate work
 - unlocked subjects are derived from the real subject component graph
 - level transitions require ceil(90% of the level's Kanji) to have passed.

All unobserved future study behavior is a CONDITION, not observed fact.
This simulation is never labeled "Actual ETA" or "realistic forecast".
"""
from collections import Counter
from datetime import datetime, timedelta, time, timezone
from heapq import heappop, heappush
from math import ceil
from zoneinfo import ZoneInfo
from wk_detailed import passed, dt


def conditional_earliest(subjects, assigned, current_level, target_level,
                         now, timezone_name, lesson_limit, intervals,
                         lessons_today=0, include_vocab=False,
                         max_days=2300):
    """Return {'at': aware datetime | None, 'status': str, 'lessons': int}.

    Unlike a guessed 7-day level floor, each prerequisite review and daily
    lesson capacity is simulated with the actual published SRS intervals.
    """
    if target_level<=current_level:
        return {"at":now,"status":"Already at or beyond the target WK level.",
                "lessons":0}
    if lesson_limit<=0:
        return {"at":None,"status":"Invalid configured daily lesson limit.",
                "lessons":0}
    tz=ZoneInfo(timezone_name)
    relevant={sid:s for sid,s in subjects.items()
              if isinstance(s.get("level"),int) and s["level"]<target_level
              and s.get("type") in ("radical","kanji","vocabulary","kana_vocabulary")}
    if not relevant:
        return {"at":None,"status":"No WaniKani subjects for this milestone.",
                "lessons":0}
    by_level={}
    for sid,s in relevant.items():
        by_level.setdefault(s["level"],[]).append(sid)
    # Complete, API-grounded SRS interval info is mandatory to project reviews.
    for sid,s in relevant.items():
        if s["type"] not in ("radical","kanji"):
            continue
        if passed(assigned.get(sid)):
            continue
        srs_id=s.get("spaced_repetition_system_id")
        stage_delays=intervals.get(srs_id)
        if not stage_delays or any(stage not in stage_delays for stage in (1,2,3,4)):
            return {"at":None,"status":"No complete SRS stage timings for some Radical/Kanji.",
                    "lessons":0}
    level=current_level
    state={sid:(assigned.get(sid,{}).get("srs_stage") or 0) for sid in relevant}
    previously_passed={sid for sid in relevant if passed(assigned.get(sid))}
    passed_ids=set(previously_passed)
    started={sid for sid in relevant if assigned.get(sid,{}).get("started_at")}
    # Started subjects should always have stage >=1; validate live data.
    events=[]
    serial=0
    for sid in started:
        if sid in passed_ids or state[sid]<1:
            continue
        nxt=dt(assigned[sid].get("available_at"))
        if nxt is None:
            return {"at":None,"status":"Started SRS item missing assignment.available_at.",
                    "lessons":0}
        serial+=1
        heappush(events,(max(now,nxt),serial,sid))
    limit_day=now+timedelta(days=max_days)
    at=now
    lessons_started_in_scenario=0
    today=now.astimezone(tz).date()
    day_count=Counter({today:int(max(0,lessons_today))})

    def next_midnight(t):
        return datetime.combine(t.astimezone(tz).date()+timedelta(days=1),
                                time.min,tzinfo=tz).astimezone(timezone.utc)

    def unlocked(sid):
        s=relevant[sid]
        if s["level"]>level:
            return False
        if s["level"]<current_level:
            return bool(assigned.get(sid,{}).get("unlocked_at"))
        return all(comp in passed_ids or passed(assigned.get(comp))
                   for comp in s.get("component_subject_ids",[]))

    def gate_complete(l):
        ks=[sid for sid in by_level.get(l,[]) if relevant[sid]["type"]=="kanji"]
        if not ks:
            return False
        threshold=ceil(len(ks)*.9)
        return sum(sid in passed_ids for sid in ks)>=threshold

    def full_complete():
        return all(sid in started for sid in relevant)

    def priority(sid):
        s=relevant[sid]
        typ=s["type"]
        if typ=="radical":
            return (0,s["level"],sid)
        if typ=="kanji":
            return (1,s["level"],sid)
        return (2,s["level"],sid)

    def eligible():
        return sorted((sid for sid in relevant if sid not in started
                       and (include_vocab or relevant[sid]["type"] in ("radical","kanji"))
                       and unlocked(sid)),key=priority)

    def start_one(sid,t):
        nonlocal serial, lessons_started_in_scenario
        started.add(sid)
        lessons_started_in_scenario+=1
        state[sid]=1
        interval=intervals[relevant[sid]["spaced_repetition_system_id"]]
        serial+=1
        heappush(events,(t+timedelta(seconds=interval[1]),serial,sid))

    # For vocabulary-only items, SRS future reviews are irrelevant to target
    # and to unlock progression; we count starting their lesson, not Guru.
    def start_vocab(sid):
        nonlocal lessons_started_in_scenario
        started.add(sid)
        lessons_started_in_scenario+=1

    for iteration in range(180000):
        if at>limit_day:
            return {"at":None,"status":"Conditional lower bound exceeds simulation horizon.",
                    "lessons":lessons_started_in_scenario}
        while level<target_level and gate_complete(level):
            level+=1
        if level>=target_level and (not include_vocab or full_complete()):
            return {"at":at,"status":(
                "Conditional earliest (all future reviews correct and on time; "
                "lessons immediately after unlocking, daily cap respected). "
                "NOT a predicted actual date."),"lessons":lessons_started_in_scenario}
        # Handle all SRS reviews due by this time, including review intervals
        # known exactly from current assignments.
        while events and events[0][0]<=at:
            when,_,sid=heappop(events)
            if sid in passed_ids:
                continue
            stage=state[sid]
            if stage not in (1,2,3,4):
                return {"at":None,"status":"Unrecognized active SRS stage.",
                        "lessons":lessons_started_in_scenario}
            stage+=1
            state[sid]=stage
            if stage>=5:
                passed_ids.add(sid)
            else:
                inter=intervals[relevant[sid]["spaced_repetition_system_id"]]
                serial+=1
                heappush(events,(at+timedelta(seconds=inter[stage]),serial,sid))
        while level<target_level and gate_complete(level):
            level+=1
        if level>=target_level and (not include_vocab or full_complete()):
            return {"at":at,"status":"Conditional earliest with correct on-time reviews.",
                    "lessons":lessons_started_in_scenario}

        day=at.astimezone(tz).date()
        capacity=max(0,lesson_limit-day_count[day])
        todo=eligible()
        if capacity and todo:
            for sid in todo[:capacity]:
                if relevant[sid]["type"] in ("vocabulary","kana_vocabulary"):
                    start_vocab(sid)
                else:
                    start_one(sid,at)
                day_count[day]+=1
            continue
        upcoming=[events[0][0]] if events else []
        if todo and capacity==0:
            upcoming.append(next_midnight(at))
        # If no eligible lessons and no review events, the data can't
        # establish further progress (e.g., subscription cap or missing links).
        if not upcoming:
            return {"at":None,"status":"Blocked by existing assignments or missing prerequisite unlock evidence.",
                    "lessons":lessons_started_in_scenario}
        at=min(t for t in upcoming if t>at)
    return {"at":None,"status":"Event simulation iteration limit exceeded.",
            "lessons":lessons_started_in_scenario}
