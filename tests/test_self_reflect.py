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
        return [0.1] * 1024


class _MemoryIndex:
    async def add_memory(self, *a, **k):
        return None

    def search_memories(self, *a, **k):
        return []


class _DB:
    conn = None


class _LLM:
    async def text_chat(self, *a, **k):
        return types.SimpleNamespace(completion_text="提炼后的认知结论，长度符合要求")


@pytest.fixture
def reflect():
    service = SelfReflectService(
        _DB(), _MemoryIndex(), _Embedding(), _LLM(), None, "",
        bot_name="bot", bot_id="bot-a",
        cooldown_seconds=0,
    )
    yield service


@pytest.mark.asyncio
async def test_recent_reply_isolated_between_bots(reflect):
    service = reflect
    service.record_reply("bot A 原回复", "group-1", bot_id="bot-a", scope=_scope("bot-a", subject="bot-a"))
    service.record_reply("bot B 原回复", "group-1", bot_id="bot-b", scope=_scope("bot-b", subject="bot-b"))

    assert await service.check_correction(
        "其实是潮汐改变商路", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"), message_id="m-a",
    )
    assert await service.check_correction(
        "其实是港口改变商路", "bob", "group-1", bot_id="bot-b",
        scope=_scope("bot-b", subject="bob"), message_id="m-b",
    )


@pytest.mark.asyncio
async def test_missing_message_scope_returns_false(reflect):
    service = reflect
    service.record_reply("有上下文的回复", "group-1", bot_id="bot-a", scope=_scope("bot-a", subject="bot-a"))
    assert not await service.check_correction(
        "其实是另一个事实", "alice", "group-1", bot_id="bot-a", message_id="m-missing-scope",
    )
    assert not await service.check_correction(
        "其实是另一个事实", "alice", "group-1", bot_id="bot-a",
        scope=_scope("bot-a", subject="alice"),
    )
