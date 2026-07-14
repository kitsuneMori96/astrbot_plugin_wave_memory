import asyncio
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

    class _Request:
        headers = {}
        args = {}

        async def get_json(self, silent=False):
            return {}

    def _jsonify(obj=None, *args, **kwargs):
        return {} if obj is None else obj

    quart_mod.Blueprint = _Blueprint
    quart_mod.request = _Request()
    quart_mod.jsonify = _jsonify
    sys.modules["quart"] = quart_mod


_SCOPE_ARGS = {
    "bot_id": "bot-alpha",
    "session_id": "qq:group:g1",
    "visibility": "group",
}


class NeuroGalaxy3DFrontendContractTest(unittest.TestCase):
    def test_explore_product_entry_is_retained_and_scope_is_explicit(self):
        html_path = REPO_ROOT / "webui" / "static" / "explore.html"
        self.assertTrue(html_path.exists())
        html = html_path.read_text(encoding="utf-8")
        self.assertIn("galaxy-container", html)
        self.assertIn("OrbitControls", html)
        self.assertIn("scope-bot-id", html)
        self.assertIn("scope-session-id", html)
        self.assertIn("scope-visibility", html)
        self.assertIn("installScopedApiFetch", html)
        self.assertIn("只读", html)

    def test_kg_js_keeps_3d_read_runtime(self):
        js = (REPO_ROOT / "webui" / "static" / "kg.js").read_text(encoding="utf-8")
        for marker in (
            "new THREE.Scene", "new THREE.Raycaster", "graphState", "nodes: new Map",
            "edges: new Map", "appendGraphData", "doQuery", "doPathFind",
            "loadPersonGraph", "focusPerson", "focusNode", "loadTimeline",
        ):
            self.assertIn(marker, js)
        self.assertNotIn("new Sigma", js)
        self.assertNotIn("graphology.Graph", js)

    def test_multilayer_threejs_contract_is_explicit_and_read_only(self):
        html = (REPO_ROOT / "webui" / "static" / "explore.html").read_text(encoding="utf-8")
        js = (REPO_ROOT / "webui" / "static" / "kg.js").read_text(encoding="utf-8")
        config = (REPO_ROOT / "webui" / "static" / "kg-config.js").read_text(encoding="utf-8")
        for layer in (
            "facts", "memories", "beliefs", "jargon", "concerns", "mood",
            "timeline", "affinity", "few_shot", "book_lore", "communities",
        ):
            self.assertIn(f'data-layer="{layer}"', html)
        for marker in (
            "_kgFullNodes", "data.nodes", "cfg-memory-limit", "cfg-similarity-k",
            "cfg-similarity-threshold", "buildProjectionMetadataHtml",
        ):
            self.assertIn(marker, html + js + config)
        self.assertNotIn("fact-edit-dialog", html)
        self.assertNotIn("relation-edit-dialog", html)
        self.assertNotIn("method: 'DELETE'", js)
        self.assertNotIn("method: 'PUT'", js)
        self.assertNotIn("/api/kg/add-fact", js)

    def test_explore_blueprint_has_auth_on_every_route(self):
        source = (REPO_ROOT / "webui" / "blueprints" / "explore.py").read_text(encoding="utf-8")
        route_count = source.count("@explore_bp.route")
        self.assertGreater(route_count, 0)
        self.assertEqual(route_count, source.count("@require_auth"))
        for legacy_query in ("FROM person_registry", "JOIN memory_tags ", "FROM tags ", "FROM facts ", "FROM tag_relations "):
            self.assertNotIn(legacy_query, source)


