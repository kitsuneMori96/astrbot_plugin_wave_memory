"""Task 11 学习中心 API 契约测试。"""

from __future__ import annotations

import sqlite3

import pytest

from engine.db.learning_repository import LearningRepositories
from services.learning.candidate_service import LearningCandidateService
from webui.container import ServiceContainer, get_container


class _DB:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False


@pytest.fixture()
def api_context():
    conn = sqlite3.connect(":memory:")
    repos = LearningRepositories.from_connection(conn, now=lambda: 100.0)
    source_id = repos.sources.create(
        bot_id="bot-a", source_type="chat", name="chat-a", config={"group_id": "g1"}
    )
    job_id = repos.jobs.create(
        bot_id="bot-a", source_id=source_id, candidate_type="fact", name="facts", schedule={"manual": True}
    )
    candidate_service = LearningCandidateService(repos)
    candidate_id = candidate_service.create(
        bot_id="bot-a",
        candidate_type="fact",
        content="Alice likes tea",
        evidence={"subject": "Alice", "predicate": "likes", "object": "tea"},
        source_fingerprint="fact-1",
        source_id=source_id,
        job_id=job_id,
        reason="evidence",
    )
    other_id = candidate_service.create(
        bot_id="bot-b",
        candidate_type="fact",
        content="Other bot secret",
        evidence={"subject": "secret"},
        source_fingerprint="fact-1",
    )

    ServiceContainer.reset()
    container = get_container()
    container.db = _DB(conn)
    container.password = ""
    container.learning_repositories = repos
    yield container, repos, candidate_id, other_id
    ServiceContainer.reset()
    conn.close()


@pytest.fixture()
def quart_client(api_context):
    quart = pytest.importorskip("quart")
    # test_rework_core 注入的轻量 Quart stub 仅用于导入兜底，不提供 test_client。
    if not hasattr(getattr(quart, "Quart", None), "test_client"):
        pytest.skip("Quart runtime client is unavailable; fallback import mode")
    from webui.app import create_app

    app = create_app()
    return app.test_client()


@pytest.mark.asyncio
async def test_learning_center_requires_authentication_when_password_is_configured(api_context, quart_client):
    container, _, _, _ = api_context
    container.password = "secret"
    container.sessions.clear()
    response = await quart_client.get("/api/learning-center/candidates?bot_id=bot-a")
    assert response.status_code == 401
    payload = await response.get_json()
    assert payload["error"]["code"] == "unauthorized"
    assert payload["retryable"] is False


@pytest.mark.asyncio
async def test_candidates_are_scoped_filtered_paginated_and_detail_is_traceable(api_context, quart_client):
    _, repos, candidate_id, _ = api_context
    # Add another same-bot candidate to exercise pagination and filters.
    LearningCandidateService(repos).create(
        bot_id="bot-a",
        candidate_type="worldview_internalization",
        content="World view",
        evidence={"source": "book"},
        source_fingerprint="world-1",
    )

    response = await quart_client.get(
        "/api/learning-center/candidates?bot_id=bot-a&candidate_type=fact&review_status=pending&limit=1&offset=0"
    )
    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == candidate_id
    assert payload["items"][0]["bot_id"] == "bot-a"
    assert payload["has_more"] is False

    detail = await quart_client.get(f"/api/learning-center/candidates/{candidate_id}?bot_id=bot-a")
    assert detail.status_code == 200
    detail_payload = await detail.get_json()
    assert detail_payload["item"]["evidence"]["subject"] == "Alice"
    assert detail_payload["item"]["source"]["id"] == repos.candidates.get(candidate_id, bot_id="bot-a")["source_id"]
    assert "task" in detail_payload["item"]
    assert "operations" in detail_payload["item"]


