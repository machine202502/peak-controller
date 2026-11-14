"""Тесты парсеров на зафиксированных копиях реальных страниц (tests/fixtures).

Запуск:  py tests/test_parsers.py   (или py -m unittest tests.test_parsers -v)
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core import schedule  # noqa: E402
from sources import deepseek, glm  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
UTC = timezone.utc


def read(name: str) -> str | None:
    p = FIXTURES / name
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


class TestDeepSeekParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = read("deepseek_pricing_en.md")
        if cls.md is None:
            raise unittest.SkipTest("нет fixture deepseek_pricing_en.md")
        cls.data = deepseek.parse_pricing(cls.md)

    def test_models(self):
        self.assertEqual([m["alias"] for m in self.data["models"]], ["deepseek-flash", "deepseek-v4-pro"])
        self.assertEqual(self.data["models"][0]["version"], "DeepSeek-V4.1-Flash-1017")
        self.assertEqual(self.data["models"][1]["version"], "DeepSeek-V4-Pro-0813")

    def test_prices(self):
        flash = self.data["models"][0]["prices"]
        pro = self.data["models"][1]["prices"]
        self.assertEqual(flash["input_cache_hit"], {"offpeak": 0.003, "peak": 0.006})
        self.assertEqual(flash["input"], {"offpeak": 0.15, "peak": 0.3})
        self.assertEqual(flash["output"], {"offpeak": 0.6, "peak": 1.2})
        self.assertEqual(pro["input_cache_hit"], {"offpeak": 0.022, "peak": 0.044})
        self.assertEqual(pro["input"], {"offpeak": 0.66, "peak": 1.32})
        self.assertEqual(pro["output"], {"offpeak": 1.98, "peak": 3.96})

    def test_peak_windows(self):
        self.assertEqual(self.data["peak_windows_utc"], [[60, 240], [360, 600]])
        self.assertTrue(self.data["weekend_offpeak"])
        self.assertTrue(self.data["cn_holidays_offpeak"])

    def test_sanity_offpeak_is_half(self):
        for m in self.data["models"]:
            for pair in m["prices"].values():
                self.assertAlmostEqual(pair["offpeak"] * 2, pair["peak"])
        self.assertEqual(self.data["warnings"], [])


class TestGlmParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = read("glm_overview.md")
        if cls.md is None:
            raise unittest.SkipTest("нет fixture glm_overview.md")
        cls.data = glm.parse_overview(cls.md)

    def test_tiers(self):
        self.assertEqual(self.data["tiers"], {
            "lite": {"h5": 2000, "week": 10000},
            "pro": {"h5": 12000, "week": 60000},
            "max": {"h5": 28000, "week": 140000},
        })

    def test_models(self):
        by_name = {m["name"]: m for m in self.data["models"]}
        self.assertEqual(by_name["GLM-5.3"], {"name": "GLM-5.3", "in": 6.9, "cached": 1.7, "out": 24.0})
        self.assertEqual(by_name["GLM-5.3-Flash"], {"name": "GLM-5.3-Flash", "in": 2.3, "cached": 0.56, "out": 8.0})

    def test_peak_window_and_discount(self):
        self.assertEqual(self.data["peak_windows_sgt"], [[840, 1080]])  # 14:00–18:00 UTC+8
        self.assertEqual(self.data["tz"], "Asia/Singapore")
        self.assertEqual(self.data["offpeak_pct"], 50)

    def test_token_estimates(self):
        self.assertEqual(self.data["token_estimates"],
                         {"lite": "48–97", "pro": "290–580", "max": "676–1,352"})


class TestNoticeCrossCheck(unittest.TestCase):
    def test_notice_if_present(self):
        md = read("glm_usage_revision.md")
        if md is None:
            self.skipTest("нет fixture glm_usage_revision.md (не скачалась)")
        self.assertIn("UTC+8", md)
        self.assertIn("Peak hours", md)


class TestSchedule(unittest.TestCase):
    DS_WINDOWS = [[60, 240], [360, 600]]       # 01:00–04:00, 06:00–10:00 UTC
    GLM_WINDOWS = [[840, 1080]]                # 14:00–18:00 UTC+8

    def test_weekday_inside_window_is_peak(self):
        # среда 2026-11-11, 07:00 UTC — внутри второго окна DeepSeek
        now = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
        ps = schedule.peak_state(self.DS_WINDOWS, "UTC", [0, 1, 2, 3, 4], {}, now)
        self.assertTrue(ps["peak"])
        self.assertIsNone(ps["reason"])
        # конец окна — 10:00 UTC того же дня
        self.assertEqual(ps["next_change"], datetime(2026, 11, 11, 10, 0, tzinfo=UTC))

    def test_weekday_between_windows_is_offpeak(self):
        # среда 2026-11-11, 05:00 UTC — между окнами
        now = datetime(2026, 11, 11, 5, 0, tzinfo=UTC)
        ps = schedule.peak_state(self.DS_WINDOWS, "UTC", [0, 1, 2, 3, 4], {}, now)
        self.assertFalse(ps["peak"])
        self.assertEqual(ps["reason"], "offpeak_hours")
        self.assertEqual(ps["next_change"], datetime(2026, 11, 11, 6, 0, tzinfo=UTC))

    def test_weekend_is_offpeak(self):
        # суббота 2026-11-14, 07:00 UTC
        now = datetime(2026, 11, 14, 7, 0, tzinfo=UTC)
        ps = schedule.peak_state(self.DS_WINDOWS, "UTC", [0, 1, 2, 3, 4], {}, now)
        self.assertFalse(ps["peak"])
        self.assertEqual(ps["reason"], "weekend")
        self.assertEqual(ps["next_change"], datetime(2026, 11, 16, 1, 0, tzinfo=UTC))  # понедельник 01:00 UTC

    def test_chinese_holiday_is_offpeak(self):
        # пятница 2027-01-01 — китайский праздник, всё число не-пик
        now = datetime(2027, 1, 1, 7, 0, tzinfo=UTC)
        ps = schedule.peak_state(self.DS_WINDOWS, "UTC", [0, 1, 2, 3, 4],
                                 {"2027-01-01": "Новый год"}, now)
        self.assertFalse(ps["peak"])
        self.assertEqual(ps["reason"], "cn_holiday")

    def test_glm_peak_in_singapore_time(self):
        # среда 06:59 UTC = 14:59 SGT → пик; 06:00 UTC = 14:00 SGT → пик (граница включительно)
        now = datetime(2026, 11, 11, 6, 59, tzinfo=UTC)
        ps = schedule.peak_state(self.GLM_WINDOWS, "Asia/Singapore", [0, 1, 2, 3, 4], None, now)
        self.assertTrue(ps["peak"])
        self.assertEqual(ps["next_change"], datetime(2026, 11, 11, 10, 0, tzinfo=UTC))  # 18:00 SGT
        # 05:59 UTC = 13:59 SGT → ещё не пик
        now2 = datetime(2026, 11, 11, 5, 59, tzinfo=UTC)
        ps2 = schedule.peak_state(self.GLM_WINDOWS, "Asia/Singapore", [0, 1, 2, 3, 4], None, now2)
        self.assertFalse(ps2["peak"])
        self.assertEqual(ps2["reason"], "offpeak_hours")

    def test_windows_local_format(self):
        now = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
        ps = schedule.peak_state(self.GLM_WINDOWS, "Asia/Singapore", [0, 1, 2, 3, 4], None, now)
        self.assertEqual(len(ps["windows_local"]), 1)
        self.assertRegex(ps["windows_local"][0], r"^\d{2}:\d{2}–\d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
