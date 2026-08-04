"""可审计的 scoped Belief 证据置信度计算。

分数表示当前已验证经历对某个主观判断的支持强度，而不是客观真理概率。
此模块不依赖数据库或 LLM，方便单独测试和版本化演进。
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


POLICY_VERSION = "evidence-v1"
ACTIVATION_MIN_SUPPORT_WINDOWS = 2
ACTIVATION_MIN_CONFIDENCE = 0.55

_COMPONENT_WEIGHTS = {
    "evidence": 0.30,
    "frequency": 0.15,
    "source": 0.17,
    "span": 0.08,
    "recency": 0.05,
    "consistency": 0.25,
}


def _clamp(value: Any, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, numeric))


def _positive_ids(values: Any) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return ()
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            memory_id = int(value)
        except (TypeError, ValueError):
            continue
        if memory_id > 0 and memory_id not in seen:
            seen.add(memory_id)
            result.append(memory_id)
    return tuple(result)


def _strings(values: Any) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        values = [values]
    if not isinstance(values, Iterable):
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _as_observation(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    polarity = str(raw.get("polarity") or "").strip().lower()
    if polarity not in {"support", "challenge"}:
        return None
    window_key = str(raw.get("window_key") or "").strip()
    if not window_key:
        return None
    try:
        started_at = float(raw.get("window_started_at") or raw.get("observed_at") or 0.0)
    except (TypeError, ValueError):
        started_at = 0.0
    try:
        ended_at = float(raw.get("window_ended_at") or raw.get("observed_at") or started_at)
    except (TypeError, ValueError):
        ended_at = started_at
    if ended_at < started_at:
        started_at, ended_at = ended_at, started_at
    return {
        "polarity": polarity,
        "window_key": window_key,
        "memory_ids": _positive_ids(raw.get("memory_ids") or raw.get("evidence_memory_ids") or ()),
        "participants": _strings(raw.get("participants") or raw.get("sender_ids") or ()),
        "window_started_at": max(0.0, started_at),
        "window_ended_at": max(0.0, ended_at),
    }


def calculate_confidence(
    observations: Sequence[Mapping[str, Any]], *, now: float | None = None,
) -> dict[str, Any]:
    """基于真实、已落库的经历观察重算支持强度。

    ``support`` 与 ``challenge`` 都以经历窗口去重；同一窗口中的多条消息会提升
    frequency，但不会被当作多段独立经历。返回的 components 可直接保存到
    scoped belief provenance。
    """
    normalized = [item for item in (_as_observation(raw) for raw in observations) if item]
    support = [item for item in normalized if item["polarity"] == "support"]
    challenge = [item for item in normalized if item["polarity"] == "challenge"]
    support_windows = {item["window_key"] for item in support}
    challenge_windows = {item["window_key"] for item in challenge}
    support_ids = {memory_id for item in support for memory_id in item["memory_ids"]}
    challenge_ids = {memory_id for item in challenge for memory_id in item["memory_ids"]}
    participants = {participant for item in support for participant in item["participants"]}

    support_window_count = len(support_windows)
    challenge_window_count = len(challenge_windows)
    support_message_count = len(support_ids)
    challenge_message_count = len(challenge_ids)

    if not support_window_count:
        components = {key: 0.0 for key in (*_COMPONENT_WEIGHTS, "confidence")}
        return {
            "policy_version": POLICY_VERSION,
            "components": components,
            "summary": {
                "support_windows": 0,
                "challenge_windows": challenge_window_count,
                "support_messages": 0,
                "challenge_messages": challenge_message_count,
                "distinct_sources": 0,
                "span_seconds": 0.0,
                "latest_support_at": None,
            },
            "activation_eligible": False,
        }

    # 1/2/3 个独立经历窗口分别是 50%/75%/87.5%，其后逐步饱和。
    evidence = 1.0 - (0.5 ** support_window_count)
    # 实际引用的消息数量，而非 LLM 或摘要伪造的次数；八条后饱和。
    frequency = min(1.0, support_message_count / 8.0)

    support_started = [item["window_started_at"] for item in support if item["window_started_at"] > 0]
    support_ended = [item["window_ended_at"] for item in support if item["window_ended_at"] > 0]
    latest_support_at = max(support_ended) if support_ended else 0.0
    earliest_support_at = min(support_started) if support_started else latest_support_at
    span_seconds = max(0.0, latest_support_at - earliest_support_at)
    span_days = span_seconds / 86_400.0

    # 独立来源不只看发送者，也会保留跨 consolidation 窗口与时间跨度信息。
    source = (
        0.70 * min(1.0, len(participants) / 3.0)
        + 0.20 * min(1.0, support_window_count / 3.0)
        + 0.10 * min(1.0, span_days / 21.0)
    )
    span = 1.0 - math.exp(-span_days / 21.0) if span_days > 0 else 0.0

    reference_time = float(now if now is not None else time.time())
    age_days = max(0.0, (reference_time - latest_support_at) / 86_400.0) if latest_support_at else 365.0
    # 新鲜度仅占 5% 权重，长期信念不会因为时间流逝被自动清空。
    recency = 0.20 + 0.80 * math.exp(-age_days / 90.0) if latest_support_at else 0.0

    # 无反证时最高只有 90%，避免“没记录到反例”被误解为绝对正确。
    total_window_count = support_window_count + challenge_window_count
    support_ratio = support_window_count / total_window_count if total_window_count else 0.0
    consistency = 0.55 + 0.35 * support_ratio

    components = {
        "evidence": _clamp(evidence),
        "frequency": _clamp(frequency),
        "source": _clamp(source),
        "span": _clamp(span),
        "recency": _clamp(recency),
        "consistency": _clamp(consistency),
    }
    confidence = sum(components[key] * weight for key, weight in _COMPONENT_WEIGHTS.items())
    components["confidence"] = _clamp(confidence)
    return {
        "policy_version": POLICY_VERSION,
        "components": components,
        "summary": {
            "support_windows": support_window_count,
            "challenge_windows": challenge_window_count,
            "support_messages": support_message_count,
            "challenge_messages": challenge_message_count,
            "distinct_sources": len(participants),
            "span_seconds": span_seconds,
            "latest_support_at": latest_support_at or None,
        },
        "activation_eligible": (
            support_window_count >= ACTIVATION_MIN_SUPPORT_WINDOWS
            and components["confidence"] >= ACTIVATION_MIN_CONFIDENCE
        ),
    }


def is_activation_eligible(provenance: Mapping[str, Any] | None) -> bool:
    """验证 evidence-v1 信念能否被人工激活；旧数据不会伪造资格。"""
    if not isinstance(provenance, Mapping):
        return False
    if provenance.get("confidence_policy_version") != POLICY_VERSION:
        return False
    if provenance.get("tag_chain_status") != "complete":
        return False
    if not provenance.get("source_tags") or not provenance.get("evidence"):
        return False
    components = provenance.get("confidence_components")
    summary = provenance.get("confidence_evidence")
    if not isinstance(components, Mapping) or not isinstance(summary, Mapping):
        return False
    try:
        confidence = float(components.get("confidence", 0.0))
        support_windows = int(summary.get("support_windows", 0))
    except (TypeError, ValueError):
        return False
    return (
        bool(provenance.get("activation_eligible"))
        and support_windows >= ACTIVATION_MIN_SUPPORT_WINDOWS
        and confidence >= ACTIVATION_MIN_CONFIDENCE
    )


__all__ = [
    "ACTIVATION_MIN_CONFIDENCE",
    "ACTIVATION_MIN_SUPPORT_WINDOWS",
    "POLICY_VERSION",
    "calculate_confidence",
    "is_activation_eligible",
]
