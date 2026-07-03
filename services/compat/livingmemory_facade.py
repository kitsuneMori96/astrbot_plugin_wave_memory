"""LivingMemory-compatible facade for WaveMemory.

This module exposes a small memory-provider surface for plugins that already
expect a LivingMemory-like backend. It delegates reads to QueryEngine and writes
into the existing MessageWriter queue, so WaveMemory remains a pure memory
backend and does not duplicate storage.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

try:  # pragma: no cover - runtime has AstrBot logger; tests may not.
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)  # type: ignore[assignment]


@dataclass(frozen=True)
class LivingMemoryCompatSurface:
    """Plugin-level compatibility attributes exposed to memory-plugin callers."""

    memory_engine: "WaveMemoryLivingMemoryFacade"
    initializer: SimpleNamespace


class WaveMemoryLivingMemoryFacade:
    """Compatibility memory engine backed by WaveMemory query/write internals."""

    def __init__(
        self,
        *,
        query_engine: Any = None,
        writer: Any = None,
        default_session_id: str = "global",
        default_sender_name: str = "LivingMemory兼容",
        now: Callable[[], float] | None = None,
        max_k: int = 50,
    ) -> None:
        self.query_engine = query_engine
        self.writer = writer
        self.default_session_id = default_session_id or "global"
        self.default_sender_name = default_sender_name or "LivingMemory兼容"
        self._now = now or time.time
        self.max_k = max(1, int(max_k or 50))
        self.last_error: str | None = None

    async def search_memories(
        self,
        query: str,
        k: int = 5,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search WaveMemory memories through QueryEngine.

        Returns stable dict results with id/content/score/importance/metadata.
        Errors are contained and exposed through ``last_error`` so callers do not
        crash when the backend is temporarily unavailable.
        """
        self.last_error = None
        text = str(query or "").strip()
        if not text:
            return []
        if self.query_engine is None:
            self.last_error = "query_engine_unavailable"
            return []

        top_k = self._bounded_int(k, default=5, minimum=1, maximum=self.max_k)
        try:
            memories = await self.query_engine.query(text=text, group_id=session_id, top_k=top_k)
        except Exception as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            logger.debug(f"[WaveMemory] LivingMemory facade search failed: {self.last_error}")
            return []

        return [self._format_memory_result(memory, session_id=session_id, persona_id=persona_id) for memory in (memories or [])]

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.7,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Queue a memory write through MessageWriter and return a stable queued id."""
        self.last_error = None
        text = str(content or "").strip()
        if not text:
            return ""
        if self.writer is None or not hasattr(self.writer, "enqueue"):
            self.last_error = "writer_unavailable"
            return ""

        group_id = str(session_id or self.default_session_id or "global")
        persona = str(persona_id or "")
        safe_importance = self._bounded_float(importance, default=0.7, minimum=0.0, maximum=3.0)
        queued_id = self._queued_id(text, session_id=group_id, persona_id=persona, importance=safe_importance)
        item_metadata = self._metadata_from_mapping(metadata)
        item_metadata.update({
            "source": "wave_memory_livingmemory_compat",
            "session_id": group_id,
            "persona_id": persona,
            "queued_id": queued_id,
        })
        item = {
            "group_id": group_id,
            "sender_id": f"compat:{persona}" if persona else "compat_livingmemory",
            "sender_name": self.default_sender_name,
            "content": text,
            "timestamp": self._now(),
            "importance": safe_importance,
            "source": "compat_livingmemory",
            "metadata": item_metadata,
        }
        try:
            await self.writer.enqueue(item)
        except Exception as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            logger.warning(f"[WaveMemory] LivingMemory facade enqueue failed: {self.last_error}")
            return ""
        return queued_id

    def _format_memory_result(
        self,
        memory: Mapping[str, Any],
        *,
        session_id: str | None,
        persona_id: str | None,
    ) -> dict[str, Any]:
        content = str(memory.get("content") or memory.get("summary") or "")
        raw_id = memory.get("id", memory.get("memory_id", ""))
        metadata = self._metadata_from_mapping(memory.get("metadata"))
        for key in (
            "source",
            "group_id",
            "sender_id",
            "sender_name",
            "timestamp",
            "similarity",
            "access_count",
            "memory_type",
        ):
            if key in memory and memory.get(key) is not None:
                metadata[key] = memory.get(key)
        metadata.update({
            "source": metadata.get("source", "wave_memory"),
            "session_id": session_id,
            "persona_id": persona_id,
        })
        return {
            "id": str(raw_id),
            "content": content,
            "score": self._bounded_float(memory.get("score", memory.get("similarity", 0.0)), default=0.0),
            "importance": self._bounded_float(memory.get("importance", 1.0), default=1.0),
            "metadata": metadata,
        }

    @staticmethod
    def _metadata_from_mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    @staticmethod
    def _bounded_float(value: Any, *, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        if minimum is not None:
            number = max(minimum, number)
        if maximum is not None:
            number = min(maximum, number)
        return number

    @staticmethod
    def _queued_id(content: str, *, session_id: str, persona_id: str, importance: float) -> str:
        seed = f"{session_id}\0{persona_id}\0{importance:.6f}\0{content}".encode("utf-8")
        return "queued:" + hashlib.sha256(seed).hexdigest()[:16]


def build_livingmemory_compat_surface(*, query_engine: Any = None, writer: Any = None) -> LivingMemoryCompatSurface:
    """Build plugin attributes compatible with LivingMemory-style integrations."""
    memory_engine = WaveMemoryLivingMemoryFacade(query_engine=query_engine, writer=writer)
    initializer = SimpleNamespace(is_initialized=True, memory_engine=memory_engine)
    return LivingMemoryCompatSurface(memory_engine=memory_engine, initializer=initializer)


__all__ = ["LivingMemoryCompatSurface", "WaveMemoryLivingMemoryFacade", "build_livingmemory_compat_surface"]
