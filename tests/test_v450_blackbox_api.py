import sqlite3


def _seed_blackbox_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE book_entities (id INTEGER PRIMARY KEY, name TEXT, summary TEXT, source_book TEXT)")
    conn.execute("CREATE TABLE book_relations (id INTEGER PRIMARY KEY, source TEXT, target TEXT, relation TEXT)")
    conn.execute("CREATE TABLE book_communities (id INTEGER PRIMARY KEY, title TEXT, summary TEXT)")
    conn.execute("CREATE TABLE book_notes (id INTEGER PRIMARY KEY, title TEXT, content TEXT)")
    conn.execute("INSERT INTO book_entities (id, name, summary, source_book) VALUES (1, '羽书', '角色', 'demo')")
    conn.execute("INSERT INTO book_relations (id, source, target, relation) VALUES (1, '羽书', '世界', 'belongs')")
    conn.execute("INSERT INTO book_communities (id, title, summary) VALUES (1, '主社区', '摘要')")
    conn.execute("INSERT INTO book_notes (id, title, content) VALUES (1, '设定', '内容')")

    conn.execute("CREATE TABLE few_shot_examples (id INTEGER PRIMARY KEY, content TEXT, score REAL, traits TEXT, status TEXT, bot_id TEXT, created_at REAL, approved_at REAL)")
    conn.execute("INSERT INTO few_shot_examples (id, content, score, traits, status, bot_id, created_at) VALUES (1, '范例', 0.8, '温柔', 'pending', 'yushu', 1)")
    conn.execute("INSERT INTO few_shot_examples (id, content, score, traits, status, bot_id, created_at) VALUES (2, '已批', 0.9, '吐槽', 'approved', 'yushu', 2)")

    conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, fact_type TEXT, confidence REAL, source_memory_id INTEGER)")
    conn.execute("INSERT INTO facts (id, subject, predicate, object, fact_type, confidence, source_memory_id) VALUES (1, 'A', 'likes', 'B', 'relation', 0.7, 10)")

    conn.execute("CREATE TABLE person_registry (qq_id TEXT PRIMARY KEY, display_name TEXT, aliases TEXT, message_count INTEGER, first_seen REAL, last_seen REAL)")
    conn.execute("CREATE TABLE user_profiles (id INTEGER PRIMARY KEY, user_id TEXT, group_id TEXT, bot_id TEXT, nickname TEXT, affection REAL, interaction_count INTEGER, last_seen REAL, metadata TEXT)")
    conn.execute("INSERT INTO person_registry (qq_id, display_name, aliases, message_count) VALUES ('1001', '小羽', '羽书', 5)")
    conn.execute("INSERT INTO user_profiles (id, user_id, group_id, bot_id, nickname, affection, interaction_count) VALUES (1, '1001', '2001', 'yushu', '小羽', 0.6, 8)")

    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, vector BLOB)")
    conn.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, vector BLOB)")
    conn.execute("CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER)")
    conn.execute("INSERT INTO memories (id, content, vector) VALUES (1, '有向量', X'00')")
    conn.execute("INSERT INTO memories (id, content, vector) VALUES (2, '缺向量', NULL)")
    conn.execute("INSERT INTO tags (id, name, vector) VALUES (1, 'tag', NULL)")
    conn.commit()
    return conn


class TestV450BlackboxApi:
    def test_blackbox_blueprint_is_registered(self):
        registry = open("webui/blueprints/__init__.py", encoding="utf-8").read()
        assert "blackbox_bp" in registry
        assert "from .blackbox import blackbox_bp" in registry

    def test_blackbox_readonly_helpers_return_summary_and_lists(self):
        from webui.blueprints.blackbox import (
            build_book_lore_summary,
            build_facts_payload,
            build_fewshot_summary,
            build_indexes_summary,
            build_people_payload,
        )

        conn = _seed_blackbox_db()
        try:
            book_lore = build_book_lore_summary(conn)
            assert book_lore["counts"]["entities"] == 1
            assert book_lore["counts"]["relations"] == 1
            assert book_lore["counts"]["communities"] == 1
            assert book_lore["counts"]["notes"] == 1
            assert book_lore["readonly"] is True
            assert book_lore["route_prefix"] == "/api/blackbox/book-lore"

            fewshot = build_fewshot_summary(conn)
            assert fewshot["counts"]["pending"] == 1
            assert fewshot["counts"]["approved"] == 1
            assert fewshot["average_score"] == 0.85
            assert fewshot["readonly"] is True

            facts = build_facts_payload(conn, limit=10, offset=0, search="A")
            assert facts["total"] == 1
            assert facts["items"][0]["subject"] == "A"
            assert facts["limit"] == 10
            assert facts["offset"] == 0

            people = build_people_payload(conn, limit=10, offset=0, search="小羽")
            assert people["total"] == 1
            assert people["items"][0]["qq_id"] == "1001"
            assert people["items"][0]["bot_id"] == "yushu"

            indexes = build_indexes_summary(conn)
            assert indexes["counts"]["memories"] == 2
            assert indexes["counts"]["memories_missing_vector"] == 1
            assert indexes["counts"]["tags_missing_vector"] == 1
            assert indexes["readonly"] is True
            assert indexes["dangerous_operations_require_preview"] is True
        finally:
            conn.close()

    def test_blackbox_blueprint_declares_required_routes(self):
        source = open("webui/blueprints/blackbox.py", encoding="utf-8").read()
        for marker in (
            "Blueprint(\"blackbox\"",
            "url_prefix=\"/api/blackbox\"",
            "@blackbox_bp.route(\"/book-lore/summary\"",
            "@blackbox_bp.route(\"/book-lore/entities\"",
            "@blackbox_bp.route(\"/book-lore/communities\"",
            "@blackbox_bp.route(\"/book-lore/relations\"",
            "@blackbox_bp.route(\"/book-lore/notes\"",
            "@blackbox_bp.route(\"/fewshot/summary\"",
            "@blackbox_bp.route(\"/fewshot/examples\"",
            "@blackbox_bp.route(\"/facts\"",
            "@blackbox_bp.route(\"/facts/<int:fact_id>\"",
            "@blackbox_bp.route(\"/people\"",
            "@blackbox_bp.route(\"/people/<person_id>\"",
            "@blackbox_bp.route(\"/indexes/summary\"",
            "@blackbox_bp.route(\"/indexes/check\"",
            "limit",
            "offset",
            "search",
            "sort",
            "filter",
        ):
            assert marker in source
