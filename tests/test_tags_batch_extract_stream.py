import asyncio
import sqlite3
import sys
import types
import unittest


if "quart" not in sys.modules:
    quart_mod = types.ModuleType("quart")

    class _Blueprint:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    class _Response:
        def __init__(self, body, content_type=None):
            self.body = body
            self.content_type = content_type

    quart_mod.Blueprint = _Blueprint
    quart_mod.Response = _Response
    quart_mod.jsonify = lambda payload=None, **kwargs: payload if payload is not None else kwargs
    quart_mod.request = types.SimpleNamespace(args={}, headers={}, method="GET", get_json=lambda *args, **kwargs: {})
    sys.modules["quart"] = quart_mod


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn
        self._next_tag_id = 100

    def add_tag_extended(self, **kwargs):
        self._next_tag_id += 1
        return self._next_tag_id


class _FakeExtractor:
    def __init__(self):
        self.calls = []

    async def extract_tags_batch(self, messages):
        self.calls.append(messages)
        return [[{"name": f"标签{m['id']}", "type": "topic", "confidence": 0.9}] for m in messages]


class _FakeEmbedding:
    async def get_embeddings(self, names):
        return [None for _ in names]


class TagsBatchExtractStreamTest(unittest.TestCase):
    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, sender_name TEXT)")
        conn.execute("CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER, position INTEGER, relevance REAL, UNIQUE(memory_id, tag_id))")
        for mid in range(1, 4):
            conn.execute(
                "INSERT INTO memories (id, content, sender_name) VALUES (?, ?, ?)",
                (mid, f"这是一条需要标签提取的测试记忆 {mid}", "tester"),
            )
        conn.commit()
        self.addCleanup(conn.close)
        return conn

    def test_one_http_stream_processes_only_requested_batch_and_reports_remaining(self):
        from webui.blueprints.tags import iter_batch_extract_events

        conn = self._conn()
        extractor = _FakeExtractor()
        container = types.SimpleNamespace(
            db=_FakeDB(conn),
            tag_extractor=extractor,
            embedding_service=_FakeEmbedding(),
        )

        events = asyncio.run(_collect(iter_batch_extract_events(container, batch_size=2, runtime_budget_seconds=45)))

        self.assertEqual(len(extractor.calls), 1)
        self.assertEqual([m["id"] for m in extractor.calls[0]], [3, 2])
        self.assertEqual(events[-1]["processed"], 2)
        self.assertEqual(events[-1]["tagged"], 2)
        self.assertEqual(events[-1]["remaining"], 1)
        self.assertTrue(events[-1]["partial"])
        self.assertTrue(events[-1]["done"])
        self.assertIn("本轮", events[-1]["message"])

    def test_quality_counts_ignore_orphan_memory_tag_rows_and_report_extractable(self):
        from webui.blueprints.tags import build_tag_quality_payload
        from webui.blueprints.system import count_existing_tagged_memories, count_untagged_memories

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, tag_type TEXT)")
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER)")
        conn.executemany("INSERT INTO tags (id, name, tag_type) VALUES (?, ?, ?)", [(10, "有效", "topic"), (99, "孤儿", "topic")])
        conn.executemany("INSERT INTO memories (id, content) VALUES (?, ?)", [
            (1, "这是一条已经有标签的长期记忆"),
            (2, "这是一条仍然需要提取标签的长期记忆"),
            (3, "短"),
        ])
        conn.executemany("INSERT INTO memory_tags (memory_id, tag_id) VALUES (?, ?)", [(1, 10), (999, 99)])
        conn.commit()

        payload = build_tag_quality_payload(conn)

        self.assertEqual(count_existing_tagged_memories(conn), 1)
        self.assertEqual(count_untagged_memories(conn), 2)
        self.assertEqual(payload["tagged_memories"], 1)
        self.assertEqual(payload["untagged_memories"], 2)
        self.assertEqual(payload["extractable_untagged_memories"], 1)
        self.assertEqual(payload["skipped_short_untagged_memories"], 1)
        self.assertEqual(payload["orphan_memory_tag_refs"], 1)

    def test_normalize_tag_execution_options_clamps_and_validates_shared_fields(self):
        from webui.blueprints.tags import normalize_tag_execution_options

        opts = normalize_tag_execution_options({
            "extract_tags": "yes",
            "tag_batch_size": "999",
            "tag_write_policy": "replace",
            "skip_short_min_length": "3",
        })

        self.assertTrue(opts["extract_tags"])
        self.assertEqual(opts["tag_batch_size"], 50)
        self.assertEqual(opts["tag_write_policy"], "replace")
        self.assertEqual(opts["skip_short_min_length"], 3)
        with self.assertRaises(ValueError):
            normalize_tag_execution_options({"tag_write_policy": "wipe"})

    def test_shared_tag_memory_batch_respects_missing_only_append_and_replace(self):
        from webui.blueprints.tags import tag_memory_batch

        conn = self._conn()
        conn.execute("INSERT INTO memory_tags (memory_id, tag_id, position, relevance) VALUES (1, 1, 1, 0.5)")
        conn.commit()
        db = _FakeDB(conn)
        extractor = _FakeExtractor()
        messages = [
            {"id": 1, "content": "已有标签但仍在选中范围内的记忆", "sender": "tester"},
            {"id": 2, "content": "没有标签，需要被 missing_only 处理", "sender": "tester"},
        ]

        result = asyncio.run(tag_memory_batch(db, _FakeEmbedding(), extractor, messages, tag_batch_size=5, tag_write_policy="missing_only"))
        self.assertEqual([m["id"] for m in extractor.calls[0]], [2])
        self.assertEqual(result["tagged"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_tags WHERE memory_id = 1").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_tags WHERE memory_id = 2").fetchone()[0], 1)

        extractor.calls.clear()
        append_result = asyncio.run(tag_memory_batch(db, _FakeEmbedding(), extractor, [messages[0]], tag_batch_size=5, tag_write_policy="append"))
        self.assertEqual([m["id"] for m in extractor.calls[0]], [1])
        self.assertEqual(append_result["tagged"], 1)
        self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM memory_tags WHERE memory_id = 1").fetchone()[0], 2)

        extractor.calls.clear()
        replace_result = asyncio.run(tag_memory_batch(db, _FakeEmbedding(), extractor, [messages[0]], tag_batch_size=5, tag_write_policy="replace"))
        self.assertEqual([m["id"] for m in extractor.calls[0]], [1])
        self.assertEqual(replace_result["tagged"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_tags WHERE memory_id = 1 AND tag_id = 1").fetchone()[0], 0)

    def test_frontend_stream_reader_throws_error_payloads_instead_of_silent_success(self):
        source = open("webui/frontend/src/api/memories.ts", encoding="utf-8").read()

        self.assertIn("payload.error", source)
        self.assertIn("throw new Error", source)

    def test_frontend_maintain_page_tracks_cancellable_durable_job_progress(self):
        page = open("webui/frontend/src/pages/maintain/MaintainPage.tsx", encoding="utf-8").read()

        self.assertIn("waitForMaintenanceJob", page)
        self.assertIn("cancelMaintenanceJob", page)
        self.assertIn("AbortController", page)
        self.assertIn("请求取消", page)
        self.assertIn("progress.processed", page)
        self.assertIn("progress.total", page)
        self.assertIn("jobLogs", page)
        self.assertIn("在任务历史查看", page)


async def _collect(generator):
    return [event async for event in generator]


if __name__ == "__main__":
    unittest.main()
