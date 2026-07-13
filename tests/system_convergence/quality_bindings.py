"""Canonical GateInput-to-legacy promotion composition adapter used only by contracts.

The adapter copies a candidate, maps canonical ``target_scope`` to the current promotion service's
legacy ``scope`` input, records delegation, and calls the supplied real service. It contains no gate,
reason-code, rejection, or write behavior.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromotionCall:
    service_id: int
    operation: str


class _QualityPromotionBinding:
    def __init__(self, *, fact_service: Any = None, worldview_service: Any = None):
        self.fact_service = fact_service
        self.worldview_service = worldview_service
        self.calls: list[PromotionCall] = []

    @staticmethod
    def _legacy_candidate(candidate: dict) -> dict:
        adapted = deepcopy(candidate)
        evidence = adapted.get("evidence")
        if isinstance(evidence, dict) and "target_scope" in evidence:
            evidence["scope"] = evidence["target_scope"]
        return adapted

    def promote_fact(self, *, candidate: dict, bot_id: str, target_kind: str) -> Any:
        self.calls.append(PromotionCall(id(self.fact_service), "fact.promote"))
        return self.fact_service.promote(
            candidate=self._legacy_candidate(candidate),
            bot_id=bot_id,
            target_kind=target_kind,
        )

    def promote_worldview(self, *, candidate: dict, bot_id: str, target_kind: str) -> Any:
        self.calls.append(PromotionCall(id(self.worldview_service), "worldview.promote"))
        return self.worldview_service.promote(
            candidate=self._legacy_candidate(candidate),
            bot_id=bot_id,
            target_kind=target_kind,
        )


def create_quality_promotion_adapter(
    *, fact_service: Any = None, worldview_service: Any = None
) -> _QualityPromotionBinding:
    return _QualityPromotionBinding(
        fact_service=fact_service,
        worldview_service=worldview_service,
    )
