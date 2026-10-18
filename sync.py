"""Synchronize personal WaniKani v2 metrics to two existing Notion data sources."""
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

NOTION_VERSION = "2025-09-03"
WK_BASE = "https://api.wanikani.com/v2"
NOTION_BASE = "https://api.notion.com/v1"


def iso_dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def request(method, url, token, headers=None, payload=None):
    extra = {"Authorization": "Bearer " + token, "Accept": "application/json", **(headers or {})}
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    if body is not None:
        extra["Content-Type"] = "application/json"
    for attempt in range(6):
        try:
            with urlopen(Request(url, data=body, headers=extra, method=method), timeout=35) as r:
                return json.loads(r.read().decode())
        except HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < 5:
                try:
                    delay = int(err.headers.get("Retry-After", "0"))
                except ValueError:
                    delay = 0
                time.sleep(min(max(delay, 2**attempt), 60))
                continue
            raise RuntimeError(f"HTTP {err.code} on {urlparse(url).path}: {err.read(200).decode(errors='replace')}") from None
        except (URLError, TimeoutError):
            if attempt < 5:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"Connection failed: {urlparse(url).netloc}") from None
    raise RuntimeError("Request failed")


def wk(endpoint, token):
    return request("GET", WK_BASE + "/" + endpoint, token, {"Wanikani-Revision": "20170710"})


def collection(endpoint, token):
    url, data = WK_BASE + "/" + endpoint, []
    while url:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.wanikani.com" or not parsed.path.startswith("/v2/"):
            raise ValueError("Invalid WaniKani pagination URL")
        page = request("GET", url, token, {"Wanikani-Revision": "20170710"})
        data += page.get("data", [])
        url = (page.get("pages") or {}).get("next_url")
    return data


def median_level_days(progressions, level):
    starts = sorted((iso_dt(d["started_at"]), d["level"])
                    for p in progressions for d in [p.get("data", {})]
                    if d.get("started_at") and isinstance(d.get("level"), int))
    deltas = [(b-a).total_seconds()/86400 for (a, l1), (b, l2) in zip(starts, starts[1:])
              if l2 == l1+1 and b > a][-10:]
    current_started = max((dt for dt, l in starts if l == level), default=None)
    return (round(statistics.median(deltas), 2) if len(deltas) >= 2 else None,
            len(deltas), current_started)


def forecast(target, level, pace, current_started, now, progressions=None):
    if level >= target:
        # Show the actual historical start of the target WK level when available.
        achieved = [iso_dt(d["started_at"]) for p in (progressions or [])
                    for d in [p.get("data", {})] if d.get("level") == target and d.get("started_at")]
        return max(achieved).date() if achieved else now.date()
    if pace is None:
        return None
    guess = (current_started or now) + timedelta(days=(target-level)*pace)
    return max(guess.date(), now.date())


def metrics(assignments, review_stats, summary, now):
    types, stages = Counter(), Counter()
    for item in assignments:
        d = item["data"]
        n = d.get("srs_stage", 0)
        if n < 1:
            continue
        if d.get("subject_type") in ("kanji", "vocabulary"):
            types[d["subject_type"]] += 1
        label = ("Apprentice" if n <= 4 else "Guru" if n <= 6 else
                 "Master" if n == 7 else "Enlightened" if n == 8 else "Burned")
        stages[label] += 1
    correct = wrong = 0
    for entry in review_stats:
        d = entry["data"]
        if d.get("hidden") or d.get("subject_type") not in ("kanji", "vocabulary"):
            continue
        correct += d.get("meaning_correct", 0) + d.get("reading_correct", 0)
        wrong += d.get("meaning_incorrect", 0) + d.get("reading_incorrect", 0)
    due_now = due_24 = 0
    for entry in summary.get("reviews", []):
        due = iso_dt(entry.get("available_at"))
        if due:
            if due <= now: due_now += len(entry.get("subject_ids", []))
            elif due <= now + timedelta(hours=24): due_24 += len(entry.get("subject_ids", []))
    return {"Learned Kanji": types["kanji"], "Learned Vocab": types["vocabulary"],
            **{k: stages[k] for k in ["Apprentice", "Guru", "Master", "Enlightened", "Burned"]},
            "Accuracy Pct": round(100*correct/(correct+wrong), 2) if correct+wrong else None,
            "Reviews Now": due_now, "Reviews Next 24h": due_24}


