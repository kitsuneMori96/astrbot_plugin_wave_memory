import sqlite3

import pytest

from engine.db.learning_repository import LearningRepositories
from services.learning.book_experience import (
    BOOK_EXPERIENCE_CANDIDATE,
    BOOK_LORE_CANDIDATE,
    BookExperienceEvidenceValidator,
    BookExperienceSourceAdapter,
    register_book_experience_task,
)
from services.learning.candidate_service import LearningCandidateService
from services.learning.config import resolve_learning_config
from services.learning.job_runner import LearningJobRunner
from services.learning.source import LearningSourceRegistry


def complete_evidence():
    return {
        "corpus_id": "novel-corpus",
        "book_version": "v2.1",
        "chapter_ref": "chapter-03#p12",
        "original_quote": "守望者在旧港看见潮汐改变了商路。",
        "participants": [{"name": "阿岚", "role": "harbor-warden"}],
        "target_bot_role": "harbor-warden",
        "informed_perspective": "witness",
        "source_item_id": "notes:chapter-03:12",
        "extraction_method": "book_note_with_quote",
    }


def test_complete_book_experience_evidence_is_accepted():
    result = BookExperienceEvidenceValidator(expected_bot_role="harbor-warden").validate(
        complete_evidence()
    )

    assert result.valid is True
    assert result.missing == ()
    assert result.normalized["chapter_ref"] == "chapter-03#p12"


@pytest.mark.parametrize(
    "field",
    ["chapter_ref", "original_quote", "participants", "target_bot_role", "informed_perspective"],
)
def test_missing_book_experience_evidence_is_rejected(field):
    evidence = complete_evidence()
    evidence[field] = [] if field == "participants" else ""

    result = BookExperienceEvidenceValidator(expected_bot_role="harbor-warden").validate(evidence)

    assert result.valid is False
    assert field in result.missing


def test_book_version_or_corpus_identifies_the_book_source():
    evidence = complete_evidence()
    evidence["book_version"] = ""

    result = BookExperienceEvidenceValidator(expected_bot_role="harbor-warden").validate(evidence)

    assert result.valid is True


def test_participants_must_include_configured_target_role():
    evidence = complete_evidence()
    evidence["participants"] = [{"name": "阿岚", "role": "other-role"}]

    result = BookExperienceEvidenceValidator(expected_bot_role="harbor-warden").validate(evidence)

    assert result.valid is False
    assert "participants_target_role" in result.errors


def test_unknown_informed_perspective_is_rejected():
    evidence = complete_evidence()
    evidence["informed_perspective"] = "omniscient"

    result = BookExperienceEvidenceValidator(expected_bot_role="harbor-warden").validate(evidence)

    assert result.valid is False
    assert "informed_perspective" in result.errors


def test_invalid_evidence_downgrades_to_book_lore_without_episode_write():
    adapter = BookExperienceSourceAdapter(
        items=[{"content": "旧港的潮汐改变商路。", "evidence": {"chapter_ref": ""}}],
        target_bot_role="harbor-warden",
    )
    items = list(adapter.collect(bot_id="baizz", source={}, job={}, cursor=None))

    assert len(items) == 1
    assert items[0].metadata["candidate_type"] == BOOK_LORE_CANDIDATE
    assert items[0].metadata["downgraded_from"] == BOOK_EXPERIENCE_CANDIDATE
    assert items[0].evidence["evidence_status"] == "insufficient"


def test_runner_creates_complete_book_experience_candidate_without_episode_write():
    connection = sqlite3.connect(":memory:")
    repositories = LearningRepositories.from_connection(connection)
    source_id = repositories.sources.create(
        bot_id="baizz", source_type="book_experience", name="book episodes"
    )
    job_id = repositories.jobs.create(
        bot_id="baizz", source_id=source_id,
        candidate_type=BOOK_EXPERIENCE_CANDIDATE, name="book episodes",
        policy={"target_bot_role": "harbor-warden"},
    )
    registry = LearningSourceRegistry()
    registry.register(BookExperienceSourceAdapter(
        items=[{"content": "我在旧港目击了潮汐改变商路。", "evidence": complete_evidence()}],
        target_bot_role="harbor-warden",
    ))

    import asyncio

    result = asyncio.run(LearningJobRunner(repositories, registry).run_job(job_id, bot_id="baizz"))
    candidates, count = repositories.candidates.list(bot_id="baizz")

    assert result.status == "succeeded"
    assert count == 1
    assert candidates[0]["candidate_type"] == BOOK_EXPERIENCE_CANDIDATE
    assert candidates[0]["evidence"]["evidence_status"] == "complete"
    connection.close()


def test_runner_creates_only_learning_candidate_and_downgrades_insufficient_evidence():
    connection = sqlite3.connect(":memory:")
    repositories = LearningRepositories.from_connection(connection)
    source_id = repositories.sources.create(
        bot_id="baizz", source_type="book_experience", name="book episodes"
    )
    job_id = repositories.jobs.create(
        bot_id="baizz",
        source_id=source_id,
        candidate_type=BOOK_EXPERIENCE_CANDIDATE,
        name="book episodes",
    )
    registry = LearningSourceRegistry()
    registry.register(
        BookExperienceSourceAdapter(
            items=[{"content": "没有章节证据的经历。", "evidence": {"participants": []}}],
            target_bot_role="harbor-warden",
        )
    )

    import asyncio

    result = asyncio.run(LearningJobRunner(repositories, registry).run_job(job_id, bot_id="baizz"))
    candidates, count = repositories.candidates.list(bot_id="baizz")

    assert result.status == "succeeded"
    assert count == 1
    assert candidates[0]["candidate_type"] == BOOK_LORE_CANDIDATE
    connection.close()


def test_candidate_service_rejects_direct_unsupported_book_experience():
    connection = sqlite3.connect(":memory:")
    repositories = LearningRepositories.from_connection(connection)
    service = LearningCandidateService(repositories)

    with pytest.raises(ValueError, match="book_experience_episode evidence"):
        service.create(
            bot_id="baizz",
            candidate_type=BOOK_EXPERIENCE_CANDIDATE,
            content="未经证据的经历",
            evidence={"chapter_ref": ""},
            source_fingerprint="episode-1",
        )
    connection.close()


def test_yushu_does_not_register_book_experience_by_default_but_explicit_enable_uses_same_guard():
    registry = LearningSourceRegistry()
    assert register_book_experience_task(registry, bot_id="yushu", config={}) is None
    assert "book_experience" not in registry

    enabled = {
        "Learning_Settings": {
            "bots": {
                "yushu": {
                    "tasks": {"book_experience_episode_enabled": True},
                    "book_experience": {"target_bot_role": "harbor-warden"},
                }
            }
        }
    }
    adapter = register_book_experience_task(registry, bot_id="yushu", config=enabled)
    assert adapter is not None
    assert registry.resolve("book_experience") is adapter
    invalid = list(adapter.collect(
        bot_id="yushu",
        source={"config": {"items": [{"content": "x", "evidence": {}}]}},
        job={},
        cursor=None,
    ))
    assert invalid[0].metadata["candidate_type"] == BOOK_LORE_CANDIDATE
    assert resolve_learning_config(enabled).for_bot("yushu").task_enabled(
        "book_experience_episode_enabled"
    ) is True
