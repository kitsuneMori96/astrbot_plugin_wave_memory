import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path


class FakeTextPart:
    def __init__(self, text):
        self.text = text


class FakeReq:
    def __init__(self):
        self.extra_user_content_parts = []


class HitChannel:
    name = "memory"

    async def build(self, ctx):
        from services.injection.channel_base import InjectionResult

        return InjectionResult.hit("memory", "主动注入文本", items=[{"id": 42, "preview": "主动注入文本"}])


class ErrorChannel:
    name = "facts"

    async def build(self, ctx):
        raise RuntimeError("facts boom")


class InjectionActiveTest(unittest.TestCase):
    def _trace_store(self):
        from services.injection.trace_store import InjectionTraceStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "trace.db")
        self.addCleanup(conn.close)
        store = InjectionTraceStore(conn)
        store.ensure_schema()
        return store

    def test_active_runner_mutates_real_request_and_records_trace(self):
        from services.config.channel_config import build_default_channel_config
        from services.injection.active import run_injection_active
        from services.injection.context import InjectionContext

        trace_store = self._trace_store()
        req = FakeReq()
        ctx = InjectionContext(
            event="event",
            req=req,
            message="主动模式测试",
            group_id="g1",
            sender_id="u1",
            sender_name="用户",
            bot_id="bot",
            bot_profile_id="bot",
            mode="full",
            trace_id="trace-active-1",
        )

        result = asyncio.run(run_injection_active(
            ctx=ctx,
            channels=[HitChannel()],
            config=build_default_channel_config(runtime_mode="full"),
            trace_store=trace_store,
            text_part_factory=FakeTextPart,
        ))
        detail = trace_store.get("trace-active-1")

        self.assertTrue(result.injected)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertEqual(req.extra_user_content_parts[0].text, "主动注入文本")
        self.assertEqual(detail["status"], "ok")
        self.assertEqual(detail["final_preview"], "主动注入文本")
        self.assertEqual(detail["channels"][0]["channel"], "memory")
        self.assertEqual(detail["channels"][0]["item_count"], 1)

    def test_active_runner_keeps_successful_channels_when_one_channel_errors(self):
        from services.config.channel_config import apply_channel_overrides, build_default_channel_config
        from services.injection.active import run_injection_active
        from services.injection.context import InjectionContext

        trace_store = self._trace_store()
        req = FakeReq()
        config = apply_channel_overrides(
            build_default_channel_config(runtime_mode="full"),
            {"channels": {"facts": {"priority": 200}, "memory": {"priority": 100}}},
        )
        ctx = InjectionContext(
            event="event",
            req=req,
            message="主动模式错误通道测试",
            group_id="g1",
            sender_id="u1",
            sender_name="用户",
            bot_id="bot",
            bot_profile_id="bot",
            mode="full",
            trace_id="trace-active-2",
        )

        result = asyncio.run(run_injection_active(
            ctx=ctx,
            channels=[ErrorChannel(), HitChannel()],
            config=config,
            trace_store=trace_store,
            text_part_factory=FakeTextPart,
        ))
        detail = trace_store.get("trace-active-2")
        statuses = {row["channel"]: row["status"] for row in detail["channels"]}

        self.assertTrue(result.injected)
        self.assertEqual(req.extra_user_content_parts[0].text, "主动注入文本")
        self.assertEqual(statuses["facts"], "error")
        self.assertEqual(statuses["memory"], "hit")


if __name__ == "__main__":
    unittest.main()
