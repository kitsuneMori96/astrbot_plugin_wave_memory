from __future__ import annotations

import asyncio
import json
import sqlite3
import types

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.migrations.scoped_soul import ensure_scoped_soul_schema
from engine.db.outbox_repo import OutboxRepository
from engine.db.scoped_soul_repo import ScopedSoulRepository
from services.relationship_calibration import RelationshipCalibrationGateway


def scope(group: str = "g1", subject: str = "qq:user:u1") -> RuntimeScope:
    return RuntimeScope(
        "bot-alpha",
        "group",
        SessionRef(f"qq:group:{group}", "qq", "group", group),
        subject_principal_id=subject,
    )


def evidence(value: RuntimeScope) -> list[dict]:
    return [{
        "kind": "raw_message",
        "id": "message:1",
        "content_hash": "sha256:message-1",
        "captured_at": 100.0,
        "source_scope": value.to_dict(),
        "available": True,
    }]


def test_four_layers_preserve_manual_values_when_automatic_events_continue(tmp_path):
    manager = ConnectionManager(str(tmp_path / "relationship.db"))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        value_scope = scope()
        repo.upsert_relationship(value_scope, subject_principal_id=value_scope.subject_principal_id, affinity=7, dimensions={"trust": 20, "familiarity": 10})
        initial = repo.get_state(value_scope, subject_principal_id=value_scope.subject_principal_id)
        assert initial["relationship"]["revision"] == 1
        assert initial["relationship"]["values"]["trust"]["automatic_value"] == 20
        assert initial["relationship"]["values"]["trust"]["manual_adjustment"] is None

        with manager.write_transaction() as tx:
            calibrated = repo.calibrate_relationship(
                value_scope,
                subject_principal_id="qq:user:u1",
                expected_revision=1,
                action="adjust",
                dimension="trust",
                delta=5,
                reason="人工确认协作可靠性",
                evidence=evidence(value_scope),
                operation_id="calibration-1",
                connection=tx,
            )
        assert calibrated["revision"] == 2
        with manager.write_transaction() as tx:
            automatic = repo.record_relationship_event(
                value_scope,
                event_type="direct_reply",
                dimension="trust",
                delta=4,
                reason="完成一次直接回复",
                created_at=200.0,
                connection=tx,
            )
        assert automatic["applied_delta"] == 4
        state = repo.get_state(value_scope, subject_principal_id=value_scope.subject_principal_id)
        trust = state["relationship"]["values"]["trust"]
        assert trust["automatic_value"] == 24
        assert trust["manual_adjustment"] == 5
        assert trust["manual_override"] is None
        assert trust["effective_value"] == 29
        assert state["relationship"]["revision"] == 3
    finally:
        manager.close()


