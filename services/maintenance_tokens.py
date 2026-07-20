"""Stable idempotency tokens for durable maintenance jobs."""

from __future__ import annotations


def maintenance_repair_token(
    kind: str,
    reason: str,
    *,
    watermark: int,
    generation: int,
) -> str:
    """Coalesce physical memory-capacity repairs within one HNSW generation."""
    if kind == "memory_index" and reason == "hot_capacity":
        return f"{kind}:{reason}:{generation}"
    return f"{kind}:{reason}:{watermark}:{generation}"
