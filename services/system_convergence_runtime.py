"""Production write gateway for Stage 1 coordinated Memory/Tag mutations."""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

try:
    from ..domain.commands import DomainCommand, EntityChange
    from ..domain.quality import QualityDecision, QualityProposal
    from ..domain.scope import RuntimeScope
    from ..engine.db.migrations.memories_v2 import MEMORIES_V2_VERSION
    from ..engine.write_coordinator import MutationOutcome, OutboxEventDraft, WriteCoordinator
    from .outbox_dispatcher import OutboxDispatcher
    from .durable_jobs import DurableJobService
except ImportError:  # pragma: no cover - focused repository tests import top-level packages
    from domain.commands import DomainCommand, EntityChange
    from domain.quality import QualityDecision, QualityProposal
    from domain.scope import RuntimeScope
    from engine.db.migrations.memories_v2 import MEMORIES_V2_VERSION
    from engine.write_coordinator import MutationOutcome, OutboxEventDraft, WriteCoordinator
    from services.outbox_dispatcher import OutboxDispatcher
    from services.durable_jobs import DurableJobService


_APPEND_MEMORY = "memory.append.v1"
_BACKFILL_MEMORY_VECTOR = "memory.vector_backfill.v1"
_APPLY_TAG_EXTRACTION = "tag_extraction.apply.v1"
_MUTATE_MEMORIES = "memory.mutate.v1"


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_memory_scope(scope: RuntimeScope) -> RuntimeScope:
    if (
        not isinstance(scope, RuntimeScope)
        or scope.visibility not in {"group", "private"}
        or scope.session is None
    ):
        raise ValueError("a canonical group/private RuntimeScope is required")
    return scope


def _require_group_scope(scope: RuntimeScope) -> RuntimeScope:
    if not isinstance(scope, RuntimeScope) or scope.visibility != "group" or scope.session is None:
        raise ValueError("a canonical group RuntimeScope is required")
    return scope


def _scope_tuple(scope: RuntimeScope) -> tuple[str, str, str]:
    scope = _require_memory_scope(scope)
    assert scope.session is not None
    return scope.bot_id, scope.session.id, scope.visibility


def _append_memory_handler(connection, command: DomainCommand, now: float) -> MutationOutcome:
    scope = _require_memory_scope(command.scope)
    assert scope.session is not None
    payload = command.payload
    group_id = str(payload["group_id"])
    if group_id != scope.session.conversation_id:
        raise ValueError("group_id does not match RuntimeScope")

    provenance = dict(payload.get("provenance") or {})
    origin_metadata = dict(payload.get("origin_metadata") or {})
    timestamp = float(payload.get("timestamp") or now)
    source = str(payload.get("source") or "live")
    content = str(payload.get("content") or "")
    sender_id = str(payload.get("sender_id") or "")
    sender_name = str(payload.get("sender_name") or "")
    scope_payload = {
        "bot_id": scope.bot_id,
        "session_id": scope.session.id,
        "visibility": scope.visibility,
        "group_id": scope.session.conversation_id,
    }
    origin_payload = {
        "kind": "wave_memory_origin",
        "version": MEMORIES_V2_VERSION,
        "scope": scope_payload,
        "content": content,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "timestamp": timestamp,
        "source": source,
        "metadata": origin_metadata,
    }
    origin_fingerprint = hashlib.sha256(_canonical_json(origin_payload).encode("utf-8")).hexdigest()
    provenance_payload = {
        "kind": "wave_memory_provenance",
        "version": MEMORIES_V2_VERSION,
        "fingerprint_algorithm": "sha256",
        "origin_fingerprint": origin_fingerprint,
        "scope": scope_payload,
        "metadata": provenance,
    }
    quarantined = bool(payload.get("quarantine", False))
    memory_type = "archived" if quarantined else "message"
    summary = "quarantined: transient roleplay/identity confusion" if quarantined else None
    cursor = connection.execute(
        """INSERT INTO memories (
               group_id, sender_id, sender_name, content, vector, timestamp, importance, source,
               memory_type, summary, bot_id, session_id, visibility, origin_fingerprint,
               provenance, version, quarantine, resolution_state
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'resolved')""",
        (
            group_id,
            sender_id,
            sender_name,
            content,
            payload.get("vector_blob"),
            timestamp,
            float(payload.get("importance", 1.0)),
            source,
            memory_type,
            summary,
            scope.bot_id,
            scope.session.id,
            scope.visibility,
            origin_fingerprint,
            _canonical_json(provenance_payload),
            MEMORIES_V2_VERSION,
            int(quarantined),
        ),
    )
    memory_id = int(cursor.lastrowid)
    return MutationOutcome(
        entities=(EntityChange("memory", str(memory_id), MEMORIES_V2_VERSION, "created"),),
        events=(
            OutboxEventDraft(
                aggregate_kind="memory",
                aggregate_id=str(memory_id),
                aggregate_version=MEMORIES_V2_VERSION,
                event_type="memory.created",
                payload={
                    "memory_id": memory_id,
                    "source": source,
                    "quarantine": quarantined,
                    "scope": scope_payload,
                },
            ),
        ),
    )


