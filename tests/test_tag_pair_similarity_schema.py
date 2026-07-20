import sqlite3
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TagPairSimilaritySchemaTest(unittest.TestCase):
    def test_legacy_tag_a_table_is_rebuilt_to_canonical_columns(self):
        from engine.db.migrations.tag_pair_similarity_schema import (
            ensure_tag_pair_similarity_schema_connection,
        )

        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE tag_pair_similarity (
                    tag_a INTEGER NOT NULL,
                    tag_b INTEGER NOT NULL,
                    similarity REAL NOT NULL,
                    computed_at REAL,
                    PRIMARY KEY (tag_a, tag_b)
                );
                INSERT INTO tag_pair_similarity(tag_a, tag_b, similarity, computed_at)
                VALUES (1, 2, 0.75, 123.0);
                """
            )
            ensure_tag_pair_similarity_schema_connection(conn)
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(tag_pair_similarity)")
            }
            self.assertEqual(columns, {"tag_id_a", "tag_id_b", "similarity", "updated_at"})
            row = conn.execute(
                "SELECT tag_id_a, tag_id_b, similarity, updated_at FROM tag_pair_similarity"
            ).fetchone()
            self.assertEqual(row, (1, 2, 0.75, 123.0))
        finally:
            conn.close()

    def test_canonical_table_is_left_untouched(self):
        from engine.db.migrations.tag_pair_similarity_schema import (
            ensure_tag_pair_similarity_schema_connection,
        )

        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE tag_pair_similarity (
                    tag_id_a INTEGER NOT NULL,
                    tag_id_b INTEGER NOT NULL,
                    similarity REAL NOT NULL,
                    updated_at REAL,
                    PRIMARY KEY (tag_id_a, tag_id_b)
                );
                INSERT INTO tag_pair_similarity VALUES (3, 4, 0.5, 9.0);
                """
            )
            ensure_tag_pair_similarity_schema_connection(conn)
            count = conn.execute("SELECT COUNT(*) FROM tag_pair_similarity").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
