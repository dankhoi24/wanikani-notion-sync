"""Synchronize personal WaniKani v2 metrics to two existing Notion data sources."""
import json
import os
import statistics
import sys
import time
from collections import Counter
from n5_dashboard import card_values, update_n5_dashboard
from unlock_forecast import estimate_unlocks_today, unlock_cards
from guru_forecast import guru_scenarios
from milestone_bounds import conditional_earliest
from milestone_monte_carlo import simulate_milestones
from daily_lessons import history_rows, push_daily_rows
from notion_svg_chart import update_daily_chart
from notion_review_chart import update_discipline_chart
from daily_discipline import reviews_by_day, fetch_checkpoint_history
from daily_reviews import (most_recent_before_midnight, day_start_utc,
                           estimate_reviews_today)
from wk_detailed import (subjects_by_id,assignments_by_id,inventory,milestone_inventory,
                         review_counters,review_delta,earliest_next_gate,intervals_from_srs,
                         forecast_with_inventory,forecast_full_lessons)
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


def lesson_activity(assignments, now, tz, tracking_start):
    from zoneinfo import ZoneInfo
    today=now.astimezone(ZoneInfo(tz)).date()
    daily=Counter()
    radical=Counter()
    kanji=Counter()
    vocab=Counter()
    gate=Counter()
    starts=[]
    for item in assignments:
        data=item.get("data", {})
        at=iso_dt(data.get("started_at"))
        if at is None or at > now:
            continue
        subject_type=data.get("subject_type")
        if subject_type not in ("radical","kanji","vocabulary","kana_vocabulary"):
            continue
        starts.append(at)
        day=at.astimezone(ZoneInfo(tz)).date()
        daily[day]+=1
        if subject_type=="radical":
            radical[day]+=1
            gate[day]+=1
        elif subject_type=="kanji":
            kanji[day]+=1
            gate[day]+=1
        else:
            vocab[day]+=1
    # Count only dates starting on the new plan's Tracking Start.
    # The current (partial) day is included, so the ETA can react immediately.
    elapsed_days=max(0,(today-tracking_start).days+1)
    def avg(counter,n):
        days=min(n,elapsed_days)
        return (round(sum(counter[today-timedelta(days=i)] for i in range(days))/days,2)
                if days else None)
    return {
      "Lessons Today":daily[today] if today>=tracking_start else 0,
      "Lessons 7d Avg":avg(daily,7),
      "Lessons 30d Avg":avg(daily,30),
      "Vocab 7d Avg":avg(vocab,7),
      "Vocab 30d Avg":avg(vocab,30),
      "Radical 7d Avg":avg(radical,7),
      "Kanji 7d Avg":avg(kanji,7),
      "Radical 30d Avg":avg(radical,30),
      "Kanji 30d Avg":avg(kanji,30),
      "Gate 7d Avg":avg(gate,7),
      "Gate 30d Avg":avg(gate,30),
      "Radical Lessons Today":radical[today] if today>=tracking_start else 0,
      "Kanji Lessons Today":kanji[today] if today>=tracking_start else 0,
      "Vocab Lessons Today":vocab[today] if today>=tracking_start else 0,
      "Active Days 7d":sum(daily[today-timedelta(days=i)]>0 for i in range(min(7,elapsed_days))),
      "Elapsed Days":elapsed_days
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


def choose_eta_rate(seven_day, thirty_day, target):
    """Prefer recent actual pace; fall back to target as a labeled plan-only ETA."""
    if seven_day is not None and seven_day > 0:
        return seven_day, "observed_recent_7d"
    if thirty_day is not None and thirty_day > 0:
        return thirty_day, "observed_30d_fallback"
    return None, "no_new_lessons_since_tracking_start"


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
    start=properties["Tracking Start"].get("date")
    if not start or not start.get("start"):
        raise ValueError("Tracking Start must be set in Notion Learning Settings")
    return int(target),timezone,date.fromisoformat(start["start"][:10])


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
    validate_source(snaps_id, {"Name", "Synced At", "Level", "Days Per Level", "Daily Target","Lessons Today","Lessons 7d Avg","Lessons 30d Avg","Vocab 7d Avg","Vocab 30d Avg","Radical 7d Avg","Kanji 7d Avg","Radical 30d Avg","Kanji 30d Avg","Gate 7d Avg","Gate 30d Avg","Radical Lessons Today","Kanji Lessons Today","Vocab Lessons Today","Target Hit 7d Pct","Actual Pace Days/Level", "Reviews Today Approx", "Reviews Today Window", *field_metrics}, notion_token)
    validate_source(milestones_id, {"Milestone", "Target WK Level", "Start", "ETA",
                                    "Days Left", "Progress Pct", "Basis","Target ETA","Actual Pace","Lessons/Level","Recent Pace (7d)","ETA Status","Target Full Lessons ETA","Actual Full Lessons ETA","Gate Pace (7d)","Gate Pace (30d)","Radical Pace (7d)","Kanji Pace (7d)","Vocab Pace (7d)","Full ETA Status","Forecast Method"}, notion_token)
    settings_id = required("NOTION_SETTINGS_DATA_SOURCE_ID")
    validate_source(settings_id, {"Name","Daily Lessons Target","Timezone","Tracking Start"}, notion_token)
    daily_target,tz,tracking_start = learning_settings(settings_id,notion_token)
    now = datetime.now(timezone.utc)
    user_data=wk("user", wk_token)["data"]
    level=user_data["level"]
    granted_level=(user_data.get("subscription") or {}).get("max_level_granted")
    assignments = collection("assignments", wk_token)
    progressions = collection("level_progressions", wk_token)
    stats = collection("review_statistics?subject_types=kanji,vocabulary", wk_token)
    summary = wk("summary", wk_token)["data"]
    guru_fields={"Guru 90 ETA (Accuracy)","Guru 100 ETA (Accuracy)",
                 "Guru 90 ETA Range","Guru 100 ETA Range",
                 "Kanji Meaning Accuracy","Kanji Reading Accuracy",
                 "Kanji Review Success Pct","Guru Forecast Note"}
    validate_source(required("NOTION_LEVEL_BREAKDOWN_DATA_SOURCE_ID"),
                    {"Name","Level","Lessons Remaining","Kanji To Guru",
                     "Min Reviews to Gate",*guru_fields,
                     "Unlock P10","Unlock P50","Unlock P90",
                     "Days to Next Mean","Days to Next P10","Days to Next P90",
                     "Unlock Forecast Status"},notion_token)
    sim_date_fields={"Sim Level P10","Sim Level P50","Sim Level P90",
                     "Sim Full P10","Sim Full P50","Sim Full P90"}
    sim_numeric_fields={"Sim Trials","Sim Accuracy Kanji Meaning",
                        "Sim Accuracy Kanji Reading",
                        "Sim Accuracy Radical Meaning",
                        "Sim Accuracy Kanji Samples",
                        "Sim Accuracy Radical Samples"}
    validate_source(milestones_id,guru_fields | sim_date_fields | sim_numeric_fields | {
        "SRS Scenario Level Date","SRS Scenario Full Date","Evidence & Limits",
        "Sim Conditions","Sim Status"},notion_token)
    validate_source(required("NOTION_REVIEW_CHECKPOINTS_DATA_SOURCE_ID"),
                    {"Name","Captured At","Meaning Correct Total","Reviews Since Baseline"},notion_token)
    subject_rows = collection("subjects", wk_token)
    subjects = subjects_by_id(subject_rows)
    assigned = assignments_by_id(assignments)
    review_stats_all = collection("review_statistics", wk_token)
    counters = review_counters(review_stats_all)
    # Review-accuracy-adjusted current-level Guru dates use actual per-Kanji
    # statistics (NOT vocabulary) and each Kanji's SRS next-review schedule.
    srs_intervals=intervals_from_srs(collection("spaced_repetition_systems",wk_token))
    guru_projection=guru_scenarios(
        subjects,assigned,review_stats_all,level,now,srs_intervals)
    print("Current-level Guru scenarios:",
          "accuracy meaning=",guru_projection["Kanji Meaning Accuracy"],
          "reading=",guru_projection["Kanji Reading Accuracy"],
          "90% P50=",guru_projection["Gate P50"],
          "100% P50=",guru_projection["All P50"],
          "valid=",guru_projection["Simulation Valid"],
          "note=",guru_projection["Simulation Note"])
    def range_text(prefix):
        lo,hi=guru_projection.get(prefix+" P10"),guru_projection.get(prefix+" P90")
        if lo is None or hi is None:
            return "Unavailable — insufficient SRS/accuracy history"
        from zoneinfo import ZoneInfo
        zone=ZoneInfo(tz)
        return (lo.astimezone(zone).strftime("%d/%m/%Y") + " — " +
                hi.astimezone(zone).strftime("%d/%m/%Y") + " (P10–P90; on-time reviews)")
    guru_numbers={
        "Kanji Meaning Accuracy":guru_projection["Kanji Meaning Accuracy"],
        "Kanji Reading Accuracy":guru_projection["Kanji Reading Accuracy"],
        "Kanji Review Success Pct":guru_projection["Kanji Review Success Pct"]}
    guru_dates={
        "Guru 90 ETA (Accuracy)":guru_projection["Gate P50"],
        "Guru 100 ETA (Accuracy)":guru_projection["All P50"]}
    guru_rich={
        "Guru 90 ETA Range":range_text("Gate"),
        "Guru 100 ETA Range":range_text("All"),
        "Guru Forecast Note":guru_projection["Simulation Note"]}
    target_config = json.loads(Path(__file__).with_name("milestones.json").read_text(encoding="utf-8"))
    max_target = min(60,max(int(x["target_wk_level"]) for x in target_config))
    levels = inventory(subjects,assigned,level,max_target)
    # WaniKani API no longer exposes historical completed review events.
    # Checkpoint cumulative counts. The first sync establishes the baseline.
    checkpoints_id=required("NOTION_REVIEW_CHECKPOINTS_DATA_SOURCE_ID")
    detail_id=required("NOTION_LEVEL_BREAKDOWN_DATA_SOURCE_ID")
    previous_rows=notion("POST",f"/data_sources/{checkpoints_id}/query",notion_token,
                         {"page_size":100,"sorts":[{"property":"Captured At","direction":"descending"}]})["results"]
    def old_counts(row):
        p=row["properties"]
        return {k:p[field]["number"] or 0 for k,field in (
            ("meaning_correct","Meaning Correct Total"),
            ("meaning_incorrect","Meaning Incorrect Total"),
            ("reading_correct","Reading Correct Total"),
            ("reading_incorrect","Reading Incorrect Total"))}
    checkpoint_previous=old_counts(previous_rows[0]) if previous_rows else counters
    # Keep the baseline checkpoint stable even when >100 checkpoints exist.
    baseline_rows=notion("POST",f"/data_sources/{checkpoints_id}/query",notion_token,
                         {"page_size":1,"sorts":[{"property":"Captured At","direction":"ascending"}]})["results"]
    checkpoint_base=old_counts(baseline_rows[0]) if baseline_rows else counters
    review=review_delta(counters,checkpoint_base,checkpoint_previous)
    # GET /reviews no longer contains sessions. Compute a labeled approximation
    # from cumulative meaning-correct count at the last pre-midnight checkpoint.
    # This can include activity in the interval just before local midnight.
    before_midnight=most_recent_before_midnight(previous_rows,now,tz)
    if before_midnight is None:
        midnight=day_start_utc(now,tz).isoformat().replace("+00:00","Z")
        older=notion("POST",f"/data_sources/{checkpoints_id}/query",notion_token,{
            "page_size":1,
            "filter":{"property":"Captured At","date":{"before":midnight}},
            "sorts":[{"property":"Captured At","direction":"descending"}]})
        before_midnight=(older.get("results") or [None])[0]
    daily_reviews=estimate_reviews_today(counters,before_midnight,now,tz)
    checkpoint=props("Name",now.strftime("Checkpoint %Y-%m-%d %H:%M UTC"),{
        "Meaning Correct Total":counters["meaning_correct"],
        "Meaning Incorrect Total":counters["meaning_incorrect"],
        "Reading Correct Total":counters["reading_correct"],
        "Reading Incorrect Total":counters["reading_incorrect"],
        "Reviews Since Baseline":review["Reviews Since Baseline"],
        "Reviews Since Last Sync":review["Reviews Since Last Sync"],
        "Meaning Accuracy New":review["Meaning Accuracy New"],
        "Reading Accuracy New":review["Reading Accuracy New"],
        "Review Accuracy New":review["Review Accuracy New"]},
        dates={"Captured At":now},
        rich={"Note":"Review completed count is a lower-bound proxy based on correct meaning answers; API does not expose exact review-event history."})
    upsert(checkpoints_id,"Name",now.strftime("Checkpoint %Y-%m-%d %H:%M UTC"),checkpoint,notion_token)
    pace, observations, current_started = median_level_days(progressions, level)
    counts = metrics(assignments, stats, summary, now)
    activity,started_at=lesson_activity(assignments,now,tz,tracking_start)
    daily_source_id=os.environ.get("NOTION_DAILY_LESSONS_DATA_SOURCE_ID","").strip()
    if daily_source_id:
        validate_source(daily_source_id,{
            "Name","Day","Lessons Today","Recent Pace (7d)",
            "Radical Today","Kanji Today","Vocab Today","Daily Target",
            "Reviews Done Approx","Review Measurement Window",
            "Review Window Start","Review Window End","Review Data Quality"
        },notion_token)
        # A single collection supports both the current seven-day tracker
        # and the from-tracking-start SVGs. Refresh the last 7 rows in Notion
        # while using the retained checkpoint history for all chart dates.
        chart_days=history_rows(
            assignments,now,tz,tracking_start,daily_target,
            lookback_days=max(30,activity["Elapsed Days"]))
        recent_checkpoint_rows=fetch_checkpoint_history(
            notion,notion_token,checkpoints_id,chart_days[0]["Day"],tz) if chart_days else []
        chart_days=reviews_by_day(chart_days,recent_checkpoint_rows,counters,now,tz)
        history=chart_days[-7:]
        published=push_daily_rows(history,daily_source_id,notion_token,upsert,props)
        today_review=next((row for row in history
                           if row["Day"]==now.astimezone(__import__("zoneinfo").ZoneInfo(tz)).date()),None)
        if today_review:
            daily_reviews={
                "Reviews Today Approx":today_review["Reviews Done Approx"],
                "Reviews Today Window":today_review["Review Measurement Window"]}
        print("Daily lesson/review chart local days:",
              [row["Day"].isoformat() for row in history],published)
    volume,samples=lessons_per_level(started_at,progressions)
    actual=activity["Lessons 30d Avg"]
    # Level-up lessons require Radical/Kanji (NOT Vocabulary). Starting
    # every remaining lesson also includes Vocabulary and Kana Vocabulary.
    gate_rate,gate_source=choose_eta_rate(
        activity["Gate 7d Avg"],activity["Gate 30d Avg"],daily_target)
    full_rate,full_source=choose_eta_rate(
        activity["Lessons 7d Avg"],actual,daily_target)
    actual_pace=(round(max(7,volume/full_rate),2)
                 if volume is not None and full_rate is not None else None)
    gate=levels[0] if levels else {}
    key = now.strftime("WK %Y-%m-%d UTC")
    snap = props("Name", key, {"Level": level, "Days Per Level": pace, **counts,
                 "Daily Target":daily_target,"Lessons Today":activity["Lessons Today"],
                 "Lessons 7d Avg":activity["Lessons 7d Avg"],
                 "Lessons 30d Avg":activity["Lessons 30d Avg"],
                 "Vocab 7d Avg":activity["Vocab 7d Avg"],
                 "Vocab 30d Avg":activity["Vocab 30d Avg"],
                 "Radical 7d Avg":activity["Radical 7d Avg"],
                 "Kanji 7d Avg":activity["Kanji 7d Avg"],
                 "Radical 30d Avg":activity["Radical 30d Avg"],
                 "Kanji 30d Avg":activity["Kanji 30d Avg"],
                 "Gate 7d Avg":activity["Gate 7d Avg"],
                 "Gate 30d Avg":activity["Gate 30d Avg"],
                 "Radical Lessons Today":activity["Radical Lessons Today"],
                 "Kanji Lessons Today":activity["Kanji Lessons Today"],
                 "Vocab Lessons Today":activity["Vocab Lessons Today"],
                 "Target Hit 7d Pct":round(activity["Lessons 7d Avg"]/daily_target*100,1) if activity["Lessons 7d Avg"] is not None else None,
                 "Actual Pace Days/Level":actual_pace,
                 "Reviews Done (Tracked)":review["Reviews Since Baseline"],
                 "Reviews Last Sync":review["Reviews Since Last Sync"],
                 "Reviews Today Approx":daily_reviews["Reviews Today Approx"],
                 "Review Accuracy (Tracked)":review["Review Accuracy New"],
                 "Meaning Accuracy (Tracked)":review["Meaning Accuracy New"],
                 "Reading Accuracy (Tracked)":review["Reading Accuracy New"],
                 "Next Level Guru Remaining":gate.get("Kanji To Guru"),
                 "Next Level Gate Reviews":gate.get("Min Reviews to Gate"),
                 "Available Lessons":gate.get("Lessons Available")},
                 
                 dates={"Synced At": now},
                 rich={"Reviews Today Window":daily_reviews["Reviews Today Window"]})
    print(f"WaniKani level={level}; target={daily_target}; full7d={activity['Lessons 7d Avg']}; gate7d={activity['Gate 7d Avg']}; radical7d={activity['Radical 7d Avg']}; kanji7d={activity['Kanji 7d Avg']}; vocab7d={activity['Vocab 7d Avg']}; inventory ETA")
    print("Snapshot", upsert(snaps_id, "Name", key, snap, notion_token))
    config = target_config
    earliest_gate=earliest_next_gate(subjects,assigned,level,now,srs_intervals)
    unlock_estimate=estimate_unlocks_today(subjects,assigned,level,now,tz)
    # The current level is limited by Kanji SRS/Guru even when all its Kanji
    # lessons have ALREADY been started. P50 includes plausible wrong answers.
    # Future-level SRS floors are still optimistic heuristics, not simulations.
    gate_review_eta=guru_projection.get("Gate P50") or earliest_gate
    sim_targets=tuple(sorted({int(x["target_wk_level"]) for x in target_config
                              if int(x["target_wk_level"])>level}))
    # Track ALL intervening WK levels in the SAME Monte Carlo trajectories:
    # allows paired gate-to-gate duration estimates rather than subtracting
    # independently sorted milestone quantiles.
    sim_detail_targets=tuple(range(level+1,max(sim_targets)+1)) if sim_targets else ()
    simulation=(simulate_milestones(
        subjects,assigned,review_stats_all,level,sim_detail_targets,now,tz,
        daily_target,srs_intervals,lessons_today=activity["Lessons Today"],
        trials=160,seed=20261021)
        if sim_targets and (granted_level is None or
                            granted_level>=max(sim_targets))
        else {"valid":False,"reason":(
            "No future milestone, or subscription level cap blocks simulation."),
              "targets":{},"trials":0,"conditions":"Subscription/target blocked.",
              "accuracy_rates":{},"accuracy_samples":{}})
    print("MULTI-LEVEL STOCHASTIC SIMULATION:",
          "valid=",simulation["valid"],"reason=",simulation.get("reason"),
          "runs=",simulation.get("trials"),
          "sample gate P50=",[(t,simulation.get("targets",{}).get(t,{}).get(
             "gate",{}).get("P50")) for t in sim_targets])
    if simulation["valid"]:
        per_level=simulation.get("level_days",{})
        groups={}
        for lo,hi,label in ((5,9,"WK5-9"),(10,19,"WK10-19"),(20,29,"WK20-29")):
            values=[per_level[l]["Mean"] for l in range(lo,hi+1) if l in per_level]
            groups[label]=round(sum(values)/len(values),2) if values else None
        diagnostic={"current_level":level,"sample_time_utc":now.isoformat(),
                    "target_lessons_per_day":daily_target,
                    "trials":simulation["trials"],
                    "average_full_level_days":simulation.get("average_complete_level_days"),
                    "group_means":groups,
                    "per_level":{str(k):{**v,**simulation["level_inventory"].get(k,{})}
                                 for k,v in per_level.items()}}
        print("LEVEL_SIM_DIAGNOSTICS_JSON="+json.dumps(diagnostic,ensure_ascii=False))
        # Preserve exact projected level-entry P10/P50/P90 dates, not a
        # cumulative 12.04-day/level approximation. Log them for verification.
        from zoneinfo import ZoneInfo
        zone=ZoneInfo(tz)
        all_unlocks={str(i):{
            k:v.astimezone(zone).strftime("%Y-%m-%d") for k,v in
            simulation["targets"][i]["gate"].items()}
            for i in sorted(simulation["targets"])}
        print("LEVEL_UNLOCKS_JSON="+json.dumps(all_unlocks,sort_keys=True))
    # One upsert for each level: real inventory AND simulation-derived
    # unlock dates. If simulation cannot be computed, dates are cleared.
    for row in levels:
        lv=row["Level"]
        is_current=(lv==level)
        outcome=simulation.get("targets",{}).get(lv,{}).get("gate",{})
        duration=simulation.get("level_days",{}).get(lv,{})
        unlock_fields={
            "Unlock P10":outcome.get("P10"),
            "Unlock P50":outcome.get("P50"),
            "Unlock P90":outcome.get("P90")}
        days_fields={
            "Days to Next Mean":duration.get("Mean"),
            "Days to Next P10":duration.get("P10"),
            "Days to Next P90":duration.get("P90")}
        forecast_status=("Conditional 160-run review-error simulation, daily "
                         f"lessons cap {daily_target}; review all due items "
                         "immediately, lessons when unlocked. Not an actual "
                         "promise or calibrated confidence interval."
                         if simulation.get("valid") else
                         "No estimate: "+str(simulation.get("reason")))
        value=props("Name",f"WK Level {lv}",
                    {**row,**(guru_numbers if is_current else {}),**days_fields},
                    dates={"Last Synced":now,
                           **(guru_dates if is_current else {}),
                           **unlock_fields},
                    rich={**(guru_rich if is_current else {}),
                          "Unlock Forecast Status":forecast_status})
        upsert(detail_id,"Name",f"WK Level {lv}",value,notion_token)
    print("Conditional unlock forecast local date:", unlock_estimate["date"],
          "already available:", unlock_estimate["available_now"],
          "additional possible:", unlock_estimate["potential_today"],
          "earliest:", unlock_estimate["first_unlock_at"])
    for item in config:
        target = int(item["target_wk_level"])
        if target < 1 or target > 60:
            raise ValueError("Milestone target must be a WaniKani level 1..60")
        title = item["name"]
        # There is no factual future review timetable for the locked Kanji in
        # WK5..WK30. Remove prior invented dates: 7 days/level and historic
        # lessons/day ARE NOT valid forecasts for not-yet-unlocked levels.
        # Instead publish a separately named CONDITIONAL SRS scenario
        # supported by actual subject dependencies and published SRS timings.
        if granted_level is not None and granted_level<target:
            scenario_gate={"at":None,"status":(
                f"Subscription currently grants WK{granted_level}, below target WK{target}.")}
            scenario_full=dict(scenario_gate)
        else:
            scenario_gate=conditional_earliest(
                subjects,assigned,level,target,now,tz,daily_target,srs_intervals,
                lessons_today=activity["Lessons Today"],include_vocab=False)
            scenario_full=conditional_earliest(
                subjects,assigned,level,target,now,tz,daily_target,srs_intervals,
                lessons_today=activity["Lessons Today"],include_vocab=True)
        conditional_level=scenario_gate.get("at")
        conditional_full=scenario_full.get("at")
        result_pair=simulation.get("targets",{}).get(target,{})
        sim_gate=result_pair.get("gate",{})
        sim_full=result_pair.get("full",{})
        sim_dates={
            "Sim Level P10":sim_gate.get("P10"),
            "Sim Level P50":sim_gate.get("P50"),
            "Sim Level P90":sim_gate.get("P90"),
            "Sim Full P10":sim_full.get("P10"),
            "Sim Full P50":sim_full.get("P50"),
            "Sim Full P90":sim_full.get("P90")}
        rate=simulation.get("accuracy_rates",{})
        sample_counts=simulation.get("accuracy_samples",{})
        kanji_rate=rate.get("kanji") or (None,None)
        radical_rate=rate.get("radical") or (None,None)
        sim_numbers={
            "Sim Trials":simulation["trials"] if simulation["valid"] else None,
            "Sim Accuracy Kanji Meaning":(
                round(100*kanji_rate[0],2) if kanji_rate[0] is not None else None),
            "Sim Accuracy Kanji Reading":(
                round(100*kanji_rate[1],2) if kanji_rate[1] is not None else None),
            "Sim Accuracy Radical Meaning":(
                round(100*radical_rate[0],2) if radical_rate[0] is not None else None),
            "Sim Accuracy Kanji Samples":(
                (sample_counts.get("kanji") or {}).get("meaning")),
            "Sim Accuracy Radical Samples":(
                (sample_counts.get("radical") or {}).get("meaning"))}
        sim_status=("Conditional stochastic simulation from observed subject "
                    "accuracy; NOT a factual or guaranteed completion date."
                    if simulation["valid"] else
                    "Not estimable: "+str(simulation.get("reason")))
        # Do NOT represent conditional all-correct schedules as actual ETA.
        achieved=level>=target
        eta=forecast(target,level,None,None,now,progressions) if achieved else None
        actual_full_eta=eta if achieved else None
        target_eta=None
        full_target_eta=None
        eta_status=("Achieved" if achieved else
                    "Not estimable: future Kanji reviews are not scheduled")
        full_eta_status=("Achieved" if achieved else
                         "Not estimable: future lesson unlocks are unknown")
        method=("Observed WK stage/available_at and Kanji-specific historical "
                "accuracy support only current-level P10/P50/P90 Guru scenarios. "
                "The milestone SRS Scenario dates are conditional calendars "
                "computed from actual subject component prerequisites, API SRS "
                "intervals and a strict 15 lessons/day upper limit. ALL future "
                "reviews must be correct and on time; future unlocks/attendance "
                "and correctness are unobserved. Thus no actual or target "
                "milestone ETA is published for WK levels not yet reached. "
                "No 7-day-per-level heuristic. No claim of JLPT readiness. "
                "Sim Level/Full P10,P50,P90 are 160 stochastic review-outcome "
                "scenarios across all future WK levels with per-type historical "
                "accuracy; ALL reviews on-time and available lessons started "
                "immediately are UNOBSERVED user-behavior conditions, not facts.")
        evidence=(f"Observed current WK={level}, Kanji Guru="
                  f"{guru_projection['Guru Passed']}/{guru_projection['Guru Required']}; "
                  f"Kanji meaning accuracy={guru_projection['Kanji Meaning Accuracy']}%, "
                  f"reading={guru_projection['Kanji Reading Accuracy']}%; "
                  f"current-level Guru P50={guru_projection['Gate P50']}; "
                  f"scenario level={conditional_level} ({scenario_gate['status']}); "
                  f"scenario full={conditional_full} ({scenario_full['status']}); "
                  f"Monte Carlo P10/P50/P90={sim_dates}; "
                  f"Accuracy samples={simulation.get('accuracy_samples')}; "
                  f"model validity={simulation['valid']}; "
                  f"model reason={simulation.get('reason')}. "
                  "Future milestone actual ETA UNKNOWN. Only currently scheduled "
                  "reviews are facts; later due times and outcomes are conditional.")
        days_left = max(0, (eta - now.date()).days) if eta else None
        basis=(f"WaniKani level proxy, NOT JLPT readiness. "
               f"Actual new lessons/day: 7d total={activity['Lessons 7d Avg']}, "
               f"radical={activity['Radical 7d Avg']}, kanji={activity['Kanji 7d Avg']}, "
               f"vocab/kana={activity['Vocab 7d Avg']}; "
               f"30d total={actual}, gate R+K={activity['Gate 30d Avg']}. "
               f"Gate ETA pace={gate_rate} ({gate_source}); "
               f"full ETA pace={full_rate} ({full_source}); target={daily_target}; "
               f"tracking start={tracking_start}, elapsed={activity['Elapsed Days']} days. "
               f"Inventory from WaniKani API, perfect-review earliest Guru={earliest_gate}; "
               f"Kanji review accuracy-adjusted Guru P50={guru_projection['Gate P50']}; "
               f"Kanji meaning accuracy={guru_projection['Kanji Meaning Accuracy']}, "
               f"reading accuracy={guru_projection['Kanji Reading Accuracy']}. "
               f"Future SRS progression uses API stage intervals (not 7d/level). "
               f"Future milestone ETA withheld; staged scenario only. "
               f"Scenario level={conditional_level}; scenario full={conditional_full}.")
        extra=milestone_inventory(levels,level,target)
        # WK4 Guru 90/100 applies to CURRENT level, not to WK20/WK30.
        # Avoid copying WK4 dates into unrelated milestone rows.
        primary=(target==min(int(x["target_wk_level"]) for x in config))
        row_guru_dates=(guru_dates if primary else
                        {key:None for key in guru_dates})
        row_guru_rich=(guru_rich if primary else {
            "Guru 90 ETA Range":f"Current WK{level} Guru forecast is shown only in the N5 row.",
            "Guru 100 ETA Range":f"Current WK{level} Guru forecast is shown only in the N5 row.",
            "Guru Forecast Note":f"Not a Guru forecast for milestone WK{target}."})
        properties = props("Milestone", title,
                           {"Target WK Level": target, "Progress Pct": round(min(100,level/target*100),1),
                            "Days Left": days_left,"Actual Pace":actual,"Recent Pace (7d)":activity["Lessons 7d Avg"],"Lessons/Level":volume,
                            "Review Accuracy New":review["Review Accuracy New"],
                            **guru_numbers,
                            "Gate Pace (7d)":activity["Gate 7d Avg"],
                            "Gate Pace (30d)":activity["Gate 30d Avg"],
                            "Radical Pace (7d)":activity["Radical 7d Avg"],
                            "Kanji Pace (7d)":activity["Kanji 7d Avg"],
                            "Vocab Pace (7d)":activity["Vocab 7d Avg"],
                            **sim_numbers,**extra},
                           dates={"Start": tracking_start, "ETA": eta, "Target ETA":target_eta,
                                  "Actual Full Lessons ETA":actual_full_eta,
                                  "Target Full Lessons ETA":full_target_eta,
                                  "Next Gate Earliest":earliest_gate,**row_guru_dates,
                                  "SRS Scenario Level Date":conditional_level,
                                  "SRS Scenario Full Date":conditional_full,
                                  **sim_dates},
                           rich={"Basis": basis,"ETA Status":eta_status,
                                 "Full ETA Status":full_eta_status,
                                 "Forecast Method":method,
                                 "Evidence & Limits":evidence,
                                 "Sim Conditions":simulation.get("conditions",""),
                                 "Sim Status":sim_status,**row_guru_rich})
        print(title, upsert(milestones_id, "Milestone", title, properties, notion_token),
              "ETA:", eta or "insufficient history")
        n5_dashboard_id=os.environ.get("NOTION_N5_DASHBOARD_PAGE_ID", "").strip()
        if target == 10 and n5_dashboard_id:
            cards, stamp = card_values(
                level, target,
                {**extra, "ETA":eta, "Target ETA":target_eta,
                 "Actual Full Lessons ETA":actual_full_eta,
                 "Target Full Lessons ETA":full_target_eta,
                 "SRS Scenario Level Date":conditional_level,
                 "SRS Scenario Full Date":conditional_full,
                 **sim_dates},
                gate, activity, counts, review, now, tz,
                daily_target=daily_target, daily_reviews=daily_reviews,
                guru_forecast=guru_projection,
                simulation=simulation,sim_target=target)
            cards.update(unlock_cards(unlock_estimate,tz))
            updates=update_n5_dashboard(notion, notion_token, n5_dashboard_id,
                                        cards, stamp)
            print("N5 dashboard cards updated:", updates)
    if n5_dashboard_id and daily_source_id:
        # Render one native SVG image holding BOTH daily lessons and 7-day pace.
        # Does not consume Notion Free's single interactive Chart-view allowance.
        # Chart's left edge stays at the plan's tracking-start calendar date
        # (18 Oct 2026), even after the first 30 days.
        if update_daily_chart(notion,notion_token,n5_dashboard_id,
                              chart_days,now,tz):
            print("N5 combined daily chart image updated:",
                  len(chart_days),"local calendar days")
        if update_discipline_chart(notion,notion_token,n5_dashboard_id,
                                   chart_days,now,tz):
            print("N5 Lessons vs Reviews chart updated:",
                  len(chart_days),"local calendar days; "
                  "reviews approximate from checkpoint windows")
    print("Success: Notion has been synchronized.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        sys.exit(1)
