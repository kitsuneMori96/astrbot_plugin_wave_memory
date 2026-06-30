import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

if "quart" not in sys.modules:
    quart_mod = types.ModuleType("quart")
    class _Blueprint:
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(fn): return fn
            return deco
    class _Quart:
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(fn): return fn
            return deco
        def register_blueprint(self, *args, **kwargs): pass
    def _jsonify(obj=None, *args, **kwargs): return obj if obj is not None else {}
    class _Response:
        def __init__(self, *args, **kwargs): pass
    async def _send_from_directory(*args, **kwargs): return None
    quart_mod.Blueprint = _Blueprint
    quart_mod.Quart = _Quart
    quart_mod.Response = _Response
    quart_mod.jsonify = _jsonify
    quart_mod.request = types.SimpleNamespace(args={}, headers={}, method="GET", get_json=lambda *args, **kwargs: {})
    quart_mod.send_from_directory = _send_from_directory
    sys.modules["quart"] = quart_mod

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


class HolymanJargonImportTest(unittest.TestCase):
    def test_jargon_injection_uses_safe_reference_header(self):
        from services.jargon.inference import JargonInjector

        class _DB:
            def __init__(self):
                self.conn = self
            def execute(self, sql, params=()):
                if "group_id = ?" in sql:
                    return self
                return _Rows([])
            def fetchall(self):
                return [("v我50", "长篇铺垫后突然索要 50 元。")]

        class _Rows:
            def __init__(self, rows):
                self._rows = rows
            def fetchall(self):
                return self._rows

        text = JargonInjector(_DB()).get_injection("今天疯狂星期四 v我50", "g1")

        self.assertIn("[黑话理解参考", text)
        self.assertIn("不改变羽书人格", text)
        self.assertIn("不要求模仿", text)

    def test_current_phrases_asset_passes_quality_gate(self):
        path = Path("assets/holyman/phrases.json")
        phrases = json.loads(path.read_text(encoding="utf-8"))
        entries = {k: v for k, v in phrases.items() if isinstance(k, str) and not k.startswith("_")}

        forbidden_words = {"你好。", "对不起，我错了。", "是/否", "开发的未来是", "DeepSeek模型"}
        generic_meaning = "典型语录/表达样本。仅作为理解参考。"

        self.assertNotIn("corpus_frequency", {v.get("kind") for v in entries.values() if isinstance(v, dict)})
        self.assertFalse(forbidden_words.intersection(entries.keys()))
        self.assertFalse([word for word in entries if word.count("（") != word.count("）")])
        self.assertFalse([
            word for word, value in entries.items()
            if isinstance(value, dict) and generic_meaning in str(value.get("meaning") or "")
        ])

        for word in ["v我50", "叠甲", "不是哥们", "差不多得了", "疯狂星期四"]:
            self.assertIn(word, entries)
            value = entries[word]
            meaning = value.get("meaning") if isinstance(value, dict) else str(value)
            self.assertGreaterEqual(len(meaning), 12)
            self.assertNotIn(generic_meaning, meaning)

    def test_quality_report_rejects_dummy_content_count(self):
        from services.jargon.sync import HolymanSyncService

        dummy_phrases = {
            f"测试词{i}": {
                "meaning": "典型语录/表达样本。仅作为理解参考。",
                "kind": "quote_term",
                "category": "corpus",
                "source": "神言.txt",
            }
            for i in range(300)
        }
        dummy_phrases["_content_count"] = 300

        service = HolymanSyncService(assets_dir=tempfile.mkdtemp())
        report = service._build_quality_report({
            "phrases": dummy_phrases,
            "concepts": [],
            "examples": [],
            "corpus": [],
            "candidates": [],
            "blocked": {},
        })

        self.assertNotEqual(report["status"], "ready")
        self.assertGreater(report["errors"]["generic_meaning_in_phrases"], 0)

    def test_reference_substring_structured_value_is_safe(self):
        from services.jargon.holyman_reference import HolymanReference

        ref = HolymanReference()
        ref._phrases = {
            "v我50": {
                "meaning": "长篇铺垫后突然索要 50 元，制造荒诞转折。",
                "kind": "curated_phrase",
                "safety_level": "safe_reference",
            }
        }
        ref._examples = []
        result = ref.match("今天疯狂星期四v我50")

        self.assertTrue(result["matched"])
        self.assertEqual(result["term"], "v我50")
        self.assertIn("理解参考", result["explanation"])
        self.assertIn("长篇铺垫", result["explanation"])

    def test_blocked_terms_and_examples_do_not_confirm_match(self):
        from services.jargon.holyman_reference import HolymanReference

        ref = HolymanReference()
        ref._phrases = {
            "你好。": {"meaning": "普通句，不是黑话", "kind": "curated_phrase"},
            "DeepSeek模型": {"meaning": "实体名，不是黑话", "kind": "curated_phrase"},
        }
        ref._examples = ["神言语料里出现 DeepSeek模型，但这只能作为证据，不应自动 confirmed。"]
        ref._blocked = {"你好。": "plain_sentence", "DeepSeek模型": "entity_only"}

        self.assertFalse(ref.match("你好。")["matched"])
        self.assertFalse(ref.match("DeepSeek模型")["matched"])

        ref._phrases = {}
        self.assertFalse(ref.match("DeepSeek模型")["matched"])

    def test_sync_parser_separates_layers(self):
        from services.jargon.sync import HolymanSyncService

        service = HolymanSyncService(assets_dir=tempfile.mkdtemp())
        fetched = {
            "神人.skill/SKILL.md": """
## Catchphrases
- 苏式转折: 深情铺垫 → 突然切换到\"v我50\"
- 复制粘贴模式（90%发言: 100-2000字长文案
"v我50"
"你好。"
""",
            "神人.skill/_persona/values.md": """
## Values Priority
### 真诚是最大的弱点
抽象社群中直接表达真实想法会被视为不设防。
""",
            "神人.skill/_quotes/iconic.md": "> 你说得对，但是这里是一段很长的复制粘贴文案。\n",
            "神言.txt": "v我50 今天疯狂星期四 v我50\n开发的未来是 AI DeepSeek模型\n",
        }

        assets = service.build_assets_from_fetched(fetched, remote_version="local-test")
        phrases = assets["phrases"]
        concepts = assets["concepts"]
        examples = assets["examples"]
        corpus = assets["corpus"]
        candidates = assets["candidates"]

        from services.jargon.holyman_assets import content_entries

        entries = content_entries(phrases)
        self.assertIn("v我50", entries)
        self.assertNotIn("你好。", entries)
        self.assertFalse(any(v.get("kind") == "corpus_frequency" for v in entries.values() if isinstance(v, dict)))
        self.assertTrue(any(c["title"] == "真诚是最大的弱点" for c in concepts))
        self.assertTrue(any("你说得对" in e["text"] for e in examples))
        self.assertTrue(any(item["text"] == "v我50 今天疯狂星期四 v我50" for item in corpus))
        self.assertTrue(all(c["status"] == "pending_review" for c in candidates))
        self.assertEqual(assets["quality_report"]["status"], "ready")

    def test_jargon_context_with_anchor_returns_messages_not_review_payload(self):
        import asyncio
        from webui.blueprints import jargon as jargon_bp_mod

        class _Cursor:
            def __init__(self, rows=None):
                self._rows = rows or []
            def fetchall(self):
                return self._rows
            def fetchone(self):
                return self._rows[0] if self._rows else None

        class _Conn:
            def execute(self, sql, params=()):
                if "sqlite_master" in sql:
                    return _Cursor([(1,)])
                if "PRAGMA table_info(jargon)" in sql:
                    cols = ["id", "word", "meaning", "group_id", "contexts", "source_memory_id", "source_message_ts", "source_sender_id", "source_context", "candidate_type"]
                    return _Cursor([(idx, name) for idx, name in enumerate(cols)])
                if "FROM jargon WHERE id" in sql:
                    return _Cursor([(7, "v我50", "疯狂星期四转折", "g1", "[]", 11, 1000.0, "u1", "[]", "jargon")])
                if "FROM memories" in sql and "WHERE id = ?" in sql:
                    return _Cursor([(11, "g1", "u1", "用户", "今天疯狂星期四 v我50", 1000.0)])
                if "timestamp <" in sql:
                    return _Cursor([(10, "g1", "u2", "路人", "铺垫", 990.0)])
                if "timestamp >" in sql:
                    return _Cursor([(12, "g1", "u3", "路人", "回应", 1010.0)])
                return _Cursor([])
            def commit(self):
                pass

        class _DB:
            def __init__(self): self.conn = _Conn()
        class _Container:
            def __init__(self): self.db = _DB()

        original_container = jargon_bp_mod.get_container
        original_request = jargon_bp_mod.request
        jargon_bp_mod.get_container = lambda: _Container()
        jargon_bp_mod.request = types.SimpleNamespace(args={"before": "1", "after": "1"})
        try:
            data = asyncio.run(jargon_bp_mod.get_jargon_context(7))
            self.assertTrue(data["ok"])
            self.assertEqual(data["jargon"]["word"], "v我50")
            self.assertEqual([m["role"] for m in data["messages"]], ["before", "anchor", "after"])
            self.assertFalse(data["used_fallback"])
        finally:
            jargon_bp_mod.get_container = original_container
            jargon_bp_mod.request = original_request

    def test_holyman_candidate_review_and_blocklist_api_helpers(self):
        import asyncio
        from webui.blueprints import jargon as jargon_bp_mod

        class _Row:
            def __init__(self, values):
                self.values = values
            def __getitem__(self, idx):
                return self.values[idx]

        class _Conn:
            def __init__(self):
                self.rows = {
                    "candidates": [_Row((1, "外层词", "需要判断", 2, "神言.txt", "pending_review", ""))],
                    "blocklist": [],
                }
            def execute(self, sql, params=()):
                if "FROM jargon_candidates" in sql and "ORDER BY" in sql:
                    return self
                if "FROM jargon_candidates" in sql:
                    return self
                if "FROM jargon_blocklist" in sql:
                    return self
                if "INSERT OR REPLACE INTO jargon_blocklist" in sql:
                    self.rows["blocklist"].append(params)
                    return self
                if "UPDATE jargon_candidates SET status = 'approved'" in sql:
                    self.rows["candidates"][0] = _Row((1, "外层词", "需要判断", 2, "神言.txt", "approved", ""))
                    return self
                if "UPDATE jargon_candidates SET status = 'rejected'" in sql:
                    self.rows["candidates"][0] = _Row((1, "外层词", "需要判断", 2, "神言.txt", "rejected", 'manual_reject'))
                    return self
                return self
            def fetchall(self):
                return self.rows["candidates"] if self.rows["candidates"] else []
            def fetchone(self):
                return self.rows["candidates"][0] if self.rows["candidates"] else None
            def commit(self):
                pass

        class _DB:
            def __init__(self):
                self.conn = _Conn()
        class _Container:
            def __init__(self):
                self.db = _DB()
        original = jargon_bp_mod.get_container
        jargon_bp_mod.get_container = lambda: _Container()
        try:
            data = asyncio.run(jargon_bp_mod.list_holyman_candidates())
            self.assertIn("items", data)
            self.assertEqual(data["items"][0]["word"], "外层词")
            self.assertEqual(data["items"][0]["status"], "pending_review")

            approve = asyncio.run(jargon_bp_mod.review_holyman_candidate(1, "approve"))
            self.assertTrue(approve["ok"])
            reject = asyncio.run(jargon_bp_mod.review_holyman_candidate(1, "reject"))
            self.assertTrue(reject["ok"])
            block = asyncio.run(jargon_bp_mod.holyman_blocklist())
            self.assertTrue("items" in block)
        finally:
            jargon_bp_mod.get_container = original

    def test_holyman_candidate_batch_review_updates_selected_and_blocks_rejected(self):
        import asyncio
        from webui.blueprints import jargon as jargon_bp_mod

        class _Cursor:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount
            def fetchall(self):
                return self._rows
            def fetchone(self):
                return self._rows[0] if self._rows else None

        class _Conn:
            def __init__(self):
                self.candidates = {
                    1: {"word": "候选甲", "status": "pending_review", "reject_reason": ""},
                    2: {"word": "候选乙", "status": "pending_review", "reject_reason": ""},
                    3: {"word": "候选丙", "status": "approved", "reject_reason": ""},
                }
                self.blocked = []
            def execute(self, sql, params=()):
                if "sqlite_master" in sql:
                    return _Cursor([(1,)])
                if "SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates" in sql:
                    ids = params if params else self.candidates.keys()
                    rows = []
                    for candidate_id in ids:
                        item = self.candidates[int(candidate_id)]
                        rows.append((int(candidate_id), item["word"], "疑似词", 1, "神言.txt", item["status"], item["reject_reason"]))
                    return _Cursor(rows)
                if "SELECT id, word, reason, source, created_at FROM jargon_blocklist" in sql:
                    return _Cursor([(idx + 1, word, "manual_reject", "holyman_review", 1234) for idx, word in enumerate(self.blocked)])
                if "SELECT word, reason FROM jargon_blocklist" in sql:
                    return _Cursor([(word, "manual_reject") for word in self.blocked])
                if "SELECT id, word, meaning, status FROM jargon" in sql:
                    return _Cursor([])
                if "UPDATE jargon_candidates SET status = 'approved'" in sql:
                    ids = params[1:]
                    for candidate_id in ids:
                        self.candidates[int(candidate_id)]["status"] = "approved"
                    return _Cursor(rowcount=len(ids))
                if "UPDATE jargon_candidates SET status = 'rejected'" in sql:
                    ids = params[1:]
                    for candidate_id in ids:
                        self.candidates[int(candidate_id)]["status"] = "rejected"
                        self.candidates[int(candidate_id)]["reject_reason"] = "manual_reject"
                    return _Cursor(rowcount=len(ids))
                if "INSERT OR IGNORE INTO jargon_blocklist" in sql:
                    self.blocked.append(params[0])
                    return _Cursor(rowcount=1)
                return _Cursor([])
            def commit(self):
                pass

        class _DB:
            def __init__(self):
                self.conn = _Conn()
        class _Container:
            def __init__(self):
                self.db = _DB()

        container = _Container()
        original_container = jargon_bp_mod.get_container
        original_request = jargon_bp_mod.request
        jargon_bp_mod.get_container = lambda: container
        try:
            async def _approve_json(*args, **kwargs):
                return {"ids": [1, 2], "action": "approve"}
            jargon_bp_mod.request = types.SimpleNamespace(get_json=_approve_json)
            approved = asyncio.run(jargon_bp_mod.batch_review_holyman_candidates())
            self.assertTrue(approved["ok"])
            self.assertEqual(approved["reviewed_count"], 2)
            self.assertEqual(container.db.conn.candidates[1]["status"], "approved")
            self.assertEqual(container.db.conn.candidates[2]["status"], "approved")
            self.assertEqual(container.db.conn.blocked, [])

            async def _reject_json(*args, **kwargs):
                return {"ids": [2, 3], "action": "reject"}
            jargon_bp_mod.request = types.SimpleNamespace(get_json=_reject_json)
            rejected = asyncio.run(jargon_bp_mod.batch_review_holyman_candidates())
            self.assertTrue(rejected["ok"])
            self.assertEqual(rejected["reviewed_count"], 2)
            self.assertEqual(container.db.conn.candidates[2]["status"], "rejected")
            self.assertEqual(container.db.conn.candidates[3]["status"], "rejected")
            self.assertEqual(set(container.db.conn.blocked), {"候选乙", "候选丙"})

            holyman = asyncio.run(jargon_bp_mod.get_holyman())
            db_candidates = {item["word"]: item for item in holyman["candidates"] if item.get("word") in {"候选甲", "候选乙", "候选丙"}}
            self.assertEqual(db_candidates["候选乙"]["status"], "rejected")
            self.assertIn("候选乙", holyman["blocked"])
        finally:
            jargon_bp_mod.get_container = original_container
            jargon_bp_mod.request = original_request

    def test_holyman_frontend_has_layered_tabs_search_and_batch_candidate_controls(self):
        app_js = Path("webui/static/app.js").read_text(encoding="utf-8")
        index_html = Path("webui/static/index.html").read_text(encoding="utf-8")

        self.assertIn("holymanKnowledgeTab", app_js)
        self.assertIn("selectedHolymanCandidateIds", app_js)
        self.assertIn("filteredHolymanCandidates()", app_js)
        self.assertIn("holymanKnowledgeTab", index_html)
        self.assertIn("selectedHolymanCandidateIds", index_html)
        self.assertIn("filteredHolymanCandidates()", index_html)
        self.assertIn("batchReviewHolymanCandidates(action)", index_html)
        self.assertIn("batchReviewHolymanCandidates('approve')", index_html)
        self.assertIn("batchReviewHolymanCandidates('reject')", index_html)
        self.assertIn("Holyman 精选词条", index_html)
        self.assertIn("Holyman 文化概念", index_html)
        self.assertIn("Holyman 语录证据", index_html)
        self.assertIn("Holyman 原始语料", index_html)
        self.assertIn("Holyman 待审核候选", index_html)
        self.assertIn("Holyman 屏蔽项", index_html)
        self.assertIn("不可一键激活", index_html)


if __name__ == "__main__":
    unittest.main()