def _backfill_memory_vector_handler(connection, command: DomainCommand, now: float) -> MutationOutcome:
    """Attach a recovered embedding to one still-live, exact-scope memory.

    A missing/deleted/replaced row is deliberately a no-op: the durable backfill
    runner must be able to continue past stale work without widening its Scope.
    """
    scope = _require_memory_scope(command.scope)
    payload = command.payload
    memory_id = int(payload["memory_id"])
    vector_blob = payload.get("vector_blob")
    if not isinstance(vector_blob, (bytes, bytearray, memoryview)) or not vector_blob:
        raise ValueError("memory vector backfill requires a non-empty vector blob")
    if len(vector_blob) % np.dtype(np.float32).itemsize:
        raise ValueError("memory vector backfill requires float32-aligned data")

    row = connection.execute(
        """SELECT version, vector FROM memories
             WHERE id=? AND bot_id=? AND session_id=? AND visibility=?
               AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0""",
        (memory_id, *_scope_tuple(scope)),
    ).fetchone()
    if row is None:
        return MutationOutcome(entities=(), events=(), warnings=("memory_missing_or_stale",))
    if row[1] is not None:
        return MutationOutcome(entities=(), events=(), warnings=("memory_vector_already_present",))

    previous_version = int(row[0] or MEMORIES_V2_VERSION)
    next_version = previous_version + 1
    connection.execute(
        """UPDATE memories SET vector=?, version=?
             WHERE id=? AND bot_id=? AND session_id=? AND visibility=? AND group_id=?""",
        (bytes(vector_blob), next_version, memory_id, *_scope_tuple(scope), scope.session.conversation_id),
    )
    scope_payload = {
        "bot_id": scope.bot_id,
        "session_id": scope.session.id,
        "visibility": scope.visibility,
        "group_id": scope.session.conversation_id,
    }
    return MutationOutcome(
        entities=(EntityChange("memory", str(memory_id), next_version, "vector_backfilled"),),
        events=(
            OutboxEventDraft(
                aggregate_kind="memory",
                aggregate_id=str(memory_id),
                aggregate_version=next_version,
                event_type="memory.vector_backfilled",
                payload={"memory_id": memory_id, "scope": scope_payload},
            ),
        ),
    )


def _catalog_neighbors_for_admission(connection, *, tag_type: str, limit: int = 64):
    """Load a bounded Catalog neighborhood for exact/semantic admission decisions."""
    try:
        from .tag_admission import CatalogNeighbor
    except ImportError:  # pragma: no cover
        from services.tag_admission import CatalogNeighbor

    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tag_catalog'"
    ).fetchone() is None:
        return []
    try:
        rows = connection.execute(
            """
            SELECT id, normalized_name, display_name, tag_type, embedding
              FROM tag_catalog
             WHERE status='active' AND tag_type=?
             ORDER BY COALESCE(updated_at, created_at, 0) DESC, id ASC
             LIMIT ?
            """,
            (str(tag_type or "keyword"), max(1, int(limit))),
        ).fetchall()
    except Exception:
        return []
    neighbors: list[CatalogNeighbor] = []
    for row in rows:
        embedding = None
        if row[4] is not None:
            try:
                embedding = np.frombuffer(row[4], dtype=np.float32).astype(float).tolist()
            except (TypeError, ValueError):
                embedding = None
        neighbors.append(
            CatalogNeighbor(
                catalog_id=int(row[0]),
                normalized_name=str(row[1] or ""),
                display_name=str(row[2] or row[1] or ""),
                tag_type=str(row[3] or "keyword"),
                embedding=embedding,
            )
        )
    return neighbors


