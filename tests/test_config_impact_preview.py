import asyncio
import copy
import unittest
from types import SimpleNamespace


class _PreviewQueryEngine:
    def __init__(self):
        self.calls = []
        self.shared_stage = SimpleNamespace(top_k=99)
        self.touch_count = 0

    async def query(self, text, *, top_k, scope, options, collector=None, **kwargs):
        self.calls.append({"text": text, "top_k": top_k, "scope": scope, "options": options})
        if options.touch:
            self.touch_count += 1
        memories = [
            {
                "id": index,
                "content": f"memory-{index}",
                "source": "core",
                "similarity": 0.95 - index * 0.05,
                "score": 1.0 - index * 0.1,
                "timestamp": 0,
            }
            for index in range(1, top_k + 1)
        ]
        if collector is not None:
            collector.record("final", {"ids": [item["id"] for item in memories], "result_count": len(memories)})
        return memories

    async def shotgun_query(self, *args, **kwargs):
        raise AssertionError("dry-run preview must not use shotgun_query without QueryOptions.touch")

    @staticmethod
    def format_injection(memories, current_group_id=""):
        return "\n".join(str(item["id"]) for item in memories)


class ConfigImpactPreviewTest(unittest.TestCase):
    @staticmethod
    def _scope(*, conversation="g1", subject="test:user:u1"):
        from domain.scope import RuntimeScope, SessionRef

        return RuntimeScope(
            bot_id="bot-alpha",
            visibility="group",
            session=SessionRef(
                id=f"test:group:{conversation}",
                platform_id="test",
                kind="group",
                conversation_id=conversation,
            ),
            subject_principal_id=subject,
        )

    def _container(self):
        from services.injection.channels.memory_recall import MemoryRecallChannel

        engine = _PreviewQueryEngine()
        container = SimpleNamespace(
            plugin_config={
                "Runtime_Settings": {"runtime_mode": "full"},
                "Query_Settings": {"inject_top_k": 1, "enable_shotgun": True},
                "Channel_Settings": {},
            },
            query_engine=engine,
            injection_channels=[MemoryRecallChannel(query_engine=engine)],
            injection_channel_config=None,
            injection_channel_config_setter=None,
        )
        return container, engine

    def test_real_current_candidate_preview_is_same_scope_bounded_and_side_effect_free(self):
        from webui.blueprints.channel_config import preview_channel_config_impact

        container, engine = self._container()
        before_config = copy.deepcopy(container.plugin_config)
        scope = self._scope()
        body = {
            "layer": "session",
            "scope": scope.to_dict(),
            "message": "remember",
            "sender_id": "u1",
            "channels": {"memory": {"top_k": 3, "min_score": 0.0}},
            "query_options": {
                "stages": {"epa": False},
                "params": {"pyramid_top_k": 2},
            },
            "max_items": 2,
        }

        result = asyncio.run(preview_channel_config_impact(container, body, request_scope=scope))

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["scope"], scope.to_dict())
        self.assertEqual(result["current"]["result"]["ranking"], ["memory:id:1"])
        self.assertEqual(result["candidate"]["result"]["ranking"], ["memory:id:1", "memory:id:2"])
        self.assertEqual(result["diff"]["hits"]["added"], ["memory:id:2"])
        self.assertEqual(result["limits"]["max_items"], 2)
        self.assertNotEqual(result["current"]["revision"], result["candidate"]["revision"])
        self.assertEqual(result["candidate"]["provenance"]["channels.memory.top_k"]["layer"], "session")

        self.assertEqual(len(engine.calls), 2)
        self.assertTrue(all(call["scope"] == scope for call in engine.calls))
        self.assertTrue(all(call["options"].touch is False for call in engine.calls))
        self.assertEqual(engine.calls[1]["options"].stages["epa"], False)
        self.assertEqual(engine.calls[1]["options"].params["pyramid_top_k"], 2)
        self.assertEqual(engine.touch_count, 0)
        self.assertEqual(engine.shared_stage.top_k, 99)
        self.assertEqual(container.plugin_config, before_config)

    def test_missing_or_cross_scope_preview_is_rejected_before_query(self):
        from webui.blueprints.channel_config import preview_channel_config_impact

        container, engine = self._container()
        scope = self._scope(conversation="g1")
        foreign = self._scope(conversation="g2")
        body = {
            "layer": "session",
            "scope": scope.to_dict(),
            "message": "remember",
            "channels": {"memory": {"top_k": 2}},
        }

        missing = asyncio.run(preview_channel_config_impact(container, body, request_scope=None))
        crossed = asyncio.run(preview_channel_config_impact(container, body, request_scope=foreign))

        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error_code"], "cross_scope_preview_rejected")
        self.assertFalse(crossed["ok"])
        self.assertEqual(crossed["error_code"], "cross_scope_preview_rejected")
        self.assertEqual(engine.calls, [])


if __name__ == "__main__":
    unittest.main()
