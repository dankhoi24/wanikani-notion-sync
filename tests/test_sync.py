import unittest
from datetime import date, datetime, timezone
from sync import median_level_days, forecast, metrics, props


class SyncTests(unittest.TestCase):
    def test_recent_median_pace(self):
        history = [{"data": {"level": n, "started_at": f"2026-09-{d:02d}T00:00:00Z"}}
                   for n,d in [(4,1),(5,8),(6,17),(7,25)]]
        pace, count, current_start = median_level_days(history, 7)
        self.assertEqual((pace, count, current_start.date()), (8, 3, date(2026,9,25)))

    def test_requires_two_intervals(self):
        history = [{"data": {"level": 1, "started_at": "2026-10-01T00:00:00Z"}}]
        pace, count, _ = median_level_days(history, 1)
        self.assertIsNone(pace)
        self.assertEqual(count, 0)

    def test_eta(self):
        now = datetime(2026,10,18,tzinfo=timezone.utc)
        current_start = datetime(2026,10,15,tzinfo=timezone.utc)
        self.assertEqual(forecast(10, 8, 8, current_start, now), date(2026,10,31))
        self.assertEqual(forecast(5, 8, None, None, now), now.date())
        self.assertIsNone(forecast(20, 8, None, None, now))
        past = [{"data": {"level": 5, "started_at": "2026-05-04T00:00:00Z"}}]
        self.assertEqual(forecast(5, 8, None, None, now, past), date(2026, 5, 4))

    def test_srs_review_counts_and_weighted_accuracy(self):
        now = datetime(2026,10,18,10,tzinfo=timezone.utc)
        assignments = [{"data": {"srs_stage":3, "subject_type":"kanji"}},
                       {"data": {"srs_stage":9, "subject_type":"vocabulary"}},
                       {"data": {"srs_stage":0, "subject_type":"kanji"}}]
        stats = [{"data": {"subject_type":"kanji", "meaning_correct":8,
                           "meaning_incorrect":2, "reading_correct":5, "reading_incorrect":5}}]
        summary = {"reviews":[{"available_at":"2026-10-18T09:00:00Z","subject_ids":[1,2]},
                              {"available_at":"2026-10-18T12:00:00Z","subject_ids":[3]}]}
        m = metrics(assignments,stats,summary,now)
        self.assertEqual((m["Learned Kanji"],m["Learned Vocab"]), (1,1))
        self.assertEqual((m["Apprentice"],m["Burned"]), (1,1))
        self.assertEqual(m["Accuracy Pct"], 65.0)
        self.assertEqual((m["Reviews Now"],m["Reviews Next 24h"]), (2,1))

    def test_null_date_when_unknown(self):
        p = props("Milestone", "Test", {"Days Left":None},dates={"ETA":None})
        self.assertIsNone(p["ETA"]["date"])


    def test_dynamic_activity(self):
        from sync import lesson_activity,lessons_per_level,workload_eta
        now=datetime(2026,10,18,16,30,tzinfo=timezone.utc)
        assignments=[{"data":{"subject_type":"vocabulary","started_at":"2026-10-18T16:00:00Z"}},
                     {"data":{"subject_type":"radical","started_at":"2026-10-17T16:00:00Z"}},
                     {"data":{"subject_type":"kanji","started_at":None}}]
        stats,starts=lesson_activity(assignments,now,"Asia/Ho_Chi_Minh",date(2026,10,17))
        self.assertEqual(stats["Lessons Today"],1)
        self.assertEqual(stats["Lessons 7d Avg"],1.0)
        self.assertEqual(stats["Vocab 7d Avg"],0.5)
        self.assertEqual(stats["Radical 7d Avg"],0.5)
        self.assertEqual(stats["Kanji 7d Avg"],0)
        self.assertEqual(stats["Gate 7d Avg"],0.5)
        self.assertEqual(stats["Radical Lessons Today"],0)
        self.assertEqual(stats["Vocab Lessons Today"],1)
        self.assertIsNone(workload_eta(10,4,None,15,None,starts,now))

    def test_tracking_starts_today(self):
        from sync import lesson_activity
        now=datetime(2026,10,18,16,30,tzinfo=timezone.utc)
        assignments=[{"data":{"subject_type":"vocabulary","started_at":"2026-10-18T15:00:00Z"}},
                     {"data":{"subject_type":"vocabulary","started_at":"2026-10-17T15:00:00Z"}}]
        data,_=lesson_activity(assignments,now,"Asia/Ho_Chi_Minh",date(2026,10,18))
        self.assertEqual(data["Lessons Today"],1)
        self.assertEqual(data["Lessons 7d Avg"],1.0)
        self.assertEqual(data["Lessons 30d Avg"],1.0)
        self.assertEqual(data["Radical 7d Avg"],0)
        self.assertEqual(data["Kanji 7d Avg"],0)
        self.assertEqual(data["Vocab 7d Avg"],1.0)
        self.assertEqual(data["Gate 7d Avg"],0)
        self.assertEqual(data["Elapsed Days"],1)

    def test_no_starts_uses_provisional_eta(self):
        from sync import lesson_activity,choose_eta_rate
        now=datetime(2026,10,18,15,tzinfo=timezone.utc)
        data,_=lesson_activity([{"data":{"subject_type":"kanji","started_at":"2026-10-17T12:00:00Z"}}],
                                now,"Asia/Ho_Chi_Minh",date(2026,10,18))
        self.assertEqual(data["Lessons Today"],0)
        self.assertEqual(data["Lessons 7d Avg"],0)
        self.assertEqual(data["Lessons 30d Avg"],0)
        self.assertEqual(choose_eta_rate(0,0,15),(None,"no_new_lessons_since_tracking_start"))

    def test_recent_ignores_before_start_includes_today(self):
        from sync import lesson_activity,choose_eta_rate
        now=datetime(2026,10,19,12,tzinfo=timezone.utc)
        items=[{"data":{"subject_type":"vocabulary","started_at":"2026-10-17T12:00:00Z"}}]*10
        items += [{"data":{"subject_type":"kanji","started_at":"2026-10-18T12:00:00Z"}}]*4
        items += [{"data":{"subject_type":"vocabulary","started_at":"2026-10-19T10:00:00Z"}}]*2
        data,_=lesson_activity(items,now,"Asia/Ho_Chi_Minh",date(2026,10,18))
        self.assertEqual(data["Elapsed Days"],2)
        self.assertEqual(data["Lessons Today"],2)
        self.assertEqual(data["Lessons 7d Avg"],3.0)
        self.assertEqual(data["Lessons 30d Avg"],3.0)
        self.assertEqual(data["Gate 7d Avg"],2.0)
        self.assertEqual(data["Kanji 7d Avg"],2.0)
        self.assertEqual(data["Vocab 7d Avg"],1.0)
        self.assertEqual(data["Radical 7d Avg"],0)
        self.assertEqual(choose_eta_rate(3.0,3.0,15),(3.0,"observed_recent_7d"))

    def test_empty_recent_week_falls_back_to_30d(self):
        from sync import choose_eta_rate
        self.assertEqual(choose_eta_rate(0,2.4,15),(2.4,"observed_30d_fallback"))

    def test_five_lessons_split_across_types_and_etas(self):
        from sync import lesson_activity, choose_eta_rate
        from wk_detailed import forecast_with_inventory,forecast_full_lessons
        from datetime import timedelta
        now=datetime(2026,10,18,12,tzinfo=timezone.utc)
        assignments=([
            {"data":{"subject_type":kind,"started_at":"2026-10-18T10:00:00Z"}}
            for kind in (["kanji"]*3 + ["vocabulary","kana_vocabulary"])
        ] + [
            {"data":{"subject_type":"radical","started_at":"2026-10-17T10:00:00Z"}}
        ])
        activity,_=lesson_activity(assignments,now,"Asia/Ho_Chi_Minh",date(2026,10,18))
        self.assertEqual(activity["Lessons Today"],5)
        self.assertEqual(activity["Radical 7d Avg"],0)
        self.assertEqual(activity["Kanji 7d Avg"],3)
        self.assertEqual(activity["Vocab 7d Avg"],2)
        self.assertEqual(activity["Gate 7d Avg"],3)
        self.assertEqual(activity["Gate 30d Avg"],3)
        gate_rate,_=choose_eta_rate(activity["Gate 7d Avg"],activity["Gate 30d Avg"],15)
        full_rate,_=choose_eta_rate(activity["Lessons 7d Avg"],activity["Lessons 30d Avg"],15)
        self.assertEqual((gate_rate,full_rate),(3,5))
        rows=[{"Level":4,"Kanji Total":36,"Lessons Total":100,
               "Gate Lessons Remaining":6,"Lessons Remaining":100,"Kanji To Guru":23}]
        srs_floor=now+timedelta(days=4)
        gate_actual=forecast_with_inventory(rows,4,5,gate_rate,now,srs_floor)
        gate_target=forecast_with_inventory(rows,4,5,15,now,srs_floor)
        full_actual=forecast_full_lessons(rows,4,5,full_rate,now,gate_actual)
        full_target=forecast_full_lessons(rows,4,5,15,now,gate_target)
        self.assertEqual(gate_actual,(now+timedelta(days=4)).date())
        self.assertEqual(full_actual,(now+timedelta(days=20)).date())
        self.assertEqual(full_target,(now+timedelta(days=7)).date())
        self.assertLess(gate_actual,full_actual)
        self.assertGreater(full_actual,full_target)

    def test_only_vocabulary_does_not_create_a_level_up_eta(self):
        from sync import lesson_activity,choose_eta_rate
        from wk_detailed import forecast_with_inventory
        now=datetime(2026,10,18,12,tzinfo=timezone.utc)
        assignments=[{"data":{"subject_type":"vocabulary",
                              "started_at":"2026-10-18T10:00:00Z"}}]*5
        activity,_=lesson_activity(assignments,now,"Asia/Ho_Chi_Minh",date(2026,10,18))
        self.assertEqual(activity["Gate 7d Avg"],0)
        self.assertEqual(activity["Lessons 7d Avg"],5)
        rate,_=choose_eta_rate(activity["Gate 7d Avg"],activity["Gate 30d Avg"],15)
        self.assertIsNone(rate)
        self.assertIsNone(forecast_with_inventory(
            [{"Level":4,"Kanji Total":36,"Lessons Total":100,"Gate Lessons Remaining":6}],
            4,5,rate,now))

    def test_zero_lessons_produces_zero_for_all_categories(self):
        from sync import lesson_activity
        now=datetime(2026,10,18,12,tzinfo=timezone.utc)
        activity,_=lesson_activity([],now,"Asia/Ho_Chi_Minh",date(2026,10,18))
        for key in ("Lessons 7d Avg","Radical 7d Avg","Kanji 7d Avg",
                    "Vocab 7d Avg","Gate 7d Avg"):
            self.assertEqual(activity[key],0)

    def test_unlock_forecast_apprentice4_prerequisites_only(self):
        from unlock_forecast import estimate_unlocks_today,unlock_cards
        now=datetime(2026,10,18,18,tzinfo=timezone.utc) # 19/10 01:00 VN
        subjects={
          1:{"type":"radical","level":4,"component_subject_ids":[]},
          2:{"type":"radical","level":4,"component_subject_ids":[]},
          3:{"type":"kanji","level":4,"component_subject_ids":[1,2]},
          4:{"type":"vocabulary","level":4,"component_subject_ids":[3]},
          5:{"type":"vocabulary","level":4,"component_subject_ids":[1]},
          6:{"type":"vocabulary","level":4,"component_subject_ids":[2]},
          7:{"type":"kanji","level":4,"component_subject_ids":[1]},
          8:{"type":"kanji","level":5,"component_subject_ids":[1]},
          9:{"type":"vocabulary","level":4,"component_subject_ids":[12]},
          10:{"type":"vocabulary","level":4,"component_subject_ids":[13]},
          12:{"type":"kanji","level":4,"component_subject_ids":[]},
          13:{"type":"kanji","level":4,"component_subject_ids":[]}
        }
        assignments={
           1:{"passed_at":"2026-10-14T00:00:00Z","srs_stage":5},
           2:{"srs_stage":4,"available_at":"2026-10-19T05:00:00Z"},
           3:{"srs_stage":3,"started_at":"2026-10-18T10:00:00Z"},
           4:{"srs_stage":0},
           5:{"unlocked_at":"2026-10-14T00:00:00Z"},
           6:{"srs_stage":0},
           7:{"srs_stage":0},
           9:{"srs_stage":0},
           10:{"srs_stage":0},
           12:{"srs_stage":4,"available_at":"2026-10-19T17:30:00Z"},
           13:{"srs_stage":3,"available_at":"2026-10-19T04:00:00Z"}
        }
        estimated=estimate_unlocks_today(subjects,assignments,4,now,"Asia/Ho_Chi_Minh")
        self.assertEqual(estimated["date"],date(2026,10,19))
        self.assertEqual(estimated["available_now"],1)
        self.assertEqual(estimated["potential_today"],1)
        self.assertEqual(estimated["by_type"]["vocabulary"],1)
        self.assertEqual(estimated["by_type"]["kanji"],0)
        self.assertEqual(estimated["first_unlock_at"],
                         datetime(2026,10,19,5,tzinfo=timezone.utc))
        cards=unlock_cards(estimated,"Asia/Ho_Chi_Minh")
        self.assertIn("1 bài",cards["LESSONS CÓ THỂ MỞ KHÓA HÔM NAY"])
        self.assertIn("12:00",cards["MỞ KHÓA SỚM NHẤT"])

    def test_unlock_forecast_no_guess_for_missing_review_time(self):
        from unlock_forecast import estimate_unlocks_today
        now=datetime(2026,10,18,18,tzinfo=timezone.utc)
        s={1:{"type":"radical","level":4,"component_subject_ids":[]},
           2:{"type":"kanji","level":4,"component_subject_ids":[1]}}
        a={1:{"srs_stage":4,"available_at":None}}
        f=estimate_unlocks_today(s,a,4,now,"Asia/Ho_Chi_Minh")
        self.assertEqual(f["potential_today"],0)
        self.assertIsNone(f["first_unlock_at"])

    def test_review_today_approx_uses_last_pre_midnight_checkpoint(self):
        from daily_reviews import (most_recent_before_midnight,
                                   estimate_reviews_today)
        now=datetime(2026,10,18,17,21,tzinfo=timezone.utc)
        def checkpoint(at,meaning_correct):
            return {"properties":{
                "Captured At":{"date":{"start":at}},
                "Meaning Correct Total":{"number":meaning_correct},
                "Meaning Incorrect Total":{"number":306},
                "Reading Correct Total":{"number":3913},
                "Reading Incorrect Total":{"number":500}
            }}
        before=checkpoint("2026-10-18T16:31:00Z",3912)
        after=checkpoint("2026-10-18T17:01:00Z",3913)
        prior=checkpoint("2026-10-17T16:00:00Z",3900)
        rows=[prior,after,before]
        baseline=most_recent_before_midnight(rows,now,"Asia/Ho_Chi_Minh")
        self.assertIs(baseline,before)
        counters={"meaning_correct":3913,"meaning_incorrect":306,
                  "reading_correct":3913,"reading_incorrect":500}
        estimate=estimate_reviews_today(counters,baseline,now,"Asia/Ho_Chi_Minh")
        self.assertEqual(estimate["Reviews Today Approx"],1)
        self.assertIn("18/10/2026 23:31",estimate["Reviews Today Window"])
        self.assertIn("KHÔNG phải",estimate["Reviews Today Window"])

    def test_review_today_unknown_without_baseline_or_counter_reset(self):
        from daily_reviews import estimate_reviews_today
        now=datetime(2026,10,18,17,21,tzinfo=timezone.utc)
        counters={"meaning_correct":5,"meaning_incorrect":0,
                  "reading_correct":5,"reading_incorrect":0}
        self.assertIsNone(estimate_reviews_today(
            counters,None,now,"Asia/Ho_Chi_Minh")["Reviews Today Approx"])
        old={"properties":{
            "Captured At":{"date":{"start":"2026-10-18T16:31:00Z"}},
            "Meaning Correct Total":{"number":6},
            "Meaning Incorrect Total":{"number":0},
            "Reading Correct Total":{"number":5},
            "Reading Incorrect Total":{"number":0}
        }}
        self.assertIsNone(estimate_reviews_today(
            counters,old,now,"Asia/Ho_Chi_Minh")["Reviews Today Approx"])

    def test_n5_today_cards_display_lesson_goal_and_approx_reviews(self):
        from n5_dashboard import card_values
        now=datetime(2026,10,18,17,21,tzinfo=timezone.utc)
        a={"Lessons Today":15,"Radical Lessons Today":2,
           "Kanji Lessons Today":3,"Vocab Lessons Today":10}
        cards,_=card_values(4,10,{}, {}, a, {}, {}, now,
                            "Asia/Ho_Chi_Minh",daily_target=15,
                            daily_reviews={"Reviews Today Approx":7})
        self.assertIn("15 / 15",cards["LESSONS HÔM NAY"])
        self.assertIn("100%",cards["LESSONS HÔM NAY"])
        self.assertEqual(cards["REVIEWS HÔM NAY · ƯỚC TÍNH"],"≈ 7 review")
        self.assertIn("Kanji 3",cards["LESSONS HÔM NAY THEO LOẠI"])

    def test_daily_lesson_history_starts_oct18_and_uses_vietnam_dates(self):
        from daily_lessons import history_rows
        now=datetime(2026,10,18,18,30,tzinfo=timezone.utc)  # 19 Oct 01:30 VN
        rows=[
            {"data":{"started_at":"2026-10-18T16:40:00Z","subject_type":"kanji"}}, # Oct18 VN
            {"data":{"started_at":"2026-10-18T13:00:00Z","subject_type":"kanji"}}, # Oct18
            {"data":{"started_at":"2026-10-18T17:10:00Z","subject_type":"vocabulary"}}, # Oct19
            {"data":{"started_at":"2026-10-17T10:00:00Z","subject_type":"radical"}}, # before plan
            {"data":{"started_at":"2026-10-18T20:00:00Z","subject_type":"kana_vocabulary"}}, # future
        ]
        data=history_rows(rows,now,"Asia/Ho_Chi_Minh",date(2026,10,18),15)
        self.assertEqual([x["Day"].isoformat() for x in data],
                         ["2026-10-18","2026-10-19"])
        self.assertEqual([x["Lessons Today"] for x in data],[2,1])
        self.assertEqual([x["Recent Pace (7d)"] for x in data],[2.0,1.5])
        self.assertEqual([x["Kanji Today"] for x in data],[2,0])
        self.assertEqual([x["Vocab Today"] for x in data],[0,1])
        self.assertEqual([x["Daily Target"] for x in data],[15,15])

    def test_daily_lesson_history_includes_zero_days(self):
        from daily_lessons import history_rows
        now=datetime(2026,10,20,12,tzinfo=timezone.utc)
        rows=[{"data":{"started_at":"2026-10-18T12:00:00Z",
                       "subject_type":"kana_vocabulary"}}]
        data=history_rows(rows,now,"Asia/Ho_Chi_Minh",date(2026,10,18),15)
        self.assertEqual([r["Lessons Today"] for r in data],[1,0,0])
        self.assertEqual([r["Recent Pace (7d)"] for r in data],[1.0,0.5,0.33])
        self.assertEqual([r["Vocab Today"] for r in data],[1,0,0])

    def test_daily_lesson_history_rolling_seven_and_recent_backfill(self):
        from daily_lessons import history_rows
        from datetime import timedelta
        now=datetime(2026,10,27,12,tzinfo=timezone.utc)
        starts=[{"data":{"started_at":(
            datetime(2026,10,18,12,tzinfo=timezone.utc)+timedelta(days=d)
            ).isoformat(),"subject_type":"kanji"}} for d in range(10)]
        data=history_rows(starts,now,"Asia/Ho_Chi_Minh",date(2026,10,18),15)
        self.assertEqual(len(data),7)
        self.assertEqual(data[0]["Day"],date(2026,10,21))
        self.assertEqual(data[-1]["Day"],date(2026,10,27))
        self.assertTrue(all(x["Recent Pace (7d)"]==1 for x in data))

    def test_combined_notion_chart_is_one_svg_with_two_series_and_target(self):
        from notion_svg_chart import chart_svg
        sample=[
            {"Day":date(2026,10,18),"Lessons Today":19,
             "Recent Pace (7d)":19,"Daily Target":15},
            {"Day":date(2026,10,19),"Lessons Today":0,
             "Recent Pace (7d)":9.5,"Daily Target":15}
        ]
        svg=chart_svg(sample,datetime(2026,10,18,17,10,tzinfo=timezone.utc),
                      "Asia/Ho_Chi_Minh").decode("utf-8")
        self.assertTrue(svg.startswith("<svg "))
        self.assertIn("18/10",svg)
        self.assertIn("19/10",svg)
        self.assertIn("Lessons Today",svg)
        self.assertIn("Recent Pace (7d)",svg)
        self.assertIn("Daily Target: 15 lessons",svg)
        self.assertEqual(svg.count("</svg>"),1)

    def test_combined_chart_replaces_existing_image_not_duplicate(self):
        from unittest.mock import patch
        from notion_svg_chart import update_daily_chart
        image={"id":"chart-block-id","type":"image","image":{
            "type":"file","file":{"url":"https://notion-static.com/wanikani-daily-lessons-pace.svg"},
            "caption":[]}}
        calls=[]
        def fake_notion(method,path,token,payload=None):
            calls.append((method,path,payload))
            if method=="GET":
                return {"results":[image],"has_more":False}
            return {"object":"block"}
        sample=[{"Day":date(2026,10,18),"Lessons Today":19,
                 "Recent Pace (7d)":19,"Daily Target":15}]
        with patch("notion_svg_chart.send_svg",return_value="some-upload-id") as upload:
            success=update_daily_chart(fake_notion,"fake-token","fake-page-id",
                                       sample,datetime(2026,10,18,15,tzinfo=timezone.utc),
                                       "Asia/Ho_Chi_Minh")
        self.assertTrue(success)
        upload.assert_called_once()
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1][1],"/blocks/chart-block-id")
        self.assertEqual(calls[1][2]["image"]["type"],"file_upload")
        self.assertIn("WK_DAILY_COMBO_CHART",
                      calls[1][2]["image"]["caption"][0]["text"]["content"])

    def test_volume_and_target_projection(self):
        from sync import lessons_per_level,workload_eta
        history=[{"data":{"level":i,"started_at":f"2026-09-{d:02d}T00:00:00Z"}} for i,d in [(1,1),(2,11),(3,21)]]
        starts=[datetime(2026,9,d,tzinfo=timezone.utc) for d in [2,3,4,12,13,14] for _ in range(5)]
        volume,samples=lessons_per_level(starts,history)
        self.assertEqual((volume,samples),(15,2))
        now=datetime(2026,10,18,tzinfo=timezone.utc)
        self.assertIsNotNone(workload_eta(6,3,volume,15,None,starts,now))
        self.assertIsNone(workload_eta(6,3,volume,0,None,starts,now))