def _apply_tag_extraction_handler(connection, command: DomainCommand, now: float) -> MutationOutcome:
    scope = _require_group_scope(command.scope)
    payload = command.payload
    memory_id = int(payload["memory_id"])
    row = connection.execute(
        """SELECT version FROM memories
             WHERE id=? AND bot_id=? AND session_id=? AND visibility=?
               AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0""",
        (memory_id, *_scope_tuple(scope)),
    ).fetchone()
    if row is None:
        raise ValueError("memory is not a resolved member of the RuntimeScope")

    try:
        from .tag_admission import admit_tag_batch, normalize_admission_name
    except ImportError:  # pragma: no cover
        from services.tag_admission import admit_tag_batch, normalize_admission_name

    # Neighbor pool is loaded once per batch; exact/semantic decisions stay pure.
    raw_tags = [item for item in (payload.get("tags") or ()) if isinstance(item, Mapping)]
    neighbor_types = {
        str(item.get("type") or item.get("tag_type") or "keyword").strip().casefold() or "keyword"
        for item in raw_tags
    }
    catalog_neighbors = []
    for tag_type in neighbor_types:
        catalog_neighbors.extend(_catalog_neighbors_for_admission(connection, tag_type=tag_type))
    admitted_tags, decisions = admit_tag_batch(raw_tags, catalog=catalog_neighbors)
    rejected = [decision.reason for decision in decisions if decision.action == "reject"]

    entities: list[EntityChange] = []
    tag_ids: list[int] = []
    catalog_ids: list[int] = []
    tag_columns = {str(item[1]) for item in connection.execute("PRAGMA table_info(scoped_tags)").fetchall()}
    catalog_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tag_catalog'"
    ).fetchone()

    for position, raw_tag in enumerate(admitted_tags, 1):
        name = str(raw_tag.get("name") or "").strip()
        if not name:
            continue
        tag_type = str(raw_tag.get("type") or "keyword")
        confidence = float(raw_tag.get("confidence", 0.8))
        metadata = _canonical_json({
            "producer": "tag_worker",
            "memory_id": memory_id,
            "admission": raw_tag.get("admission"),
            "admission_reason": raw_tag.get("admission_reason"),
        })
        catalog_id = None
        try:
            explicit_catalog = raw_tag.get("catalog_id")
            if explicit_catalog is not None:
                catalog_id = int(explicit_catalog)
        except (TypeError, ValueError):
            catalog_id = None

        if catalog_table is not None and "catalog_id" in tag_columns:
            normalized_name = normalize_admission_name(name)
            if catalog_id is None:
                connection.execute(
                    """INSERT INTO tag_catalog(
                           normalized_name, display_name, tag_type, description,
                           status, created_at, updated_at
                       ) VALUES (?, ?, ?, '', 'active', ?, ?)
                       ON CONFLICT(normalized_name, tag_type) DO UPDATE SET
                           display_name=CASE WHEN tag_catalog.display_name='' THEN excluded.display_name
                                             ELSE tag_catalog.display_name END,
                           updated_at=excluded.updated_at""",
                    (normalized_name, name, tag_type, now, now),
                )
                catalog_row = connection.execute(
                    "SELECT id, display_name FROM tag_catalog WHERE normalized_name=? AND tag_type=?",
                    (normalized_name, tag_type),
                ).fetchone()
            else:
                catalog_row = connection.execute(
                    "SELECT id, display_name FROM tag_catalog WHERE id=? AND status='active'",
                    (catalog_id,),
                ).fetchone()
                if catalog_row is None:
                    # Stale catalog_id from the caller must not invent a new row by id.
                    connection.execute(
                        """INSERT INTO tag_catalog(
                               normalized_name, display_name, tag_type, description,
                               status, created_at, updated_at
                           ) VALUES (?, ?, ?, '', 'active', ?, ?)
                           ON CONFLICT(normalized_name, tag_type) DO UPDATE SET
                               updated_at=excluded.updated_at""",
                        (normalized_name, name, tag_type, now, now),
                    )
                    catalog_row = connection.execute(
                        "SELECT id, display_name FROM tag_catalog WHERE normalized_name=? AND tag_type=?",
                        (normalized_name, tag_type),
                    ).fetchone()
            if catalog_row is not None:
                catalog_id = int(catalog_row[0])
                # Prefer the Catalog display name so semantic reuse collapses aliases.
                reused_name = str(catalog_row[1] or "").strip()
                if reused_name:
                    name = reused_name
            raw_vector = raw_tag.get("embedding")
            if catalog_id is not None and raw_vector is not None:
                try:
                    vector = np.asarray(raw_vector, dtype=np.float32).reshape(-1)
                    if vector.size:
                        connection.execute(
                            """UPDATE tag_catalog SET embedding=?, embedding_dim=?, updated_at=?
                                WHERE id=? AND status='active' AND embedding IS NULL""",
                            (vector.tobytes(), int(vector.size), now, catalog_id),
                        )
                except (TypeError, ValueError):
                    pass

        if catalog_id is not None:
            connection.execute(
                """INSERT INTO scoped_tags (
                       catalog_id, bot_id, session_id, visibility, name, tag_type, description, confidence,
                       metadata, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)
                   ON CONFLICT(bot_id, session_id, visibility, name) DO UPDATE SET
                       catalog_id=COALESCE(excluded.catalog_id, scoped_tags.catalog_id),
                       tag_type=excluded.tag_type, confidence=excluded.confidence,
                       metadata=excluded.metadata, updated_at=excluded.updated_at""",
                (catalog_id, *_scope_tuple(scope), name, tag_type, confidence, metadata, now, now),
            )
        else:
            connection.execute(
                """INSERT INTO scoped_tags (
                       bot_id, session_id, visibility, name, tag_type, description, confidence,
                       metadata, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?)
                   ON CONFLICT(bot_id, session_id, visibility, name) DO UPDATE SET
                       tag_type=excluded.tag_type, confidence=excluded.confidence,
                       metadata=excluded.metadata, updated_at=excluded.updated_at""",
                (*_scope_tuple(scope), name, tag_type, confidence, metadata, now, now),
            )
        tag_select = "id, catalog_id" if "catalog_id" in tag_columns else "id, NULL"
        tag_row = connection.execute(
            f"""SELECT {tag_select} FROM scoped_tags
                 WHERE bot_id=? AND session_id=? AND visibility=? AND name=?""",
            (*_scope_tuple(scope), name),
        ).fetchone()
        if tag_row is None:
            raise RuntimeError("scoped tag upsert did not return a row")
        tag_id = int(tag_row[0])
        catalog_id = int(tag_row[1]) if tag_row[1] is not None else catalog_id
        if catalog_id is not None:
            catalog_ids.append(catalog_id)
        connection.execute(
            """INSERT INTO scoped_memory_tags (
                   bot_id, session_id, visibility, memory_id, tag_id, position, relevance, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, 1.0, ?)
               ON CONFLICT(bot_id, session_id, visibility, memory_id, tag_id) DO UPDATE SET
                   position=excluded.position, relevance=excluded.relevance""",
            (*_scope_tuple(scope), memory_id, tag_id, position, now),
        )
        tag_ids.append(tag_id)
        entities.append(EntityChange("scoped_tag", str(tag_id), 1, "upserted"))

    status = str(payload.get("status") or ("done" if tag_ids else "skipped"))
    # All candidates rejected by admission still counts as a completed extraction.
    if not tag_ids and rejected and not str(payload.get("status") or "").strip():
        status = "skipped"
    error = payload.get("error")
    connection.execute(
        """INSERT INTO tag_extraction_status(memory_id, status, attempts, last_error, updated_at)
           VALUES (?, ?, 0, ?, ?)
           ON CONFLICT(memory_id) DO UPDATE SET
               status=excluded.status, last_error=excluded.last_error, updated_at=excluded.updated_at""",
        (memory_id, status, None if error is None else str(error)[:2000], now),
    )
    if bool(payload.get("upgrade_source")):
        connection.execute(
            """UPDATE memories SET source='core'
                 WHERE id=? AND bot_id=? AND session_id=? AND visibility=?
                   AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0 AND source='chat'""",
            (memory_id, *_scope_tuple(scope)),
        )

    version = int(row[0] or 1) + 1
    connection.execute(
        """UPDATE memories SET version=?
             WHERE id=? AND bot_id=? AND session_id=? AND visibility=?""",
        (version, memory_id, *_scope_tuple(scope)),
    )
    entities.append(EntityChange("memory", str(memory_id), version, "tag_extraction_updated"))
    return MutationOutcome(
        entities=tuple(entities),
        events=(
            OutboxEventDraft(
                aggregate_kind="memory",
                aggregate_id=str(memory_id),
                aggregate_version=version,
                event_type="memory.tags_applied",
                payload={
                    "memory_id": memory_id,
                    "tag_ids": tag_ids,
                    "catalog_ids": sorted(set(catalog_ids)),
                    "status": status,
                    "rejected_count": len(rejected),
                    "scope": scope.to_dict(),
                },
            ),
        ),
    )


