import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "astrbot.api" not in sys.modules:
    astrbot_module = types.ModuleType("astrbot")
    api_module = types.ModuleType("astrbot.api")
    api_module.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules["astrbot.api"] = api_module


class TagRepoUpsertTest(unittest.TestCase):
    def test_add_tag_extended_returns_real_id_on_unique_conflict(self):
        from engine.database import WaveMemoryDB

        db = WaveMemoryDB(":memory:", dimension=4)
        try:
            first = db.add_tag_extended("AI生图", tag_type="event", confidence=0.9)
            second = db.add_tag_extended("AI生图", tag_type="event", confidence=0.95)
            self.assertEqual(first, second)
            self.assertGreater(first, 0)

            mid = db.conn.execute(
                "INSERT INTO memories (group_id, content, timestamp, importance, source) "
                "VALUES ('g','c',1,1,'core')"
            ).lastrowid
            if not mid:
                row = db.conn.execute("SELECT id FROM memories LIMIT 1").fetchone()
                mid = row[0] if row else None
            tag_id = db.add_tag_extended("群友调侃", tag_type="event")
            again = db.add_tag_extended("群友调侃", tag_type="topic")
            self.assertEqual(tag_id, again)
            self.assertGreater(tag_id, 0)
            if mid:
                db.conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag_id, position, relevance) "
                    "VALUES (?, ?, ?, 1.0)",
                    (mid, again, 1),
                )
                db.conn.commit()
                linked = db.conn.execute(
                    "SELECT tag_id FROM memory_tags WHERE memory_id=?",
                    (mid,),
                ).fetchone()
                self.assertEqual(linked[0], again)
            freq = db.conn.execute(
                "SELECT frequency FROM tags WHERE id=?",
                (tag_id,),
            ).fetchone()[0]
            self.assertGreaterEqual(int(freq or 0), 2)
        finally:
            db.close()

    def test_add_tag_sees_uncommitted_insert_on_write_connection(self):
        """Regression: read-connection SELECT after uncommitted INSERT must not be used."""
        from engine.database import WaveMemoryDB

        db = WaveMemoryDB(":memory:", dimension=4)
        try:
            # First insert of a brand-new name must succeed even though WAL
            # read snapshots would miss an uncommitted write-connection row.
            tag_id = db.add_tag_extended("全新标签-读写分离回归", tag_type="topic")
            self.assertGreater(tag_id, 0)
            row = db.conn.execute(
                "SELECT id, name FROM tags WHERE id=?",
                (tag_id,),
            ).fetchone()
            self.assertEqual(row[1], "全新标签-读写分离回归")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
