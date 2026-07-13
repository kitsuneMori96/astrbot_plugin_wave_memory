import json
import sqlite3
from types import SimpleNamespace

import pytest

from engine.db.learning_repository import LearningRepositories
from services.learning.candidate_service import LearningCandidateService
from services.learning.promotion import PromotionOrchestrator
from services.learning.review import LearningReviewService
from services.learning.domain_promotions import (
    BookExperiencePromotionService,
    BookLorePromotionService,
    FactPromotionService,
    FewShotStylePromotionService,
    RelationshipPromotionService,
    WorldviewInternalizationPromotionService,
)


def _evidence(candidate_type, *, group_id="group-1", user_id="user-1"):
    scope = {
        "bot_id": "bot-a",
        "visibility": "group",
        "session": {
            "id": f"test:group:{group_id}",
            "platform_id": "test",
            "kind": "group",
            "conversation_id": group_id,
        },
        "subject_principal_id": f"test:user:{user_id}",
    }
    evidence_ref = {
        "kind": "raw_message",
        "id": f"message:{candidate_type}",
        "content_hash": f"hash:{candidate_type}",
        "captured_at": 100.0,
        "source_scope": scope,
        "available": True,
    }
    evidence_binding = {
        "evidence_id": evidence_ref["id"],
        "target_scope": scope,
        "derivation_chain": ("raw_chat", "reviewed_candidate"),
        "policy_version": "contract-v1",
    }
    common = {
        "group_id": group_id,
        "user_id": user_id,
        "scope": scope,
        "target_scope": scope,
        "evidence_ref": evidence_ref,
        "evidence_refs": (evidence_ref,),
        "evidence_binding": evidence_binding,
        "evidence_bindings": (evidence_binding,),
    }
    if candidate_type == "worldview_internalization":
        return {**common, "source": "learning", "sender_id": user_id, "sender_name": "reader"}
    if candidate_type == "book_experience_episode":
        return {
            **common,
            "corpus_id": "corpus-a",
            "book_version": "v1",
            "chapter_ref": "chapter-1",
            "original_quote": "原文证据",
            "participants": [{"role": "harbor-warden"}],
            "target_bot_role": "harbor-warden",
            "informed_perspective": "witness",
            "source_item_id": "item-1",
            "extraction_method": "quote",
        }
    if candidate_type == "few_shot_style":
        return {**common, "score": 0.92, "traits": ["克制"]}
    if candidate_type == "fact":
        return {**common, "subject": "user-1", "predicate": "likes", "object": "tea"}
    if candidate_type == "relationship":
        return {
            **common,
            "event_type": "bot_praised",
            "dimension": "trust",
            "delta": 2,
            "reason": "approved learning evidence",
        }
    if candidate_type == "book_lore":
        return {
            **common,
            "community_id": 7,
            "title": "旧港",
            "summary_snapshot": "潮汐改变商路",
            "rank": 8.5,
            "source_library_id": "lore-a",
        }
    raise AssertionError(candidate_type)


class _MemoryDomain:
    def __init__(self):
        self.calls = []
        self.refresh_calls = []

    def add_memory(self, **kwargs):
        self.calls.append(kwargs)
        return 101

    def refresh_index_and_cache(self, **kwargs):
        self.refresh_calls.append(kwargs)


class _FewShotDomain:
    def __init__(self):
        self.calls = []
        self.refresh_calls = []

    def add_approved_example(self, **kwargs):
        self.calls.append(kwargs)
        return 202

    def refresh(self, **kwargs):
        self.refresh_calls.append(kwargs)


class _BookLoreDomain:
    def __init__(self):
        self.calls = []
        self.refresh_calls = []

    def upsert_lore(self, **kwargs):
        self.calls.append(kwargs)
        return 303

    def refresh_index_and_cache(self, **kwargs):
        self.refresh_calls.append(kwargs)


@pytest.fixture
def promotion_context():
    conn = sqlite3.connect(":memory:")
    repos = LearningRepositories.from_connection(conn, now=lambda: 100.0)
    candidates = LearningCandidateService(repos)
    review = LearningReviewService(repos, now=lambda: 100.0, policy_version="contract-v1")
    yield conn, repos, candidates, review
    conn.close()


def _approved_candidate(repos, candidates, review, candidate_type):
    candidate_id = candidates.create(
        bot_id="bot-a",
        candidate_type=candidate_type,
        content=f"approved {candidate_type}",
        evidence=_evidence(candidate_type),
        source_fingerprint=f"fingerprint:{candidate_type}",
    )
    review.approve(candidate_id, bot_id="bot-a", reviewer="tester")
    return candidate_id


def test_book_experience_schema_migration_is_idempotent_and_distinct_from_interactive_table(tmp_path):
    from engine.db.migrations.book_experience import run_migration

    path = tmp_path / "book-experience.db"
    assert run_migration(str(path)) is True
    assert run_migration(str(path)) is True
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(book_experience_episodes)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(book_experience_episodes)")}
    assert "book_experience_episodes" in tables
    assert "experience_episodes" not in tables
    assert {"bot_id", "group_id", "user_id", "evidence_json", "idempotency_key"} <= columns
    assert "uq_book_experience_episode_identity" in indexes
    conn.close()