def test_relationship_history_merges_real_layers_and_time_scope(tmp_path):
    manager = ConnectionManager(str(tmp_path / "relationship-history.db"))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        value_scope = scope()
        repo.upsert_relationship(value_scope, subject_principal_id=value_scope.subject_principal_id, affinity=20, dimensions={"trust": 20})
        with manager.write_transaction() as tx:
            automatic = repo.record_relationship_event(
                value_scope,
                event_type="direct_reply",
                dimension="trust",
                delta=4,
                reason="真实消息触发自动变化",
                source_memory_id=101,
                created_at=100.0,
                connection=tx,
            )
        assert automatic["event_id"]
        with manager.write_transaction() as tx:
            calibrated = repo.calibrate_relationship(
                value_scope,
                subject_principal_id="qq:user:u1",
                expected_revision=2,
                action="adjust",
                dimension="trust",
                delta=5,
                reason="人工确认该变化",
                evidence=evidence(value_scope),
                operation_id="calibration-history",
                created_at=105.0,
                connection=tx,
            )
            tx.execute(
                """INSERT INTO scoped_soul_relationship_calibration_events(
                       calibration_id, operation_id, bot_id, session_id, visibility,
                       subject_principal_id, dimension, action, before_json, after_json,
                       reason, evidence, actor, relationship_revision, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("calibration-history", "calibration-history", value_scope.bot_id, value_scope.session.id,
                 value_scope.visibility, value_scope.subject_principal_id, "trust", "adjust",
                 json.dumps(calibrated["before"], ensure_ascii=False),
                 json.dumps(calibrated["after"], ensure_ascii=False), "人工确认该变化",
                 json.dumps(evidence(value_scope), ensure_ascii=False), "webui", calibrated["revision"], 105.0),
            )
        history = repo.get_state(value_scope, subject_principal_id=value_scope.subject_principal_id, from_ts=90.0, to_ts=110.0)["relationship_history"]
        assert history["total"] == 2
        assert [item["kind"] for item in reversed(history["items"])] == ["automatic", "manual"]
        automatic_item = next(item for item in history["items"] if item["kind"] == "automatic")
        assert automatic_item["source_memory_id"] == 101
        assert automatic_item["before"]["automatic_value"] == 20
        assert automatic_item["after"]["automatic_value"] == 24
        manual_item = next(item for item in history["items"] if item["kind"] == "manual")
        assert manual_item["after"]["manual_adjustment"] == 5
        assert repo.get_state(scope("g2"), subject_principal_id="qq:user:u1")["relationship_history"]["total"] == 0
    finally:
        manager.close()


def test_unknown_relationship_does_not_create_zero_baseline(tmp_path):
    manager = ConnectionManager(str(tmp_path / "unknown.db"))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        state = repo.get_state(scope(), subject_principal_id="qq:user:u1")
        relationship = state["relationship"]
        assert relationship["state"] == "unknown"
        assert relationship["affinity"] is None
        assert relationship["values"] is None
        assert relationship["calibration"]["available"] is False
    finally:
        manager.close()


def test_manual_actions_share_revision_and_cap_adjustment(tmp_path):
    manager = ConnectionManager(str(tmp_path / "manual-actions.db"))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        value_scope = scope()
        repo.upsert_relationship(value_scope, subject_principal_id=value_scope.subject_principal_id, affinity=7, dimensions={"trust": 20})
        with manager.write_transaction() as tx:
            adjusted = repo.calibrate_relationship(value_scope, subject_principal_id="qq:user:u1", expected_revision=1, action="adjust", dimension="trust", delta=100, reason="人工确认", evidence=evidence(value_scope), operation_id="adjust", connection=tx)
        assert adjusted["revision"] == 2
        assert adjusted["after"]["manual_adjustment"] == 20
        with manager.write_transaction() as tx:
            overridden = repo.calibrate_relationship(value_scope, subject_principal_id="qq:user:u1", expected_revision=2, action="override", dimension="trust", value=50, reason="人工覆盖", evidence=evidence(value_scope), operation_id="override", connection=tx)
        assert overridden["revision"] == 3
        with manager.write_transaction() as tx:
            cleared = repo.calibrate_relationship(value_scope, subject_principal_id="qq:user:u1", expected_revision=3, action="clear_override", dimension="trust", reason="取消覆盖", evidence=evidence(value_scope), operation_id="clear", connection=tx)
        assert cleared["revision"] == 4
        assert cleared["after"]["manual_adjustment"] == 20
        with manager.write_transaction() as tx:
            restored = repo.calibrate_relationship(value_scope, subject_principal_id="qq:user:u1", expected_revision=4, action="restore_auto", dimension="trust", reason="恢复自动", evidence=evidence(value_scope), operation_id="restore", connection=tx)
        assert restored["revision"] == 5
        assert restored["after"]["manual_adjustment"] is None
        assert restored["after"]["manual_override"] is None
    finally:
        manager.close()


def test_calibration_gateway_writes_audit_timeline_operation_and_outbox(tmp_path):
    manager = ConnectionManager(str(tmp_path / "gateway.db"))
    try:
        ensure_scoped_soul_schema(manager)
        with manager.write_transaction() as tx:
            OutboxRepository.migrate(tx)
        repo = ScopedSoulRepository(manager)
        value_scope = scope()
        repo.upsert_relationship(value_scope, subject_principal_id=value_scope.subject_principal_id, affinity=7, dimensions={"trust": 20})

        class Coordinator:
            _consumer_names = ()

            async def transaction(self, callback, *, actor=None):
                del actor
                with manager.write_transaction() as tx:
                    return callback(tx)

        gateway = RelationshipCalibrationGateway(types.SimpleNamespace(coordinator=Coordinator(), _consumers={}), repo)
        result = asyncio.run(gateway.calibrate(
            scope=value_scope,
            subject_principal_id="qq:user:u1",
            expected_revision=1,
            action="override",
            dimension="trust",
            value=50,
            reason="人工确认长期信任",
            evidence=evidence(value_scope),
        ))
        assert result.status == "succeeded"
        assert result.revision == 2
        assert manager.execute_read("SELECT COUNT(*) FROM scoped_soul_relationship_calibration_events").fetchone()[0] == 1
        timeline = manager.execute_read("SELECT event_summary FROM scoped_soul_timeline WHERE event_type='relationship.manual_calibration'").fetchone()
        assert timeline is not None and "人工确认长期信任" in timeline[0]
        assert manager.execute_read("SELECT COUNT(*) FROM domain_outbox WHERE aggregate_kind='relationship'").fetchone()[0] == 1
        assert manager.execute_read("SELECT status FROM write_operations WHERE operation_id=?", (result.operation_id,)).fetchone()[0] == "committed"
        assert manager.execute_read("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='user_profiles'").fetchone()[0] == 0
    finally:
        manager.close()
