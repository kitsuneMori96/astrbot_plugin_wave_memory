"""Agent-submitted review candidates."""

from __future__ import annotations

import json
import time
from typing import Any

VALID_REVIEW_CANDIDATE_TYPES = frozenset({"memory", "fact", "belief", "style", "jargon"})


class ReviewCandidateStore:
    """SQLite-backed pending review queue for memory-backed candidates."""

    def __init__(self, conn):
        self.conn = conn

    def ensure_schema(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS review_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_type TEXT NOT NULL,
                content TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending',
                promoted INTEGER NOT NULL DEFAULT 0,
                actor TEXT DEFAULT 'agent',
                created_at REAL NOT NULL,
                metadata_json TEXT
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_review_candidates_status ON review_candidates(review_status)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_review_candidates_type ON review_candidates(candidate_type)")
        self.conn.commit()

    def create(
        self,
        *,
        candidate_type: str,
        content: str,
        evidence: list[str],
        reason: str,
        actor: str = "agent",
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> int:
        candidate_type = str(candidate_type or "").strip().lower()
        if candidate_type not in VALID_REVIEW_CANDIDATE_TYPES:
            raise ValueError(f"invalid review candidate type: {candidate_type}")
        if not str(content or "").strip():
            raise ValueError("content is required")
        if not evidence:
            raise ValueError("evidence is required")
        if not str(reason or "").strip():
            raise ValueError("reason is required")
        self.ensure_schema()
        cur = self.conn.execute(
            """INSERT INTO review_candidates
               (candidate_type, content, evidence_json, reason, review_status, promoted, actor, created_at, metadata_json)
               VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
            (
                candidate_type,
                str(content or ""),
                json.dumps(list(evidence), ensure_ascii=False),
                str(reason or ""),
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
            """SELECT id, candidate_type, content, evidence_json, reason, review_status,
                      promoted, actor, created_at, metadata_json
                 FROM review_candidates
                WHERE review_status = 'pending'
                ORDER BY id ASC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [self._row(row) for row in rows]

    def list_all(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        self.ensure_schema()
        if status:
            rows = self.conn.execute(
                """SELECT id, candidate_type, content, evidence_json, reason, review_status,
                          promoted, actor, created_at, metadata_json
                     FROM review_candidates
                    WHERE review_status = ?
                    ORDER BY id DESC LIMIT ?""",
                (str(status), int(limit)),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT id, candidate_type, content, evidence_json, reason, review_status,
                          promoted, actor, created_at, metadata_json
                     FROM review_candidates
                    ORDER BY id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, candidate_id: int) -> dict[str, Any] | None:
        self.ensure_schema()
        row = self.conn.execute(
            """SELECT id, candidate_type, content, evidence_json, reason, review_status,
                      promoted, actor, created_at, metadata_json
                 FROM review_candidates WHERE id = ?""",
            (int(candidate_id),),
        ).fetchone()
        return self._row(row) if row else None

    def update_review_status(self, candidate_id: int, status: str, *, promoted: bool = False) -> dict[str, Any] | None:
        status = str(status or "").strip().lower()
        if status not in {"pending", "approved", "rejected", "ignored"}:
            raise ValueError(f"invalid review status: {status}")
        self.ensure_schema()
        self.conn.execute(
            "UPDATE review_candidates SET review_status = ?, promoted = ? WHERE id = ?",
            (status, 1 if promoted else 0, int(candidate_id)),
        )
        self.conn.commit()
        return self.get(candidate_id)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        try:
            evidence = json.loads(row[3] or "[]")
        except Exception:
            evidence = []
        try:
            metadata = json.loads(row[9] or "{}")
        except Exception:
            metadata = {}
        return {
            "id": row[0],
            "candidate_type": row[1],
            "content": row[2],
            "evidence": evidence,
            "reason": row[4],
            "review_status": row[5],
            "promoted": bool(row[6]),
            "actor": row[7] or "agent",
            "created_at": row[8],
            "metadata": metadata,
        }


__all__ = ["ReviewCandidateStore", "VALID_REVIEW_CANDIDATE_TYPES"]
