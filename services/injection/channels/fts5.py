"""FTS5 精确检索注入通道。"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

try:
    from ....domain.scope import RuntimeScope
except ImportError:  # 兼容独立测试/插件顶级加载
    from domain.scope import RuntimeScope

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


def _group_scope(ctx: Any) -> RuntimeScope | None:
    scope = getattr(ctx, "scope", None)
    if not isinstance(scope, RuntimeScope):
        return None
    if scope.visibility != "group" or scope.session is None:
        return None
    return scope


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


def _match_expr(words: list[str]) -> str:
    quoted = []
    for word in words:
        safe = word.replace('"', ' ').strip()
        if safe:
            quoted.append(f'"{safe}"')
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
        if _group_scope(ctx) is None:
            return InjectionResult.empty(self.name, reason="fts5 requires resolved group RuntimeScope")

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
        scope = _group_scope(ctx)
        if scope is None:  # build() guards this too; keep direct callers fail-closed.
            return []
        limit = max(20, top_k * 3)
        base = """
            m.bot_id = ? AND m.session_id = ? AND m.visibility = ?
            AND m.resolution_state = 'resolved' AND m.quarantine = 0
            AND m.memory_type = 'message'
        """
        params = (scope.bot_id, scope.session.id, scope.visibility)
        try:
            rows = self.db.conn.execute(
                f"""SELECT m.id, m.content, m.sender_id, m.sender_name, m.timestamp,
                           m.importance, m.source, m.group_id, m.memory_type
                      FROM fts_memories
                      JOIN memories AS m ON m.id = fts_memories.rowid
                     WHERE fts_memories MATCH ? AND {base}
                     ORDER BY rank LIMIT ?""",
                (expr, *params, limit),
            ).fetchall()
            if not rows:
                rows = self._like_fallback(words=words, limit=limit, scope=scope)
        except Exception:
            # Missing scoped schema or an invalid FTS expression must never fall
            # back to an unscoped legacy read.
            return []

        return [
            {
                "id": row[0], "content": row[1] or "", "sender_id": row[2],
                "sender_name": row[3], "timestamp": row[4], "importance": row[5],
                "source": row[6], "group_id": row[7], "memory_type": row[8], "score": 1.0,
            }
            for row in rows
        ]

    def _like_fallback(self, *, words: list[str], limit: int, scope: RuntimeScope) -> list[tuple[Any, ...]]:
        if not words or scope.session is None:
            return []
        conditions = " OR ".join(["m.content LIKE ?"] * len(words))
        params = [f"%{word}%" for word in words]
        return self.db.conn.execute(
            f"""SELECT m.id, m.content, m.sender_id, m.sender_name, m.timestamp,
                       m.importance, m.source, m.group_id, m.memory_type
                  FROM memories AS m
                 WHERE m.bot_id = ? AND m.session_id = ? AND m.visibility = ?
                   AND m.resolution_state = 'resolved' AND m.quarantine = 0
                   AND m.memory_type = 'message' AND ({conditions})
                 ORDER BY m.timestamp DESC LIMIT ?""",
            [scope.bot_id, scope.session.id, scope.visibility, *params, limit],
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
        lines = ["<wave_memory>"]
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
