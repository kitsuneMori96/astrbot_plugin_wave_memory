from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest

from domain.scope import RuntimeScope, SessionRef
from engine.db.outbox_repo import OutboxEvent
from engine.vector_index import IndexCapacityError
from services.derived_projections import MemoryIndexProjection, TagIndexProjection
from services.memory_index_policy import MemoryIndexPolicy
from services.system_convergence_runtime import ProductionWriteGateway


def _prepare_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT, sender_id TEXT, sender_name TEXT, content TEXT NOT NULL,
                vector BLOB, timestamp REAL, importance REAL, source TEXT,
                memory_type TEXT DEFAULT 'message', summary TEXT,
                bot_id TEXT, session_id TEXT, visibility TEXT,
                origin_fingerprint TEXT, provenance TEXT, version INTEGER DEFAULT 1,
                quarantine INTEGER DEFAULT 0, resolution_state TEXT DEFAULT 'resolved',
                access_count INTEGER DEFAULT 0, last_accessed REAL
            );
            CREATE TABLE scoped_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL,
                session_id TEXT NOT NULL, visibility TEXT NOT NULL, name TEXT NOT NULL,
                tag_type TEXT, description TEXT, confidence REAL, metadata TEXT,
                created_at REAL, updated_at REAL,
                UNIQUE(bot_id, session_id, visibility, name)
            );
            CREATE TABLE scoped_memory_tags (
                bot_id TEXT NOT NULL, session_id TEXT NOT NULL, visibility TEXT NOT NULL,
                memory_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, position INTEGER,
                relevance REAL, created_at REAL,
                UNIQUE(bot_id, session_id, visibility, memory_id, tag_id)
            );
            CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER);
            CREATE TABLE tag_extraction_status (
                memory_id INTEGER PRIMARY KEY, status TEXT, attempts INTEGER DEFAULT 0,
                last_error TEXT, updated_at REAL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _scope() -> RuntimeScope:
    return RuntimeScope(
        bot_id="bot-a",
        visibility="group",
        session=SessionRef(
            id="qq:group:g1",
            platform_id="qq",
            kind="group",
            conversation_id="g1",
        ),
    )


class _Index:
    dimension = 3

    def __init__(self):
        self.added = []
        self.deleted = []
        self.saved = []

    def add(self, ids, vectors):
        self.added.append((list(ids), np.asarray(vectors).copy()))

    def mark_deleted(self, ids):
        self.deleted.extend(ids)

    def save(self, **kwargs):
        self.saved.append(dict(kwargs))


class _CapacityIndex(_Index):
    def add(self, ids, vectors):
        raise IndexCapacityError("index capacity reached")


@pytest.mark.asyncio
async def test_committed_memory_event_projects_vector_and_delete_tombstone(tmp_path):
    path = tmp_path / "stage3.sqlite"
    _prepare_db(path)
    index = _Index()
    projection = MemoryIndexProjection(str(path), index)
    gateway = ProductionWriteGateway(str(path), consumers={projection.consumer_name: projection})
    scope = _scope()
    vector = np.asarray([0.1, 0.2, 0.3], dtype=np.float32)

    memory_id = await gateway.append_memory(
        scope=scope,
        group_id="g1",
        content="canonical memory",
        vector=vector,
        sender_id="u1",
        sender_name="user",
        timestamp=time.time(),
        importance=1.0,
        source="chat",
        provenance={},
        origin_metadata={},
        quarantine=False,
        idempotency_hint="stage3-create",
    )
    watermark = await gateway.drain_committed()

    # A raw committed chat Memory stays cold until the canonical scoped Tag
    # transaction succeeds; this prevents unbounded pre-classification growth.
    assert index.added == []
    await gateway.apply_tag_extraction(
        scope=scope,
        memory_id=memory_id,
        tags=[{"name": "canonical", "type": "topic", "confidence": 0.9}],
        status="done",
    )
    watermark = await gateway.drain_committed()
    assert index.added and index.added[-1][0] == [memory_id]
    np.testing.assert_allclose(index.added[-1][1][0], vector)

    changed = await gateway.mutate_memories(
        scope=scope,
        memory_ids=[memory_id],
        action="delete",
        idempotency_hint="stage3-delete",
    )
    assert changed == (memory_id,)
    watermark = await gateway.drain_committed()
    await gateway.save_projection_barrier(watermark)

    assert memory_id in index.deleted
    assert index.saved[-1]["db_watermark"] == watermark
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_hot_capacity_keeps_tagged_memory_cold_and_requests_rebuild(tmp_path):
    path = tmp_path / "capacity.sqlite"
    _prepare_db(path)
    index = _CapacityIndex()
    projection = MemoryIndexProjection(str(path), index)
    gateway = ProductionWriteGateway(str(path), consumers={projection.consumer_name: projection})
    scope = _scope()
    memory_id = await gateway.append_memory(
        scope=scope,
        group_id="g1",
        content="bounded hot candidate",
        vector=np.ones(3, dtype=np.float32),
        sender_id="u1",
        sender_name="user",
        timestamp=time.time(),
        importance=1.0,
        source="chat",
        provenance={},
        origin_metadata={},
        quarantine=False,
        idempotency_hint="capacity-create",
    )
    await gateway.drain_committed()
    await gateway.apply_tag_extraction(
        scope=scope,
        memory_id=memory_id,
        tags=[{"name": "bounded", "type": "topic", "confidence": 0.9}],
        status="done",
    )
    await gateway.drain_committed()

    assert projection.capacity_rebuild_required is True
    assert index.added == []
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_incremental_projection_respects_per_scope_hot_quota(tmp_path):
    path = tmp_path / "scope-quota.sqlite"
    _prepare_db(path)
    index = _Index()
    projection = MemoryIndexProjection(
        str(path),
        index,
        policy=MemoryIndexPolicy(max_vectors=10, per_scope_max_vectors=1),
    )
    gateway = ProductionWriteGateway(str(path), consumers={projection.consumer_name: projection})
    scope = _scope()
    for sequence in (1, 2):
        memory_id = await gateway.append_memory(
            scope=scope,
            group_id="g1",
            content=f"candidate-{sequence}",
            vector=np.ones(3, dtype=np.float32),
            sender_id="u1",
            sender_name="user",
            timestamp=time.time(),
            importance=1.0,
            source="chat",
            provenance={},
            origin_metadata={},
            quarantine=False,
            idempotency_hint=f"scope-quota-create-{sequence}",
        )
        await gateway.drain_committed()
        await gateway.apply_tag_extraction(
            scope=scope,
            memory_id=memory_id,
            tags=[{"name": f"tag-{sequence}", "type": "topic", "confidence": 0.9}],
            status="done",
        )
        await gateway.drain_committed()

    assert [entry[0] for entry in index.added] == [[1]]
    assert projection.capacity_rebuild_required is True
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_tag_index_consumer_checkpoints_scoped_links_without_legacy_vector_guessing():
    index = _Index()
    projection = TagIndexProjection(index)
    event = OutboxEvent(
        event_id="event-1",
        operation_id="operation-1",
        write_sequence=1,
        aggregate_kind="memory",
        aggregate_id="1",
        aggregate_version=2,
        event_type="memory.tags_applied",
        payload_version=1,
        payload={"memory_id": 1, "tag_ids": [10, 11]},
        consumer_name="tag_index",
        attempt=1,
    )

    await projection(event)
    await projection.save_barrier(db_watermark=1)

    assert projection.withheld_count == 2
    assert index.added == []
    assert index.deleted == []
    assert index.saved == []


@pytest.mark.asyncio
async def test_tag_correction_name_payload_is_not_coerced_to_an_integer():
    projection = TagIndexProjection(_Index())
    event = OutboxEvent(
        event_id="event-name-correction",
        operation_id="operation-name-correction",
        write_sequence=1,
        aggregate_kind="memory",
        aggregate_id="1",
        aggregate_version=2,
        event_type="memory.tags_corrected",
        payload_version=1,
        payload={"memory_id": 1, "after_tags": ["人工标签"]},
        consumer_name="tag_index",
        attempt=1,
    )

    await projection(event)

    assert projection.withheld_count == 0


@pytest.mark.asyncio
async def test_memory_versions_increase_so_lifecycle_events_are_not_stale(tmp_path):
    path = tmp_path / "versions.sqlite"
    _prepare_db(path)
    index = _Index()
    projection = MemoryIndexProjection(str(path), index)
    gateway = ProductionWriteGateway(str(path), consumers={projection.consumer_name: projection})
    scope = _scope()

    memory_id = await gateway.append_memory(
        scope=scope,
        group_id="g1",
        content="versioned memory",
        vector=np.ones(3, dtype=np.float32),
        sender_id="u1",
        sender_name="user",
        timestamp=10.0,
        importance=1.0,
        source="chat",
        provenance={},
        origin_metadata={},
        quarantine=False,
        idempotency_hint="version-create",
    )
    await gateway.drain_committed()
    await gateway.mutate_memories(
        scope=scope,
        memory_ids=[memory_id],
        action="evict",
        idempotency_hint="version-evict",
    )
    await gateway.drain_committed()

    connection = sqlite3.connect(path)
    try:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT aggregate_version FROM domain_outbox WHERE aggregate_id=? ORDER BY created_at, event_id",
                (str(memory_id),),
            ).fetchall()
        ]
    finally:
        connection.close()

    assert len(versions) == 2
    assert max(versions) == min(versions) + 1
    assert memory_id in index.deleted
    await gateway.shutdown()