def _mutate_memories_handler(connection, command: DomainCommand, now: float) -> MutationOutcome:
    scope = _require_memory_scope(command.scope)
    payload = command.payload
    memory_ids = tuple(dict.fromkeys(int(value) for value in payload.get("memory_ids") or ()))
    if not memory_ids:
        return MutationOutcome(entities=(), events=())
    placeholders = ",".join("?" for _ in memory_ids)
    rows = connection.execute(
        f"""SELECT id, COALESCE(version, 1) FROM memories
              WHERE id IN ({placeholders}) AND bot_id=? AND session_id=? AND visibility=?
                AND group_id=? AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0""",
        (*memory_ids, *_scope_tuple(scope), scope.session.conversation_id),
    ).fetchall()
    versions = {int(row[0]): int(row[1]) for row in rows}
    allowed_ids = tuple(memory_id for memory_id in memory_ids if memory_id in versions)
    if not allowed_ids:
        return MutationOutcome(entities=(), events=(), warnings=("no_scoped_memories_matched",))
    allowed_placeholders = ",".join("?" for _ in allowed_ids)
    action = str(payload.get("action") or "")
    if action == "touch":
        boost = float(payload.get("importance_boost", 0.01))
        connection.execute(
            f"""UPDATE memories
                   SET access_count=COALESCE(access_count, 0)+1,
                       last_accessed=?, importance=MIN(3.0, COALESCE(importance, 1.0)+?)
                 WHERE id IN ({allowed_placeholders})
                   AND bot_id=? AND session_id=? AND visibility=? AND group_id=?""",
            (now, boost, *allowed_ids, *_scope_tuple(scope), scope.session.conversation_id),
        )
    elif action == "set_importance":
        importance = float(payload["importance"])
        connection.execute(
            f"""UPDATE memories SET importance=? WHERE id IN ({allowed_placeholders})
                AND bot_id=? AND session_id=? AND visibility=? AND group_id=?""",
            (importance, *allowed_ids, *_scope_tuple(scope), scope.session.conversation_id),
        )
    elif action in {"archive", "evict"}:
        memory_type = "archived" if action == "archive" else "evicted"
        connection.execute(
            f"""UPDATE memories SET memory_type=? WHERE id IN ({allowed_placeholders})
                AND bot_id=? AND session_id=? AND visibility=? AND group_id=?""",
            (memory_type, *allowed_ids, *_scope_tuple(scope), scope.session.conversation_id),
        )
    elif action == "delete":
        connection.execute(
            f"DELETE FROM scoped_memory_tags WHERE memory_id IN ({allowed_placeholders})",
            allowed_ids,
        )
        connection.execute(
            f"DELETE FROM memory_tags WHERE memory_id IN ({allowed_placeholders})",
            allowed_ids,
        )
        connection.execute(
            f"""DELETE FROM memories WHERE id IN ({allowed_placeholders})
                AND bot_id=? AND session_id=? AND visibility=? AND group_id=?""",
            (*allowed_ids, *_scope_tuple(scope), scope.session.conversation_id),
        )
    else:
        raise ValueError(f"unsupported memory mutation: {action}")

    new_versions = {memory_id: versions[memory_id] + 1 for memory_id in allowed_ids}
    if action != "delete":
        connection.execute(
            f"""UPDATE memories SET version=COALESCE(version, 1)+1
                WHERE id IN ({allowed_placeholders})
                  AND bot_id=? AND session_id=? AND visibility=? AND group_id=?""",
            (*allowed_ids, *_scope_tuple(scope), scope.session.conversation_id),
        )
    change_type = {
        "touch": "accessed",
        "set_importance": "importance_updated",
        "archive": "archived",
        "evict": "evicted",
        "delete": "deleted",
    }[action]
    entities = tuple(
        EntityChange("memory", str(memory_id), new_versions[memory_id], change_type)
        for memory_id in allowed_ids
    )
    events = tuple(
        OutboxEventDraft(
            aggregate_kind="memory",
            aggregate_id=str(memory_id),
            aggregate_version=new_versions[memory_id],
            event_type=f"memory.{change_type}",
            payload={"memory_id": memory_id, "action": action},
        )
        for memory_id in allowed_ids
    )
    warnings = () if len(allowed_ids) == len(memory_ids) else ("some_memories_failed_scope_check",)
    return MutationOutcome(entities=entities, events=events, warnings=warnings)


