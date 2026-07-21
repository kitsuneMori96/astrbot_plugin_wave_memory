"""Read-only helpers to surface historical_audit_summary from relationship evidence.

Never mutates affinity/values/revision. Safe when evidence is missing or machine-only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def parse_evidence_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
        except Exception:
            return []
        if isinstance(loaded, list):
            return list(loaded)
    return []


def extract_historical_audit_summaries(
    evidence: Any,
    *,
    max_items: int = 3,
    max_chars: int = 200,
) -> list[str]:
    """Return human-readable summary strings from evidence JSON list."""
    items = parse_evidence_list(evidence)
    out: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind != "historical_audit_summary":
            continue
        text = str(item.get("summary") or item.get("text") or item.get("narrative") or "").strip()
        if not text:
            continue
        text = text.replace("\n", " ")
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        out.append(text)
        if len(out) >= max(1, int(max_items)):
            break
    return out


def format_evidence_summary_lines(
    evidence: Any,
    *,
    header: str = "可读历史摘要（只读，不改变好感度）",
    max_items: int = 2,
) -> list[str]:
    summaries = extract_historical_audit_summaries(evidence, max_items=max_items)
    if not summaries:
        return []
    lines = [header]
    for s in summaries:
        lines.append(f"- {s}")
    return lines


def relationship_injection_summary_snippet(
    evidence: Any,
    *,
    max_chars: int = 160,
) -> str:
    """One-line snippet for RelationshipChannel injection text."""
    summaries = extract_historical_audit_summaries(evidence, max_items=1, max_chars=max_chars)
    if not summaries:
        return ""
    return summaries[0]


__all__ = [
    "parse_evidence_list",
    "extract_historical_audit_summaries",
    "format_evidence_summary_lines",
    "relationship_injection_summary_snippet",
]
