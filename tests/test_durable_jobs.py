from __future__ import annotations

import sqlite3

import pytest

from engine.db.job_repo import (
    JobLeaseLostError,
    JobRepository,
    JobRequestConflictError,
)
from services.durable_jobs import DurableJobService


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
