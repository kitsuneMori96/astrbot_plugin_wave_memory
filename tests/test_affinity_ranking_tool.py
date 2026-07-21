from __future__ import annotations

import asyncio
import sqlite3
import sys
import types
from types import SimpleNamespace


def _install_astrbot_tool_stub() -> None:
    """The focused tool test only needs FunctionTool's generic base type."""
    if "astrbot.core.agent.tool" in sys.modules:
        return

    class FunctionTool:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

    astrbot = types.ModuleType("astrbot")
    core = types.ModuleType("astrbot.core")
    agent = types.ModuleType("astrbot.core.agent")
    tool = types.ModuleType("astrbot.core.agent.tool")
    run_context = types.ModuleType("astrbot.core.agent.run_context")
    astr_context = types.ModuleType("astrbot.core.astr_agent_context")
    tool.FunctionTool = FunctionTool
    run_context.ContextWrapper = object
    astr_context.AstrAgentContext = object
    sys.modules.update({
        "astrbot": astrbot,
        "astrbot.core": core,
        "astrbot.core.agent": agent,
        "astrbot.core.agent.tool": tool,
        "astrbot.core.agent.run_context": run_context,
        "astrbot.core.astr_agent_context": astr_context,
    })


_install_astrbot_tool_stub()

from domain.scope import RuntimeScope, SessionRef
from tools.affinity_update import WaveMemoryAffinityTool


class _Repository:
    def __init__(self):
        self.list_scopes = []

    def list_relationships(self, scope):
        self.list_scopes.append(scope)
        return [
            {
                "subject_principal_id": "qq:user:u1",
                "affinity": 42,
                "state": "friendly",
                "values": {"trust": {"effective_value": 50}},
            },
            {
                "subject_principal_id": "qq:user:u2",
                "affinity": -8,
                "state": "cold",
                "values": {"hostility": {"effective_value": 20}},
            },
        ]

    def get_state(self, _scope, **_kwargs):
        return {
            "relationship": {
                "affinity": 42,
                "state": "friendly",
                "dimensions": {"familiarity": 30, "trust": 50, "fun": 4, "hostility": 0, "depth": 18},
                "values": {},
            },
        }

    def list_legacy_relationship_audit_summary(self, _scope, **_kwargs):
        return {
            "available": True,
            "total": 12,
            "by_type": [
                {"event_type": "direct_reply", "count": 10},
                {"event_type": "bot_attacked", "count": 2},
            ],
            "recent": [
                {
                    "event_type": "direct_reply",
                    "dimension": "familiarity",
                    "delta": 0.5,
                    "reason": "看见一条群友消息",
                    "occurred_at": 1.0,
                    "legacy_event_id": "10",
                }
            ],
        }


def _context() -> SimpleNamespace:
    scope = RuntimeScope(
        "bot-a",
        "group",
        SessionRef("qq:group:g1", "qq", "group", "g1"),
        subject_principal_id="qq:user:u1",
    )
    return SimpleNamespace(context=SimpleNamespace(event=SimpleNamespace(_wave_memory_runtime_scope=scope)))


def _tool() -> WaveMemoryAffinityTool:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE user_profiles(user_id TEXT, group_id TEXT, bot_id TEXT, nickname TEXT, interaction_count INTEGER, last_seen REAL);
        CREATE TABLE person_registry(qq_id TEXT, display_name TEXT);
        INSERT INTO user_profiles VALUES ('u1', 'g1', 'bot-a', '甲昵称', 5, 1);
        INSERT INTO user_profiles VALUES ('u2', 'g1', 'bot-a', '乙昵称', 23, 1);
        INSERT INTO user_profiles VALUES ('u3', 'g2', 'bot-a', '跨群用户', 999, 1);
        INSERT INTO person_registry VALUES ('u1', '甲');
        INSERT INTO person_registry VALUES ('u2', '乙');
        INSERT INTO person_registry VALUES ('u3', '不应出现');
        """
    )
    return WaveMemoryAffinityTool(db=SimpleNamespace(conn=connection, soul_repository=_Repository()))


def test_formal_affinity_tool_restores_group_scoped_rankings_only():
    tool = _tool()
    ctx = _context()

    ranking = asyncio.run(tool.call(ctx, mode="ranking", limit=10))
    blacklist = asyncio.run(tool.call(ctx, mode="blacklist", limit=10))
    active = asyncio.run(tool.call(ctx, mode="active", limit=10))

    assert "甲（u1）" in ranking
    assert "乙（u2）" not in ranking
    assert "乙（u2）" in blacklist
    assert "乙（u2）" in active
    assert "跨群用户" not in active
    # 排行只放宽 subject 过滤；Bot + canonical group session 仍不可改变。
    queried_scopes = tool.db.soul_repository.list_scopes
    assert len(queried_scopes) == 3
    assert all(item.subject_principal_id is None for item in queried_scopes)
    assert all(item.bot_id == "bot-a" and item.session == ctx.context.event._wave_memory_runtime_scope.session for item in queried_scopes)


def test_formal_affinity_tool_keeps_single_query_in_current_scope():
    tool = _tool()

    result = asyncio.run(tool.call(_context(), mode="single", target_user="u1"))

    assert "关系对象：甲（u1）" in result
    assert "hostility: 0" in result
    assert "历史事件审计：12 条" in result
    assert "direct_reply×10" in result
    assert "不改变好感度" in result
