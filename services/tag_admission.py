"""Write-path Tag admission: exact reuse, high-confidence semantic reuse, reject.

This is the enforcement layer that was previously only a prompt wish.  Callers
feed raw LLM tags plus optional Catalog neighbors; the pure decision function
returns either a reusable Catalog identity, a createable formal tag, or a reject.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# High enough that only near-duplicate embeddings collapse into one Catalog id.
DEFAULT_SEMANTIC_REUSE_THRESHOLD = 0.92
# Below this, a brand-new formal Catalog row is not created on the write path.
DEFAULT_MIN_CREATE_CONFIDENCE = 0.55
# Cap neighbor scan so admission never walks the entire Catalog in Python.
DEFAULT_MAX_SEMANTIC_CANDIDATES = 64

_STOP_WORDS = frozenset({
    "东西", "事情", "问题", "情况", "感觉", "觉得", "可能", "应该",
    "这个", "那个", "什么", "一下", "今天", "明天", "哈哈", "呵呵",
})
_TRAILING_PARTICLES = re.compile(r"[的了着吗呢吧啊呀]+$")
_WHITESPACE = re.compile(r"\s+")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\ufeff]")


@dataclass(frozen=True, slots=True)
class CatalogNeighbor:
    """One active Catalog row available for exact or semantic reuse."""

    catalog_id: int
    normalized_name: str
    display_name: str
    tag_type: str
    embedding: Sequence[float] | None = None


@dataclass(frozen=True, slots=True)
class TagAdmissionDecision:
    """Result of admitting one raw LLM tag into the formal write path."""

    action: str  # reuse | create | reject
    name: str
    tag_type: str
    confidence: float
    catalog_id: int | None = None
    reason: str = ""
    embedding: Sequence[float] | None = None
    raw_name: str = ""

    @property
    def admitted(self) -> bool:
        return self.action in {"reuse", "create"}


def normalize_admission_name(value: Any) -> str:
    """Stronger write-path normalization than display-preserving Catalog NFKC."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _ZERO_WIDTH.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return ""
    # Latin identifiers collapse case; CJK is unchanged by casefold.
    folded = text.casefold()
    stripped = _TRAILING_PARTICLES.sub("", folded).strip()
    return stripped or folded


def _as_confidence(value: Any, default: float = 0.8) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(max(number, 0.0), 1.0)


def _as_type(value: Any) -> str:
    text = str(value or "keyword").strip().casefold()
    return text or "keyword"


