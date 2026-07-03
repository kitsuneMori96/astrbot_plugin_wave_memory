import asyncio
import unittest


class FakeQueryEngine:
    def __init__(self, results=None, exc=None):
        self.results = results or []
        self.exc = exc
        self.calls = []

    async def query(self, *, text, group_id=None, top_k=5):
        self.calls.append({"text": text, "group_id": group_id, "top_k": top_k})
        if self.exc:
            raise self.exc
        return list(self.results)


class FakeWriter:
    def __init__(self):
        self.items = []

    async def enqueue(self, item):
        self.items.append(dict(item))


class LivingMemoryFacadeTest(unittest.TestCase):
    def test_search_memories_uses_query_engine_and_returns_stable_result_fields(self):
        from services.compat.livingmemory_facade import WaveMemoryLivingMemoryFacade

        query_engine = FakeQueryEngine(results=[
            {
                "id": 7,
                "content": "羽书喜欢苹果派",
                "score": 0.91,
                "importance": 1.4,
                "source": "core",
                "group_id": "group-1",
                "sender_id": "10001",
                "sender_name": "小明",
                "timestamp": 12345.0,
                "similarity": 0.88,
            }
        ])
        facade = WaveMemoryLivingMemoryFacade(query_engine=query_engine, writer=FakeWriter())

        results = asyncio.run(facade.search_memories("苹果派", k=3, session_id="group-1", persona_id="yushu"))

        self.assertEqual(query_engine.calls, [{"text": "苹果派", "group_id": "group-1", "top_k": 3}])
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["id"], "7")
        self.assertEqual(result["content"], "羽书喜欢苹果派")
        self.assertEqual(result["score"], 0.91)
        self.assertEqual(result["importance"], 1.4)
        self.assertEqual(result["metadata"]["source"], "core")
        self.assertEqual(result["metadata"]["session_id"], "group-1")
        self.assertEqual(result["metadata"]["persona_id"], "yushu")
        self.assertEqual(result["metadata"]["sender_id"], "10001")
        self.assertEqual(result["metadata"]["similarity"], 0.88)

    def test_search_memories_returns_empty_list_for_blank_or_failed_query(self):
        from services.compat.livingmemory_facade import WaveMemoryLivingMemoryFacade

        facade = WaveMemoryLivingMemoryFacade(query_engine=FakeQueryEngine(exc=RuntimeError("boom")), writer=FakeWriter())

        self.assertEqual(asyncio.run(facade.search_memories("", k=5)), [])
        self.assertEqual(asyncio.run(facade.search_memories("会失败", k=5)), [])
        self.assertEqual(facade.last_error, "boom")

    def test_add_memory_enqueues_same_writer_queue_and_returns_stable_queued_id(self):
        from services.compat.livingmemory_facade import WaveMemoryLivingMemoryFacade

        writer = FakeWriter()
        facade = WaveMemoryLivingMemoryFacade(query_engine=FakeQueryEngine(), writer=writer, now=lambda: 456.0)

        queued_id = asyncio.run(facade.add_memory(
            "用户喜欢苹果派",
            session_id="group-1",
            persona_id="yushu",
            importance=0.7,
            metadata={"origin": "self_learning"},
        ))

        self.assertTrue(queued_id.startswith("queued:"))
        self.assertEqual(len(writer.items), 1)
        item = writer.items[0]
        self.assertEqual(item["group_id"], "group-1")
        self.assertEqual(item["sender_id"], "compat:yushu")
        self.assertEqual(item["sender_name"], "LivingMemory兼容")
        self.assertEqual(item["content"], "用户喜欢苹果派")
        self.assertEqual(item["timestamp"], 456.0)
        self.assertEqual(item["importance"], 0.7)
        self.assertEqual(item["source"], "compat_livingmemory")
        self.assertEqual(item["metadata"]["session_id"], "group-1")
        self.assertEqual(item["metadata"]["persona_id"], "yushu")
        self.assertEqual(item["metadata"]["origin"], "self_learning")

    def test_add_memory_returns_empty_string_when_writer_unavailable_or_content_blank(self):
        from services.compat.livingmemory_facade import WaveMemoryLivingMemoryFacade

        facade = WaveMemoryLivingMemoryFacade(query_engine=FakeQueryEngine(), writer=None)

        self.assertEqual(asyncio.run(facade.add_memory("", session_id="group-1")), "")
        self.assertEqual(asyncio.run(facade.add_memory("内容", session_id="group-1")), "")
        self.assertEqual(facade.last_error, "writer_unavailable")

    def test_build_livingmemory_compat_surface_exposes_initializer(self):
        from services.compat.livingmemory_facade import build_livingmemory_compat_surface

        surface = build_livingmemory_compat_surface(query_engine=FakeQueryEngine(), writer=FakeWriter())

        self.assertIs(surface.memory_engine, surface.initializer.memory_engine)
        self.assertTrue(surface.initializer.is_initialized)
        self.assertTrue(hasattr(surface.memory_engine, "search_memories"))
        self.assertTrue(hasattr(surface.memory_engine, "add_memory"))

    def test_plugin_source_mounts_facade_without_spoofing_livingmemory_plugin_name(self):
        from pathlib import Path

        source = Path("main.py").read_text(encoding="utf-8")

        self.assertIn("self.memory_engine = livingmemory_surface.memory_engine", source)
        self.assertIn("self.initializer = livingmemory_surface.initializer", source)
        self.assertNotIn("astrbot_plugin_livingmemory\"", source)
        self.assertNotIn("astrbot_plugin_livingmemory'", source)


if __name__ == "__main__":
    unittest.main()
