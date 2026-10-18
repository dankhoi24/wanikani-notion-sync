"""Conditional same-day WaniKani lesson unlock forecast.

An unlocked lesson is one whose subject components have *passed* (Guru 5
at least once). Only in-progress prerequisites at Apprentice 4 with a known
next-review available_at can newly reach Guru on today's schedule.
This is deliberately NOT a promise: a late or incorrect review delays unlocks.
"""
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from wk_detailed import passed, dt


def estimate_unlocks_today(subjects, assigned, current_level, now, timezone):
    """Estimate additional unstarted lessons unlocked by local midnight.

    Returns currently available lessons separately from conditional *new*
    unlocks. Assumes all SRS-stage-4 prerequisites are reviewed correctly
    immediately at their due times. Does not assume new lessons can be made
    Guru the same day; previous stages require more than a day.
    """
    local=now.astimezone(ZoneInfo(timezone))
    start_of_tomorrow=datetime.combine(
        local.date()+timedelta(days=1),time.min,ZoneInfo(timezone))
    available=Counter()
    scheduled=[]
    for subject_id,subject in subjects.items():
        if subject.get("level",61)>current_level:
            continue
        a=assigned.get(subject_id,{})
        if a.get("started_at"):
            continue
        typ=subject.get("type")
        if typ not in ("radical","kanji","vocabulary","kana_vocabulary"):
            continue
        if a.get("unlocked_at"):
            available[typ]+=1
            continue
        components=subject.get("component_subject_ids",[])
        if not components:
            # Not enough evidence to predict why this item is locked.
            continue
        required_times=[]
        possible=True
        for component_id in components:
            ca=assigned.get(component_id)
            if passed(ca):
                continue
            if not ca or ca.get("srs_stage")!=4:
                possible=False
                break
            next_at=dt(ca.get("available_at"))
            if next_at is None:
                possible=False
                break
            due=max(now,next_at)
            if due.astimezone(ZoneInfo(timezone)) >= start_of_tomorrow:
                possible=False
                break
            required_times.append(due)
        # At least one incomplete prerequisite actually must pass today.
        if possible and required_times:
            unlock_at=max(required_times)
            scheduled.append((unlock_at,typ,subject_id))
    scheduled.sort(key=lambda item:(item[0],item[2]))
    forecast=Counter(typ for _,typ,_ in scheduled)
    earliest=scheduled[0][0] if scheduled else None
    return {
        "date":local.date(),
        "available_now":sum(available.values()),
        "potential_today":len(scheduled),
        "by_type":forecast,
        "first_unlock_at":earliest,
    }


def unlock_cards(value, tz):
    local=ZoneInfo(tz)
    count=value["potential_today"]
    first=value["first_unlock_at"]
    types=value["by_type"]
    return {
        "LESSONS CÓ THỂ MỞ KHÓA HÔM NAY": (
            f"{count} bài · nếu review đúng" if count else "0 bài dự kiến hôm nay"),
        "LOẠI BÀI CÓ THỂ MỞ KHÓA": (
            f"Radical {types['radical']} · Kanji {types['kanji']} · "
            f"Vocab {types['vocabulary']+types['kana_vocabulary']}"),
        "MỞ KHÓA SỚM NHẤT": (
            first.astimezone(local).strftime("%H:%M · %d/%m/%Y")
            if first else "Chưa có mốc hôm nay"),
    }
