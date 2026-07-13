"""SQLite audit repository for transaction-external quality decisions."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable

try:
    from ...domain.quality import QualityDecision, QualityProposal
except ImportError:  # pragma: no cover - standalone service imports
    from domain.quality import QualityDecision, QualityProposal


class QualityRepository:
    """Persist gate audit records separately from the eventual domain write.

    ``record`` intentionally refuses an already active SQLite transaction.  This
    prevents a proposal decision from accidentally becoming part of the existing
    writer's commit/rollback boundary.
    """

    def __init__(self, connection: sqlite3.Connection, *, now: Callable[[], float] | None = None):
        self.connection = connection
        self.now = now or time.time
        self.migrate(connection)

    @staticmethod
    def migrate(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS quality_decisions (
                proposal_id TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('allow','quarantine','reject','defer')),
                reason_code TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                raw_artifact_json TEXT NOT NULL,
                target_scope_json TEXT,
                normalized_content_hash TEXT NOT NULL,
                decided_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quality_decisions_outcome_time
                ON quality_decisions(outcome, decided_at DESC);
            """
        )

    def record(self, proposal: QualityProposal, decision: QualityDecision) -> QualityDecision:
        if not isinstance(proposal, QualityProposal) or not isinstance(decision, QualityDecision):
            raise TypeError("proposal and decision must use the canonical quality domain types")
        if proposal.proposal_id != decision.proposal_id:
            raise ValueError("quality decision does not belong to proposal")
        if bool(getattr(self.connection, "in_transaction", False)):
            raise RuntimeError("quality decisions must be recorded outside the final writer transaction")

        import hashlib

        normalized_hash = "sha256:" + hashlib.sha256(
            decision.normalized_content.encode("utf-8")
        ).hexdigest()
        proposal_payload = proposal.to_dict()
        values = (
            proposal.proposal_id,
            proposal.operation,
            decision.outcome,
            decision.reason_code,
            json.dumps(decision.reason_codes, ensure_ascii=False, separators=(",", ":")),
            decision.rule_version,
            json.dumps(proposal_payload["raw_artifact"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            None
            if proposal_payload["target_scope"] is None
            else json.dumps(proposal_payload["target_scope"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            normalized_hash,
            float(self.now()),
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            cursor = self.connection.execute(
                """INSERT INTO quality_decisions(
                       proposal_id, operation, outcome, reason_code, reason_codes_json,
                       rule_version, raw_artifact_json, target_scope_json,
                       normalized_content_hash, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(proposal_id) DO NOTHING""",
                values,
            )
            if cursor.rowcount == 0:
                existing = self.connection.execute(
                    "SELECT operation, outcome, reason_code, rule_version, normalized_content_hash, "
                    "target_scope_json FROM quality_decisions WHERE proposal_id=?",
                    (proposal.proposal_id,),
                ).fetchone()
                expected = (
                    proposal.operation,
                    decision.outcome,
                    decision.reason_code,
                    decision.rule_version,
                    normalized_hash,
                    None
                    if proposal_payload["target_scope"] is None
                    else json.dumps(
                        proposal_payload["target_scope"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                if existing is None or tuple(existing) != expected:
                    raise ValueError("quality_decision_conflict")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return decision

    save = record

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT proposal_id, operation, outcome, reason_code, reason_codes_json,
                      rule_version, raw_artifact_json, target_scope_json,
                      normalized_content_hash, decided_at
                 FROM quality_decisions WHERE proposal_id=?""",
            (str(proposal_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "proposal_id": row[0],
            "operation": row[1],
            "outcome": row[2],
            "reason_code": row[3],
            "reason_codes": tuple(json.loads(row[4])),
            "rule_version": row[5],
            "raw_artifact": json.loads(row[6]),
            "target_scope": None if row[7] is None else json.loads(row[7]),
            "normalized_content_hash": row[8],
            "decided_at": float(row[9]),
        }


QualityDecisionRepository = QualityRepository

__all__ = ["QualityDecisionRepository", "QualityRepository"]

