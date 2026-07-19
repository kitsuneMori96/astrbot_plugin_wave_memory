"""Read-only policy for bounded, tag-driven memory HNSW admission.

The policy deliberately reads canonical memory and scoped-tag tables only.  It
never creates projection tables or changes SQLite state, so it is safe to use
while rebuilding a derived index from a read connection.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

try:
    from ..engine.db.scoped_tag_projection import effective_tag_rows
except ImportError:  # pragma: no cover - direct package imports in focused tests
    from engine.db.scoped_tag_projection import effective_tag_rows


_INACTIVE_MEMORY_TYPES = frozenset({"archived", "evicted", "deleted"})
# Legacy ``evicted`` is a historical old-HNSW residency marker, not a canonical
# deletion. Compatibility recall keeps it while still excluding explicit delete/noise.
_LEGACY_EXCLUDED_MEMORY_TYPES = frozenset({"deleted", "noise"})
_DURABLE_SOURCES = frozenset({"bzz_experience", "book_lore", "oni_lore"})
_DURABLE_MEMORY_TYPES = frozenset({"experience", "knowledge"})
_REQUIRED_MEMORY_COLUMNS = frozenset({"id", "vector", "group_id"})


@dataclass(frozen=True, slots=True)
class MemoryIndexPolicy:
    """Bounds and freshness controls for the shared memory HNSW index."""

    max_vectors: int = 100_000
    per_scope_max_vectors: int = 1_000
    scoped_reserved_vectors: int = 10_000
    chat_hot_days: int = 30
    candidate_limit: int = 128


@dataclass(frozen=True, slots=True)
class HotMemoryCandidate:
    """A validated vector that was admitted to the bounded index rebuild set."""

    memory_id: int
    vector: np.ndarray | None
    score: float
    bot_id: str
    session_id: str
    visibility: str
    group_id: str
    durable: bool
    tag_count: int
    tag_relevance: float
    recall_visibility: str = "scoped"
    enforce_scope_quota: bool = True

    @property
    def scope_key(self) -> tuple[str, str, str, str] | None:
        """Return the quota key only for formally scoped memory."""
        if not self.enforce_scope_quota:
            return None
        return self.bot_id, self.session_id, self.visibility, self.group_id


def decode_vector(value: Any, dimension: int) -> np.ndarray | None:
    """Decode one finite float32 vector with exactly ``dimension`` entries."""
    try:
        expected_dimension = int(dimension)
    except (TypeError, ValueError):
        return None
    if value is None or expected_dimension <= 0:
        return None
    try:
        if isinstance(value, memoryview):
            value = value.tobytes()
        if isinstance(value, bytes):
            if len(value) != expected_dimension * np.dtype(np.float32).itemsize:
                return None
            vector = np.frombuffer(value, dtype=np.float32)
        elif isinstance(value, str):
            vector = np.asarray(json.loads(value), dtype=np.float32)
        else:
            vector = np.asarray(value, dtype=np.float32)
        vector = vector.reshape(-1)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if vector.size != expected_dimension or not np.isfinite(vector).all():
        return None
    return vector


def _table_columns(connection: Any, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _canonical_scope(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    bot_id = _text(row.get("bot_id"))
    session_id = _text(row.get("session_id"))
    visibility = _text(row.get("visibility"))
    group_id = _text(row.get("group_id"))
    if not bot_id or not session_id or not group_id or visibility != "group":
        return None
    parts = session_id.split(":", 2)
    if len(parts) != 3 or not parts[0] or parts[1] != "group" or parts[2] != group_id:
        return None
    return bot_id, session_id, visibility, group_id


def _is_legacy_group_row(row: dict[str, Any]) -> bool:
    """Recognize only fully unscoped legacy rows; partial modern scope fails closed."""
    if not _text(row.get("group_id")):
        return False
    return not any(_text(row.get(name)) for name in ("bot_id", "session_id", "visibility"))


def _scoped_effective_tags_by_memory(
    connection: Any,
    *,
    memory_id: int | None = None,
) -> dict[tuple[str, str, str, int], list[dict[str, Any]]]:
    """Read canonical scoped effective tags without ever falling back to legacy rows."""
    required = {"bot_id", "session_id", "visibility", "memory_id", "tag_id"}
    if not required <= _table_columns(connection, "scoped_memory_tags"):
        return {}
    if not required - {"memory_id", "tag_id"} <= _table_columns(connection, "scoped_tags"):
        return {}
    try:
        rows = effective_tag_rows(connection, memory_id=memory_id)
    except Exception:
        return {}
    grouped: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
    for tag in rows:
        try:
            key = (
                _text(tag.get("bot_id")),
                _text(tag.get("session_id")),
                _text(tag.get("visibility")),
                int(tag["memory_id"]),
            )
            int(tag["tag_id"])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if not all(key[:3]):
            continue
        grouped.setdefault(key, []).append(tag)
    return grouped


def _legacy_tags_by_memory(
    connection: Any,
    *,
    memory_id: int | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Read global legacy tag links solely for explicitly unscoped legacy memory."""
    memory_tag_columns = _table_columns(connection, "memory_tags")
    tag_columns = _table_columns(connection, "tags")
    if not {"memory_id", "tag_id"} <= memory_tag_columns or "id" not in tag_columns:
        return {}
    relevance = "COALESCE(mt.relevance, 1.0)" if "relevance" in memory_tag_columns else "1.0"
    where = ""
    params: tuple[Any, ...] = ()
    if memory_id is not None:
        try:
            where = " WHERE mt.memory_id=?"
            params = (int(memory_id),)
        except (TypeError, ValueError):
            return {}
    try:
        rows = connection.execute(
            f"""SELECT mt.memory_id, mt.tag_id, {relevance} AS relevance
                   FROM memory_tags mt JOIN tags t ON t.id=mt.tag_id{where}""",
            params,
        ).fetchall()
    except Exception:
        return {}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for raw_memory_id, raw_tag_id, raw_relevance in rows:
        try:
            key = int(raw_memory_id)
            grouped.setdefault(key, []).append({
                "tag_id": int(raw_tag_id),
                "relevance": _number(raw_relevance, 1.0),
            })
        except (TypeError, ValueError):
            continue
    return grouped


