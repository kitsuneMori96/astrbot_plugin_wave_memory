from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest

from domain.scope import RuntimeScope, SessionRef
from engine.db.outbox_repo import OutboxRepository
from engine.db.memory_repo import MemoryRepo, MemoryRevisionConflict
from services.durable_jobs import DurableJobEnvelope, DurableJobService
from services.memory_jobs import MemoryDurableJobHandlers
from services.memory_mutations import MemoryMutationGateway, MemoryMutationTarget
from webui.blueprints import memories


def _scope() -> RuntimeScope:
    return RuntimeScope(
        bot_id="bot-alpha",
        visibility="group",
        session=SessionRef(
            id="qq:group:group-alpha",
            platform_id="qq",
            kind="group",
            conversation_id="group-alpha",
        ),
        subject_principal_id="qq:user:user-alpha",
    )


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            group_id TEXT NOT NULL,
            content TEXT NOT NULL,
            sender_name TEXT,
            vector BLOB,
            importance REAL NOT NULL DEFAULT 1.0,
            bot_id TEXT,
            session_id TEXT,
            visibility TEXT,
            resolution_state TEXT,
            quarantine INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE memory_tags(memory_id INTEGER, tag_id INTEGER);
        CREATE TABLE scoped_memory_tags(memory_id INTEGER, tag_id INTEGER);
        CREATE TABLE facts(id INTEGER PRIMARY KEY, source_memory_id INTEGER);
        """
    )
    OutboxRepository.migrate(connection)
    connection.execute(
        """INSERT INTO memories(
               id, group_id, content, vector, importance, bot_id, session_id,
               visibility, resolution_state, quarantine, version
           ) VALUES (1, 'group-alpha', 'before', ?, 1.0, 'bot-alpha',
                     'qq:group:group-alpha', 'group', 'resolved', 0, 7)""",
        (np.asarray([1.0, 0.0], dtype=np.float32).tobytes(),),
    )
    connection.commit()
    return connection


def test_memory_repo_scoped_update_owns_no_commit_and_checks_revision():
    connection = _connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = MemoryRepo.update_scoped_memory(
            connection,
            scope=_scope(),
            memory_id=1,
            expected_revision=7,
            content="after",
            importance=1.5,
        )
        assert connection.in_transaction is True
        assert result == {"memory_id": 1, "previous_revision": 7, "revision": 8}
        assert connection.execute(
            "SELECT content, importance, vector, version FROM memories WHERE id=1"
        ).fetchone() == ("after", 1.5, None, 8)
        connection.rollback()
        assert connection.execute(
            "SELECT content, importance, version FROM memories WHERE id=1"
        ).fetchone() == ("before", 1.0, 7)
    finally:
        connection.close()


def test_memory_repo_batch_delete_is_all_or_nothing_on_stale_revision():
    connection = _connection()
    try:
        connection.execute(
            """INSERT INTO memories(
                   id, group_id, content, importance, bot_id, session_id,
                   visibility, resolution_state, quarantine, version
               ) VALUES (2, 'group-alpha', 'second', 1.0, 'bot-alpha',
                         'qq:group:group-alpha', 'group', 'resolved', 0, 3)"""
        )
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(MemoryRevisionConflict) as caught:
            MemoryRepo.delete_scoped_memories(
                connection,
                scope=_scope(),
                expected_revisions={1: 7, 2: 99},
            )
        assert caught.value.code == "memory_revision_conflict"
        assert connection.execute("SELECT id FROM memories ORDER BY id").fetchall() == [(1,), (2,)]
        connection.rollback()
    finally:
        connection.close()


class _Coordinator:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self._consumer_names = ("memory_index", "runtime_refresh")
        self.actors: list[str | None] = []

    async def transaction(self, callback, *, actor=None):
        self.actors.append(actor)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            result = callback(self.connection)
            self.connection.commit()
            return result
        except BaseException:
            self.connection.rollback()
            raise

    async def read(self, callback):
        return callback(self.connection)


@pytest.mark.asyncio
async def test_memory_mutation_gateway_commits_revision_and_outbox_together():
    connection = _connection()
    coordinator = _Coordinator(connection)
    gateway = MemoryMutationGateway(SimpleNamespace(coordinator=coordinator))
    try:
        result = await gateway.update_memory(
            scope=_scope(),
            target=MemoryMutationTarget(memory_id=1, revision=7),
            content="coordinated",
        )
        assert result.revision == 8
        assert result.operation_id
        operation = connection.execute(
            "SELECT status, command_type FROM write_operations WHERE operation_id=?",
            (result.operation_id,),
        ).fetchone()
        assert operation == ("committed", "memory.webui.update.v1")
        event = connection.execute(
            "SELECT event_type, aggregate_version FROM domain_outbox WHERE operation_id=?",
            (result.operation_id,),
        ).fetchone()
        assert event == ("memory.updated", 8)
        deliveries = connection.execute(
            "SELECT consumer_name, state FROM outbox_deliveries ORDER BY consumer_name"
        ).fetchall()
        assert deliveries == [("memory_index", "pending"), ("runtime_refresh", "pending")]
        assert coordinator.actors == ["webui.memory.update"]
    finally:
        connection.close()


class _Clock:
    @staticmethod
    def now():
        return 100.0


@pytest.mark.asyncio
async def test_durable_job_enqueue_returns_stable_envelope_in_one_facade_call():
    connection = _connection()
    coordinator = _Coordinator(connection)
    service = DurableJobService(coordinator, clock=_Clock())
    try:
        envelope = await service.enqueue(
            idempotency_key="memory-reembed:1:7",
            kind="memory.reembed.v1",
            scope=_scope().to_dict(),
            payload={"targets": [{"memory_id": 1, "revision": 7}]},
            schedule_slot="manual:memory-reembed:1:7",
            cursor={"phase": "queued"},
        )
        assert isinstance(envelope, DurableJobEnvelope)
        assert envelope.kind == "memory.reembed.v1"
        assert envelope.status == "queued"
        assert envelope.request_id
        assert envelope.job_id
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_reembed_job_handler_writes_vector_via_revisioned_outbox_gateway():
    connection = _connection()
    coordinator = _Coordinator(connection)
    write_gateway = SimpleNamespace(coordinator=coordinator)

    class Embedding:
        async def get_embedding(self, content):
            assert content == "before"
            return np.asarray([0.25, 0.75], dtype=np.float32)

    class JobService:
        def __init__(self):
            self.progress = []

        async def update_progress(self, run_id, **kwargs):
            self.progress.append((run_id, kwargs))

    service = JobService()
    runner = SimpleNamespace(
        service=service,
        lease_owner="worker-1",
        lease_seconds=60.0,
    )
    run = SimpleNamespace(run_id="run-1", cursor={})
    request = SimpleNamespace(payload={
        "scope": _scope().to_dict(),
        "targets": [{"memory_id": 1, "revision": 7}],
    })
    handlers = MemoryDurableJobHandlers(
        write_gateway=write_gateway,
        db=SimpleNamespace(),
        embedding_service=Embedding(),
        tag_extractor=None,
    )
    try:
        result = await handlers.reembed(run, request, runner)
        row = connection.execute("SELECT vector, version FROM memories WHERE id=1").fetchone()
        assert np.frombuffer(row[0], dtype=np.float32).tolist() == pytest.approx([0.25, 0.75])
        assert row[1] == 8
        assert result == {
            "processed": 1,
            "total": 1,
            "errors": 0,
            "projection": "domain_outbox",
        }
        assert service.progress[0][1]["cursor"] == {"phase": "reembed", "processed": 1}
        assert connection.execute(
            "SELECT event_type FROM domain_outbox ORDER BY created_at DESC LIMIT 1"
        ).fetchone() == ("memory.reembedded",)
    finally:
        connection.close()


@dataclass(frozen=True)
class _Binding:
    scope: RuntimeScope
    revision: int


class _Request:
    def __init__(self, body, *, args=None):
        self._body = body
        self.args = args or {}

    async def get_json(self, *args, **kwargs):
        return self._body


class _Jobs:
    def __init__(self):
        self.calls = []

    async def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return DurableJobEnvelope(
            request_id="request-1",
            job_id="run-1",
            kind=kwargs["kind"],
            status="queued",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "body", "kind"),
    [
        ("batch_re_embed", {"refs": [{"id": 1, "ref": "opaque"}]}, "memory.batch.reembed.v1"),
        (
            "batch_extract_tags_for_ids",
            {"refs": [{"id": 1, "ref": "opaque"}], "tag_batch_size": 10},
            "memory.batch.extract_tags.v1",
        ),
    ],
)
async def test_batch_routes_return_durable_json_envelope_not_http_sse(
    monkeypatch, handler_name, body, kind
):
    jobs = _Jobs()
    container = SimpleNamespace(durable_jobs=jobs, db=SimpleNamespace(conn=object()))
    target = MemoryMutationTarget(memory_id=1, revision=7)
    monkeypatch.setattr(memories, "get_container", lambda: container)
    monkeypatch.setattr(memories, "request", _Request(body))
    monkeypatch.setattr(memories, "jsonify", lambda value: value)
    monkeypatch.setattr(
        memories,
        "_resolve_batch_refs",
        lambda conn, refs: (( _scope(), [target]), None),
    )

    payload, status = await getattr(memories, handler_name)()

    assert status == 202
    assert payload["accepted"] is True
    assert payload["operation"] == {"id": "run-1", "kind": kind, "status": "queued"}
    assert "text/event-stream" not in str(payload)
    assert jobs.calls[0]["payload"]["targets"] == [{"memory_id": 1, "revision": 7}]


@pytest.mark.asyncio
async def test_legacy_batch_ids_receive_stable_migration_error(monkeypatch):
    monkeypatch.setattr(
        memories,
        "get_container",
        lambda: SimpleNamespace(db=SimpleNamespace(conn=object()), durable_jobs=_Jobs()),
    )
    monkeypatch.setattr(memories, "request", _Request({"ids": [1]}))
    monkeypatch.setattr(memories, "jsonify", lambda value: value)

    payload, status = await memories.batch_re_embed()

    assert status == 410
    assert payload["error"]["code"] == "memory_object_ref_migration_required"
