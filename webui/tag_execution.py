"""Shared Tag extraction option parsing and tag writing helpers."""

from __future__ import annotations

import asyncio
from typing import Any

try:
    from ..domain.scope import RuntimeScope
    from ..engine.db.scoped_tag_projection import effective_tag_rows
except ImportError:
    from domain.scope import RuntimeScope
    from engine.db.scoped_tag_projection import effective_tag_rows

TAG_WRITE_POLICIES = {"missing_only", "append", "replace"}


def _source_get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    getter = getattr(source, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(source, key, default)


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_tag_execution_options(source: Any = None, *, defaults: dict | None = None) -> dict:
    """Normalize shared Tag execution options from query args or JSON body.

    Supported semantic fields:
    - extract_tags
    - tag_batch_size (falls back to legacy batch_size)
    - tag_write_policy: missing_only | append | replace
    - skip_short_min_length
    """
    defaults = defaults or {}
    extract_default = _bool_value(defaults.get("extract_tags"), True)
    batch_default = _int_value(defaults.get("tag_batch_size"), 20)
    policy_default = defaults.get("tag_write_policy", "missing_only")
    skip_default = _int_value(defaults.get("skip_short_min_length"), 10)

    raw_batch_size = _source_get(source, "tag_batch_size", None)
    if raw_batch_size is None:
        raw_batch_size = _source_get(source, "batch_size", None)
    batch_size = max(1, min(50, _int_value(raw_batch_size, batch_default)))

    policy = str(_source_get(source, "tag_write_policy", policy_default) or policy_default).strip()
    if policy not in TAG_WRITE_POLICIES:
        raise ValueError(f"Invalid tag_write_policy: {policy}")

    skip_short_min_length = max(0, _int_value(_source_get(source, "skip_short_min_length", None), skip_default))

    return {
        "extract_tags": _bool_value(_source_get(source, "extract_tags", None), extract_default),
        "tag_batch_size": batch_size,
        "tag_write_policy": policy,
        "skip_short_min_length": skip_short_min_length,
    }


def _memory_has_tags(conn, memory_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM memory_tags WHERE memory_id = ? LIMIT 1", (memory_id,)).fetchone()
    return row is not None


def _message_content(message: dict) -> str:
    return str(message.get("content") or "")


def _message_sender(message: dict) -> str:
    return str(message.get("sender") or message.get("sender_name") or "")


async def _extract_tag_batches(tag_extractor, messages: list[dict]) -> list[list[dict]]:
    if hasattr(tag_extractor, "extract_tags_batch"):
        return await tag_extractor.extract_tags_batch(messages)
    return await asyncio.gather(*[
        tag_extractor.extract_tags(_message_content(item)[:800], sender=_message_sender(item))
        for item in messages
    ])


async def tag_memory_batch(
    db,
    embedding_service,
    tag_extractor,
    messages: list[dict],
    *,
    tag_batch_size: int = 20,
    tag_write_policy: str = "missing_only",
    skip_short_min_length: int = 10,
    write_gateway=None,
) -> dict:
    """Extract and write tags for a batch of memory messages.

    The helper owns the shared write policies so selected-memory extraction,
    import-time tagging, and maintenance backfill cannot drift apart.
    """
    if tag_write_policy not in TAG_WRITE_POLICIES:
        raise ValueError(f"Invalid tag_write_policy: {tag_write_policy}")

    conn = db.conn
    tag_batch_size = max(1, min(50, _int_value(tag_batch_size, 20)))
    skip_short_min_length = max(0, _int_value(skip_short_min_length, 10))

    candidates: list[dict] = []
    skipped = 0
    for item in messages or []:
        try:
            memory_id = int(item.get("id"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        content = _message_content(item)
        if len(content) < skip_short_min_length:
            skipped += 1
            continue
        if tag_write_policy == "missing_only":
            if write_gateway is not None:
                scope_payload = item.get("scope")
                if not isinstance(scope_payload, dict):
                    raise ValueError("runtime_scope_required_for_tag_extraction")
                scope = RuntimeScope.from_dict(scope_payload)
                has_tags = bool(effective_tag_rows(conn, scope=scope, memory_id=memory_id))
            else:
                has_tags = _memory_has_tags(conn, memory_id)
            if has_tags:
                skipped += 1
                continue
        candidates.append({**item, "id": memory_id, "content": content[:800], "sender": _message_sender(item)})

    result = {
        "processed": 0,
        "total": len(messages or []),
        "selected": len(candidates),
        "tagged": 0,
        "errors": 0,
        "skipped": skipped,
    }

    if not candidates or not tag_extractor:
        return result

    for start in range(0, len(candidates), tag_batch_size):
        chunk = candidates[start:start + tag_batch_size]
        try:
            tag_batches = await _extract_tag_batches(tag_extractor, chunk)
        except Exception:
            result["errors"] += len(chunk)
            result["processed"] += len(chunk)
            continue

        if len(tag_batches) < len(chunk):
            tag_batches = list(tag_batches) + [[] for _ in range(len(chunk) - len(tag_batches))]

        for item, tags in zip(chunk, tag_batches):
            result["processed"] += 1
            try:
                clean_tags = [tag for tag in (tags or []) if isinstance(tag, dict) and tag.get("name")]
                if write_gateway is not None:
                    if tag_write_policy != "missing_only":
                        raise ValueError(
                            "production tag extraction only supports missing_only through the scoped gateway"
                        )
                    scope_payload = item.get("scope")
                    if not isinstance(scope_payload, dict):
                        raise ValueError("runtime_scope_required_for_tag_extraction")
                    scope = RuntimeScope.from_dict(scope_payload)
                    await write_gateway.apply_tag_extraction(
                        scope=scope,
                        memory_id=item["id"],
                        tags=clean_tags,
                        status="done" if clean_tags else "skipped",
                    )
                    if clean_tags:
                        result["tagged"] += 1
                    continue

                if tag_write_policy == "replace":
                    conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (item["id"],))
                if not clean_tags:
                    conn.commit()
                    continue

                tag_names = [str(tag["name"]) for tag in clean_tags]
                if embedding_service and hasattr(embedding_service, "get_embeddings"):
                    tag_vecs = await embedding_service.get_embeddings(tag_names)
                else:
                    tag_vecs = [None for _ in tag_names]

                tag_ids = []
                for tag_info, tag_vec in zip(clean_tags, tag_vecs):
                    tag_id = db.add_tag_extended(
                        name=str(tag_info["name"]),
                        tag_type=tag_info.get("type", "keyword"),
                        vector=tag_vec,
                        confidence=tag_info.get("confidence", tag_info.get("score", 0.8)),
                    )
                    tag_ids.append(tag_id)

                for position, (tag_id, tag_info) in enumerate(zip(tag_ids, clean_tags), 1):
                    conn.execute(
                        "INSERT OR IGNORE INTO memory_tags (memory_id, tag_id, position, relevance) VALUES (?, ?, ?, ?)",
                        (item["id"], tag_id, position, tag_info.get("confidence", tag_info.get("score", 0.8))),
                    )
                conn.commit()
                result["tagged"] += 1
            except Exception:
                result["errors"] += 1

    return result
