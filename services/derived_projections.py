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
    from ..engine.vector_index import IndexCapacityError
    from .memory_index_policy import MemoryIndexPolicy, evaluate_memory_eligibility
except ImportError:  # pragma: no cover - repository tests import top-level packages
    from engine.db.outbox_repo import OutboxEvent
    from engine.vector_index import IndexCapacityError
    from services.memory_index_policy import MemoryIndexPolicy, evaluate_memory_eligibility


_INACTIVE_MEMORY_TYPES = {"archived", "evicted", "deleted"}


def _readonly_uri(database_path: str) -> str:
    return f"{Path(database_path).resolve().as_uri()}?mode=ro"


def _positive_ints(values: Any) -> list[int]:
    """Decode only positive numeric IDs; correction payloads may carry names."""
    result: list[int] = []
    for value in values or ():
        if isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result.append(parsed)
    return result


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
    """Project policy-admitted canonical memories into the bounded hot HNSW tier."""

    consumer_name = "memory_index"

    def __init__(
        self,
        database_path: str,
        index: Any,
        *,
        policy: MemoryIndexPolicy | None = None,
    ) -> None:
        self.database_path = str(database_path)
        self.index = index
        self.policy = policy or MemoryIndexPolicy()
        self._dirty = False
        self._lock = asyncio.Lock()
        self._capacity_rebuild_required = False
        # Keep lane and quota metadata separate: legacy group rows have a stable
        # group compatibility key but deliberately no fabricated formal Scope.
        self._hot_memory_lanes: dict[int, str] = {}
        self._hot_memory_scopes: dict[int, tuple[str, str, str, str]] = {}
        self._scope_counts: dict[tuple[str, str, str, str], int] = {}
        self._membership_loaded = False
        self._membership_safe = True

    @property
    def capacity_rebuild_required(self) -> bool:
        """Whether an incremental admission hit the fixed physical capacity."""
        return self._capacity_rebuild_required

    def clear_capacity_rebuild_required(self) -> None:
        self._capacity_rebuild_required = False

    @staticmethod
    def _canonical_scope_key(row: tuple[Any, ...]) -> tuple[str, str, str, str] | None:
        bot_id, session_id, visibility, group_id = (str(value or "").strip() for value in row)
        if not bot_id or not session_id or not group_id or visibility != "group":
            return None
        parts = session_id.split(":", 2)
        if len(parts) != 3 or not parts[0] or parts[1] != "group" or parts[2] != group_id:
            return None
        return bot_id, session_id, visibility, group_id

    @staticmethod
    def _legacy_group_member(row: tuple[Any, ...]) -> bool:
        bot_id, session_id, visibility, group_id = (str(value or "").strip() for value in row)
        return bool(group_id) and not any((bot_id, session_id, visibility))

    def _decrement_member(self, memory_id: int) -> None:
        memory_id = int(memory_id)
        self._hot_memory_lanes.pop(memory_id, None)
        scope_key = self._hot_memory_scopes.pop(memory_id, None)
        if scope_key is None:
            return
        remaining = self._scope_counts.get(scope_key, 0) - 1
        if remaining > 0:
            self._scope_counts[scope_key] = remaining
        else:
            self._scope_counts.pop(scope_key, None)

    def _set_member(
        self,
        memory_id: int,
        *,
        lane: str,
        scope_key: tuple[str, str, str, str] | None,
    ) -> None:
        memory_id = int(memory_id)
        previous_lane = self._hot_memory_lanes.get(memory_id)
        previous_scope = self._hot_memory_scopes.get(memory_id)
        if previous_lane == lane and previous_scope == scope_key:
            return
        if previous_lane is not None:
            self._decrement_member(memory_id)
        self._hot_memory_lanes[memory_id] = lane
        if scope_key is not None:
            self._hot_memory_scopes[memory_id] = scope_key
            self._scope_counts[scope_key] = self._scope_counts.get(scope_key, 0) + 1

    def _ensure_membership_cache(self) -> None:
        """Hydrate existing HNSW labels once so per-Scope quotas survive restart."""
        if self._membership_loaded:
            return
        self._membership_loaded = True
        get_ids = getattr(getattr(self.index, "index", None), "get_ids_list", None)
        if not callable(get_ids):
            return
        try:
            ids = [int(value) for value in get_ids() if int(value) > 0]
        except Exception:
            self._membership_safe = False
            return
        if not ids:
            return
        connection = None
        try:
            connection = sqlite3.connect(_readonly_uri(self.database_path), uri=True)
            connection.execute("PRAGMA query_only=ON")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)").fetchall()}
            if not {"id", "group_id"} <= columns:
                self._membership_safe = False
                return
            bot_id = "bot_id" if "bot_id" in columns else "''"
            session_id = "session_id" if "session_id" in columns else "''"
            visibility = "visibility" if "visibility" in columns else "''"
            for offset in range(0, len(ids), 500):
                chunk = ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""SELECT id, {bot_id}, {session_id}, {visibility}, group_id
                          FROM memories WHERE id IN ({placeholders})""",
                    chunk,
                ).fetchall()
                for row in rows:
                    values = tuple(row[1:])
                    scope_key = self._canonical_scope_key(values)
                    if scope_key is not None:
                        self._set_member(int(row[0]), lane="scoped", scope_key=scope_key)
                    elif self._legacy_group_member(values):
                        self._set_member(int(row[0]), lane="legacy_group", scope_key=None)
        except Exception:
            self._membership_safe = False
        finally:
            if connection is not None:
                connection.close()

    def set_hot_membership(self, candidates: Any) -> None:
        """Replace lane/quota membership after an authoritative rebuild."""
        self._hot_memory_lanes.clear()
        self._hot_memory_scopes.clear()
        self._scope_counts.clear()
        for candidate in candidates or ():
            try:
                raw_scope = getattr(candidate, "scope_key", None)
                scope_key = tuple(raw_scope) if raw_scope is not None else None
                if scope_key is not None and len(scope_key) != 4:
                    continue
                self._set_member(
                    int(candidate.memory_id),
                    lane=str(getattr(candidate, "recall_visibility", "scoped")),
                    scope_key=scope_key,  # type: ignore[arg-type]
                )
            except (AttributeError, TypeError, ValueError):
                continue
        self._membership_loaded = True
        self._membership_safe = True

    def _read_memory_admission(self, memory_id: int):
        connection = sqlite3.connect(_readonly_uri(self.database_path), uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)").fetchall()}
            version = "COALESCE(version, 1)" if "version" in columns else "1"
            row = connection.execute(
                f"SELECT {version} FROM memories WHERE id=?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return None, None
            candidate = evaluate_memory_eligibility(
                connection,
                memory_id,
                self.policy,
                int(self.index.dimension),
            )
            return int(row[0] or 1), candidate
        finally:
            connection.close()

    async def __call__(self, event: OutboxEvent) -> None:
        if event.aggregate_kind != "memory":
            return
        memory_id = int(event.aggregate_id)
        async with self._lock:
            canonical_version, candidate = await asyncio.to_thread(
                self._read_memory_admission,
                memory_id,
            )
            self._ensure_membership_cache()
            if canonical_version is None:
                self._decrement_member(memory_id)
                await asyncio.to_thread(self.index.mark_deleted, [memory_id])
                self._dirty = True
                return
            if int(canonical_version) > int(event.aggregate_version):
                return
            if candidate is None:
                # A creation without effective Tags, a demotion, or an inactive
                # lifecycle state must remove any stale hot label.  The canonical
                # row remains available to the bounded cold-retrieval path.
                self._decrement_member(memory_id)
                await asyncio.to_thread(self.index.mark_deleted, [memory_id])
                self._dirty = True
                return
            scope_key = candidate.scope_key
            previous_scope = self._hot_memory_scopes.get(memory_id)
            if not self._membership_safe:
                # Do not guess when a persisted index could not be mapped back to
                # its lane/quota state; wait for the durable selector to reconcile it.
                self._capacity_rebuild_required = True
                return
            if (
                scope_key is not None
                and previous_scope != scope_key
                and (
                    self.policy.per_scope_max_vectors <= 0
                    or self._scope_counts.get(scope_key, 0) >= self.policy.per_scope_max_vectors
                )
            ):
                self._capacity_rebuild_required = True
                return
            if candidate.vector is None:
                self._decrement_member(memory_id)
                await asyncio.to_thread(self.index.mark_deleted, [memory_id])
                self._dirty = True
                return
            try:
                await asyncio.to_thread(
                    self.index.add,
                    [memory_id],
                    candidate.vector.reshape(1, -1),
                )
            except IndexCapacityError:
                # Preserve the hard memory ceiling.  A coalesced durable rebuild
                # will select the highest-priority set; this candidate is cold in
                # the meantime rather than forcing an unbounded resize.
                self._capacity_rebuild_required = True
                return
            self._set_member(
                memory_id,
                lane=str(getattr(candidate, "recall_visibility", "scoped")),
                scope_key=scope_key,
            )
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

    def __init__(self, index: Any, database_path: str | None = None) -> None:
        self.index = index
        self.database_path = str(database_path or "")
        self._dirty = False
        self._lock = asyncio.Lock()
        self.withheld_count = 0

    def _read_catalog_vector(self, catalog_id: int) -> np.ndarray | None:
        if not self.database_path:
            return None
        connection = sqlite3.connect(_readonly_uri(self.database_path), uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT embedding, status FROM tag_catalog WHERE id=?",
                (int(catalog_id),),
            ).fetchone()
            if row is None or str(row[1] or "active") != "active":
                return None
            return _decode_vector(row[0], int(self.index.dimension))
        except Exception:
            return None
        finally:
            connection.close()

    def _read_catalog_ids_for_scoped_tags(self, tag_ids: list[int]) -> list[int]:
        if not self.database_path or not tag_ids:
            return []
        connection = sqlite3.connect(_readonly_uri(self.database_path), uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            placeholders = ",".join("?" for _ in tag_ids)
            rows = connection.execute(
                f"SELECT DISTINCT catalog_id FROM scoped_tags WHERE id IN ({placeholders}) AND catalog_id IS NOT NULL",
                [int(value) for value in tag_ids],
            ).fetchall()
            return [int(row[0]) for row in rows if row[0] is not None]
        except Exception:
            return []
        finally:
            connection.close()

    async def __call__(self, event: OutboxEvent) -> None:
        if event.event_type in {
            "memory.tags_applied",
            "memory.tags_corrected",
            "memory.tags_correction_undone",
        }:
            raw_tag_ids = _positive_ints(
                event.payload.get("tag_ids") or event.payload.get("after_tags") or ()
            )
            raw_catalog_ids = _positive_ints(event.payload.get("catalog_ids") or ())
            if not raw_catalog_ids:
                raw_catalog_ids = self._read_catalog_ids_for_scoped_tags(raw_tag_ids)
            if not self.database_path or not raw_catalog_ids:
                self.withheld_count += len(raw_tag_ids)
                return
            added_ids: list[int] = []
            async with self._lock:
                for catalog_id in dict.fromkeys(raw_catalog_ids):
                    vector = await asyncio.to_thread(self._read_catalog_vector, catalog_id)
                    if vector is None:
                        self.withheld_count += 1
                        continue
                    await asyncio.to_thread(self.index.add, [catalog_id], vector.reshape(1, -1))
                    added_ids.append(catalog_id)
                if added_ids:
                    self._dirty = True
            if not added_ids:
                self.withheld_count += len(raw_tag_ids)
            return
        governance_event = (
            event.aggregate_kind == "tag_audit_suggestion"
            and event.event_type in {
                "tag.merge",
                "tag.deactivate",
                "tag.governance.applied",
                "tag.governance.compensated",
            }
        )
        if event.aggregate_kind not in {"tag", "scoped_tag"} and not governance_event:
            return
        if governance_event:
            impact = event.payload.get("impact") if isinstance(event.payload, dict) else {}
            removed = impact.get("removed_tag_ids") if isinstance(impact, dict) else ()
            if removed:
                async with self._lock:
                    await asyncio.to_thread(self.index.mark_deleted, [int(value) for value in removed])
                    self._dirty = True
            else:
                self.withheld_count += len(impact.get("related_tag_ids") or ()) if isinstance(impact, dict) else 1
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
            "memory.tags_corrected",
            "memory.tags_correction_undone",
            "memory.deleted",
            "memory.archived",
            "memory.evicted",
            "tag.deleted",
            "tag.merged",
            "tag.merge",
            "tag.deactivate",
            "tag.governance.applied",
            "tag.governance.compensated",
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