def lesson_activity(assignments, now, tz):
    from zoneinfo import ZoneInfo
    today=now.astimezone(ZoneInfo(tz)).date()
    daily=Counter()
    vocab=Counter()
    starts=[]
    for item in assignments:
        data=item.get("data", {})
        at=iso_dt(data.get("started_at"))
        if at is None or at > now:
            continue
        starts.append(at)
        day=at.astimezone(ZoneInfo(tz)).date()
        daily[day]+=1
        if data.get("subject_type") in ("vocabulary","kana_vocabulary"):
            vocab[day]+=1
    def avg(counter,n):
        return round(sum(counter[today-timedelta(days=i)] for i in range(n))/n,2)
    return {
      "Lessons Today":daily[today],"Lessons 7d Avg":avg(daily,7),
      "Lessons 30d Avg":avg(daily,30),"Vocab 7d Avg":avg(vocab,7),
      "Vocab 30d Avg":avg(vocab,30),
      "Active Days 7d":sum(daily[today-timedelta(days=i)]>0 for i in range(7))
    },starts


def lessons_per_level(starts, progressions):
    transitions=sorted((iso_dt(d["started_at"]), d["level"])
        for p in progressions for d in [p.get("data",{})]
        if d.get("started_at") and isinstance(d.get("level"),int))
    volumes=[]
    for (a,level),(b,next_level) in zip(transitions,transitions[1:]):
        if next_level != level+1 or b <= a:
            continue
        volume=sum(a<=s<b for s in starts)
        if volume >= 5:
            volumes.append(volume)
    return (round(statistics.median(volumes[-5:]),2),len(volumes)) if len(volumes)>=2 else (None,len(volumes))


def workload_eta(target,level,volume,rate,current_start,starts,now):
    # Approximate, not a WaniKani Guru-unlock or JLPT readiness forecast.
    if target<=level:
        return now.date()
    if volume is None or rate is None or rate < 1:
        return None
    current_done=sum(current_start<=s<=now for s in starts) if current_start else 0
    remaining=max(0,volume-current_done)
    days=max(7,remaining/rate)+max(0,target-level-1)*max(7,volume/rate)
    return (now+timedelta(days=days)).date()


def learning_settings(source_id,token):
    from zoneinfo import ZoneInfo
    response=notion("POST",f"/data_sources/{source_id}/query",token,{
        "filter":{"property":"Name","title":{"equals":"Configuration"}},"page_size":2})
    rows=response.get("results",[])
    if len(rows)!=1:
        raise ValueError("Expected exactly one Learning Settings Configuration row")
    properties=rows[0]["properties"]
    target=properties["Daily Lessons Target"]["number"]
    if target is None or int(target)!=target or not 1<=target<=100:
        raise ValueError("Daily Lessons Target must be a whole number 1..100")
    timezone="".join(t.get("plain_text","") for t in properties["Timezone"].get("rich_text",[])).strip() or "Asia/Ho_Chi_Minh"
    ZoneInfo(timezone)
    return int(target),timezone


def notion(method, path, token, payload=None):
    return request(method, NOTION_BASE + path, token,
                   {"Notion-Version": NOTION_VERSION}, payload)


def validate_source(source_id, fields, token):
    obj = notion("GET", f"/data_sources/{source_id}", token)
    missing = set(fields) - set(obj.get("properties", {}))
    if missing:
        raise ValueError(f"Data source {source_id} missing: {sorted(missing)}")


def text(value):
    return [{"type": "text", "text": {"content": str(value)[:1900]}}]


def props(title_col, title, numbers, dates=None, rich=None):
    result = {title_col: {"title": text(title)}}
    result.update({k: {"number": v} for k, v in numbers.items()})
    result.update({k: {"date": {"start": v.isoformat()} if v else None}
                   for k, v in (dates or {}).items()})
    result.update({k: {"rich_text": text(v)} for k, v in (rich or {}).items()})
    return result


def upsert(source_id, title_col, title, properties, token):
    existing = notion("POST", f"/data_sources/{source_id}/query", token,
                      {"filter": {"property": title_col, "title": {"equals": title}}, "page_size": 10})
    matches = existing.get("results", [])
    if len(matches) > 1:
        raise ValueError(f"Duplicate Notion title '{title}': refusing ambiguous update")
    if matches:
        notion("PATCH", f"/pages/{matches[0]['id']}", token, {"properties": properties})
        return "updated"
    notion("POST", "/pages", token,
           {"parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": properties})
    return "created"


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


