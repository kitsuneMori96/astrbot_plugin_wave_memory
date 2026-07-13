"""学习中心服务级端到端链路：来源任务到晋升历史。"""

from __future__ import annotations

import sqlite3

import pytest

from engine.db.learning_repository import LearningRepositories
from services.learning.job_runner import LearningJobRunner
from services.learning.promotion import PromotionOrchestrator, PromotionTargetRegistry
from services.learning.review import LearningReviewService
from services.learning.source import LearningSourceAdapter, LearningSourceItem, LearningSourceRegistry


class _BotSource(LearningSourceAdapter):
    source_type = "e2e_source"

    async def collect(self, *, bot_id, source, job, cursor=None):
        yield LearningSourceItem(
            content=f"{bot_id} learned from the source",
            evidence={"group_id": "e2e-group", "sender_id": "reader", "source": "e2e"},
            source_fingerprint="e2e-fingerprint",
            cursor={"offset": 1},
        )


class _IndexedMemoryTarget:
    def __init__(self):
        self.memory = []
        self.refreshes = []

    def promote(self, *, candidate, bot_id, target_kind):
        target_id = f"memory:{bot_id}:1"
        self.memory.append((target_id, candidate["content"], bot_id, target_kind))
        return {"target_id": target_id, "refresh_required": True}

    def refresh_index_and_cache(self, *, target_id, candidate, bot_id, target_kind):
        self.refreshes.append((target_id, bot_id, target_kind))


@pytest.mark.asyncio
async def test_bot_source_candidate_review_promotion_refresh_and_history_are_connected():
    connection = sqlite3.connect(":memory:")
    repositories = LearningRepositories.from_connection(connection, now=lambda: 100.0)
    try:
        source_id = repositories.sources.create(
            bot_id="bot-a", source_type="e2e_source", name="source-a"
        )
        job_id = repositories.jobs.create(
            bot_id="bot-a",
            source_id=source_id,
            candidate_type="worldview_internalization",
            name="source-job-a",
        )

        sources = LearningSourceRegistry()
        sources.register(_BotSource())
        run = await LearningJobRunner(repositories, sources).run_job(job_id, bot_id="bot-a")
        assert run.status == "succeeded"
        assert run.candidates_created == 1

        candidates, total = repositories.candidates.list(bot_id="bot-a")
        assert total == 1
        candidate = candidates[0]
        assert candidate["review_status"] == "pending"
        assert candidate["bot_id"] == "bot-a"
        assert repositories.candidates.list(bot_id="bot-b")[1] == 0

        review = LearningReviewService(repositories, now=lambda: 100.0, policy_version="e2e-v1")
        reviewed = review.approve(candidate["id"], bot_id="bot-a", reviewer="e2e-reviewer")
        assert reviewed["candidate"]["review_status"] == "approved"
        assert reviewed["promotions"][0]["promotion_status"] == "queued"

        target = _IndexedMemoryTarget()
        registry = PromotionTargetRegistry()
        registry.register("memory", target)
        orchestrator = PromotionOrchestrator(
            repositories, registry, now=lambda: 100.0, policy_version="e2e-v1"
        )
        promoted = orchestrator.promote_candidate(candidate["id"], bot_id="bot-a")
        assert len(promoted) == 1
        assert promoted[0]["promotion_status"] == "succeeded"
        assert promoted[0]["target_id"] == "memory:bot-a:1"
        assert target.memory == [("memory:bot-a:1", "bot-a learned from the source", "bot-a", "memory")]
        assert target.refreshes == [("memory:bot-a:1", "bot-a", "memory")]

        history, history_total = repositories.promotions.list(bot_id="bot-a")
        assert history_total == 1
        assert history[0]["candidate_id"] == candidate["id"]
        assert history[0]["promotion_status"] == "succeeded"
        assert history[0]["target_id"] == "memory:bot-a:1"
        assert repositories.promotions.list(bot_id="bot-b")[1] == 0
    finally:
        connection.close()
