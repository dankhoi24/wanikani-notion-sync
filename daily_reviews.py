"""Day-scoped approximate review counts from WaniKani cumulative counters.

GET /reviews is deprecated. We cannot reconstruct exact review sessions or exact
midnight boundaries from cumulative review_statistics. Compare against the most
recent checkpoint *before* local midnight. Label the result as an estimate and
preserve its measurement window.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def day_start_utc(now, timezone_name):
    tz=ZoneInfo(timezone_name)
    local_date=now.astimezone(tz).date()
    return datetime.combine(local_date, time.min, tzinfo=tz).astimezone(timezone.utc)


def most_recent_before_midnight(rows, now, timezone_name):
    cutoff=day_start_utc(now,timezone_name)
    valid=[]
    for row in rows:
        prop=row.get("properties",{})
        at=parse_time((prop.get("Captured At",{}).get("date") or {}).get("start"))
        if at is not None and at < cutoff:
            valid.append((at,row))
    return max(valid,key=lambda entry:entry[0])[1] if valid else None


def estimate_reviews_today(counters, baseline_row, now, timezone_name):
    """Return annotated proxy from counter delta, not exact review events."""
    output={"Reviews Today Approx":None, "Reviews Today Window":"Không có checkpoint trước 00:00."}
    if baseline_row is None:
        return output
    prop=baseline_row.get("properties",{})
    fields={
        "meaning_correct":"Meaning Correct Total",
        "meaning_incorrect":"Meaning Incorrect Total",
        "reading_correct":"Reading Correct Total",
        "reading_incorrect":"Reading Incorrect Total",
    }
    try:
        old={k:prop[v]["number"] for k,v in fields.items()}
        if any(value is None for value in old.values()):
            return {**output,"Reviews Today Window":"Checkpoint trước ngày thiếu bộ đếm."}
        differences={k:counters[k]-old[k] for k in fields}
        if any(x<0 for x in differences.values()):
            return {**output,"Reviews Today Window":"Bộ đếm review giảm; không thể ước tính hôm nay."}
        dt=parse_time(prop["Captured At"]["date"]["start"])
        window=dt.astimezone(ZoneInfo(timezone_name)).strftime("%d/%m/%Y %H:%M")
        return {
            "Reviews Today Approx":int(differences["meaning_correct"]),
            "Reviews Today Window":(
                f"Từ checkpoint {window} (giờ địa phương) đến hiện tại; "
                "chênh lệch số câu meaning trả lời đúng. "
                "KHÔNG phải số lần review chính xác trong ngày; "
                "có thể bao gồm hoạt động trước 00:00 sau checkpoint."
            ),
        }
    except (KeyError,TypeError,ValueError):
        return {**output,"Reviews Today Window":"Dữ liệu checkpoint không đọc được."}
