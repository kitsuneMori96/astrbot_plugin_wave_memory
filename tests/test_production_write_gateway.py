from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

if "astrbot.api" not in sys.modules:
    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

from domain.commands import CommandRejectedError
from domain.scope import RuntimeScope, SessionRef
from engine.database import WaveMemoryDB
from services.quality_gate import QualityGate
from services.system_convergence_runtime import ProductionWriteGateway


def _scope() -> RuntimeScope:
    return RuntimeScope(
        bot_id="bot-alpha",
        visibility="group",
        session=SessionRef("qq:group:group-1", "qq", "group", "group-1"),
    )


@pytest.mark.asyncio
async def test_production_gateway_commits_memory_tags_operation_and_outbox_atomically(tmp_path):
    path = str(tmp_path / "production-write.sqlite3")
    db = WaveMemoryDB(path, dimension=4)
    gateway = ProductionWriteGateway(path)
    try:
        quality_gate = QualityGate(repository=gateway.quality_repository, now=lambda: 99.0)
        proposal = quality_gate.propose(
            operation="message.write",
            content="coordinated memory",
            raw_artifact=quality_gate.make_raw_artifact(
                kind="chat_message",
                artifact_id="event-1",
                content="coordinated memory",
                source_scope=_scope(),
            ),
            target_scope=_scope(),
        )
        assert quality_gate.evaluate(proposal).allowed is True

        kwargs = dict(
            scope=_scope(),
            group_id="group-1",
            content="coordinated memory",
            vector=np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float32),
            sender_id="qq:user:user-1",
            sender_name="tester",
            timestamp=100.0,
            importance=0.8,
            source="chat",
            provenance={"event_id": "event-1"},
            origin_metadata={"event_id": "event-1"},
            quarantine=False,
            idempotency_hint="event-1",
        )
        first_id = await gateway.append_memory(**kwargs)
        replayed_id = await gateway.append_memory(**kwargs)
        assert replayed_id == first_id

        tag_count = await gateway.apply_tag_extraction(
            scope=_scope(),
            memory_id=first_id,
            tags=[{
                "name": "topic-a",
                "type": "topic",
                "confidence": np.float32(0.9),
                "embedding": [np.float32(0.1), np.float32(0.2)],
            }],
            status="done",
        )
        assert tag_count == 1
        assert db.conn.execute(
            "SELECT bot_id, session_id, visibility, source FROM memories WHERE id=?",
            (first_id,),
        ).fetchone() == ("bot-alpha", "qq:group:group-1", "group", "chat")
        assert db.conn.execute("SELECT COUNT(*) FROM scoped_tags").fetchone()[0] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM scoped_memory_tags").fetchone()[0] == 1
        assert db.conn.execute(
            "SELECT COUNT(*) FROM write_operations WHERE status='committed'"
        ).fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM domain_outbox").fetchone()[0] == 2
        tag_event_payload = db.conn.execute(
            "SELECT payload_json FROM domain_outbox WHERE event_type='memory.tags_applied'"
        ).fetchone()[0]
        assert '"bot_id":"bot-alpha"' in tag_event_payload
        assert '"id":"qq:group:group-1"' in tag_event_payload
        assert '"visibility":"group"' in tag_event_payload
        assert db.conn.execute("SELECT COUNT(*) FROM quality_decisions").fetchone()[0] == 1
    finally:
        await gateway.shutdown()
        db.close()


@pytest.mark.asyncio
async def test_production_gateway_enforces_single_process_writer_lease(tmp_path):
    path = str(tmp_path / "writer-lease.sqlite3")
    first = ProductionWriteGateway(path)
    try:
        with pytest.raises(CommandRejectedError) as error:
            ProductionWriteGateway(path)
        assert error.value.reason_code == "writer_lease_unavailable"
    finally:
        await first.shutdown()

    replacement = ProductionWriteGateway(path)
    await replacement.shutdown()
