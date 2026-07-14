"""Recoverable outbox consumers for derived WaveMemory state."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

try:
    from ..engine.db.outbox_repo import OutboxEvent
except ImportError:  # pragma: no cover - repository tests import top-level packages
    from engine.db.outbox_repo import OutboxEvent


_INACTIVE_MEMORY_TYPES = {"archived", "evicted", "deleted"}


def _readonly_uri(database_path: str) -> str:
    return f"{Path(database_path).resolve().as_uri()}?mode=ro"


def _decode_vector(value: Any, dimension: int) -> np.ndarray | None:
    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        vector = np.frombuffer(value, dtype=np.float32)
    elif isinstance(value, str):
        try:
            vector = np.asarray(json.loads(value), dtype=np.float32)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    else:
        try:
            vector = np.asarray(value, dtype=np.float32)
        except (TypeError, ValueError):
            return None
    vector = vector.reshape(-1)
    if vector.size != int(dimension):
        return None
    return vector


class MemoryIndexProjection:
    """Project canonical memory rows into HNSW after the DB transaction commits."""

    consumer_name = "memory_index"

    def __init__(self, database_path: str, index: Any) -> None:
        self.database_path = str(database_path)
        self.index = index
        self._dirty = False
        self._lock = asyncio.Lock()

    def _read_memory(self, memory_id: int):
        connection = sqlite3.connect(_readonly_uri(self.database_path), uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            return connection.execute(
                """SELECT vector, COALESCE(version, 1), COALESCE(quarantine, 0),
                          COALESCE(resolution_state, 'unresolved_legacy'),
                          COALESCE(memory_type, '')
                     FROM memories WHERE id=?""",
                (memory_id,),
            ).fetchone()
        finally:
            connection.close()

    async def __call__(self, event: OutboxEvent) -> None:
        if event.aggregate_kind != "memory":
            return
        memory_id = int(event.aggregate_id)
        async with self._lock:
            row = await asyncio.to_thread(self._read_memory, memory_id)
            if row is None:
                await asyncio.to_thread(self.index.mark_deleted, [memory_id])
                self._dirty = True
                return
            vector_raw, canonical_version, quarantined, resolution_state, memory_type = row
            if int(canonical_version) > int(event.aggregate_version):
                return
            if (
                bool(quarantined)
                or resolution_state != "resolved"
                or memory_type in _INACTIVE_MEMORY_TYPES
            ):
                await asyncio.to_thread(self.index.mark_deleted, [memory_id])
                self._dirty = True
                return
            vector = _decode_vector(vector_raw, int(self.index.dimension))
            if vector is None:
                await asyncio.to_thread(self.index.mark_deleted, [memory_id])
                self._dirty = True
                return
            await asyncio.to_thread(self.index.add, [memory_id], vector.reshape(1, -1))
            self._dirty = True

    async def save_barrier(self, *, db_watermark: int = 0) -> None:
        async with self._lock:
            if not self._dirty:
                return
            save = getattr(self.index, "save", None)
            if callable(save):
                try:
                    await asyncio.to_thread(save, db_watermark=db_watermark)
                except TypeError:
                    await asyncio.to_thread(save)
            self._dirty = False


class TagIndexProjection:
    """Consume Tag events without guessing vectors from legacy unscoped tables.

    Current scoped Tag writes emit memory.tags_applied with IDs but no canonical Tag
    vector/version. Those events are checkpointed and counted as withheld. Future
    scoped_tag vector events can use the explicit payload path below.
    """

    consumer_name = "tag_index"

    def __init__(self, index: Any) -> None:
        self.index = index
        self._dirty = False
        self._lock = asyncio.Lock()
        self.withheld_count = 0

    async def __call__(self, event: OutboxEvent) -> None:
        if event.event_type == "memory.tags_applied":
            self.withheld_count += len(event.payload.get("tag_ids") or ())
            return
        if event.aggregate_kind not in {"tag", "scoped_tag"}:
            return
        tag_id = int(event.payload.get("tag_id") or event.aggregate_id)
        async with self._lock:
            if event.event_type in {"tag.deleted", "tag.merged", "scoped_tag.deleted"}:
                await asyncio.to_thread(self.index.mark_deleted, [tag_id])
                self._dirty = True
                return
            vector = _decode_vector(event.payload.get("vector"), int(self.index.dimension))
            if vector is None:
                self.withheld_count += 1
                return
            await asyncio.to_thread(self.index.add, [tag_id], vector.reshape(1, -1))
            self._dirty = True

    async def save_barrier(self, *, db_watermark: int = 0) -> None:
        async with self._lock:
            if not self._dirty:
                return
            save = getattr(self.index, "save", None)
            if callable(save):
                try:
                    await asyncio.to_thread(save, db_watermark=db_watermark)
                except TypeError:
                    await asyncio.to_thread(save)
            self._dirty = False


class CooccurrenceProjection:
    """Rebuild the in-memory cooccurrence view after committed tag-link changes."""

    consumer_name = "cooccurrence"

    def __init__(self, cooccurrence: Any) -> None:
        self.cooccurrence = cooccurrence
        self._lock = asyncio.Lock()
        self._dirty = False

    async def __call__(self, event: OutboxEvent) -> None:
        if event.event_type not in {
            "memory.tags_applied",
            "memory.deleted",
            "memory.archived",
            "memory.evicted",
            "tag.deleted",
            "tag.merged",
        }:
            return
        async with self._lock:
            await asyncio.to_thread(self.cooccurrence.rebuild)
            self._dirty = True

    async def save_barrier(self, *, db_watermark: int = 0) -> None:
        self._dirty = False


class RuntimeRefreshProjection:
    """Invoke exact-scope runtime/cache refresh callbacks after committed changes."""

    consumer_name = "runtime_refresh"

    def __init__(self, callbacks: dict[str, Any] | None = None) -> None:
        self.callbacks = dict(callbacks or {})
        self._epochs: dict[str, int] = {}

    @staticmethod
    def _epoch_key(event: OutboxEvent) -> str:
        scope = event.payload.get("scope") if isinstance(event.payload, dict) else None
        scope_key = json.dumps(scope or {}, sort_keys=True, separators=(",", ":"))
        return f"{event.aggregate_kind}:{scope_key}"

    def epoch(self, aggregate_kind: str, scope: dict[str, Any] | None = None) -> int:
        scope_key = json.dumps(scope or {}, sort_keys=True, separators=(",", ":"))
        return self._epochs.get(f"{aggregate_kind}:{scope_key}", 0)

    async def __call__(self, event: OutboxEvent) -> None:
        key = self._epoch_key(event)
        self._epochs[key] = self._epochs.get(key, 0) + 1
        callback = self.callbacks.get(event.aggregate_kind)
        if callback is None:
            return
        result = callback(event)
        if hasattr(result, "__await__"):
            await result

    async def save_barrier(self, *, db_watermark: int = 0) -> None:
        return
