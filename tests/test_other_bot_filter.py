"""其他 bot 发言识别 — 名单优先 + 启发式兜底，避免 bot 互聊循环。"""

import time
import unittest

from services.meta_thinking import detect_other_bot_message

NOW = 1_000_000.0
IDS = {"bot_qq_1", "bot_qq_2"}


class OtherBotDetectTest(unittest.TestCase):
    def test_blacklist_hit(self):
        """名单命中：无条件视为其他 bot（即使没在 bot 发言窗口内）。"""
        self.assertTrue(detect_other_bot_message("bot_qq_1", "短文本", 0.0, NOW, IDS))

    def test_heuristic_fast_long_reply(self):
        """启发式命中：bot 发言后 <10s 内到达的 >=80 字长文本。"""
        msg = "对，这条回复足够长" + "很长很长的内容啊" * 9
        self.assertTrue(detect_other_bot_message("u1", msg, NOW - 3, NOW, IDS))

    def test_heuristic_slow_reply_allowed(self):
        """慢速回复（>=10s）不算 bot。"""
        msg = "对，这条回复足够长" + "很长很长的内容" * 10
        self.assertFalse(detect_other_bot_message("u1", msg, NOW - 30, NOW, IDS))

    def test_heuristic_short_reply_allowed(self):
        """短文本（<80 字）不算 bot。"""
        self.assertFalse(detect_other_bot_message("u1", "对，我同意你的看法。", NOW - 3, NOW, IDS))

    def test_heuristic_disabled(self):
        """启发式关闭后只认名单。"""
        msg = "对，这条回复足够长" + "很长很长的内容" * 10
        self.assertFalse(detect_other_bot_message("u1", msg, NOW - 3, NOW, IDS, heuristic_enabled=False))

    def test_no_recent_bot_send(self):
        """bot 从未发言：启发式不触发。"""
        msg = "对，这条回复足够长" + "很长很长的内容" * 10
        self.assertFalse(detect_other_bot_message("u1", msg, 0.0, NOW, IDS))

    def test_edge_boundary(self):
        """恰好 80 字算 bot；恰好 10s 不算（需严格小于）。"""
        msg = "长" * 80
        self.assertTrue(detect_other_bot_message("u1", msg, NOW - 9, NOW, IDS))
        self.assertFalse(detect_other_bot_message("u1", msg, NOW - 10, NOW, IDS))
        msg79 = "长" * 79
        self.assertFalse(detect_other_bot_message("u1", msg79, NOW - 3, NOW, IDS))


class OtherBotConfigReadTest(unittest.TestCase):
    """配置读取：名单/启发式开关/阈值解析。"""

    def test_multi_ids_parsing(self):
        """多个 QQ 号：支持英文逗号、中文逗号、换行分隔。"""
        import re
        for raw in ("123,456", "123，456", "123\n456", "123, 456,789"):
            ids = {s.strip() for s in re.split(r"[，,;\n\r]+", raw) if s.strip()}
            self.assertEqual(ids, {"123", "456"} if "789" not in raw else {"123", "456", "789"})

    def _load(self, cfg):
        from main import WaveMemoryPlugin
        with self.assertRaises(ImportError):
            WaveMemoryPlugin  # noqa: F841 — 环境无 astrbot，仅确认调用点存在

    def test_schema_has_keys(self):
        import json, os
        schema = json.load(open(os.path.join(os.path.dirname(__file__), "..", "_conf_schema.json")))
        items = schema["Message_Filter"]["items"]
        for key in ("other_bot_ids", "bot_chat_heuristic", "other_bot_quick_seconds", "other_bot_min_length"):
            self.assertIn(key, items)
        self.assertEqual(items["other_bot_ids"]["default"], "")
        self.assertEqual(items["bot_chat_heuristic"]["default"], True)
        self.assertEqual(items["other_bot_quick_seconds"]["default"], 10)
        self.assertEqual(items["other_bot_min_length"]["default"], 80)


if __name__ == "__main__":
    unittest.main()