def _read_memory_rows(
    connection: Any,
    *,
    memory_id: int | None = None,
    include_vector: bool = True,
) -> list[dict[str, Any]]:
    columns = _table_columns(connection, "memories")
    if not _REQUIRED_MEMORY_COLUMNS <= columns:
        return []

    def expression(name: str, fallback: str) -> str:
        return name if name in columns else fallback

    where_clause = ""
    params: tuple[Any, ...] = ()
    if memory_id is not None:
        try:
            where_clause = " WHERE id=?"
            params = (int(memory_id),)
        except (TypeError, ValueError):
            return []
    vector_expression = "vector" if include_vector else "NULL"
    vector_bytes_expression = "length(vector)" if "vector" in columns else "0"
    rows = connection.execute(
        f"""SELECT id, {vector_expression} AS vector, {vector_bytes_expression} AS vector_bytes,
                   {expression('bot_id', "''")} AS bot_id,
                   {expression('session_id', "''")} AS session_id,
                   {expression('visibility', "''")} AS visibility,
                   group_id,
                   {expression('resolution_state', "''")} AS resolution_state,
                   {expression('quarantine', '0')} AS quarantine,
                   {expression('source', "''")} AS source,
                   {expression('memory_type', "''")} AS memory_type,
                   {expression('importance', '1.0')} AS importance,
                   {expression('access_count', '0')} AS access_count,
                   {expression('timestamp', '0.0')} AS timestamp
              FROM memories{where_clause}""",
        params,
    ).fetchall()
    names = (
        "id", "vector", "vector_bytes", "bot_id", "session_id", "visibility", "group_id",
        "resolution_state", "quarantine", "source", "memory_type", "importance",
        "access_count", "timestamp",
    )
    return [dict(zip(names, row)) for row in rows]


def _score(*, durable: bool, importance: float, tag_relevance: float,
           tag_count: int, access_count: float, timestamp: float, now: float) -> float:
    """Stable relevance score; a durable bonus preserves long-lived knowledge."""
    age_days = max(0.0, (now - timestamp) / 86_400.0)
    recency = max(0.0, 1.0 - min(age_days, 365.0) / 365.0)
    return (
        (100.0 if durable else 0.0)
        + max(0.0, importance) * 10.0
        + max(0.0, tag_relevance) * 4.0
        + min(max(0, tag_count), 32) * 1.5
        + math.log1p(max(0.0, access_count)) * 2.0
        + recency
    )


