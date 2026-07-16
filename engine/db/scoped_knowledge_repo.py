"""RuntimeScope 严格隔离的派生知识仓储。

这是新 ``scoped_*`` 数据面的唯一正式 API。它不会向 legacy 表回退，也不会仅凭
``group_id`` 推断归属；每次读写都需要一个 canonical group ``RuntimeScope``。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from ...domain.scope import RuntimeScope
except ImportError:  # pragma: no cover - repository tests import engine as top-level
    from domain.scope import RuntimeScope

from .connection import ConnectionManager
try:
    from ...services.facts_conflict import FactConflictClassifier
except ImportError:  # pragma: no cover
    from services.facts_conflict import FactConflictClassifier


class ScopedKnowledgeScopeError(ValueError):
    """派生知识 API 的稳定 fail-closed Scope 拒绝。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.reason_code = code
        super().__init__(message or code)


def _require_group_scope(scope: RuntimeScope | None) -> RuntimeScope:
    """接受仅可持久化为 scoped group resource 的完整 RuntimeScope。"""
    if not isinstance(scope, RuntimeScope):
        raise ScopedKnowledgeScopeError(
            "scope_required",
            "a canonical group RuntimeScope is required for scoped derived knowledge",
        )
    if scope.visibility != "group" or scope.session is None or scope.session.kind != "group":
        raise ScopedKnowledgeScopeError(
            "derived_scope_visibility_unsupported",
            "scoped derived knowledge only accepts group RuntimeScope values",
        )
    return scope


def _scope_params(scope: RuntimeScope) -> tuple[str, str, str]:
    # RuntimeScope 已在构造时验证 canonical SessionRef；不要从 group_id 或 caller
    # 提供的裸字符串重建 scope。subject 是消息主体，不是 group 派生对象的归属维度。
    assert scope.session is not None
    return (scope.bot_id, scope.session.id, scope.visibility)


def _require_exact_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty exact string")
    return value


def _canonical_json(value: Mapping[str, Any] | None, field_name: str) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping when provided")
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_contexts(value: Sequence[Any] | None) -> str:
    if value is None:
        return "[]"
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("contexts must be a non-string sequence when provided")
    return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))


