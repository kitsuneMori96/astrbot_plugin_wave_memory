"""时间线双轨兜底与 quarantined 过滤回归测试。

背景：记忆整合依赖外部 LLM provider，该 provider 持续 503 后 summary 自 7-13 起
零产出。当时 TimelineChannel 只查 `summary IS NOT NULL`，于是对近期活跃的用户
只能捞到数月前仅存的旧摘要，表现得像"时间线错乱"，而实际是数据断供且无人察觉。

这里锁住三件事：
1. summary 不足时回退该用户的原文发言，而不是静默退到更早的历史；
2. 回退行必须带 degraded_to_raw_content 标记，让停产在 trace 中可观测；
3. `quarantined:` 隔离标记（真实库里有 959 条）不得被当成事件摘要注入。
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

_COLUMNS = (
    "id, summary, timestamp, group_id, sender_id, content, "
    "bot_id, session_id, visibility, resolution_state, quarantine"
)
_INSERT = f"INSERT INTO memories ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
NOW = 1_700_000_000.0


class _DBBox:
    def __init__(self, conn):
        self.conn = conn


def _row(memory_id, summary, timestamp, sender_id, content, *,
         group_id="g1", bot_id="bot-a", quarantine=0):
    return (
        memory_id, summary, timestamp, group_id, sender_id, content,
        bot_id, f"qq:group:{group_id}", "group", "resolved", quarantine,
    )


def _scope(*, bot_id="bot-a", group_id="g1"):
    from domain.scope import RuntimeScope, SessionRef

    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(f"qq:group:{group_id}", "qq", "group", group_id),
        subject_principal_id="qq:user:u1",
    )


def _ctx(*, max_items=3, now=NOW, days=0):
    from services.injection.context import InjectionContext

    channel_cfg = {"channels": {"timeline": {"max_items": max_items}}}
    if days:
        channel_cfg["timeline"] = {"days": days}
    return InjectionContext(
        event="event",
        req=object(),
        message="最近怎么样",
        group_id="g1",
        sender_id="u1",
        sender_name="练体博导",
        bot_id="bot",
        bot_profile_id="yushu",
        scope=_scope(),
        recent_context=[],
        mode="full",
        config=channel_cfg,
        now=now,
        trace_id="trace-timeline-fallback",
    )


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY, summary TEXT, timestamp REAL, group_id TEXT,
            sender_id TEXT, content TEXT, bot_id TEXT, session_id TEXT,
            visibility TEXT, resolution_state TEXT, quarantine INTEGER
        )"""
    )
    try:
        yield _DBBox(conn)
    finally:
        conn.close()


def _build(db, ctx=None):
    from services.injection.channels.timeline import TimelineChannel

    channel = TimelineChannel(db=db, cross_group_enabled=False)
    return asyncio.run(channel.build(ctx or _ctx()))


