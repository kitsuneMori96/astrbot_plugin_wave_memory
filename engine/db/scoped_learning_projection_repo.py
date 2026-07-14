"""Scoped FewShot 与 reviewed BookLore projection 的正式仓储。"""

from __future__ import annotations

import hashlib
import json
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


class ScopedFewShotRepository:
    """只读写完整 RuntimeScope 的正式 FewShot 表。"""

    _SELECT = (
        "id, runtime_scope_json, content, score, traits_json, candidate_json, "
        "evidence_refs_json, evidence_bindings_json, status, revision, "
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
        traits_value = tuple(_text(item, "trait") for item in traits or ())
        now = float(self.now())
        scope_key = _scope_key(scope)
        scope_json = _json(_runtime_owner(scope))
        candidate_json = _json(dict(candidate))
        refs_json = _json([item.to_dict() for item in refs])
        bindings_json = _json([item.to_dict() for item in bindings])
        traits_json = _json(list(traits_value))
        mutable = (
            scope_json,
            content,
            float(score),
            traits_json,
            candidate_json,
            refs_json,
            bindings_json,
            "approved",
            source_candidate_id,
        )
        with _repository_write(self.connection, connection) as tx:
            existing = tx.execute(
                """SELECT id, runtime_scope_json, content, score, traits_json, candidate_json,
                          evidence_refs_json, evidence_bindings_json, status, source_candidate_id
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
                               evidence_refs_json=?, evidence_bindings_json=?, status=?,
                               source_candidate_id=?, revision=revision+1, updated_at=?, approved_at=?
                           WHERE id=?""",
                        (*mutable, now, now, example_id),
                    )
                return example_id
            result = tx.execute(
                """INSERT INTO scoped_few_shot_examples
                   (runtime_scope_key, runtime_scope_json, content, score, traits_json,
                    candidate_json, evidence_refs_json, evidence_bindings_json, status,
                    revision, source_candidate_id, idempotency_key, created_at, updated_at, approved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', 1, ?, ?, ?, ?, ?)""",
                (
                    scope_key,
                    scope_json,
                    content,
                    float(score),
                    traits_json,
                    candidate_json,
                    refs_json,
                    bindings_json,
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
            "status": row[8],
            "revision": int(row[9]),
            "source_candidate_id": row[10],
            "idempotency_key": row[11],
            "created_at": float(row[12]),
            "updated_at": float(row[13]),
            "approved_at": float(row[14]),
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
    "ReviewedBookLoreProjectionRepository",
    "ScopedFewShotRepository",
]
