from __future__ import annotations

from types import SimpleNamespace
import sqlite3

import numpy as np
import pytest

from engine.db.job_repo import JobRequest, JobRun
from engine.write_coordinator import WriteCoordinator
from services.maintenance_tokens import maintenance_repair_token
from services.pair_similarity_projection import compute_pair_similarity_projection
from webui.blueprints import maintenance, memories, tags


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


def test_maintenance_tokens_use_uniform_watermark_generation_formula():
    """容量重建链已移除，token 不再对 hot_capacity 做 generation 特例。"""
    first = maintenance_repair_token(
        "memory_index",
        "startup_drift",
        watermark=100,
        generation=7,
    )
    assert first == "memory_index:startup_drift:100:7"

    # watermark 或 generation 任一变化都产生新 token（正常合并语义）。
    assert maintenance_repair_token(
        "memory_index",
        "startup_drift",
        watermark=101,
        generation=7,
    ) != first
    assert maintenance_repair_token(
        "memory_index",
        "startup_drift",
        watermark=100,
        generation=8,
    ) != first

    # 不同 kind/reason 之间互不冲突。
    assert maintenance_repair_token(
        "tag_index",
        "startup_drift",
        watermark=100,
        generation=7,
    ) == "tag_index:startup_drift:100:7"


@pytest.mark.asyncio
async def test_index_rebuild_route_requires_preflight_and_confirmation(monkeypatch):
    jobs = _Jobs()
    monkeypatch.setattr(maintenance, "get_container", lambda: SimpleNamespace(durable_jobs=jobs))
    monkeypatch.setattr(maintenance, "request", _Request({"kind": "memory_index"}))
    monkeypatch.setattr(maintenance, "jsonify", lambda value: value)

    payload, status = await maintenance.schedule_rebuild()

    assert status == 409
    assert payload["error"]["code"] == "maintenance_confirmation_required"
    assert jobs.requests == []


@pytest.mark.asyncio
async def test_index_rebuild_route_only_schedules_durable_job(monkeypatch):
    jobs = _Jobs()
    monkeypatch.setattr(maintenance, "get_container", lambda: SimpleNamespace(durable_jobs=jobs))
    monkeypatch.setattr(
        maintenance,
        "request",
        _Request({
            "kind": "memory_index",
            "preflight_token": "snapshot-abc",
            "confirmation": "rebuild",
            "schedule_slot": "slot-1",
        }),
    )
    monkeypatch.setattr(maintenance, "jsonify", lambda value: value)

    payload, status = await maintenance.schedule_rebuild()

    assert status == 202
    assert payload["ok"] is False
    assert payload["operation"] == {
        "id": "run-1",
        "kind": "maintenance.memory_index.rebuild",
        "status": "queued",
    }
    assert payload["request_id"] == "request-1"
    assert payload["job_id"] == "run-1"
    assert payload["item"]["status"] == "pending"
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
async def test_legacy_audit_resolution_is_read_only_and_never_enters_coordinator():
    class _Coordinator:
        async def transaction(self, callback, *, actor):
            pytest.fail("legacy Tag audit resolve must not enter a write transaction")

    container = SimpleNamespace(
        write_gateway=SimpleNamespace(coordinator=_Coordinator())
    )

    rejected = await tags._resolve_audit_suggestion(container, 1, "reject")
    approved = await tags._resolve_audit_suggestion(container, 2, "approve")

    assert rejected == {"error": "legacy_mutation_disabled"}
    assert approved == {"error": "legacy_mutation_disabled"}


def test_pair_similarity_projection_builder_is_pure_and_filters_noise():
    rows = [
        (1, np.asarray([1.0, 0.0], dtype=np.float32).tobytes()),
        (2, np.asarray([0.8, 0.2], dtype=np.float32).tobytes()),
        (3, np.asarray([-1.0, 0.0], dtype=np.float32).tobytes()),
    ]

    params, cache = compute_pair_similarity_projection(rows, top_k=4, min_similarity=0.1)

    assert len(params) == 1
    assert params[0][:2] == (1, 2)
    assert cache[(1, 2)] == pytest.approx(params[0][2])
    assert (1, 3) not in cache
    assert (2, 3) not in cache


def test_pair_similarity_projection_is_sparse_top_k_not_full_triangle():
    """Each tag keeps only top_k undirected neighbors; never n*(n-1)/2 edges."""
    # Five near-orthogonal unit vectors so many pairs fail the floor, plus one
    # cluster of three highly similar vectors that must still be sparse-capped.
    rows = [
        (1, np.asarray([1.0, 0.0, 0.0], dtype=np.float32).tobytes()),
        (2, np.asarray([0.99, 0.01, 0.0], dtype=np.float32).tobytes()),
        (3, np.asarray([0.98, 0.02, 0.0], dtype=np.float32).tobytes()),
        (4, np.asarray([0.0, 1.0, 0.0], dtype=np.float32).tobytes()),
        (5, np.asarray([0.0, 0.0, 1.0], dtype=np.float32).tobytes()),
    ]
    full_triangle = len(rows) * (len(rows) - 1) // 2
    params, cache = compute_pair_similarity_projection(
        rows,
        top_k=1,
        min_similarity=0.5,
    )
    assert len(params) == len(cache)
    assert len(params) < full_triangle
    # Only the mutually-similar cluster can produce retained edges; with top_k=1
    # each of {1,2,3} keeps its single best neighbor and undirected dedupe keeps
    # the set tiny (well under the previous O(n^2) dump).
    assert len(params) <= 3
    assert all(sim >= 0.5 for _, _, sim, _ in params)
    assert all(a < b for a, b, _, _ in params)


@pytest.mark.asyncio
async def test_write_coordinator_transaction_accepts_actor_audit_context():
    coordinator = object.__new__(WriteCoordinator)
    calls = []

    async def _dispatch(callback, transactional):
        calls.append((callback, transactional))
        return "committed"

    coordinator._dispatch = _dispatch

    result = await coordinator.transaction(lambda connection: connection, actor="maintenance.test")

    assert result == "committed"
    assert calls[0][1] is True


@pytest.mark.asyncio
async def test_write_coordinator_shutdown_is_idempotent_and_releases_lease(tmp_path):
    class Clock:
        @staticmethod
        def now():
            return 1.0

    path = str(tmp_path / "coordinator-shutdown.sqlite3")
    coordinator = WriteCoordinator(
        path,
        command_handlers={},
        consumer_names=(),
        clock=Clock(),
    )
    await coordinator.shutdown()
    await coordinator.shutdown()

    replacement = WriteCoordinator(
        path,
        command_handlers={},
        consumer_names=(),
        clock=Clock(),
    )
    await replacement.shutdown()
