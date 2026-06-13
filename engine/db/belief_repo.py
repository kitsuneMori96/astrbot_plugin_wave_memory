"""BeliefRepo — 信念系统数据持久化"""

from __future__ import annotations

import json
import time
from typing import Optional

from .connection import ConnectionManager


class BeliefRepo:
    """信念系统的 SQLite 存储层。"""

    def __init__(self, cm: ConnectionManager):
        self.cm = cm
        self._create_tables()

    def _create_tables(self):
        self.cm.executescript("""
            CREATE TABLE IF NOT EXISTS beliefs (
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
                archived_reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_beliefs_bot ON beliefs(bot_id);
            CREATE INDEX IF NOT EXISTS idx_beliefs_type ON beliefs(type);
            CREATE INDEX IF NOT EXISTS idx_beliefs_status ON beliefs(status);
        """)
        self.cm.commit()

    def add_belief(
        self,
        content: str,
        belief_type: str,
        bot_id: str,
        strength: float = 0.5,
        sources: list[int] = None,
        status: str = "active",
    ) -> int:
        """新增信念，返回 ID。status 默认 active；传 'pending' 进入待审。"""
        now = time.time()
        cursor = self.cm.execute_write(
            """INSERT INTO beliefs (content, type, strength, bot_id, sources, status, created_at, last_reinforced)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (content, belief_type, strength, bot_id, json.dumps(sources or []), status, now, now),
        )
        self.cm.commit()
        return cursor.lastrowid

    def get_beliefs(
        self,
        bot_id: str = None,
        belief_type: str = None,
        status: str = "active",
        limit: int = 50,
    ) -> list[dict]:
        """查询信念。"""
        conditions = ["status = ?"]
        params: list = [status]

        if bot_id:
            conditions.append("bot_id = ?")
            params.append(bot_id)
        if belief_type:
            conditions.append("type = ?")
            params.append(belief_type)

        where = " AND ".join(conditions)
        rows = self.cm.conn.execute(
            f"SELECT id, content, type, strength, bot_id, sources, conflicts, status, created_at, last_reinforced, archived_reason "
            f"FROM beliefs WHERE {where} ORDER BY strength DESC LIMIT ?",
            params + [limit],
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def get_belief_by_id(self, belief_id: int) -> Optional[dict]:
        row = self.cm.conn.execute(
            "SELECT id, content, type, strength, bot_id, sources, conflicts, status, created_at, last_reinforced, archived_reason "
            "FROM beliefs WHERE id = ?",
            (belief_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def reinforce(self, belief_id: int, amount: float = 0.05):
        """强化信念。"""
        self.cm.execute_write(
            "UPDATE beliefs SET strength = MIN(1.0, strength + ?), last_reinforced = ? WHERE id = ?",
            (amount, time.time(), belief_id),
        )
        self.cm.commit()

    def weaken(self, belief_id: int, amount: float = 0.1):
        """动摇信念。如果 strength < 0.2 自动标记为 challenged。"""
        self.cm.execute_write(
            "UPDATE beliefs SET strength = MAX(0.0, strength - ?), last_reinforced = ? WHERE id = ?",
            (amount, time.time(), belief_id),
        )
        # 检查是否应标记为 challenged
        row = self.cm.conn.execute("SELECT strength FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
        if row and row[0] < 0.2:
            self.cm.execute_write(
                "UPDATE beliefs SET status = 'challenged' WHERE id = ? AND status = 'active'",
                (belief_id,),
            )
        self.cm.commit()

    def archive(self, belief_id: int, reason: str = ""):
        """归档（推翻）信念。"""
        self.cm.execute_write(
            "UPDATE beliefs SET status = 'archived', archived_reason = ? WHERE id = ?",
            (reason, belief_id),
        )
        self.cm.commit()

    def add_source(self, belief_id: int, memory_id: int):
        """给信念增加一个支撑记忆。"""
        row = self.cm.conn.execute("SELECT sources FROM beliefs WHERE id = ?", (belief_id,)).fetchone()
        if row:
            sources = json.loads(row[0] or "[]")
            if memory_id not in sources:
                sources.append(memory_id)
                # 只保留最近 20 个
                sources = sources[-20:]
                self.cm.execute_write(
                    "UPDATE beliefs SET sources = ? WHERE id = ?",
                    (json.dumps(sources), belief_id),
                )
                self.cm.commit()

    def search_by_content(self, keywords: list[str], bot_id: str = None, limit: int = 5) -> list[dict]:
        """按关键词搜索信念（简单 LIKE 匹配）。"""
        conditions = ["status = 'active'"]
        params: list = []

        if bot_id:
            conditions.append("bot_id = ?")
            params.append(bot_id)

        keyword_conds = []
        for kw in keywords[:5]:  # 最多 5 个关键词
            keyword_conds.append("content LIKE ?")
            params.append(f"%{kw}%")

        if keyword_conds:
            conditions.append(f"({' OR '.join(keyword_conds)})")

        where = " AND ".join(conditions)
        rows = self.cm.conn.execute(
            f"SELECT id, content, type, strength, bot_id, sources, conflicts, status, created_at, last_reinforced, archived_reason "
            f"FROM beliefs WHERE {where} ORDER BY strength DESC LIMIT ?",
            params + [limit],
        ).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def count(self, bot_id: str = None, status: str = "active") -> int:
        if bot_id:
            return self.cm.conn.execute(
                "SELECT COUNT(*) FROM beliefs WHERE bot_id = ? AND status = ?", (bot_id, status)
            ).fetchone()[0]
        return self.cm.conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE status = ?", (status,)
        ).fetchone()[0]

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "content": row[1],
            "type": row[2],
            "strength": row[3],
            "bot_id": row[4],
            "sources": json.loads(row[5] or "[]"),
            "conflicts": json.loads(row[6] or "[]"),
            "status": row[7],
            "created_at": row[8],
            "last_reinforced": row[9],
            "archived_reason": row[10],
        }
