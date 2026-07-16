"""真实 Bot 回复到 scoped FewShot 的正式垂直切片。"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from ...domain.evidence import EvidenceBinding, EvidenceRef
    from ...domain.scope import RuntimeScope
    from ...engine.db.scoped_learning_projection_repo import (
        ScopedBotReplyRepository,
        ScopedFewShotRepository,
    )
except ImportError:  # pragma: no cover
    from domain.evidence import EvidenceBinding, EvidenceRef
    from domain.scope import RuntimeScope
    from engine.db.scoped_learning_projection_repo import (
        ScopedBotReplyRepository,
        ScopedFewShotRepository,
    )

from .candidate_service import LearningCandidateService
from .fewshot_contract import (
    FEWSHOT_BINDING_POLICY,
    FEWSHOT_CANDIDATE_TYPE,
    FEWSHOT_CONTRACT_VERSION,
    FEWSHOT_DERIVATION_CHAIN,
)


class FewShotLifecycleError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = str(code)
        self.reason_code = self.code
        super().__init__(message or self.code)


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _trace_hash(trace: Mapping[str, Any]) -> str:
    payload = {
        "trace_id": trace.get("trace_id"),
        "timestamp": trace.get("timestamp"),
        "message_hash": trace.get("message_hash"),
        "status": trace.get("status"),
        "payload_json": trace.get("payload_json"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _hash_text(canonical)


def _require_scope(scope: RuntimeScope) -> RuntimeScope:
    if not isinstance(scope, RuntimeScope) or scope.visibility != "group" or scope.session is None:
        raise FewShotLifecycleError("fewshot_runtime_scope_required")
    return scope


def _require_trace(trace_store: Any, *, trace_id: str, scope: RuntimeScope) -> dict[str, Any]:
    getter = getattr(trace_store, "get_for_scope", None)
    if not callable(getter):
        raise FewShotLifecycleError("fewshot_trace_store_unavailable")
    trace = getter(str(trace_id or "").strip(), scope)
    if not isinstance(trace, Mapping):
        raise FewShotLifecycleError("fewshot_query_trace_unavailable")
    status = str(trace.get("status") or "").strip().lower()
    if status not in {"ok", "success"} or str(trace.get("error") or "").strip():
        raise FewShotLifecycleError("fewshot_query_trace_failed")
    return dict(trace)


async def _assess(assessor: Any, reply: str) -> Mapping[str, Any]:
    method = getattr(assessor, "evaluate_reply", None)
    if not callable(method):
        raise FewShotLifecycleError("fewshot_quality_assessor_unavailable")
    result = method(reply)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise FewShotLifecycleError("fewshot_quality_assessment_invalid")
    return result


class FewShotReplyCandidateService:
    """只从真实 scoped Bot reply 记忆创建 Learning candidate。"""

    def __init__(
        self,
        candidate_service: LearningCandidateService,
        reply_repository: ScopedBotReplyRepository,
        trace_store: Any,
        quality_assessor: Any,
        *,
        min_score: float = 0.7,
        max_trace_age: float = 900.0,
        policy_version: str = FEWSHOT_CONTRACT_VERSION,
    ) -> None:
        if not isinstance(candidate_service, LearningCandidateService):
            raise TypeError("candidate_service must be LearningCandidateService")
        if not isinstance(reply_repository, ScopedBotReplyRepository):
            raise TypeError("reply_repository must be ScopedBotReplyRepository")
        self.candidate_service = candidate_service
        self.reply_repository = reply_repository
        self.trace_store = trace_store
        self.quality_assessor = quality_assessor
        self.min_score = float(min_score)
        self.max_trace_age = max(0.0, float(max_trace_age))
        self.policy_version = str(policy_version or "").strip()
        if not self.policy_version:
            raise ValueError("policy_version is required")

    async def create_from_reply(
        self,
        *,
        scope: RuntimeScope,
        memory_id: int,
        query_trace_id: str,
    ) -> int:
        scope = _require_scope(scope)
        trace_id = str(query_trace_id or "").strip()
        if not trace_id:
            raise FewShotLifecycleError("fewshot_query_trace_required")
        reply = self.reply_repository.resolve(scope=scope, memory_id=int(memory_id))
        trace = _require_trace(self.trace_store, trace_id=trace_id, scope=scope)
        trace_at = float(trace.get("timestamp") or 0.0)
        reply_at = float(reply.get("captured_at") or 0.0)
        if not math.isfinite(trace_at) or not math.isfinite(reply_at) or trace_at > reply_at:
            raise FewShotLifecycleError("fewshot_trace_reply_order_invalid")
        if reply_at - trace_at > self.max_trace_age:
            raise FewShotLifecycleError("fewshot_query_trace_too_old")

        assessment = await _assess(self.quality_assessor, str(reply["content"]))
        try:
            score = float(assessment.get("score"))
        except (TypeError, ValueError) as exc:
            raise FewShotLifecycleError("fewshot_quality_assessment_invalid") from exc
        raw_traits = assessment.get("traits")
        if (
            not math.isfinite(score)
            or score < self.min_score
            or score > 1.0
            or isinstance(raw_traits, (str, bytes))
            or not isinstance(raw_traits, Sequence)
        ):
            raise FewShotLifecycleError("fewshot_reply_quality_insufficient")
        traits = tuple(dict.fromkeys(str(value or "").strip() for value in raw_traits))
        traits = tuple(value for value in traits if value)
        if not traits:
            raise FewShotLifecycleError("fewshot_reply_quality_insufficient")

        reply_hash = _hash_text(str(reply["content"]))
        refs = (
            EvidenceRef(
                kind="bot_reply_memory",
                id=f"memory:{int(reply['memory_id'])}",
                content_hash=reply_hash,
                captured_at=reply_at,
                source_scope=scope,
                available=True,
            ),
            EvidenceRef(
                kind="query_trace",
                id=f"trace:{trace_id}",
                content_hash=_trace_hash(trace),
                captured_at=trace_at,
                source_scope=scope,
                available=True,
            ),
        )
        bindings = tuple(
            EvidenceBinding(
                evidence_id=ref.id,
                target_scope=scope,
                derivation_chain=FEWSHOT_DERIVATION_CHAIN,
                policy_version=FEWSHOT_BINDING_POLICY,
            )
            for ref in refs
        )
        evidence = {
            "contract_version": FEWSHOT_CONTRACT_VERSION,
            "scope": scope.to_dict(),
            "target_scope": scope.to_dict(),
            "source_reply": {
                "memory_id": int(reply["memory_id"]),
                "content_hash": reply_hash,
                "origin_fingerprint": str(reply.get("origin_fingerprint") or ""),
                "captured_at": reply_at,
            },
            "source_tags": list(reply["source_tags"]),
            "query_trace_id": trace_id,
            "query_trace": {
                "trace_id": trace_id,
                "content_hash": refs[1].content_hash,
                "captured_at": trace_at,
                "message_hash": str(trace.get("message_hash") or ""),
                "status": str(trace.get("status") or ""),
            },
            "score": score,
            "traits": list(traits),
            "evidence_refs": [ref.to_dict() for ref in refs],
            "evidence_bindings": [binding.to_dict() for binding in bindings],
        }
        fingerprint_payload = {
            "scope": scope.to_dict(),
            "memory_id": int(reply["memory_id"]),
            "reply_hash": reply_hash,
            "trace_id": trace_id,
            "policy_version": self.policy_version,
        }
        source_fingerprint = _hash_text(json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ))
        return self.candidate_service.create(
            bot_id=scope.bot_id,
            candidate_type=FEWSHOT_CANDIDATE_TYPE,
            content=str(reply["content"]),
            evidence=evidence,
            source_fingerprint=source_fingerprint,
            reason="high_quality_scoped_bot_reply",
            metadata={
                "fewshot_contract_version": FEWSHOT_CONTRACT_VERSION,
                "quality_policy_version": self.policy_version,
                "source_memory_id": int(reply["memory_id"]),
                "query_trace_id": trace_id,
            },
        )


class FewShotUsageFeedbackService:
    """以真实注入 trace 记录 usage/feedback，并按稳定策略淘汰样例。"""

    def __init__(
        self,
        repository: ScopedFewShotRepository,
        trace_store: Any,
        *,
        negative_feedback_threshold: int = 2,
        policy_version: str = "fewshot-retirement/v1",
    ) -> None:
        if not isinstance(repository, ScopedFewShotRepository):
            raise TypeError("repository must be ScopedFewShotRepository")
        self.repository = repository
        self.trace_store = trace_store
        self.negative_feedback_threshold = max(1, int(negative_feedback_threshold))
        self.policy_version = str(policy_version or "").strip()
        if not self.policy_version:
            raise ValueError("policy_version is required")

    @staticmethod
    def _trace_example_ids(trace: Mapping[str, Any]) -> tuple[int, ...]:
        ids: list[int] = []
        for channel in trace.get("channels") or ():
            if not isinstance(channel, Mapping) or str(channel.get("channel") or "") != "fewshot":
                continue
            try:
                details = json.loads(str(channel.get("details") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                raise FewShotLifecycleError("fewshot_trace_details_invalid")
            for item in details.get("items") or ():
                if not isinstance(item, Mapping):
                    continue
                try:
                    example_id = int(item.get("example_id"))
                except (TypeError, ValueError):
                    continue
                if example_id > 0 and example_id not in ids:
                    ids.append(example_id)
        if not ids:
            raise FewShotLifecycleError("fewshot_trace_has_no_examples")
        return tuple(ids)

    def record_usage(self, *, scope: RuntimeScope, query_trace_id: str) -> tuple[dict[str, Any], ...]:
        scope = _require_scope(scope)
        trace = _require_trace(self.trace_store, trace_id=query_trace_id, scope=scope)
        return self.repository.record_usage(
            scope=scope,
            example_ids=self._trace_example_ids(trace),
            query_trace_id=str(query_trace_id),
            used_at=float(trace.get("timestamp") or 0.0),
        )

    def record_feedback(
        self,
        *,
        scope: RuntimeScope,
        query_trace_id: str,
        example_id: int,
        feedback: str,
        request_id: str,
    ) -> dict[str, Any]:
        scope = _require_scope(scope)
        trace = _require_trace(self.trace_store, trace_id=query_trace_id, scope=scope)
        selected_ids = self._trace_example_ids(trace)
        if int(example_id) not in selected_ids:
            raise FewShotLifecycleError("fewshot_example_not_used_by_trace")
        self.repository.record_usage(
            scope=scope,
            example_ids=selected_ids,
            query_trace_id=str(query_trace_id),
            used_at=float(trace.get("timestamp") or 0.0),
        )
        request_id = str(request_id or "").strip()
        if not request_id:
            raise FewShotLifecycleError("fewshot_feedback_request_id_required")
        key = f"{scope.bot_id}:{scope.session.id}:{query_trace_id}:{int(example_id)}:{request_id}"
        row = self.repository.record_feedback(
            scope=scope,
            example_id=int(example_id),
            query_trace_id=str(query_trace_id),
            feedback=feedback,
            idempotency_key=key,
        )
        normalized = str(feedback or "").strip().lower()
        should_retire = normalized == "misleading" or (
            normalized == "not_useful"
            and int(row["negative_feedback_count"]) >= self.negative_feedback_threshold
        )
        if should_retire:
            reason = (
                "misleading_feedback"
                if normalized == "misleading"
                else f"negative_feedback_threshold:{self.negative_feedback_threshold}"
            )
            retirement_key = f"{self.policy_version}:example:{int(example_id)}:{reason}"
            row = self.repository.retire(
                scope=scope,
                example_id=int(example_id),
                reason=reason,
                idempotency_key=retirement_key,
            )
        return row


__all__ = [
    "FewShotLifecycleError",
    "FewShotReplyCandidateService",
    "FewShotUsageFeedbackService",
]
