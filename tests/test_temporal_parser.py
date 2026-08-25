"""temporal_parser 时间词解析单测。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.temporal_parser import parse_time_range


def ts(y, m, d):
    return datetime(y, m, d).timestamp()


class TemporalParserTest(unittest.TestCase):

    def setUp(self):
        # 固定「2026-08-25 21:25」——原始 bug 场景：晚上问昨天
        self.now = datetime(2026, 8, 25, 21, 25, 44)

    def test_yesterday_is_calendar_day_not_rolling_24h(self):
        """晚上 9 点问「昨天」必须覆盖昨天全天，而非 now-24h。"""
        start, end = parse_time_range("能帮我写一下昨天的日记吗", self.now)
        self.assertEqual(start, ts(2026, 8, 24))
        self.assertEqual(end, ts(2026, 8, 25))

    def test_yesterday_late_night_covers_early_morning(self):
        """凌晨 0:30 问「昨天」= 前天全天（日历语义）。"""
        now = datetime(2026, 8, 25, 0, 30)
        start, end = parse_time_range("昨天聊了啥", now)
        self.assertEqual(start, ts(2026, 8, 24))
        self.assertEqual(end, ts(2026, 8, 25))

    def test_day_before_yesterday(self):
        start, end = parse_time_range("前天那个事好好笑", self.now)
        self.assertEqual(start, ts(2026, 8, 23))
        self.assertEqual(end, ts(2026, 8, 24))

    def test_three_days_ago(self):
        start, end = parse_time_range("3天前发生了什么", self.now)
        self.assertEqual(start, ts(2026, 8, 22))
        self.assertEqual(end, ts(2026, 8, 23))

    def test_last_week_open_upper_bound(self):
        start, end = parse_time_range("上周我们玩什么了", self.now)
        self.assertEqual(start, ts(2026, 8, 18))
        self.assertEqual(end, float("inf"))

    def test_vague_past_30d(self):
        start, end = parse_time_range("之前说过的那个梗", self.now)
        self.assertEqual(start, ts(2026, 7, 26))
        self.assertEqual(end, float("inf"))

    def test_hours_ago_rolling(self):
        start, _ = parse_time_range("2小时前你说了啥", self.now)
        expected = self.now.timestamp() - 2 * 3600
        self.assertAlmostEqual(start, expected, delta=1)

    def test_no_time_word_returns_none(self):
        self.assertIsNone(parse_time_range("你好呀", self.now))
        self.assertIsNone(parse_time_range("", self.now))

    def test_priority_da_qian_tian_over_qian_tian(self):
        start, end = parse_time_range("大前天的事", self.now)
        self.assertEqual(start, ts(2026, 8, 22))
        self.assertEqual(end, ts(2026, 8, 23))


if __name__ == "__main__":
    unittest.main()