def test_worldview_uses_generic_learning_source_and_records_memory_id_and_refresh(promotion_context):
    _, repos, candidates, review = promotion_context
    domain = _MemoryDomain()
    target = WorldviewInternalizationPromotionService(domain)
    orchestrator = PromotionOrchestrator(
        repos, target_services={"memory": target}, policy_version="contract-v1", now=lambda: 100.0
    )
    candidate_id = _approved_candidate(repos, candidates, review, "worldview_internalization")

    result = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]

    assert result["promotion_status"] == "succeeded"
    assert result["target_id"] == "101"
    assert domain.calls[0]["source"] == "learning"
    assert domain.calls[0]["group_id"] == "group-1"
    assert domain.calls[0]["sender_id"] == "user-1"
    assert domain.refresh_calls[0]["target_id"] == "101"
    assert orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]["target_id"] == "101"
    assert len(domain.calls) == 1


def test_book_experience_uses_separate_table_and_never_interactive_episode(promotion_context):
    conn, repos, candidates, review = promotion_context
    domain = BookExperiencePromotionService(conn)
    orchestrator = PromotionOrchestrator(
        repos,
        target_services={"book_experience_episode": domain},
        policy_version="contract-v1",
        now=lambda: 100.0,
    )
    candidate_id = _approved_candidate(repos, candidates, review, "book_experience_episode")

    result = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
    row = conn.execute(
        "SELECT bot_id, group_id, user_id, content FROM book_experience_episodes WHERE id=?",
        (int(result["target_id"]),),
    ).fetchone()

    assert result["promotion_status"] == "succeeded"
    assert row[:3] == ("bot-a", "group-1", "user-1")
    assert row[3] == "approved book_experience_episode"
    interactive_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='experience_episodes'"
    ).fetchone()
    assert interactive_table is None or conn.execute("SELECT COUNT(*) FROM experience_episodes").fetchone()[0] == 0
    assert len(domain.refresh_events) == 1
    repeated = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
    assert repeated["target_id"] == result["target_id"]
    assert len(domain.refresh_events) == 1
    assert len(conn.execute("SELECT id FROM book_experience_episodes").fetchall()) == 1


def test_fewshot_uses_approved_domain_path_and_reuses_cache_refresh(promotion_context):
    _, repos, candidates, review = promotion_context
    domain = _FewShotDomain()
    target = FewShotStylePromotionService(domain)
    orchestrator = PromotionOrchestrator(
        repos, target_services={"few_shot": target}, policy_version="contract-v1", now=lambda: 100.0
    )
    candidate_id = _approved_candidate(repos, candidates, review, "few_shot_style")

    result = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]

    assert result["target_id"] == "202"
    assert domain.calls[0]["bot_id"] == "bot-a"
    assert domain.calls[0]["status"] == "approved"
    assert domain.refresh_calls[0]["target_id"] == "202"
    repeated = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
    assert repeated["target_id"] == "202"
    assert len(domain.calls) == 1
    assert len(domain.refresh_calls) == 1


@pytest.mark.parametrize(
    "candidate_type,target_kind,service_factory,target_id",
    [
        ("fact", "fact", lambda: FactPromotionService(SimpleNamespace(insert_fact=lambda **kwargs: 404)), "404"),
        ("relationship", "relationship", lambda: RelationshipPromotionService(SimpleNamespace(record_event=lambda **kwargs: SimpleNamespace(event_id=505))), "505"),
        ("book_lore", "book_lore", lambda: BookLorePromotionService(_BookLoreDomain()), "303"),
    ],
)
def test_remaining_domain_promotions_preserve_scope_and_target_id(
    promotion_context, candidate_type, target_kind, service_factory, target_id
):
    _, repos, candidates, review = promotion_context
    target = service_factory()
    orchestrator = PromotionOrchestrator(
        repos, target_services={target_kind: target}, policy_version="contract-v1", now=lambda: 100.0
    )
    candidate_id = _approved_candidate(repos, candidates, review, candidate_type)

    result = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]

    assert result["promotion_status"] == "succeeded"
    assert result["target_id"] == target_id
    calls = getattr(target, "calls", None) or getattr(target.service, "calls", None)
    assert calls[0]["group_id"] == "group-1"
    assert calls[0]["user_id"] == "user-1"
    repeated = orchestrator.promote_candidate(candidate_id, bot_id="bot-a")[0]
    assert repeated["target_id"] == target_id
    assert len(calls) == 1
    if hasattr(target, "refresh_calls"):
        assert len(target.refresh_calls) == 1


def test_review_maps_book_experience_to_independent_target_kind(promotion_context):
    _, repos, candidates, review = promotion_context
    candidate_id = _approved_candidate(repos, candidates, review, "book_experience_episode")
    promotion = repos.promotions.list_for_candidate(candidate_id, bot_id="bot-a")
    assert promotion[0]["target_kind"] == "book_experience_episode"
