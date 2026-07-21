"""Collapse multi-group fanout clones for recall/injection.

Physical rows may still exist.  This module only decides which candidates keep a
slot in the active result list.  It never deletes database rows.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_WS_RE = re.compile(r"\s+")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except Exception:
            return {}
        if isinstance(loaded, Mapping):
            return loaded
    return {}


def _norm_text(value: Any) -> str:
    return _WS_RE.sub(" ", str(value or "").strip())


def collapse_key(memory: Mapping[str, Any]) -> str:
    """Stable identity for fanout/content clones.

    Prefer explicit fanout family, then **sender+content** (same person / same
    utterance across groups). Historical fanout rows often carry *different*
    ``origin_fingerprint`` per copy; using origin before text would fail to
    collapse true duplicates and re-flood recall.
    """
    provenance = _as_mapping(memory.get("provenance"))
    family = str(
        memory.get("fanout_family_id")
        or provenance.get("fanout_family_id")
        or provenance.get("legacy_memory_id")
        or ""
    ).strip()
    if family:
        return f"family:{family}"

    content = _norm_text(memory.get("content") or memory.get("summary") or "")
    sender = str(memory.get("sender_id") or memory.get("sender_name") or "").strip()
    if content:
        return f"text:{sender}|{content[:240]}"

    origin = str(
        memory.get("origin_fingerprint")
        or memory.get("origin_key")
        or provenance.get("origin_fingerprint")
        or memory.get("source_memory_id")
        or ""
    ).strip()
    if origin:
        return f"origin:{origin}"
    return f"id:{memory.get('id')}"


def is_fanout_duplicate(memory: Mapping[str, Any]) -> bool:
    provenance = _as_mapping(memory.get("provenance"))
    if memory.get("_fanout_duplicate") or memory.get("fanout_duplicate"):
        return True
    kind = str(provenance.get("projection_kind") or provenance.get("kind") or "").strip()
    return kind in {"fanout_duplicate", "group_bound_core_chat_fanout"}


def collapse_memories(
    memories: list[Mapping[str, Any]] | list[dict[str, Any]],
    *,
    current_group_id: str = "",
) -> list[dict[str, Any]]:
    """Prefer current-group / non-fanout rows and keep one row per collapse key."""
    if not memories:
        return []

    current = str(current_group_id or "").strip()

    def sort_key(memory: Mapping[str, Any]) -> tuple:
        group_id = str(memory.get("group_id") or "")
        in_current = 0 if current and group_id == current else 1
        fanout = 1 if is_fanout_duplicate(memory) else 0
        cross = 1 if memory.get("_is_cross_group") else 0
        try:
            score = -float(memory.get("score", memory.get("similarity", 0.0)) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            timestamp = -float(memory.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            timestamp = 0.0
        try:
            memory_id = int(memory.get("id") or 0)
        except (TypeError, ValueError):
            memory_id = 0
        return (in_current, fanout, cross, score, timestamp, memory_id)

    ordered = sorted((dict(item) for item in memories if isinstance(item, Mapping)), key=sort_key)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for memory in ordered:
        key = collapse_key(memory)
        if key in seen:
            memory["_collapsed_as_fanout_duplicate"] = True
            continue
        seen.add(key)
        deduped.append(memory)
    return deduped


__all__ = [
    "collapse_key",
    "collapse_memories",
    "is_fanout_duplicate",
]
