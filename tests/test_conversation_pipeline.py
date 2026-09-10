"""conversation_pipeline 单元测试：输出解析 / 风格构建 / 场景注册表 / Planner。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.conversation_pipeline import (
    ConversationPlanner,
    Scenario,
    build_style_directive,
    normalize_detail,
    normalize_tone,
    parse_custom_scenarios,
    parse_plan_response,
)


class ParsePlanResponseTest(unittest.TestCase):

    def test_full_parse_5fields(self):
        """5 行输出格式：行动/把握/语气/详略/念头。"""
        text = (
            "行动：回复\n"
            "把握：高\n"
            "语气：热情\n"
            "详略：详细\n"
            "念头：得赶紧帮他搞明白"
        )
        r = parse_plan_response(text)
        self.assertTrue(r["reply"])
        self.assertEqual(r["tone"], "热情")
        self.assertEqual(r["detail"], "详细")
        self.assertEqual(r["inner_thought"], "得赶紧帮他搞明白")
        self.assertEqual(r["confidence"], "高")

    def test_negation_priority(self):
        """P0-1 修复：「不回复」「无需回复」被判为沉默。"""
        for action in ("不回复", "无需回复", "不回应", "沉默", "跳过", "不答复", "不回"):
            r = parse_plan_response(f"行动：{action}")
            self.assertFalse(r["reply"], f"action='{action}' should be False")

    def test_positive_reply(self):
        """肯定式回复正常识别。"""
        for action in ("回复", "回应"):
            r = parse_plan_response(f"行动：{action}")
            self.assertTrue(r["reply"], f"action='{action}' should be True")

    def test_markdown_normalization(self):
        """P10 修复：markdown 符号归一化。"""
        r = parse_plan_response("**行动**：回复\n**把握**：中")
        self.assertTrue(r["reply"])
        self.assertEqual(r["confidence"], "中")

    def test_halfwidth_colon(self):
        """P10 修复：半角冒号。"""
        r = parse_plan_response("行动:回复\n把握:低")
        self.assertTrue(r["reply"])
        self.assertEqual(r["confidence"], "低")

    def test_empty_and_garbage(self):
        self.assertFalse(parse_plan_response("")["reply"])
        self.assertFalse(parse_plan_response("乱七八糟")["reply"])
        # 无行动字段 → default_reply
        self.assertFalse(parse_plan_response("语气：冷淡")["reply"])

    def test_default_reply_parameter(self):
        """default_reply 参数：forced 传 True。"""
        r = parse_plan_response("语气：热情", default_reply=True)
        self.assertTrue(r["reply"])

    def test_confidence_field(self):
        r = parse_plan_response("行动：沉默\n把握：低")
        self.assertEqual(r["confidence"], "低")

    def test_confidence_unknown_fallback(self):
        r = parse_plan_response("行动：回复\n把握：也许")
        self.assertEqual(r["confidence"], "中")

    def test_thought_field(self):
        r = parse_plan_response("行动：回复\n念头：想逗他一下")
        self.assertEqual(r["inner_thought"], "想逗他一下")

    def test_missing_thought_recorded(self):
        """念头缺失时 _miss 记录。"""
        r = parse_plan_response("行动：回复\n语气：热情")
        self.assertIn("inner_thought", r["_miss"])

    def test_unknown_tone_fallback(self):
        r = parse_plan_response("行动：回复\n语气：温柔")
        self.assertEqual(r["tone"], "热情")

    def test_unknown_detail_fallback(self):
        r = parse_plan_response("行动：回复\n详略：一般")
        self.assertEqual(r["detail"], "简洁")

    def test_forced_fixed_action(self):
        """forced 模板固定行动：回复。"""
        r = parse_plan_response("行动：回复\n把握：高\n语气：热情\n详略：简洁\n念头：好")
        self.assertTrue(r["reply"])


class StyleDirectiveTest(unittest.TestCase):

    def test_fallback_without_service(self):
        out = build_style_directive(None, tone="热情", detail="详细", inner_thought="想帮忙")
        self.assertIn("[回复格式硬性要求]", out)
        self.assertIn("热情", out)
        self.assertIn("详细", out)
        self.assertIn("想帮忙", out)
        self.assertIn("开口前你的念头", out)

    def test_fallback_brief_rule(self):
        out = build_style_directive(None, tone="正常", detail="简洁")
        self.assertIn("禁止展开解释", out)
        self.assertIn("40 字", out)

    def test_with_service_template(self):
        class FakePS:
            def render(self, key, default="", **kw):
                return f"T[{key}]" + "|".join(f"{k}={v}" for k, v in kw.items())

        out = build_style_directive(FakePS(), tone="克制", detail="简洁")
        self.assertIn("style_directive", out)
        self.assertIn("克制", out)

    def test_cue_omitted_when_empty(self):
        out = build_style_directive(None, tone="正常", detail="简洁", inner_thought="")
        self.assertNotIn("念头", out)
        self.assertNotIn("opening_cue", out)

    def test_cue_independent_block(self):
        """念头放在 <opening_cue> 独立标签中。"""
        out = build_style_directive(None, tone="热情", detail="简洁", inner_thought="想帮他")
        self.assertIn("<opening_cue>", out)
        self.assertIn("开口前你的念头：想帮他", out)
        self.assertIn("</opening_cue>", out)


class ScenarioTest(unittest.TestCase):

    def test_quota_hourly(self):
        sc = Scenario(name="t", matcher=lambda m: True, max_per_hour=2)
        self.assertTrue(sc.quota_ok("g"))
        sc.record("g")
        sc.record("g")
        self.assertFalse(sc.quota_ok("g"))

    def test_quota_interval(self):
        sc = Scenario(name="t", matcher=lambda m: True, interval_seconds=600)
        sc.record("g")
        self.assertFalse(sc.quota_ok("g"))

    def test_disabled_and_matcher_error(self):
        sc = Scenario(name="t", matcher=lambda m: True, enabled=False)
        self.assertFalse(sc.matches("x"))
        bad = Scenario(name="t", matcher=lambda m: 1 / 0)
        self.assertFalse(bad.matches("x"))

    def test_hit_combines_match_and_quota(self):
        sc = Scenario(name="t", matcher=lambda m: "关键词" in m, max_per_hour=1)
        hit = sc.hit("有关键词的消息", "g")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.name, "t")
        sc.record("g")
        self.assertIsNone(sc.hit("再命中一次", "g"))
        self.assertIsNone(sc.hit("不相关", "g"))


class CustomScenarioParseTest(unittest.TestCase):

    def test_parse_valid_lines(self):
        text = (
            "游戏讨论|原神,星穹铁道|聊游戏可以热情|5\n"
            "\n"
            "美食|好吃的|简短回应\n"
        )
        scenarios, errors = parse_custom_scenarios(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0].name, "游戏讨论")
        self.assertTrue(scenarios[0].matches("今天原神更新了"))
        self.assertFalse(scenarios[0].matches("无关消息"))
        self.assertEqual(scenarios[0].max_per_hour, 5)
        self.assertIn("游戏讨论", scenarios[0].hint)
        self.assertEqual(scenarios[1].max_per_hour, 3)

    def test_bad_lines_reported_and_skipped(self):
        scenarios, errors = parse_custom_scenarios("只有一段\n名称||\n好的|词|提示")
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(len(errors), 2)

    def test_case_insensitive_keywords(self):
        scs, _ = parse_custom_scenarios("代码|Python|提示")
        self.assertTrue(scs[0].matches("学 python 中"))


class _FakeLLMResponse:
    def __init__(self, text: str):
        self.completion_text = text


class _FakeLLM:
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    async def text_chat(self, *, prompt, system_prompt=None, contexts=None, **kw):
        self.prompts.append(prompt)
        return _FakeLLMResponse(self.text)


class _FakePS:
    """最小 PromptService 替身。"""

    def __init__(self):
        self.persona = "测试人格"

    def resolve_persona(self, bot_id="", group_id="", bot_name=""):
        return {"id": None, "name": "", "system_prompt": self.persona}

    def render_identity_guard(self, bot_name):
        return f"<guard>{bot_name}</guard>"

    def get_template(self, key, default=""):
        return default or "沉默"

    def render(self, key, default="", **kw):
        return f"[{key}]" + "|".join(f"{k}={v}" for k, v in kw.items())


class ConversationPlannerTest(unittest.IsolatedAsyncioTestCase):

    async def test_gate_yes(self):
        llm = _FakeLLM(
            "行动：回复\n把握：高\n语气：克制\n详略：详细\n念头：他在问我问题"
        )
        planner = ConversationPlanner(llm, _FakePS())
        r = await planner.plan_gate(context_messages=["a: 你好"], message="怎么部署",
                                    bot_name="茉莉")
        self.assertTrue(r["reply"])
        self.assertEqual(r["tone"], "克制")
        self.assertEqual(r["detail"], "详细")
        self.assertEqual(r["inner_thought"], "他在问我问题")
        prompt = llm.prompts[0]
        self.assertIn("测试人格", prompt)
        self.assertIn("<guard>茉莉</guard>", prompt)
        self.assertIn("怎么部署", prompt)

    async def test_gate_no(self):
        llm = _FakeLLM("行动：沉默\n把握：中\n语气：热情\n详略：简洁\n念头：与我无关")
        planner = ConversationPlanner(llm, _FakePS())
        r = await planner.plan_gate(context_messages=[], message="旁人对聊")
        self.assertFalse(r["reply"])

    async def test_forced_always_replies(self):
        llm = _FakeLLM("行动：回复\n把握：高\n语气：热情\n详略：简洁\n念头：主人叫我")
        planner = ConversationPlanner(llm, _FakePS())
        r = await planner.plan_forced(context_messages=[], message="@茉莉 在吗")
        self.assertTrue(r["reply"])
        self.assertEqual(r["tone"], "热情")

    async def test_llm_failure_defaults(self):
        class BoomLLM:
            async def text_chat(self, **kw):
                raise RuntimeError("boom")

        planner = ConversationPlanner(BoomLLM(), _FakePS())
        r = await planner.plan_gate(context_messages=[], message="x")
        self.assertFalse(r["reply"])
        r2 = await planner.plan_forced(context_messages=[], message="x")
        self.assertTrue(r2["reply"])
        self.assertEqual(r2["tone"], "热情")

    async def test_scenario_hint_injected(self):
        llm = _FakeLLM("行动：回复\n把握：中\n语气：热情\n详略：简洁\n念头：好")
        planner = ConversationPlanner(llm, _FakePS())
        await planner.plan_gate(context_messages=[], message="求助",
                                scenario_hint="（触发场景：求助答疑）")
        self.assertIn("触发场景：求助答疑", llm.prompts[0])


if __name__ == "__main__":
    unittest.main()
