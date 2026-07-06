import sqlite3
import sys
import types
import unittest


if "astrbot.api" not in sys.modules:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")

    class _Logger:
        def debug(self, *args, **kwargs): pass
        def info(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass

    api_mod.logger = _Logger()
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.inserted_facts = []
        self.conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, group_id TEXT, sender_id TEXT, sender_name TEXT, content TEXT, timestamp REAL, memory_type TEXT DEFAULT 'message')")
        self.conn.execute("CREATE TABLE user_profiles (user_id TEXT, group_id TEXT, bot_id TEXT, nickname TEXT)")
        self.conn.execute("CREATE TABLE facts (subject TEXT, predicate TEXT, object TEXT, fact_type TEXT, group_id TEXT)")

    def close(self):
        self.conn.close()

    def insert_fact(self, **kwargs):
        self.inserted_facts.append(kwargs)
        self.conn.execute(
            "INSERT INTO facts (subject, predicate, object, fact_type, group_id) VALUES (?, ?, ?, ?, ?)",
            (kwargs.get("subject"), kwargs.get("predicate"), kwargs.get("obj"), kwargs.get("fact_type"), kwargs.get("group_id")),
        )


class V430JargonCandidateRoutingTest(unittest.TestCase):
    def setUp(self):
        from services.jargon.service import JargonService

        self.db = _DB()
        self.service = JargonService(self.db, enabled=True, config={"min_frequency": 1})

    def tearDown(self):
        self.db.close()

    def test_known_sender_name_routes_to_person_alias_without_llm(self):
        self.db.conn.execute(
            "INSERT INTO memories (group_id, sender_id, sender_name, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            ("g1", "u1", "阿洛", "阿洛 今天来了", 1000.0),
        )

        route = self.service.classify_candidate("阿洛", "g1", {"sender_id": "u1", "sender_name": "阿洛"})

        self.assertEqual(route["candidate_type"], "person_alias")
        self.assertFalse(route["enter_llm"])
        self.assertEqual(route["reject_reason"], "person_alias_diverted")

    def test_known_user_profile_nickname_routes_to_person_alias_without_llm(self):
        self.db.conn.execute(
            "INSERT INTO user_profiles (user_id, group_id, bot_id, nickname) VALUES (?, ?, ?, ?)",
            ("u2", "g1", "yushu", "小羽"),
        )

        route = self.service.classify_candidate("小羽", "g1", {"sender_id": "u2"})

        self.assertEqual(route["candidate_type"], "person_alias")
        self.assertFalse(route["enter_llm"])

    def test_technical_noise_routes_to_audit_without_llm(self):
        route = self.service.classify_candidate("object", "g1", {"sender_id": "u1"})

        self.assertEqual(route["candidate_type"], "technical_noise")
        self.assertFalse(route["enter_llm"])
        self.assertEqual(route["reject_reason"], "technical_noise_filtered")

    def test_ordinary_word_routes_to_audit_without_llm(self):
        route = self.service.classify_candidate("吃饭", "g1", {"sender_id": "u1"})

        self.assertEqual(route["candidate_type"], "ordinary_word")
        self.assertFalse(route["enter_llm"])
        self.assertEqual(route["reject_reason"], "ordinary_word_filtered")

    def test_holyman_phrase_routes_to_reference_hit_without_llm(self):
        route = self.service.classify_candidate("v我50", "g1", {"sender_id": "u1"})

        self.assertEqual(route["candidate_type"], "holyman_reference_hit")
        self.assertFalse(route["enter_llm"])
        self.assertEqual(route["source"], "holyman_skills")
        self.assertTrue(route["reference_only"])
        self.assertIn("理解参考", route["meaning"])

    def test_unknown_candidate_routes_to_local_jargon_candidate_for_llm(self):
        route = self.service.classify_candidate("猫猫税", "g1", {"sender_id": "u1"})

        self.assertEqual(route["candidate_type"], "local_jargon_candidate")
        self.assertTrue(route["enter_llm"])
        self.assertEqual(route["source"], "wave_memory")


if __name__ == "__main__":
    unittest.main()
