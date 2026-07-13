"""Named asyncio task supervision with observable failures and deterministic shutdown."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class _CompletedAwaitable:
    def __await__(self):
        if False:  # pragma: no cover - keeps this a generator without scheduling work
            yield None
        return None


class TaskSupervisorError(RuntimeError):
    """One or more supervised tasks failed while being settled."""

    reason_code = "supervised_task_failed"

    def __init__(self, failures: dict[str, BaseException]) -> None:
        self.failures = dict(failures)
        detail = ", ".join(
            f"{name}: {type(error).__name__}: {error}" for name, error in self.failures.items()
        )
        super().__init__(f"supervised task failure(s): {detail}")


@dataclass
class _TaskRecord:
    name: str
    owner: str
    accepted_at: float
    state: str = "pending"
    started_at: float | None = None
    ended_at: float | None = None
    last_error: str | None = None
    cancel_requested: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "state": self.state,
            "healthy": self.state not in {"failed"},
            "accepted_at": self.accepted_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "last_error": self.last_error,
            "cancel_requested": self.cancel_requested,
        }


class TaskSupervisor:
    """Own named tasks and make their lifecycle and failures explicit.

    Names are unique for the lifetime of a supervisor.  A completed task remains in
    the health snapshot, so callers must use a new name (normally a durable run id)
    for a later execution.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._records: dict[str, _TaskRecord] = {}
        self._observed_failures: set[str] = set()
        self._accepting = True
        self._shutdown = False

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def closed(self) -> bool:
        return self._shutdown

    def start(
        self,
        name: str,
        awaitable: Awaitable[Any],
        owner: str = "unspecified",
    ) -> asyncio.Task[Any]:
        """Start one uniquely named task.

        Rejected coroutine objects are explicitly closed so duplicate/late ingress
        cannot produce an unrelated "coroutine was never awaited" warning.
        """

        task_name = str(name).strip()
        task_owner = str(owner).strip()
        try:
            if not task_name:
                raise ValueError("task name must not be empty")
            if not task_owner:
                raise ValueError("task owner must not be empty")
            if not self._accepting:
                raise RuntimeError("task supervisor is not accepting new tasks")
            if task_name in self._records:
                raise RuntimeError(f"task already registered: {task_name}")
            if not inspect.isawaitable(awaitable):
                raise TypeError("awaitable must implement the await protocol")
        except BaseException:
            self._close_rejected_awaitable(awaitable)
            raise

        record = _TaskRecord(
            name=task_name,
            owner=task_owner,
            accepted_at=float(self._clock()),
        )
        self._records[task_name] = record

        async def run() -> Any:
            record.state = "running"
            record.started_at = float(self._clock())
            return await awaitable

        task = asyncio.create_task(run(), name=task_name)
        self._tasks[task_name] = task
        task.add_done_callback(lambda done, key=task_name: self._on_done(key, done))
        return task

    create_task = start

    @staticmethod
    def _close_rejected_awaitable(awaitable: Any) -> None:
        close = getattr(awaitable, "close", None)
        if inspect.iscoroutine(awaitable) and callable(close):
            close()

    def _on_done(self, name: str, task: asyncio.Task[Any]) -> None:
        record = self._records[name]
        record.ended_at = float(self._clock())
        if task.cancelled():
            record.state = "cancelled"
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            record.state = "cancelled"
            return
        if error is None:
            record.state = "succeeded"
            return
        record.state = "failed"
        record.last_error = f"{type(error).__name__}: {error}"
        logger.error(
            "supervised task %s owned by %s failed: %s",
            name,
            record.owner,
            record.last_error,
            exc_info=(type(error), error, error.__traceback__),
        )

    def health_snapshot(self) -> dict[str, Any]:
        tasks = {name: record.snapshot() for name, record in sorted(self._records.items())}
        states: dict[str, int] = {}
        for record in self._records.values():
            states[record.state] = states.get(record.state, 0) + 1
        return {
            "accepting": self._accepting,
            "shutdown": self._shutdown,
            "healthy": states.get("failed", 0) == 0,
            "task_count": len(tasks),
            "running_count": states.get("pending", 0) + states.get("running", 0),
            "failed_count": states.get("failed", 0),
            "states": states,
            "tasks": tasks,
        }

    snapshot = health_snapshot
    health = health_snapshot

    @property
    def tasks(self) -> dict[str, asyncio.Task[Any]]:
        return dict(self._tasks)

    def task_snapshot(self, name: str) -> dict[str, Any] | None:
        record = self._records.get(str(name))
        return None if record is None else record.snapshot()

    def close_accepting(self) -> Awaitable[None]:
        """Fence new work immediately; the returned token is also awaitable."""

        self._accepting = False
        return _CompletedAwaitable()

    def _select_names(
        self,
        *,
        name: str | None = None,
        owner: str | None = None,
    ) -> tuple[str, ...]:
        if name is not None:
            task_name = str(name)
            record = self._records.get(task_name)
            if record is None or (owner is not None and record.owner != str(owner)):
                return ()
            return (task_name,)
        if owner is not None:
            task_owner = str(owner)
            return tuple(key for key, record in self._records.items() if record.owner == task_owner)
        return tuple(self._records)

    async def settle(
        self,
        name: str | None = None,
        *,
        owner: str | None = None,
        timeout: float | None = None,
    ) -> None:
        """Await selected tasks and raise any failure not reported by a prior settle."""

        names = self._select_names(name=name, owner=owner)
        tasks = [self._tasks[key] for key in names if key in self._tasks]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                pending_names = sorted(task.get_name() for task in pending)
                raise TimeoutError(f"supervised tasks did not settle: {pending_names}")
            # The done callback normally ran already, but explicitly retrieve results
            # before inspecting records so no exception can remain unobserved.
            for task in done:
                if not task.cancelled():
                    task.exception()

        failures: dict[str, BaseException] = {}
        for task_name in names:
            if task_name in self._observed_failures:
                continue
            task = self._tasks.get(task_name)
            if task is None or not task.done() or task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                failures[task_name] = error
                self._observed_failures.add(task_name)
        if failures:
            first = next(iter(failures.values()))
            raise TaskSupervisorError(failures) from first

    async def cancel(
        self,
        name: str | None = None,
        *,
        owner: str | None = None,
        timeout: float | None = None,
    ) -> int:
        """Cancel and await selected unfinished tasks, returning the cancel count."""

        names = self._select_names(name=name, owner=owner)
        cancelled = 0
        for task_name in names:
            task = self._tasks.get(task_name)
            if task is None or task.done():
                continue
            self._records[task_name].cancel_requested = True
            task.cancel()
            cancelled += 1
        await self.settle(name=name, owner=owner, timeout=timeout)
        return cancelled

    async def shutdown(
        self,
        *,
        cancel: bool = True,
        timeout: float | None = None,
    ) -> None:
        """Fence new tasks, then cancel-or-settle all accepted work."""

        await self.close_accepting()
        try:
            if cancel:
                await self.cancel(timeout=timeout)
            else:
                await self.settle(timeout=timeout)
        finally:
            self._shutdown = True


__all__ = ["TaskSupervisor", "TaskSupervisorError"]
