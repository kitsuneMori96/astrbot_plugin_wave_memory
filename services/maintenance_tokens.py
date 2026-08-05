"""Stable idempotency tokens for durable maintenance jobs."""

from __future__ import annotations


def maintenance_repair_token(
    kind: str,
    reason: str,
    *,
    watermark: int,
    generation: int,
) -> str:
    """Coalesce one repair per (kind, reason, watermark, generation) tuple.

    容量满载已改为 inline resize（v4.2.1 语义），不再产生 hot_capacity /
    chat_hot_window 重建请求，因此这里不需要按 generation 单独合并。
    """
    return f"{kind}:{reason}:{watermark}:{generation}"
