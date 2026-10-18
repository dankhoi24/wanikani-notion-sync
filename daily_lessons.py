"""Daily Vietnam-calendar WaniKani lesson history for honest day-by-day charts.

Unlike UTC-keyed snapshots, the Day property is the actual calendar date in
Learning Settings timezone. One stable row per local day; all lessons come from
assignments.started_at and are not inferred from snapshot acquisition times.
"""
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo


LESSON_TYPES = ("radical", "kanji", "vocabulary", "kana_vocabulary")


def history_rows(assignments, now, timezone_name, tracking_start, daily_target,
                 lookback_days=7):
    """Include current and recent local dates, including 0-lesson dates.

    Backfills every date since Tracking Start for the first week. On ongoing
    runs, recomputes the last 7 days in place, without duplicating rows.
    """
    from datetime import datetime
    tz=ZoneInfo(timezone_name)
    today=now.astimezone(tz).date()
    if today < tracking_start:
        return []
    counts=Counter()
    per_type=Counter()
    for item in assignments:
        data=item.get("data",{})
        kind=data.get("subject_type")
        started=data.get("started_at")
        if kind not in LESSON_TYPES or not started:
            continue
        at=datetime.fromisoformat(started.replace("Z","+00:00"))
        if at > now:
            continue
        day=at.astimezone(tz).date()
        if day < tracking_start or day > today:
            continue
        counts[day]+=1
        per_type[(day,kind)]+=1

    first=max(tracking_start,today-timedelta(days=lookback_days-1))
    rows=[]
    cursor=first
    while cursor <= today:
        # Unlike total-days-since-tracking-start, the 7d mean is computed
        # as-of the specific historical date, not today's current pace.
        period=min(7,(cursor-tracking_start).days+1)
        recent=sum(counts[cursor-timedelta(days=i)] for i in range(period))
        rows.append({
            "Day":cursor,
            "Lessons Today":counts[cursor],
            "Recent Pace (7d)":round(recent/period,2),
            "Radical Today":per_type[(cursor,"radical")],
            "Kanji Today":per_type[(cursor,"kanji")],
            "Vocab Today":(per_type[(cursor,"vocabulary")]+
                           per_type[(cursor,"kana_vocabulary")]),
            "Daily Target":daily_target,
        })
        cursor+=timedelta(days=1)
    return rows


def push_daily_rows(rows, source_id, token, upsert, props):
    """Publish one idempotent Notion row for each local date."""
    stats={"created":0,"updated":0}
    for row in rows:
        day=row["Day"]
        title=day.isoformat()
        numbers={k:v for k,v in row.items() if k != "Day"}
        properties=props("Name",title,numbers,dates={"Day":day})
        result=upsert(source_id,"Name",title,properties,token)
        stats[result]+=1
    return stats
