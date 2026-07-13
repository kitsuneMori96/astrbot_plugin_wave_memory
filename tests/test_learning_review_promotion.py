import sqlite3
import threading
import unittest

from engine.db.learning_repository import LearningRepositories
from services.learning.candidate_service import LearningCandidateService
from services.learning.promotion import (
    PromotionOrchestrator,
    PromotionRetryableError,
    PromotionTargetRegistry,
)
from services.learning.review import LearningReviewService


class _Target:
    def __init__(self, *, fail_create=False, fail_refresh=False):
        self.fail_create = fail_create
        self.fail_refresh = fail_refresh
        self.create_calls = 0
        self.refresh_calls = 0
        self.created_ids = []

    def promote(self, *, candidate, bot_id, target_kind):
        self.create_calls += 1
        if self.fail_create:
            raise PromotionRetryableError("target unavailable")
        target_id = f"{target_kind}-{self.create_calls}"
        self.created_ids.append(target_id)
        return {"target_id": target_id, "refresh_required": True}

    def refresh(self, *, target_id, candidate, bot_id, target_kind):
        self.refresh_calls += 1
        if self.fail_refresh:
            self.fail_refresh = False
            raise PromotionRetryableError("refresh unavailable")


class LearningReviewPromotionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.repos = LearningRepositories.from_connection(self.connection, now=lambda: 100.0)
        self.candidate_service = LearningCandidateService(self.repos)
        self.review = LearningReviewService(self.repos, now=lambda: 100.0, policy_version="policy-1")
        self.registry = PromotionTargetRegistry()
        self.orchestrator = PromotionOrchestrator(
            self.repos, self.registry, now=lambda: 100.0, policy_version="policy-1"
        )

    def tearDown(self):
        self.connection.close()

    def _candidate(self, candidate_type="fact", content="a fact"):
        scope = {
            "bot_id": "bot-a",
            "visibility": "group",
            "session": {
                "id": "test:group:g",
                "platform_id": "test",
                "kind": "group",
                "conversation_id": "g",
            },
            "subject_principal_id": "test:user:alice",
        }
        evidence_ref = {
            "kind": "raw_message",
            "id": f"message:{candidate_type}:{content}",
            "content_hash": f"hash:{candidate_type}:{content}",
            "captured_at": 100.0,
            "source_scope": scope,
            "available": True,
        }
        evidence_binding = {
            "evidence_id": evidence_ref["id"],
            "target_scope": scope,
            "derivation_chain": ("raw_chat", "reviewed_candidate"),
            "policy_version": "test-v1",
        }
        evidence = {
            "subject": "alice",
            "predicate": "likes",
            "object": "tea",
            "group_id": "g",
            "user_id": "alice",
            "scope": scope,
            "target_scope": scope,
            "evidence_ref": evidence_ref,
            "evidence_refs": (evidence_ref,),
            "evidence_binding": evidence_binding,
            "evidence_bindings": (evidence_binding,),
        }
        if candidate_type == "correction_learning":
            evidence.update({
                "bot_reply": "old",
                "user_correction": "new",
                "message_ref": {"group_id": "g", "message_id": "m"},
                "book_lore_hits": [],
                "generated_text": content,
            })
        return self.candidate_service.create(
            bot_id="bot-a",
            candidate_type=candidate_type,
            content=content,
            evidence=evidence,
            source_fingerprint=f"{candidate_type}-{content}",
        )

    def test_review_and_promotion_are_separate_and_reject_never_enqueues(self):
        candidate_id = self._candidate()
        result = self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        self.assertEqual(result["candidate"]["review_status"], "approved")
        self.assertEqual(result["promotions"][0]["promotion_status"], "queued")
        self.assertEqual(self.repos.promotions.list(bot_id="bot-a")[1], 1)

        rejected_id = self._candidate(content="reject me")
        rejected = self.review.reject(rejected_id, bot_id="bot-a", reviewer="admin", note="bad")
        self.assertEqual(rejected["candidate"]["review_status"], "rejected")
        self.assertEqual(self.repos.promotions.list(bot_id="bot-a")[1], 1)

    def test_double_approve_is_idempotent_and_policy_version_is_in_key(self):
        candidate_id = self._candidate()
        first = self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        second = self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        self.assertEqual(first["promotions"][0]["id"], second["promotions"][0]["id"])
        key = first["promotions"][0]["idempotency_key"]
        self.assertIn(str(candidate_id), key)
        self.assertIn("fact", key)
        self.assertIn("policy-1", key)

    def test_target_failure_is_retryable_and_success_refresh_failure_does_not_duplicate_target(self):
        candidate_id = self._candidate()
        target = _Target(fail_refresh=True)
        self.registry.register("fact", target)
        self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        promotion = self.orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
        self.assertEqual(promotion["promotion_status"], "retryable_failed")
        self.assertEqual(promotion["target_id"], "fact-1")
        self.assertEqual(target.create_calls, 1)
        retried = self.orchestrator.retry(promotion["id"], bot_id="bot-a")
        self.assertEqual(retried["promotion_status"], "succeeded")
        self.assertEqual(target.create_calls, 1)
        self.assertEqual(target.refresh_calls, 2)

    def test_correction_learning_has_independent_memory_and_fact_promotions(self):
        candidate_id = self._candidate("correction_learning", "corrected")
        self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        promotions = self.repos.promotions.list(bot_id="bot-a")[0]
        self.assertEqual({p["target_kind"] for p in promotions}, {"memory", "fact"})

        memory = _Target()
        facts = _Target(fail_create=True)
        self.registry.register("memory", memory)
        self.registry.register("fact", facts)
        results = self.orchestrator.promote_candidate(candidate_id, bot_id="bot-a")
        statuses = {item["target_kind"]: item["promotion_status"] for item in results}
        self.assertEqual(statuses, {"memory": "succeeded", "fact": "retryable_failed"})
        failed = next(item for item in results if item["target_kind"] == "fact")
        facts.fail_create = False
        retried = self.orchestrator.retry(failed["id"], bot_id="bot-a")
        self.assertEqual(retried["promotion_status"], "succeeded")
        self.assertEqual(memory.create_calls, 1)

    def test_domain_service_adapter_calls_public_memory_and_fact_methods(self):
        class Domain:
            def __init__(self):
                self.memory_calls = []
                self.fact_calls = []

            def add_memory(self, **kwargs):
                self.memory_calls.append(kwargs)
                return 12

            def insert_fact(self, **kwargs):
                self.fact_calls.append(kwargs)
                return 13

        domain = Domain()
        candidate_id = self._candidate()
        self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        orchestrator = PromotionOrchestrator(
            self.repos, domain_services={"fact": domain}, now=lambda: 100.0,
            policy_version="policy-1",
        )
        result = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
        self.assertEqual(result["promotion_status"], "succeeded")
        self.assertEqual(domain.fact_calls[0]["subject"], "alice")
        self.assertEqual(domain.fact_calls[0]["predicate"], "likes")

    def test_running_promotion_recovers_after_process_interruption(self):
        candidate_id = self._candidate()
        self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        promotion = self.repos.promotions.list(bot_id="bot-a")[0][0]
        self.repos.promotions.update_status(
            promotion["id"], bot_id="bot-a", promotion_status="running", started_at=1.0
        )
        recovered = self.orchestrator.recover_interrupted(bot_id="bot-a", timeout=10)
        self.assertEqual(recovered, 1)
        self.assertEqual(
            self.repos.promotions.get(promotion["id"], bot_id="bot-a")["promotion_status"],
            "retryable_failed",
        )

    def test_concurrent_execution_claims_once(self):
        candidate_id = self._candidate()
        target = _Target()
        self.registry.register("fact", target)
        self.review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        outcomes = []

        def run():
            outcomes.append(self.orchestrator.promote_candidate(candidate_id, bot_id="bot-a"))

        first = threading.Thread(target=run)
        second = threading.Thread(target=run)
        first.start()
        second.start()
        first.join()
        second.join()
        self.assertEqual(target.create_calls, 1)
        self.assertTrue(any(item[0]["promotion_status"] == "succeeded" for item in outcomes))


if __name__ == "__main__":
    unittest.main()