def _eligible_candidates(
    connection: Any,
    policy: MemoryIndexPolicy,
    dimension: int,
    now: float,
    *,
    memory_id: int | None = None,
    include_vectors: bool = True,
) -> list[HotMemoryCandidate]:
    scoped_tags = _scoped_effective_tags_by_memory(connection, memory_id=memory_id)
    legacy_tags = _legacy_tags_by_memory(connection, memory_id=memory_id)
    candidates: list[HotMemoryCandidate] = []
    for row in _read_memory_rows(
        connection,
        memory_id=memory_id,
        include_vector=include_vectors,
    ):
        resolution_state = _text(row.get("resolution_state")).casefold()
        if resolution_state not in {"", "resolved"} or _truthy(row.get("quarantine")):
            continue
        source = _text(row.get("source")).casefold()
        memory_type = _text(row.get("memory_type")).casefold()
        if source == "noise" or memory_type == "noise":
            continue
        vector: np.ndarray | None
        if include_vectors:
            vector = decode_vector(row.get("vector"), dimension)
            if vector is None:
                continue
        else:
            # Keep the first, metadata-only ranking pass bounded.  Full finite
            # validation happens when only the selected rows are hydrated.
            try:
                vector_bytes = int(row.get("vector_bytes") or 0)
            except (TypeError, ValueError):
                vector_bytes = 0
            if vector_bytes != int(dimension) * np.dtype(np.float32).itemsize:
                continue
            vector = None
        try:
            candidate_id = int(row["id"])
        except (TypeError, ValueError):
            continue

        scope = _canonical_scope(row)
        if scope is not None:
            if memory_type in _INACTIVE_MEMORY_TYPES:
                continue
            tags = scoped_tags.get((*scope[:3], candidate_id), [])
            recall_visibility = "scoped"
            enforce_scope_quota = True
            bot_id, session_id, visibility, group_id = scope
        elif _is_legacy_group_row(row):
            if memory_type in _LEGACY_EXCLUDED_MEMORY_TYPES:
                continue
            tags = legacy_tags.get(candidate_id, [])
            recall_visibility = "legacy_group"
            enforce_scope_quota = False
            bot_id = session_id = visibility = ""
            group_id = _text(row.get("group_id"))
        else:
            # A partially populated modern scope must never be downgraded to a
            # legacy lane merely because one field is malformed or missing.
            continue
        if not tags:
            continue

        tag_relevance = sum(max(0.0, _number(tag.get("relevance"), 1.0)) for tag in tags)
        durable = source in _DURABLE_SOURCES or memory_type in _DURABLE_MEMORY_TYPES
        timestamp = _number(row.get("timestamp"))
        if (
            recall_visibility == "scoped"
            and not durable
            and now - timestamp > max(0, int(policy.chat_hot_days)) * 86_400
        ):
            continue
        candidates.append(HotMemoryCandidate(
            memory_id=candidate_id,
            vector=vector,
            score=_score(
                durable=durable,
                importance=_number(row.get("importance"), 1.0),
                tag_relevance=tag_relevance,
                tag_count=len(tags),
                access_count=_number(row.get("access_count")),
                timestamp=timestamp,
                now=now,
            ),
            bot_id=bot_id,
            session_id=session_id,
            visibility=visibility,
            group_id=group_id,
            durable=durable,
            tag_count=len(tags),
            tag_relevance=tag_relevance,
            recall_visibility=recall_visibility,
            enforce_scope_quota=enforce_scope_quota,
        ))
    return candidates


