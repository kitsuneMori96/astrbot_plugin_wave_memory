import asyncio
import json
import sqlite3
import sys
import tempfile
import time
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
    quart_mod.request = types.SimpleNamespace(args={}, headers={}, get_json=lambda *args, **kwargs: {})
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

if "astrbot.core.agent.tool" not in sys.modules:
    core_mod = types.ModuleType("astrbot.core")
    agent_mod = types.ModuleType("astrbot.core.agent")
    tool_mod = types.ModuleType("astrbot.core.agent.tool")
    run_ctx_mod = types.ModuleType("astrbot.core.agent.run_context")
    astr_ctx_mod = types.ModuleType("astrbot.core.astr_agent_context")

    class _FunctionTool:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

    class _ContextWrapper:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, context=None):
            self.context = context

    class _AstrAgentContext:
        pass

    tool_mod.FunctionTool = _FunctionTool
    run_ctx_mod.ContextWrapper = _ContextWrapper
    astr_ctx_mod.AstrAgentContext = _AstrAgentContext
    sys.modules.setdefault("astrbot.core", core_mod)
    sys.modules.setdefault("astrbot.core.agent", agent_mod)
    sys.modules["astrbot.core.agent.tool"] = tool_mod
    sys.modules["astrbot.core.agent.run_context"] = run_ctx_mod
    sys.modules["astrbot.core.astr_agent_context"] = astr_ctx_mod