class CoordinatorQualityRepository:
    """Persist quality decisions through the same writer-owned SQLite connection."""

    def __init__(self, coordinator: WriteCoordinator, *, clock: Any) -> None:
        self.coordinator = coordinator
        self.clock = clock

    def record(self, proposal: QualityProposal, decision: QualityDecision) -> QualityDecision:
        if proposal.proposal_id != decision.proposal_id:
            raise ValueError("quality decision does not belong to proposal")
        normalized_hash = "sha256:" + hashlib.sha256(
            decision.normalized_content.encode("utf-8")
        ).hexdigest()
        proposal_payload = proposal.to_dict()
        decided_at = float(self.clock.now())

        def persist(connection):
            cursor = connection.execute(
                """INSERT INTO quality_decisions(
                       proposal_id, operation, outcome, reason_code, reason_codes_json,
                       rule_version, raw_artifact_json, target_scope_json,
                       normalized_content_hash, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(proposal_id) DO NOTHING""",
                (
                    proposal.proposal_id,
                    proposal.operation,
                    decision.outcome,
                    decision.reason_code,
                    _canonical_json(decision.reason_codes),
                    decision.rule_version,
                    _canonical_json(proposal_payload["raw_artifact"]),
                    None
                    if proposal_payload["target_scope"] is None
                    else _canonical_json(proposal_payload["target_scope"]),
                    normalized_hash,
                    decided_at,
                ),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    """SELECT operation, outcome, reason_code, rule_version,
                              normalized_content_hash, target_scope_json
                         FROM quality_decisions WHERE proposal_id=?""",
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
                    else _canonical_json(proposal_payload["target_scope"]),
                )
                if existing is None or tuple(existing) != expected:
                    raise ValueError("quality_decision_conflict")
            return decision

        return self.coordinator.transaction_blocking(persist)


