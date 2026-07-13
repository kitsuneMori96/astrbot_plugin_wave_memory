"""Single-owner SQLite writer thread and durable command coordinator."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import queue
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from domain.commands import (
    CommandRejectedError,
    DomainCommand,
    DomainWriteResult,
    EntityChange,
    IdempotencyConflictError,
    OutboxEventRef,
)
from domain.scope import scope_to_dict
from engine.db.outbox_repo import OutboxRepository


@dataclass(frozen=True)
class OutboxEventDraft:
    aggregate_kind: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    payload: Mapping[str, Any]
    payload_version: int = 1


@dataclass(frozen=True)
class MutationOutcome:
    entities: tuple[EntityChange, ...]
    events: tuple[OutboxEventDraft, ...]
    warnings: tuple[str, ...] = ()


CommandHandler = Callable[[sqlite3.Connection, DomainCommand, float], MutationOutcome]


class WriteCoordinator:
    """Owns the only writable connection and serializes all transactional work."""

    def __init__(
        self,
        database_path: str,
        *,
        command_handlers: Mapping[str, CommandHandler],
        consumer_names: tuple[str, ...],
        clock: Any,
        queue_capacity: int = 256,
    ) -> None:
        self.database_path = os.path.abspath(database_path)
        self._lease_path = self.database_path + ".writer.lock"
        self._lease_file = self._acquire_writer_lease()
        self._handlers = dict(command_handlers)
        self._consumer_names = tuple(sorted(set(consumer_names)))
        self._clock = clock
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_capacity)
        self._accept_lock = threading.Lock()
        self._accepting = True
        self._stopped = False
        self._ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._thread = threading.Thread(
            target=self._writer_main, name="wave-memory-writer", daemon=True
        )
        self._thread.start()
        try:
            self._ready.result(timeout=30)
        except BaseException:
            self._release_writer_lease()
            raise

    def _acquire_writer_lease(self):
        parent = os.path.dirname(self._lease_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        lease = open(self._lease_path, "a+b")
        lease.seek(0, os.SEEK_END)
        if lease.tell() == 0:
            lease.write(b"\0")
            lease.flush()
        lease.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - native deployment is Windows
                import fcntl

                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lease.close()
            raise CommandRejectedError("writer_lease_unavailable") from exc
        return lease

    def _release_writer_lease(self) -> None:
        lease = getattr(self, "_lease_file", None)
        if lease is None:
            return
        try:
            lease.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - native deployment is Windows
                import fcntl

                fcntl.flock(lease.fileno(), fcntl.LOCK_UN)
        finally:
            lease.close()
            self._lease_file = None

    def _writer_main(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            parent = os.path.dirname(self.database_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            connection = sqlite3.connect(
                self.database_path, isolation_level=None, timeout=30.0
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=NORMAL")
            OutboxRepository.migrate(connection)
            if connection.in_transaction:
                raise RuntimeError("bootstrap left an active transaction")
            self._ready.set_result(None)
            while True:
                item = self._queue.get()
                try:
                    if item is None:
                        return
                    function, transactional, future = item
                    if future.cancelled():
                        continue
                    try:
                        if connection.in_transaction:
                            raise RuntimeError("writer connection already in transaction")
                        if transactional:
                            connection.execute("BEGIN IMMEDIATE")
                        result = function(connection)
                        if transactional:
                            connection.commit()
                        if connection.in_transaction:
                            raise RuntimeError("writer transaction did not settle")
                    except BaseException as exc:
                        if connection.in_transaction:
                            try:
                                connection.rollback()
                            except BaseException:
                                pass
                        future.set_exception(exc)
                    else:
                        future.set_result(result)
                finally:
                    self._queue.task_done()
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    item[2].set_exception(exc)
                self._queue.task_done()
        finally:
            if connection is not None:
                connection.close()

    async def _dispatch(self, function: Callable[[sqlite3.Connection], Any], transactional: bool) -> Any:
        if self._stopped:
            raise RuntimeError("write coordinator is stopped")
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._queue.put((function, transactional, future))
        return await asyncio.wrap_future(future)

    async def transaction(self, function: Callable[[sqlite3.Connection], Any]) -> Any:
        return await self._dispatch(function, True)

    def transaction_blocking(self, function: Callable[[sqlite3.Connection], Any]) -> Any:
        """Submit a short synchronous caller operation to the writer-owned transaction."""
        if self._stopped:
            raise RuntimeError("write coordinator is stopped")
        if threading.current_thread() is self._thread:
            raise RuntimeError("writer thread cannot synchronously dispatch to itself")
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._queue.put((function, True, future))
        return future.result(timeout=30)

    async def read(self, function: Callable[[sqlite3.Connection], Any]) -> Any:
        return await self._dispatch(function, False)

    async def submit(self, command: DomainCommand) -> DomainWriteResult:
        if not isinstance(command, DomainCommand):
            raise TypeError("submit requires DomainCommand")
        future: concurrent.futures.Future[DomainWriteResult] = concurrent.futures.Future()
        with self._accept_lock:
            if not self._accepting or self._stopped:
                raise CommandRejectedError("ingress_closed")
            # Enqueue while holding the ingress lock: close_accepting is therefore a real
            # acceptance fence rather than a check-then-enqueue race.
            self._queue.put(
                (lambda conn: self._execute_command(conn, command), True, future)
            )
        return await asyncio.wrap_future(future)

    def _execute_command(
        self, connection: sqlite3.Connection, command: DomainCommand
    ) -> DomainWriteResult:
        existing = connection.execute(
            """SELECT operation_id, request_hash, result_json, status
               FROM write_operations WHERE idempotency_key=?""",
            (command.idempotency_key,),
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != command.request_hash:
                raise IdempotencyConflictError()
            if existing[3] != "committed" or not existing[2]:
                raise CommandRejectedError("operation_incomplete")
            return self._decode_result(str(existing[2]))
        collision = connection.execute(
            "SELECT 1 FROM write_operations WHERE operation_id=?", (command.operation_id,)
        ).fetchone()
        if collision is not None:
            raise IdempotencyConflictError("operation_id was reused")
        handler = self._handlers.get(command.command_type)
        if handler is None:
            raise CommandRejectedError("unknown_command_type")
        now = float(self._clock.now())
        sequence = OutboxRepository.next_write_sequence(connection)
        connection.execute(
            """INSERT INTO write_operations(
                   operation_id, idempotency_key, request_hash, command_type, scope_json,
                   status, write_sequence, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                command.operation_id, command.idempotency_key, command.request_hash,
                command.command_type,
                json.dumps(scope_to_dict(command.scope), sort_keys=True, separators=(",", ":")),
                sequence, now,
            ),
        )
        outcome = handler(connection, command, now)
        effects: list[OutboxEventRef] = []
        for index, draft in enumerate(outcome.events):
            event_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"wave-memory:{command.operation_id}:{index}"
            ).hex
            connection.execute(
                """INSERT INTO domain_outbox(
                       event_id, operation_id, aggregate_kind, aggregate_id,
                       aggregate_version, event_type, payload_version, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, command.operation_id, draft.aggregate_kind, draft.aggregate_id,
                    draft.aggregate_version, draft.event_type, draft.payload_version,
                    json.dumps(dict(draft.payload), sort_keys=True, separators=(",", ":")), now,
                ),
            )
            OutboxRepository.add_deliveries(
                connection, event_id, self._consumer_names, now
            )
            effects.append(
                OutboxEventRef(
                    event_id=event_id, event_type=draft.event_type,
                    aggregate_kind=draft.aggregate_kind, aggregate_id=draft.aggregate_id,
                    aggregate_version=draft.aggregate_version,
                )
            )
        result = DomainWriteResult(
            operation_id=command.operation_id,
            committed_at=now,
            write_sequence=sequence,
            entities=outcome.entities,
            effects=tuple(effects),
            warnings=outcome.warnings,
        )
        encoded = self._encode_result(result)
        connection.execute(
            """UPDATE write_operations SET status='committed', result_json=?, committed_at=?
               WHERE operation_id=?""",
            (encoded, now, command.operation_id),
        )
        return result

    @staticmethod
    def _encode_result(result: DomainWriteResult) -> str:
        return json.dumps(asdict(result), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_result(raw: str) -> DomainWriteResult:
        value = json.loads(raw)
        return DomainWriteResult(
            operation_id=value["operation_id"],
            committed_at=float(value["committed_at"]),
            write_sequence=int(value["write_sequence"]),
            entities=tuple(EntityChange(**item) for item in value.get("entities", ())),
            effects=tuple(OutboxEventRef(**item) for item in value.get("effects", ())),
            warnings=tuple(value.get("warnings", ())),
        )

    async def committed_watermark(self) -> int:
        return int(await self.read(OutboxRepository.committed_watermark))

    async def close_accepting(self) -> None:
        with self._accept_lock:
            self._accepting = False

    async def shutdown(self) -> None:
        if self._stopped:
            return
        await self.close_accepting()
        self._stopped = True
        self._queue.put(None)
        try:
            await asyncio.to_thread(self._thread.join, 30)
            if self._thread.is_alive():
                raise RuntimeError("writer thread did not stop")
        finally:
            self._release_writer_lease()
