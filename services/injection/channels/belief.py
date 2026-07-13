"""Belief 注入通道。"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from ...identity_safety import is_identity_contamination
try:
    from ....domain.scope import validate_formal_command_scope
except ImportError:  # pragma: no cover - standalone repository tests
    from domain.scope import validate_formal_command_scope
from ..channel_base import InjectionResult
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


def _channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("belief", {}))


def _keywords(message: str) -> list[str]:
    tokens = [token.strip() for token in str(message or "").split() if len(token.strip()) > 1]
    if not tokens:
        tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", str(message or ""))
    deduped: list[str] = []
    for token in tokens:
        if token not in deduped:
            deduped.append(token)
        if len(deduped) >= 5:
            break
    return deduped


def _extract_belief_ids(text: str) -> list[int]:
    return [int(value) for value in re.findall(r"ID[:#]?(\d+)", text or "")[:10]]


def _preview(text: str | None, limit: int = 160) -> str:
    compact = str(text or "").replace("\n", " ").strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


class BeliefChannel:
    """只读调用 BeliefEngine.get_injection，不写入或提升信念。"""

    name = "belief"

    def __init__(self, *, belief_engine: Any = None):
        self.belief_engine = belief_engine

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"belief channel disabled in {mode} mode")

        cfg = _channel_cfg(ctx)
        if not _as_bool(cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="belief channel disabled by config")
        if not self.belief_engine:
            return InjectionResult.empty(self.name, reason="belief engine is unavailable")

        runtime_scope = getattr(ctx, "scope", None)
        scope_decision = validate_formal_command_scope("belief.inject", runtime_scope)
        if not scope_decision.allowed:
            return InjectionResult.empty(
                self.name,
                reason=scope_decision.reason_code or "scope_rejected",
            )

        try:
            bot_profile_id = getattr(ctx, "bot_profile_id", "") or getattr(ctx, "bot_id", "") or "bot"
            if hasattr(self.belief_engine, "bot_id"):
                self.belief_engine.bot_id = bot_profile_id
            keywords = _keywords(getattr(ctx, "message", "") or "")
            text = self.belief_engine.get_injection(
                scope=runtime_scope,
                sender_id=getattr(ctx, "sender_id", "") or "",
                keywords=keywords,
            ) or ""
            text = str(text).strip()
            if not text:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no active beliefs")
            if is_identity_contamination(text):
                result = InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no safe beliefs")
                result.filtered = [self._audit_filtered(text, reason="identity_contamination")]
                return result
            return InjectionResult.hit(
                self.name,
                text,
                items=[{
                    "source": "BeliefEngine.get_injection",
                    "bot_id": bot_profile_id,
                    "sender_id": getattr(ctx, "sender_id", "") or "",
                    "keywords": keywords,
                    "belief_ids": _extract_belief_ids(text),
                    "preview": _preview(text),
                }],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    @staticmethod
    def _audit_filtered(text: str, *, reason: str) -> dict[str, Any]:
        return {
            "source": "BeliefEngine.get_injection",
            "filter_reason": reason,
            "filter_channel": "belief",
            "belief_ids": _extract_belief_ids(text),
            "preview": _preview(text),
        }

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["BeliefChannel"]
