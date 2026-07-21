from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.observation_idle_check import check_db


class ObservationIdleCheckTest(unittest.TestCase):
    def test_check_db_counts_active_and_soft_deleted(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                quarantine INTEGER,
                memory_type TEXT,
                source TEXT,
                content TEXT,
                group_id TEXT,
                sender_id TEXT,
                bot_id TEXT,
                timestamp REAL
            )"""
        )
        conn.execute(
            "INSERT INTO memories VALUES (1,0,'message','live','a','11111','u1','yushu',1000)"
        )
        conn.execute(
            "INSERT INTO memories VALUES (2,1,'deleted','live','b','11111','u1','yushu',1000)"
        )
        conn.commit()
        stats = check_db(conn)
        self.assertEqual(stats["quick_check"], "ok")
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["active"], 1)
        self.assertEqual(stats["soft_deleted"], 1)
        self.assertEqual(stats["dual_bot_1h"]["buckets"], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
