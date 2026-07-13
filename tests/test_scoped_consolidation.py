"""Focused formal-path tests for scoped consolidation and belief extraction."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from types import SimpleNamespace

if "astrbot.api" not in sys.modules:
    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, debug=lambda *args, **kwargs: None)
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

from domain.scope import RuntimeScope, SessionRef
from engine.database import WaveMemoryDB
from services.belief_engine import BeliefEngine
from services.consolidation import ConsolidationService


class _Completion:
    def __init__(self, completion_text: str):
        self.completion_text = completion_text


class _Provider:
    async def text_chat(self, **kwargs):
        return _Completion(json.dumps({
            "summary": "大家讨论了如何照顾群里的流浪猫，并决定安排领养。",
            "topics": ["流浪猫领养"],
            "facts": [{"subject": "小明", "predicate": "计划", "object": "领养流浪猫"}],
            "relations": [{"source": "流浪猫领养", "target": "小明计划领养流浪猫", "type": "decides"}],
            "social": [{"person_a": "小明", "person_b": "小红", "relation": "合作"}],
            "nicknames": [{"person": "小明", "called": "猫管家"}],
        }, ensure_ascii=False))


class _Context:
    def get_provider_by_id(self, provider_id):
        return _Provider() if provider_id == "provider" else None


class _BeliefRecorder:
    def __init__(self):
        self.calls = []

    async def extract_from_summary(self, summary, scope, source_memory_ids=None):
        self.calls.append((summary, scope, source_memory_ids))
        return []


class _BeliefLLM:
    def __init__(self):
        self.calls = 0

    async def text_chat(self, **kwargs):
        self.calls += 1
        return _Completion('[{"content":"小明对照顾动物一直很有责任感", "type":"person_judgment"}]')


def _scope(bot_id="bot-alpha", group_id="group-1"):
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(f"qq:group:{group_id}", "qq", "group", group_id),
    )


def _add_messages(db, scope, count, prefix):
    return [
        db.add_memory(
            scope.session.conversation_id,
            f"{prefix} 第 {index} 条关于领养流浪猫的消息",
            sender_id=f"user-{index}",
            sender_name=f"用户{index}",
            timestamp=1000 + index,
            scope=scope,
        )
        for index in range(count)
    ]


def _legacy_counts(db):
    return {
        table: db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("facts", "tags", "memory_tags", "tag_relations", "beliefs", "kv_store")
    }


def test_consolidation_uses_exact_scope_cursor_and_never_writes_legacy_tables(tmp_path):
    db = WaveMemoryDB(str(tmp_path / "wave-memory.sqlite3"), dimension=4)
    try:
        alpha, beta = _scope(), _scope(bot_id="bot-beta")
        alpha_ids = _add_messages(db, alpha, 5, "alpha")
        beta_ids = _add_messages(db, beta, 5, "beta")
        db.add_memory("group-1", "隔离消息", sender_id="q", scope=alpha, quarantine=True)
        before = _legacy_counts(db)
        beliefs = _BeliefRecorder()
        service = ConsolidationService(
            db, context=_Context(), provider_id="provider", belief_engine=beliefs,
        )

        result = asyncio.run(service.consolidate_once())

        assert result["messages"] == 10
        assert db.get_scoped_consolidation_cursor(alpha, cursor_name="messages_v2_id") == str(alpha_ids[-1])
        assert db.get_scoped_consolidation_cursor(beta, cursor_name="messages_v2_id") == str(beta_ids[-1])
        assert len(db.list_scoped_facts(alpha)) == 3
        assert len(db.list_scoped_facts(beta)) == 3
        assert len(beliefs.calls) == 2
        assert all(call[1] in {alpha, beta} for call in beliefs.calls)
        assert all(set(call[2]).issubset(set(alpha_ids) | set(beta_ids)) for call in beliefs.calls)
        assert _legacy_counts(db) == before

        # Per-scope cursors prevent a second run from reprocessing the same messages.
        assert asyncio.run(service.consolidate_once())["messages"] == 0
    finally:
        db.close()


def test_belief_extraction_requires_scope_and_rejects_cross_scope_source_ids(tmp_path):
    db = WaveMemoryDB(str(tmp_path / "wave-memory.sqlite3"), dimension=4)
    try:
        alpha, beta = _scope(), _scope(bot_id="bot-beta")
        alpha_id = _add_messages(db, alpha, 1, "alpha")[0]
        beta_id = _add_messages(db, beta, 1, "beta")[0]
        llm = _BeliefLLM()
        engine = BeliefEngine(db, llm, bot_id="bot-alpha")
        summary = "小明多次主动照顾流浪猫，大家认可他的责任感。"
        before = _legacy_counts(db)

        assert asyncio.run(engine.extract_from_summary(summary, alpha, source_memory_ids=[beta_id])) == []
        assert llm.calls == 0
        created = asyncio.run(engine.extract_from_summary(summary, alpha, source_memory_ids=[alpha_id]))

        assert len(created) == 1
        scoped = db.list_scoped_beliefs(alpha)
        assert len(scoped) == 1
        assert scoped[0]["source_memory_id"] == alpha_id
        assert scoped[0]["status"] == "pending"
        assert db.list_scoped_beliefs(beta) == []
        assert _legacy_counts(db) == before
    finally:
        db.close()
