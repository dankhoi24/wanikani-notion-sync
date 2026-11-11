"""Regression: both Notion chart images must be real PNG payloads."""
from datetime import date, datetime, timezone
from io import BytesIO
from struct import unpack
from unittest import TestCase
from unittest.mock import patch

from notion_svg_chart import chart_svg, to_png, send_svg
from notion_review_chart import discipline_svg


class ChartPngTests(TestCase):
    def sample(self):
        return [
          {"Day":date(2026,10,18),"Lessons Today":19,
           "Recent Pace (7d)":19,"Daily Target":15,
           "Reviews Done Approx":None},
          {"Day":date(2026,10,19),"Lessons Today":0,
           "Recent Pace (7d)":9.5,"Daily Target":15,
           "Reviews Done Approx":90},
        ]

    def test_both_charts_rasterize_to_valid_high_resolution_png(self):
        now=datetime(2026,10,19,12,tzinfo=timezone.utc)
        for svg in (chart_svg(self.sample(),now,"Asia/Ho_Chi_Minh"),
                    discipline_svg(self.sample(),now,"Asia/Ho_Chi_Minh")):
            png=to_png(svg)
            self.assertTrue(png.startswith(bytes.fromhex("89504e470d0a1a0a")))
            width,height=unpack(">II",png[16:24])
            self.assertEqual(width,2320)
            self.assertGreaterEqual(height,1040)
            self.assertLess(len(png),5_000_000)

    def test_upload_uses_real_png_bytes_and_png_mime(self):
        now=datetime(2026,10,19,12,tzinfo=timezone.utc)
        svg=chart_svg(self.sample(),now,"Asia/Ho_Chi_Minh")
        calls=[]
        def notion(method,path,token,payload):
            calls.append((method,path,payload))
            return {"id":"12345678-aaaa-bbbb-cccc-123456789abc"}
        class FakeResp:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self):return b'{"status":"uploaded"}'
        seen=[]
        def fake_urlopen(req,timeout):
            seen.append(req)
            return FakeResp()
        with patch("notion_svg_chart.urlopen",fake_urlopen):
            upload=send_svg(notion,"test-token",svg,
                            filename="wanikani-daily-lessons-reviews.svg")
        self.assertEqual(upload,"12345678-aaaa-bbbb-cccc-123456789abc")
        self.assertEqual(calls[0][2]["filename"],
                         "wanikani-daily-lessons-reviews.png")
        self.assertEqual(calls[0][2]["content_type"],"image/png")
        self.assertIn(b"Content-Type: image/png",seen[0].data)
        self.assertIn(bytes.fromhex("89504e470d0a1a0a"),seen[0].data)
        self.assertNotIn(b"image/svg+xml",seen[0].data)


if __name__ == "__main__":
    import unittest
    unittest.main()
