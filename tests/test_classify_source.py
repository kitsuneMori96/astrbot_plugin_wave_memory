"""classify_source v2 单元测试——用例全部来自真实语料实测样本。

设计依据：2026-08 全库 13k 条记忆特征分析，
白名单用精确锚点（身份类别后缀 + 偏好宾语排除「你」）。
"""

from __future__ import annotations

import logging
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "astrbot.api" not in sys.modules:
    astrbot_module = types.ModuleType("astrbot")
    api_module = types.ModuleType("astrbot.api")
    api_module.logger = logging.getLogger("astrbot-test")
    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules["astrbot.api"] = api_module

from astrbot_plugin_wave_memory.services.message_writer import (
    classify_source,
    has_info_signal,
    is_noise_pattern,
)


def cls(msg: str) -> str:
    return classify_source(msg, sender_id="123", bot_keywords=set(), is_at_bot=False)


class InfoSignalWhitelistTest(unittest.TestCase):
    """A. 真实语料中的正例：短句但携带画像信息 → chat。"""

    def test_real_samples_from_db(self):
        # 这些都是库里被旧规则误判为 noise 的真实消息
        self.assertEqual(cls("我是福建人"), "chat")
        self.assertEqual(cls("我超喜欢吃饺子"), "chat")
        self.assertEqual(cls("我也喜欢INFP"), "chat")

    def test_identity_category_suffix(self):
        self.assertEqual(cls("我是猫控"), "chat")
        self.assertEqual(cls("我是原神粉"), "chat")

    def test_preference_with_modifier(self):
        self.assertEqual(cls("我特别讨厌下雨"), "chat")
        self.assertEqual(cls("我不太喜欢社交"), "chat")

    def test_fact_patterns(self):
        self.assertEqual(cls("我在杭州上班"), "chat")
        self.assertEqual(cls("我的专业是计算机"), "chat")
        self.assertEqual(cls("我生日3月5号"), "chat")
        self.assertEqual(cls("今年18岁"), "chat")


class InfoSignalNegativeTest(unittest.TestCase):
    """A-反例：真实语料中的碎片/会话句，必须保持 noise。"""

    def test_conversational_like_is_not_profile(self):
        # 「喜欢你」的宾语是你 → 会话表白不是画像
        self.assertEqual(cls("我喜欢你"), "noise")
        self.assertFalse(has_info_signal("我也喜欢你呀"))

    def test_fragments_stay_noise(self):
        # 库中实测碎片：不完整、无事实价值
        self.assertEqual(cls("还好我不是给"), "noise")
        self.assertEqual(cls("饱饱我是"), "noise")
        self.assertEqual(cls("我不记得我不还了"), "noise")
        self.assertEqual(cls("就剩我是肉腿了"), "noise")
        self.assertEqual(cls("我不是dsh"), "noise")

    def test_slang_fragment_without_category_suffix(self):
        # 「我是正常xp」：无类别后缀，俚语碎片
        self.assertFalse(has_info_signal("我是正常xp"))


class NoisePatternBlacklistTest(unittest.TestCase):
    """B. 噪声模式：即使够长也降为 noise。"""

    def test_pure_laughter_long(self):
        self.assertEqual(cls("哈哈哈哈哈哈哈哈哈哈哈哈"), "noise")
        self.assertTrue(is_noise_pattern("嘿嘿嘿嘿嘿"))

    def test_single_char_spam(self):
        # 实测阈值 ≥5 即可覆盖（chat 中此类仅 4 条）
        self.assertEqual(cls("啊啊啊啊啊啊"), "noise")
        self.assertTrue(is_noise_pattern("草草草草草"))

    def test_pure_punctuation(self):
        self.assertTrue(is_noise_pattern("！！！？？？。。。"))
        self.assertEqual(cls("！！！！！！！！！！！！！"), "noise")

    def test_mixed_laughter_with_content_is_chat(self):
        self.assertFalse(is_noise_pattern("哈哈哈这个太好笑了真的"))
        self.assertEqual(cls("哈哈哈这个太好笑了真的"), "chat")


class RegressionTest(unittest.TestCase):
    """原有行为不回归。"""

    def test_short_no_signal_still_noise(self):
        self.assertEqual(cls("哦"), "noise")
        self.assertEqual(cls("好的"), "noise")

    def test_long_message_chat(self):
        self.assertEqual(cls("今天天气不错我们出去玩吧哈哈"), "chat")

    def test_at_bot_is_core(self):
        r = classify_source("哈哈哈", sender_id="1", bot_keywords=set(), is_at_bot=True)
        self.assertEqual(r, "core")

    def test_bot_sender_is_core(self):
        r = classify_source("任意内容", sender_id="bot", bot_keywords=set(), is_at_bot=False)
        self.assertEqual(r, "core")

    def test_empty_string_noise(self):
        self.assertEqual(cls(""), "noise")
        self.assertEqual(cls("   "), "noise")


if __name__ == "__main__":
    unittest.main()
