from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from engine.db.job_repo import (
    JobLeaseLostError,
    JobRepository,
    JobRequestConflictError,
)
from services.durable_jobs import DurableJobRunner, DurableJobService


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    JobRepository.migrate(connection)
    JobRepository.migrate(connection)
    return connection


def _request(connection: sqlite3.Connection, key: str = "repair:index:memory"):
    return JobRepository.create_request(
        connection,
        request_id="request-1",
        idempotency_key=key,
        kind="index.rebuild",
        scope={"bot_id": "bot-alpha", "visibility": "bot_private"},
        payload={"index": "memory"},
        created_at=10.0,
    )


def test_request_idempotency_and_schedule_slot_generation_are_independent():
    connection = _connection()
    try:
        first = _request(connection)
        replay = _request(connection)
        assert replay == first
        with pytest.raises(JobRequestConflictError):
            JobRepository.create_request(
                connection,
                idempotency_key=first.idempotency_key,
                kind="index.rebuild",
                scope=first.scope,
                payload={"index": "tag"},
                created_at=11.0,
            )

        run_1 = JobRepository.schedule_run(
            connection,
            request_id=first.request_id,
            schedule_slot="2026-07-14T00:00Z",
            cursor_generation=0,
            cursor={"after_id": 0},
            created_at=12.0,
        )
        assert JobRepository.schedule_run(
            connection,
            request_id=first.request_id,
            schedule_slot="2026-07-14T00:00Z",
            cursor_generation=0,
            cursor={"after_id": 0},
            created_at=13.0,
        ) == run_1
        next_generation = JobRepository.schedule_run(
            connection,
            request_id=first.request_id,
            schedule_slot="2026-07-14T00:00Z",
            cursor_generation=1,
            cursor={"after_id": 0},
            created_at=14.0,
        )
        next_slot = JobRepository.schedule_run(
            connection,
            request_id=first.request_id,
            schedule_slot="2026-07-15T00:00Z",
            cursor_generation=0,
            cursor={"after_id": 0},
            created_at=15.0,
        )
        assert len({run_1.run_id, next_generation.run_id, next_slot.run_id}) == 3
    finally:
        connection.close()


def test_schedule_replay_ignores_mutable_checkpoint_cursor():
    connection = _connection()
    try:
        request = _request(connection)
        scheduled = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="checkpoint-replay",
            cursor_generation=0,
            cursor={"after_id": 0},
            created_at=16.0,
        )
        claimed = JobRepository.claim_run(
            connection,
            scheduled.run_id,
            now=17.0,
            lease_owner="worker-A",
            lease_seconds=30.0,
        )
        assert claimed is not None
        progressed = JobRepository.update_progress(
            connection,
            scheduled.run_id,
            lease_owner="worker-A",
            now=18.0,
            cursor={"after_id": 99},
        )

        replay = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="checkpoint-replay",
            cursor_generation=0,
            cursor={"after_id": 0},
            created_at=19.0,
        )

        assert replay.run_id == scheduled.run_id
        assert replay.cursor == progressed.cursor == {"after_id": 99}
    finally:
        connection.close()


def test_terminal_maintenance_reschedule_advances_generation_once():
    connection = _connection()
    try:
        request = _request(connection)
        first = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="startup-drift",
            cursor_generation=1,
            created_at=20.0,
            reschedule_terminal=True,
        )
        claimed = JobRepository.claim_run(
            connection,
            first.run_id,
            now=21.0,
            lease_owner="worker-A",
        )
        assert claimed is not None
        JobRepository.mark_failed(
            connection,
            first.run_id,
            lease_owner="worker-A",
            now=22.0,
            error_code="drift_remaining",
            error_message="verification still reports drift",
        )

        second = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="startup-drift",
            cursor_generation=1,
            created_at=23.0,
            reschedule_terminal=True,
        )
        replay = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="startup-drift",
            cursor_generation=1,
            created_at=24.0,
            reschedule_terminal=True,
        )

        assert second.cursor_generation == 2
        assert replay.run_id == second.run_id
        assert len(JobRepository.list_runs(connection, request_id=request.request_id)) == 2
    finally:
        connection.close()


def test_claim_progress_cursor_success_and_lease_ownership():
    connection = _connection()
    try:
        request = _request(connection)
        scheduled = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="manual:1",
            cursor_generation=0,
            created_at=20.0,
        )
        claimed = JobRepository.claim_next(
            connection,
            now=21.0,
            lease_owner="worker-A",
            lease_seconds=10.0,
        )
        assert claimed is not None
        assert claimed.run_id == scheduled.run_id
        assert claimed.status == JobRepository.RUNNING
        assert claimed.attempt == 1
        assert claimed.lease_until == 31.0

        progress = JobRepository.update_progress(
            connection,
            claimed.run_id,
            lease_owner="worker-A",
            now=22.0,
            progress={"completed": 5, "total": 10},
            cursor={"after_id": 42},
        )
        assert progress.progress == {"completed": 5, "total": 10}
        assert progress.cursor == {"after_id": 42}
        assert progress.lease_until == 52.0
        with pytest.raises(JobLeaseLostError):
            JobRepository.update_progress(
                connection,
                claimed.run_id,
                lease_owner="worker-B",
                now=22.0,
                progress={"completed": 6},
            )

        succeeded = JobRepository.mark_succeeded(
            connection,
            claimed.run_id,
            lease_owner="worker-A",
            now=23.0,
            result={"verified": True},
        )
        assert succeeded.status == JobRepository.SUCCEEDED
        assert succeeded.result == {"verified": True}
        assert succeeded.lease_owner is None
        assert JobRepository.claim_next(
            connection, now=24.0, lease_owner="worker-B"
        ) is None
    finally:
        connection.close()


