"""Scoped Memory Tag 的 automatic/effective projection helpers。

``scoped_memory_tags`` 始终表示自动基线；人工 correction 通过
``scoped_memory_tag_corrections`` 叠加到 effective 读取面。该模块同时维护一个
可恢复的 materialized projection，读取时在 projection 未 backfill 的情况下仍可
从 canonical 表安全计算，避免把 pending 误报为空。
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

try:
    from ...domain.scope import RuntimeScope
except ImportError:  # pragma: no cover - focused tests import top-level packages
    from domain.scope import RuntimeScope


_SCOPE_COLUMNS = ("bot_id", "session_id", "visibility")


class ScopedTagProjectionError(ValueError):
    """Stable projection validation error."""


_EFFECTIVE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS scoped_memory_effective_tags ("
    "bot_id TEXT NOT NULL, session_id TEXT NOT NULL, visibility TEXT NOT NULL, "
    "memory_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, position INTEGER NOT NULL DEFAULT 0, "
    "relevance REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL, correction_id TEXT, "
    "projection_revision INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL, "
    "PRIMARY KEY (bot_id, session_id, visibility, memory_id, tag_id))",
    "CREATE TABLE IF NOT EXISTS scoped_tag_projection_state ("
    "bot_id TEXT NOT NULL, session_id TEXT NOT NULL, visibility TEXT NOT NULL, "
    "state TEXT NOT NULL, projection_revision INTEGER NOT NULL DEFAULT 0, "
    "cursor_memory_id INTEGER, last_error TEXT, updated_at REAL NOT NULL, "
    "PRIMARY KEY (bot_id, session_id, visibility))",
    "CREATE INDEX IF NOT EXISTS idx_scoped_memory_effective_tags_scope_memory "
    "ON scoped_memory_effective_tags (bot_id, session_id, visibility, memory_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_scoped_memory_effective_tags_scope_tag "
    "ON scoped_memory_effective_tags (bot_id, session_id, visibility, tag_id, memory_id)",
)


def _scope_tuple(scope: RuntimeScope) -> tuple[str, str, str]:
    if not isinstance(scope, RuntimeScope) or scope.visibility != "group" or scope.session is None:
        raise ScopedTagProjectionError("scope_required")
    return scope.bot_id, scope.session.id, scope.visibility


def _table_columns(connection: Any, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def ensure_projection_tables(connection: Any) -> None:
    """Idempotently create projection tables on a writer-owned connection."""
    for statement in _EFFECTIVE_SCHEMA:
        connection.execute(statement)


def _scope_predicate(scope: RuntimeScope | None, *, alias: str = "") -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    if scope is None:
        return "", []
    values = _scope_tuple(scope)
    return (
        f" AND {prefix}bot_id=? AND {prefix}session_id=? AND {prefix}visibility=?",
        list(values),
    )


def _decode_names(value: Any) -> tuple[str, ...]:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(decoded, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in decoded:
        name = str(item or "").strip()
        key = name.casefold()
        if name and key not in seen:
            result.append(name)
            seen.add(key)
    return tuple(result)


def _active_corrections(connection: Any, scope: RuntimeScope | None = None) -> dict[tuple[str, str, str, int], dict[str, Any]]:
    if not _table_columns(connection, "scoped_memory_tag_corrections"):
        return {}
    where, params = _scope_predicate(scope)
    rows = connection.execute(
        f"""SELECT correction_id, bot_id, session_id, visibility, memory_id,
                          after_tags_json, correction_revision, created_at
                     FROM scoped_memory_tag_corrections
                    WHERE status='active'{where}
                    ORDER BY created_at ASC, rowid ASC""",
        params,
    ).fetchall()
    result: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row[1]), str(row[2]), str(row[3]), int(row[4]))
        result[key] = {
            "correction_id": str(row[0]),
            "names": _decode_names(row[5]),
            "revision": int(row[6] or 1),
        }
    return result


def _normalized_tag_ids(tag_ids: Iterable[Any] | None) -> list[int] | None:
    """Return a bounded, de-duplicated positive tag id filter, or None."""
    if tag_ids is None:
        return None
    normalized: set[int] = set()
    for value in tag_ids:
        if isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            normalized.add(parsed)
    # An explicitly empty filter means "no tag matches"; keep it distinct from None.
    return sorted(normalized)


def _automatic_rows(
    connection: Any,
    scope: RuntimeScope | None = None,
    memory_id: int | None = None,
    tag_ids: Iterable[Any] | None = None,
) -> list[tuple[Any, ...]]:
    tag_columns = _table_columns(connection, "scoped_tags")
    memory_tag_columns = _table_columns(connection, "scoped_memory_tags")
    status_expression = "COALESCE(t.status, 'active')" if "status" in tag_columns else "'active'"
    position_expression = "mt.position" if "position" in memory_tag_columns else "0"
    relevance_expression = "mt.relevance" if "relevance" in memory_tag_columns else "1.0"
    order_position_expression = position_expression if "position" in memory_tag_columns else "mt.tag_id"
    where, params = _scope_predicate(scope, alias="mt")
    memory_clause = ""
    if memory_id is not None:
        memory_clause = " AND mt.memory_id=?"
        params.append(int(memory_id))
    # Push the semantic tag filter into SQL. Cold recall only ever needs the rows
    # for a handful of candidate tags; scanning every scoped link was the main
    # source of multi-second injection latency.
    tag_clause = ""
    normalized_tags = _normalized_tag_ids(tag_ids)
    if normalized_tags is not None:
        if not normalized_tags:
            return []
        placeholders = ",".join("?" for _ in normalized_tags)
        tag_clause = f" AND mt.tag_id IN ({placeholders})"
        params.extend(normalized_tags)
    return connection.execute(
        f"""SELECT mt.bot_id, mt.session_id, mt.visibility, mt.memory_id,
                          mt.tag_id, {position_expression}, {relevance_expression}, t.name, {status_expression}
                     FROM scoped_memory_tags mt
                     JOIN scoped_tags t
                       ON t.id=mt.tag_id AND t.bot_id=mt.bot_id
                      AND t.session_id=mt.session_id AND t.visibility=mt.visibility
                    WHERE 1=1{where}{memory_clause}{tag_clause}
                    ORDER BY mt.bot_id, mt.session_id, mt.visibility, mt.memory_id,
                                 {order_position_expression}, mt.tag_id""",

        params,
    ).fetchall()


def _tag_rows_by_name(connection: Any, scope: RuntimeScope, names: Iterable[str]) -> dict[str, tuple[Any, ...]]:
    names = tuple(names)
    if not names:
        return {}
    columns = _table_columns(connection, "scoped_tags")
    status_expression = "COALESCE(status, 'active')" if "status" in columns else "'active'"
    bot_id, session_id, visibility = _scope_tuple(scope)
    placeholders = ",".join("?" for _ in names)
    rows = connection.execute(
        f"""SELECT id, name, tag_type, {status_expression}
                     FROM scoped_tags
                    WHERE bot_id=? AND session_id=? AND visibility=?
                      AND name IN ({placeholders})""",
        (bot_id, session_id, visibility, *names),
    ).fetchall()
    return {str(row[1]).casefold(): row for row in rows}


def _effective_from_rows(
    connection: Any,
    automatic_rows: Iterable[tuple[Any, ...]],
    corrections: Mapping[tuple[str, str, str, int], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, int], list[tuple[Any, ...]]] = defaultdict(list)
    for row in automatic_rows:
        scope_key = (str(row[0]), str(row[1]), str(row[2]), int(row[3]))
        grouped[scope_key].append(row)

    tag_cache: dict[tuple[str, str, str], dict[str, tuple[Any, ...]]] = {}
    result: list[dict[str, Any]] = []
    memory_keys = set(grouped) | set(corrections)
    for scope_key in sorted(memory_keys):
        bot_id, session_id, visibility, memory_id = scope_key
        correction = corrections.get(scope_key)
        if correction is None:
            for row in grouped.get(scope_key, ()):
                if str(row[8] or "active") != "active":
                    continue
                result.append(
                    {
                        "bot_id": bot_id,
                        "session_id": session_id,
                        "visibility": visibility,
                        "memory_id": memory_id,
                        "tag_id": int(row[4]),
                        "position": int(row[5] or 0),
                        "relevance": float(row[6] if row[6] is not None else 1.0),
                        "source": "automatic",
                        "correction_id": None,
                    }
                )
            continue

        scope_key_without_memory = (bot_id, session_id, visibility)
        if scope_key_without_memory not in tag_cache:
            scope_obj = RuntimeScope.from_dict(
                {
                    "bot_id": bot_id,
                    "visibility": visibility,
                    "session": {
                        "id": session_id,
                        "platform_id": session_id.split(":", 1)[0],
                        "kind": session_id.split(":", 2)[1],
                        "conversation_id": session_id.split(":", 2)[2],
                    },
                    "subject_principal_id": None,
                }
            )
            tag_cache[scope_key_without_memory] = _tag_rows_by_name(connection, scope_obj, correction["names"])
        tag_rows = tag_cache[scope_key_without_memory]
        automatic_by_name = {
            str(row[7]).casefold(): row
            for row in grouped.get(scope_key, ())
            if str(row[8] or "active") == "active"
        }
        for position, name in enumerate(correction["names"], 1):
            tag_row = tag_rows.get(name.casefold())
            if tag_row is None or str(tag_row[3] or "active") != "active":
                continue
            automatic = automatic_by_name.get(name.casefold())
            result.append(
                {
                    "bot_id": bot_id,
                    "session_id": session_id,
                    "visibility": visibility,
                    "memory_id": memory_id,
                    "tag_id": int(tag_row[0]),
                    "position": position,
                    "relevance": float(automatic[6] if automatic and automatic[6] is not None else 1.0),
                    "source": "manual" if automatic is None else "automatic",
                    "correction_id": str(correction["correction_id"]),
                }
            )
    return result


def effective_tag_rows(
    connection: Any,
    *,
    scope: RuntimeScope | None = None,
    memory_id: int | None = None,
    tag_ids: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """Return effective Tag links, with automatic baseline fallback when needed.

    ``tag_ids`` narrows the automatic baseline in SQL. Corrections are still read
    for the affected memories so a manual override cannot be silently dropped,
    and the final result is filtered back to the requested tag ids.
    """
    automatic = _automatic_rows(connection, scope, memory_id, tag_ids)
    wanted_tags = _normalized_tag_ids(tag_ids)
    corrections = _active_corrections(connection, scope)
    if memory_id is not None:
        if scope is None:
            corrections = {
                key: value for key, value in corrections.items() if key[3] == int(memory_id)
            }
        else:
            scoped_key = (*_scope_tuple(scope), int(memory_id))
            corrections = {key: value for key, value in corrections.items() if key == scoped_key}
    if wanted_tags is not None:
        # Only corrections for memories that the narrowed baseline already touched
        # can still change this tag slice; unrelated memories stay out of the scan.
        touched = {(str(row[0]), str(row[1]), str(row[2]), int(row[3])) for row in automatic}
        corrections = {key: value for key, value in corrections.items() if key in touched}
    rows = _effective_from_rows(connection, automatic, corrections)
    if wanted_tags is None:
        return rows
    allowed = set(wanted_tags)
    return [row for row in rows if int(row.get("tag_id", 0)) in allowed]


def rebuild_memory_effective_tags(
    connection: Any,
    *,
    scope: RuntimeScope,
    memory_id: int,
    projection_revision: int | None = None,
    now: float | None = None,
) -> int:
    """Rebuild one materialized effective memory projection inside writer transaction."""
    ensure_projection_tables(connection)
    bot_id, session_id, visibility = _scope_tuple(scope)
    timestamp = float(time.time() if now is None else now)
    rows = effective_tag_rows(connection, scope=scope, memory_id=int(memory_id))
    if projection_revision is None:
        current = connection.execute(
            """SELECT COALESCE(projection_revision, 0)
                 FROM scoped_tag_projection_state
                WHERE bot_id=? AND session_id=? AND visibility=?""",
            (bot_id, session_id, visibility),
        ).fetchone()
        projection_revision = int(current[0] if current else 0) + 1
    connection.execute(
        """DELETE FROM scoped_memory_effective_tags
            WHERE bot_id=? AND session_id=? AND visibility=? AND memory_id=?""",
        (bot_id, session_id, visibility, int(memory_id)),
    )
    connection.executemany(
        """INSERT INTO scoped_memory_effective_tags(
               bot_id, session_id, visibility, memory_id, tag_id, position,
               relevance, source, correction_id, projection_revision, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                bot_id,
                session_id,
                visibility,
                int(memory_id),
                int(row["tag_id"]),
                int(row["position"]),
                float(row["relevance"]),
                str(row["source"]),
                row.get("correction_id"),
                int(projection_revision),
                timestamp,
            )
            for row in rows
        ],
    )
    connection.execute(
        """INSERT INTO scoped_tag_projection_state(
               bot_id, session_id, visibility, state, projection_revision,
               cursor_memory_id, last_error, updated_at)
           VALUES (?, ?, ?, 'ready', ?, ?, NULL, ?)
           ON CONFLICT(bot_id, session_id, visibility) DO UPDATE SET
               state='ready', projection_revision=excluded.projection_revision,
               cursor_memory_id=excluded.cursor_memory_id, last_error=NULL,
               updated_at=excluded.updated_at""",
        (bot_id, session_id, visibility, int(projection_revision), int(memory_id), timestamp),
    )
    return int(projection_revision)