class ProductionWriteGateway:
    """Typed production ingress backed by the process-exclusive WriteCoordinator."""

    def __init__(
        self,
        database_path: str,
        *,
        clock: Any | None = None,
        consumers: Mapping[str, Any] | None = None,
    ) -> None:
        self._clock = clock or _SystemClock()
        self._consumers = dict(consumers or {})
        self._closing = False
        self.coordinator = WriteCoordinator(
            database_path,
            command_handlers={
                _APPEND_MEMORY: _append_memory_handler,
                _BACKFILL_MEMORY_VECTOR: _backfill_memory_vector_handler,
                _APPLY_TAG_EXTRACTION: _apply_tag_extraction_handler,
                _MUTATE_MEMORIES: _mutate_memories_handler,
            },
            consumer_names=tuple(self._consumers),
            clock=self._clock,
        )
        self.dispatcher = OutboxDispatcher(
            self.coordinator,
            self._consumers,
            self._clock,
        )
        self.quality_repository = CoordinatorQualityRepository(
            self.coordinator,
            clock=self._clock,
        )
        self.jobs = DurableJobService(self.coordinator, clock=self._clock)

    @staticmethod
    def _command(
        *,
        command_type: str,
        actor: str,
        scope: RuntimeScope,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_shape: Mapping[str, Any],
    ) -> DomainCommand:
        request_hash = _digest(request_shape)
        operation_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"wave-memory:{command_type}:{idempotency_key}:{request_hash}",
        ).hex
        return DomainCommand(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            actor=actor,
            scope=scope,
            command_type=command_type,
            payload=payload,
            request_hash=request_hash,
        )

    async def append_memory(
        self,
        *,
        scope: RuntimeScope,
        group_id: str,
        content: str,
        vector: np.ndarray | None,
        sender_id: str,
        sender_name: str,
        timestamp: float,
        importance: float,
        source: str,
        provenance: Mapping[str, Any],
        origin_metadata: Mapping[str, Any],
        quarantine: bool,
        idempotency_hint: str | None = None,
    ) -> int:
        vector_blob = vector.astype(np.float32).tobytes() if vector is not None else None
        request_shape = {
            "scope": scope.to_dict(),
            "group_id": group_id,
            "content": content,
            "vector_sha256": None if vector_blob is None else hashlib.sha256(vector_blob).hexdigest(),
            "sender_id": sender_id,
            "sender_name": sender_name,
            "timestamp": float(timestamp),
            "importance": float(importance),
            "source": source,
            "provenance": dict(provenance),
            "origin_metadata": dict(origin_metadata),
            "quarantine": bool(quarantine),
        }
        stable_hint = str(idempotency_hint or "").strip()
        idempotency_key = (
            f"memory.append:{stable_hint}" if stable_hint else f"memory.append:{_digest(request_shape)}"
        )
        command = self._command(
            command_type=_APPEND_MEMORY,
            actor="message_writer",
            scope=scope,
            payload={**request_shape, "vector_blob": vector_blob},
            idempotency_key=idempotency_key,
            request_shape=request_shape,
        )
        result = await self.coordinator.submit(command)
        memory = next(item for item in result.entities if item.aggregate_kind == "memory")
        return int(memory.aggregate_id)

    async def backfill_memory_vector(
        self,
        *,
        scope: RuntimeScope,
        memory_id: int,
        vector: np.ndarray,
        idempotency_hint: str | None = None,
    ) -> bool:
        """Persist one recovered embedding through the canonical scoped write path."""
        normalized = np.asarray(vector, dtype=np.float32)
        if normalized.ndim != 1 or normalized.size <= 0 or not np.isfinite(normalized).all():
            raise ValueError("memory vector backfill requires a finite one-dimensional vector")
        vector_blob = normalized.tobytes()
        request_shape = {
            "scope": scope.to_dict(),
            "memory_id": int(memory_id),
            "vector_sha256": hashlib.sha256(vector_blob).hexdigest(),
        }
        stable_hint = str(idempotency_hint or _digest(request_shape)).strip()
        command = self._command(
            command_type=_BACKFILL_MEMORY_VECTOR,
            actor="memory_vector_backfill",
            scope=scope,
            payload={**request_shape, "vector_blob": vector_blob},
            idempotency_key=f"memory.vector_backfill:{stable_hint}",
            request_shape=request_shape,
        )
        result = await self.coordinator.submit(command)
        return any(item.aggregate_kind == "memory" for item in result.entities)

    async def apply_tag_extraction(
        self,
        *,
        scope: RuntimeScope,
        memory_id: int,
        tags: Sequence[Mapping[str, Any]],
        status: str,
        upgrade_source: bool = False,
        error: str | None = None,
    ) -> int:
        normalized_tags = [dict(tag) for tag in tags if isinstance(tag, Mapping)]
        request_shape = {
            "scope": scope.to_dict(),
            "memory_id": int(memory_id),
            "tags": normalized_tags,
            "status": status,
            "upgrade_source": bool(upgrade_source),
            "error": error,
        }
        command = self._command(
            command_type=_APPLY_TAG_EXTRACTION,
            actor="tag_worker",
            scope=scope,
            payload=request_shape,
            idempotency_key=f"tag-extraction:{memory_id}:{_digest(request_shape)}",
            request_shape=request_shape,
        )
        result = await self.coordinator.submit(command)
        return sum(1 for item in result.entities if item.aggregate_kind == "scoped_tag")

    async def mutate_memories(
        self,
        *,
        scope: RuntimeScope,
        memory_ids: Sequence[int],
        action: str,
        importance: float | None = None,
        importance_boost: float = 0.01,
        idempotency_hint: str | None = None,
    ) -> tuple[int, ...]:
        normalized_ids = tuple(dict.fromkeys(int(value) for value in memory_ids))
        request_shape = {
            "scope": scope.to_dict(),
            "memory_ids": normalized_ids,
            "action": action,
            "importance": importance,
            "importance_boost": float(importance_boost),
        }
        if action == "touch" and not idempotency_hint:
            idempotency_hint = uuid.uuid4().hex
        stable_hint = str(idempotency_hint or _digest(request_shape))
        command = self._command(
            command_type=_MUTATE_MEMORIES,
            actor="memory_lifecycle",
            scope=scope,
            payload=request_shape,
            idempotency_key=f"memory.mutate:{action}:{stable_hint}",
            request_shape=request_shape,
        )
        result = await self.coordinator.submit(command)
        return tuple(
            int(item.aggregate_id)
            for item in result.entities
            if item.aggregate_kind == "memory"
        )

    async def touch_memories(
        self,
        *,
        scope: RuntimeScope,
        memory_ids: Sequence[int],
        importance_boost: float = 0.01,
    ) -> tuple[int, ...]:
        return await self.mutate_memories(
            scope=scope,
            memory_ids=memory_ids,
            action="touch",
            importance_boost=importance_boost,
        )

    async def set_memory_importance(
        self,
        *,
        scope: RuntimeScope,
        memory_ids: Sequence[int],
        importance: float,
        idempotency_hint: str | None = None,
    ) -> tuple[int, ...]:
        return await self.mutate_memories(
            scope=scope,
            memory_ids=memory_ids,
            action="set_importance",
            importance=importance,
            idempotency_hint=idempotency_hint,
        )

    async def run_outbox_loop(self, interval_seconds: float = 0.25) -> None:
        import asyncio

        while not self._closing:
            try:
                await self.drain_committed()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(min(max(float(interval_seconds), 0.05), 2.0))
                continue
            await asyncio.sleep(max(float(interval_seconds), 0.05))

    async def drain_committed(self) -> int:
        watermark = await self.coordinator.committed_watermark()
        await self.dispatcher.drain_to_watermark(watermark)
        return watermark

    async def advance_and_drain(self) -> int:
        await self.dispatcher.advance_clock_to_next_attempt()
        return await self.drain_committed()

    async def save_projection_barrier(self, watermark: int) -> None:
        for consumer in self._consumers.values():
            barrier = getattr(consumer, "save_barrier", None)
            if not callable(barrier):
                continue
            result = barrier(db_watermark=int(watermark))
            if hasattr(result, "__await__"):
                await result

    async def shutdown(self) -> None:
        self._closing = True
        await self.coordinator.close_accepting()
        watermark = await self.drain_committed()
        await self.save_projection_barrier(watermark)
        await self.dispatcher.close()
        await self.coordinator.shutdown()


class _SystemClock:
    @staticmethod
    def now() -> float:
        return time.time()


__all__ = ["ProductionWriteGateway"]
