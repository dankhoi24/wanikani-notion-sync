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
        now=datetime(2026,10,18,17,tzinfo=timezone.utc)
        assignments=[{"data":{"subject_type":"vocabulary","started_at":"2026-10-18T16:00:00Z"}},
                     {"data":{"subject_type":"radical","started_at":"2026-10-17T16:00:00Z"}},
                     {"data":{"subject_type":"kanji","started_at":None}}]
        stats,starts=lesson_activity(assignments,now,"Asia/Ho_Chi_Minh")
        self.assertEqual(stats["Lessons Today"],1)
        self.assertEqual(stats["Lessons 7d Avg"],round(2/7,2))
        self.assertEqual(stats["Vocab 7d Avg"],round(1/7,2))
        self.assertIsNone(workload_eta(10,4,None,15,None,starts,now))

    def test_volume_and_target_projection(self):
        from sync import lessons_per_level,workload_eta
        history=[{"data":{"level":i,"started_at":f"2026-09-{d:02d}T00:00:00Z"}} for i,d in [(1,1),(2,11),(3,21)]]
        starts=[datetime(2026,9,d,tzinfo=timezone.utc) for d in [2,3,4,12,13,14] for _ in range(5)]
        volume,samples=lessons_per_level(starts,history)
        self.assertEqual((volume,samples),(15,2))
        now=datetime(2026,10,18,tzinfo=timezone.utc)
        self.assertIsNotNone(workload_eta(6,3,volume,15,None,starts,now))
        self.assertIsNone(workload_eta(6,3,volume,0,None,starts,now))

if __name__ == '__main__':
    unittest.main()
