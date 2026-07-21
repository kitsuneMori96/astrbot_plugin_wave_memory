"""Helpers for narrow shared_memory_grants read expansion (no fanout, no touch)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def load_active_grant_memory_ids(
    db: Any,
    *,
    bot_id: str,
    session_id: str,
    visibility: str = "group",
    group_id: str = "",
    limit: int = 5000,
) -> list[int]:
    """Return active granted memory_ids for a consumer scope, or []."""
    repo = getattr(db, "shared_memory_grants", None)
    if repo is None:
        return []
    lister = getattr(repo, "active_memory_ids_for_consumer", None)
    if not callable(lister):
        return []
    consumer = {
        "bot_id": str(bot_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "visibility": str(visibility or "group").strip() or "group",
        "group_id": str(group_id or "").strip(),
    }
    if not consumer["bot_id"] or not consumer["session_id"] or not consumer["group_id"]:
        return []
    try:
        ids = lister(consumer_scope=consumer, limit=limit) or []
    except TypeError:
        try:
            ids = lister(consumer_scope=consumer) or []
        except Exception:
            return []
    except Exception:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for raw in ids:
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
        if len(out) >= max(1, int(limit)):
            break
    return out


def formal_grant_id_predicate(
    grant_ids: Sequence[int],
    *,
    alias: str = "m",
) -> tuple[str, tuple[Any, ...]]:
    """SQL fragment: formal resolved group rows whose id is in grant allow-list."""
    if not grant_ids:
        return "0=1", ()
    prefix = f"{alias}." if alias else ""
    placeholders = ",".join("?" * len(grant_ids))
    sql = (
        f"{prefix}id IN ({placeholders}) "
        f"AND {prefix}visibility = 'group' "
        f"AND COALESCE({prefix}group_id, '') != '' "
        f"AND COALESCE({prefix}bot_id, '') != '' "
        f"AND COALESCE({prefix}session_id, '') != '' "
        f"AND COALESCE({prefix}resolution_state, '') = 'resolved' "
        f"AND COALESCE({prefix}quarantine, 0) = 0 "
        f"AND COALESCE({prefix}memory_type, 'message') NOT IN ('archived', 'evicted', 'deleted') "
        f"AND COALESCE({prefix}source, '') != 'noise'"
    )
    return sql, tuple(int(x) for x in grant_ids)


def tag_shared_grant_rows(
    rows: list[Mapping[str, Any]] | list[dict[str, Any]],
    grant_ids: Sequence[int],
    *,
    current_group_id: str,
) -> list[dict[str, Any]]:
    """Mark cross-group grant hits; never mutates ownership fields."""
    allow = {int(x) for x in grant_ids}
    current = str(current_group_id or "").strip()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            mid = int(item.get("id"))
        except (TypeError, ValueError):
            mid = 0
        if mid in allow and str(item.get("group_id") or "") != current:
            item["_shared_grant"] = True
        out.append(item)
    return out


__all__ = [
    "load_active_grant_memory_ids",
    "formal_grant_id_predicate",
    "tag_shared_grant_rows",
]
