"""Scoped FewShot 与 reviewed BookLore projection 的正式仓储。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Callable

try:
    from ...domain.evidence import EvidenceBinding, EvidenceDerivation, EvidenceRef
    from ...domain.scope import CatalogScope, RuntimeScope, ScopeValidator
except ImportError:  # pragma: no cover - repository tests import engine as top-level
    from domain.evidence import EvidenceBinding, EvidenceDerivation, EvidenceRef
    from domain.scope import CatalogScope, RuntimeScope, ScopeValidator

from .migrations.scoped_learning_projections import ensure_scoped_learning_projection_schema


def _json(value: Any) -> str:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _runtime_owner(scope: RuntimeScope) -> RuntimeScope:
    """群级派生知识由 Bot + canonical session + visibility 归属。

    subject_principal_id 是产生证据的消息主体，不是群级 FewShot / BookLore 的
    分区维度；主体仍完整保留在 EvidenceRef、EvidenceBinding 与 candidate 中。
    """
    if scope.visibility != "group" or scope.session is None or scope.session.kind != "group":
        raise ValueError("a canonical group RuntimeScope is required")
    return RuntimeScope(
        bot_id=scope.bot_id,
        visibility=scope.visibility,
        session=scope.session,
        subject_principal_id=None,
    )


def _same_runtime_owner(left: RuntimeScope, right: RuntimeScope) -> bool:
    return _runtime_owner(left) == _runtime_owner(right)


def _scope_key(scope: RuntimeScope | CatalogScope) -> str:
    canonical = _runtime_owner(scope) if isinstance(scope, RuntimeScope) else scope
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def _text(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _refs(values: Sequence[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    result = tuple(values or ())
    if not result or any(not isinstance(item, EvidenceRef) for item in result):
        raise ValueError("at least one EvidenceRef is required")
    if any(not item.available for item in result):
        raise ValueError("evidence must be available")
    return result


def _bindings(values: Sequence[EvidenceBinding]) -> tuple[EvidenceBinding, ...]:
    result = tuple(values or ())
    if not result or any(not isinstance(item, EvidenceBinding) for item in result):
        raise ValueError("at least one EvidenceBinding is required")
    return result


def _source_tags(values: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for value in values or ():
        if not isinstance(value, Mapping):
            raise TypeError("source tags must be mappings")
        try:
            tag_id = int(value.get("tag_id", value.get("id")))
        except (TypeError, ValueError) as exc:
            raise ValueError("source tag_id must be a positive integer") from exc
        name = _text(value.get("name"), "source tag name")
        if tag_id <= 0:
            raise ValueError("source tag_id must be a positive integer")
        if tag_id in seen:
            continue
        seen.add(tag_id)
        result.append({
            "tag_id": tag_id,
            "name": name,
            "tag_type": str(value.get("tag_type") or "keyword"),
            "position": int(value.get("position") or 0),
            "relevance": float(value.get("relevance") if value.get("relevance") is not None else 1.0),
            "source": str(value.get("source") or "automatic"),
        })
    if not result:
        raise ValueError("at least one source Tag is required")
    return tuple(result)


class FewShotProjectionConflict(ValueError):
    """幂等键被不同 lifecycle 事实占用。"""

    code = "fewshot_projection_conflict"


def _validate_evidence(
    *,
    evidence_refs: tuple[EvidenceRef, ...],
    evidence_bindings: tuple[EvidenceBinding, ...],
    source_scope: RuntimeScope | CatalogScope,
    target_scope: RuntimeScope,
) -> None:
    ref_ids = {item.id for item in evidence_refs}
    if any(item.source_scope != source_scope for item in evidence_refs):
        raise ValueError("EvidenceRef source scope mismatch")
    for binding in evidence_bindings:
        if binding.evidence_id not in ref_ids:
            raise ValueError("EvidenceBinding does not reference supplied evidence")
        if binding.target_scope != target_scope:
            raise ValueError("EvidenceBinding target scope mismatch")


@contextmanager
def _write_transaction(connection):
    factory = getattr(connection, "write_transaction", None)
    if callable(factory):
        with factory() as tx:
            yield tx
        return
    if bool(getattr(connection, "in_transaction", False)):
        raise RuntimeError("connection already has an active transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


@contextmanager
def _repository_write(default_connection, external_connection=None):
    """外部 tx 由 WriteCoordinator 管理；仓储不得提交或回滚它。"""
    if external_connection is not None:
        yield external_connection
        return
    with _write_transaction(default_connection) as tx:
        yield tx


class ScopedBotReplyRepository:
    """从正式 memories/scoped Tag 表解析真实 Bot 回复，不接受自由文本。"""

    _REQUIRED_MEMORY_COLUMNS = frozenset({
        "id", "content", "sender_id", "timestamp", "source", "memory_type",
        "bot_id", "session_id", "visibility", "resolution_state", "quarantine",
        "origin_fingerprint", "provenance",
    })

    def __init__(self, connection):
        self.connection = connection

    def resolve(self, *, scope: RuntimeScope, memory_id: int) -> dict[str, Any]:
        owner = _runtime_owner(scope)
        memory_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(memories)").fetchall()
        }
        missing = self._REQUIRED_MEMORY_COLUMNS - memory_columns
        if missing:
            raise RuntimeError(
                "formal bot reply store is missing columns: " + ", ".join(sorted(missing))
            )
        row = self.connection.execute(
            """SELECT id, content, timestamp, source, memory_type, sender_id,
                      origin_fingerprint, provenance
                 FROM memories
                WHERE id=? AND bot_id=? AND session_id=? AND visibility=?
                  AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0""",
            (int(memory_id), owner.bot_id, owner.session.id, owner.visibility),
        ).fetchone()
        if row is None:
            raise ValueError("scoped bot reply was not found")
        if str(row[5] or "") != "bot" or str(row[4] or "message") != "message":
            raise ValueError("source memory is not a real Bot reply")
        content = _text(row[1], "bot reply content")
        tags = self._tags(scope=owner, memory_id=int(row[0]))
        return {
            "memory_id": int(row[0]),
            "content": content,
            "captured_at": float(row[2] or 0.0),
            "source": str(row[3] or ""),
            "origin_fingerprint": str(row[6] or ""),
            "provenance": _json_dict(row[7]),
            "source_tags": tags,
        }

    def _tags(self, *, scope: RuntimeScope, memory_id: int) -> tuple[dict[str, Any], ...]:
        required = {"scoped_tags", "scoped_memory_tags"}
        tables = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not required <= tables:
            raise RuntimeError("formal scoped Tag store is unavailable")
        tag_columns = {
            str(row[1]) for row in self.connection.execute("PRAGMA table_info(scoped_tags)")
        }
        status_expression = "COALESCE(t.status, 'active')" if "status" in tag_columns else "'active'"
        rows = self.connection.execute(
            f"""SELECT t.id, t.name, t.tag_type, mt.position, mt.relevance, {status_expression}
                    FROM scoped_memory_tags mt
                    JOIN scoped_tags t
                      ON t.id=mt.tag_id AND t.bot_id=mt.bot_id
                     AND t.session_id=mt.session_id AND t.visibility=mt.visibility
                   WHERE mt.bot_id=? AND mt.session_id=? AND mt.visibility=?
                     AND mt.memory_id=?
                   ORDER BY mt.position, t.id""",
            (scope.bot_id, scope.session.id, scope.visibility, int(memory_id)),
        ).fetchall()
        tags = tuple(
            {
                "tag_id": int(row[0]),
                "name": str(row[1]),
                "tag_type": str(row[2] or "keyword"),
                "position": int(row[3] or 0),
                "relevance": float(row[4] if row[4] is not None else 1.0),
                "source": "automatic",
            }
            for row in rows
            if str(row[5] or "active") == "active"
        )
        if not tags:
            raise ValueError("source Bot reply has no active scoped Tags")
        return tags


class ScopedFewShotRepository:
    """只读写完整 RuntimeScope 的正式 FewShot 表。"""

    _SELECT = (
        "id, runtime_scope_json, content, score, traits_json, candidate_json, "
        "evidence_refs_json, evidence_bindings_json, source_tags_json, query_trace_id, "
        "status, revision, usage_count, positive_feedback_count, negative_feedback_count, "
        "last_used_at, retired_at, retirement_reason, retirement_idempotency_key, "
        "source_candidate_id, idempotency_key, created_at, updated_at, approved_at"
    )

    def __init__(
        self,
        connection,
        *,
        now: Callable[[], float] | None = None,
        ensure_schema: bool = True,
    ):
        self.connection = connection
        self.now = now or time.time
        if ensure_schema:
            ensure_scoped_learning_projection_schema(connection)

    def ensure_schema(self, *, connection=None) -> None:
        ensure_scoped_learning_projection_schema(connection or self.connection)

    def write_approved(
        self,
        *,
        scope: RuntimeScope,
        candidate: Mapping[str, Any],
        evidence_refs: Sequence[EvidenceRef],
        evidence_bindings: Sequence[EvidenceBinding],
        source_tags: Sequence[Mapping[str, Any]],
        query_trace_id: str,
        content: str,
        score: float = 0.0,
        traits: Sequence[str] = (),
        source_candidate_id: int | None = None,
        idempotency_key: str,
        connection=None,
    ) -> int:
        if not isinstance(scope, RuntimeScope):
            raise TypeError("scope must be RuntimeScope")
        if not isinstance(candidate, Mapping):
            raise TypeError("candidate must be a mapping")
        refs = _refs(evidence_refs)
        bindings = _bindings(evidence_bindings)
        _validate_evidence(
            evidence_refs=refs,
            evidence_bindings=bindings,
            source_scope=scope,
            target_scope=scope,
        )
        content = _text(content, "content")
        key = _text(idempotency_key, "idempotency_key")
        trace_id = _text(query_trace_id, "query_trace_id")
        tags = _source_tags(source_tags)
        traits_value = tuple(_text(item, "trait") for item in traits or ())
        now = float(self.now())
        scope_key = _scope_key(scope)
        scope_json = _json(_runtime_owner(scope))
        candidate_json = _json(dict(candidate))
        refs_json = _json([item.to_dict() for item in refs])
        bindings_json = _json([item.to_dict() for item in bindings])
        tags_json = _json(list(tags))
        traits_json = _json(list(traits_value))
        mutable = (
            scope_json,
            content,
            float(score),
            traits_json,
            candidate_json,
            refs_json,
            bindings_json,
            tags_json,
            trace_id,
            "approved",
            source_candidate_id,
        )
        with _repository_write(self.connection, connection) as tx:
            existing = tx.execute(
                """SELECT id, runtime_scope_json, content, score, traits_json, candidate_json,
                          evidence_refs_json, evidence_bindings_json, source_tags_json,
                          query_trace_id, status, source_candidate_id
                   FROM scoped_few_shot_examples
                   WHERE runtime_scope_key=? AND idempotency_key=?""",
                (scope_key, key),
            ).fetchone()
            if existing:
                example_id = int(existing[0])
                if tuple(existing[1:]) != mutable:
                    tx.execute(
                        """UPDATE scoped_few_shot_examples
                           SET runtime_scope_json=?, content=?, score=?, traits_json=?, candidate_json=?,
                               evidence_refs_json=?, evidence_bindings_json=?, source_tags_json=?,
                               query_trace_id=?, status=?, source_candidate_id=?, revision=revision+1,
                               updated_at=?, approved_at=?
                           WHERE id=?""",
                        (*mutable, now, now, example_id),
                    )
                return example_id
            result = tx.execute(
                """INSERT INTO scoped_few_shot_examples
                   (runtime_scope_key, runtime_scope_json, content, score, traits_json,
                    candidate_json, evidence_refs_json, evidence_bindings_json, source_tags_json,
                    query_trace_id, status, revision, usage_count, positive_feedback_count,
                    negative_feedback_count, source_candidate_id, idempotency_key, created_at,
                    updated_at, approved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', 1, 0, 0, 0, ?, ?, ?, ?, ?)""",
                (
                    scope_key,
                    scope_json,
                    content,
                    float(score),
                    traits_json,
                    candidate_json,
                    refs_json,
                    bindings_json,
                    tags_json,
                    trace_id,
                    source_candidate_id,
                    key,
                    now,
                    now,
                    now,
                ),
            )
            return int(result.lastrowid)

    def get(self, example_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {self._SELECT} FROM scoped_few_shot_examples WHERE id=?",
            (int(example_id),),
        ).fetchone()
        return self._row(row) if row else None

    def list_approved(
        self,
        *,
        scope: RuntimeScope,
        limit: int = 20,
        offset: int = 0,
        search: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(scope, RuntimeScope):
            raise TypeError("scope must be RuntimeScope")
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        search = str(search or "").strip()
        where = ["runtime_scope_key=?", "status='approved'"]
        params: list[Any] = [_scope_key(scope)]
        if search:
            where.append("(content LIKE ? OR traits_json LIKE ?)")
            params.extend((f"%{search}%", f"%{search}%"))
        rows = self.connection.execute(
            f"""SELECT {self._SELECT} FROM scoped_few_shot_examples
                WHERE {' AND '.join(where)}
                ORDER BY score DESC, updated_at DESC, id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        result = [self._row(row) for row in rows]
        return [item for item in result if _same_runtime_owner(item["scope"], scope)]

    def count_approved(self, *, scope: RuntimeScope, search: str = "") -> int:
        if not isinstance(scope, RuntimeScope):
            raise TypeError("scope must be RuntimeScope")
        search = str(search or "").strip()
        where = ["runtime_scope_key=?", "status='approved'"]
        params: list[Any] = [_scope_key(scope)]
        if search:
            where.append("(content LIKE ? OR traits_json LIKE ?)")
            params.extend((f"%{search}%", f"%{search}%"))
        return int(self.connection.execute(
            f"SELECT COUNT(*) FROM scoped_few_shot_examples WHERE {' AND '.join(where)}",
            params,
        ).fetchone()[0])

    def record_usage(
        self,
        *,
        scope: RuntimeScope,
        example_ids: Sequence[int],
        query_trace_id: str,
        used_at: float | None = None,
    ) -> tuple[dict[str, Any], ...]:
        trace_id = _text(query_trace_id, "query_trace_id")
        ids = tuple(dict.fromkeys(int(value) for value in example_ids or ()))
        if not ids or any(value <= 0 for value in ids):
            raise ValueError("at least one positive example_id is required")
        scope_key = _scope_key(scope)
        timestamp = float(self.now() if used_at is None else used_at)
        with _repository_write(self.connection) as tx:
            for example_id in ids:
                key = f"trace:{trace_id}:example:{example_id}:usage:v1"
                existing_usage = tx.execute(
                    """SELECT 1 FROM scoped_few_shot_usage_events
                         WHERE idempotency_key=? AND example_id=? AND runtime_scope_key=?
                           AND query_trace_id=?""",
                    (key, example_id, scope_key, trace_id),
                ).fetchone()
                if existing_usage:
                    continue
                row = tx.execute(
                    """SELECT status FROM scoped_few_shot_examples
                         WHERE id=? AND runtime_scope_key=?""",
                    (example_id, scope_key),
                ).fetchone()
                if row is None:
                    raise ValueError("FewShot example was not found for RuntimeScope")
                if str(row[0]) != "approved":
                    raise ValueError("retired FewShot example cannot record new usage")
                inserted = tx.execute(
                    """INSERT INTO scoped_few_shot_usage_events
                       (example_id, runtime_scope_key, query_trace_id, idempotency_key, used_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(idempotency_key) DO NOTHING RETURNING id""",
                    (example_id, scope_key, trace_id, key, timestamp),
                ).fetchone()
                if inserted:
                    tx.execute(
                        """UPDATE scoped_few_shot_examples
                           SET usage_count=usage_count+1, last_used_at=?, updated_at=?
                           WHERE id=? AND runtime_scope_key=? AND status='approved'""",
                        (timestamp, timestamp, example_id, scope_key),
                    )
        return tuple(self.get(example_id) for example_id in ids)

    def record_feedback(
        self,
        *,
        scope: RuntimeScope,
        example_id: int,
        query_trace_id: str,
        feedback: str,
        idempotency_key: str,
        created_at: float | None = None,
    ) -> dict[str, Any]:
        trace_id = _text(query_trace_id, "query_trace_id")
        key = _text(idempotency_key, "idempotency_key")
        feedback = str(feedback or "").strip().lower()
        if feedback not in {"useful", "not_useful", "misleading"}:
            raise ValueError("invalid FewShot feedback")
        scope_key = _scope_key(scope)
        timestamp = float(self.now() if created_at is None else created_at)
        with _repository_write(self.connection) as tx:
            row = tx.execute(
                "SELECT status FROM scoped_few_shot_examples WHERE id=? AND runtime_scope_key=?",
                (int(example_id), scope_key),
            ).fetchone()
            if row is None:
                raise ValueError("FewShot example was not found for RuntimeScope")
            existing = tx.execute(
                """SELECT example_id, runtime_scope_key, query_trace_id, feedback
                     FROM scoped_few_shot_feedback_events WHERE idempotency_key=?""",
                (key,),
            ).fetchone()
            expected = (int(example_id), scope_key, trace_id, feedback)
            if existing and tuple(existing) != expected:
                raise FewShotProjectionConflict("feedback idempotency key conflict")
            if existing:
                return self.get(int(example_id))
            trace_feedback = tx.execute(
                """SELECT feedback FROM scoped_few_shot_feedback_events
                     WHERE example_id=? AND runtime_scope_key=? AND query_trace_id=?""",
                (int(example_id), scope_key, trace_id),
            ).fetchone()
            if trace_feedback:
                if str(trace_feedback[0]) != feedback:
                    raise FewShotProjectionConflict("trace already has different FewShot feedback")
                return self.get(int(example_id))
            if str(row[0]) != "approved":
                raise ValueError("retired FewShot example cannot accept new feedback")
            tx.execute(
                """INSERT INTO scoped_few_shot_feedback_events
                   (example_id, runtime_scope_key, query_trace_id, feedback, idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (int(example_id), scope_key, trace_id, feedback, key, timestamp),
            )
            positive = 1 if feedback == "useful" else 0
            negative = 0 if feedback == "useful" else 1
            tx.execute(
                """UPDATE scoped_few_shot_examples
                   SET positive_feedback_count=positive_feedback_count+?,
                       negative_feedback_count=negative_feedback_count+?, updated_at=?
                   WHERE id=? AND runtime_scope_key=? AND status='approved'""",
                (positive, negative, timestamp, int(example_id), scope_key),
            )
        return self.get(int(example_id))

    def retire(
        self,
        *,
        scope: RuntimeScope,
        example_id: int,
        reason: str,
        idempotency_key: str,
        retired_at: float | None = None,
    ) -> dict[str, Any]:
        reason = _text(reason, "retirement reason")
        key = _text(idempotency_key, "retirement idempotency_key")
        scope_key = _scope_key(scope)
        timestamp = float(self.now() if retired_at is None else retired_at)
        with _repository_write(self.connection) as tx:
            row = tx.execute(
                """SELECT status, retirement_reason, retirement_idempotency_key
                     FROM scoped_few_shot_examples WHERE id=? AND runtime_scope_key=?""",
                (int(example_id), scope_key),
            ).fetchone()
            if row is None:
                raise ValueError("FewShot example was not found for RuntimeScope")
            if str(row[0]) == "revoked":
                if str(row[1] or "") != reason or str(row[2] or "") != key:
                    raise FewShotProjectionConflict("FewShot retirement already has another fact")
                return self.get(int(example_id))
            tx.execute(
                """UPDATE scoped_few_shot_examples
                   SET status='revoked', revision=revision+1, retired_at=?, retirement_reason=?,
                       retirement_idempotency_key=?, updated_at=?
                   WHERE id=? AND runtime_scope_key=? AND status='approved'""",
                (timestamp, reason, key, timestamp, int(example_id), scope_key),
            )
        return self.get(int(example_id))

    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "scope": RuntimeScope.from_dict(_json_dict(row[1])),
            "content": row[2],
            "score": float(row[3] or 0.0),
            "traits": tuple(str(item) for item in _json_list(row[4])),
            "candidate": _json_dict(row[5]),
            "evidence_refs": tuple(EvidenceRef.from_dict(item) for item in _json_list(row[6])),
            "evidence_bindings": tuple(
                EvidenceBinding.from_dict(item) for item in _json_list(row[7])
            ),
            "source_tags": tuple(_json_list(row[8])),
            "query_trace_id": row[9],
            "status": row[10],
            "revision": int(row[11]),
            "usage_count": int(row[12] or 0),
            "positive_feedback_count": int(row[13] or 0),
            "negative_feedback_count": int(row[14] or 0),
            "last_used_at": float(row[15]) if row[15] is not None else None,
            "retired_at": float(row[16]) if row[16] is not None else None,
            "retirement_reason": row[17],
            "retirement_idempotency_key": row[18],
            "source_candidate_id": row[19],
            "idempotency_key": row[20],
            "created_at": float(row[21]),
            "updated_at": float(row[22]),
            "approved_at": float(row[23]),
        }


class ReviewedBookLoreProjectionRepository:
    """Catalog raw 只读前提下，持久化经过审核的 Catalog→Runtime projection。"""

    _SELECT = (
        "id, source_catalog_scope_json, target_runtime_scope_json, community_id, "
        "title, summary, content, rank, candidate_json, evidence_refs_json, "
        "evidence_bindings_json, evidence_derivation_json, status, revision, "
        "source_candidate_id, idempotency_key, created_at, updated_at, approved_at"
    )
    _STATUSES = frozenset({"pending", "approved", "rejected", "revoked"})

    def __init__(
        self,
        connection,
        *,
        now: Callable[[], float] | None = None,
        ensure_schema: bool = True,
    ):
        self.connection = connection
        self.now = now or time.time
        if ensure_schema:
            ensure_scoped_learning_projection_schema(connection)

    def ensure_schema(self, *, connection=None) -> None:
        ensure_scoped_learning_projection_schema(connection or self.connection)

    def write_reviewed_projection(
        self,
        *,
        source_scope: CatalogScope,
        target_scope: RuntimeScope,
        candidate: Mapping[str, Any],
        evidence_refs: Sequence[EvidenceRef],
        evidence_bindings: Sequence[EvidenceBinding],
        derivation: EvidenceDerivation,
        community_id: str,
        title: str,
        summary: str,
        content: str,
        rank: float = 0.0,
        status: str = "approved",
        source_candidate_id: int | None = None,
        idempotency_key: str,
        connection=None,
    ) -> int:
        if not isinstance(source_scope, CatalogScope):
            raise TypeError("source_scope must be CatalogScope")
        if not isinstance(target_scope, RuntimeScope):
            raise TypeError("target_scope must be RuntimeScope")
        if not isinstance(candidate, Mapping):
            raise TypeError("candidate must be a mapping")
        if not isinstance(derivation, EvidenceDerivation):
            raise TypeError("derivation must be EvidenceDerivation")
        decision = ScopeValidator().compatibility(
            catalog=source_scope,
            runtime=target_scope,
            evidence_derivation=derivation,
        )
        if not decision.allowed:
            raise ValueError(decision.reason_code or "invalid reviewed derivation")
        refs = _refs(evidence_refs)
        bindings = _bindings(evidence_bindings)
        _validate_evidence(
            evidence_refs=refs,
            evidence_bindings=bindings,
            source_scope=source_scope,
            target_scope=target_scope,
        )
        status = str(status or "").strip().lower()
        if status not in self._STATUSES:
            raise ValueError("invalid projection status")
        community_id = _text(community_id, "community_id")
        title = _text(title, "title")
        summary = _text(summary, "summary")
        content = _text(content, "content")
        key = _text(idempotency_key, "idempotency_key")
        now = float(self.now())
        source_key = _scope_key(source_scope)
        target_key = _scope_key(target_scope)
        source_json = _json(source_scope)
        target_json = _json(_runtime_owner(target_scope))
        candidate_json = _json(dict(candidate))
        refs_json = _json([item.to_dict() for item in refs])
        bindings_json = _json([item.to_dict() for item in bindings])
        derivation_json = _json(derivation)
        approved_at = now if status == "approved" else None
        mutable = (
            source_key,
            source_json,
            target_json,
            community_id,
            title,
            summary,
            content,
            float(rank),
            candidate_json,
            refs_json,
            bindings_json,
            derivation_json,
            status,
            source_candidate_id,
        )
        with _repository_write(self.connection, connection) as tx:
            existing = tx.execute(
                """SELECT id, source_catalog_scope_key, source_catalog_scope_json,
                          target_runtime_scope_json, community_id, title, summary, content, rank,
                          candidate_json, evidence_refs_json, evidence_bindings_json,
                          evidence_derivation_json, status, source_candidate_id
                   FROM reviewed_book_lore_projections
                   WHERE target_runtime_scope_key=? AND idempotency_key=?""",
                (target_key, key),
            ).fetchone()
            if existing:
                projection_id = int(existing[0])
                if tuple(existing[1:]) != mutable:
                    tx.execute(
                        """UPDATE reviewed_book_lore_projections
                           SET source_catalog_scope_key=?, source_catalog_scope_json=?,
                               target_runtime_scope_json=?, community_id=?, title=?, summary=?,
                               content=?, rank=?, candidate_json=?, evidence_refs_json=?,
                               evidence_bindings_json=?, evidence_derivation_json=?, status=?,
                               source_candidate_id=?, revision=revision+1, updated_at=?,
                               approved_at=?
                           WHERE id=?""",
                        (*mutable, now, approved_at, projection_id),
                    )
                return projection_id
            result = tx.execute(
                """INSERT INTO reviewed_book_lore_projections
                   (source_catalog_scope_key, source_catalog_scope_json,
                    target_runtime_scope_key, target_runtime_scope_json, community_id,
                    title, summary, content, rank, candidate_json, evidence_refs_json,
                    evidence_bindings_json, evidence_derivation_json, status, revision,
                    source_candidate_id, idempotency_key, created_at, updated_at, approved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (
                    source_key,
                    source_json,
                    target_key,
                    target_json,
                    community_id,
                    title,
                    summary,
                    content,
                    float(rank),
                    candidate_json,
                    refs_json,
                    bindings_json,
                    derivation_json,
                    status,
                    source_candidate_id,
                    key,
                    now,
                    now,
                    approved_at,
                ),
            )
            return int(result.lastrowid)

    def get(self, projection_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {self._SELECT} FROM reviewed_book_lore_projections WHERE id=?",
            (int(projection_id),),
        ).fetchone()
        return self._row(row) if row else None

    def list_approved(
        self,
        *,
        scope: RuntimeScope,
        limit: int = 20,
        offset: int = 0,
        search: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(scope, RuntimeScope):
            raise TypeError("scope must be RuntimeScope")
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        search = str(search or "").strip()
        where = ["target_runtime_scope_key=?", "status='approved'"]
        params: list[Any] = [_scope_key(scope)]
        if search:
            where.append("(title LIKE ? OR summary LIKE ? OR content LIKE ? OR community_id LIKE ?)")
            params.extend((f"%{search}%",) * 4)
        rows = self.connection.execute(
            f"""SELECT {self._SELECT} FROM reviewed_book_lore_projections
                WHERE {' AND '.join(where)}
                ORDER BY rank DESC, updated_at DESC, id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        result = [self._row(row) for row in rows]
        return [item for item in result if _same_runtime_owner(item["target_scope"], scope)]

    def search_approved(
        self,
        *,
        scope: RuntimeScope,
        query: str,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Return relevant reviewed lore for the exact RuntimeScope.

        Projection rows currently do not carry a shared embedding column, so the
        formal fallback is deterministic token relevance over reviewed content. It
        is intentionally not a top-k dump: an empty match returns no lore.
        """
        query = str(query or "").strip()
        if not query:
            return []
        tokens = [token for token in re.findall(r"[\\w\\u4e00-\\u9fff]{2,}", query.casefold()) if token]
        if not tokens:
            return []
        candidates = self.list_approved(scope=scope, limit=500, offset=0)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in candidates:
            haystack = " ".join(
                str(item.get(field) or "") for field in ("title", "summary", "content", "community_id")
            ).casefold()
            hits = sum(1 for token in tokens if token in haystack)
            if hits <= 0:
                continue
            lexical = hits / max(1, len(tokens))
            score = min(1.0, lexical * 0.75 + min(0.25, float(item.get("rank") or 0.0) * 0.25))
            if score >= float(min_score):
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], float(pair[1].get("rank") or 0.0), int(pair[1].get("id") or 0)), reverse=True)
        return [item for _score, item in ranked[: max(1, min(int(limit), 50))]]

    def count_approved(self, *, scope: RuntimeScope, search: str = "") -> int:
        if not isinstance(scope, RuntimeScope):
            raise TypeError("scope must be RuntimeScope")
        search = str(search or "").strip()
        where = ["target_runtime_scope_key=?", "status='approved'"]
        params: list[Any] = [_scope_key(scope)]
        if search:
            where.append("(title LIKE ? OR summary LIKE ? OR content LIKE ? OR community_id LIKE ?)")
            params.extend((f"%{search}%",) * 4)
        return int(self.connection.execute(
            f"SELECT COUNT(*) FROM reviewed_book_lore_projections WHERE {' AND '.join(where)}",
            params,
        ).fetchone()[0])

    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "source_scope": CatalogScope.from_dict(_json_dict(row[1])),
            "target_scope": RuntimeScope.from_dict(_json_dict(row[2])),
            "community_id": row[3],
            "title": row[4],
            "summary": row[5],
            "content": row[6],
            "rank": float(row[7] or 0.0),
            "candidate": _json_dict(row[8]),
            "evidence_refs": tuple(EvidenceRef.from_dict(item) for item in _json_list(row[9])),
            "evidence_bindings": tuple(
                EvidenceBinding.from_dict(item) for item in _json_list(row[10])
            ),
            "derivation": EvidenceDerivation.from_dict(_json_dict(row[11])),
            "status": row[12],
            "revision": int(row[13]),
            "source_candidate_id": row[14],
            "idempotency_key": row[15],
            "created_at": float(row[16]),
            "updated_at": float(row[17]),
            "approved_at": float(row[18]) if row[18] is not None else None,
        }


class CoordinatorScopedProjectionWriter:
    """把 scoped projection 正式写入派发到 WriteCoordinator 独占事务。"""

    def __init__(
        self,
        coordinator: Any,
        *,
        fewshot_repository: ScopedFewShotRepository | None = None,
        book_lore_repository: ReviewedBookLoreProjectionRepository | None = None,
    ) -> None:
        transaction_blocking = getattr(coordinator, "transaction_blocking", None)
        if not callable(transaction_blocking):
            raise TypeError("coordinator must provide transaction_blocking")
        self.coordinator = coordinator
        self.fewshot_repository = fewshot_repository
        self.book_lore_repository = book_lore_repository

    def migrate(self) -> None:
        """在 writer-owned transaction 内创建两张 projection 表。"""
        self.coordinator.transaction_blocking(ensure_scoped_learning_projection_schema)

    def write_approved(self, **kwargs: Any) -> int:
        if self.fewshot_repository is None:
            raise RuntimeError("scoped FewShot repository is unavailable")
        return int(self.coordinator.transaction_blocking(
            lambda connection: self.fewshot_repository.write_approved(
                connection=connection, **kwargs
            )
        ))

    def write_reviewed_projection(self, **kwargs: Any) -> int:
        if self.book_lore_repository is None:
            raise RuntimeError("reviewed BookLore projection repository is unavailable")
        return int(self.coordinator.transaction_blocking(
            lambda connection: self.book_lore_repository.write_reviewed_projection(
                connection=connection, **kwargs
            )
        ))


__all__ = [
    "CoordinatorScopedProjectionWriter",
    "FewShotProjectionConflict",
    "ReviewedBookLoreProjectionRepository",
    "ScopedBotReplyRepository",
    "ScopedFewShotRepository",
]
