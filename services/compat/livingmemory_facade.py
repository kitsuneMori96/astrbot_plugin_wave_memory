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

try:  # 兼容插件包导入和仓库测试直接导入
    from ...domain.scope import RuntimeScope, ScopeValidationError
except ImportError:  # pragma: no cover - 由仓库测试直接导入 services 使用
    from domain.scope import RuntimeScope, ScopeValidationError


@dataclass(frozen=True)
class LivingMemoryCompatSurface:
    """Plugin-level compatibility attributes exposed to memory-plugin callers."""

    memory_engine: "WaveMemoryLivingMemoryFacade"
    initializer: SimpleNamespace


def _project_group_scope(scope: RuntimeScope | None) -> tuple[str, str, str]:
    """Project only a resolved group Scope; never infer it from legacy fields."""
    if not isinstance(scope, RuntimeScope):
        raise ScopeValidationError("scope_required", "LivingMemory read/write requires RuntimeScope")
    if scope.visibility != "group" or scope.session is None:
        raise ScopeValidationError(
            "legacy_writer_scope_visibility_unsupported",
            "LivingMemory currently supports group RuntimeScope only",
        )
    return scope.bot_id, scope.session.conversation_id, scope.session.id


def _project_group_subject_scope(scope: RuntimeScope | None) -> tuple[str, str, str, str]:
    """Project a group Scope and its verified user subject for compatibility writes."""
    bot_id, group_id, canonical_session_id = _project_group_scope(scope)
    assert scope is not None and scope.session is not None
    prefix = f"{scope.session.platform_id}:user:"
    principal = scope.subject_principal_id or ""
    if not principal.startswith(prefix) or principal == prefix:
        raise ScopeValidationError(
            "scope_subject_required",
            "LivingMemory write requires a scoped platform user",
        )
    return bot_id, group_id, canonical_session_id, principal[len(prefix):]


class WaveMemoryLivingMemoryFacade:
    """Compatibility memory engine backed by WaveMemory query/write internals."""

    def __init__(
        self,
        *,
        query_engine: Any = None,
        writer: Any = None,
        default_session_id: str | None = None,
        default_sender_name: str = "LivingMemory兼容",
        now: Callable[[], float] | None = None,
        max_k: int = 50,
    ) -> None:
        self.query_engine = query_engine
        self.writer = writer
        self.default_session_id = str(default_session_id or "").strip()
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
        scope: RuntimeScope | None = None,
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

        try:
            bot_id, group_id, canonical_session_id = _project_group_scope(scope)
        except ScopeValidationError as exc:
            self.last_error = exc.reason_code
            return []
        declared_session = str(session_id or "").strip()
        if declared_session and declared_session not in {group_id, canonical_session_id}:
            self.last_error = "scope_session_mismatch"
            return []
        declared_persona = str(persona_id or "").strip()
        if declared_persona and declared_persona != bot_id:
            self.last_error = "scope_bot_mismatch"
            return []

        top_k = self._bounded_int(k, default=5, minimum=1, maximum=self.max_k)
        try:
            memories = await self.query_engine.query(
                text=text,
                group_id=group_id,
                top_k=top_k,
                scope=scope,
            )
        except Exception as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            logger.debug(f"[WaveMemory] LivingMemory facade search failed: {self.last_error}")
            return []

        return [
            self._format_memory_result(
                memory,
                session_id=canonical_session_id,
                persona_id=bot_id,
            )
            for memory in (memories or [])
        ]

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.7,
        metadata: Mapping[str, Any] | None = None,
        scope: RuntimeScope | None = None,
    ) -> str:
        """Queue a Scope-bound memory write and return a stable queued id.

        The legacy ``session_id``/``persona_id`` fields are assertions only; they
        may match a supplied Scope but can never construct or override one.
        """
        self.last_error = None
        text = str(content or "").strip()
        if not text:
            return ""
        if self.writer is None or not hasattr(self.writer, "enqueue"):
            self.last_error = "writer_unavailable"
            return ""
        try:
            bot_id, group_id, canonical_session_id, sender_id = _project_group_subject_scope(scope)
        except ScopeValidationError as exc:
            self.last_error = exc.reason_code
            return ""

        declared_session = str(session_id or "").strip()
        if declared_session and declared_session not in {group_id, canonical_session_id}:
            self.last_error = "scope_session_mismatch"
            return ""
        declared_persona = str(persona_id or "").strip()
        if declared_persona and declared_persona != bot_id:
            self.last_error = "scope_bot_mismatch"
            return ""

        persona = declared_persona or bot_id
        safe_importance = self._bounded_float(importance, default=0.7, minimum=0.0, maximum=3.0)
        queued_id = self._queued_id(
            text,
            session_id=f"{bot_id}:{canonical_session_id}",
            persona_id=persona,
            importance=safe_importance,
        )
        item_metadata = self._metadata_from_mapping(metadata)
        item_metadata.update({
            "source": "wave_memory_livingmemory_compat",
            "origin_kind": "livingmemory_compat",
            "session_id": group_id,
            "canonical_session_id": canonical_session_id,
            "persona_id": persona,
            "bot_id": bot_id,
            "queued_id": queued_id,
        })
        item = {
            "scope": scope,
            "group_id": group_id,
            "sender_id": sender_id,
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