class TestRawContentFallback:
    def test_falls_back_to_raw_content_when_no_summary_exists(self, db):
        """整合停产期间的发言必须仍然进入时间线。"""
        db.conn.executemany(_INSERT, [
            _row(1, None, NOW - 3600, "u1", "我在家泳池游泳，遇到了很多斑衣蜡蝉"),
            _row(2, "", NOW - 7200, "u1", "dc现在一到夏天一堆这个斑衣蜡蝉"),
        ])

        result = _build(db)

        assert result.status == "hit"
        assert "[最近发言片段（尚未生成事件摘要）]" in result.text
        assert "斑衣蜡蝉" in result.text
        assert all(item["degraded_to_raw_content"] for item in result.items)

    def test_degraded_rows_are_marked_for_trace_observability(self, db):
        db.conn.execute(_INSERT, _row(1, None, NOW - 60, "u1", "明天飞温哥华"))

        item = _build(db).items[0]

        assert item["degraded_to_raw_content"] is True
        assert item["source"] == "timeline_raw"

    def test_summary_rows_are_not_marked_degraded(self, db):
        db.conn.execute(_INSERT, _row(1, "一起排查了注入链路", NOW - 60, "u1", "正文"))

        item = _build(db).items[0]

        assert "degraded_to_raw_content" not in item
        assert result_text_has_event_heading(_build(db).text)

    def test_mixed_mode_keeps_summary_and_raw_in_separate_sections(self, db):
        """有摘要的旧事件与无摘要的新发言必须分区呈现，避免把原话当结论。"""
        db.conn.executemany(_INSERT, [
            _row(1, "五月一起聊过内部事务", NOW - 80 * 86400, "u1", "旧事件正文"),
            _row(2, None, NOW - 3600, "u1", "我现在还在dc，明天飞温哥华"),
        ])

        text = _build(db).text

        assert "[最近与此人的事件]" in text
        assert "[最近发言片段（尚未生成事件摘要）]" in text
        assert text.index("[最近与此人的事件]") < text.index("[最近发言片段")

    def test_raw_fallback_only_fills_newer_than_latest_summary(self, db):
        """已有摘要覆盖的时段不重复用原文填充。"""
        db.conn.executemany(_INSERT, [
            _row(1, "已归纳的近期事件", NOW - 3600, "u1", "该时段正文"),
            _row(2, None, NOW - 7200, "u1", "更早的未归纳发言"),
            _row(3, None, NOW - 600, "u1", "更新的未归纳发言"),
        ])

        text = _build(db).text

        assert "更新的未归纳发言" in text
        assert "更早的未归纳发言" not in text

    def test_no_fallback_when_summaries_already_fill_quota(self, db):
        db.conn.executemany(_INSERT, [
            _row(1, "事件一", NOW - 60, "u1", "正文一"),
            _row(2, "事件二", NOW - 120, "u1", "正文二"),
            _row(3, None, NOW - 30, "u1", "不该出现的原文"),
        ])

        result = _build(db, _ctx(max_items=2))

        assert len(result.items) == 2
        assert "不该出现的原文" not in result.text

    def test_raw_fallback_respects_max_items(self, db):
        db.conn.executemany(_INSERT, [
            _row(index, None, NOW - index * 60, "u1", f"发言 {index}")
            for index in range(1, 8)
        ])

        assert len(_build(db, _ctx(max_items=3)).items) == 3

    def test_raw_fallback_deduplicates_repeated_content(self, db):
        db.conn.executemany(_INSERT, [
            _row(1, None, NOW - 60, "u1", "是个好东西"),
            _row(2, None, NOW - 120, "u1", "是个好东西"),
            _row(3, None, NOW - 180, "u1", "另一句话"),
        ])

        summaries = [item["summary"] for item in _build(db).items]

        assert summaries.count("是个好东西") == 1
        assert "另一句话" in summaries


class TestQuarantinedExclusion:
    def test_quarantined_marker_is_not_injected_as_event(self, db):
        """真实库里有 959 条 'quarantined: ...' 标记，它们不是事件摘要。"""
        db.conn.execute(
            _INSERT,
            _row(1, "quarantined: transient roleplay/identity confusion",
                 NOW - 60, "u1", "正文"),
        )

        result = _build(db)

        assert "quarantined" not in result.text

    def test_quarantined_summary_falls_through_to_raw_content(self, db):
        db.conn.execute(
            _INSERT,
            _row(1, "quarantined: transient roleplay/identity confusion",
                 NOW - 60, "u1", "这句正文应当被使用"),
        )

        result = _build(db)

        assert "这句正文应当被使用" in result.text
        assert result.items[0]["degraded_to_raw_content"] is True


class TestFallbackScopeIsolation:
    def test_raw_fallback_excludes_other_groups(self, db):
        db.conn.executemany(_INSERT, [
            _row(1, None, NOW - 60, "u1", "本群发言"),
            _row(2, None, NOW - 30, "u1", "他群发言", group_id="g2"),
        ])

        text = _build(db).text

        assert "本群发言" in text
        assert "他群发言" not in text

    def test_raw_fallback_excludes_other_speakers(self, db):
        """原文兜底是"与此人"的时间线，不能混入别人的发言。"""
        db.conn.executemany(_INSERT, [
            _row(1, None, NOW - 60, "u1", "本人发言"),
            _row(2, None, NOW - 30, "other", "别人发言"),
        ])

        text = _build(db).text

        assert "本人发言" in text
        assert "别人发言" not in text

    def test_raw_fallback_excludes_quarantined_rows(self, db):
        db.conn.executemany(_INSERT, [
            _row(1, None, NOW - 60, "u1", "正常发言"),
            _row(2, None, NOW - 30, "u1", "被隔离的发言", quarantine=1),
        ])

        text = _build(db).text

        assert "正常发言" in text
        assert "被隔离的发言" not in text

    def test_raw_fallback_respects_days_window(self, db):
        db.conn.executemany(_INSERT, [
            _row(1, None, NOW - 3600, "u1", "窗口内发言"),
            _row(2, None, NOW - 30 * 86400, "u1", "窗口外发言"),
        ])

        text = _build(db, _ctx(days=7)).text

        assert "窗口内发言" in text
        assert "窗口外发言" not in text

    def test_empty_when_speaker_has_no_activity(self, db):
        db.conn.execute(_INSERT, _row(1, None, NOW - 60, "other", "别人的发言"))

        result = _build(db)

        assert result.status == "empty"


def result_text_has_event_heading(text: str) -> bool:
    return "[最近与此人的事件]" in text
