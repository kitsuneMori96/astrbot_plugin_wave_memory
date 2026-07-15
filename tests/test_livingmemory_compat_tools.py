import asyncio
import json
import unittest
from pathlib import Path


class FakeFacade:
    def __init__(self):
        self.search_calls = []
        self.add_calls = []

    async def search_memories(self, query, k=5, session_id=None, persona_id=None):
        self.search_calls.append({"query": query, "k": k, "session_id": session_id, "persona_id": persona_id})
        return [{"id": "7", "content": "苹果派记忆", "score": 0.9, "importance": 1.2, "metadata": {"source": "core"}}]

    async def add_memory(self, content, session_id=None, persona_id=None, importance=0.7, metadata=None):
        self.add_calls.append({
            "content": content,
            "session_id": session_id,
            "persona_id": persona_id,
            "importance": importance,
            "metadata": metadata,
        })
        return "queued:abc123"


class LivingMemoryCompatToolsTest(unittest.TestCase):
    def test_build_alias_tools_respects_config_gate(self):
        from tools.livingmemory_compat_tools import build_livingmemory_compat_tools

        facade = FakeFacade()

        self.assertEqual(build_livingmemory_compat_tools(facade, enabled=False), [])
        tools = build_livingmemory_compat_tools(facade, enabled=True)

        self.assertEqual([tool.name for tool in tools], ["recall_long_term_memory", "memorize_long_term_memory"])

    def test_recall_alias_calls_facade_search_and_returns_json_results(self):
        from tools.livingmemory_compat_tools import RecallLongTermMemoryTool

        facade = FakeFacade()
        tool = RecallLongTermMemoryTool(memory_engine=facade)

        payload = json.loads(asyncio.run(tool.call(None, query="苹果派", k=3, session_id="group-1", persona_id="yushu")))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["results"][0]["id"], "7")
        self.assertEqual(facade.search_calls, [{"query": "苹果派", "k": 3, "session_id": "group-1", "persona_id": "yushu"}])

    def test_memorize_alias_calls_facade_add_and_returns_queued_id(self):
        from tools.livingmemory_compat_tools import MemorizeLongTermMemoryTool

        facade = FakeFacade()
        tool = MemorizeLongTermMemoryTool(memory_engine=facade)

        payload = json.loads(asyncio.run(tool.call(
            None,
            content="用户喜欢苹果派",
            session_id="group-1",
            persona_id="yushu",
            importance=0.8,
            metadata={"origin": "chatplus"},
        )))

        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["memory_id"], "queued:abc123")
        self.assertEqual(facade.add_calls[0]["metadata"], {"origin": "chatplus"})

    def test_alias_tools_return_friendly_error_when_facade_missing(self):
        from tools.livingmemory_compat_tools import RecallLongTermMemoryTool, MemorizeLongTermMemoryTool

        recall = RecallLongTermMemoryTool(memory_engine=None)
        memorize = MemorizeLongTermMemoryTool(memory_engine=None)

        self.assertIn("未初始化", asyncio.run(recall.call(None, query="苹果派")))
        self.assertIn("未初始化", asyncio.run(memorize.call(None, content="用户喜欢苹果派")))

    def test_main_source_registers_alias_tools_without_default_spoofing(self):
        source = Path("main.py").read_text(encoding="utf-8")

        self.assertIn("build_livingmemory_compat_tools", source)
        self.assertIn("self.livingmemory_alias_tools_registered", source)
        self.assertIn("*livingmemory_alias_tools", source)


if __name__ == "__main__":
    unittest.main()