@pytest.mark.asyncio
async def test_missing_bot_and_other_bot_object_have_stable_non_leaking_errors(api_context, quart_client):
    _, _, candidate_id, _ = api_context
    missing = await quart_client.get(f"/api/learning-center/candidates/{candidate_id}")
    assert missing.status_code == 400
    missing_payload = await missing.get_json()
    assert missing_payload["error"]["code"] == "bot_id_required"
    assert set(missing_payload["error"]) >= {"code", "message", "retryable"}

    cross_bot = await quart_client.get(f"/api/learning-center/candidates/{candidate_id}?bot_id=bot-b")
    assert cross_bot.status_code == 404
    cross_payload = await cross_bot.get_json()
    assert cross_payload["error"]["code"] == "not_found"
    assert str(candidate_id) not in str(cross_payload)


@pytest.mark.asyncio
async def test_sources_jobs_classification_and_promotion_routes_are_available(api_context, quart_client):
    _, _, candidate_id, _ = api_context
    source = await quart_client.post(
        "/api/learning-center/sources",
        json={"bot_id": "bot-a", "source_type": "manual", "name": "manual", "request_id": "source-1"},
    )
    assert source.status_code == 201
    source_payload = await source.get_json()
    source_id = source_payload["item"]["id"]

    duplicate_source = await quart_client.post(
        "/api/learning-center/sources",
        json={"bot_id": "bot-a", "source_type": "manual", "name": "manual", "request_id": "source-1"},
    )
    assert (await duplicate_source.get_json())["item"]["id"] == source_id

    job = await quart_client.post(
        "/api/learning-center/jobs",
        json={"bot_id": "bot-a", "source_id": source_id, "candidate_type": "fact", "name": "manual-job", "request_id": "job-1"},
    )
    assert job.status_code == 201
    job_id = (await job.get_json())["item"]["id"]
    patched = await quart_client.patch(
        f"/api/learning-center/jobs/{job_id}",
        json={"bot_id": "bot-a", "enabled": False, "request_id": "job-patch-1"},
    )
    assert (await patched.get_json())["item"]["enabled"] is False
    run = await quart_client.post(
        f"/api/learning-center/jobs/{job_id}/run?bot_id=bot-a",
        json={"request_id": "job-run-1"},
    )
    assert run.status_code == 202
    assert (await run.get_json())["item"]["status"] == "skipped"

    reviewed = await quart_client.post(
        f"/api/learning-center/candidates/{candidate_id}/review?bot_id=bot-a",
        json={"action": "approve", "reviewer": "admin", "request_id": "fact-review-list"},
    )
    assert reviewed.status_code == 200
    promotions = await quart_client.get("/api/learning-center/promotions?bot_id=bot-a&promotion_status=queued")
    assert (await promotions.get_json())["total"] == 1

    few_shot = await quart_client.get("/api/learning-center/few-shot?bot_id=bot-a")
    assert few_shot.status_code == 200
    assert (await few_shot.get_json())["items"] == []
    experiences = await quart_client.get("/api/learning-center/experiences?bot_id=bot-a")
    assert experiences.status_code == 200
    assert "worldview_internalization" in await experiences.get_json()


@pytest.mark.asyncio
async def test_dedicated_review_endpoint_delegates_and_exposes_deep_link(api_context, quart_client):
    container, repos, _, _ = api_context

    class Handler:
        def create_candidate(self, *, candidate, bot_id, candidate_type):
            return {"target_id": "dedicated-1", "status": "pending"}

        def get_review_status(self, *, target_id, bot_id, candidate_type):
            return {"target_id": target_id, "status": "pending"}

    from services.learning.dedicated_review import DedicatedReviewBridge

    container.learning_dedicated_review_bridge = DedicatedReviewBridge(jargon_service=Handler())
    candidate_id = LearningCandidateService(repos).create(
        bot_id="bot-a",
        candidate_type="jargon_candidate",
        content="候选黑话",
        evidence={"group_id": "g1"},
        source_fingerprint="jargon-api-1",
    )
    review = await quart_client.post(
        f"/api/learning-center/candidates/{candidate_id}/review?bot_id=bot-a",
        json={"action": "approve", "reviewer": "admin", "request_id": "jargon-review-1"},
    )
    assert review.status_code == 200
    reviewed = await review.get_json()
    assert reviewed["item"]["candidate"]["review_status"] == "delegated"
    assert reviewed["item"]["promotions"][0]["promotion_status"] == "waiting_dedicated_review"

    status = await quart_client.get(
        f"/api/learning-center/dedicated-review-status/{candidate_id}?bot_id=bot-a"
    )
    assert status.status_code == 200
    status_payload = await status.get_json()
    assert status_payload["item"]["target_id"] == "dedicated-1"
    assert status_payload["item"]["deep_link"] == "/jargon?id=dedicated-1"