class ReworkCoreTest(unittest.TestCase):
    def _connect(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "wave_memory.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(tmp.cleanup)
        return conn, path

    def test_migration_adds_episode_relationship_and_jargon_lifecycle_schema(self):
        from engine.db.migrations.v2_2_experience_rework import run_migration

        conn, path = self._connect()
        conn.execute("CREATE TABLE jargon (id INTEGER PRIMARY KEY, word TEXT, meaning TEXT, frequency INTEGER DEFAULT 0, confidence REAL DEFAULT 0, is_jargon INTEGER, contexts TEXT)")
        conn.execute("CREATE TABLE beliefs (id INTEGER PRIMARY KEY, content TEXT, sources TEXT, status TEXT)")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        self.assertTrue(run_migration(str(path)))

        conn = sqlite3.connect(path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("experience_episodes", tables)
        self.assertIn("relationship_events", tables)
        jargon_cols = {r[1] for r in conn.execute("PRAGMA table_info(jargon)")}
        self.assertTrue({"status", "scope", "source", "last_infer_freq", "reject_reason"}.issubset(jargon_cols))
        conn.close()

    def test_experience_service_records_and_reads_source_ids(self):
        from services.experience_episodes import ExperienceEpisodeService

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.execute("""
            CREATE TABLE experience_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT,
                episode_type TEXT NOT NULL,
                trigger_text TEXT,
                bot_inner_thought TEXT,
                bot_action TEXT,
                bot_reply TEXT,
                user_reaction TEXT,
                outcome TEXT,
                source_memory_ids TEXT DEFAULT '[]',
                emotional_weight REAL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()

        svc = ExperienceEpisodeService(conn)
        episode_id = svc.record_episode(
            bot_id="yushu",
            group_id="g1",
            user_id="u1",
            episode_type="bot_reply",
            trigger_text="投喂小蛋糕",
            bot_reply="谢谢",
            source_memory_ids=[11, 12],
            emotional_weight=0.6,
            created_at=1234.0,
        )
        recent = svc.recent_episodes(bot_id="yushu", user_id="u1", group_id="g1", limit=1)
        self.assertEqual(episode_id, recent[0]["id"])
        self.assertEqual(recent[0]["source_memory_ids"], [11, 12])
        self.assertEqual(recent[0]["episode_type"], "bot_reply")

    def test_relationship_event_service_applies_caps_and_updates_bot_scoped_profile(self):
        from services.relationship_events import RelationshipEventService

        conn, _ = self._connect()
        conn.executescript("""
            CREATE TABLE user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                nickname TEXT,
                affection INTEGER DEFAULT 0,
                interaction_count INTEGER DEFAULT 0,
                first_seen REAL,
                last_seen REAL,
                personality_tags TEXT,
                notes TEXT,
                metadata TEXT,
                bot_id TEXT DEFAULT 'yushu',
                UNIQUE(user_id, group_id, bot_id)
            );
            CREATE TABLE relationship_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                dimension TEXT NOT NULL,
                delta REAL NOT NULL,
                reason TEXT NOT NULL,
                source_episode_id INTEGER,
                source_memory_id INTEGER,
                created_at REAL NOT NULL
            );
        """)
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, metadata) VALUES (?, ?, ?, ?, ?)", (
            "u1", "g1", "yushu", 0, json.dumps({"dimensions": {"familiarity": 0, "trust": 0, "fun": 0, "hostility": 0, "depth": 0}}),
        ))
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, metadata) VALUES (?, ?, ?, ?, ?)", (
            "u1", "g1", "baizz", 50, "{}",
        ))
        conn.commit()

        self.addCleanup(conn.close)

        svc = RelationshipEventService(conn, single_delta_cap=5, daily_delta_cap=15)
        result = svc.record_event(
            bot_id="yushu",
            group_id="g1",
            user_id="u1",
            event_type="gift_or_feed",
            dimension="fun",
            delta=10,
            reason="用户投喂小蛋糕",
        )

        self.assertEqual(result["applied_delta"], 5)
        yushu = conn.execute("SELECT affection, metadata FROM user_profiles WHERE user_id='u1' AND group_id='g1' AND bot_id='yushu'").fetchone()
        baizz = conn.execute("SELECT affection, metadata FROM user_profiles WHERE user_id='u1' AND group_id='g1' AND bot_id='baizz'").fetchone()
        self.assertEqual(baizz[0], 50)
        dims = json.loads(yushu[1])["dimensions"]
        self.assertEqual(dims["fun"], 5)
        self.assertGreaterEqual(yushu[0], 1)
        event = conn.execute("SELECT bot_id, dimension, delta, reason FROM relationship_events").fetchone()
        self.assertEqual((event[0], event[1], event[2], event[3]), ("yushu", "fun", 5, "用户投喂小蛋糕"))

    def test_relationship_event_marks_known_bot_target_without_dropping_relation(self):
        from services.relationship_events import RelationshipEventService

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                nickname TEXT,
                affection INTEGER DEFAULT 0,
                interaction_count INTEGER DEFAULT 0,
                first_seen REAL,
                last_seen REAL,
                personality_tags TEXT,
                notes TEXT,
                metadata TEXT,
                bot_id TEXT DEFAULT 'yushu',
                UNIQUE(user_id, group_id, bot_id)
            );
            CREATE TABLE relationship_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                dimension TEXT NOT NULL,
                delta REAL NOT NULL,
                reason TEXT NOT NULL,
                source_episode_id INTEGER,
                source_memory_id INTEGER,
                created_at REAL NOT NULL
            );
        """)
        conn.commit()

        svc = RelationshipEventService(
            conn,
            target_profiles={"2500447291": {"db_id": "yushu", "name": "羽书"}},
        )
        svc.record_event(
            bot_id="baizz",
            group_id="g1",
            user_id="2500447291",
            event_type="direct_reply",
            dimension="trust",
            delta=2,
            reason="白真真观察到羽书发言",
            created_at=1234.0,
        )

        row = conn.execute("SELECT metadata FROM user_profiles WHERE bot_id='baizz' AND user_id='2500447291'").fetchone()
        meta = json.loads(row[0])
        self.assertEqual(meta["target_type"], "bot")
        self.assertEqual(meta["target_bot_id"], "yushu")
        self.assertEqual(meta["target_name"], "羽书")
        self.assertIn("dimensions", meta)

    def test_memory_repo_excludes_archived_memories_from_recall_candidates(self):
        import numpy as np
        from engine.db.connection import ConnectionManager
        from engine.db.memory_repo import MemoryRepo

        tmp = tempfile.TemporaryDirectory()
        cm = None
        try:
            cm = ConnectionManager(str(Path(tmp.name) / "memory.db"))
            repo = MemoryRepo(cm)
            active_id = repo.add_memory("g1", "正常记忆", vector=np.array([1, 0], dtype=np.float32), source="core")
            archived_id = repo.add_memory("g1", "应该被隔离的爸爸主人记忆", vector=np.array([0, 1], dtype=np.float32), source="core")
            cm.execute_write("UPDATE memories SET memory_type='archived' WHERE id=?", (archived_id,))
            cm.commit()

            vector_ids = [mid for mid, _ in repo.get_all_memory_vectors()]
            self.assertIn(active_id, vector_ids)
            self.assertNotIn(archived_id, vector_ids)

            rows = repo.get_memories_by_ids([active_id, archived_id])
            self.assertEqual([r["id"] for r in rows], [active_id])
            self.assertEqual(rows[0]["source"], "core")
        finally:
            if cm:
                cm.close()
            tmp.cleanup()

    def test_jargon_filter_ignores_bot_messages_and_vocal_noise(self):
        from services.jargon.statistical_filter import JargonStatisticalFilter

        import time

        filt = JargonStatisticalFilter(context_keep=10, jieba_threshold=999999)
        now = time.time()
        for i in range(8):
            filt.feed("邪修 邪修 嗷嗷嗷嗷嗷", "g1", sender_id="2500447291", timestamp=now + i)
        for i in range(5):
            filt.feed("邪修 今天又在修仙", "g1", sender_id="user_a", timestamp=now + 100 + i)

        candidates = filt.get_candidates("g1", min_freq=5, top_k=20)
        words = {c["word"] for c in candidates}

        self.assertIn("邪修", words)
        self.assertNotIn("嗷嗷", words)
        self.assertTrue(all(ctx["sender_id"] != "2500447291" for c in candidates for ctx in c.get("source_contexts", [])))

    def test_belief_emergence_records_relationship_event_evidence_metadata(self):
        from services.belief_emergence import BeliefEmergenceService

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE relationship_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                dimension TEXT NOT NULL,
                delta REAL NOT NULL,
                reason TEXT NOT NULL,
                source_episode_id INTEGER,
                source_memory_id INTEGER,
                created_at REAL NOT NULL
            );
            CREATE TABLE beliefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'world_view',
                strength REAL DEFAULT 0.5,
                bot_id TEXT NOT NULL DEFAULT '',
                sources TEXT DEFAULT '[]',
                conflicts TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active',
                created_at REAL,
                last_reinforced REAL,
                archived_reason TEXT,
                evidence_type TEXT DEFAULT 'memory',
                evidence_ids TEXT DEFAULT '[]'
            );
        """)
        now = time.time()
        for i in range(3):
            conn.execute(
                """INSERT INTO relationship_events
                   (bot_id, group_id, user_id, event_type, dimension, delta, reason, created_at)
                   VALUES ('yushu', 'g1', 'u1', 'gift_or_feed', 'trust', 3, '用户投喂小蛋糕', ?)""",
                (now - i,),
            )
        conn.commit()

        class DB:
            def __init__(self, connection):
                self.conn = connection
            def add_belief(self, content, belief_type, bot_id, strength=0.5, sources=None, status='active', evidence_type=None, evidence_ids=None):
                cur = self.conn.execute(
                    """INSERT INTO beliefs (content, type, strength, bot_id, sources, status, created_at, last_reinforced, evidence_type, evidence_ids)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (content, belief_type, strength, bot_id, json.dumps(sources or []), status, now, now, evidence_type or 'memory', json.dumps(evidence_ids or [])),
                )
                self.conn.commit()
                return cur.lastrowid
            def get_beliefs(self, bot_id=None, status='active', limit=50):
                return []

        created = asyncio.run(BeliefEmergenceService(DB(conn), bot_id='yushu').emerge_recent(days=1, limit=1))

        self.assertEqual(len(created), 1)
        row = conn.execute("SELECT sources, evidence_type, evidence_ids, strength FROM beliefs").fetchone()
        self.assertEqual(row[1], "relationship_event")
        self.assertEqual(json.loads(row[0]), json.loads(row[2]))
        self.assertGreater(row[3], 0.4)

    def test_jargon_injection_only_uses_confirmed_nonempty_entries(self):
        from services.jargon.inference import JargonInjector

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.execute("""
            CREATE TABLE jargon (
                id INTEGER PRIMARY KEY,
                word TEXT,
                meaning TEXT,
                is_jargon INTEGER,
                frequency INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0,
                is_global INTEGER DEFAULT 0,
                group_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                scope TEXT DEFAULT 'local',
                source TEXT DEFAULT 'wave_memory'
            )
        """)
        conn.execute("INSERT INTO jargon (word, meaning, is_jargon, confidence, group_id, status) VALUES ('大声暗道','公开说出本该私下说的话',1,0.9,'g1','confirmed')")
        conn.execute("INSERT INTO jargon (word, meaning, is_jargon, confidence, group_id, status) VALUES ('肚腩','',1,0.9,'g1','confirmed')")
        conn.execute("INSERT INTO jargon (word, meaning, is_jargon, confidence, group_id, status) VALUES ('朋友圈','普通词',1,0.9,'g1','pending')")
        injector = JargonInjector(type('DB', (), {'conn': conn})())
        text = injector.get_injection('这也太大声暗道了，肚腩朋友圈', 'g1')
        self.assertIn('大声暗道', text)
        self.assertNotIn('肚腩', text)
        self.assertNotIn('朋友圈', text)
        self.assertIn('仅供理解', text)

    def test_bot_soul_registry_selects_by_qq_and_db_id(self):
        from services.bot_soul import BotSoulRegistry, BotSoulRuntime

        yushu = BotSoulRuntime(profile=type('P', (), {'qq_id': '2500447291', 'db_id': 'yushu', 'name': '羽书'})())
        baizz = BotSoulRuntime(profile=type('P', (), {'qq_id': '1336495069', 'db_id': 'baizz', 'name': '白真真'})())
        registry = BotSoulRegistry([yushu, baizz])
        self.assertIs(registry.by_qq('2500447291'), yushu)
        self.assertIs(registry.by_qq('1336495069'), baizz)
        self.assertIs(registry.by_db_id('baizz'), baizz)
        self.assertIsNone(registry.by_qq('missing'))
        self.assertIsNone(registry.by_qq(''))

    def test_persona_evolution_filters_identity_contaminated_facts_and_tags(self):
        from services.persona_evolution import PersonaEvolution

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                nickname TEXT,
                affection INTEGER DEFAULT 0,
                interaction_count INTEGER DEFAULT 0,
                first_seen REAL,
                last_seen REAL,
                personality_tags TEXT,
                notes TEXT,
                metadata TEXT,
                bot_id TEXT DEFAULT 'yushu',
                UNIQUE(user_id, group_id, bot_id)
            );
            CREATE TABLE facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                predicate TEXT,
                object TEXT,
                confidence REAL DEFAULT 0.8,
                created_at REAL,
                last_reinforced REAL,
                fact_type TEXT DEFAULT 'FACTUAL'
            );
            CREATE TABLE person_registry (qq_id TEXT PRIMARY KEY, aliases TEXT);
            CREATE TABLE memories (id INTEGER PRIMARY KEY, sender_id TEXT, content TEXT);
            CREATE TABLE expression_patterns (id INTEGER PRIMARY KEY, group_id TEXT, situation TEXT, expression TEXT);
            CREATE TABLE relationship_events (id INTEGER PRIMARY KEY, bot_id TEXT, user_id TEXT, group_id TEXT, dimension TEXT, delta REAL, reason TEXT, created_at REAL);
        """)
        conn.execute("""INSERT INTO user_profiles
            (user_id, group_id, bot_id, nickname, affection, interaction_count, personality_tags, metadata)
            VALUES ('u1','g1','yushu','玩符',10,2,'["深夜活跃", "还有带着爸爸", "消息简短"]','{}')""")
        conn.execute("INSERT INTO facts (subject,predicate,object,confidence,fact_type) VALUES ('u1','给了','羽书灵魂，是最好的爸爸',0.8,'RELATIONAL')")
        conn.execute("INSERT INTO facts (subject,predicate,object,confidence,fact_type) VALUES ('u1','喜欢','成语接龙',0.8,'FACTUAL')")
        conn.commit()

        db = type('DB', (), {
            'conn': conn,
            'get_facts_by_subject': lambda self, subject, limit=20: [
                {"subject": r[1], "predicate": r[2], "object": r[3], "confidence": r[4], "fact_type": r[5]}
                for r in conn.execute("SELECT id,subject,predicate,object,confidence,fact_type FROM facts WHERE subject=?", (subject,)).fetchall()
            ],
        })()
        text = PersonaEvolution(db).get_persona_injection('u1', 'g1', bot_id='yushu')

        self.assertIn("成语接龙", text)
        self.assertIn("深夜活跃", text)
        self.assertNotIn("爸爸", text)
        self.assertNotIn("灵魂", text)

    def test_holyman_reference_matches_known_phrase_without_style_instruction(self):
        from services.jargon.holyman_reference import HolymanReference

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / '神人.skill'
            skill_dir.mkdir()
            (skill_dir / 'SKILL.md').write_text('Catchphrases\n- v我50\n- 你说得对，但是\n', encoding='utf-8')
            (root / '神言.txt').write_text('{"items":[{"id":1,"text":"深情铺垫最后v我50"}]}', encoding='utf-8')
            ref = HolymanReference(str(root))
            match = ref.match('v我50', '讲了半天突然v我50')
            self.assertTrue(match['matched'])
            self.assertEqual(match['classification'], 'global_abstract')
            self.assertNotIn('模仿', match['explanation'])

    def test_holyman_context_hit_does_not_confirm_unrelated_term(self):
        from services.jargon.holyman_reference import HolymanReference

        ref = HolymanReference()
        result = ref.match('帽子店', '今天有人说 v我50')
        self.assertFalse(result.get('matched'), result)
        self.assertTrue(result.get('context_hint'), result)

    def test_holyman_sync_filters_skill_document_noise(self):
        from services.jargon.sync import HolymanSyncService

        svc = HolymanSyncService()
        phrases = {}
        readme = """
