"""正式 Scoped Soul 仓储，不向 legacy Soul 表回退。"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Callable

try:
    from ...domain.scope import RuntimeScope
except ImportError:  # pragma: no cover
    from domain.scope import RuntimeScope

from .connection import ConnectionManager


_DIMENSION_WEIGHTS = {"familiarity": 0.25, "trust": 0.30, "fun": 0.20, "depth": 0.25}
_DIMENSION_RANGES = {
    "familiarity": (0.0, 100.0),
    "trust": (-50.0, 100.0),
    "fun": (0.0, 80.0),
    "hostility": (0.0, 100.0),
    "depth": (0.0, 80.0),
}


class ScopedSoulScopeError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.reason_code = code
        super().__init__(message or code)


def _require_scope(scope: RuntimeScope | None) -> RuntimeScope:
    if not isinstance(scope, RuntimeScope):
        raise ScopedSoulScopeError("scope_required")
    if scope.visibility != "group" or scope.session is None or scope.session.kind != "group":
        raise ScopedSoulScopeError("soul_scope_visibility_unsupported")
    return scope


def _scope_params(scope: RuntimeScope) -> tuple[str, str, str]:
    assert scope.session is not None
    return scope.bot_id, scope.session.id, scope.visibility


def _exact_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty exact string")
    return value


def _json_mapping(value: Mapping[str, Any] | None) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise TypeError("value must be a mapping")
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_evidence(value: Sequence[Mapping[str, Any]] | None) -> str:
    if value is None:
        return "[]"
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("evidence must be a sequence of mappings")
    items = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError("every evidence item must be a mapping")
        items.append(dict(item))
    return json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _state_for_affinity(affinity: int) -> str:
    if affinity >= 60:
        return "intimate"
    if affinity >= 30:
        return "friendly"
    if affinity >= 0:
        return "neutral"
    if affinity >= -30:
        return "cold"
    return "hostile"


def _compute_affinity(dimensions: Mapping[str, float]) -> int:
    score = sum(float(dimensions.get(key, 0)) * weight for key, weight in _DIMENSION_WEIGHTS.items())
    score -= float(dimensions.get("hostility", 0)) * 0.5
    return int(max(-100, min(100, score)))


class ScopedSoulRepository:
    """以 bot_id + session_id + visibility（关系再加 subject）隔离 Soul。"""

    def __init__(self, cm: ConnectionManager):
        if not isinstance(cm, ConnectionManager):
            raise TypeError("cm must be a ConnectionManager")
        self.cm = cm

    def _write(self, callback: Callable[[Any], Any], connection=None):
        if connection is not None:
            return callback(connection)
        with self.cm.write_transaction() as tx:
            return callback(tx)

    @staticmethod
    def _next_revision(tx, scope: RuntimeScope, component: str, subject: str = "", now: float | None = None) -> int:
        timestamp = float(now or time.time())
        params = (*_scope_params(scope), component, subject)
        tx.execute(
            """INSERT INTO scoped_soul_revisions
                   (bot_id, session_id, visibility, component, subject_principal_id, revision, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(bot_id, session_id, visibility, component, subject_principal_id)
               DO UPDATE SET revision=revision+1, updated_at=excluded.updated_at""",
            (*params, timestamp),
        )
        row = tx.execute(
            """SELECT revision FROM scoped_soul_revisions
               WHERE bot_id=? AND session_id=? AND visibility=?
                 AND component=? AND subject_principal_id=?""",
            params,
        ).fetchone()
        return int(row[0])

    def upsert_mood(
        self,
        scope: RuntimeScope,
        *,
        valence: float,
        arousal: float,
        cause: str = "",
        evidence: Sequence[Mapping[str, Any]] | None = None,
        policy_version: str = "scoped-mood/v1",
        observed_at: float | None = None,
        connection=None,
    ) -> int:
        scope = _require_scope(scope)
        valence = max(-1.0, min(1.0, float(valence)))
        arousal = max(0.0, min(1.0, float(arousal)))
        encoded_evidence = _json_evidence(evidence)
        now = float(observed_at or time.time())

        def persist(tx):
            revision = self._next_revision(tx, scope, "mood", now=now)
            tx.execute(
                """INSERT INTO scoped_soul_mood
                       (bot_id, session_id, visibility, valence, arousal, cause, policy_version,
                        revision, evidence, observed_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bot_id, session_id, visibility) DO UPDATE SET
                       valence=excluded.valence, arousal=excluded.arousal, cause=excluded.cause,
                       policy_version=excluded.policy_version, revision=excluded.revision,
                       evidence=excluded.evidence, observed_at=excluded.observed_at,
                       updated_at=excluded.updated_at""",
                (*_scope_params(scope), valence, arousal, str(cause or ""), str(policy_version),
                 revision, encoded_evidence, now, now),
            )
            return revision

        return int(self._write(persist, connection))

    def replace_concerns(
        self,
        scope: RuntimeScope,
        *,
        concerns: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]] | None = None,
        connection=None,
    ) -> int:
        scope = _require_scope(scope)
        if isinstance(concerns, (str, bytes)) or not isinstance(concerns, Sequence):
            raise TypeError("concerns must be a sequence")
        encoded_default_evidence = _json_evidence(evidence)
        normalized = []
        for item in concerns:
            if not isinstance(item, Mapping):
                raise TypeError("every concern must be a mapping")
            topic = _exact_string(item.get("topic"), "topic")
            intensity = max(0.0, min(1.0, float(item.get("intensity", 0.7))))
            created_at = float(item.get("created_at") or time.time())
            last_triggered = float(item.get("last_triggered") or created_at)
            item_evidence = item.get("evidence")
            normalized.append((
                topic,
                intensity,
                item.get("origin_memory_id") or None,
                created_at,
                last_triggered,
                _json_evidence(item_evidence) if item_evidence is not None else encoded_default_evidence,
            ))

        def persist(tx):
            now = time.time()
            revision = self._next_revision(tx, scope, "concerns", now=now)
            tx.execute(
                "DELETE FROM scoped_soul_concerns WHERE bot_id=? AND session_id=? AND visibility=?",
                _scope_params(scope),
            )
            for row in normalized:
                tx.execute(
                    """INSERT INTO scoped_soul_concerns
                           (bot_id, session_id, visibility, topic, intensity, origin_memory_id,
                            created_at, last_triggered, revision, evidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (*_scope_params(scope), *row[:5], revision, row[5]),
                )
            return revision

        return int(self._write(persist, connection))

    def add_timeline_event(
        self,
        scope: RuntimeScope,
        *,
        event_summary: str,
        emotional_weight: float = 0.5,
        timestamp: float | None = None,
        event_type: str = "time_anchor",
        subject_principal_id: str | None = None,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        connection=None,
    ) -> int:
        scope = _require_scope(scope)
        summary = _exact_string(event_summary, "event_summary")
        subject = subject_principal_id or scope.subject_principal_id
        if subject is not None:
            subject = _exact_string(subject, "subject_principal_id")
        occurred_at = float(timestamp or time.time())
        weight = max(0.0, min(1.0, float(emotional_weight)))
        encoded_evidence = _json_evidence(evidence)

        def persist(tx):
            revision = self._next_revision(tx, scope, "timeline", now=occurred_at)
            cur = tx.execute(
                """INSERT INTO scoped_soul_timeline
                       (bot_id, session_id, visibility, subject_principal_id, event_summary,
                        event_type, emotional_weight, occurred_at, revision, evidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*_scope_params(scope), subject, summary, str(event_type), weight, occurred_at,
                 revision, encoded_evidence, time.time()),
            )
            return int(getattr(cur, "lastrowid", 0) or 0)

        return int(self._write(persist, connection))

    def upsert_relationship(
        self,
        scope: RuntimeScope,
        *,
        subject_principal_id: str,
        affinity: int,
        state: str | None = None,
        dimensions: Mapping[str, Any] | None = None,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        connection=None,
    ) -> int:
        scope = _require_scope(scope)
        subject = _exact_string(subject_principal_id, "subject_principal_id")
        if scope.subject_principal_id is not None and scope.subject_principal_id != subject:
            raise ScopedSoulScopeError("scope_subject_mismatch")
        affinity = int(max(-100, min(100, int(affinity))))
        encoded_dimensions = _json_mapping(dimensions)
        encoded_evidence = _json_evidence(evidence)

        def persist(tx):
            now = time.time()
            revision = self._next_revision(tx, scope, "relationship", subject, now)
            tx.execute(
                """INSERT INTO scoped_soul_relationships
                       (bot_id, session_id, visibility, subject_principal_id, affinity, state,
                        dimensions, revision, evidence, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bot_id, session_id, visibility, subject_principal_id) DO UPDATE SET
                       affinity=excluded.affinity, state=excluded.state,
                       dimensions=excluded.dimensions, revision=excluded.revision,
                       evidence=excluded.evidence, updated_at=excluded.updated_at""",
                (*_scope_params(scope), subject, affinity, state or _state_for_affinity(affinity),
                 encoded_dimensions, revision, encoded_evidence, now),
            )
            return revision

        return int(self._write(persist, connection))

    def record_relationship_event(
        self,
        scope: RuntimeScope,
        *,
        event_type: str,
        dimension: str,
        delta: float,
        reason: str,
        source_episode_id: int | None = None,
        source_memory_id: int | None = None,
        created_at: float | None = None,
        connection=None,
    ) -> dict[str, Any]:
        scope = _require_scope(scope)
        subject = scope.subject_principal_id
        if subject is None:
            raise ScopedSoulScopeError("scope_subject_required")
        if dimension not in _DIMENSION_RANGES:
            raise ValueError(f"invalid relationship dimension: {dimension}")
        reason = _exact_string(reason, "reason")
        requested_delta = float(delta)
        now = float(created_at or time.time())

        def persist(tx):
            row = tx.execute(
                """SELECT affinity, dimensions FROM scoped_soul_relationships
                   WHERE bot_id=? AND session_id=? AND visibility=? AND subject_principal_id=?""",
                (*_scope_params(scope), subject),
            ).fetchone()
            before = int(row[0] or 0) if row else 0
            dimensions = json.loads(row[1]) if row and row[1] else {}
            for key in _DIMENSION_RANGES:
                dimensions.setdefault(key, 0.0)
            lo, hi = _DIMENSION_RANGES[dimension]
            dimensions[dimension] = max(lo, min(hi, float(dimensions[dimension]) + requested_delta))
            after = _compute_affinity(dimensions)
            revision = self._next_revision(tx, scope, "relationship", subject, now)
            cur = tx.execute(
                """INSERT INTO scoped_soul_relationship_events
                       (bot_id, session_id, visibility, subject_principal_id, event_type,
                        dimension, delta, reason, source_episode_id, source_memory_id,
                        revision, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*_scope_params(scope), subject, str(event_type), dimension, requested_delta,
                 reason, source_episode_id, source_memory_id, revision, now),
            )
            event_id = int(getattr(cur, "lastrowid", 0) or 0)
            evidence = [{"relationship_event_id": event_id}]
            if source_episode_id is not None:
                evidence.append({"episode_id": source_episode_id})
            if source_memory_id is not None:
                evidence.append({"memory_id": source_memory_id})
            tx.execute(
                """INSERT INTO scoped_soul_relationships
                       (bot_id, session_id, visibility, subject_principal_id, affinity, state,
                        dimensions, revision, evidence, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bot_id, session_id, visibility, subject_principal_id) DO UPDATE SET
                       affinity=excluded.affinity, state=excluded.state,
                       dimensions=excluded.dimensions, revision=excluded.revision,
                       evidence=excluded.evidence, updated_at=excluded.updated_at""",
                (*_scope_params(scope), subject, after, _state_for_affinity(after),
                 _json_mapping(dimensions), revision, _json_evidence(evidence), now),
            )
            return {
                "event_id": event_id,
                "dimension": dimension,
                "requested_delta": requested_delta,
                "applied_delta": requested_delta,
                "before_affinity": before,
                "after_affinity": after,
                "reason": reason,
            }

        return dict(self._write(persist, connection))

    def get_state(
        self,
        scope: RuntimeScope,
        *,
        subject_principal_id: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        scope = _require_scope(scope)
        if limit not in {25, 50, 100} or offset < 0:
            raise ValueError("invalid_pagination")
        subject = subject_principal_id or scope.subject_principal_id
        if subject is not None:
            subject = _exact_string(subject, "subject_principal_id")
            if scope.subject_principal_id is not None and scope.subject_principal_id != subject:
                raise ScopedSoulScopeError("scope_subject_mismatch")
        params = _scope_params(scope)
        mood_row = self.cm.execute_read(
            """SELECT valence, arousal, cause, policy_version, revision, evidence, observed_at
               FROM scoped_soul_mood WHERE bot_id=? AND session_id=? AND visibility=?""",
            params,
        ).fetchone()
        if mood_row:
            mood = {
                "value": mood_row[0],
                "state": "known",
                "components": {"valence": mood_row[0], "arousal": mood_row[1]},
                "cause": mood_row[2],
                "policy_version": mood_row[3],
                "revision": mood_row[4],
                "evidence": json.loads(mood_row[5]),
                "observed_at": mood_row[6],
            }
        else:
            mood = {"value": None, "state": "unknown", "components": None,
                    "policy_version": None, "revision": None, "evidence": []}

        concern_total = int(self.cm.execute_read(
            "SELECT COUNT(*) FROM scoped_soul_concerns WHERE bot_id=? AND session_id=? AND visibility=?",
            params,
        ).fetchone()[0])
        concern_rows = self.cm.execute_read(
            """SELECT id, topic, intensity, origin_memory_id, created_at, last_triggered,
                      revision, evidence
               FROM scoped_soul_concerns
               WHERE bot_id=? AND session_id=? AND visibility=?
               ORDER BY intensity DESC, last_triggered DESC, id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        concerns = [{
            "id": row[0], "topic": row[1], "intensity": row[2],
            "origin_memory_id": row[3], "created_at": row[4], "last_triggered": row[5],
            "revision": row[6], "evidence": json.loads(row[7]),
        } for row in concern_rows]

        timeline_total = int(self.cm.execute_read(
            "SELECT COUNT(*) FROM scoped_soul_timeline WHERE bot_id=? AND session_id=? AND visibility=?",
            params,
        ).fetchone()[0])
        timeline_rows = self.cm.execute_read(
            """SELECT id, subject_principal_id, event_summary, event_type, emotional_weight,
                      occurred_at, revision, evidence
               FROM scoped_soul_timeline
               WHERE bot_id=? AND session_id=? AND visibility=?
               ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        timeline = [{
            "id": row[0], "subject_principal_id": row[1], "event_summary": row[2],
            "event_type": row[3], "emotional_weight": row[4], "timestamp": row[5],
            "revision": row[6], "evidence": json.loads(row[7]),
        } for row in timeline_rows]

        relationship = {"affinity": None, "state": "unknown", "revision": None,
                        "evidence": [], "people_ref": subject, "dimensions": None}
        if subject is not None:
            row = self.cm.execute_read(
                """SELECT affinity, state, dimensions, revision, evidence, updated_at
                   FROM scoped_soul_relationships
                   WHERE bot_id=? AND session_id=? AND visibility=? AND subject_principal_id=?""",
                (*params, subject),
            ).fetchone()
            if row:
                relationship = {
                    "affinity": row[0], "state": row[1], "dimensions": json.loads(row[2]),
                    "revision": row[3], "evidence": json.loads(row[4]),
                    "updated_at": row[5], "people_ref": subject,
                }

        revision_params: list[Any] = list(params)
        revision_where = "bot_id=? AND session_id=? AND visibility=? AND subject_principal_id=''"
        if subject is not None:
            revision_where = "bot_id=? AND session_id=? AND visibility=? AND (subject_principal_id='' OR subject_principal_id=?)"
            revision_params.append(subject)
        revision_row = self.cm.execute_read(
            f"SELECT COALESCE(MAX(revision), 0) FROM scoped_soul_revisions WHERE {revision_where}",
            revision_params,
        ).fetchone()
        revision = int(revision_row[0] or 0)
        aggregate_evidence = []
        for component in (mood, relationship, *concerns, *timeline):
            for item in component.get("evidence", []):
                if item not in aggregate_evidence:
                    aggregate_evidence.append(item)
        return {
            "revision": revision,
            "evidence": aggregate_evidence,
            "mood": mood,
            "concerns": {"items": concerns, "total": concern_total, "revision": max((item["revision"] for item in concerns), default=None)},
            "timeline": {"items": timeline, "total": timeline_total, "revision": max((item["revision"] for item in timeline), default=None)},
            "relationship": relationship,
        }


__all__ = ["ScopedSoulRepository", "ScopedSoulScopeError"]
