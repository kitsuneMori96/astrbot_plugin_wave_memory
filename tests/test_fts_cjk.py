"""FTS CJK 单字切分回归测试。

unicode61 分词器不切中文，连续汉字成一整个巨型 token；索引与查询两侧
必须做同样的单字切分才能短语匹配。本测试复刻触发器行为做端到端验证。
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.database import WaveMemoryDB  # noqa: E402
from services.injection.channels.fts5 import _match_expr  # noqa: E402


class FtsCjkTests(unittest.TestCase):
    def make_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.create_function("fts_norm", 1, WaveMemoryDB._fts_normalize, deterministic=True)
        conn.executescript(
            """
            CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, sender_name TEXT DEFAULT '', group_id TEXT DEFAULT '');
            CREATE VIRTUAL TABLE fts_memories USING fts5(
                content, sender_name, group_id,
                content='memories', content_rowid='id', tokenize='unicode61'
            );
            CREATE TRIGGER fts_memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO fts_memories(rowid, content, sender_name, group_id)
                VALUES (new.id, fts_norm(new.content), new.sender_name, new.group_id);
            END;
            """
        )
        return conn

    def test_normalize_spaces_every_han_char(self):
        # 行为契约：切分后每个汉字独立成词，英文/数字串保持完整
        n = WaveMemoryDB._fts_normalize
        self.assertEqual(n("生日快乐").split(), ["生", "日", "快", "乐"])
        self.assertEqual(n("galgame").split(), ["galgame"])
        self.assertEqual(n("玩galgame生日").split(), ["玩", "galgame", "生", "日"])
        self.assertEqual(n(None), "")

    def test_match_roundtrip_finds_subword(self):
        conn = self.make_db()
        # 模拟真实写入：原文入库，触发器负责归一化
        conn.execute(
            "INSERT INTO memories (id, content) VALUES (545, ?)",
            ("差点忘了牲的生日？你们俩生日只差两天，他八月十二，你八月十号",),
        )
        conn.execute("INSERT INTO memories (id, content) VALUES (546, (?))", ("今天天气不错",))

        # 关键词「生日」应命中 545 而非 546——旧 unicode61 直查为 0 行的用例
        expr = _match_expr(["生日"])
        rows = conn.execute(
            "SELECT rowid FROM fts_memories WHERE fts_memories MATCH ?", (expr,)
        ).fetchall()
        self.assertEqual([r[0] for r in rows], [545])

        # 多词 OR
        expr2 = _match_expr(["天气", "生日"])
        rows2 = conn.execute(
            "SELECT rowid FROM fts_memories WHERE fts_memories MATCH ?", (expr2,)
        ).fetchall()
        self.assertEqual(sorted(r[0] for r in rows2), [545, 546])
        conn.close()

    def test_match_expr_format(self):
        self.assertEqual(_match_expr(["生日"]), '"生 日"')
        self.assertEqual(_match_expr(["galgame"]), '"galgame"')
        self.assertEqual(_match_expr(["", '  ']), "")


if __name__ == "__main__":
    unittest.main()
