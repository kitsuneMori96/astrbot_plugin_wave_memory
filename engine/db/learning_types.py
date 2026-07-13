"""学习中心集中枚举与持久化值校验。"""

from __future__ import annotations

from enum import Enum
from typing import TypeVar


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ReviewStatus(_StringEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    IGNORED = "ignored"
    DELEGATED = "delegated"


class PromotionStatus(_StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    DELEGATED = "delegated"
    WAITING_DEDICATED_REVIEW = "waiting_dedicated_review"


class CandidateType(_StringEnum):
    WORLDVIEW_INTERNALIZATION = "worldview_internalization"
    BOOK_EXPERIENCE_EPISODE = "book_experience_episode"
    CORRECTION_LEARNING = "correction_learning"
    FEW_SHOT_STYLE = "few_shot_style"
    FACT = "fact"
    RELATIONSHIP = "relationship"
    BOOK_LORE = "book_lore"
    JARGON_CANDIDATE = "jargon_candidate"
    BELIEF_CANDIDATE = "belief_candidate"


class TargetKind(_StringEnum):
    MEMORY = "memory"
    FACT = "fact"
    FEW_SHOT = "few_shot"
    RELATIONSHIP = "relationship"
    EXPERIENCE_EPISODE = "experience_episode"
    BOOK_EXPERIENCE_EPISODE = "book_experience_episode"
    BOOK_LORE = "book_lore"
    JARGON_REVIEW = "jargon_review"
    BELIEF_REVIEW = "belief_review"


_EnumT = TypeVar("_EnumT", bound=_StringEnum)


def enum_value(value: str | _EnumT, enum_type: type[_EnumT], field_name: str) -> str:
    """返回规范化枚举值；未知值立即拒绝，避免脏状态进入数据库。"""
    raw = value.value if isinstance(value, enum_type) else str(value or "").strip().lower()
    try:
        return enum_type(raw).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"invalid {field_name}: {raw!r}; expected one of: {allowed}") from exc


__all__ = [
    "CandidateType",
    "PromotionStatus",
    "ReviewStatus",
    "TargetKind",
    "enum_value",
]
