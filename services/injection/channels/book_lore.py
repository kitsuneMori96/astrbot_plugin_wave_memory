"""BookLore 书设知识注入通道。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from ...identity_safety import is_identity_contamination
from ..channel_base import InjectionResult
from .safety import is_channel_allowed_in_mode

try:
    from ....domain.scope import RuntimeScope
except ImportError:  # pragma: no cover - direct services imports in isolated tests
    from domain.scope import RuntimeScope


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
    return _mapping(_mapping(config.get("channels", {})).get("book_lore", {}))


def _preview(text: str | None, limit: int = 160) -> str:
    compact = str(text or "").replace("\n", " ").strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


class BookLoreChannel:
    """只读当前 RuntimeScope 的 approved reviewed projection。"""

    name = "book_lore"

    def __init__(
        self,
        *,
        projection_repository: Any = None,
        # 旧参数只为保持装配调用兼容；通道绝不访问这些 raw Catalog 依赖。
        book_lore_index: Any = None,
        embedding_service: Any = None,
        lore_db_path: str = "",
        catalog_scope: Any = None,
        lore_store: Any = None,
    ):
        self.projection_repository = projection_repository

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"book_lore channel disabled in {mode} mode")

        cfg = _channel_cfg(ctx)
        if not _as_bool(cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="book_lore channel disabled by config")
        runtime_scope = getattr(ctx, "scope", None)
        if not isinstance(runtime_scope, RuntimeScope):
            return InjectionResult.empty(self.name, reason="runtime_scope_required")
        bot_profile_id = str(getattr(ctx, "bot_profile_id", "") or "").strip()
        if not bot_profile_id or runtime_scope.bot_id != bot_profile_id:
            return InjectionResult.empty(self.name, reason="runtime_scope_bot_mismatch")
        if self.projection_repository is None:
            return InjectionResult.empty(self.name, reason="reviewed_book_lore_projection_unavailable")

        top_k = _as_int(cfg.get("top_k"), 1)
        if top_k <= 0:
            return InjectionResult.empty(self.name, reason="book_lore top_k is zero")

        try:
            rows = self.projection_repository.list_approved(scope=runtime_scope, limit=top_k)
            selected: list[dict[str, Any]] = []
            filtered: list[dict[str, Any]] = []
            for row in rows or ():
                item = {
                    "projection_id": row.get("id"),
                    "community_id": row.get("community_id"),
                    "revision": row.get("revision"),
                    "title": row.get("title") or "",
                    "summary": row.get("summary") or "",
                    "rank": row.get("rank"),
                }
                if is_identity_contamination(f"{item['title']} {item['summary']}"):
                    filtered.append({
                        **item,
                        "filter_reason": "identity_contamination",
                        "filter_channel": "book_lore",
                    })
                    continue
                selected.append(item)

            if not selected:
                result = InjectionResult.empty(
                    self.name,
                    latency_ms=self._latency_ms(started),
                    reason="no approved book lore projections",
                )
                result.filtered = [self._audit_filtered(item) for item in filtered]
                return result

            lines = ["<world_knowledge>"]
            for item in selected:
                lines.append(f"{item['title']}：{str(item['summary'])[:300]}")
            lines.append("</world_knowledge>")
            return InjectionResult.hit(
                self.name,
                "\n".join(lines),
                items=[self._audit_item(item) for item in selected],
                filtered=[self._audit_filtered(item) for item in filtered],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    @staticmethod
    def _audit_item(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "projection_id": item.get("projection_id"),
            "community_id": item.get("community_id"),
            "revision": item.get("revision"),
            "title": item.get("title", ""),
            "rank": item.get("rank"),
            "preview": _preview(item.get("summary", "")),
        }

    @staticmethod
    def _audit_filtered(item: Mapping[str, Any]) -> dict[str, Any]:
        payload = BookLoreChannel._audit_item(item)
        payload["filter_reason"] = item.get("filter_reason", "filtered")
        payload["filter_channel"] = item.get("filter_channel", "book_lore")
        return payload

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["BookLoreChannel"]
