import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]


if "astrbot.api" not in sys.modules:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")

    class _Logger:
        def debug(self, *args, **kwargs): pass
        def info(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass

    api_mod.logger = _Logger()
    sys.modules.setdefault("astrbot", astrbot_mod)
    sys.modules["astrbot.api"] = api_mod


if "quart" not in sys.modules:
    quart_mod = types.ModuleType("quart")

    class _Blueprint:
        def __init__(self, name, import_name, url_prefix=None):
            self.name = name
            self.import_name = import_name
            self.url_prefix = url_prefix

        def route(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    class _Quart:
        def __init__(self, *args, **kwargs):
            self.secret_key = ""

        def after_request(self, func):
            return func

        def register_blueprint(self, bp):
            return None

    class _Request:
        headers = {}
        args = {}

        async def get_json(self, silent=False):
            return {}

    def _jsonify(obj=None, *args, **kwargs):
        if obj is None:
            obj = {}
        if kwargs:
            if isinstance(obj, dict):
                obj.update(kwargs)
            else:
                obj = kwargs
        return obj

    class _Response:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    quart_mod.Blueprint = _Blueprint
    quart_mod.Quart = _Quart
    quart_mod.Response = _Response
    quart_mod.request = _Request()
    quart_mod.jsonify = _jsonify
    sys.modules["quart"] = quart_mod


class NeuroGalaxy3DFrontendContractTest(unittest.TestCase):
    def test_explore_uses_threejs_without_sigma_or_graphology(self):
        html = (REPO_ROOT / "webui" / "static" / "explore.html").read_text(encoding="utf-8")

        self.assertIn("three", html.lower())
        self.assertIn("OrbitControls", html)
        self.assertIn("galaxy-container", html)
        self.assertNotIn("sigma.js", html)
        self.assertNotIn("graphology", html.lower())
        self.assertNotIn("sigma-container", html)

    def test_kg_js_exposes_3d_graph_state_and_business_migrations(self):
        js = (REPO_ROOT / "webui" / "static" / "kg.js").read_text(encoding="utf-8")

        self.assertIn("new THREE.Scene", js)
        self.assertIn("new THREE.Raycaster", js)
        self.assertIn("graphState", js)
        self.assertIn("nodes: new Map", js)
        self.assertIn("edges: new Map", js)
        self.assertIn("appendGraphData", js)
        self.assertIn("doQuery", js)
        self.assertIn("doPathFind", js)
        self.assertIn("loadPersonGraph", js)
        self.assertIn("focusPerson", js)
        self.assertIn("focusNode", js)
        self.assertIn("loadTimeline", js)
        self.assertIn("deriveExpansionFromEntity", js)
        self.assertIn("d.facts", js)
        self.assertIn("d.relations", js)
        self.assertNotIn("new Sigma", js)
        self.assertNotIn("graphology.Graph", js)
        self.assertNotIn("renderer.getCamera", js)
        self.assertNotIn("window.focus =", js)

    def test_person_graph_contract_includes_focusable_person_node(self):
        py = (REPO_ROOT / "webui" / "blueprints" / "explore.py").read_text(encoding="utf-8")
        js = (REPO_ROOT / "webui" / "static" / "kg.js").read_text(encoding="utf-8")

        self.assertIn('person_node_id = f"p{qq_id}"', py)
        self.assertIn('"type": "person"', py)
        self.assertIn('"source": person_node_id', py)
        self.assertIn('`p${qqId}`', js)

    def test_kg_config_exposes_3d_layer_node_types(self):
        py = (REPO_ROOT / "webui" / "blueprints" / "kg.py").read_text(encoding="utf-8")

        for node_type in ("memory", "belief", "concern", "jargon", "community", "affinity", "source"):
            self.assertIn(f'"{node_type}"', py)

    def test_neuro_toy_table_frontend_exposes_layout_labels_and_relation_hud(self):
        html = (REPO_ROOT / "webui" / "static" / "explore.html").read_text(encoding="utf-8")
        js = (REPO_ROOT / "webui" / "static" / "kg.js").read_text(encoding="utf-8")

        for html_marker in (
            "layout-mode",
            "label-density",
            "camera-preset",
            "relation-panel",
            "relation-edit-dialog",
            "node-action-ring",
        ):
            self.assertIn(html_marker, html)

        for js_marker in (
            "LAYOUT_MODES",
            "applySemanticLayout",
            "setLabelDensity",
            "createAllReadableLabels",
            "createEdgeLabelObject",
            "pickEdge",
            "showRelationDetail",
            "saveRelationEdit",
            "createContextActionRing",
            "applyCameraPreset",
            "getEdgesForNode",
            "relationState",
        ):
            self.assertIn(js_marker, js)


class KGCacheWarmupContractTest(unittest.TestCase):
    def _connect(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "wave_memory.db")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                sender_id TEXT,
                sender_name TEXT,
                content TEXT,
                timestamp REAL
            );
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY,
                name TEXT,
                tag_type TEXT,
                description TEXT
            );
            CREATE TABLE tag_relations (
                id INTEGER PRIMARY KEY,
                source_tag_id INTEGER,
                target_tag_id INTEGER,
                relation_type TEXT,
                weight REAL,
                confidence REAL,
                metadata TEXT,
                created_at REAL
            );
            CREATE TABLE facts (
                id INTEGER PRIMARY KEY,
                subject TEXT,
                predicate TEXT,
                object TEXT,
                group_id TEXT,
                source_memory_id INTEGER,
                confidence REAL,
                created_at REAL,
                last_reinforced REAL,
                fact_type TEXT
            );
            CREATE TABLE beliefs (
                id INTEGER PRIMARY KEY,
                content TEXT,
                type TEXT,
                strength REAL,
                bot_id TEXT,
                status TEXT
            );
            CREATE TABLE concerns (
                id INTEGER PRIMARY KEY,
                topic TEXT,
                intensity REAL,
                bot_id TEXT
            );
            CREATE TABLE jargon (
                id INTEGER PRIMARY KEY,
                word TEXT,
                meaning TEXT,
                frequency INTEGER,
                group_id TEXT,
                is_jargon INTEGER
            );
            CREATE TABLE user_profiles (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                nickname TEXT,
                affection INTEGER,
                bot_id TEXT
            );
            """
        )
        conn.execute("INSERT INTO memories (id, sender_id, sender_name, content, timestamp) VALUES (1, 'u1', '羽书', '你好', 100)")
        conn.execute("INSERT INTO tags (id, name, tag_type) VALUES (1, '羽书', 'person'), (2, '记忆', 'topic')")
        conn.execute("INSERT INTO tag_relations (id, source_tag_id, target_tag_id, relation_type, weight, confidence, metadata, created_at) VALUES (7, 1, 2, '喜欢', 0.8, 0.77, '{\"source\":\"test\"}', 100)")
        conn.execute("INSERT INTO facts (id, subject, predicate, object, group_id, source_memory_id, confidence, created_at, last_reinforced, fact_type) VALUES (11, '羽书', '关心', '记忆', 'g1', 1, 0.9, 100, 120, 'FACTUAL')")
        conn.commit()
        return conn

    def test_warmup_populates_full_graph_cache_without_http_request(self):
        from webui.container import ServiceContainer, get_container
        from webui.blueprints import kg

        ServiceContainer.reset()
        conn = self._connect()
        self.addCleanup(conn.close)
        container = get_container()
        container.db = types.SimpleNamespace(conn=conn)
        container.cooccurrence = None

        kg.clear_kg_cache()
        result = kg.warmup_kg_cache(layers="facts")

        self.assertTrue(result["ok"])
        self.assertEqual(result["layers"], "facts")
        self.assertGreaterEqual(result["edges"], 1)
        self.assertIn("full:facts", kg._overview_cache)
        cached = kg._overview_cache["full:facts"]
        self.assertGreaterEqual(cached["total"], 1)
        self.assertEqual(cached["layers"], ["facts"])

    def test_full_graph_cache_version_refreshes_non_fact_layers(self):
        from webui.container import ServiceContainer, get_container
        from webui.blueprints import kg

        ServiceContainer.reset()
        conn = self._connect()
        self.addCleanup(conn.close)
        container = get_container()
        container.db = types.SimpleNamespace(conn=conn)
        container.cooccurrence = None

        kg.clear_kg_cache()
        first = kg.build_full_graph_data("jargon", use_cache=True)
        conn.execute("INSERT INTO jargon (word, meaning, frequency, group_id, is_jargon) VALUES ('大声暗道', '公开说出私密话', 7, 'g1', 1)")
        conn.commit()
        second = kg.build_full_graph_data("jargon", use_cache=True)

        self.assertEqual(first["total"], 0)
        self.assertEqual(second["total"], 1)
        self.assertIn("full:jargon_version", kg._overview_cache)

    def test_full_graph_edges_include_editable_relation_metadata(self):
        from webui.container import ServiceContainer, get_container
        from webui.blueprints import kg

        ServiceContainer.reset()
        conn = self._connect()
        self.addCleanup(conn.close)
        container = get_container()
        container.db = types.SimpleNamespace(conn=conn)
        container.cooccurrence = None

        kg.clear_kg_cache()
        data = kg.build_full_graph_data("facts", use_cache=False)
        edges = data["edges"]
        fact_edge = next(e for e in edges if e.get("kind") == "fact")
        tag_edge = next(e for e in edges if e.get("kind") == "tag_relation")

        self.assertEqual(fact_edge["id"], "fact:11")
        self.assertEqual(fact_edge["fact_id"], 11)
        self.assertEqual(fact_edge["source_memory_id"], 1)
        self.assertEqual(fact_edge["fact_type"], "FACTUAL")
        self.assertTrue(fact_edge["editable"])
        self.assertEqual(tag_edge["id"], "tagrel:7")
        self.assertEqual(tag_edge["relation_id"], 7)
        self.assertEqual(tag_edge["source_tag_id"], 1)
        self.assertEqual(tag_edge["target_tag_id"], 2)
        self.assertEqual(tag_edge["confidence"], 0.77)
        self.assertEqual(tag_edge["metadata"], {"source": "test"})
        self.assertTrue(tag_edge["editable"])

    def test_update_tag_relation_and_tag_and_entity_rename_contracts(self):
        from webui.container import ServiceContainer, get_container
        from webui.blueprints import kg

        class FakeRequest:
            args = {}
            def __init__(self, payload):
                self.payload = payload
            async def get_json(self, silent=False):
                return self.payload

        async def run():
            ServiceContainer.reset()
            conn = self._connect()
            self.addCleanup(conn.close)
            container = get_container()
            container.db = types.SimpleNamespace(conn=conn)
            container.cooccurrence = None

            with patch.object(kg, "request", FakeRequest({"relation_type": "讨厌", "weight": 0.33, "confidence": 0.44})):
                rel_result = await kg.update_tag_relation(7)
            rel = conn.execute("SELECT relation_type, weight, confidence FROM tag_relations WHERE id=7").fetchone()

            with patch.object(kg, "request", FakeRequest({"name": "记忆体", "tag_type": "entity", "description": "可编辑标签"})):
                tag_result = await kg.update_tag(2)
            tag = conn.execute("SELECT name, tag_type, description FROM tags WHERE id=2").fetchone()

            with patch.object(kg, "request", FakeRequest({"from": "羽书", "to": "羽書"})):
                preview_result = await kg.rename_entity_preview()
            with patch.object(kg, "request", FakeRequest({"from": "羽书", "to": "羽書"})):
                rename_result = await kg.rename_entity()
            renamed = conn.execute("SELECT subject FROM facts WHERE id=11").fetchone()[0]
            return rel_result, rel, tag_result, tag, preview_result, rename_result, renamed

        import asyncio
        rel_result, rel, tag_result, tag, preview_result, rename_result, renamed = asyncio.run(run())

        self.assertEqual(rel_result, {"ok": True, "relation_id": 7})
        self.assertEqual(tuple(rel), ("讨厌", 0.33, 0.44))
        self.assertEqual(tag_result, {"ok": True, "tag_id": 2})
        self.assertEqual(tuple(tag), ("记忆体", "entity", "可编辑标签"))
        self.assertEqual(preview_result["affected_facts"], 1)
        self.assertEqual(rename_result["updated_facts"], 1)
        self.assertEqual(renamed, "羽書")

    def test_kg_edit_apis_report_missing_rows_and_rename_preview_counts_tags(self):
        from webui.container import ServiceContainer, get_container
        from webui.blueprints import kg

        class FakeRequest:
            args = {}
            def __init__(self, payload):
                self.payload = payload
            async def get_json(self, silent=False):
                return self.payload

        async def run():
            ServiceContainer.reset()
            conn = self._connect()
            self.addCleanup(conn.close)
            container = get_container()
            container.db = types.SimpleNamespace(conn=conn)
            container.cooccurrence = None

            with patch.object(kg, "request", FakeRequest({"relation_type": "不存在"})):
                missing_rel = await kg.update_tag_relation(999)
            with patch.object(kg, "request", FakeRequest({"name": "不存在"})):
                missing_tag = await kg.update_tag(999)
            missing_delete = await kg.delete_tag_relation(999)
            with patch.object(kg, "request", FakeRequest({"from": "记忆", "to": "记忆体"})):
                preview = await kg.rename_entity_preview()
            with patch.object(kg, "request", FakeRequest({"from": "记忆", "to": "记忆体", "sync_tags": True})):
                renamed = await kg.rename_entity()
            tag_name = conn.execute("SELECT name FROM tags WHERE id=2").fetchone()[0]
            return missing_rel, missing_tag, missing_delete, preview, renamed, tag_name

        import asyncio
        missing_rel, missing_tag, missing_delete, preview, renamed, tag_name = asyncio.run(run())

        self.assertEqual(missing_rel[1], 404)
        self.assertFalse(missing_rel[0]["ok"])
        self.assertEqual(missing_tag[1], 404)
        self.assertFalse(missing_tag[0]["ok"])
        self.assertEqual(missing_delete[1], 404)
        self.assertFalse(missing_delete[0]["ok"])
        self.assertEqual(preview["tag_matches"], 1)
        self.assertEqual(renamed["updated_tags"], 1)
        self.assertEqual(tag_name, "记忆体")

    def test_add_fact_uses_requested_confidence_and_clears_cache(self):
        from webui.container import ServiceContainer, get_container
        from webui.blueprints import kg

        ServiceContainer.reset()
        inserted = []

        class FakeDB:
            def insert_fact(self, subject, predicate, obj, confidence=0.8, **kwargs):
                inserted.append((subject, predicate, obj, confidence))

        class FakeRequest:
            async def get_json(self, silent=False):
                return {"subject": "羽书", "predicate": "关心", "object": "记忆", "confidence": 0.42}

        container = get_container()
        container.db = FakeDB()
        kg._overview_cache["full:facts"] = {"total": 1}

        async def run():
            with patch.object(kg, "request", FakeRequest()):
                return await kg.add_fact()

        import asyncio
        result = asyncio.run(run())

        self.assertEqual(result, {"ok": True})
        self.assertEqual(inserted, [("羽书", "关心", "记忆", 0.42)])
        self.assertNotIn("full:facts", kg._overview_cache)

    def test_webui_start_triggers_background_kg_warmup_without_waiting(self):
        from webui import WaveMemoryWebUI

        calls = []

        async def fake_start(self):
            return None

        async def fake_warmup(self):
            calls.append("warmup-started")
            await asyncio.Event().wait()

        async def run():
            with patch("webui.server.Server.start", fake_start), patch("webui.WaveMemoryWebUI._async_kg_cache_warmup", fake_warmup):
                ui = WaveMemoryWebUI(
                    db=types.SimpleNamespace(conn=None),
                    query_engine=None,
                    embedding_service=None,
                    memory_index=None,
                    tag_index=None,
                    cooccurrence=None,
                )
                await asyncio.wait_for(ui.start(), timeout=0.1)
                await asyncio.sleep(0)
                self.assertEqual(calls, ["warmup-started"])
                self.assertIsNotNone(ui._kg_warmup_task)
                self.assertFalse(ui._kg_warmup_task.done())
                await ui.stop()
                self.assertIsNone(ui._kg_warmup_task)

        import asyncio
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
