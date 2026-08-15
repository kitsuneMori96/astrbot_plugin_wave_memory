"""回话后窗口内粗筛 — 是否值得交给 LLM 自判主动回答。"""

import unittest

from services.meta_thinking import window_analysis_candidate

NOW = 1_000_000.0


class WindowAnalyzeCandidateTest(unittest.TestCase):
    def _vals(self, **kw):
        base = {
            "message": "",
            "topic_overlap": 0.0,
            "identity_hit": False,
            "reply_ts": 0.0,
            "now": NOW,
            "aba_window": 30.0,
            "overlap_threshold": 0.12,
            "per_min": 3,
        }
        base.update(kw)
        return base

    def test_ignore_plain_chatter_has_no_bot_context(self):
        """无任何接话线索的旁人对聊 → 不候选。（问句/呢结尾属 R1 宽筛，会用别的用例验证）"""
        r = window_analysis_candidate(**self._vals(message="今天晚饭吃了火锅"))
        self.assertFalse(r)
        r = window_analysis_candidate(**self._vals(message="明天陪我去超市"))
        self.assertFalse(r)

    def test_complaint_ignoring_me_is_candidate(self):
        r = window_analysis_candidate(**self._vals(message="不理我"))
        self.assertTrue(r)
        r = window_analysis_candidate(**self._vals(message="别装死 出来一下"))
        self.assertTrue(r)

    def test_imperative_to_recent_partner_is_candidate(self):
        """R5：发送者是 bot 刚互动对象 + 我向祈使。"""
        r = window_analysis_candidate(**self._vals(
            message="去搜索kitsuneMori", reply_ts=NOW - 10))
        self.assertTrue(r)

    def test_imperative_to_stale_partner_is_not_candidate(self):
        """reply_ts 超过 ABA 窗口 → R5 不触发。"""
        r = window_analysis_candidate(**self._vals(
            message="去搜索kitsuneMori", reply_ts=NOW - 100))
        self.assertFalse(r)

    def test_imperative_with_no_reply_history_is_not_candidate(self):
        r = window_analysis_candidate(**self._vals(message="去搜索kitsuneMori"))
        self.assertFalse(r)

    def test_question_is_candidate(self):
        r = window_analysis_candidate(**self._vals(message="你刚才说啥了？"))
        self.assertTrue(r)
        r = window_analysis_candidate(**self._vals(message="为什么这么想呢"))
        self.assertTrue(r)

    def test_identity_hit_always_candidate(self):
        r = window_analysis_candidate(**self._vals(message="茉莉说说看", identity_hit=True))
        self.assertTrue(r)

    def test_topic_overlap_is_candidate(self):
        r = window_analysis_candidate(**self._vals(message="武侠小说", topic_overlap=0.5))
        self.assertTrue(r)

    def test_command_prefix_excluded(self):
        for p in ("/teach 规则是X", "记住这件事很重要", "忘记某条记录"):
            r = window_analysis_candidate(**self._vals(message=p))
            self.assertFalse(r)

    def test_too_short_excluded(self):
        r = window_analysis_candidate(**self._vals(message="好"))
        self.assertFalse(r)

    def test_per_min_rate_cap(self):
        state = {"minute": int(NOW // 60), "count": 0}
        first = window_analysis_candidate(**self._vals(
            message="不理我", per_min=1, count_state=state))
        self.assertTrue(first)
        second = window_analysis_candidate(**self._vals(
            message="人呢", per_min=1, count_state=state))
        self.assertFalse(second)

    def test_count_state_resets_on_new_minute(self):
        state = {"minute": 0, "count": 0}
        r = window_analysis_candidate(**self._vals(
            message="不理我", per_min=1, count_state=state))
        self.assertTrue(r)


if __name__ == "__main__":
    unittest.main()