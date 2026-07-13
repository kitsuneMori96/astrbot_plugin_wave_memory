from __future__ import annotations

import asyncio

import pytest

from services.task_supervisor import TaskSupervisor, TaskSupervisorError


@pytest.mark.asyncio
async def test_supervisor_tracks_owner_lifecycle_and_rejects_duplicate_names():
    supervisor = TaskSupervisor()
    release = asyncio.Event()

    async def worker():
        await release.wait()
        return "done"

    task = supervisor.start("tag-backfill:run-1", worker(), owner="tag-worker")
    await asyncio.sleep(0)
    snapshot = supervisor.health_snapshot()
    assert snapshot["accepting"] is True
    assert snapshot["running_count"] == 1
    assert snapshot["tasks"]["tag-backfill:run-1"]["owner"] == "tag-worker"
    assert snapshot["tasks"]["tag-backfill:run-1"]["started_at"] is not None

    with pytest.raises(RuntimeError, match="already registered"):
        supervisor.start("tag-backfill:run-1", worker(), owner="tag-worker")

    release.set()
    assert await task == "done"
    await supervisor.settle()
    completed = supervisor.task_snapshot("tag-backfill:run-1")
    assert completed is not None
    assert completed["state"] == "succeeded"
    assert completed["ended_at"] is not None
    assert completed["last_error"] is None


@pytest.mark.asyncio
async def test_supervisor_records_and_raises_task_failures(caplog):
    supervisor = TaskSupervisor()

    async def fail():
        raise ValueError("injected failure")

    supervisor.start("auditor:run-1", fail(), owner="auditor")
    with pytest.raises(TaskSupervisorError) as error:
        await supervisor.settle()

    assert isinstance(error.value.failures["auditor:run-1"], ValueError)
    failed = supervisor.health_snapshot()
    assert failed["healthy"] is False
    assert failed["failed_count"] == 1
    assert failed["tasks"]["auditor:run-1"]["last_error"] == "ValueError: injected failure"
    assert "supervised task auditor:run-1" in caplog.text

    # Failure reporting is explicit but shutdown remains idempotent after it was observed.
    await supervisor.shutdown(cancel=False)


@pytest.mark.asyncio
async def test_supervisor_close_accepting_cancel_and_shutdown_are_deterministic():
    supervisor = TaskSupervisor()
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def worker():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    supervisor.start("import:run-1", worker(), owner="importer")
    await started.wait()
    await supervisor.close_accepting()
    with pytest.raises(RuntimeError, match="not accepting"):
        supervisor.start("late", asyncio.sleep(0), owner="scheduler")

    assert await supervisor.cancel(owner="importer") == 1
    assert cleaned_up.is_set()
    assert supervisor.task_snapshot("import:run-1")["state"] == "cancelled"

    await supervisor.shutdown()
    health = supervisor.health_snapshot()
    assert health["accepting"] is False
    assert health["shutdown"] is True
    assert health["running_count"] == 0
