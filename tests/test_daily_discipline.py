"""Daily review activity figures are evidence-bounded, never fabricated."""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from daily_discipline import reviews_by_day, fetch_checkpoint_history
from notion_review_chart import discipline_svg, update_discipline_chart


def checkpoint(when,correct):
    return {"properties":{
       "Captured At":{"date":{"start":when}},
       "Meaning Correct Total":{"number":correct}}}


def lesson(day,lessons=0):
    return {"Day":date.fromisoformat(day),"Lessons Today":lessons,
            "Daily Target":15}


class DailyDisciplineTests(unittest.TestCase):
    def test_nearest_midnight_snapshots_and_partial_today(self):
        # VN midnight = 17:00 UTC the previous day.
        now=datetime(2026,10,20,4,30,tzinfo=timezone.utc)
        checks=[checkpoint("2026-10-18T17:30:00Z",100),
                checkpoint("2026-10-19T17:10:00Z",134)]
        rows=reviews_by_day([lesson("2026-10-18",19),
                             lesson("2026-10-19",0),
                             lesson("2026-10-20",7)],
                            checks,{"meaning_correct":142},now,
                            "Asia/Ho_Chi_Minh")
        self.assertIsNone(rows[0]["Reviews Done Approx"])
        self.assertEqual(rows[1]["Reviews Done Approx"],34)
        self.assertEqual(rows[2]["Reviews Done Approx"],8)
        self.assertIn("ngày chưa kết thúc",rows[2]["Review Data Quality"])
        self.assertEqual(rows[1]["Lessons Today"],0)

    def test_exact_zero_vs_missing_and_counter_reset(self):
        now=datetime(2026,10,20,5,tzinfo=timezone.utc)
        checks=[checkpoint("2026-10-18T17:30:00Z",100),
                checkpoint("2026-10-19T17:10:00Z",100)]
        result=reviews_by_day([lesson("2026-10-19")],checks,
            {"meaning_correct":100},now,"Asia/Ho_Chi_Minh")
        self.assertEqual(result[0]["Reviews Done Approx"],0)
        result=reviews_by_day([lesson("2026-10-19")],
                              checks,{"meaning_correct":90},now,
                              "Asia/Ho_Chi_Minh")
        self.assertEqual(result[0]["Reviews Done Approx"],0)
        future=reviews_by_day([lesson("2026-10-20")],checks,
            {"meaning_correct":90},now,"Asia/Ho_Chi_Minh")
        self.assertIsNone(future[0]["Reviews Done Approx"])
        self.assertIn("reset",future[0]["Review Data Quality"])

    def test_far_midnight_checkpoints_do_not_count(self):
        now=datetime(2026,10,20,15,tzinfo=timezone.utc)
        checks=[checkpoint("2026-10-18T22:10:00Z",130),
                checkpoint("2026-10-19T23:10:00Z",150)]
        rows=reviews_by_day([lesson("2026-10-19")],checks,
                            {"meaning_correct":160},now,
                            "Asia/Ho_Chi_Minh")
        self.assertIsNone(rows[0]["Reviews Done Approx"])

    def test_chart_has_dual_axes_and_never_draws_unknown_as_zero(self):
        rows=[{**lesson("2026-10-18",15),"Reviews Done Approx":None},
              {**lesson("2026-10-19",0),"Reviews Done Approx":0},
              {**lesson("2026-10-20",7),"Reviews Done Approx":120}]
        now=datetime(2026,10,20,6,tzinfo=timezone.utc)
        text=discipline_svg(rows,now,"Asia/Ho_Chi_Minh").decode("utf-8")
        self.assertIn("Lessons (left axis)",text)
        self.assertIn("Reviews &#8776; (right axis)",text)
        self.assertIn("Daily Lessons vs Reviews Done",text)
        self.assertIn("18/10",text)
        self.assertIn("?",text)
        self.assertIn("Reviews Done (&#8776;)",text)
        self.assertEqual(text.count("</svg>"),1)

    def test_replace_existing_chart_without_duplicating(self):
        prior={"id":"review-chart","type":"image","image":{"caption":[
            {"plain_text":"WK_DAILY_LESSONS_REVIEWS_CHART"}]}}
        calls=[]
        def fake_notion(method,path,token,payload=None):
            calls.append((method,path,payload))
            if method=="GET":return {"results":[prior],"has_more":False}
            return {}
        rows=[{**lesson("2026-10-20",8),"Reviews Done Approx":55}]
        now=datetime(2026,10,20,6,tzinfo=timezone.utc)
        with patch("notion_review_chart.send_svg",return_value="fake-uuid"):
            self.assertTrue(update_discipline_chart(fake_notion,"x","page",rows,
                                                     now,"Asia/Ho_Chi_Minh"))
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[-1][1],"/blocks/review-chart")
        self.assertEqual(calls[-1][2]["image"]["file_upload"]["id"],"fake-uuid")

    def test_insert_chart_directly_after_old_image(self):
        existing={"id":"old-chart","type":"image","image":{"caption":[
            {"plain_text":"WK_DAILY_COMBO_CHART"}]}}
        calls=[]
        def fake_notion(method,path,token,payload=None):
            calls.append((method,path,payload))
            if method=="GET":return {"results":[existing],"has_more":False}
            return {}
        rows=[{**lesson("2026-10-20",8),"Reviews Done Approx":55}]
        now=datetime(2026,10,20,6,tzinfo=timezone.utc)
        with patch("notion_review_chart.send_svg",return_value="fake-uuid"):
            self.assertTrue(update_discipline_chart(fake_notion,"x","page",rows,
                                                     now,"Asia/Ho_Chi_Minh"))
        self.assertEqual(calls[-1][1],"/blocks/page/children")
        self.assertEqual(calls[-1][2]["after"],"old-chart")
        self.assertEqual(len(calls[-1][2]["children"]),3)
        self.assertIn("WK_DAILY_LESSONS_REVIEWS_CHART",
                      calls[-1][2]["children"][-1]["image"]["caption"][0]["text"]["content"])

    def test_checkpoint_pagination_until_earliest_boundary(self):
        pages=[]
        start={"properties":{"Captured At":{"date":{"start":"2026-10-21T17:02:00Z"}}}}
        old={"properties":{"Captured At":{"date":{"start":"2026-10-17T12:00:00Z"}}}}
        def fake(method,path,token,payload):
            pages.append(payload)
            if not payload.get("start_cursor"):
                return {"results":[start],"has_more":True,"next_cursor":"next"}
            return {"results":[old],"has_more":False}
        out=fetch_checkpoint_history(fake,"t","db",date(2026,10,18),"Asia/Ho_Chi_Minh")
        self.assertEqual(len(out),2)
        self.assertEqual(len(pages),2)


if __name__=="__main__":
    unittest.main()
