from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from domain.scope import RuntimeScope, SessionRef
from engine.db.learning_repository import LearningRepositories
from engine.db.scoped_learning_projection_repo import (
    ScopedBotReplyRepository,
    ScopedFewShotRepository,
)
from services.few_shot.service import FewShotService
from services.injection.trace_store import InjectionTraceStore, runtime_scope_metadata
from services.learning.candidate_service import LearningCandidateService
from services.learning.domain_promotions import FewShotStylePromotionService
from services.learning.fewshot_lifecycle import (
    FewShotLifecycleError,
    FewShotReplyCandidateService,
    FewShotUsageFeedbackService,
)
from services.learning.promotion import PromotionOrchestrator, PromotionTargetRegistry
from services.learning.review import LearningReviewService


class JsonQualityProvider:
    async def text_chat(self, **kwargs):
        return SimpleNamespace(completion_text='{"score": 0.93, "traits": ["克制", "简洁"]}')


def scope(group_id: str = "group-1", *, bot_id: str = "bot-a") -> RuntimeScope:
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(
            id=f"test:group:{group_id}",
            platform_id="test",
            kind="group",
            conversation_id=group_id,
        ),
        subject_principal_id="test:user:user-1",
    )


def prepare_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            group_id TEXT NOT NULL,
            sender_id TEXT,
            sender_name TEXT,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            source TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            bot_id TEXT,
            session_id TEXT,
            visibility TEXT,
            origin_fingerprint TEXT,
            provenance TEXT,
            resolution_state TEXT,
            quarantine INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE scoped_tags (
            id INTEGER PRIMARY KEY,
            bot_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            visibility TEXT NOT NULL,
            name TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE scoped_memory_tags (
            bot_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            visibility TEXT NOT NULL,
            memory_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            relevance REAL NOT NULL
        );
        """
    )
    current = scope()
    connection.execute(
        """INSERT INTO memories(
               id, group_id, sender_id, sender_name, content, timestamp, source,
               memory_type, bot_id, session_id, visibility, origin_fingerprint,
               provenance, resolution_state, quarantine)
           VALUES (41, ?, 'bot', '测试Bot', ?, 110, 'core', 'message', ?, ?, ?,
                   'origin:reply-41', '{"schema":"memory-origin/v1"}', 'resolved', 0)""",
        (
            current.session.conversation_id,
            "先核实事实，再用简短而克制的方式回应，不夸大未知信息。",
            current.bot_id,
            current.session.id,
            current.visibility,
        ),
    )
    connection.execute(
        "INSERT INTO scoped_tags VALUES (7, ?, ?, ?, '克制表达', 'style', 'active')",
        (current.bot_id, current.session.id, current.visibility),
    )
    connection.execute(
        "INSERT INTO scoped_memory_tags VALUES (?, ?, ?, 41, 7, 1, 0.95)",
        (current.bot_id, current.session.id, current.visibility),
    )
    connection.commit()
    return connection


def record_trace(
    store: InjectionTraceStore,
    *,
    trace_id: str,
    timestamp: float,
    current_scope: RuntimeScope,
    example_ids: tuple[int, ...] = (),
) -> None:
    channels = []
    if example_ids:
        channels.append({
            "channel": "fewshot",
            "status": "hit",
            "text": "<style_examples />",
            "items": [{"example_id": value, "preview": "克制回复"} for value in example_ids],
            "filtered": [],
        })
    store.record(
        {
            "trace_id": trace_id,
            "timestamp": timestamp,
            "mode": "full",
            "group_id": current_scope.session.conversation_id,
            "bot_profile_id": current_scope.bot_id,
            "message": "请解释这个问题",
            "final_text": "query context",
            "status": "ok",
            "metadata": runtime_scope_metadata(current_scope),
        },
        channels,
    )


def build_slice(connection: sqlite3.Connection):
    repositories = LearningRepositories.from_connection(connection, now=lambda: 120.0)
    projection = ScopedFewShotRepository(connection, now=lambda: 120.0)
    trace_store = InjectionTraceStore(connection)
    trace_store.ensure_schema()
    record_trace(
        trace_store,
        trace_id="query-41",
        timestamp=100.0,
        current_scope=scope(),
    )
    assessor = FewShotService(
        db=SimpleNamespace(conn=connection),
        llm_client=JsonQualityProvider(),
        repository=projection,
        enabled=True,
    )
    candidates = LearningCandidateService(repositories)
    candidate_lifecycle = FewShotReplyCandidateService(
        candidates,
        ScopedBotReplyRepository(connection),
        trace_store,
        assessor,
        min_score=0.8,
        policy_version="batch8/v1",
    )
    return repositories, projection, trace_store, candidate_lifecycle


def test_real_reply_candidate_review_projection_usage_feedback_and_retirement_are_idempotent():
    connection = prepare_connection()
    repositories, projection, trace_store, candidate_lifecycle = build_slice(connection)
    try:
        candidate_id = asyncio.run(candidate_lifecycle.create_from_reply(
            scope=scope(), memory_id=41, query_trace_id="query-41"
        ))
        repeated_candidate_id = asyncio.run(candidate_lifecycle.create_from_reply(
            scope=scope(), memory_id=41, query_trace_id="query-41"
        ))
        assert repeated_candidate_id == candidate_id
        candidate = repositories.candidates.get(candidate_id, bot_id="bot-a")
        assert candidate["content"].startswith("先核实事实")
        assert candidate["evidence"]["source_reply"]["memory_id"] == 41
        assert candidate["evidence"]["source_tags"][0]["name"] == "克制表达"
        assert candidate["evidence"]["query_trace_id"] == "query-41"
        assert {item["kind"] for item in candidate["evidence"]["evidence_refs"]} == {
            "bot_reply_memory", "query_trace"
        }

        review = LearningReviewService(
            repositories, now=lambda: 130.0, policy_version="batch8/v1"
        )
        first_review = review.approve(candidate_id, bot_id="bot-a", reviewer="reviewer")
        second_review = review.approve(candidate_id, bot_id="bot-a", reviewer="reviewer")
        assert first_review["candidate"]["review_status"] == "approved"
        assert first_review["promotions"][0]["id"] == second_review["promotions"][0]["id"]

        registry = PromotionTargetRegistry()
        registry.register("few_shot", FewShotStylePromotionService(projection))
        orchestrator = PromotionOrchestrator(
            repositories, registry, now=lambda: 140.0, policy_version="batch8/v1"
        )
        promoted = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
        repeated_promotion = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
        assert promoted["promotion_status"] == "succeeded"
        assert repeated_promotion["target_id"] == promoted["target_id"]
        example_id = int(promoted["target_id"])
        row = projection.get(example_id)
        assert row["status"] == "approved"
        assert row["source_tags"][0]["tag_id"] == 7
        assert row["query_trace_id"] == "query-41"

        usage_feedback = FewShotUsageFeedbackService(
            projection,
            trace_store,
            negative_feedback_threshold=2,
            policy_version="batch8-retirement/v1",
        )
        record_trace(
            trace_store,
            trace_id="use-1",
            timestamp=200.0,
            current_scope=scope(),
            example_ids=(example_id,),
        )
        usage_feedback.record_usage(scope=scope(), query_trace_id="use-1")
        usage_feedback.record_usage(scope=scope(), query_trace_id="use-1")
        assert projection.get(example_id)["usage_count"] == 1

        record_trace(
            trace_store,
            trace_id="use-2",
            timestamp=210.0,
            current_scope=scope(),
            example_ids=(example_id,),
        )
        first_negative = usage_feedback.record_feedback(
            scope=scope(),
            query_trace_id="use-2",
            example_id=example_id,
            feedback="not_useful",
            request_id="feedback-1",
        )
        assert first_negative["status"] == "approved"
        assert first_negative["negative_feedback_count"] == 1

        record_trace(
            trace_store,
            trace_id="use-3",
            timestamp=220.0,
            current_scope=scope(),
            example_ids=(example_id,),
        )
        retired = usage_feedback.record_feedback(
            scope=scope(),
            query_trace_id="use-3",
            example_id=example_id,
            feedback="not_useful",
            request_id="feedback-2",
        )
        repeated_retirement = usage_feedback.record_feedback(
            scope=scope(),
            query_trace_id="use-3",
            example_id=example_id,
            feedback="not_useful",
            request_id="feedback-2",
        )
        assert retired["status"] == "revoked"
        assert repeated_retirement["revision"] == retired["revision"]
        assert retired["usage_count"] == 3
        assert retired["negative_feedback_count"] == 2
        assert projection.list_approved(scope=scope()) == []
        assert connection.execute("SELECT COUNT(*) FROM scoped_few_shot_usage_events").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM scoped_few_shot_feedback_events").fetchone()[0] == 2
    finally:
        connection.close()


def test_candidate_creation_fails_closed_for_non_bot_reply_missing_tags_trace_or_quality_dependency():
    connection = prepare_connection()
    repositories, projection, trace_store, candidate_lifecycle = build_slice(connection)
    try:
        connection.execute("UPDATE memories SET sender_id='user-1' WHERE id=41")
        connection.commit()
        with pytest.raises(ValueError, match="real Bot reply"):
            asyncio.run(candidate_lifecycle.create_from_reply(
                scope=scope(), memory_id=41, query_trace_id="query-41"
            ))

        connection.execute("UPDATE memories SET sender_id='bot' WHERE id=41")
        connection.execute("DELETE FROM scoped_memory_tags WHERE memory_id=41")
        connection.commit()
        with pytest.raises(ValueError, match="no active scoped Tags"):
            asyncio.run(candidate_lifecycle.create_from_reply(
                scope=scope(), memory_id=41, query_trace_id="query-41"
            ))

        connection.execute(
            "INSERT INTO scoped_memory_tags VALUES (?, ?, ?, 41, 7, 1, 0.95)",
            (scope().bot_id, scope().session.id, scope().visibility),
        )
        connection.commit()
        with pytest.raises(FewShotLifecycleError) as missing_trace:
            asyncio.run(candidate_lifecycle.create_from_reply(
                scope=scope(), memory_id=41, query_trace_id="missing"
            ))
        assert missing_trace.value.code == "fewshot_query_trace_unavailable"

        unavailable_quality = FewShotReplyCandidateService(
            LearningCandidateService(repositories),
            ScopedBotReplyRepository(connection),
            trace_store,
            object(),
        )
        with pytest.raises(FewShotLifecycleError) as missing_quality:
            asyncio.run(unavailable_quality.create_from_reply(
                scope=scope(), memory_id=41, query_trace_id="query-41"
            ))
        assert missing_quality.value.code == "fewshot_quality_assessor_unavailable"
        assert repositories.candidates.list(bot_id="bot-a")[1] == 0
        assert projection.count_approved(scope=scope()) == 0
    finally:
        connection.close()


def test_feedback_requires_same_scope_trace_and_actual_fewshot_hit():
    connection = prepare_connection()
    repositories, projection, trace_store, candidate_lifecycle = build_slice(connection)
    try:
        candidate_id = asyncio.run(candidate_lifecycle.create_from_reply(
            scope=scope(), memory_id=41, query_trace_id="query-41"
        ))
        review = LearningReviewService(repositories, policy_version="batch8/v1")
        review.approve(candidate_id, bot_id="bot-a", reviewer="reviewer")
        registry = PromotionTargetRegistry()
        registry.register("few_shot", FewShotStylePromotionService(projection))
        result = PromotionOrchestrator(
            repositories, registry, policy_version="batch8/v1"
        ).promote_candidate(candidate_id, bot_id="bot-a")[0]
        example_id = int(result["target_id"])
        usage_feedback = FewShotUsageFeedbackService(projection, trace_store)

        record_trace(
            trace_store,
            trace_id="other-example",
            timestamp=200.0,
            current_scope=scope(),
            example_ids=(example_id + 1,),
        )
        with pytest.raises(FewShotLifecycleError) as not_used:
            usage_feedback.record_feedback(
                scope=scope(),
                query_trace_id="other-example",
                example_id=example_id,
                feedback="useful",
                request_id="feedback-other",
            )
        assert not_used.value.code == "fewshot_example_not_used_by_trace"

        other_scope = scope("group-2")
        record_trace(
            trace_store,
            trace_id="other-scope",
            timestamp=201.0,
            current_scope=other_scope,
            example_ids=(example_id,),
        )
        with pytest.raises(FewShotLifecycleError) as mismatch:
            usage_feedback.record_usage(scope=scope(), query_trace_id="other-scope")
        assert mismatch.value.code == "fewshot_query_trace_unavailable"
        assert projection.get(example_id)["usage_count"] == 0
    finally:
        connection.close()


def test_slice_keeps_fact_conflict_belief_and_jargon_as_explicit_gaps():
    """批次 8 第一条不伪装成 Facts 冲突分类或 Belief/Jargon 第二主线。"""
    from engine.fact_classifier import classify_fact

    connection = prepare_connection()
    repositories, _, _, candidate_lifecycle = build_slice(connection)
    try:
        candidate_id = asyncio.run(candidate_lifecycle.create_from_reply(
            scope=scope(), memory_id=41, query_trace_id="query-41"
        ))
        candidate = repositories.candidates.get(candidate_id, bot_id="bot-a")
        assert candidate["candidate_type"] == "few_shot_style"
        assert classify_fact("用户", "喜欢", "茶") == "PREFERENCE"
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert not {"scoped_facts", "scoped_beliefs", "scoped_jargon"} & tables
    finally:
        connection.close()
