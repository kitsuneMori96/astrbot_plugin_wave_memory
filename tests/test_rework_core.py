import json
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

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