from wk_detailed import subjects_by_id,assignments_by_id,inventory,milestone_inventory,review_delta,earliest_next_gate
class DetailedTests(unittest.TestCase):
    def test_gate_inventory(self):
        subs=[{"id":i,"object":"kanji","data":{"level":4,"component_subject_ids":[]}} for i in range(1,11)]
        ass=[{"data":{"subject_id":i,"srs_stage":5,"started_at":"2026-10-01T00:00:00Z"}} for i in range(1,9)]
        row=inventory(subjects_by_id(subs),assignments_by_id(ass),4,4)[0]
        self.assertEqual(row["Kanji Guru Required"],9)
        self.assertEqual(row["Kanji To Guru"],1)
    def test_counter_reset(self):
        base={k:5 for k in ("meaning_correct","meaning_incorrect","reading_correct","reading_incorrect")}
        now={k:3 for k in base}
        self.assertFalse(review_delta(now,base,base)["Counter Valid"])

    def test_inventory_forecast_uses_each_level_not_141(self):
        from wk_detailed import forecast_with_inventory, forecast_full_lessons
        from datetime import timedelta
        now=datetime(2026,10,18,15,0,tzinfo=timezone.utc)
        levels=[
          {"Level":4,"Kanji Total":36,"Lessons Total":100,"Gate Lessons Remaining":6,"Lessons Remaining":78,"Kanji To Guru":23},
          {"Level":5,"Kanji Total":43,"Lessons Total":220,"Gate Lessons Remaining":66,"Lessons Remaining":213,"Kanji To Guru":42},
          {"Level":6,"Kanji Total":37,"Lessons Total":180,"Gate Lessons Remaining":54,"Lessons Remaining":183,"Kanji To Guru":33}]
        bound=now+timedelta(days=4)
        target=forecast_with_inventory(levels,4,7,15,now,bound)
        self.assertEqual(target,(now+timedelta(days=18)).date())
        full=forecast_full_lessons(levels,4,7,15,now,target)
        self.assertEqual(full,(now+timedelta(days=32)).date())
        self.assertIsNone(forecast_with_inventory(levels,4,7,0,now,bound))
        self.assertIsNone(forecast_with_inventory(levels,4,8,15,now,bound))

    def test_actual_eta_with_zero_pace_must_be_blank(self):
        from sync import choose_eta_rate
        from wk_detailed import forecast_with_inventory
        now=datetime(2026,10,18,15,tzinfo=timezone.utc)
        speed,source=choose_eta_rate(0,0,15)
        self.assertIsNone(speed)
        self.assertEqual(source,"no_new_lessons_since_tracking_start")
        self.assertIsNone(forecast_with_inventory(
            [{"Level":4,"Kanji Total":36,"Lessons Total":100,"Gate Lessons Remaining":6}],
            4,5,speed,now))


if __name__ == '__main__':
    unittest.main()