def _hydrate_candidate_vectors(
    connection: Any,
    candidates: list[HotMemoryCandidate],
    dimension: int,
) -> list[HotMemoryCandidate]:
    """Load vectors only after metadata ranking has enforced the hard quota."""
    if not candidates:
        return []
    by_id: dict[int, np.ndarray] = {}
    ids = [candidate.memory_id for candidate in candidates]
    for offset in range(0, len(ids), 500):
        chunk = ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        try:
            rows = connection.execute(
                f"SELECT id, vector FROM memories WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
        except Exception:
            continue
        for row in rows:
            try:
                vector = decode_vector(row[1], dimension)
                if vector is not None:
                    by_id[int(row[0])] = vector
            except (IndexError, TypeError, ValueError):
                continue
    return [
        replace(candidate, vector=vector)
        for candidate in candidates
        if (vector := by_id.get(candidate.memory_id)) is not None
    ]


def select_hot_memory_candidates(
    connection: Any,
    policy: MemoryIndexPolicy,
    dimension: int,
    now: float | None = None,
) -> list[HotMemoryCandidate]:
    """Return deterministic candidates from scoped and legacy-group lanes.

    Scope remains the access and quota boundary for modern rows. Fully unscoped
    legacy group rows may use their existing tags as semantic evidence without
    consuming a synthetic Scope quota; a bounded reservation prevents that
    legacy corpus from starving the modern lane.
    """
    timestamp = float(time.time() if now is None else now)
    try:
        candidates = _eligible_candidates(
            connection,
            policy,
            int(dimension),
            timestamp,
            include_vectors=False,
        )
    except (TypeError, ValueError, OverflowError):
        return []
    ranked = sorted(candidates, key=lambda item: (-item.score, item.memory_id))
    per_scope_limit = max(0, int(policy.per_scope_max_vectors))
    global_limit = max(0, int(policy.max_vectors))
    if not global_limit:
        return []

    admitted: list[HotMemoryCandidate] = []
    admitted_ids: set[int] = set()
    scope_counts: dict[tuple[str, str, str, str], int] = {}

    def admit(candidate: HotMemoryCandidate) -> bool:
        scope_key = candidate.scope_key
        if scope_key is not None:
            if not per_scope_limit or scope_counts.get(scope_key, 0) >= per_scope_limit:
                return False
            scope_counts[scope_key] = scope_counts.get(scope_key, 0) + 1
        admitted.append(candidate)
        admitted_ids.add(candidate.memory_id)
        return True

    # First preserve a fixed slice for formally scoped rows. ``0`` intentionally
    # disables the reservation but does not disable the scoped lane itself.
    reservation = min(global_limit, max(0, int(policy.scoped_reserved_vectors)))
    for candidate in ranked:
        if len(admitted) >= reservation:
            break
        if candidate.scope_key is not None:
            admit(candidate)

    # Then fill the remaining hard global capacity in one rank order. Legacy rows
    # never inherit the per-Scope 1,000 cap because they have no formal Scope.
    for candidate in ranked:
        if len(admitted) >= global_limit:
            break
        if candidate.memory_id in admitted_ids:
            continue
        admit(candidate)
    return _hydrate_candidate_vectors(connection, admitted, int(dimension))


def evaluate_memory_eligibility(
    connection: Any,
    memory_id: int,
    policy: MemoryIndexPolicy,
    dimension: int,
    now: float | None = None,
) -> HotMemoryCandidate | None:
    """Evaluate one Memory without scanning global quota membership.

    Incremental outbox projection uses this fast gate.  Exact per-Scope/global
    ranking is reconciled by the durable rebuild selector above, while a hard
    HNSW capacity prevents any temporary memory growth.
    """
    try:
        expected_id = int(memory_id)
        timestamp = float(time.time() if now is None else now)
    except (TypeError, ValueError):
        return None
    try:
        candidates = _eligible_candidates(
            connection,
            policy,
            int(dimension),
            timestamp,
            memory_id=expected_id,
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return candidates[0] if candidates else None


def evaluate_memory_admission(
    connection: Any,
    memory_id: int,
    policy: MemoryIndexPolicy,
    dimension: int,
    now: float | None = None,
) -> HotMemoryCandidate | None:
    """Return the quota-admitted candidate for one memory, or ``None`` if rejected."""
    try:
        expected_id = int(memory_id)
    except (TypeError, ValueError):
        return None
    return next(
        (candidate for candidate in select_hot_memory_candidates(connection, policy, dimension, now)
         if candidate.memory_id == expected_id),
        None,
    )


__all__ = [
    "HotMemoryCandidate",
    "MemoryIndexPolicy",
    "decode_vector",
    "evaluate_memory_admission",
    "evaluate_memory_eligibility",
    "select_hot_memory_candidates",
]
