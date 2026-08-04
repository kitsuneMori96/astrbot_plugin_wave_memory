"""对话者统计画像（speaker_profile）注入回归测试。

背景：PersonaEvolution 的对话者画像因依赖 legacy read-model 被主动断开
（main.py 传 persona_evolution=None），导致 persona 通道长期只注入 45 tokens
的固定自我身份，bot 丢失了对熟人的统计认知。

这里锁住替代实现：只读 scope 键齐全的表（user_profiles 按
(user_id, group_id, bot_id)、memories 按 bot_id+session_id、scoped_facts 按
完整 RuntimeScope），不触碰 legacy facts / 全局聚合，且不依赖 LLM。
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

if "astrbot.api" not in sys.modules:  # pragma: no cover - test harness shim
    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        debug=lambda *a, **k: None,
    )
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

import pytest

from domain.scope import RuntimeScope, SessionRef
from engine.database import WaveMemoryDB
from services.persona_composer import PersonaComposer

BOT_DB_ID = "yushu"
GROUP_ID = "398291136"
SPEAKER_ID = "1465892206"


def _scope(bot_id=BOT_DB_ID, group_id=GROUP_ID) -> RuntimeScope:
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(f"qq:group:{group_id}", "qq", "group", group_id),
    )


def _profiles() -> dict:
    return {BOT_DB_ID: SimpleNamespace(name="羽书", db_id=BOT_DB_ID, aliases=["器灵"])}


def _seed_profile(db, *, nickname="练体博导", interactions=12,
                  first_seen=1778111762.0, last_seen=1785607753.0,
                  group_id=GROUP_ID, bot_id=BOT_DB_ID, user_id=SPEAKER_ID):
    db.conn.execute(
        """INSERT INTO user_profiles
               (user_id, group_id, bot_id, nickname, interaction_count, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, group_id, bot_id, nickname, interactions, first_seen, last_seen),
    )
    db.conn.commit()


def _composer(db) -> PersonaComposer:
    return PersonaComposer(db=db, bot_profiles=_profiles())


def _build(db, *, scope=None, sender_id=SPEAKER_ID, sender_name="练体博导"):
    return asyncio.run(
        _composer(db).build_self_persona(
            bot_id=BOT_DB_ID,
            group_id=GROUP_ID,
            sender_id=sender_id,
            sender_name=sender_name,
            message="我在家泳池游泳，遇到了很多斑衣蜡蝉",
            recent_context=[],
            scope=scope if scope is not None else _scope(),
        )
    )


@pytest.fixture()
def db(tmp_path):
    database = WaveMemoryDB(str(tmp_path / "wave-memory.sqlite3"), dimension=4)
    try:
        yield database
    finally:
        database.close()


class TestSpeakerBlockContent:
    def test_reports_message_count_interactions_and_dates(self, db):
        scope = _scope()
        _seed_profile(db)
        for index in range(3):
            db.add_memory(
                GROUP_ID,
                f"第 {index} 条关于斑衣蜡蝉的发言",
                sender_id=SPEAKER_ID,
                sender_name="练体博导",
                timestamp=1785600000 + index,
                scope=scope,
            )

        block = _build(db)["speaker_block"]

        assert "<speaker_profile>" in block
        assert "练体博导" in block
        assert SPEAKER_ID in block
        assert "本群发言 3 条" in block
        assert "与你直接互动 12 次" in block

    def test_marks_frequent_speaker_above_threshold(self, db):
        scope = _scope()
        _seed_profile(db, interactions=0)
        db.conn.execute(
            """INSERT INTO memories
                   (group_id, content, sender_id, sender_name, timestamp,
                    bot_id, session_id, visibility, quarantine, memory_type,
                    resolution_state)
               SELECT ?, '灌水', ?, '练体博导', 1785600000, ?, ?, 'group', 0,
                      'message', 'resolved'
                 FROM (WITH RECURSIVE c(x) AS (
                         SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 201)
                       SELECT x FROM c)""",
            (GROUP_ID, SPEAKER_ID, scope.bot_id, scope.session.id),
        )
        db.conn.commit()

        block = _build(db)["speaker_block"]

        assert "本群发言 201 条" in block
        assert "群里常客" in block

    def test_includes_scoped_facts_about_speaker(self, db):
        scope = _scope()
        _seed_profile(db)
        db.add_memory(
            GROUP_ID, "锚点消息", sender_id=SPEAKER_ID, sender_name="练体博导",
            timestamp=1785600000, scope=scope,
        )
        db.upsert_scoped_fact(
            scope,
            subject="练体博导",
            predicate="在",
            object="Duke 做核物理实验",
            confidence=0.7,
            status="pending",
        )

        block = _build(db)["speaker_block"]

        assert "已知信息：" in block
        assert "Duke 做核物理实验" in block

    def test_excludes_rejected_scoped_facts(self, db):
        """被否决的事实不能进入画像。"""
        scope = _scope()
        _seed_profile(db)
        db.add_memory(
            GROUP_ID, "锚点消息", sender_id=SPEAKER_ID, sender_name="练体博导",
            timestamp=1785600000, scope=scope,
        )
        db.upsert_scoped_fact(
            scope, subject="练体博导", predicate="据称", object="已被否决的说法",
            confidence=0.7, status="rejected",
        )

        assert "已被否决的说法" not in _build(db)["speaker_block"]

    def test_block_warns_against_fabrication(self, db):
        _seed_profile(db)
        assert "不要据此编造未记录的经历" in _build(db)["speaker_block"]

    def test_debug_records_sources_used(self, db):
        scope = _scope()
        _seed_profile(db)
        db.add_memory(
            GROUP_ID, "锚点", sender_id=SPEAKER_ID, timestamp=1785600000, scope=scope,
        )

        debug = _build(db)["debug"]

        assert debug["speaker_source"] == "scoped_speaker_profile"
        assert "user_profiles" in debug["speaker_sources"]
        assert "memories" in debug["speaker_sources"]