def _decode_embedding(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        if isinstance(value, (bytes, bytearray, memoryview)):
            import numpy as np

            array = np.frombuffer(bytes(value), dtype=np.float32).astype(float)
            values = array.tolist()
        else:
            values = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not values or not all(math.isfinite(item) for item in values):
        return None
    return values


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return -1.0
    return dot / math.sqrt(left_norm * right_norm)


def quality_reject_reason(name: str, tag_type: str, confidence: float) -> str | None:
    """Return a reject reason when the raw tag must never become a formal row."""
    if not name or len(name) < 2:
        return "too_short"
    if len(name) > 20:
        return "too_long"
    if confidence < 0.4:
        return "confidence_too_low"
    if name in _STOP_WORDS:
        return "stop_word"
    if re.match(r"^[\d\s\W]+$", name):
        return "non_lexical"
    if len(name) > 5 and tag_type == "keyword":
        return "keyword_too_long"
    return None


def decide_tag_admission(
    raw_tag: Mapping[str, Any],
    *,
    catalog: Sequence[CatalogNeighbor] = (),
    min_create_confidence: float = DEFAULT_MIN_CREATE_CONFIDENCE,
    semantic_reuse_threshold: float = DEFAULT_SEMANTIC_REUSE_THRESHOLD,
    max_semantic_candidates: int = DEFAULT_MAX_SEMANTIC_CANDIDATES,
) -> TagAdmissionDecision:
    """Admit one raw tag as reuse, create, or reject without performing I/O."""
    raw_name = str(raw_tag.get("name") or "").strip()
    tag_type = _as_type(raw_tag.get("type") or raw_tag.get("tag_type"))
    confidence = _as_confidence(raw_tag.get("confidence", raw_tag.get("score", 0.8)))
    embedding = _decode_embedding(raw_tag.get("embedding"))
    normalized = normalize_admission_name(raw_name)

    if not normalized:
        return TagAdmissionDecision(
            action="reject",
            name="",
            tag_type=tag_type,
            confidence=confidence,
            reason="empty_name",
            embedding=embedding,
            raw_name=raw_name,
        )

    reject = quality_reject_reason(normalized, tag_type, confidence)
    if reject is not None:
        return TagAdmissionDecision(
            action="reject",
            name=normalized,
            tag_type=tag_type,
            confidence=confidence,
            reason=reject,
            embedding=embedding,
            raw_name=raw_name,
        )

    # Exact Catalog hit wins first; preserve the stored display name/type.
    for neighbor in catalog:
        if (
            neighbor.normalized_name == normalized
            and _as_type(neighbor.tag_type) == tag_type
            and int(neighbor.catalog_id) > 0
        ):
            return TagAdmissionDecision(
                action="reuse",
                name=str(neighbor.display_name or neighbor.normalized_name),
                tag_type=_as_type(neighbor.tag_type),
                confidence=confidence,
                catalog_id=int(neighbor.catalog_id),
                reason="exact_normalized_match",
                embedding=embedding,
                raw_name=raw_name,
            )

    # High-confidence near-duplicate vectors of the same type reuse Catalog ids.
    if embedding is not None:
        best: tuple[float, CatalogNeighbor] | None = None
        scanned = 0
        for neighbor in catalog:
            if scanned >= max(1, int(max_semantic_candidates)):
                break
            if _as_type(neighbor.tag_type) != tag_type or neighbor.embedding is None:
                continue
            scanned += 1
            score = _cosine(embedding, neighbor.embedding)
            if score < float(semantic_reuse_threshold):
                continue
            if best is None or score > best[0]:
                best = (score, neighbor)
        if best is not None:
            score, neighbor = best
            return TagAdmissionDecision(
                action="reuse",
                name=str(neighbor.display_name or neighbor.normalized_name),
                tag_type=_as_type(neighbor.tag_type),
                confidence=confidence,
                catalog_id=int(neighbor.catalog_id),
                reason=f"semantic_reuse:{score:.3f}",
                embedding=embedding,
                raw_name=raw_name,
            )

    if confidence < float(min_create_confidence):
        return TagAdmissionDecision(
            action="reject",
            name=normalized,
            tag_type=tag_type,
            confidence=confidence,
            reason="create_confidence_below_threshold",
            embedding=embedding,
            raw_name=raw_name,
        )

    # Prefer the original display text when it is already normalized-equivalent;
    # otherwise keep the normalized form so Catalog uniqueness collapses variants.
    display = raw_name if normalize_admission_name(raw_name) == normalized else normalized
    return TagAdmissionDecision(
        action="create",
        name=display,
        tag_type=tag_type,
        confidence=confidence,
        catalog_id=None,
        reason="create_formal",
        embedding=embedding,
        raw_name=raw_name,
    )


def admit_tag_batch(
    tags: Sequence[Mapping[str, Any]] | None,
    *,
    catalog: Sequence[CatalogNeighbor] = (),
    min_create_confidence: float = DEFAULT_MIN_CREATE_CONFIDENCE,
    semantic_reuse_threshold: float = DEFAULT_SEMANTIC_REUSE_THRESHOLD,
) -> tuple[list[dict[str, Any]], list[TagAdmissionDecision]]:
    """Filter a batch into write-ready dicts plus the full decision audit trail."""
    decisions: list[TagAdmissionDecision] = []
    admitted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in tags or ():
        if not isinstance(raw, Mapping):
            continue
        decision = decide_tag_admission(
            raw,
            catalog=catalog,
            min_create_confidence=min_create_confidence,
            semantic_reuse_threshold=semantic_reuse_threshold,
        )
        decisions.append(decision)
        if not decision.admitted:
            continue
        key = (normalize_admission_name(decision.name), decision.tag_type)
        if key in seen:
            continue
        seen.add(key)
        payload: dict[str, Any] = {
            "name": decision.name,
            "type": decision.tag_type,
            "confidence": decision.confidence,
            "admission": decision.action,
            "admission_reason": decision.reason,
        }
        if decision.catalog_id is not None:
            payload["catalog_id"] = decision.catalog_id
        if decision.embedding is not None:
            payload["embedding"] = list(decision.embedding)
        admitted.append(payload)
    return admitted, decisions


__all__ = [
    "CatalogNeighbor",
    "DEFAULT_MIN_CREATE_CONFIDENCE",
    "DEFAULT_SEMANTIC_REUSE_THRESHOLD",
    "TagAdmissionDecision",
    "admit_tag_batch",
    "decide_tag_admission",
    "normalize_admission_name",
    "quality_reject_reason",
]
