"""FTS5 精确检索注入通道。"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from ...identity_safety import is_identity_contamination
from ..channel_base import InjectionResult, estimate_injection_tokens
from .safety import is_channel_allowed_in_mode


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("fts5", {}))


def _keywords(message: str, limit: int = 6) -> list[str]:
    try:
        import jieba  # type: ignore

        try:
            jieba.setLogLevel(40)
        except Exception:
            pass
        candidates = jieba.cut(message or "")
    except Exception:  # pragma: no cover
        candidates = re.findall(r"[\w\u4e00-\u9fff]{2,}", message or "")
    words: list[str] = []
    for candidate in candidates:
        word = str(candidate or "").strip()
        if len(word) >= 2 and word not in words:
            words.append(word)
        if len(words) >= limit:
            break
    return words


def _cjk_spaced(text: str) -> str:
    """与 engine.database._fts_normalize 一致：每个汉字两侧加空格。"""
    out = []
    for ch in str(text or ""):
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            out.append(" ")
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def _match_expr(words: list[str]) -> str:
    """每个关键词归一化为 CJK 单字短语（"生 日"），短语内 AND、词间 OR。

    索引侧触发器对 content 做同样的单字切分，短语匹配即可命中
    连续中文里的任意子词——unicode61 巨型 token 问题的双侧修复。
    """
    quoted = []
    for word in words:
        safe = word.replace('"', " ").strip()
        if not safe:
            continue
        spaced = _cjk_spaced(safe).split()
        if spaced:
            quoted.append('"' + " ".join(spaced) + '"')
    return " OR ".join(quoted)


def _preview(text: str | None, limit: int = 160) -> str:
    compact = str(text or "").replace("\n", " ").strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _audit_memory(memory: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": memory.get("id"),
        "source": memory.get("source", "live"),
        "sender_id": memory.get("sender_id", ""),
        "sender_name": memory.get("sender_name", ""),
        "group_id": memory.get("group_id", ""),
        "score": memory.get("score"),
        "preview": _preview(memory.get("content", "")),
    }


class FTS5Channel:
    """基于 fts_memories 的精确关键词召回通道。"""

    name = "fts5"

    def __init__(self, *, db: Any):
        self.db = db

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"fts5 channel disabled in {mode} mode")

        cfg = _channel_cfg(ctx)
        if not _as_bool(cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="fts5 channel disabled by config")
        top_k = _as_int(cfg.get("top_k"), 10)
        token_budget = _as_int(cfg.get("token_budget"), 350)
        min_score = _as_float(cfg.get("min_score"), 0.0)
        if top_k <= 0:
            return InjectionResult.empty(self.name, reason="fts5 top_k is zero")

        try:
            words = _keywords(getattr(ctx, "message", "") or "")
            if not words:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no exact keywords")
            expr = _match_expr(words)
            if not expr:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="empty match expression")
            memories = self._query_memories(ctx, expr=expr, words=words, top_k=top_k)
            selected, filtered = self._filter_and_budget(memories, top_k=top_k, token_budget=token_budget, min_score=min_score)
            if not selected:
                result = InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no fts5 hits")
                result.filtered = [self._audit_filtered(item) for item in filtered]
                return result
            text = self._format(selected)
            return InjectionResult.hit(
                self.name,
                text,
                items=[_audit_memory(memory) for memory in selected],
                filtered=[self._audit_filtered(item) for item in filtered],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    def _query_memories(self, ctx: Any, *, expr: str, words: list[str], top_k: int) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """SELECT rowid, content, sender_name, group_id FROM fts_memories
               WHERE fts_memories MATCH ? LIMIT ?""",
            (expr, max(20, top_k * 3)),
        ).fetchall()
        if not rows:
            rows = self._like_fallback(words=words, limit=max(20, top_k * 3))

        memories: list[dict[str, Any]] = []
        seen_ids: set[Any] = set()
        for row in rows:
            mem = self.db.conn.execute(
                """SELECT id, content, sender_id, sender_name, timestamp, importance, source, group_id, memory_type
                   FROM memories WHERE id=? AND memory_type='message'""",
                (row[0],),
            ).fetchone()
            if not mem or mem[0] in seen_ids:
                continue
            seen_ids.add(mem[0])
            score = 1.0 if mem[7] == getattr(ctx, "group_id", None) else 0.5
            memories.append({
                "id": mem[0],
                "content": mem[1] or "",
                "sender_id": mem[2],
                "sender_name": mem[3],
                "timestamp": mem[4],
                "importance": mem[5],
                "source": mem[6],
                "group_id": mem[7],
                "memory_type": mem[8],
                "score": score,
            })
        memories.sort(key=lambda item: item.get("score", 0), reverse=True)
        return memories

    def _like_fallback(self, *, words: list[str], limit: int) -> list[tuple[Any, ...]]:
        if not words:
            return []
        conditions = " OR ".join(["content LIKE ?"] * len(words))
        params = [f"%{word}%" for word in words]
        return self.db.conn.execute(
            f"""SELECT id AS rowid, content, sender_name, group_id
                FROM memories
               WHERE memory_type='message' AND ({conditions})
               ORDER BY timestamp DESC LIMIT ?""",
            params + [limit],
        ).fetchall()

    @staticmethod
    def _filter_and_budget(
        memories: list[dict[str, Any]],
        *,
        top_k: int,
        token_budget: int,
        min_score: float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        used_tokens = 0
        for memory in memories:
            if memory.get("score", 0.0) < min_score:
                filtered.append({**memory, "filter_reason": "min_score", "filter_channel": "fts5"})
                continue
            if is_identity_contamination(memory.get("content", "")):
                filtered.append({**memory, "filter_reason": "identity_contamination", "filter_channel": "fts5"})
                continue
            if len(selected) >= top_k:
                filtered.append({**memory, "filter_reason": "top_k", "filter_channel": "fts5"})
                continue
            tokens = estimate_injection_tokens(memory.get("content", ""))
            if selected and token_budget >= 0 and used_tokens + tokens > token_budget:
                filtered.append({**memory, "filter_reason": "token_budget", "filter_channel": "fts5"})
                continue
            selected.append(memory)
            used_tokens += tokens
        return selected, filtered

    @staticmethod
    def _format(memories: list[dict[str, Any]]) -> str:
        from ..memory_note import MEMORY_SNAPSHOT_NOTE
        lines = ["<wave_memory>", MEMORY_SNAPSHOT_NOTE]
        for memory in memories:
            sender = memory.get("sender_name") or memory.get("sender_id") or "unknown"
            ts = time.strftime("%m-%d %H:%M", time.localtime(float(memory.get("timestamp") or 0)))
            lines.append(f"[记忆] {sender}({ts}): {memory.get('content', '')} (relevance: {float(memory.get('score') or 0):.2f})")
        lines.append("</wave_memory>")
        return "\n".join(lines)

    @staticmethod
    def _audit_filtered(memory: Mapping[str, Any]) -> dict[str, Any]:
        payload = _audit_memory(memory)
        payload["filter_reason"] = memory.get("filter_reason", "filtered")
        payload["filter_channel"] = memory.get("filter_channel", "fts5")
        return payload

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["FTS5Channel"]
