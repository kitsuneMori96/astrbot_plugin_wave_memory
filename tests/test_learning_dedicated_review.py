import sqlite3

import pytest

from engine.db.learning_repository import LearningRepositories
from services.learning.candidate_service import LearningCandidateService
from services.learning.dedicated_review import DedicatedReviewBridge
from services.learning.promotion import PromotionOrchestrator
from services.learning.review import LearningReviewService


class DedicatedHandler:
    def __init__(self, target_id, status="pending"):
        self.target_id = target_id
        self.status = status
        self.create_calls = []
        self.status_calls = []

    def create_candidate(self, *, candidate, bot_id, candidate_type):
        self.create_calls.append((candidate["id"], bot_id, candidate_type))
        return {"target_id": self.target_id, "status": self.status}

    def get_review_status(self, *, target_id, bot_id, candidate_type):
        self.status_calls.append((target_id, bot_id, candidate_type))
        return {"target_id": target_id, "status": self.status}


def _context(candidate_type, handler):
    conn = sqlite3.connect(":memory:")
    repos = LearningRepositories.from_connection(conn, now=lambda: 100.0)
    candidates = LearningCandidateService(repos)
    bridge = DedicatedReviewBridge(
        jargon_service=handler if candidate_type == "jargon_candidate" else None,
        belief_service=handler if candidate_type == "belief_candidate" else None,
    )
    review = LearningReviewService(
        repos, now=lambda: 100.0, policy_version="dedicated-v1", dedicated_review_bridge=bridge
    )
    orchestrator = PromotionOrchestrator(
        repos, now=lambda: 100.0, policy_version="dedicated-v1", dedicated_review_bridge=bridge
    )
    candidate_id = candidates.create(
        bot_id="bot-a",
        candidate_type=candidate_type,
        content="专属候选内容",
        evidence={"group_id": "g1", "source": "test"},
        source_fingerprint=f"{candidate_type}:one",
    )
    return conn, repos, bridge, review, orchestrator, candidate_id


def test_dedicated_candidate_is_visible_but_learning_approve_only_delegates_and_keeps_deep_link():
    handler = DedicatedHandler(41)
    conn, repos, bridge, review, orchestrator, candidate_id = _context("jargon_candidate", handler)
    try:
        result = review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        promotion = result["promotions"][0]
        assert result["candidate"]["review_status"] == "delegated"
        assert promotion["promotion_status"] == "waiting_dedicated_review"
        assert promotion["target_id"] == "41"
        assert promotion["metadata"]["dedicated_url"] == "/jargon?id=41"
        assert handler.create_calls == [(candidate_id, "bot-a", "jargon_candidate")]
        # No direct jargon confirmed/active mutation is possible through this path.
        assert promotion["metadata"]["dedicated_status"] == "pending"
        repeated = review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        assert repeated["promotions"][0]["id"] == promotion["id"]
        assert handler.create_calls == [(candidate_id, "bot-a", "jargon_candidate")]
    finally:
        conn.close()


def test_dedicated_status_is_mirrored_on_promotion_and_approval_or_rejection_is_echoed():
    handler = DedicatedHandler(52, status="pending")
    conn, repos, bridge, review, orchestrator, candidate_id = _context("belief_candidate", handler)
    try:
        review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        promotion = repos.promotions.list_for_candidate(candidate_id, bot_id="bot-a")[0]
        assert orchestrator.execute(promotion["id"], bot_id="bot-a")["promotion_status"] == "waiting_dedicated_review"

        handler.status = "approved"
        synced = orchestrator.execute(promotion["id"], bot_id="bot-a")
        assert synced["promotion_status"] == "succeeded"
        assert synced["target_id"] == "52"
        assert synced["metadata"]["dedicated_status"] == "approved"

        # A separate rejected candidate must be represented as a terminal domain rejection.
        rejected_handler = DedicatedHandler(53, status="rejected")
        conn2, repos2, bridge2, review2, orchestrator2, candidate2 = _context("belief_candidate", rejected_handler)
        try:
            review2.approve(candidate2, bot_id="bot-a", reviewer="admin")
            p2 = repos2.promotions.list_for_candidate(candidate2, bot_id="bot-a")[0]
            rejected = orchestrator2.execute(p2["id"], bot_id="bot-a")
            assert rejected["promotion_status"] == "terminal_failed"
            assert rejected["metadata"]["dedicated_status"] == "rejected"
            assert rejected["target_id"] == "53"
        finally:
            conn2.close()
    finally:
        conn.close()


def test_existing_review_candidate_store_can_be_used_without_writing_domain_tables():
    from services.review.candidate_store import ReviewCandidateStore

    conn = sqlite3.connect(":memory:")
    try:
        store = ReviewCandidateStore(conn)
        bridge = DedicatedReviewBridge(jargon_store=store)
        result = bridge.delegate(
            {
                "id": 7,
                "candidate_type": "jargon_candidate",
                "content": "候选黑话",
                "evidence": {"memory_id": 9},
                "reason": "待领域审核",
                "metadata": {},
            },
            bot_id="bot-a",
        )
        assert result.target_id == "1"
        assert result.status == "pending"
        assert result.deep_link == "/jargon?id=1"
        assert store.get(1)["candidate_type"] == "jargon"
    finally:
        conn.close()


def test_dedicated_service_unavailable_is_unknown_and_never_succeeded():
    conn, repos, bridge, review, orchestrator, candidate_id = _context("jargon_candidate", None)
    try:
        review.approve(candidate_id, bot_id="bot-a", reviewer="admin")
        promotion = repos.promotions.list_for_candidate(candidate_id, bot_id="bot-a")[0]
        synced = orchestrator.execute(promotion["id"], bot_id="bot-a")
        assert synced["promotion_status"] == "waiting_dedicated_review"
        assert synced["metadata"]["dedicated_status"] == "unknown"
        assert synced["metadata"]["dedicated_error"] == "service_unavailable"
    finally:
        conn.close()