class ScopedExploreKgContractTest(unittest.TestCase):
    def setUp(self):
        from webui.container import ServiceContainer, get_container
        from webui.blueprints import explore, kg

        ServiceContainer.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(Path(self.tmp.name) / "wave_memory.db")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY, sender_id TEXT, sender_name TEXT, group_id TEXT,
                content TEXT, timestamp REAL, bot_id TEXT, session_id TEXT, visibility TEXT,
                resolution_state TEXT, quarantine INTEGER, version INTEGER
            );
            CREATE TABLE scoped_facts (
                id INTEGER PRIMARY KEY, bot_id TEXT, session_id TEXT, visibility TEXT,
                subject TEXT, predicate TEXT, object TEXT, confidence REAL, status TEXT,
                source_memory_id INTEGER, provenance TEXT, valid_from REAL, valid_until REAL,
                created_at REAL, updated_at REAL
            );
            CREATE TABLE scoped_tags (
                id INTEGER PRIMARY KEY, bot_id TEXT, session_id TEXT, visibility TEXT,
                name TEXT, tag_type TEXT, description TEXT, confidence REAL, metadata TEXT,
                created_at REAL, updated_at REAL
            );
            CREATE TABLE scoped_memory_tags (
                bot_id TEXT, session_id TEXT, visibility TEXT, memory_id INTEGER, tag_id INTEGER,
                position INTEGER, relevance REAL, created_at REAL
            );
            CREATE TABLE scoped_tag_relations (
                id INTEGER PRIMARY KEY, bot_id TEXT, session_id TEXT, visibility TEXT,
                source_tag_id INTEGER, target_tag_id INTEGER, relation_type TEXT,
                weight REAL, confidence REAL, metadata TEXT, created_at REAL, updated_at REAL
            );
            CREATE TABLE facts (id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE tag_relations (id INTEGER PRIMARY KEY, source_tag_id INTEGER, target_tag_id INTEGER);

            INSERT INTO memories VALUES
                (1, 'u1', '同域用户', 'g1', '规范记忆', 10, 'bot-alpha', 'qq:group:g1', 'group', 'resolved', 0, 1),
                (2, 'u2', '跨域用户', 'g2', '跨域记忆', 11, 'bot-beta', 'qq:group:g2', 'group', 'resolved', 0, 1),
                (3, 'u3', '未解析用户', 'g1', '旧记忆', 12, NULL, NULL, NULL, 'unresolved', 1, NULL);
            INSERT INTO scoped_facts VALUES
                (11, 'bot-alpha', 'qq:group:g1', 'group', '同域用户', '喜欢', '猫', 0.9, 'reviewed', 1, '{}', NULL, NULL, 10, 10),
                (12, 'bot-beta', 'qq:group:g2', 'group', '跨域用户', '知道', '秘密', 0.9, 'reviewed', 2, '{}', NULL, NULL, 11, 11);
            INSERT INTO scoped_tags VALUES
                (21, 'bot-alpha', 'qq:group:g1', 'group', '同域用户', 'person', '', 0.9, '{}', 10, 10),
                (22, 'bot-alpha', 'qq:group:g1', 'group', '猫', 'entity', '', 0.9, '{}', 10, 10),
                (23, 'bot-beta', 'qq:group:g2', 'group', '秘密', 'entity', '', 0.9, '{}', 11, 11);
            INSERT INTO scoped_memory_tags VALUES ('bot-alpha', 'qq:group:g1', 'group', 1, 22, 0, 1.0, 10);
            INSERT INTO scoped_tag_relations VALUES
                (31, 'bot-alpha', 'qq:group:g1', 'group', 21, 22, '关注', 0.8, 0.8, '{}', 10, 10),
                (32, 'bot-beta', 'qq:group:g2', 'group', 23, 23, '泄漏', 1.0, 1.0, '{}', 11, 11);
            INSERT INTO facts VALUES (99, 'legacy', '泄漏', 'legacy-secret');
            """
        )
        self.conn.commit()
        container = get_container()
        container.db = types.SimpleNamespace(conn=self.conn, scoped_knowledge=None)
        container.plugin_config = {}
        self.explore = explore
        self.kg = kg
        self.originals = []
        for module in (explore, kg):
            self.originals.append((module, module.jsonify, module.request))
            module.jsonify = lambda value: value

    def tearDown(self):
        for module, jsonify, request in self.originals:
            module.jsonify = jsonify
            module.request = request

    def _request(self, args=None, body=None):
        class FakeRequest:
            def __init__(self):
                self.args = args or {}

            async def get_json(self, silent=False):
                return body or {}

        return FakeRequest()

    def test_explore_requires_complete_runtime_scope(self):
        self.explore.request = self._request({})
        payload, status = asyncio.run(self.explore.persons.__wrapped__())
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "scope_required")

    def test_explore_reads_only_exact_scope_memories(self):
        self.explore.request = self._request({**_SCOPE_ARGS, "limit": "50"})
        people = asyncio.run(self.explore.persons.__wrapped__())
        self.assertEqual([item["name"] for item in people], ["同域用户"])

        self.explore.request = self._request({**_SCOPE_ARGS, "max_memories": "50"})
        person = asyncio.run(self.explore.person.__wrapped__("u1"))
        self.assertEqual([item["content"] for item in person["memories"]], ["规范记忆"])
        self.assertNotIn("跨域记忆", str(person))

    def test_kg_full_uses_scoped_facts_and_relations_only(self):
        self.kg.request = self._request({**_SCOPE_ARGS, "layers": "facts"})
        payload = asyncio.run(self.kg.kg_full.__wrapped__())
        serialized = str(payload)
        self.assertEqual(payload["scope"]["payload"]["bot_id"], "bot-alpha")
        self.assertTrue(payload["read_only"])
        self.assertIn("同域用户", serialized)
        self.assertIn("猫", serialized)
        self.assertNotIn("legacy-secret", serialized)
        self.assertNotIn("跨域用户", serialized)
        self.assertNotIn("秘密", serialized)

    def test_legacy_and_bare_id_mutations_are_gone(self):
        for call in (
            lambda: self.kg.create_scoped_fact.__wrapped__(),
            lambda: self.kg.create_scoped_relation.__wrapped__(),
            lambda: self.kg.update_fact.__wrapped__(11),
            lambda: self.kg.delete_fact.__wrapped__(11),
            lambda: self.kg.update_tag_relation.__wrapped__(31),
            lambda: self.kg.delete_tag_relation.__wrapped__(31),
            lambda: self.kg.update_tag.__wrapped__(21),
            lambda: self.kg.rename_entity.__wrapped__(),
            lambda: self.kg.rename_entity_preview.__wrapped__(),
            lambda: self.kg.legacy_audit_facts.__wrapped__(),
            lambda: self.kg.legacy_audit_relations.__wrapped__(),
        ):
            payload, status = asyncio.run(call())
            self.assertEqual(status, 410)
            self.assertEqual(payload["error"]["code"], "legacy_mutation_disabled")

    def test_payment_is_disabled_without_strong_secret_and_never_writes_knowledge(self):
        before = (
            self.conn.execute("SELECT COUNT(*) FROM scoped_facts").fetchone()[0],
            self.conn.execute("SELECT COUNT(*) FROM scoped_tag_relations").fetchone()[0],
        )
        self.kg.request = self._request(_SCOPE_ARGS, {"amount": 100, "secret": "wavemoney"})
        payload, status = asyncio.run(self.kg.payment_webhook.__wrapped__())
        after = (
            self.conn.execute("SELECT COUNT(*) FROM scoped_facts").fetchone()[0],
            self.conn.execute("SELECT COUNT(*) FROM scoped_tag_relations").fetchone()[0],
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "payment_disabled")
        self.assertEqual(after, before)

    def test_scope_less_cache_warmup_never_reads_legacy_tables(self):
        result = self.kg.warmup_kg_cache("facts")
        self.assertEqual(result["edges"], 0)
        self.assertEqual(result["reason_code"], "scope_required")


if __name__ == "__main__":
    unittest.main()
