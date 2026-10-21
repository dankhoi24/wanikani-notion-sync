"""Live N5 dashboard cards backed by the existing WaniKani sync metrics.

Only the heading text inside tagged native Notion callout cards is updated.
The page's handmade layout, colors, explanatory text, and linked views are kept.
"""
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

CARD_LABELS = (
    "MONTE CARLO · WK10 LEVEL",
    "MONTE CARLO · WK10 FULL",
    "MONTE CARLO · ACCURACY SOURCE",
    "MONTE CARLO · STATUS",
    "SRS SCENARIO · WK10 LEVEL",
    "SRS SCENARIO · WK10 FULL",
    "GURU 90% ETA · ACCURACY",
    "GURU 100% ETA · ACCURACY",
    "KANJI MEANING ACCURACY",
    "KANJI READING ACCURACY",
    "KANJI REVIEW SUCCESS · ESTIMATE",
    "LESSONS HÔM NAY",
    "REVIEWS HÔM NAY · ƯỚC TÍNH",
    "LESSONS HÔM NAY THEO LOẠI",
    "LÊN LEVEL · ETA THỰC TẾ",
    "LÊN LEVEL · TARGET ETA",
    "FULL LESSONS · ETA THỰC TẾ",
    "FULL LESSONS · TARGET ETA",
    "LEVEL TIẾN ĐỘ",
    "GURU · LEVEL HIỆN TẠI",
    "LESSONS CÒN LẠI",
    "GATE CÒN LẠI",
    "TỔNG PACE 7 NGÀY",
    "GATE PACE 7 NGÀY",
    "RADICAL PACE 7 NGÀY",
    "KANJI PACE 7 NGÀY",
    "VOCAB PACE 7 NGÀY",
    "REVIEW ACCURACY MỚI",
    "REVIEW ACCURACY LỊCH SỬ",
    "REVIEWS SẮP ĐẾN",
)


def date_label(value):
    return value.strftime("%d/%m/%Y") if value else "— Chưa có ETA"


def number_label(value):
    return format(int(value or 0), ",").replace(",", ".")


def pace_label(value):
    return f"{value or 0:g} bài/ngày"


def bar(pct, width=12):
    pct = max(0, min(100, float(pct)))
    filled = round(pct * width / 100)
    return "━" * filled + "─" * (width - filled)