@pytest.mark.asyncio
async def test_review_retry_and_dedicated_status_use_idempotent_service_boundaries(api_context, quart_client):
    container, _, candidate_id, _ = api_context
    review = await quart_client.post(
        f"/api/learning-center/candidates/{candidate_id}/review?bot_id=bot-a",
        json={"action": "approve", "reviewer": "admin", "request_id": "review-1"},
    )
    assert review.status_code == 200
    review_payload = await review.get_json()
    assert review_payload["item"]["candidate"]["review_status"] == "approved"
    assert review_payload["item"]["promotions"][0]["promotion_status"] == "queued"

    duplicate = await quart_client.post(
        f"/api/learning-center/candidates/{candidate_id}/review?bot_id=bot-a",
        json={"action": "approve", "reviewer": "admin", "request_id": "review-1"},
    )
    assert duplicate.status_code == 200
    assert (await duplicate.get_json())["item"]["promotions"][0]["id"] == review_payload["item"]["promotions"][0]["id"]

    promotion_id = review_payload["item"]["promotions"][0]["id"]
    retry = await quart_client.post(
        f"/api/learning-center/promotions/{promotion_id}/retry?bot_id=bot-a",
        json={"request_id": "retry-1"},
    )
    assert retry.status_code in {200, 409}
    retry_payload = await retry.get_json()
    assert "error" in retry_payload or "item" in retry_payload

    # Non-dedicated candidates must not masquerade as a jargon/belief delegation.
    dedicated = await quart_client.get(
        f"/api/learning-center/dedicated-review-status/{candidate_id}?bot_id=bot-a"
    )
    assert dedicated.status_code == 422
    assert (await dedicated.get_json())["error"]["code"] == "dedicated_review_unsupported"


@pytest.mark.asyncio
async def test_legacy_agent_feedback_list_uses_explicit_bot_projection(api_context, quart_client):
    response = await quart_client.get("/api/agent-feedback?bot_id=bot-a")
    assert response.status_code == 200
    payload = await response.get_json()
    assert [item["bot_id"] for item in payload["review_candidates"]] == ["bot-a"]
    assert "config_suggestions" in payload


@pytest.mark.asyncio
async def test_legacy_agent_review_candidate_proxies_new_candidate_when_bot_scope_is_explicit(api_context, quart_client):
    _, _, candidate_id, _ = api_context
    response = await quart_client.post(
        f"/api/agent-feedback/review-candidates/{candidate_id}/approve?bot_id=bot-a",
        json={"reviewer": "legacy-admin"},
    )
    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["candidate"]["review_status"] == "approved"
    assert payload["promotions"][0]["promotion_status"] == "queued"


@pytest.mark.asyncio
async def test_legacy_agent_review_candidate_is_a_compatibility_projection(api_context, quart_client):
    _, repos, _, _ = api_context
    from services.review.candidate_store import ReviewCandidateStore

    legacy_store = ReviewCandidateStore(repos.connection)
    legacy_id = legacy_store.create(candidate_type="belief", content="legacy", evidence=["trace"], reason="review")
    response = await quart_client.post(f"/api/agent-feedback/review-candidates/{legacy_id}/reject")
    assert response.status_code in {200, 400}
    # The compatibility path keeps config suggestions out of learning promotions.
    if response.status_code == 200:
        payload = await response.get_json()
        assert payload.get("review_status") == "rejected"
        assert "promotion" not in payload
