import sqlite3
import sys
import types

import pytest

if "astrbot.api" not in sys.modules:
    api = types.ModuleType("astrbot.api")
    api.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        debug=lambda *a, **k: None,
    )
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

from domain.scope import RuntimeScope, SessionRef
from engine.db.learning_repository import LearningRepositories
from services.learning.candidate_service import LearningCandidateService
from services.self_reflect import SelfReflectService


def _scope(bot_id="bot-a", group_id="group-1", subject="alice"):
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(
            id=f"test:group:{group_id}",
            platform_id="test",
            kind="group",
            conversation_id=group_id,
        ),
        subject_principal_id=f"test:user:{subject}",
    )


class _Embedding:
    async def get_embedding(self, text):
        return [0.1, 0.2, 0.3]


class _LLM:
    class _Response:
        completion_text = "我记得正确的理解是潮汐会改变商路。"

    async def text_chat(self, **kwargs):
        return self._Response()


class _MemoryIndex:
    def search(self, *args, **kwargs):
        raise AssertionError("unapproved correction must not search/write online memories")

    def add(self, *args, **kwargs):
        raise AssertionError("unapproved correction must not write online memories")


class _DB:
    def add_memory(self, **kwargs):
        raise AssertionError("unapproved correction must not write memories")

    def insert_fact(self, *args, **kwargs):
        raise AssertionError("unapproved correction must not write facts")


@pytest.fixture
def reflect():
    connection = sqlite3.connect(":memory:")
    repositories = LearningRepositories.from_connection(connection)
    service = SelfReflectService(
        _DB(), _MemoryIndex(), _Embedding(), _LLM(), None, "",
        bot_name="bot", bot_id="bot-a", repositories=repositories,
        cooldown_seconds=0,
    )
    yield service, repositories
    connection.close()


@pytest.mark.asyncio
async def test_recent_reply_isolated_between_bots_and_evidence_keeps_bot_scope(reflect):
    service, repositories = reflect
    service.record_reply("bot A 原回复", "group-1", bot_id="bot-a", scope=_scope("bot-a", subject="bot-a"))
    service.record_reply("bot B 原回复", "group-1", bot_id="bot-b", scope=_scope("bot-b", subject="bot-b"))

    assert await service.check_correction(
        "其实是潮汐改变商路", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"), message_id="m-a",
    )
    candidates, total = repositories.candidates.list(bot_id="bot-a")
    assert total == 1
    assert candidates[0]["evidence"]["bot_reply"] == "bot A 原回复"
    assert candidates[0]["evidence"]["message_ref"] == {
        "message_id": "m-a", "group_id": "group-1", "session_id": "test:group:group-1",
    }
    assert candidates[0]["evidence"]["scope"]["bot_id"] == "bot-a"
    assert candidates[0]["evidence"]["evidence_ref"]["source_scope"] == candidates[0]["evidence"]["scope"]

    assert await service.check_correction(
        "其实是港口改变商路", "bob", "group-1", bot_id="bot-b",
        scope=_scope("bot-b", subject="bob"), message_id="m-b",
    )
    candidates_b, total_b = repositories.candidates.list(bot_id="bot-b")
    assert total_b == 1
    assert candidates_b[0]["evidence"]["bot_reply"] == "bot B 原回复"


@pytest.mark.asyncio
async def test_cooldown_isolated_and_unreviewed_correction_does_not_write_memory_or_fact(reflect):
    service, repositories = reflect
    service.cooldown = 300
    service.record_reply("同一原回复", "group-1", bot_id="bot-a", scope=_scope("bot-a", subject="bot-a"))
    service.record_reply("同一原回复", "group-1", bot_id="bot-b", scope=_scope("bot-b", subject="bot-b"))

    assert await service.check_correction(
        "你说错了，应该是潮汐", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"), message_id="m-a",
    )
    assert not await service.check_correction(
        "你说错了，应该是潮汐", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"), message_id="m-a-repeat",
    )
    assert await service.check_correction(
        "你说错了，应该是潮汐", "bob", "group-1", bot_id="bot-b",
        scope=_scope("bot-b", subject="bob"), message_id="m-b",
    )
    assert repositories.candidates.list(bot_id="bot-a")[1] == 1
    assert repositories.candidates.list(bot_id="bot-b")[1] == 1


@pytest.mark.asyncio
async def test_missing_message_scope_evidence_does_not_create_candidate(reflect):
    service, repositories = reflect
    service.record_reply("有上下文的回复", "group-1", bot_id="bot-a", scope=_scope("bot-a", subject="bot-a"))
    assert not await service.check_correction(
        "其实是另一个事实", "alice", "group-1", bot_id="bot-a", message_id="m-missing-scope",
    )
    assert not await service.check_correction(
        "其实是另一个事实", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"),
    )
    assert repositories.candidates.list(bot_id="bot-a")[1] == 0


@pytest.mark.asyncio
async def test_missing_message_id_does_not_consume_correction_cooldown(reflect):
    service, repositories = reflect
    service.cooldown = 300
    service.record_reply("有上下文的回复", "group-1", bot_id="bot-a", scope=_scope("bot-a", subject="bot-a"))

    assert not await service.check_correction(
        "其实是另一个事实", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"),
    )
    assert await service.check_correction(
        "其实是另一个事实", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"), message_id="m-recovered",
    )
    assert repositories.candidates.list(bot_id="bot-a")[1] == 1


@pytest.mark.asyncio
async def test_correction_candidate_is_deduplicated_by_bot_and_fingerprint(reflect):
    service, repositories = reflect
    service.cooldown = 0
    for index in range(2):
        service.record_reply("原回复", "group-1", bot_id="bot-a", scope=_scope("bot-a", subject="bot-a"))
        assert await service.check_correction(
            "你说错了", "alice", "group-1", bot_id="bot-a",
            scope=_scope("bot-a", subject="alice"), message_id=f"m-{index}",
        )
    assert repositories.candidates.list(bot_id="bot-a")[1] == 1


@pytest.mark.asyncio
async def test_book_lore_lookup_requires_explicit_catalog_scope():
    class TrackingEmbedding:
        def __init__(self):
            self.calls = []

        async def get_embedding(self, text):
            self.calls.append(text)
            return [0.1, 0.2, 0.3]

    class TrackingBookLore:
        def __init__(self):
            self.calls = []

        def search_communities(self, vector, k=2):
            self.calls.append((vector, k))
            return []

    embedding = TrackingEmbedding()
    lore_index = TrackingBookLore()
    service = SelfReflectService(
        _DB(), _MemoryIndex(), embedding, _LLM(), lore_index, "unreachable.db",
        bot_name="bot", bot_id="bot-a",
    )

    knowledge, hits = await service._search_book_lore("bot-a", "原回复", "纠正内容")

    assert knowledge == "（无额外参考）"
    assert hits == []
    assert embedding.calls == []
    assert lore_index.calls == []

