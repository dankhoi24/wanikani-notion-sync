"""Daily WaniKani review-work estimate from genuine cumulative checkpoints.

WaniKani does not expose date-stamped completed review sessions via its
read-only v2 API. For each VIETNAM calendar date, estimate completed sessions
as the increase in cumulative meaning_correct between saved samples nearest
to midnight boundaries. Each completed review normally includes a meaning
question. This is a PROXY and may assign evening/morning activity to adjacent
days when a checkpoint misses midnight. Missing bounds are NULL, never zero.

The current incomplete day uses the LIVE counters at sync time for its end.
"""
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

MAX_BOUNDARY_GAP = timedelta(hours=6)


def _dt(value):
    if not value:
        return None
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _samples(checkpoints, now):
    result=[]
    for row in checkpoints:
        p=row.get("properties",{})
        at=_dt((p.get("Captured At",{}).get("date") or {}).get("start"))
        num=(p.get("Meaning Correct Total",{}).get("number"))
        if at and at<=now and isinstance(num,(int,float)) and num>=0:
            result.append((at.astimezone(timezone.utc),int(num)))
    return sorted(result)


def _nearest(samples, boundary, max_gap):
    choices=[(abs(at-boundary),at,count) for at,count in samples
             if abs(at-boundary)<=max_gap]
    if not choices:
        return None
    _,at,count=min(choices)
    return at,count


def reviews_by_day(lesson_rows, checkpoints, live_counters, now,
                   timezone_name, max_boundary_gap=MAX_BOUNDARY_GAP):
    """Return rows annotated with honest near-midnight review count estimates.

    The current local date is PARTIAL. Completed dates with no trustworthy
    start/end checkpoint retain None, never a fabricated value.
    """
    tz=ZoneInfo(timezone_name)
    current_day=now.astimezone(tz).date()
    samples=_samples(checkpoints,now)
    results=[]
    for row in lesson_rows:
        r=dict(row)
        day=r["Day"]
        start=datetime.combine(day,time.min,tzinfo=tz).astimezone(timezone.utc)
        stop=datetime.combine(day+timedelta(days=1),time.min,
                              tzinfo=tz).astimezone(timezone.utc)
        lower=_nearest(samples,start,max_boundary_gap)
        partial=(day==current_day)
        upper=(now.astimezone(timezone.utc),
               int(live_counters["meaning_correct"])) if partial else (
                    _nearest(samples,stop,max_boundary_gap))
        count=None
        status="Thiếu checkpoint gần 00:00"
        window="Không đủ dữ liệu để tính reviews; không thay bằng 0."
        start_at=lower[0] if lower else None
        end_at=upper[0] if upper else None
        if lower and upper:
            if upper[0]<=lower[0]:
                status="Cửa sổ không hợp lệ"
                window="Checkpoint kết thúc không sau checkpoint bắt đầu."
            elif upper[1]<lower[1]:
                status="Bộ đếm giảm / đã reset"
                window="Không suy diễn số reviews khi bộ đếm bị giảm."
            else:
                count=upper[1]-lower[1]
                off1=round(abs((lower[0]-start).total_seconds())/3600,1)
                off2=0.0 if partial else round(abs((upper[0]-stop).total_seconds())/3600,1)
                status=("Ước tính · ngày chưa kết thúc" if partial else
                        "Ước tính · gần mốc nửa đêm")
                window=(f"Chênh meaning_correct từ {lower[0].astimezone(tz):%d/%m %H:%M}"
                        f" đến {upper[0].astimezone(tz):%d/%m %H:%M} (VN). "
                        f"Độ lệch mốc 00:00: {off1:g}h / {off2:g}h; "
                        "KHÔNG phải lịch sử review chính xác theo ngày.")
        r.update({"Reviews Done Approx":count,
                  "Review Data Quality":status,
                  "Review Measurement Window":window,
                  "Review Window Start":start_at,
                  "Review Window End":end_at})
        results.append(r)
    return results


def fetch_checkpoint_history(notion,token,source_id,earliest_day,timezone_name,
                             max_pages=20):
    """Page until the requested midnight-window is covered (or exhausted).

    Keep the original source snapshots unchanged; return read-only rows.
    """
    tz=ZoneInfo(timezone_name)
    cutoff=(datetime.combine(earliest_day,time.min,tzinfo=tz)
            .astimezone(timezone.utc)-MAX_BOUNDARY_GAP)
    result=[]
    cursor=None
    for _ in range(max_pages):
        payload={"page_size":100,
                 "sorts":[{"property":"Captured At","direction":"descending"}]}
        if cursor:payload["start_cursor"]=cursor
        batch=notion("POST",f"/data_sources/{source_id}/query",token,payload)
        items=batch.get("results",[])
        result.extend(items)
        times=[_dt((item.get("properties",{}).get("Captured At",{}).get("date") or {}).get("start"))
               for item in items]
        if any(t and t<=cutoff for t in times):
            return result
        if not batch.get("has_more"):
            return result
        cursor=batch.get("next_cursor")
        if not cursor:
            raise ValueError("Review checkpoint pagination has no next_cursor")
    raise ValueError("Review checkpoint history exceeded pagination safety cap")