def test_cancel_and_expired_lease_recovery_are_durable():
    connection = _connection()
    try:
        request = _request(connection)
        queued = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="manual:queued",
            created_at=30.0,
        )
        assert JobRepository.request_cancel(
            connection, queued.run_id, now=31.0
        ).status == JobRepository.CANCELLED

        leased = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="manual:leased",
            created_at=32.0,
        )
        first_claim = JobRepository.claim_run(
            connection,
            leased.run_id,
            now=33.0,
            lease_owner="dead-worker",
            lease_seconds=5.0,
        )
        assert first_claim is not None and first_claim.attempt == 1
        assert JobRepository.recover_expired_leases(connection, now=38.0) == 1
        recovered = JobRepository.get_run(connection, leased.run_id)
        assert recovered is not None
        assert recovered.status == JobRepository.PENDING
        assert recovered.error_code == "lease_expired"
        assert recovered.lease_owner is None

        second_claim = JobRepository.claim_run(
            connection,
            leased.run_id,
            now=39.0,
            lease_owner="replacement-worker",
            lease_seconds=5.0,
        )
        assert second_claim is not None
        assert second_claim.attempt == 2
        assert second_claim.cursor == first_claim.cursor

        cancel_requested = JobRepository.request_cancel(
            connection, leased.run_id, now=40.0
        )
        assert cancel_requested.status == JobRepository.CANCEL_REQUESTED
        assert JobRepository.cancellation_requested(connection, leased.run_id) is True
        cancelled = JobRepository.mark_cancelled(
            connection,
            leased.run_id,
            lease_owner="replacement-worker",
            now=41.0,
        )
        assert cancelled.status == JobRepository.CANCELLED
        assert cancelled.lease_owner is None
    finally:
        connection.close()


def test_graceful_worker_release_requeues_immediately():
    connection = _connection()
    try:
        request = _request(connection)
        run = JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="manual:shutdown",
            created_at=50.0,
        )
        claimed = JobRepository.claim_run(
            connection,
            run.run_id,
            now=51.0,
            lease_owner="stopping-worker",
            lease_seconds=300.0,
        )
        assert claimed is not None

        released = JobRepository.release_for_retry(
            connection,
            run.run_id,
            lease_owner="stopping-worker",
            now=52.0,
            reason="runner_shutdown",
        )

        assert released.status == JobRepository.PENDING
        assert released.lease_owner is None
        assert released.lease_until is None
        assert released.error_code == "runner_shutdown"
        replay = JobRepository.claim_run(
            connection,
            run.run_id,
            now=53.0,
            lease_owner="replacement-worker",
        )
        assert replay is not None
        assert replay.attempt == 2
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_runner_survives_individual_recovery_claim_and_mark_failures():
    class Service:
        def __init__(self):
            self.claim_calls = 0
            self.mark_calls = 0

        async def recover_expired_leases(self):
            raise RuntimeError("recovery unavailable")

        async def claim_next(self, **kwargs):
            self.claim_calls += 1
            if self.claim_calls == 1:
                raise RuntimeError("lease transaction failed")
            if self.claim_calls == 2:
                return SimpleNamespace(run_id="run-1", request_id="request-1")
            runner._running = False
            return None

        async def get_request(self, request_id):
            return SimpleNamespace(kind="repair")

        async def cancellation_requested(self, run_id):
            return False

        async def mark_succeeded(self, run_id, **kwargs):
            self.mark_calls += 1
            raise RuntimeError("terminal mark failed")

    handled = []

    async def handler(run, request, active_runner):
        handled.append(run.run_id)
        return {"ok": True}

    service = Service()
    runner = DurableJobRunner(service, {"repair": handler}, poll_interval=0.01)
    runner._running = True

    await asyncio.wait_for(runner._loop(), timeout=1.0)

    assert handled == ["run-1"]
    assert service.mark_calls == 1
    assert service.claim_calls == 3


@pytest.mark.asyncio
async def test_service_facade_uses_injected_transaction_boundary():
    connection = _connection()

    class Coordinator:
        async def transaction(self, callback):
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = callback(connection)
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise

        async def read(self, callback):
            return callback(connection)

    class Clock:
        current = 60.0

        def now(self):
            return self.current

    service = DurableJobService(Coordinator(), clock=Clock())
    try:
        request = await service.create_request(
            idempotency_key="service-request",
            kind="audit",
            scope={"bot_id": "bot-alpha"},
            payload={"mode": "verify"},
        )
        run = await service.schedule_run(
            request_id=request.request_id,
            schedule_slot="manual:service",
        )
        claimed = await service.claim_next(lease_owner="service-worker")
        assert claimed is not None and claimed.run_id == run.run_id
        succeeded = await service.mark_succeeded(
            run.run_id,
            lease_owner="service-worker",
            result={"ok": True},
        )
        assert succeeded.status == JobRepository.SUCCEEDED
        assert (await service.get_run(run.run_id)).result == {"ok": True}
    finally:
        connection.close()


def test_repository_never_commits_the_callers_transaction():
    connection = _connection()
    try:
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        request = _request(connection, key="transaction-owned")
        JobRepository.schedule_run(
            connection,
            request_id=request.request_id,
            schedule_slot="manual:rollback",
            created_at=50.0,
        )
        assert connection.in_transaction is True
        connection.rollback()
        assert connection.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM background_job_runs").fetchone()[0] == 0
    finally:
        connection.close()
