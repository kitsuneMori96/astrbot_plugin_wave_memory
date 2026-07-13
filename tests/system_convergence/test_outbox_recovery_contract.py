"""R4 executable future contracts through the frozen production test composition port."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

import pytest

from tests.system_convergence.contracts import (
    contract_assert,
    contract_fail,
    load_outbox_test_port,
    reason_code,
    require_module,
)


class _ManualClock:
    def __init__(self, current: float = 100.0):
        self.current = current

    def now(self) -> float:
        return self.current

    def advance_to(self, timestamp: float) -> None:
        self.current = float(timestamp)


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    if isinstance(value, concurrent.futures.Future):
        return await asyncio.wrap_future(value)
    return value


async def _call(value: Any, reason: str, label: str) -> Any:
    try:
        return await _resolve(value)
    except Exception as exc:
        contract_fail(reason, f"{label} raised {type(exc).__name__}: {exc}")


@asynccontextmanager
async def _open_runtime(binding, database_path, *, consumers, clock, reason: str):
    manager = binding.create_runtime(
        str(database_path),
        consumers=consumers,
        clock=clock,
    )
    if callable(getattr(manager, "__aenter__", None)):
        runtime = await _call(manager.__aenter__(), reason, "runtime.__aenter__")
        try:
            yield runtime
        except BaseException as exc:
            await _resolve(manager.__aexit__(type(exc), exc, exc.__traceback__))
            raise
        else:
            await _call(manager.__aexit__(None, None, None), reason, "runtime.__aexit__")
        return
    contract_assert(
        callable(getattr(manager, "__enter__", None))
        and callable(getattr(manager, "__exit__", None)),
        reason,
        "invalid_contract: create_runtime must return a context manager",
    )
    runtime = manager.__enter__()
    try:
        yield runtime
    except BaseException as exc:
        manager.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        manager.__exit__(None, None, None)


def _readonly_uri(database_uri: Any) -> str:
    raw = str(database_uri)
    if raw.startswith("file:"):
        separator = "&" if "?" in raw else "?"
        return raw if "mode=ro" in raw else f"{raw}{separator}mode=ro"
    return f"{Path(raw).resolve().as_uri()}?mode=ro"


def _read_rows(
    database_uri: Any, sql: str, params: tuple[Any, ...], reason: str
) -> list[dict[str, Any]]:
    connection = None
    try:
        connection = sqlite3.connect(_readonly_uri(database_uri), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        contract_assert(
            connection.execute("PRAGMA query_only").fetchone()[0] == 1,
            reason,
            "R4 observer is not query_only",
        )
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    except sqlite3.Error as exc:
        contract_fail(reason, f"read-only observation failed: {type(exc).__name__}: {exc}")
    finally:
        if connection is not None:
            connection.close()


def _probe_rows(runtime, aggregate_id: str, reason: str) -> list[dict[str, Any]]:
    return _read_rows(
        runtime.database_uri,
        "SELECT aggregate_id, aggregate_version, value "
        "FROM system_convergence_probe_entities WHERE aggregate_id=?",
        (aggregate_id,),
        reason,
    )


def _operation_rows(runtime, operation_id: str, reason: str) -> list[dict[str, Any]]:
    return _read_rows(
        runtime.database_uri,
        "SELECT operation_id, status FROM write_operations WHERE operation_id=?",
        (operation_id,),
        reason,
    )


def _event_rows(runtime, operation_id: str, reason: str) -> list[dict[str, Any]]:
    return _read_rows(
        runtime.database_uri,
        "SELECT event_id, operation_id, aggregate_id, aggregate_version "
        "FROM domain_outbox WHERE operation_id=?",
        (operation_id,),
        reason,
    )


def _delivery_rows(runtime, operation_id: str, reason: str) -> list[dict[str, Any]]:
    return _read_rows(
        runtime.database_uri,
        "SELECT d.consumer_name, d.state, d.attempt, d.available_at, d.lease_owner, "
        "d.lease_until, d.last_error, d.processed_at "
        "FROM outbox_deliveries d JOIN domain_outbox o ON o.event_id=d.event_id "
        "WHERE o.operation_id=? ORDER BY d.consumer_name",
        (operation_id,),
        reason,
    )


def _projection_rows(runtime, aggregate_id: str, reason: str) -> list[dict[str, Any]]:
    return _read_rows(
        runtime.database_uri,
        "SELECT consumer_name, aggregate_kind, aggregate_id, applied_version, generation, "
        "checkpoint_json, updated_at FROM derived_projection_state "
        "WHERE aggregate_kind=? AND aggregate_id=?",
        ("system_convergence_probe", aggregate_id),
        reason,
    )


def _load_capabilities(reason: str):
    binding = load_outbox_test_port(reason)
    commands = require_module(
        "domain.commands", ("DomainCommand", "DomainWriteResult", "EntityChange"), reason
    )
    coordinator_module = require_module(
        "engine.write_coordinator", ("WriteCoordinator",), reason
    )
    return binding, commands, coordinator_module.WriteCoordinator


def _make_command(
    binding,
    commands,
    scope,
    reason: str,
    *,
    operation_id: str,
    idempotency_key: str,
    aggregate_id: str,
    aggregate_version: int,
    value: str,
):
    command = binding.make_probe_command(
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        actor="system-convergence-test",
        scope=scope,
        value=value,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        request_hash=f"sha256:{idempotency_key}",
    )
    contract_assert(
        isinstance(command, commands.DomainCommand),
        reason,
        "make_probe_command did not return the real DomainCommand",
    )
    return command


async def _submit(runtime, coordinator_type, commands, command, reason: str):
    contract_assert(
        isinstance(runtime.coordinator, coordinator_type),
        reason,
        "RuntimePort.coordinator is not the real WriteCoordinator",
    )
    result = await _call(runtime.coordinator.submit(command), reason, "coordinator.submit")
    contract_assert(
        isinstance(result, commands.DomainWriteResult),
        reason,
        "submit did not return DomainWriteResult",
    )
    contract_assert(
        all(isinstance(entity, commands.EntityChange) for entity in result.entities),
        reason,
        "DomainWriteResult.entities contains a non-EntityChange",
    )
    return result


async def _drain(runtime, reason: str) -> None:
    watermark = await _call(runtime.committed_watermark(), reason, "committed_watermark")
    await _call(runtime.drain_to_watermark(watermark), reason, "drain_to_watermark")


async def _advance_and_drain(runtime, reason: str) -> None:
    await _call(
        runtime.advance_clock_to_next_attempt(), reason, "advance_clock_to_next_attempt"
    )
    await _drain(runtime, reason)


def _assert_single_committed_change(runtime, operation_id: str, aggregate_id: str, reason: str):
    probes = _probe_rows(runtime, aggregate_id, reason)
    operations = _operation_rows(runtime, operation_id, reason)
    events = _event_rows(runtime, operation_id, reason)
    contract_assert(len(probes) == 1, reason, f"probe rows={probes!r}")
    contract_assert(
        len(operations) == 1 and operations[0]["status"] == "committed",
        reason,
        f"operation rows={operations!r}",
    )
    contract_assert(len(events) == 1, reason, f"outbox events={events!r}")


@pytest.mark.asyncio

async def test_committed_probe_replays_failed_consumer_after_new_runtime_without_duplicate_domain(
    tmp_path, scope_world_factory
):
    reason = "R4_POST_COMMIT_REPLAY"
    binding, commands, coordinator_type = _load_capabilities(reason)
    scope = scope_world_factory(reason).runtime_alpha_bot_private
    path = tmp_path / "post-commit-replay.sqlite"
    clock = _ManualClock()
    failed_calls: list[Any] = []

    def fail_consumer(event):
        failed_calls.append(event)
        raise RuntimeError("injected derived consumer failure")

    async with _open_runtime(
        binding, path, consumers={"derived": fail_consumer}, clock=clock, reason=reason
    ) as runtime:
        command = _make_command(
            binding,
            commands,
            scope,
            reason,
            operation_id="op-replay",
            idempotency_key="key-replay",
            aggregate_id="replay-A",
            aggregate_version=1,
            value="canonical",
        )
        await _submit(runtime, coordinator_type, commands, command, reason)
        await _drain(runtime, reason)
        _assert_single_committed_change(runtime, "op-replay", "replay-A", reason)
        delivery = _delivery_rows(runtime, "op-replay", reason)
        contract_assert(
            len(delivery) == 1
            and delivery[0]["processed_at"] is None
            and delivery[0]["attempt"] >= 1
            and delivery[0]["last_error"],
            reason,
            f"failed delivery was not durable/retryable: {delivery!r}",
        )

    replay_calls: list[Any] = []
    async with _open_runtime(
        binding,
        path,
        consumers={"derived": replay_calls.append},
        clock=clock,
        reason=reason,
    ) as reopened:
        await _advance_and_drain(reopened, reason)
        _assert_single_committed_change(reopened, "op-replay", "replay-A", reason)
        delivery = _delivery_rows(reopened, "op-replay", reason)
        contract_assert(
            len(delivery) == 1 and delivery[0]["processed_at"] is not None,
            reason,
            f"reopened runtime did not process retry: {delivery!r}",
        )
    contract_assert(
        len(failed_calls) == 1 and len(replay_calls) == 1,
        reason,
        f"consumer calls fail/replay={len(failed_calls)}/{len(replay_calls)}",
    )


@pytest.mark.asyncio

async def test_duplicate_command_is_idempotent_and_consumers_settle_independently(
    tmp_path, scope_world_factory
):
    reason = "R4_PER_CONSUMER_IDEMPOTENCY"
    binding, commands, coordinator_type = _load_capabilities(reason)
    scope = scope_world_factory(reason).runtime_alpha_bot_private
    path = tmp_path / "per-consumer.sqlite"
    clock = _ManualClock()
    calls = {"A": 0, "B": 0}

    def consumer_a(event):
        calls["A"] += 1

    def consumer_b(event):
        calls["B"] += 1
        if calls["B"] == 1:
            raise RuntimeError("injected B first-attempt failure")

    async with _open_runtime(
        binding,
        path,
        consumers={"A": consumer_a, "B": consumer_b},
        clock=clock,
        reason=reason,
    ) as runtime:
        command = _make_command(
            binding,
            commands,
            scope,
            reason,
            operation_id="op-dedup",
            idempotency_key="key-dedup",
            aggregate_id="dedup-A",
            aggregate_version=1,
            value="canonical",
        )
        first = await _submit(runtime, coordinator_type, commands, command, reason)
        await _drain(runtime, reason)
        duplicate = await _submit(runtime, coordinator_type, commands, command, reason)
        await _advance_and_drain(runtime, reason)
        contract_assert(
            first.operation_id == duplicate.operation_id == "op-dedup",
            reason,
            "duplicate command did not return existing operation result",
        )
        _assert_single_committed_change(runtime, "op-dedup", "dedup-A", reason)
        deliveries = _delivery_rows(runtime, "op-dedup", reason)
        contract_assert(
            len(deliveries) == 2
            and all(row["processed_at"] is not None for row in deliveries),
            reason,
            f"per-consumer delivery did not settle independently: {deliveries!r}",
        )
    contract_assert(calls == {"A": 1, "B": 2}, reason, f"consumer calls={calls!r}")


@pytest.mark.asyncio

async def test_delayed_stale_event_cannot_overwrite_newer_per_aggregate_checkpoint(
    tmp_path, scope_world_factory
):
    reason = "R4_AGGREGATE_CHECKPOINT_ORDERING"
    binding, commands, coordinator_type = _load_capabilities(reason)
    scope = scope_world_factory(reason).runtime_alpha_bot_private
    path = tmp_path / "checkpoint-ordering.sqlite"
    clock = _ManualClock()
    successful_calls: list[tuple[str, int]] = []
    a1_attempts = 0

    def projection(event):
        nonlocal a1_attempts
        identity = (str(event.aggregate_id), int(event.aggregate_version))
        if identity == ("A", 1):
            a1_attempts += 1
            raise RuntimeError("injected delayed A1")
        successful_calls.append(identity)

    async with _open_runtime(
        binding,
        path,
        consumers={"projection": projection},
        clock=clock,
        reason=reason,
    ) as runtime:
        specs = (
            ("op-A1", "key-A1", "A", 1, "A1"),
            ("op-B1", "key-B1", "B", 1, "B1"),
            ("op-A2", "key-A2", "A", 2, "A2"),
        )
        for operation_id, key, aggregate_id, version, value in specs:
            command = _make_command(
                binding,
                commands,
                scope,
                reason,
                operation_id=operation_id,
                idempotency_key=key,
                aggregate_id=aggregate_id,
                aggregate_version=version,
                value=value,
            )
            await _submit(runtime, coordinator_type, commands, command, reason)
            await _drain(runtime, reason)
        await _advance_and_drain(runtime, reason)
        probe_a = _probe_rows(runtime, "A", reason)
        probe_b = _probe_rows(runtime, "B", reason)
        state_a = _projection_rows(runtime, "A", reason)
        state_b = _projection_rows(runtime, "B", reason)
        contract_assert(
            len(probe_a) == 1
            and probe_a[0]["aggregate_version"] == 2
            and len(probe_b) == 1
            and probe_b[0]["aggregate_version"] == 1,
            reason,
            f"canonical aggregate versions A/B={probe_a!r}/{probe_b!r}",
        )
        contract_assert(
            len(state_a) == 1
            and state_a[0]["applied_version"] == 2
            and len(state_b) == 1
            and state_b[0]["applied_version"] == 1,
            reason,
            f"projection checkpoints A/B={state_a!r}/{state_b!r}",
        )
    contract_assert(
        successful_calls == [("B", 1), ("A", 2)] and a1_attempts == 1,
        reason,
        f"stale delivery reached handler: calls={successful_calls!r}, A1={a1_attempts}",
    )


@pytest.mark.asyncio

async def test_shutdown_rejects_late_ingress_and_new_runtime_recovers_accepted_work(
    tmp_path, scope_world_factory
):
    reason = "R4_SHUTDOWN_LATE_INGRESS_RECOVERY"
    binding, commands, coordinator_type = _load_capabilities(reason)
    scope = scope_world_factory(reason).runtime_alpha_bot_private
    path = tmp_path / "shutdown-recovery.sqlite"
    clock = _ManualClock()
    failed_calls: list[Any] = []

    def fail_consumer(event):
        failed_calls.append(event)
        raise RuntimeError("injected pre-shutdown failure")

    async with _open_runtime(
        binding, path, consumers={"derived": fail_consumer}, clock=clock, reason=reason
    ) as runtime:
        accepted = _make_command(
            binding,
            commands,
            scope,
            reason,
            operation_id="op-accepted",
            idempotency_key="key-accepted",
            aggregate_id="accepted-A",
            aggregate_version=1,
            value="accepted",
        )
        late = _make_command(
            binding,
            commands,
            scope,
            reason,
            operation_id="op-late",
            idempotency_key="key-late",
            aggregate_id="late-A",
            aggregate_version=1,
            value="late",
        )
        await _submit(runtime, coordinator_type, commands, accepted, reason)
        await _drain(runtime, reason)
        await _call(runtime.coordinator.close_accepting(), reason, "close_accepting")
        try:
            late_result = await _resolve(runtime.coordinator.submit(late))
        except Exception as exc:
            late_code = reason_code(exc)
        else:
            late_code = reason_code(late_result)
        contract_assert(late_code == "ingress_closed", reason, f"late ingress code={late_code!r}")
        await _call(runtime.shutdown(), reason, "RuntimePort.shutdown")

    recovered_calls: list[Any] = []
    async with _open_runtime(
        binding,
        path,
        consumers={"derived": recovered_calls.append},
        clock=clock,
        reason=reason,
    ) as reopened:
        await _advance_and_drain(reopened, reason)
        _assert_single_committed_change(reopened, "op-accepted", "accepted-A", reason)
        contract_assert(
            _probe_rows(reopened, "late-A", reason) == []
            and _operation_rows(reopened, "op-late", reason) == [],
            reason,
            "late rejected ingress persisted canonical/operation state",
        )
        delivery = _delivery_rows(reopened, "op-accepted", reason)
        contract_assert(
            len(delivery) == 1 and delivery[0]["processed_at"] is not None,
            reason,
            f"accepted work did not recover: {delivery!r}",
        )
    contract_assert(
        len(failed_calls) == 1 and len(recovered_calls) == 1,
        reason,
        f"accepted consumer calls fail/recover={len(failed_calls)}/{len(recovered_calls)}",
    )
