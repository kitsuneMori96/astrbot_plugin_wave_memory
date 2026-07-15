"""Agent 配置建议待审存储。"""

from __future__ import annotations

import json
import time
from typing import Any

VALID_SUGGESTION_SCOPES = frozenset({"channel", "global"})
VALID_CONFIG_PROBLEMS = frozenset({"noise", "slow", "too_much", "too_little", "wrong_source"})


class ConfigSuggestionStore:
    """SQLite-backed pending review queue for Agent config suggestions."""

    def __init__(self, conn):
        self.conn = conn

    def ensure_schema(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS config_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                channel TEXT,
                problem TEXT NOT NULL,
                suggestion TEXT,
                evidence_trace_ids_json TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending',
                applied INTEGER NOT NULL DEFAULT 0,
                actor TEXT DEFAULT 'agent',
                created_at REAL NOT NULL,
                metadata_json TEXT
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_config_suggestions_status ON config_suggestions(review_status)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_config_suggestions_channel ON config_suggestions(channel)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_config_suggestions_problem ON config_suggestions(problem)")
        self.conn.commit()

    def create(
        self,
        *,
        scope: str,
        channel: str = "",
        problem: str,
        suggestion: str = "",
        evidence_trace_ids: list[str],
        actor: str = "agent",
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> int:
        scope = str(scope or "").strip().lower()
        problem = str(problem or "").strip().lower()
        if scope not in VALID_SUGGESTION_SCOPES:
            raise ValueError(f"invalid suggestion scope: {scope}")
        if problem not in VALID_CONFIG_PROBLEMS:
            raise ValueError(f"invalid config problem: {problem}")
        if not evidence_trace_ids:
            raise ValueError("evidence_trace_ids is required")
        self.ensure_schema()
        cur = self.conn.execute(
            """INSERT INTO config_suggestions
               (scope, channel, problem, suggestion, evidence_trace_ids_json,
                review_status, applied, actor, created_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
            (
                scope,
                str(channel or ""),
                problem,
                str(suggestion or ""),
                json.dumps(list(evidence_trace_ids), ensure_ascii=False),
                str(actor or "agent"),
                float(now if now is not None else time.time()),
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_schema()
        rows = self.conn.execute(
            """SELECT id, scope, channel, problem, suggestion, evidence_trace_ids_json,
                      review_status, applied, actor, created_at, metadata_json
                 FROM config_suggestions
                WHERE review_status = 'pending'
                ORDER BY id ASC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [self._row(row) for row in rows]

    def list_all(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        self.ensure_schema()
        if status:
            rows = self.conn.execute(
                """SELECT id, scope, channel, problem, suggestion, evidence_trace_ids_json,
                          review_status, applied, actor, created_at, metadata_json
                     FROM config_suggestions
                    WHERE review_status = ?
                    ORDER BY id DESC LIMIT ?""",
                (str(status), int(limit)),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT id, scope, channel, problem, suggestion, evidence_trace_ids_json,
                          review_status, applied, actor, created_at, metadata_json
                     FROM config_suggestions
                    ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, suggestion_id: int) -> dict[str, Any] | None:
        self.ensure_schema()
        row = self.conn.execute(
            """SELECT id, scope, channel, problem, suggestion, evidence_trace_ids_json,
                      review_status, applied, actor, created_at, metadata_json
                 FROM config_suggestions WHERE id = ?""",
            (int(suggestion_id),),
        ).fetchone()
        return self._row(row) if row else None

    def update_review_status(self, suggestion_id: int, status: str, *, applied: bool = False) -> dict[str, Any] | None:
        status = str(status or "").strip().lower()
        if status not in {"pending", "approved", "rejected", "ignored"}:
            raise ValueError(f"invalid review status: {status}")
        self.ensure_schema()
        self.conn.execute(
            "UPDATE config_suggestions SET review_status = ?, applied = ? WHERE id = ?",
            (status, 1 if applied else 0, int(suggestion_id)),
        )
        self.conn.commit()
        return self.get(suggestion_id)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        try:
            evidence = json.loads(row[5] or "[]")
        except Exception:
            evidence = []
        try:
            metadata = json.loads(row[10] or "{}")
        except Exception:
            metadata = {}
        return {
            "id": row[0],
            "scope": row[1],
            "channel": row[2] or "",
            "problem": row[3],
            "suggestion": row[4] or "",
            "evidence_trace_ids": evidence,
            "review_status": row[6],
            "applied": bool(row[7]),
            "actor": row[8] or "agent",
            "created_at": row[9],
            "metadata": metadata,
        }


__all__ = ["ConfigSuggestionStore", "VALID_CONFIG_PROBLEMS", "VALID_SUGGESTION_SCOPES"]
