"""Scoped Memory WebUI mutations through the writer-owned transaction and outbox."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

try:
    from ..domain.scope import RuntimeScope, scope_to_dict
    from ..engine.db.memory_repo import MemoryRepo
    from ..engine.db.outbox_repo import OutboxRepository
except ImportError:  # pragma: no cover - focused tests import top-level packages
    from domain.scope import RuntimeScope, scope_to_dict
    from engine.db.memory_repo import MemoryRepo
    from engine.db.outbox_repo import OutboxRepository


_UNSET = object()


@dataclass(frozen=True)
class MemoryMutationTarget:
    memory_id: int
    revision: int

    def to_dict(self) -> dict[str, int]:
        return {"memory_id": int(self.memory_id), "revision": int(self.revision)}


@dataclass(frozen=True)
class MemoryMutationResult:
    operation_id: str
    targets: tuple[MemoryMutationTarget, ...]

    @property
    def revision(self) -> int | None:
        return self.targets[0].revision if len(self.targets) == 1 else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class MemoryMutationGateway:
    """Narrow WebUI gateway for exact Scope/ObjectRef revision mutations.

    ``ProductionWriteGateway`` remains the process wiring point. This facade only uses
    its writer-owned coordinator and appends operation/outbox rows in the same SQLite
    transaction as the canonical memory mutation.
    """

    def __init__(self, write_gateway: Any, *, clock: Any | None = None) -> None:
        coordinator = getattr(write_gateway, "coordinator", None)
        if coordinator is None:
            raise ValueError("write gateway coordinator is required")
        self._coordinator = coordinator
        self._clock = clock
        consumers = getattr(write_gateway, "_consumers", None)
        if isinstance(consumers, Mapping):
            self._consumer_names = tuple(sorted(str(name) for name in consumers))
        else:
            self._consumer_names = tuple(
                sorted(str(name) for name in getattr(coordinator, "_consumer_names", ()))
            )

    def _now(self) -> float:
        if self._clock is not None and callable(getattr(self._clock, "now", None)):
            return float(self._clock.now())
        return time.time()

    async def _commit(
        self,
        *,
        scope: RuntimeScope,
        command_type: str,
        actor: str,
        event_type: str,
        request_shape: Mapping[str, Any],
        mutate,
    ) -> MemoryMutationResult:
        request_hash = _digest(request_shape)
        idempotency_key = f"{command_type}:{request_hash}"
        operation_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"wave-memory:{command_type}:{request_hash}",
        ).hex
        now = self._now()

        def transaction(connection):
            existing = connection.execute(
                "SELECT request_hash, status, result_json FROM write_operations WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != request_hash:
                    raise ValueError("memory_mutation_idempotency_conflict")
                if existing[1] != "committed" or not existing[2]:
                    raise ValueError("memory_mutation_incomplete")
                payload = json.loads(str(existing[2]))
                return MemoryMutationResult(
                    operation_id=str(payload["operation_id"]),
                    targets=tuple(
                        MemoryMutationTarget(
                            memory_id=int(item["aggregate_id"]),
                            revision=int(item["aggregate_version"]),
                        )
                        for item in payload.get("entities", ())
                        if item.get("aggregate_kind") == "memory"
                    ),
                )

            write_sequence = OutboxRepository.next_write_sequence(connection)
            connection.execute(
                """INSERT INTO write_operations(
                       operation_id, idempotency_key, request_hash, command_type, scope_json,
                       status, write_sequence, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    operation_id,
                    idempotency_key,
                    request_hash,
                    command_type,
                    _canonical_json(scope_to_dict(scope)),
                    write_sequence,
                    now,
                ),
            )
            records = tuple(mutate(connection))
            entities = []
            effects = []
            scope_payload = scope_to_dict(scope)
            for index, record in enumerate(records):
                memory_id = int(record["memory_id"])
                revision = int(record["revision"])
                event_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"wave-memory:{operation_id}:{index}",
                ).hex
                connection.execute(
                    """INSERT INTO domain_outbox(
                           event_id, operation_id, aggregate_kind, aggregate_id,
                           aggregate_version, event_type, payload_version, payload_json, created_at)
                       VALUES (?, ?, 'memory', ?, ?, ?, 1, ?, ?)""",
                    (
                        event_id,
                        operation_id,
                        str(memory_id),
                        revision,
                        event_type,
                        _canonical_json(
                            {
                                "memory_id": memory_id,
                                "previous_revision": int(record["previous_revision"]),
                                "scope": scope_payload,
                            }
                        ),
                        now,
                    ),
                )
                OutboxRepository.add_deliveries(
                    connection,
                    event_id,
                    self._consumer_names,
                    now,
                )
                entities.append(
                    {
                        "aggregate_kind": "memory",
                        "aggregate_id": str(memory_id),
                        "aggregate_version": revision,
                        "change_type": event_type.removeprefix("memory."),
                    }
                )
                effects.append(
                    {
                        "event_id": event_id,
                        "event_type": event_type,
                        "aggregate_kind": "memory",
                        "aggregate_id": str(memory_id),
                        "aggregate_version": revision,
                    }
                )
            result_payload = {
                "operation_id": operation_id,
                "committed_at": now,
                "write_sequence": write_sequence,
                "entities": entities,
                "effects": effects,
                "warnings": [],
            }
            connection.execute(
                """UPDATE write_operations
                      SET status='committed', result_json=?, committed_at=?
                    WHERE operation_id=?""",
                (_canonical_json(result_payload), now, operation_id),
            )
            return MemoryMutationResult(
                operation_id=operation_id,
                targets=tuple(
                    MemoryMutationTarget(
                        memory_id=int(record["memory_id"]),
                        revision=int(record["revision"]),
                    )
                    for record in records
                ),
            )

        return await self._coordinator.transaction(transaction, actor=actor)

    async def update_memory(
        self,
        *,
        scope: RuntimeScope,
        target: MemoryMutationTarget,
        content: Any = _UNSET,
        importance: Any = _UNSET,
    ) -> MemoryMutationResult:
        fields: dict[str, Any] = {}
        repo_fields: dict[str, Any] = {}
        if content is not _UNSET:
            fields["content"] = str(content or "")
            repo_fields["content"] = content
        if importance is not _UNSET:
            fields["importance"] = importance
            repo_fields["importance"] = importance
        if not fields:
            raise ValueError("at least one mutable memory field is required")
        request_shape = {
            "scope": scope_to_dict(scope),
            "target": target.to_dict(),
            "fields": fields,
        }
        return await self._commit(
            scope=scope,
            command_type="memory.webui.update.v1",
            actor="webui.memory.update",
            event_type="memory.updated",
            request_shape=request_shape,
            mutate=lambda connection: (
                MemoryRepo.update_scoped_memory(
                    connection,
                    scope=scope,
                    memory_id=target.memory_id,
                    expected_revision=target.revision,
                    **repo_fields,
                ),
            ),
        )

    async def update_memory_vector(
        self,
        *,
        scope: RuntimeScope,
        target: MemoryMutationTarget,
        vector: Any,
    ) -> MemoryMutationResult:
        vector_array = None if vector is None else [float(value) for value in vector]
        request_shape = {
            "scope": scope_to_dict(scope),
            "target": target.to_dict(),
            "vector_sha256": _digest(vector_array),
        }
        return await self._commit(
            scope=scope,
            command_type="memory.webui.reembed.v1",
            actor="durable.memory.reembed",
            event_type="memory.reembedded",
            request_shape=request_shape,
            mutate=lambda connection: (
                MemoryRepo.update_scoped_memory(
                    connection,
                    scope=scope,
                    memory_id=target.memory_id,
                    expected_revision=target.revision,
                    vector=vector_array,
                ),
            ),
        )

    async def delete_memories(
        self,
        *,
        scope: RuntimeScope,
        targets: Sequence[MemoryMutationTarget],
    ) -> MemoryMutationResult:
        normalized = tuple(
            dict.fromkeys(
                MemoryMutationTarget(int(target.memory_id), int(target.revision))
                for target in targets
            )
        )
        request_shape = {
            "scope": scope_to_dict(scope),
            "targets": [target.to_dict() for target in normalized],
        }
        return await self._commit(
            scope=scope,
            command_type="memory.webui.delete.v1",
            actor="webui.memory.delete",
            event_type="memory.deleted",
            request_shape=request_shape,
            mutate=lambda connection: MemoryRepo.delete_scoped_memories(
                connection,
                scope=scope,
                expected_revisions={
                    target.memory_id: target.revision for target in normalized
                },
            ),
        )


__all__ = [
    "MemoryMutationGateway",
    "MemoryMutationResult",
    "MemoryMutationTarget",
]
