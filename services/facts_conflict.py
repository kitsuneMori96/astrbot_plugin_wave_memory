"""Facts 冲突分类与 scoped observation 记录。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FactConflict:
    relation: str
    reason: str
    existing_id: int | None = None


class FactConflictClassifier:
    """按同主体/谓词和有效期判断正式 Facts 关系。"""

    RELATIONS = frozenset({"compatible", "scoped", "conflicts", "supersedes"})

    @staticmethod
    def _overlaps(candidate: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
        c_start = candidate.get("valid_from")
        c_end = candidate.get("valid_until")
        e_start = existing.get("valid_from")
        e_end = existing.get("valid_until")
        # 缺失边界代表开放区间；两个无边界版本默认同时有效。
        return (e_end is None or c_start is None or float(e_end) >= float(c_start)) and (
            c_end is None or e_start is None or float(c_end) >= float(e_start)
        )

    def classify(self, subject: str | Mapping[str, Any], predicate: str | Mapping[str, Any], object: str | None = None, existing: Any = None) -> FactConflict:
        candidate: Mapping[str, Any]
        if isinstance(subject, Mapping):
            candidate = subject
            rows = predicate if isinstance(predicate, (list, tuple)) else ([predicate] if isinstance(predicate, Mapping) else [])
        else:
            candidate = {"subject": subject, "predicate": predicate, "object": object}
            rows = existing or []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("subject") != candidate.get("subject") or row.get("predicate") != candidate.get("predicate"):
                continue
            row_id = row.get("id")
            if row.get("object") == candidate.get("object"):
                return FactConflict("compatible", "same triple", int(row_id) if row_id is not None else None)
            provenance = candidate.get("provenance") if isinstance(candidate.get("provenance"), Mapping) else {}
            supersedes_id = provenance.get("supersedes_fact_id", provenance.get("supersedes_id"))
            if provenance.get("supersedes") is True and (supersedes_id is None or str(supersedes_id) == str(row_id)):
                return FactConflict("supersedes", "explicit replacement", int(row_id) if row_id is not None else None)
            if not self._overlaps(candidate, row):
                return FactConflict("scoped", "non-overlapping validity intervals", int(row_id) if row_id is not None else None)
            return FactConflict("conflicts", "same subject and predicate with different object", int(row_id) if row_id is not None else None)
        return FactConflict("compatible", "no same subject/predicate fact")

    def __call__(self, subject: Any, predicate: Any, object: Any = None, existing: Any = None) -> str:
        return self.classify(subject, predicate, object, existing).relation

__all__ = ['FactConflict', 'FactConflictClassifier']
