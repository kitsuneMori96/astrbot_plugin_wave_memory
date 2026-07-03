"""Jargon 黑话注入通道。"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from ...identity_safety import is_identity_contamination
from ..channel_base import InjectionResult
from .safety import is_channel_allowed_in_mode

_TERM_LINE_RE = re.compile(r'["“](.+?)["”]\s*[→:：-]\s*(.+)$')


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
    return _mapping(_mapping(config.get("channels", {})).get("jargon", {}))


def _preview(text: str | None, limit: int = 160) -> str:
    compact = str(text or "").replace("\n", " ").strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _parse_terms(text: str) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for line in str(text or "").splitlines():
        cleaned = line.strip().lstrip("- ").strip()
        match = _TERM_LINE_RE.search(cleaned)
        if not match:
            continue
        word = match.group(1).strip()
        meaning = match.group(2).strip()
        if word and meaning and not is_identity_contamination(f"{word} {meaning}"):
            terms.append({
                "word": word,
                "meaning": meaning,
                "source": "JargonService.get_injection",
                "preview": f"{word} → {meaning}",
            })
    if not terms and text:
        terms.append({"word": "", "meaning": "", "source": "JargonService.get_injection", "preview": _preview(text)})
    return terms


class JargonChannel:
    """只读调用现有 JargonService/JargonInjector 的黑话解释通道。"""

    name = "jargon"

    def __init__(self, *, jargon_service: Any = None):
        self.jargon_service = jargon_service

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"jargon channel disabled in {mode} mode")

        cfg = _channel_cfg(ctx)
        if not _as_bool(cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="jargon channel disabled by config")
        group_id = getattr(ctx, "group_id", None)
        if not group_id:
            return InjectionResult.empty(self.name, reason="jargon requires group_id")
        if not self.jargon_service:
            return InjectionResult.empty(self.name, reason="jargon service is unavailable")

        try:
            text = self.jargon_service.get_injection(getattr(ctx, "message", "") or "", group_id) or ""
            text = str(text).strip()
            if not text:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no matching confirmed jargon")
            if is_identity_contamination(text):
                result = InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no safe jargon")
                result.filtered = [{
                    "source": "JargonService.get_injection",
                    "filter_reason": "identity_contamination",
                    "filter_channel": "jargon",
                    "preview": _preview(text),
                }]
                return result
            items = _parse_terms(text)
            return InjectionResult.hit(self.name, text, items=items, latency_ms=self._latency_ms(started))
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["JargonChannel"]
