"""注入记忆反馈持久化。"""

from __future__ import annotations

import json
import time
from typing import Any

VALID_MEMORY_FEEDBACK = frozenset({"useful", "useless", "misleading", "duplicate"})


class MemoryFeedbackStore:
    """SQLite-backed memory feedback store.

    只记录反馈信号，不直接删除或提升候选对象；是否软应用由调用方显式决定。
    """

    def __init__(self, conn):
        self.conn = conn

    def ensure_schema(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                memory_id INTEGER NOT NULL,
                feedback TEXT NOT NULL,
                reason TEXT,
                actor TEXT DEFAULT 'agent',
                created_at REAL NOT NULL,
                metadata_json TEXT
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_feedback_trace ON memory_feedback(trace_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_feedback_memory ON memory_feedback(memory_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_feedback_feedback ON memory_feedback(feedback)")
        self.conn.commit()

    def record(
        self,
        *,
        trace_id: str,
        memory_id: int,
        feedback: str,
        reason: str = "",
        actor: str = "agent",
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> int:
        normalized = str(feedback or "").strip().lower()
        if normalized not in VALID_MEMORY_FEEDBACK:
            raise ValueError(f"invalid memory feedback: {feedback}")
        self.ensure_schema()
        cur = self.conn.execute(
            """INSERT INTO memory_feedback
               (trace_id, memory_id, feedback, reason, actor, created_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(trace_id or ""),
                int(memory_id),
                normalized,
                str(reason or ""),
                str(actor or "agent"),
                float(now if now is not None else time.time()),
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_for_trace(self, trace_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        rows = self.conn.execute(
            """SELECT id, trace_id, memory_id, feedback, reason, actor, created_at, metadata_json
                 FROM memory_feedback
                WHERE trace_id = ?
                ORDER BY id ASC""",
            (trace_id,),
        ).fetchall()
        return [self._row(row) for row in rows]

    def list_for_memory(self, memory_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_schema()
        rows = self.conn.execute(
            """SELECT id, trace_id, memory_id, feedback, reason, actor, created_at, metadata_json
                 FROM memory_feedback
                WHERE memory_id = ?
                ORDER BY id DESC LIMIT ?""",
            (int(memory_id), int(limit)),
        ).fetchall()
        return [self._row(row) for row in rows]

    def list_recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_schema()
        rows = self.conn.execute(
            """SELECT id, trace_id, memory_id, feedback, reason, actor, created_at, metadata_json
                 FROM memory_feedback
                ORDER BY id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row) -> dict[str, Any]:
        try:
            metadata = json.loads(row[7] or "{}")
        except Exception:
            metadata = {}
        return {
            "id": row[0],
            "trace_id": row[1],
            "memory_id": row[2],
            "feedback": row[3],
            "reason": row[4] or "",
            "actor": row[5] or "agent",
            "created_at": row[6],
            "metadata": metadata,
        }


__all__ = ["MemoryFeedbackStore", "VALID_MEMORY_FEEDBACK"]
