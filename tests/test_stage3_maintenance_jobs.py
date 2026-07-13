from __future__ import annotations

from types import SimpleNamespace
import sqlite3

import numpy as np
import pytest

from engine.db.job_repo import JobRequest, JobRun
from services.pair_similarity_projection import compute_pair_similarity_projection
from webui.blueprints import blackbox, memories, tags


class _Request:
    def __init__(self, body):
        self._body = body

    async def get_json(self, *args, **kwargs):
        return dict(self._body)


class _Jobs:
    def __init__(self):
        self.requests = []
        self.runs = []

    async def create_request(self, **kwargs):
        self.requests.append(kwargs)
        return JobRequest(
            request_id="request-1",
            idempotency_key=kwargs["idempotency_key"],
            kind=kwargs["kind"],
            scope=kwargs["scope"],
            payload=kwargs["payload"],
            created_at=1.0,
        )

    async def schedule_run(self, **kwargs):
        self.runs.append(kwargs)
        return JobRun(
            run_id="run-1",
            request_id=kwargs["request_id"],
            schedule_slot=kwargs["schedule_slot"],
            cursor_generation=kwargs["cursor_generation"],
            status="pending",
            cursor=kwargs["cursor"],
            attempt=0,
            lease_owner=None,
            lease_until=None,
            progress={},
            result=None,
            error_code=None,
            error_message=None,
            created_at=1.0,
            updated_at=1.0,
        )


@pytest.mark.asyncio
async def test_index_rebuild_route_requires_preflight_and_confirmation(monkeypatch):
    jobs = _Jobs()
    monkeypatch.setattr(blackbox, "get_container", lambda: SimpleNamespace(durable_jobs=jobs))
    monkeypatch.setattr(blackbox, "request", _Request({"kind": "memory_index"}))
    monkeypatch.setattr(blackbox, "jsonify", lambda value: value)

    payload, status = await blackbox.rebuild_indexes_action()

    assert status == 409
    assert payload["error"] == "maintenance_confirmation_required"
    assert jobs.requests == []


@pytest.mark.asyncio
async def test_index_rebuild_route_only_schedules_durable_job(monkeypatch):
    jobs = _Jobs()
    monkeypatch.setattr(blackbox, "get_container", lambda: SimpleNamespace(durable_jobs=jobs))
    monkeypatch.setattr(
        blackbox,
        "request",
        _Request({
            "kind": "memory_index",
            "preflight_token": "snapshot-abc",
            "confirmation": "rebuild",
            "schedule_slot": "slot-1",
        }),
    )
    monkeypatch.setattr(blackbox, "jsonify", lambda value: value)

    payload, status = await blackbox.rebuild_indexes_action()

    assert status == 202
    assert payload == {
        "ok": True,
        "accepted": True,
        "request_id": "request-1",
        "job_id": "run-1",
        "status": "pending",
        "repair_kind": "memory_index",
    }
    assert jobs.requests[0]["kind"] == "maintenance.memory_index.rebuild"
    assert jobs.runs[0]["schedule_slot"] == "slot-1"


@pytest.mark.asyncio
async def test_tag_audit_trigger_only_schedules_durable_job(monkeypatch):
    jobs = _Jobs()
    container = SimpleNamespace(
        durable_jobs=jobs,
        plugin_config={"tag_llm_provider_id": "provider-1"},
    )
    request_stub = SimpleNamespace(
        args={
            "strategy": "mixed",
            "total_count": "120",
            "schedule_slot": "audit-slot-1",
        }
    )
    monkeypatch.setattr(tags, "get_container", lambda: container)
    monkeypatch.setattr(tags, "request", request_stub)
    monkeypatch.setattr(tags, "jsonify", lambda value: value)

    payload, status = await tags.trigger_audit()

    assert status == 202
    assert payload["job_id"] == "run-1"
    assert jobs.requests[0]["kind"] == "maintenance.tag_audit.run"
    assert jobs.requests[0]["payload"]["strategy"] == "mixed"
    assert jobs.runs[0]["schedule_slot"] == "audit-slot-1"


@pytest.mark.asyncio
async def test_import_start_only_schedules_durable_job(monkeypatch):
    jobs = _Jobs()
    monkeypatch.setattr(
        memories,
        "get_container",
        lambda: SimpleNamespace(durable_jobs=jobs),
    )
    monkeypatch.setattr(
        memories,
        "request",
        _Request({
            "source": "livingmemory",
            "batch_size": 25,
            "schedule_slot": "import-slot-1",
        }),
    )
    monkeypatch.setattr(memories, "jsonify", lambda value: value)

    payload, status = await memories.import_start()

    assert status == 202
    assert payload["job_id"] == "run-1"
    assert jobs.requests[0]["kind"] == "maintenance.import.run"
    assert jobs.requests[0]["payload"]["mode"] == "legacy"
    assert jobs.runs[0]["schedule_slot"] == "import-slot-1"


@pytest.mark.asyncio
async def test_audit_rejection_uses_coordinator_and_approval_fails_closed():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE tag_audit_suggestions(
               id INTEGER PRIMARY KEY, status TEXT, resolved_at REAL
           )"""
    )
    connection.executemany(
        "INSERT INTO tag_audit_suggestions(id, status) VALUES (?, 'pending')",
        [(1,), (2,)],
    )
    actors = []

    class _Coordinator:
        async def transaction(self, callback, *, actor):
            actors.append(actor)
            result = callback(connection)
            connection.commit()
            return result

    container = SimpleNamespace(
        write_gateway=SimpleNamespace(coordinator=_Coordinator())
    )

    rejected = await tags._resolve_audit_suggestion(container, 1, "reject")
    blocked = await tags._resolve_audit_suggestion(container, 2, "approve")

    assert rejected["status"] == "rejected"
    assert connection.execute(
        "SELECT status FROM tag_audit_suggestions WHERE id=1"
    ).fetchone()[0] == "rejected"
    assert blocked["error"] == "scope_migration_required"
    assert connection.execute(
        "SELECT status FROM tag_audit_suggestions WHERE id=2"
    ).fetchone()[0] == "pending"
    assert actors == ["webui.tag_audit.resolve", "webui.tag_audit.resolve"]


def test_pair_similarity_projection_builder_is_pure_and_filters_noise():
    rows = [
        (1, np.asarray([1.0, 0.0], dtype=np.float32).tobytes()),
        (2, np.asarray([0.8, 0.2], dtype=np.float32).tobytes()),
        (3, np.asarray([-1.0, 0.0], dtype=np.float32).tobytes()),
    ]

    params, cache = compute_pair_similarity_projection(rows)

    assert len(params) == 1
    assert params[0][:2] == (1, 2)
    assert cache[(1, 2)] == pytest.approx(params[0][2])
    assert (1, 3) not in cache
    assert (2, 3) not in cache