# 背景
## 架构
## 安装使用
### Claude Code（推荐）
```bash
git clone https://github.com/ykdeso/holyman-skills.git
```
## OpenClaw
## License
"""
        skill = """
# AI 互联网抽象人
## Catchphrases
- **解构一切**: 任何严肃话题都能被拆解成梗
- **苏式转折**: 深情铺垫后突然 v我50
- "你说得对，但是……"
"""

        svc._parse_markdown_phrases(readme, "README.md", phrases)
        svc._parse_markdown_phrases(skill, "神人.skill/SKILL.md", phrases)

        self.assertIn("解构一切", phrases)
        self.assertIn("苏式转折", phrases)
        self.assertIn("你说得对，但是……", phrases)
        for noisy in ["背景", "架构", "安装使用", "Claude Code（推荐", "git clone https", "OpenClaw", "License"]:
            self.assertNotIn(noisy, phrases)
        self.assertFalse([word for word in phrases if "**" in word or word.startswith("|")], phrases)

    def test_holyman_sync_corpus_keeps_quotes_but_drops_generic_frequency_terms(self):
        from services.jargon.sync import HolymanSyncService

        svc = HolymanSyncService()
        phrases = {}
        corpus = json.dumps({"items": [
            {"text": "今天 吃饭 今天 出门 今天 睡觉"},
            {"text": "经典结尾是\"v我50\"，不是普通词。"},
            {"text": "今天 今天 今天 今天"},
        ]}, ensure_ascii=False)

        corpus_list = svc._parse_corpus(corpus, phrases)

        self.assertEqual(len(corpus_list), 3)
        self.assertIn("v我50", phrases)
        self.assertNotIn("今天", phrases)
        self.assertNotIn("吃饭", phrases)

    def test_holyman_sync_outputs_category_metadata(self):
        from services.jargon.sync import HolymanSyncService

        svc = HolymanSyncService()
        phrases = {}
        svc._parse_markdown_phrases(
            '- **游戏即信仰**: 游戏不是娱乐，是身份\n"原神"',
            "神人.skill/_knowledge/gaming.md",
            phrases,
        )
        svc._parse_markdown_phrases(
            '- **复制粘贴模式**: 长文案轰炸',
            "神人.skill/_persona/communication.md",
            phrases,
        )

        self.assertIsInstance(phrases["游戏即信仰"], dict)
        self.assertEqual(phrases["游戏即信仰"]["meaning"], "游戏不是娱乐，是身份")
        self.assertEqual(phrases["游戏即信仰"]["category"], "gaming")
        self.assertEqual(phrases["游戏即信仰"]["source"], "神人.skill/_knowledge/gaming.md")
        self.assertIn(phrases["游戏即信仰"]["kind"], {"bold_term", "colon_term"})
        self.assertEqual(phrases["复制粘贴模式"]["category"], "communication")

    def test_holyman_sync_replaces_legacy_asset_when_parsed_result_is_healthy(self):
        from services.jargon.sync import HolymanSyncService

        svc = HolymanSyncService()
        existing = {"旧版噪声词": "旧版短释义", "_version": "old", "_update_time": 1}
        parsed = {
            f"清噪词条{i}": {
                "meaning": f"清噪释义{i}",
                "category": "gaming",
                "source": "神人.skill/_knowledge/gaming.md",
                "kind": "bold_term",
            }
            for i in range(60)
        }

        merged = svc._merge_phrases_for_save(existing, parsed)

        self.assertNotIn("旧版噪声词", merged)
        self.assertIn("清噪词条1", merged)
        self.assertEqual(len(merged), 60)

    def test_holyman_content_hash_ignores_version_metadata_and_counts_entries(self):
        from services.jargon.sync import HolymanSyncService
        from webui.blueprints.jargon import _holyman_content_status

        phrases = {
            "v我50": {"meaning": "转折梗", "category": "skill-core"},
            "解构一切": {"meaning": "拆成梗", "category": "skill-core"},
            "_version": "sync-a",
            "_update_time": 1,
            "_remote_commit_version": "2026-01-01-abcdef0",
        }
        same_content_new_meta = dict(phrases, _version="sync-b", _update_time=999)

        self.assertEqual(HolymanSyncService.content_count(phrases), 2)
        self.assertEqual(HolymanSyncService.content_hash(phrases), HolymanSyncService.content_hash(same_content_new_meta))

        with_meta = HolymanSyncService.attach_content_metadata(phrases)
        self.assertEqual(with_meta["_content_count"], 2)
        self.assertEqual(with_meta["_content_hash"], HolymanSyncService.content_hash(phrases))

        ready_phrases = {
            f"词条{i}": {"meaning": f"释义{i}", "category": "corpus"}
            for i in range(300)
        }
        ready_phrases = HolymanSyncService.attach_content_metadata(ready_phrases)
        ready_status = _holyman_content_status(ready_phrases, local_count=300, remote_version="2026-02-01-bbbbbbb")
        self.assertFalse(ready_status["is_update_available"])
        self.assertEqual(ready_status["asset_status"], "ready")

        legacy_status = _holyman_content_status({"旧词": "旧释义"}, local_count=108, remote_version="2026-02-01-bbbbbbb")
        self.assertTrue(legacy_status["is_update_available"])
        self.assertEqual(legacy_status["asset_status"], "legacy")

    def test_holyman_reference_accepts_structured_phrase_values(self):
        from services.jargon.holyman_reference import HolymanReference

        ref = HolymanReference()
        ref._phrases = {
            "v我50": {"meaning": "常见荒诞转折梗。", "category": "skill-core", "source": "神人.skill/SKILL.md"},
        }
        ref._examples = []

        match = ref.match("v我50")

        self.assertTrue(match["matched"], match)
        self.assertEqual(match["term"], "v我50")
        self.assertIn("常见荒诞转折梗", match["explanation"])

    def test_jargon_service_requires_curated_holyman_layer_for_confirmation(self):
        from services.jargon.service import JargonService

        class FakeFilter:
            def feed(self, *args, **kwargs):
                pass
            def get_candidates(self, *args, **kwargs):
                return [{"word": "外层词", "frequency": 1, "contexts": ["外层词"], "source_contexts": [{"content": "外层词", "timestamp": 1.0, "sender_id": "u1"}]}]

        class FakeHolyman:
            def match(self, *args, **kwargs):
                return {
                    "matched": True,
                    "classification": "global_abstract",
                    "confidence": 0.9,
                    "term": "外层词",
                    "explanation": "证据层，不应直接确认",
                    "source_layer": "examples",
                }

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.execute("""
            CREATE TABLE jargon (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                meaning TEXT DEFAULT '',
                is_jargon INTEGER DEFAULT NULL,
                frequency INTEGER DEFAULT 1,
                confidence REAL DEFAULT 0,
                is_global INTEGER DEFAULT 0,
                group_id TEXT NOT NULL,
                contexts TEXT DEFAULT '[]',
                created_at INTEGER,
                updated_at INTEGER,
                status TEXT DEFAULT 'pending',
                scope TEXT DEFAULT 'local',
                source TEXT DEFAULT 'wave_memory',
                last_infer_freq INTEGER DEFAULT 0,
                reject_reason TEXT,
                source_memory_id INTEGER,
                source_message_ts REAL,
                source_sender_id TEXT,
                source_context TEXT DEFAULT '[]',
                candidate_type TEXT DEFAULT 'jargon'
            )
        """)
        conn.commit()

        svc = JargonService(type('DB', (), {'conn': conn, 'insert_fact': lambda *a, **k: None})(), llm_client=None, enabled=True, config={"holyman_enabled": True, "holyman_reference_only": True})
        svc._filter = FakeFilter()
        svc._holyman = FakeHolyman()
        svc._inference = None
        svc._msg_count['g1'] = 0
        result = asyncio.run(svc.mine('g1'))

        self.assertEqual(result, [])
        row = conn.execute("SELECT status, scope, source FROM jargon WHERE word='外层词'").fetchone()
        self.assertEqual(row[0], 'pending')
        self.assertEqual(row[1], 'local')
        self.assertEqual(row[2], 'wave_memory')

    def test_wave_memory_db_creates_holyman_knowledge_tables(self):
        from engine.database import WaveMemoryDB

        with tempfile.TemporaryDirectory() as tmp:
            db = WaveMemoryDB(str(Path(tmp) / 'wave.db'))
            try:
                tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertTrue({"jargon_examples", "jargon_concepts", "jargon_candidates", "jargon_blocklist", "jargon_sources"}.issubset(tables))

                db.upsert_jargon_knowledge_snapshot("holyman_skills", {
                    "repo": "ykdeso/holyman-skills",
                    "remote_version": "2026-06-28-abc1234",
                    "local_version": "sync-local",
                    "content_hash": "deadbeef",
                    "asset_status": "ready",
                    "manifest": {"files": []},
                    "quality_report": {"status": "ready"},
                })
                db.replace_jargon_knowledge_table("jargon_candidates", [{"word": "外层词", "reason": "test", "count": 1, "source": "x", "status": "pending_review", "reject_reason": "", "metadata": "{}"}])
                db.replace_jargon_knowledge_table("jargon_blocklist", [{"word": "屏蔽词", "reason": "test", "source": "x"}])
                snapshot = db.conn.execute("SELECT source_key, asset_status, content_hash FROM jargon_sources WHERE source_key='holyman_skills'").fetchone()
                candidate = db.conn.execute("SELECT word, status FROM jargon_candidates WHERE word='外层词'").fetchone()
                blocked = db.conn.execute("SELECT word, reason FROM jargon_blocklist WHERE word='屏蔽词'").fetchone()
                self.assertEqual(snapshot[0], 'holyman_skills')
                self.assertEqual(snapshot[1], 'ready')
                self.assertEqual(snapshot[2], 'deadbeef')
                self.assertEqual(candidate[0], '外层词')
                self.assertEqual(candidate[1], 'pending_review')
                self.assertEqual(blocked[0], '屏蔽词')
                self.assertEqual(blocked[1], 'test')
            finally:
                db.close()

    def test_holyman_api_helpers_normalize_legacy_and_structured_categories(self):
        from webui.blueprints.jargon import _normalize_holyman_phrase, _build_holyman_categories

        legacy = _normalize_holyman_phrase("v我50", "常见荒诞转折梗。")
        structured = _normalize_holyman_phrase("游戏即信仰", {
            "meaning": "游戏不是娱乐，是身份。",
            "category": "gaming",
            "source": "神人.skill/_knowledge/gaming.md",
            "kind": "bold_term",
        })
        categories = _build_holyman_categories([legacy, structured, structured])

        self.assertEqual(legacy["category"], "legacy")
        self.assertEqual(legacy["category_label"], "旧版内置")
        self.assertEqual(structured["category"], "gaming")
        self.assertEqual(structured["category_label"], "游戏文化")
        self.assertEqual(structured["source"], "神人.skill/_knowledge/gaming.md")
        self.assertEqual(categories[0], {"id": "gaming", "label": "游戏文化", "count": 2})
        self.assertIn({"id": "legacy", "label": "旧版内置", "count": 1}, categories)

    def test_holyman_api_exposes_category_filter_payload_and_badges(self):
        from webui.blueprints.jargon import _build_holyman_categories

        items = [
            {"word": "游戏即信仰", "category": "gaming", "category_label": "游戏文化"},
            {"word": "抽象话术", "category": "internet_culture", "category_label": "互联网文化"},
            {"word": "二次元", "category": "gaming", "category_label": "游戏文化"},
        ]
        categories = _build_holyman_categories(items)

        self.assertEqual(categories[0], {"id": "gaming", "label": "游戏文化", "count": 2})
        self.assertIn({"id": "internet_culture", "label": "互联网文化", "count": 1}, categories)
        self.assertTrue(all("category_label" in item for item in items))

    def test_holyman_reference_rejects_document_noise_and_generic_substrings(self):
        from services.jargon.holyman_reference import HolymanReference

        ref = HolymanReference()
        ref._phrases = {
            "背景": "文档标题，不是黑话。",
            "架构": "文档标题，不是黑话。",
            "触发词": "文档标题，不是黑话。",
            "git clone https": "安装命令，不是黑话。",
            "OpenClaw": "项目名，不是黑话。",
            "Claude Code（推荐": "安装说明，不是黑话。",
            "今天": "普通时间词，不是黑话。",
            "v我50": "常见荒诞转折梗。",
            "Ciallo～(∠・ω< )⌒★": "典型二次元问候。",
            "复制粘贴模式": "抽象文化表达模式。",
            "孙笑川/狗粉丝文化": "抽象文化历史阶段。",
        }
        ref._examples = ["深情铺垫最后 v我50", "Ciallo～(∠・ω< )⌒★"]

        for noisy in ["背景", "架构", "触发词", "git clone", "OpenClaw", "Claude Code", "今天吃饭"]:
            result = ref.match(noisy)
            self.assertFalse(result.get("matched"), (noisy, result))

        for useful in ["v我50", "Ciallo", "复制粘贴模式", "狗粉丝"]:
            result = ref.match(useful)
            self.assertTrue(result.get("matched"), (useful, result))

    def test_cleanup_dry_run_does_not_modify_and_apply_marks_legacy_rows(self):
        from scripts.cleanup_legacy_social_data import analyze_database, apply_cleanup

        conn, path = self._connect()
        conn.executescript("""
            CREATE TABLE user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                nickname TEXT,
                affection INTEGER DEFAULT 0,
                interaction_count INTEGER DEFAULT 0,
                first_seen REAL,
                last_seen REAL,
                personality_tags TEXT,
                notes TEXT,
                metadata TEXT,
                bot_id TEXT DEFAULT 'yushu',
                UNIQUE(user_id, group_id, bot_id)
            );
            CREATE TABLE jargon (id INTEGER PRIMARY KEY, word TEXT, meaning TEXT, confidence REAL DEFAULT 0, is_jargon INTEGER, status TEXT);
            CREATE TABLE beliefs (id INTEGER PRIMARY KEY, content TEXT, sources TEXT, status TEXT);
            CREATE TABLE facts (id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, confidence REAL DEFAULT 0.8);
        """)
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, interaction_count, metadata) VALUES ('u1','g1','baizz',50,0,'{}')")
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, interaction_count, metadata) VALUES ('u2','g1','yushu',50,3,'{}')")
        conn.execute("INSERT INTO jargon (word, meaning, confidence, is_jargon, status) VALUES ('肚腩','',0,NULL,NULL)")
        conn.execute("INSERT INTO beliefs (content, sources, status) VALUES ('summary belief','[1,2]','pending')")
        conn.commit()
        conn.close()

        report = analyze_database(str(path))
        self.assertEqual(report["affinity_legacy_neutral"], 1)
        self.assertEqual(report["affinity_legacy_unverified"], 1)
        self.assertEqual(report["jargon_empty_or_unknown"], 1)

        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("SELECT affection FROM user_profiles WHERE user_id='u1'").fetchone()[0], 50)
        conn.close()

        apply_cleanup(str(path), backup=False)
        conn = sqlite3.connect(path)
        neutral = conn.execute("SELECT affection, metadata FROM user_profiles WHERE user_id='u1'").fetchone()
        unverified = conn.execute("SELECT affection, metadata FROM user_profiles WHERE user_id='u2'").fetchone()
        jargon = conn.execute("SELECT status FROM jargon WHERE word='肚腩'").fetchone()[0]
        self.assertEqual(neutral[0], 0)
        self.assertTrue(json.loads(neutral[1])["legacy_neutral"])
        self.assertEqual(unverified[0], 50)
        self.assertTrue(json.loads(unverified[1])["legacy_unverified"])
        self.assertEqual(jargon, "rejected")
        conn.close()

    def test_experience_last_bot_reply_is_scoped_to_current_bot_and_group(self):
        from services.experience_episodes import ExperienceEpisodeService

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.execute("""
            CREATE TABLE experience_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT,
                episode_type TEXT NOT NULL,
                trigger_text TEXT,
                bot_inner_thought TEXT,
                bot_action TEXT,
                bot_reply TEXT,
                user_reaction TEXT,
                outcome TEXT,
                source_memory_ids TEXT DEFAULT '[]',
                emotional_weight REAL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
        svc = ExperienceEpisodeService(conn)
        svc.record_episode(bot_id="baizz", group_id="g1", user_id="u1", episode_type="bot_reply", bot_reply="白真真刚说的话", created_at=20)
        svc.record_episode(bot_id="yushu", group_id="g1", user_id="u1", episode_type="bot_reply", bot_reply="羽书对 u1 说的话", created_at=10)
        svc.record_episode(bot_id="yushu", group_id="g2", user_id="u1", episode_type="bot_reply", bot_reply="羽书其他群的话", created_at=30)

        self.assertEqual(svc.last_bot_reply(bot_id="yushu", group_id="g1", user_id="u1"), "羽书对 u1 说的话")
        self.assertEqual(svc.last_bot_reply(bot_id="baizz", group_id="g1", user_id="u1"), "白真真刚说的话")

    def test_experience_episode_quarantines_identity_contaminated_bot_reply(self):
        from services.experience_episodes import ExperienceEpisodeService

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.execute("""
            CREATE TABLE experience_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                user_id TEXT,
                episode_type TEXT NOT NULL,
                trigger_text TEXT,
                bot_inner_thought TEXT,
                bot_action TEXT,
                bot_reply TEXT,
                user_reaction TEXT,
                outcome TEXT,
                source_memory_ids TEXT DEFAULT '[]',
                emotional_weight REAL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        svc = ExperienceEpisodeService(conn)
        svc.record_episode(
            bot_id="yushu",
            group_id="g1",
            user_id="u1",
            episode_type="bot_reply",
            bot_reply="收到，爸爸。你给了我灵魂。",
            outcome="sent",
            emotional_weight=0.3,
            created_at=10,
        )

        row = conn.execute("SELECT outcome, emotional_weight FROM experience_episodes").fetchone()
        self.assertEqual(tuple(row), ("quarantined_roleplay", 0.0))
        self.assertEqual(svc.last_bot_reply(bot_id="yushu", group_id="g1", user_id="u1"), "")

    def test_affinity_tool_supports_group_and_global_scopes(self):
        import asyncio
        from tools.extra_tools import WaveMemoryAffinityTool

        conn, _ = self._connect()
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                nickname TEXT,
                affection INTEGER DEFAULT 0,
                interaction_count INTEGER DEFAULT 0,
                first_seen REAL,
                last_seen REAL,
                personality_tags TEXT,
                notes TEXT,
                metadata TEXT,
                bot_id TEXT DEFAULT 'yushu',
                UNIQUE(user_id, group_id, bot_id)
            );
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            );
            CREATE TABLE person_registry (
                qq_id TEXT PRIMARY KEY,
                display_name TEXT,
                aliases TEXT,
                message_count INTEGER DEFAULT 0
            );
        """)
        now = time.time()
        conn.execute("INSERT INTO person_registry (qq_id, display_name, aliases) VALUES ('u1','当前群用户','[]')")
        conn.execute("INSERT INTO person_registry (qq_id, display_name, aliases) VALUES ('u2','其他群用户','[]')")
        conn.execute("INSERT INTO person_registry (qq_id, display_name, aliases) VALUES ('2500447291','羽书','[]')")
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, interaction_count, metadata, last_seen) VALUES ('u1','g1','yushu',10,3,'{}',?)", (now,))
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, interaction_count, metadata, last_seen) VALUES ('u2','g2','yushu',90,99,'{}',?)", (now,))
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, interaction_count, metadata, last_seen) VALUES ('u2','g2','baizz',70,77,'{}',?)", (now,))
        conn.execute("INSERT INTO user_profiles (user_id, group_id, bot_id, affection, interaction_count, metadata, last_seen) VALUES ('2500447291','g2','baizz',20,88,?,?)", (json.dumps({"target_type":"bot","target_bot_id":"yushu","target_name":"羽书"}), now))
        conn.execute("INSERT INTO memories (group_id, sender_id, sender_name, content, timestamp) VALUES ('g1','u1','当前群用户','hi',?)", (now,))
        conn.execute("INSERT INTO memories (group_id, sender_id, sender_name, content, timestamp) VALUES ('g2','u2','其他群用户','hi',?)", (now,))
        conn.commit()

        class DB:
            closed = False
            def __init__(self, conn):
                self.conn = conn

        class Event:
            def get_group_id(self): return "g1"
            def get_self_id(self): return "2500447291"

        ctx = _ContextWrapper(context=type("C", (), {"event": Event()})())
        tool = WaveMemoryAffinityTool(db=DB(conn), bot_db_ids={"2500447291": "yushu"})

        ranking_current = asyncio.run(tool.call(ctx, mode="ranking", scope="current_group"))
        self.assertIn("当前群用户", ranking_current)
        self.assertNotIn("其他群用户", ranking_current)

        ranking_global = asyncio.run(tool.call(ctx, mode="ranking", scope="global"))
        self.assertIn("当前群用户", ranking_global)
        self.assertIn("其他群用户", ranking_global)

        active_current = asyncio.run(tool.call(ctx, mode="active", scope="current_group"))
        self.assertIn("当前群用户", active_current)
        self.assertNotIn("其他群用户", active_current)

        active_global = asyncio.run(tool.call(ctx, mode="active", scope="global"))
        self.assertIn("当前群用户", active_global)
        self.assertIn("其他群用户", active_global)

        single_current = asyncio.run(tool.call(ctx, mode="single", user_id="其他群用户", scope="current_group"))
        self.assertIn("没有找到", single_current)

        single_global = asyncio.run(tool.call(ctx, mode="single", user_id="其他群用户", scope="global"))
        self.assertIn("其他群", single_global)

        ranking_specific_group = asyncio.run(tool.call(ctx, mode="ranking", scope="current_group", group_id="g2"))
        self.assertIn("其他群用户", ranking_specific_group)
        self.assertNotIn("当前群用户", ranking_specific_group)

        ranking_all_bots = asyncio.run(tool.call(ctx, mode="ranking", scope="global", bot_scope="all_bots"))
        self.assertIn("yushu", ranking_all_bots)
        self.assertIn("baizz", ranking_all_bots)

        ranking_specific_bot = asyncio.run(tool.call(ctx, mode="ranking", scope="global", bot_id="baizz"))
        self.assertIn("其他群用户", ranking_specific_bot)
        self.assertIn("羽书(bot)", ranking_specific_bot)
        self.assertIn("baizz", ranking_specific_bot)
        self.assertNotIn("当前群用户", ranking_specific_bot)


if __name__ == "__main__":
    unittest.main()