def card_values(level, target, milestone, gate, activity, counts, review,
                now, timezone, daily_target=None, daily_reviews=None, guru_forecast=None,
                simulation=None,sim_target=None):
    """Create source-of-truth KPI card values; never infer Guru from lesson starts."""
    passed = milestone.get("Next Gate Passed") or 0
    required = milestone.get("Next Gate Required") or 0
    guru_pct = 100 * passed / required if required else 100
    level_pct = min(100, 100 * level / target)
    accuracy_new = review.get("Review Accuracy New")
    accuracy_all = counts.get("Accuracy Pct")
    due_now, due_24h = counts.get("Reviews Now") or 0, counts.get("Reviews Next 24h") or 0
    daily_lessons=activity.get("Lessons Today") or 0
    configured_target=daily_target or 15
    pct=min(100,100*daily_lessons/configured_target)
    review_count=(daily_reviews or {}).get("Reviews Today Approx")
    if review_count is None:
        reviews_label="— Chưa đủ checkpoint"
    else:
        reviews_label=f"≈ {number_label(review_count)} review"
    guru=guru_forecast or {}
    def guru_card(prefix):
        p50=guru.get(prefix+" P50")
        p10=guru.get(prefix+" P10")
        p90=guru.get(prefix+" P90")
        if p50 is None or p10 is None or p90 is None:
            return "— Chưa đủ dữ liệu SRS"
        return (p50.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y")+
                "\nP10–P90: "+
                p10.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y")+
                " – "+p90.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y"))
    def kanji_percent(key):
        v=guru.get(key)
        return f"{v:.1f}%" if v is not None else "— Chưa có dữ liệu"
    def scenario_local_date(value):
        if isinstance(value,datetime) and value.tzinfo is not None:
            return value.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y")
        return date_label(value)
    sim=simulation or {}
    projected=(sim.get("targets") or {}).get(sim_target,{})
    def mc_card(part):
        dates=projected.get(part,{})
        if not sim.get("valid") or not dates:
            return "— Chưa có kết quả hợp lệ"
        def fmt(key):
            return dates[key].astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y")
        return (f"P50: {fmt('P50')}\\n"
                f"P10–P90: {fmt('P10')} – {fmt('P90')}")
    rate=sim.get("accuracy_rates") or {}
    k=rate.get("kanji")
    rad=rate.get("radical")
    source=(f"Kanji Meaning: {100*k[0]:.1f}% · Reading: {100*k[1]:.1f}%"
            f"\\nRadical Meaning: {100*rad[0]:.1f}%"
            if k and rad else "— Không đủ Radical/Kanji accuracy")
    sim_status=(f"{sim.get('trials',0)} lượt mô phỏng · điều kiện, không bảo đảm"
                if sim.get("valid") else "Không thể mô phỏng: "+
                  str(sim.get("reason","thiếu dữ liệu"))[:140])
    result = {
        "MONTE CARLO · WK10 LEVEL":mc_card("gate"),
        "MONTE CARLO · WK10 FULL":mc_card("full"),
        "MONTE CARLO · ACCURACY SOURCE":source,
        "MONTE CARLO · STATUS":sim_status,
        "SRS SCENARIO · WK10 LEVEL":(
            scenario_local_date(milestone.get("SRS Scenario Level Date"))+
            " · nếu review luôn đúng & đúng hạn"),
        "SRS SCENARIO · WK10 FULL":(
            scenario_local_date(milestone.get("SRS Scenario Full Date"))+
            " · nếu review luôn đúng & đúng hạn"),
        "GURU 90% ETA · ACCURACY":guru_card("Gate"),
        "GURU 100% ETA · ACCURACY":guru_card("All"),
        "KANJI MEANING ACCURACY":kanji_percent("Kanji Meaning Accuracy"),
        "KANJI READING ACCURACY":kanji_percent("Kanji Reading Accuracy"),
        "KANJI REVIEW SUCCESS · ESTIMATE":kanji_percent("Kanji Review Success Pct"),
        "LESSONS HÔM NAY": f"{int(daily_lessons)} / {configured_target} bài · {pct:.0f}%\n{bar(pct)}",
        "REVIEWS HÔM NAY · ƯỚC TÍNH": reviews_label,
        "LESSONS HÔM NAY THEO LOẠI": (
            f"Radical {activity.get('Radical Lessons Today',0)} · "
            f"Kanji {activity.get('Kanji Lessons Today',0)} · "
            f"Vocab {activity.get('Vocab Lessons Today',0)}"),
        "LÊN LEVEL · ETA THỰC TẾ": date_label(milestone.get("ETA")),
        "LÊN LEVEL · TARGET ETA": date_label(milestone.get("Target ETA")),
        "FULL LESSONS · ETA THỰC TẾ": date_label(milestone.get("Actual Full Lessons ETA")),
        "FULL LESSONS · TARGET ETA": date_label(milestone.get("Target Full Lessons ETA")),
        "LEVEL TIẾN ĐỘ": f"WK {level} / WK {target} · {level_pct:.0f}%\n{bar(level_pct)}",
        "GURU · LEVEL HIỆN TẠI": (
            f"{int(passed)} / {int(required)} · {guru_pct:.0f}%\n{bar(guru_pct)}"),
        "LESSONS CÒN LẠI": f"{number_label(milestone.get('Remaining Lessons (Full)'))} bài",
        "GATE CÒN LẠI": f"{number_label(milestone.get('Gate Lessons Remaining'))} bài",
        "TỔNG PACE 7 NGÀY": pace_label(activity.get("Lessons 7d Avg")),
        "GATE PACE 7 NGÀY": pace_label(activity.get("Gate 7d Avg")),
        "RADICAL PACE 7 NGÀY": pace_label(activity.get("Radical 7d Avg")),
        "KANJI PACE 7 NGÀY": pace_label(activity.get("Kanji 7d Avg")),
        "VOCAB PACE 7 NGÀY": pace_label(activity.get("Vocab 7d Avg")),
        "REVIEW ACCURACY MỚI": (
            f"{accuracy_new:.1f}% · kể từ baseline"
            if accuracy_new is not None else "— Chưa đủ dữ liệu"),
        "REVIEW ACCURACY LỊCH SỬ": (
            f"{accuracy_all:.1f}% · lịch sử" if accuracy_all is not None else "— Chưa có dữ liệu"),
        "REVIEWS SẮP ĐẾN": f"{due_now} ngay · {due_24h} trong 24h",
    }
    timestamp = now.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M")
    return result, timestamp


def plain(rich_text):
    return "".join(x.get("plain_text", x.get("text", {}).get("content", ""))
                   for x in (rich_text or []))


def get_children(notion, token, block_id):
    """Return all Notion child blocks, including cursor pages."""
    all_blocks = []
    cursor = None
    while True:
        path = f"/blocks/{block_id}/children?page_size=100"
        if cursor:
            path += "&start_cursor=" + quote(cursor, safe="")
        page = notion("GET", path, token)
        all_blocks.extend(page.get("results", []))
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")
        if not cursor:
            raise ValueError("Notion returned has_more with no next_cursor")
    return all_blocks


def patch_text(notion, token, block_id, block_type, old_text, new_text):
    if old_text == new_text:
        return False
    notion("PATCH", f"/blocks/{block_id}", token, {
        block_type: {"rich_text": [{"type": "text", "text": {"content": new_text}}]}})
    return True


def update_n5_dashboard(notion, token, page_id, cards, last_synced):
    """Find KPI cards by their semantic labels and edit only their value headings."""
    found = {}
    header_synced = False
    updated = 0

    def walk(parent_id, depth):
        nonlocal header_synced, updated
        if depth > 5:
            raise ValueError("N5 dashboard nested columns too deep")
        for block in get_children(notion, token, parent_id):
            btype = block.get("type")
            bid = block["id"]
            if btype in ("column_list", "column"):
                walk(bid, depth + 1)
                continue
            if btype != "callout":
                continue
            label = plain(block["callout"].get("rich_text")).strip()
            if label not in cards and "N5 TRACKER" not in label:
                continue
            children = get_children(notion, token, bid)
            if label in cards:
                if label in found:
                    raise ValueError(f"Duplicate N5 KPI card: {label}")
                heading = next((b for b in children if b.get("type") == "heading_3"), None)
                if not heading:
                    raise ValueError(f"N5 KPI card has no value heading: {label}")
                old = plain(heading["heading_3"].get("rich_text"))
                if patch_text(notion, token, heading["id"], "heading_3", old, cards[label]):
                    updated += 1
                found[label] = True
            elif "N5 TRACKER" in label:
                for child in children:
                    if child.get("type") == "paragraph":
                        old = plain(child["paragraph"].get("rich_text"))
                        if old.startswith("📡 Đồng bộ gần nhất:"):
                            new = f"📡 Đồng bộ gần nhất: {last_synced} (giờ Việt Nam)"
                            if patch_text(notion, token, child["id"], "paragraph", old, new):
                                updated += 1
                            header_synced = True
    walk(page_id, 0)
    missing = set(cards) - set(found)
    if missing:
        raise ValueError(f"N5 KPI cards missing: {sorted(missing)}")
    if not header_synced:
        raise ValueError("N5 dashboard's last-synced indicator is missing")
    return updated