def rebuild_scope_effective_tags(
    connection: Any,
    *,
    scope: RuntimeScope,
    memory_ids: Iterable[int] | None = None,
    now: float | None = None,
) -> int:
    """Rebuild selected or all known memories for one Scope."""
    ensure_projection_tables(connection)
    bot_id, session_id, visibility = _scope_tuple(scope)
    if memory_ids is None:
        rows = connection.execute(
            """SELECT memory_id FROM scoped_memory_tags
                WHERE bot_id=? AND session_id=? AND visibility=?
                UNION
               SELECT memory_id FROM scoped_memory_tag_corrections
                WHERE bot_id=? AND session_id=? AND visibility=?
                ORDER BY memory_id""",
            (bot_id, session_id, visibility, bot_id, session_id, visibility),
        ).fetchall()
        ids = [int(row[0]) for row in rows]
    else:
        ids = sorted({int(value) for value in memory_ids})
    revision = 0
    for memory_id in ids:
        revision = rebuild_memory_effective_tags(
            connection,
            scope=scope,
            memory_id=memory_id,
            projection_revision=revision + 1,
            now=now,
        )
    if not ids:
        timestamp = float(time.time() if now is None else now)
        connection.execute(
            """INSERT INTO scoped_tag_projection_state(
                   bot_id, session_id, visibility, state, projection_revision,
                   cursor_memory_id, last_error, updated_at)
               VALUES (?, ?, ?, 'ready', 0, NULL, NULL, ?)
               ON CONFLICT(bot_id, session_id, visibility) DO UPDATE SET
                   state='ready', last_error=NULL, updated_at=excluded.updated_at""",
            (bot_id, session_id, visibility, timestamp),
        )
    return revision


__all__ = [
    "ScopedTagProjectionError",
    "effective_tag_rows",
    "ensure_projection_tables",
    "rebuild_memory_effective_tags",
    "rebuild_scope_effective_tags",
]