def main():
    wk_token, notion_token = required("WK_TOKEN"), required("NOTION_TOKEN")
    snaps_id, milestones_id = (required("NOTION_SNAPSHOTS_DATA_SOURCE_ID"),
                               required("NOTION_MILESTONES_DATA_SOURCE_ID"))
    field_metrics = ["Learned Kanji", "Learned Vocab", "Apprentice", "Guru", "Master",
                     "Enlightened", "Burned", "Accuracy Pct", "Reviews Now", "Reviews Next 24h"]
    validate_source(snaps_id, {"Name", "Synced At", "Level", "Days Per Level", "Daily Target","Lessons Today","Lessons 7d Avg","Lessons 30d Avg","Vocab 7d Avg","Vocab 30d Avg","Target Hit 7d Pct","Actual Pace Days/Level", *field_metrics}, notion_token)
    validate_source(milestones_id, {"Milestone", "Target WK Level", "Start", "ETA",
                                    "Days Left", "Progress Pct", "Basis","Target ETA","Actual Pace","Lessons/Level"}, notion_token)
    settings_id = required("NOTION_SETTINGS_DATA_SOURCE_ID")
    validate_source(settings_id, {"Name","Daily Lessons Target","Timezone"}, notion_token)
    daily_target,tz = learning_settings(settings_id,notion_token)
    now = datetime.now(timezone.utc)
    level = wk("user", wk_token)["data"]["level"]
    assignments = collection("assignments", wk_token)
    progressions = collection("level_progressions", wk_token)
    stats = collection("review_statistics?subject_types=kanji,vocabulary", wk_token)
    summary = wk("summary", wk_token)["data"]
    pace, observations, current_started = median_level_days(progressions, level)
    counts = metrics(assignments, stats, summary, now)
    activity,started_at=lesson_activity(assignments,now,tz)
    volume,samples=lessons_per_level(started_at,progressions)
    actual=activity["Lessons 30d Avg"]
    actual_pace=round(max(7,volume/actual),2) if volume is not None and actual>=1 else None
    key = now.strftime("WK %Y-%m-%d UTC")
    snap = props("Name", key, {"Level": level, "Days Per Level": pace, **counts,
                 "Daily Target":daily_target,"Lessons Today":activity["Lessons Today"],
                 "Lessons 7d Avg":activity["Lessons 7d Avg"],
                 "Lessons 30d Avg":activity["Lessons 30d Avg"],
                 "Vocab 7d Avg":activity["Vocab 7d Avg"],
                 "Vocab 30d Avg":activity["Vocab 30d Avg"],
                 "Target Hit 7d Pct":round(activity["Lessons 7d Avg"]/daily_target*100,1),
                 "Actual Pace Days/Level":actual_pace},
                 dates={"Synced At": now})
    print(f"WaniKani level={level}; target={daily_target} lessons/day; actual 7d={activity['Lessons 7d Avg']}, 30d={actual}; estimated lessons/level={volume}, samples={samples}")
    print("Snapshot", upsert(snaps_id, "Name", key, snap, notion_token))
    config = json.loads(Path(__file__).with_name("milestones.json").read_text(encoding="utf-8"))
    for item in config:
        target = int(item["target_wk_level"])
        if target < 1 or target > 60:
            raise ValueError("Milestone target must be a WaniKani level 1..60")
        title = item["name"]
        eta = (forecast(target,level,None,None,now,progressions) if level>=target
               else workload_eta(target,level,volume,actual,current_started,started_at,now))
        target_eta = (eta if level>=target else workload_eta(target,level,volume,daily_target,current_started,started_at,now))
        days_left = max(0, (eta - now.date()).days) if eta else None
        basis = (f"WaniKani level proxy, not JLPT readiness. "
                 f"Historical lesson volume/level={volume} from {samples} intervals; "
                 f"actual 30d lessons/day={actual}; target={daily_target}; "
                 f"7d active days={activity['Active Days 7d']}. "
                 f"Workload estimate only, minimum 7 days per level for SRS; "
                 f"cannot predict kanji Guru timing precisely.")
        properties = props("Milestone", title,
                           {"Target WK Level": target, "Progress Pct": round(min(100,level/target*100),1),
                            "Days Left": days_left,"Actual Pace":actual,"Lessons/Level":volume},
                           dates={"Start": now.date(), "ETA": eta, "Target ETA":target_eta}, rich={"Basis": basis})
        print(title, upsert(milestones_id, "Milestone", title, properties, notion_token),
              "ETA:", eta or "insufficient history")
    print("Success: Notion has been synchronized.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        sys.exit(1)