class TestSpeakerBlockScopeIsolation:
    def test_does_not_count_other_groups(self, db):
        """他在别的群的发言不能算进当前群的统计。"""
        here, elsewhere = _scope(), _scope(group_id="1015727706")
        _seed_profile(db, interactions=0)
        db.add_memory(
            GROUP_ID, "本群发言", sender_id=SPEAKER_ID, timestamp=1785600000, scope=here,
        )
        for index in range(5):
            db.add_memory(
                "1015727706", f"他群发言 {index}", sender_id=SPEAKER_ID,
                timestamp=1778300000 + index, scope=elsewhere,
            )

        assert "本群发言 1 条" in _build(db)["speaker_block"]

    def test_ignores_profile_of_other_bot(self, db):
        """user_profiles 唯一键含 bot_id，别的 bot 的画像不能串过来。"""
        _seed_profile(db, interactions=99, bot_id="baizz")

        block = _build(db)["speaker_block"]

        assert "99" not in block

    def test_excludes_quarantined_messages(self, db):
        scope = _scope()
        _seed_profile(db, interactions=0)
        db.add_memory(
            GROUP_ID, "正常发言", sender_id=SPEAKER_ID, timestamp=1785600000, scope=scope,
        )
        db.add_memory(
            GROUP_ID, "被隔离", sender_id=SPEAKER_ID, timestamp=1785600001,
            scope=scope, quarantine=True,
        )

        assert "本群发言 1 条" in _build(db)["speaker_block"]

    def test_requires_resolved_group_scope(self, db):
        _seed_profile(db)
        payload = _build(db, scope="not-a-scope")

        assert payload["speaker_block"] == ""
        assert payload["debug"]["speaker_source"] == "scope_required"


class TestSpeakerBlockGuards:
    def test_no_block_for_bot_itself(self, db):
        payload = _build(db, sender_id=BOT_DB_ID, sender_name="羽书")

        assert payload["speaker_block"] == ""
        assert payload["debug"]["speaker_source"] == "speaker_is_bot"

    def test_no_block_for_unknown_speaker_without_signal(self, db):
        payload = _build(db, sender_id="9999", sender_name="路人")

        assert payload["speaker_block"] == ""
        assert payload["debug"]["speaker_source"] == "no_speaker_signal"

    def test_falls_back_to_sender_name_when_profile_missing(self, db):
        scope = _scope()
        db.add_memory(
            GROUP_ID, "第一次发言", sender_id="9999", sender_name="路人",
            timestamp=1785600000, scope=scope,
        )

        block = _build(db, sender_id="9999", sender_name="路人")["speaker_block"]

        assert "路人" in block
        assert "本群发言 1 条" in block

    def test_does_not_touch_legacy_facts_table(self, db):
        scope = _scope()
        _seed_profile(db)
        db.add_memory(
            GROUP_ID, "锚点", sender_id=SPEAKER_ID, sender_name="练体博导",
            timestamp=1785600000, scope=scope,
        )
        db.conn.execute(
            "INSERT INTO facts (subject, predicate, object, confidence) VALUES (?, ?, ?, ?)",
            ("练体博导", "喜欢", "legacy 污染值", 1.0),
        )
        db.conn.commit()

        block = _build(db)["speaker_block"]

        assert "legacy 污染值" not in block

    def test_missing_timestamps_do_not_break_block(self, db):
        _seed_profile(db, first_seen=None, last_seen=0)

        block = _build(db)["speaker_block"]

        assert "首次出现" not in block
        assert "最近出现" not in block
        assert "与你直接互动 12 次" in block


class TestPersonaChannelWiring:
    def test_channel_emits_speaker_profile_candidate(self, db):
        """通道必须真的把 speaker_block 作为候选注入，而不是丢弃。"""
        from services.injection.channels.persona import PersonaChannel
        from services.injection.context import InjectionContext

        scope = _scope()
        _seed_profile(db)
        db.add_memory(
            GROUP_ID, "锚点", sender_id=SPEAKER_ID, sender_name="练体博导",
            timestamp=1785600000, scope=scope,
        )
        channel = PersonaChannel(composer=_composer(db), persona_evolution=None)
        ctx = InjectionContext(
            event="event",
            req=object(),
            message="斑衣蜡蝉对人有害吗",
            group_id=GROUP_ID,
            sender_id=SPEAKER_ID,
            sender_name="练体博导",
            bot_id="1923563505",
            bot_profile_id=BOT_DB_ID,
            scope=scope,
            recent_context=[],
        )

        result = asyncio.run(channel.build(ctx))
        blocks = [item.get("block") for item in result.items]

        assert result.status == "hit"
        assert "speaker_profile" in blocks
        assert "本群发言" in result.text

    def test_persona_channel_allows_two_blocks(self):
        """max_items=1 会让 speaker_profile 永远进不了注入。"""
        from services.config.channel_config import build_default_channel_config

        configs = build_default_channel_config(runtime_mode="full")

        assert configs.channels["persona"].max_items >= 2
