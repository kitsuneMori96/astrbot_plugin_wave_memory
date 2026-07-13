"""学习来源适配器合约与注册表。

适配器只负责把外部输入转换为标准化输入，不应依赖或写入任何最终领域对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterable, Iterable, Mapping


@dataclass(frozen=True)
class LearningSourceItem:
    """来源适配器的唯一输出格式。cursor 代表处理该输入后的提交点。"""

    content: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source_fingerprint: str = ""
    cursor: dict[str, Any] | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.content or "").strip():
            raise ValueError("source item content is required")
        if not isinstance(self.evidence, dict):
            raise TypeError("source item evidence must be a dict")
        if not str(self.source_fingerprint or "").strip():
            raise ValueError("source item source_fingerprint is required")
        if self.cursor is not None and not isinstance(self.cursor, dict):
            raise TypeError("source item cursor must be a dict or None")
        if not isinstance(self.metadata, dict):
            raise TypeError("source item metadata must be a dict")

    @classmethod
    def from_value(cls, value: "LearningSourceItem | Mapping[str, Any]") -> "LearningSourceItem":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("adapter output must be LearningSourceItem or mapping")
        # fingerprint/fingerprint 是早期适配器常用别名，统一为持久化字段名。
        return cls(
            content=str(value.get("content", "")),
            evidence=dict(value.get("evidence") or {}),
            source_fingerprint=str(value.get("source_fingerprint", value.get("fingerprint", ""))),
            cursor=value.get("cursor", value.get("next_cursor")),
            reason=str(value.get("reason") or ""),
            metadata=dict(value.get("metadata") or {}),
        )


class LearningSourceAdapter:
    """来源适配器合约；实现 collect，不直接写 repository 或领域对象。

    ``fetch`` 是兼容别名：外部适配器可实现 collect 或 fetch 任一方法。
    """

    source_type: str = ""

    def collect(
        self,
        *,
        bot_id: str,
        source: Mapping[str, Any],
        job: Mapping[str, Any],
        cursor: Mapping[str, Any] | None,
    ) -> Iterable[LearningSourceItem | Mapping[str, Any]] | AsyncIterable[LearningSourceItem | Mapping[str, Any]]:
        fetch = getattr(self, "fetch", None)
        if callable(fetch):
            return fetch(bot_id=bot_id, source=source, job=job, cursor=cursor)
        raise NotImplementedError("adapter must implement collect or fetch")

    def fetch(self, **kwargs):
        raise NotImplementedError("adapter must implement collect or fetch")


class LearningSourceRegistry:
    """按 source_type 保存适配器实例（同一适配器可服务多个 bot，但不共享状态）。"""

    def __init__(self) -> None:
        self._adapters: dict[str, LearningSourceAdapter] = {}

    def register(self, adapter_or_type, adapter: LearningSourceAdapter | None = None) -> LearningSourceAdapter:
        if adapter is None:
            adapter = adapter_or_type
            source_type = getattr(adapter, "source_type", "")
        else:
            source_type = adapter_or_type
        source_type = str(source_type or "").strip()
        if not source_type:
            raise ValueError("source_type is required")
        if not isinstance(adapter, LearningSourceAdapter):
            # 允许结构化 duck typing，便于插件按协议实现而不继承基类。
            if not callable(getattr(adapter, "collect", None)) and not callable(getattr(adapter, "fetch", None)):
                raise TypeError("adapter must implement collect or fetch")
        self._adapters[source_type] = adapter
        return adapter

    def unregister(self, source_type: str) -> None:
        self._adapters.pop(str(source_type).strip(), None)

    def resolve(self, source_type: str) -> LearningSourceAdapter:
        key = str(source_type or "").strip()
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise KeyError(f"no learning source adapter registered for {key!r}") from exc

    def get(self, source_type: str, default=None):
        return self._adapters.get(str(source_type or "").strip(), default)

    def __contains__(self, source_type: str) -> bool:
        return str(source_type or "").strip() in self._adapters

    def __len__(self) -> int:
        return len(self._adapters)


# 简短别名便于来源插件声明类型，同时保留规范名称作为公开合约。
SourceItem = LearningSourceItem
LearningSourceInput = LearningSourceItem

__all__ = [
    "LearningSourceAdapter",
    "LearningSourceInput",
    "LearningSourceItem",
    "LearningSourceRegistry",
    "SourceItem",
]