class ScopedKnowledgeRepo:
    """读写 scoped_jargon/facts/tags/beliefs/cursors 的 fail-closed 边界。"""

    _MEMORY_SCOPE_COLUMNS = frozenset(
        {"bot_id", "session_id", "visibility", "resolution_state", "quarantine"}
    )

    def __init__(self, cm: ConnectionManager):
        if not isinstance(cm, ConnectionManager):
            raise TypeError("cm must be a ConnectionManager")
        self.cm = cm

    def _require_scoped_memory(self, scope: RuntimeScope, memory_id: int | None) -> None:
        """验证关联 memory 已解析且与目标 Scope 三元组精确一致。

        legacy memories 缺少 v2 scope 列，或任一字段不一致时都拒绝建立派生链接。
        不设置 legacy 外键，避免 schema 层把未解析的历史行伪装成可用证据。
        """
        if memory_id is None:
            return
        if isinstance(memory_id, bool) or not isinstance(memory_id, int) or memory_id <= 0:
            raise ValueError("source_memory_id must be a positive integer when provided")
        columns = {
            row[1] for row in self.cm.execute_read("PRAGMA table_info(memories)").fetchall()
        }
        if not self._MEMORY_SCOPE_COLUMNS <= columns:
            raise ScopedKnowledgeScopeError(
                "memory_scope_schema_missing",
                "memories v2 scope columns are required before linking derived knowledge",
            )
        row = self.cm.execute_read(
            """SELECT id FROM memories
                 WHERE id=? AND bot_id=? AND session_id=? AND visibility=?
                   AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0""",
            (memory_id, *_scope_params(scope)),
        ).fetchone()
        if row is None:
            raise ScopedKnowledgeScopeError(
                "memory_scope_mismatch",
                "source memory is unresolved, quarantined, or belongs to another RuntimeScope",
            )

    def _tag_in_scope(self, scope: RuntimeScope, tag_id: int) -> None:
        if isinstance(tag_id, bool) or not isinstance(tag_id, int) or tag_id <= 0:
            raise ValueError("tag_id must be a positive integer")
        row = self.cm.execute_read(
            """SELECT id FROM scoped_tags
                 WHERE id=? AND bot_id=? AND session_id=? AND visibility=?""",
            (tag_id, *_scope_params(scope)),
        ).fetchone()
        if row is None:
            raise ScopedKnowledgeScopeError("tag_scope_mismatch", "tag does not belong to the RuntimeScope")

    def _select_id(self, table: str, where: str, params: tuple[Any, ...]) -> int:
        row = self.cm.execute_read(f"SELECT id FROM {table} WHERE {where}", params).fetchone()
        if row is None:  # pragma: no cover - guards against unexpected SQLite failures
            raise RuntimeError(f"upsert into {table} did not produce a row")
        return int(row[0])

    def upsert_scoped_jargon(
        self,
        scope: RuntimeScope,
        *,
        word: str,
        meaning: str = "",
        status: str = "pending",
        is_jargon: bool | None = None,
        frequency: int = 0,
        confidence: float = 0.0,
        contexts: Sequence[Any] | None = None,
        source_memory_id: int | None = None,
        source_context: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> int:
        scope = _require_group_scope(scope)
        word = _require_exact_string(word, "word")
        if not isinstance(meaning, str) or not isinstance(status, str):
            raise TypeError("meaning and status must be strings")
        if is_jargon is not None and not isinstance(is_jargon, bool):
            raise TypeError("is_jargon must be bool or None")
        if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency < 0:
            raise ValueError("frequency must be a non-negative integer")
        self._require_scoped_memory(scope, source_memory_id)
        now = time.time()
        self.cm.execute_write(
            """INSERT INTO scoped_jargon (
                    bot_id, session_id, visibility, word, meaning, status, is_jargon,
                    frequency, confidence, contexts, source_memory_id, source_context,
                    provenance, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, session_id, visibility, word) DO UPDATE SET
                    meaning=excluded.meaning, status=excluded.status, is_jargon=excluded.is_jargon,
                    frequency=excluded.frequency, confidence=excluded.confidence,
                    contexts=excluded.contexts, source_memory_id=excluded.source_memory_id,
                    source_context=excluded.source_context, provenance=excluded.provenance,
                    updated_at=excluded.updated_at""",
            (
                *_scope_params(scope), word, meaning, status,
                None if is_jargon is None else int(is_jargon), frequency, float(confidence),
                _canonical_contexts(contexts), source_memory_id, source_context,
                _canonical_json(provenance, "provenance"), now, now,
            ),
        )
        self.cm.commit()
        return self._select_id(
            "scoped_jargon",
            "bot_id=? AND session_id=? AND visibility=? AND word=?",
            (*_scope_params(scope), word),
        )

    def list_scoped_jargon(self, scope: RuntimeScope, *, status: str | None = None, limit: int = 50, include_archived: bool = False) -> list[dict[str, Any]]:
        scope = _require_group_scope(scope)
        if status is not None and not isinstance(status, str):
            raise TypeError("status must be a string when provided")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        conditions = ["bot_id=?", "session_id=?", "visibility=?"]
        params: list[Any] = list(_scope_params(scope))
        if status is not None:
            conditions.append("status=?")
            params.append(status)
        elif not include_archived:
            conditions.append("status!='archived'")
        rows = self.cm.execute_read(
            f"""SELECT id, word, meaning, status, is_jargon, frequency, confidence, contexts,
                       source_memory_id, source_context, provenance, created_at, updated_at
                  FROM scoped_jargon WHERE {' AND '.join(conditions)}
                 ORDER BY updated_at DESC, id DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
        return [
            {
                "id": row[0], "word": row[1], "meaning": row[2], "status": row[3],
                "is_jargon": None if row[4] is None else bool(row[4]), "frequency": row[5],
                "confidence": row[6], "contexts": json.loads(row[7]),
                "source_memory_id": row[8], "source_context": row[9],
                "provenance": json.loads(row[10]), "created_at": row[11], "updated_at": row[12],
            }
            for row in rows
        ]

    def upsert_scoped_fact(
        self,
        scope: RuntimeScope,
        *,
        subject: str,
        predicate: str,
        object: str,
        confidence: float = 0.0,
        status: str = "pending",
        source_memory_id: int | None = None,
        provenance: Mapping[str, Any] | None = None,
        valid_from: float | None = None,
        valid_until: float | None = None,
    ) -> int:
        scope = _require_group_scope(scope)
        subject, predicate, object = (
            _require_exact_string(subject, "subject"),
            _require_exact_string(predicate, "predicate"),
            _require_exact_string(object, "object"),
        )
        if not isinstance(status, str):
            raise TypeError("status must be a string")
        self._require_scoped_memory(scope, source_memory_id)
        now = time.time()
        self.cm.execute_write(
            """INSERT INTO scoped_facts (
                    bot_id, session_id, visibility, subject, predicate, object, confidence, status,
                    source_memory_id, provenance, valid_from, valid_until, created_at, updated_at,
                    revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(bot_id, session_id, visibility, subject, predicate, object) DO UPDATE SET
                    confidence=excluded.confidence, status=excluded.status,
                    source_memory_id=excluded.source_memory_id, provenance=excluded.provenance,
                    valid_from=excluded.valid_from, valid_until=excluded.valid_until,
                    updated_at=excluded.updated_at, revision=scoped_facts.revision+1
                WHERE scoped_facts.status NOT IN ('deleted', 'superseded')""",
            (
                *_scope_params(scope), subject, predicate, object, float(confidence), status,
                source_memory_id, _canonical_json(provenance, "provenance"), valid_from,
                valid_until, now, now,
            ),
        )
        self.cm.commit()
        return self._select_id(
            "scoped_facts",
            "bot_id=? AND session_id=? AND visibility=? AND subject=? AND predicate=? AND object=?",
            (*_scope_params(scope), subject, predicate, object),
        )

    def list_scoped_facts(self, scope: RuntimeScope, *, subject: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        scope = _require_group_scope(scope)
        if subject is not None:
            subject = _require_exact_string(subject, "subject")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        conditions = [
            "bot_id=?", "session_id=?", "visibility=?",
            "status NOT IN ('deleted', 'superseded')",
        ]
        params: list[Any] = list(_scope_params(scope))
        if subject is not None:
            conditions.append("subject=?")
            params.append(subject)
        rows = self.cm.execute_read(
            f"""SELECT id, subject, predicate, object, confidence, status, source_memory_id,
                       provenance, valid_from, valid_until, created_at, updated_at, revision
                  FROM scoped_facts WHERE {' AND '.join(conditions)}
                 ORDER BY updated_at DESC, id DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
        return [
            {
                "id": row[0], "subject": row[1], "predicate": row[2], "object": row[3],
                "confidence": row[4], "status": row[5], "source_memory_id": row[6],
                "provenance": json.loads(row[7]), "valid_from": row[8], "valid_until": row[9],
                "created_at": row[10], "updated_at": row[11], "revision": int(row[12]),
            }
            for row in rows
        ]

    def upsert_scoped_tag(
        self,
        scope: RuntimeScope,
        *,
        name: str,
        tag_type: str = "keyword",
        description: str = "",
        confidence: float = 0.0,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        scope = _require_group_scope(scope)
        name = _require_exact_string(name, "name")
        if not isinstance(tag_type, str) or not isinstance(description, str):
            raise TypeError("tag_type and description must be strings")
        now = time.time()
        self.cm.execute_write(
            """INSERT INTO scoped_tags (
                    bot_id, session_id, visibility, name, tag_type, description, confidence,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, session_id, visibility, name) DO UPDATE SET
                    tag_type=excluded.tag_type, description=excluded.description,
                    confidence=excluded.confidence, metadata=excluded.metadata,
                    updated_at=excluded.updated_at""",
            (*_scope_params(scope), name, tag_type, description, float(confidence),
             _canonical_json(metadata, "metadata"), now, now),
        )
        self.cm.commit()
        return self._select_id(
            "scoped_tags",
            "bot_id=? AND session_id=? AND visibility=? AND name=?",
            (*_scope_params(scope), name),
        )

    def link_scoped_memory_tag(
        self,
        scope: RuntimeScope,
        *,
        memory_id: int,
        tag_id: int,
        position: int = 0,
        relevance: float = 1.0,
    ) -> None:
        scope = _require_group_scope(scope)
        self._require_scoped_memory(scope, memory_id)
        self._tag_in_scope(scope, tag_id)
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            raise ValueError("position must be a non-negative integer")
        self.cm.execute_write(
            """INSERT INTO scoped_memory_tags (
                    bot_id, session_id, visibility, memory_id, tag_id, position, relevance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, session_id, visibility, memory_id, tag_id) DO UPDATE SET
                    position=excluded.position, relevance=excluded.relevance""",
            (*_scope_params(scope), memory_id, tag_id, position, float(relevance), time.time()),
        )
        self.cm.commit()

    def upsert_scoped_tag_relation(
        self,
        scope: RuntimeScope,
        *,
        source_tag_id: int,
        target_tag_id: int,
        relation_type: str,
        weight: float = 1.0,
        confidence: float = 0.0,
        metadata: Mapping[str, Any] | None = None,
        status: str = "active",
        valid_until: float | None = None,
    ) -> int:
        scope = _require_group_scope(scope)
        self._tag_in_scope(scope, source_tag_id)
        self._tag_in_scope(scope, target_tag_id)
        relation_type = _require_exact_string(relation_type, "relation_type")
        if not isinstance(status, str):
            raise TypeError("status must be a string")
        now = time.time()
        self.cm.execute_write(
            """INSERT INTO scoped_tag_relations (
                    bot_id, session_id, visibility, source_tag_id, target_tag_id, relation_type,
                    weight, confidence, metadata, status, valid_until, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(bot_id, session_id, visibility, source_tag_id, target_tag_id, relation_type)
                DO UPDATE SET weight=excluded.weight, confidence=excluded.confidence,
                    metadata=excluded.metadata, status=excluded.status,
                    valid_until=excluded.valid_until, updated_at=excluded.updated_at,
                    revision=scoped_tag_relations.revision+1
                WHERE scoped_tag_relations.status NOT IN ('deleted', 'superseded')""",
            (*_scope_params(scope), source_tag_id, target_tag_id, relation_type, float(weight),
             float(confidence), _canonical_json(metadata, "metadata"), status, valid_until, now, now),
        )
        self.cm.commit()
        return self._select_id(
            "scoped_tag_relations",
            "bot_id=? AND session_id=? AND visibility=? AND source_tag_id=? AND target_tag_id=? AND relation_type=?",
            (*_scope_params(scope), source_tag_id, target_tag_id, relation_type),
        )

    def list_scoped_memory_tags(self, scope: RuntimeScope, memory_ids: Sequence[int]) -> list[dict[str, Any]]:
        scope = _require_group_scope(scope)
        ids = list(memory_ids)
        if not ids:
            return []
        if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids):
            raise ValueError("memory_ids must contain positive integers")
        marks = ','.join('?' for _ in ids)
        rows = self.cm.execute_read(
            f"""SELECT smt.memory_id, smt.tag_id, st.name, st.tag_type, smt.relevance
                FROM scoped_memory_tags smt JOIN scoped_tags st ON st.id=smt.tag_id
                WHERE smt.bot_id=? AND smt.session_id=? AND smt.visibility=? AND smt.memory_id IN ({marks})
                ORDER BY smt.memory_id, smt.position, smt.tag_id""",
            [*_scope_params(scope), *ids],
        ).fetchall()
        return [{"memory_id": r[0], "tag_id": r[1], "name": r[2], "tag_type": r[3], "relevance": r[4]} for r in rows]

    def record_scoped_fact_observation(
        self, scope: RuntimeScope, *, subject: str, predicate: str, object: str,
        confidence: float = 0.0, review_status: str = "pending", status: str | None = None,
        source_memory_id: int | None = None, provenance: Mapping[str, Any] | None = None,
        candidate_snapshot: Mapping[str, Any] | None = None, existing_snapshot: Mapping[str, Any] | None = None,
        evidence: Mapping[str, Any] | None = None, source_tags: Sequence[Any] | None = None,
        query_trace_id: str = "", query_trace: Mapping[str, Any] | None = None,
        valid_from: float | None = None, valid_until: float | None = None,
        idempotency_key: str | None = None, observed_at: float | None = None,
    ) -> int:
        scope = _require_group_scope(scope)
        subject, predicate, object = tuple(_require_exact_string(v, n) for v, n in ((subject, "subject"), (predicate, "predicate"), (object, "object")))
        if review_status not in {"pending", "approved", "rejected"}:
            raise ValueError("invalid review_status")
        self._require_scoped_memory(scope, source_memory_id)
        candidate = {"subject": subject, "predicate": predicate, "object": object, "valid_from": valid_from, "valid_until": valid_until, "provenance": provenance or {}}
        existing_rows = self.list_scoped_facts(scope, subject=subject, limit=500)
        matches = [row for row in existing_rows if row.get("predicate") == predicate] or [None]
        candidate_fact_id = self.upsert_scoped_fact(scope, subject=subject, predicate=predicate, object=object, confidence=confidence, status=status or "pending", source_memory_id=source_memory_id, provenance=provenance, valid_from=valid_from, valid_until=valid_until)
        now = observed_at or time.time()
        first_history_id = None
        classifier = FactConflictClassifier()
        for existing in matches:
            result = classifier.classify(candidate, existing or [])
            existing_id = existing.get("id") if existing else None
            key_base = idempotency_key or f"fact-observation:{source_memory_id or 0}:{subject}\x00{predicate}\x00{object}\x00{valid_from}\x00{valid_until}"
            key = f"{key_base}:existing-{existing_id or 0}"
            self.cm.execute_write(
                """INSERT INTO scoped_fact_history
                (bot_id,session_id,visibility,candidate_fact_id,existing_fact_id,subject,predicate,object,relation,review_status,confidence,candidate_snapshot,existing_snapshot,evidence,source_tags,query_trace_id,source_memory_id,provenance,valid_from,valid_until,supersedes_id,idempotency_key,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(bot_id,session_id,visibility,idempotency_key) DO UPDATE SET evidence=excluded.evidence,source_tags=excluded.source_tags,query_trace_id=excluded.query_trace_id,provenance=excluded.provenance""",
                (*_scope_params(scope), candidate_fact_id, existing_id, subject, predicate, object, result.relation, review_status, float(confidence), _canonical_json(candidate_snapshot or candidate, "candidate_snapshot"), _canonical_json(existing or {}, "existing_snapshot"), _canonical_json(evidence, "evidence"), _canonical_contexts(source_tags), str(query_trace_id or (query_trace or {}).get("trace_id") or ""), source_memory_id, _canonical_json(provenance, "provenance"), valid_from, valid_until, result.existing_id if result.relation == "supersedes" else None, key, now),
            )
            self.cm.commit()
            history_id = self._select_id("scoped_fact_history", "bot_id=? AND session_id=? AND visibility=? AND idempotency_key=?", (*_scope_params(scope), key))
            first_history_id = first_history_id or history_id
        return int(first_history_id or candidate_fact_id)

    def list_scoped_fact_history(self, scope: RuntimeScope, *, subject: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        scope = _require_group_scope(scope); params: list[Any] = list(_scope_params(scope)); where = "bot_id=? AND session_id=? AND visibility=?"
        if subject is not None: where += " AND subject=?"; params.append(_require_exact_string(subject,"subject"))
        rows = self.cm.execute_read(f"SELECT id,subject,predicate,object,relation,review_status,confidence,candidate_snapshot,existing_snapshot,evidence,source_tags,query_trace_id,source_memory_id,provenance,valid_from,valid_until,supersedes_id,idempotency_key,observed_at,reviewed_at FROM scoped_fact_history WHERE {where} ORDER BY observed_at DESC,id DESC LIMIT ?", [*params,limit]).fetchall()
        return [{"id":r[0],"subject":r[1],"predicate":r[2],"object":r[3],"relation":r[4],"review_status":r[5],"confidence":r[6],"candidate_snapshot":json.loads(r[7]),"existing_snapshot":json.loads(r[8]),"evidence":json.loads(r[9]),"source_tags":json.loads(r[10]),"query_trace_id":r[11],"source_memory_id":r[12],"provenance":json.loads(r[13]),"valid_from":r[14],"valid_until":r[15],"supersedes_id":r[16],"idempotency_key":r[17],"observed_at":r[18],"reviewed_at":r[19]} for r in rows]

    def review_scoped_fact_history(self, scope: RuntimeScope, observation_id: int, *, review_status: str, query_trace_id: str = "") -> None:
        scope = _require_group_scope(scope)
        if review_status not in {"pending", "approved", "rejected"}:
            raise ValueError("invalid review_status")
        row = self.cm.execute_read(
            "SELECT candidate_fact_id, existing_fact_id, relation, review_status FROM scoped_fact_history WHERE id=? AND bot_id=? AND session_id=? AND visibility=?",
            (observation_id, *_scope_params(scope)),
        ).fetchone()
        if row is None:
            raise LookupError("scoped_fact_history_not_found")
        if row[3] != "pending" and review_status != "pending":
            raise ValueError("invalid_fact_review_transition")
        candidate_id, existing_id, relation = row[0], row[1], row[2]
        if review_status == "approved" and candidate_id is not None:
            candidate_status = "conflict" if relation == "conflicts" else "active"
            self.cm.execute_write("UPDATE scoped_facts SET status=?, updated_at=? WHERE id=? AND bot_id=? AND session_id=? AND visibility=?", (candidate_status, time.time(), candidate_id, *_scope_params(scope)))
            if relation == "supersedes" and existing_id is not None:
                self.cm.execute_write("UPDATE scoped_facts SET status='superseded', updated_at=? WHERE id=? AND bot_id=? AND session_id=? AND visibility=?", (time.time(), existing_id, *_scope_params(scope)))
        elif review_status == "rejected" and candidate_id is not None:
            self.cm.execute_write("UPDATE scoped_facts SET status='rejected', updated_at=? WHERE id=? AND bot_id=? AND session_id=? AND visibility=?", (time.time(), candidate_id, *_scope_params(scope)))
        self.cm.execute_write("UPDATE scoped_fact_history SET review_status=?, query_trace_id=COALESCE(NULLIF(?, ''), query_trace_id), reviewed_at=? WHERE id=? AND bot_id=? AND session_id=? AND visibility=?", (review_status, str(query_trace_id or ""), time.time(), observation_id, *_scope_params(scope)))
        self.cm.commit()

    def review_scoped_fact_observation(self, scope: RuntimeScope, observation_id: int, *, review_status: str = "pending", status: str | None = None) -> None:
        self.review_scoped_fact_history(scope, observation_id, review_status=review_status if status is None else status)

    def transition_scoped_fact_observation(self, scope: RuntimeScope, observation_id: int, *, relation: str, review_status: str = "pending", status: str | None = None) -> None:
        scope = _require_group_scope(scope)
        if relation not in {"compatible","scoped","conflicts","supersedes"}: raise ValueError("invalid fact relation")
        self.cm.execute_write("UPDATE scoped_fact_history SET relation=?,review_status=? WHERE id=? AND bot_id=? AND session_id=? AND visibility=?",(relation,review_status,observation_id,*_scope_params(scope))); self.cm.commit()

    def upsert_scoped_belief(
        self,
        scope: RuntimeScope,
        *,
        belief_key: str,
        content: str,
        belief_type: str = "world_view",
        strength: float = 0.0,
        status: str = "pending",
        source_memory_id: int | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> int:
        scope = _require_group_scope(scope)
        belief_key = _require_exact_string(belief_key, "belief_key")
        content = _require_exact_string(content, "content")
        if not isinstance(belief_type, str) or not isinstance(status, str):
            raise TypeError("belief_type and status must be strings")
        self._require_scoped_memory(scope, source_memory_id)
        now = time.time()
        self.cm.execute_write(
            """INSERT INTO scoped_beliefs (
                    bot_id, session_id, visibility, belief_key, content, belief_type, strength,
                    status, source_memory_id, provenance, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, session_id, visibility, belief_key) DO UPDATE SET
                    content=excluded.content, belief_type=excluded.belief_type,
                    strength=excluded.strength, status=excluded.status,
                    source_memory_id=excluded.source_memory_id, provenance=excluded.provenance,
                    updated_at=excluded.updated_at""",
            (*_scope_params(scope), belief_key, content, belief_type, float(strength), status,
             source_memory_id, _canonical_json(provenance, "provenance"), now, now),
        )
        self.cm.commit()
        return self._select_id(
            "scoped_beliefs",
            "bot_id=? AND session_id=? AND visibility=? AND belief_key=?",
            (*_scope_params(scope), belief_key),
        )

    def list_scoped_beliefs(self, scope: RuntimeScope, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        scope = _require_group_scope(scope)
        if status is not None and not isinstance(status, str):
            raise TypeError("status must be a string when provided")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        conditions = ["bot_id=?", "session_id=?", "visibility=?"]
        params: list[Any] = list(_scope_params(scope))
        if status is not None:
            conditions.append("status=?")
            params.append(status)
        rows = self.cm.execute_read(
            f"""SELECT id, belief_key, content, belief_type, strength, status, source_memory_id,
                       provenance, created_at, updated_at
                  FROM scoped_beliefs WHERE {' AND '.join(conditions)}
                 ORDER BY updated_at DESC, id DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
        return [
            {
                "id": row[0], "belief_key": row[1], "content": row[2], "belief_type": row[3],
                "strength": row[4], "status": row[5], "source_memory_id": row[6],
                "provenance": json.loads(row[7]), "created_at": row[8], "updated_at": row[9],
            }
            for row in rows
        ]

    def get_scoped_consolidation_cursor(self, scope: RuntimeScope, *, cursor_name: str) -> str | None:
        scope = _require_group_scope(scope)
        cursor_name = _require_exact_string(cursor_name, "cursor_name")
        row = self.cm.execute_read(
            """SELECT cursor_value FROM scoped_consolidation_cursors
                 WHERE bot_id=? AND session_id=? AND visibility=? AND cursor_name=?""",
            (*_scope_params(scope), cursor_name),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def advance_scoped_consolidation_cursor(
        self,
        scope: RuntimeScope,
        *,
        cursor_name: str,
        cursor_value: str,
    ) -> None:
        scope = _require_group_scope(scope)
        cursor_name = _require_exact_string(cursor_name, "cursor_name")
        cursor_value = _require_exact_string(cursor_value, "cursor_value")
        self.cm.execute_write(
            """INSERT INTO scoped_consolidation_cursors (
                    bot_id, session_id, visibility, cursor_name, cursor_value, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, session_id, visibility, cursor_name) DO UPDATE SET
                    cursor_value=excluded.cursor_value, updated_at=excluded.updated_at""",
            (*_scope_params(scope), cursor_name, cursor_value, time.time()),
        )
        self.cm.commit()


__all__ = ["ScopedKnowledgeRepo", "ScopedKnowledgeScopeError"]
