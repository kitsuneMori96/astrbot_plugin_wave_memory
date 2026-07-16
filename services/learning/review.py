"""学习候选审核服务。

审核只决定候选是否被认可；真正写入 memory/fact 等领域对象由晋升编排器
异步执行，二者不能用一个 promoted 标记混淆。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

try:
    from ...engine.db.learning_repository import LearningRepositories
    from ...engine.db.learning_types import ReviewStatus, TargetKind, enum_value
except ImportError:  # 兼容独立测试/外部调用 services.learning
    from engine.db.learning_repository import LearningRepositories
    from engine.db.learning_types import ReviewStatus, TargetKind, enum_value

from .dedicated_review import DedicatedReviewBridge, DedicatedReviewResult
from .fewshot_contract import FEWSHOT_CANDIDATE_TYPE, validate_fewshot_candidate_contract


class LearningReviewError(ValueError):
    """候选审核状态或作用域错误。"""


class LearningReviewService:
    """记录审核审计，并为批准候选原子创建晋升队列。"""

    _TARGETS: Mapping[str, tuple[str, ...]] = {
        "worldview_internalization": (TargetKind.MEMORY.value,),
        "book_experience_episode": (TargetKind.BOOK_EXPERIENCE_EPISODE.value,),
        "few_shot_style": (TargetKind.FEW_SHOT.value,),
        "fact": (TargetKind.FACT.value,),
        "relationship": (TargetKind.RELATIONSHIP.value,),
        "book_lore": (TargetKind.BOOK_LORE.value,),
        "correction_learning": (TargetKind.MEMORY.value, TargetKind.FACT.value),
        "jargon_candidate": (TargetKind.JARGON_REVIEW.value,),
        "belief_candidate": (TargetKind.BELIEF_REVIEW.value,),
    }
    _DEDICATED = frozenset({"jargon_candidate", "belief_candidate"})

    def __init__(
        self,
        repositories: LearningRepositories,
        *,
        now: Callable[[], float] | None = None,
        policy_version: str = "v1",
        dedicated_review_bridge: DedicatedReviewBridge | None = None,
    ):
        self.repositories = repositories
        self.now = now or time.time
        self.policy_version = self._policy_version(policy_version)
        self.dedicated_review_bridge = dedicated_review_bridge

    @staticmethod
    def _policy_version(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("policy_version is required")
        return value

    @staticmethod
    def _reviewer(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("reviewer is required")
        return value

    @staticmethod
    def make_idempotency_key(candidate_id: int, target_kind: str, policy_version: str) -> str:
        return f"candidate:{int(candidate_id)}:target:{target_kind}:policy:{policy_version}"

    def _promotion_specs(
        self,
        candidate: dict[str, Any],
        *,
        reviewer: str,
        dedicated_result: DedicatedReviewResult | None = None,
    ) -> list[dict[str, Any]]:
        candidate_type = str(candidate.get("candidate_type") or "")
        try:
            target_kinds = self._TARGETS[candidate_type]
        except KeyError as exc:
            raise LearningReviewError(f"candidate type has no promotion policy: {candidate_type}") from exc
        delegated = candidate_type in self._DEDICATED
        return [
            {
                "target_kind": target_kind,
                "target_id": dedicated_result.target_id if dedicated_result is not None else None,
                "idempotency_key": self.make_idempotency_key(
                    candidate["id"], target_kind, self.policy_version
                ),
                "promotion_status": (
                    "waiting_dedicated_review" if delegated else "queued"
                ),
                "metadata": {
                    "policy_version": self.policy_version,
                    "candidate_type": candidate_type,
                    "reviewer": reviewer,
                    "dedicated_review": delegated,
                    **(dedicated_result.as_metadata() if dedicated_result is not None else {}),
                },
            }
            for target_kind in target_kinds
        ]

    def review(
        self,
        candidate_id: int,
        *,
        bot_id: str,
        action: str | ReviewStatus | None = None,
        reviewer: str,
        note: str | None = None,
        review_note: str | None = None,
        reason: str | None = None,
        review_status: str | ReviewStatus | None = None,
        status: str | ReviewStatus | None = None,
    ) -> dict[str, Any]:
        """审核候选；approve/delegate 与晋升记录在同一数据库事务中提交。"""
        reviewer = self._reviewer(reviewer)
        note = note if note is not None else (review_note if review_note is not None else reason)
        selected_action = (
            review_status if review_status is not None else (status if status is not None else action)
        )
        if selected_action is None:
            raise LearningReviewError("review action is required")
        action_text = str(selected_action.value if isinstance(selected_action, ReviewStatus) else selected_action).strip().lower()
        action_text = {
            "approve": ReviewStatus.APPROVED.value,
            "reject": ReviewStatus.REJECTED.value,
            "ignore": ReviewStatus.IGNORED.value,
            "delegate": ReviewStatus.DELEGATED.value,
        }.get(action_text, action_text)
        status = enum_value(action_text, ReviewStatus, "review_status")
        candidate = self.repositories.candidates.get(candidate_id, bot_id=bot_id)
        if not candidate:
            raise LearningReviewError("candidate not found for bot_id")
        if status == ReviewStatus.DELEGATED.value and candidate["candidate_type"] not in self._DEDICATED:
            raise LearningReviewError("only jargon/belief candidates may be delegated")
        if (
            candidate["candidate_type"] == FEWSHOT_CANDIDATE_TYPE
            and status == ReviewStatus.APPROVED.value
        ):
            validate_fewshot_candidate_contract(candidate, bot_id=bot_id)
        # 专属审核候选即使点击 approve 也只能进入 delegated，占位状态由领域审核回写。
        dedicated_result = None
        if candidate["candidate_type"] in self._DEDICATED and status in {
            ReviewStatus.APPROVED.value,
            ReviewStatus.DELEGATED.value,
        }:
            status = ReviewStatus.DELEGATED.value
            if self.dedicated_review_bridge is not None:
                # 重复点击学习中心批准不得重复创建领域候选；已存在的 promotion
                # 元数据就是桥接关联的幂等快照。
                existing = self.repositories.promotions.list_for_candidate(candidate_id, bot_id=bot_id)
                existing_dedicated = next(
                    (item for item in existing if item.get("target_kind") == TargetKind.JARGON_REVIEW.value
                     or item.get("target_kind") == TargetKind.BELIEF_REVIEW.value),
                    None,
                )
                if existing_dedicated:
                    metadata = existing_dedicated.get("metadata") or {}
                    dedicated_result = DedicatedReviewResult(
                        candidate["candidate_type"],
                        existing_dedicated.get("target_id") or metadata.get("dedicated_candidate_id"),
                        str(metadata.get("dedicated_status") or "unknown"),
                        metadata.get("dedicated_url"),
                        metadata.get("dedicated_error"),
                    )
                else:
                    dedicated_result = self.dedicated_review_bridge.delegate(candidate, bot_id=bot_id)
        promotions = self._promotion_specs(
            candidate,
            reviewer=reviewer,
            dedicated_result=dedicated_result,
        ) if status in {
            ReviewStatus.APPROVED.value,
            ReviewStatus.DELEGATED.value,
        } else []
        return self.repositories.review_candidate(
            candidate_id,
            bot_id=bot_id,
            review_status=status,
            reviewer=reviewer,
            reviewed_at=float(self.now()),
            review_note=note,
            promotions=promotions,
        )

    def approve(self, candidate_id: int, *, bot_id: str, reviewer: str, note: str | None = None):
        return self.review(
            candidate_id,
            bot_id=bot_id,
            action=ReviewStatus.APPROVED,
            reviewer=reviewer,
            note=note,
        )

    def reject(self, candidate_id: int, *, bot_id: str, reviewer: str, note: str | None = None):
        return self.review(
            candidate_id,
            bot_id=bot_id,
            action=ReviewStatus.REJECTED,
            reviewer=reviewer,
            note=note,
        )

    def ignore(self, candidate_id: int, *, bot_id: str, reviewer: str, note: str | None = None):
        return self.review(
            candidate_id,
            bot_id=bot_id,
            action=ReviewStatus.IGNORED,
            reviewer=reviewer,
            note=note,
        )

    def delegate(self, candidate_id: int, *, bot_id: str, reviewer: str, note: str | None = None):
        return self.review(
            candidate_id,
            bot_id=bot_id,
            action=ReviewStatus.DELEGATED,
            reviewer=reviewer,
            note=note,
        )

    approve_candidate = approve
    reject_candidate = reject
    ignore_candidate = ignore
    delegate_candidate = delegate


__all__ = ["LearningReviewError", "LearningReviewService"]
