"""R3 contracts for transaction ownership, rollback hygiene, and mutation ID affinity."""

from __future__ import annotations

import asyncio
import multiprocessing
import sqlite3
import threading
from unittest import mock

import pytest

from engine.db.connection import ConnectionManager
from tests.system_convergence.contracts import contract_assert, reason_code
from webui.container import ServiceContainer, get_container


class _RollbackProbe(Exception):
    pass


def _record(target: list[str], lock: threading.Lock, value: str) -> None:
    with lock:
        target.append(value)


def _owner_probe_process(db_path: str, independent_loops: bool, sender) -> None:
    """Spawn-safe child target; all possibly stuck resources die with this process."""
    manager = None
    payload = {
        "b_attempted": False,
        "commit_entered": False,
        "finished_fast": False,
        "outcomes": [],
        "errors": [],
        "rows": [],
        "workers_alive": False,
    }
    try:
        manager = ConnectionManager(db_path)
        with manager.write_transaction() as tx:
            tx.execute("CREATE TABLE writes (id INTEGER PRIMARY KEY, owner TEXT UNIQUE)")

        a_ready = threading.Event()
        b_attempted = threading.Event()
        commit_entered = threading.Event()
        b_finished = threading.Event()
        record_lock = threading.Lock()
        proxy_type = type(manager.conn)
        original_execute = proxy_type.execute
        original_commit = proxy_type.commit

        def observed_execute(proxy, sql, params=None):
            if threading.current_thread().name.endswith("-B") and "VALUES ('B')" in sql:
                b_attempted.set()
            return original_execute(proxy, sql, params)

        def observed_commit(proxy):
            if threading.current_thread().name.endswith("-B"):
                commit_entered.set()
            return original_commit(proxy)

        def a_body() -> None:
            try:
                with manager.write_transaction() as tx:
                    tx.execute("INSERT INTO writes(owner) VALUES ('A')")
                    a_ready.set()
                    payload["b_attempted"] = b_attempted.wait(timeout=10.0)
                    if payload["b_attempted"]:
                        # Scheduling is already observed. This watchdog now measures only typed fail-fast completion.
                        payload["finished_fast"] = b_finished.wait(timeout=4.0)
                    raise _RollbackProbe()
            except _RollbackProbe:
                pass
            except Exception as exc:
                _record(payload["errors"], record_lock, f"A:{type(exc).__name__}:{exc}")
                a_ready.set()

        def b_body() -> None:
            try:
                if not a_ready.wait(timeout=10.0):
                    _record(payload["errors"], record_lock, "B:a_ready_watchdog")
                    return
                try:
                    manager.conn.execute("INSERT INTO writes(owner) VALUES ('B')")
                    manager.conn.commit()
                except Exception as exc:
                    code = reason_code(exc)
                    _record(payload["outcomes"], record_lock, f"b_rejected:{code or type(exc).__name__}")
                else:
                    _record(payload["outcomes"], record_lock, "b_raw_write_committed")
            finally:
                payload["commit_entered"] = commit_entered.is_set()
                b_finished.set()

        def loop_runner(body, label: str) -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def invoke() -> None:
                body()

            try:
                loop.run_until_complete(invoke())
            except Exception as exc:
                _record(payload["errors"], record_lock, f"{label}:{type(exc).__name__}:{exc}")
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                asyncio.set_event_loop(None)
                loop.close()

        a_target = (lambda: loop_runner(a_body, "A-loop")) if independent_loops else a_body
        b_target = (lambda: loop_runner(b_body, "B-loop")) if independent_loops else b_body
        label = "loop" if independent_loops else "thread"
        first = threading.Thread(target=a_target, name=f"owner-{label}-A")
        second = threading.Thread(target=b_target, name=f"owner-{label}-B")
        with mock.patch.object(proxy_type, "execute", observed_execute), mock.patch.object(
            proxy_type, "commit", observed_commit
        ):
            first.start()
            second.start()
            first.join(timeout=15.0)
            second.join(timeout=15.0)
        payload["workers_alive"] = first.is_alive() or second.is_alive()
        payload["b_attempted"] = b_attempted.is_set()
        payload["commit_entered"] = commit_entered.is_set()
        if not payload["workers_alive"]:
            payload["rows"] = manager.execute_read("SELECT owner FROM writes ORDER BY id").fetchall()
        sender.send(payload)
    except Exception as exc:
        try:
            payload["errors"].append(f"child:{type(exc).__name__}:{exc}")
            sender.send(payload)
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        try:
            if manager is not None:
                manager.close()
        finally:
            sender.close()


def _spawn_owner_probe(tmp_path, reason: str, *, independent_loops: bool) -> dict:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_owner_probe_process,
        args=(str(tmp_path / f"{reason}.sqlite"), independent_loops, sender),
        name=f"{reason}-probe",
    )
    payload = None
    started = False
    reaped = False
    exitcode = None
    try:
        process.start()
        started = True
        sender.close()
        if receiver.poll(35.0):
            payload = receiver.recv()
    finally:
        if started:
            process.join(timeout=10.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=10.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=10.0)
            reaped = not process.is_alive()
            exitcode = process.exitcode
        receiver.close()
        if not sender.closed:
            sender.close()
        if started and reaped:
            process.close()
    contract_assert(payload is not None, reason, f"probe_watchdog: child exitcode={exitcode}")
    contract_assert(reaped, reason, "probe_process_not_reaped")
    return payload


def _assert_raw_proxy_rejected(result: dict, reason: str) -> None:
    violations = []
    if result.get("workers_alive"):
        violations.append("worker_not_settled")
    if result.get("errors"):
        violations.append(f"worker_error={result['errors']!r}")
    if not result.get("b_attempted"):
        violations.append("raw_execute_entry_not_observed")
    if not result.get("finished_fast"):
        violations.append("raw write did not typed-fail-fast after observed entry")
    if "b_rejected:raw_write_forbidden" not in result.get("outcomes", []):
        violations.append(f"raw proxy rejection={result.get('outcomes')!r}")
    if result.get("commit_entered"):
        violations.append("raw commit entry was reached after execute should have been rejected")
    if result.get("rows") != []:
        violations.append(f"rejected raw write changed rows={result.get('rows')!r}")
    contract_assert(not violations, reason, "; ".join(violations))



def test_raw_proxy_write_from_second_thread_fails_fast_while_transaction_owned(tmp_path):
    reason = "R3_THREAD_RAW_WRITE_FORBIDDEN"
    _assert_raw_proxy_rejected(
        _spawn_owner_probe(tmp_path, reason, independent_loops=False),
        reason,
    )



def test_raw_proxy_write_from_independent_event_loop_fails_fast_while_transaction_owned(tmp_path):
    reason = "R3_EVENT_LOOP_RAW_WRITE_FORBIDDEN"
    _assert_raw_proxy_rejected(
        _spawn_owner_probe(tmp_path, reason, independent_loops=True),
        reason,
    )


def _manager(tmp_path, name: str) -> ConnectionManager:
    manager = ConnectionManager(str(tmp_path / name))
    with manager.write_transaction() as tx:
        tx.execute("CREATE TABLE writes (id INTEGER PRIMARY KEY, owner TEXT UNIQUE)")
    return manager


def test_foreign_key_failure_rolls_back_context_and_next_write_succeeds(tmp_path):
    reason = "R3_FK_CONTEXT_ROLLBACK_GUARD"
    manager = _manager(tmp_path, "fk-clean.sqlite")
    try:
        with manager.write_transaction() as tx:
            tx.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            tx.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))")
        try:
            with manager.write_transaction() as tx:
                tx.execute("INSERT INTO child(id, parent_id) VALUES (1, 999)")
        except sqlite3.IntegrityError:
            pass
        else:
            contract_assert(False, reason, "fixture did not trigger FK failure")
        contract_assert(not manager.in_transaction, reason, "transaction context left FK failure active")
        with manager.write_transaction() as tx:
            tx.execute("INSERT INTO parent(id) VALUES (1)")
            tx.execute("INSERT INTO child(id, parent_id) VALUES (2, 1)")
        rows = manager.execute_read("SELECT id, parent_id FROM child").fetchall()
        contract_assert(rows == [(2, 1)], reason, f"next legal transaction failed: {rows!r}")
    finally:
        manager.close()


def test_unique_failure_rolls_back_context_and_next_write_succeeds(tmp_path):
    reason = "R3_UNIQUE_CONTEXT_ROLLBACK_GUARD"
    manager = _manager(tmp_path, "unique-clean.sqlite")
    try:
        with manager.write_transaction() as tx:
            tx.execute("INSERT INTO writes(owner) VALUES ('duplicate')")
        try:
            with manager.write_transaction() as tx:
                tx.execute("INSERT INTO writes(owner) VALUES ('duplicate')")
        except sqlite3.IntegrityError:
            pass
        else:
            contract_assert(False, reason, "fixture did not trigger UNIQUE failure")
        contract_assert(not manager.in_transaction, reason, "transaction context left UNIQUE failure active")
        with manager.write_transaction() as tx:
            tx.execute("INSERT INTO writes(owner) VALUES ('next-valid')")
        rows = manager.execute_read("SELECT owner FROM writes ORDER BY id").fetchall()
        contract_assert(rows == [("duplicate",), ("next-valid",)], reason, f"next legal transaction failed: {rows!r}")
    finally:
        manager.close()


@pytest.mark.asyncio

async def test_jargon_mutation_returns_nontrivial_id_from_its_write_cursor(tmp_path):
    reason = "R3_MUTATION_ID_AFFINITY"
    path = tmp_path / "jargon-id.sqlite"
    manager = ConnectionManager(str(path))
    response = None
    payload = None
    actual = None
    try:
        with manager.write_transaction() as tx:
            tx.execute(
                """CREATE TABLE jargon (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT, meaning TEXT,
                    is_jargon INTEGER, status TEXT, frequency INTEGER, confidence REAL,
                    is_global INTEGER, group_id TEXT, contexts TEXT,
                    created_at INTEGER, updated_at INTEGER
                )"""
            )
            tx.executemany(
                "INSERT INTO jargon(word, meaning) VALUES (?, ?)",
                [(f"seed-{index}", "seed") for index in range(1, 129)],
            )
            tx.execute("DELETE FROM jargon")
        read_affinity = manager.execute_read("SELECT last_insert_rowid()").fetchone()[0]
        contract_assert(read_affinity != 129, reason, "fixture did not separate read/write last_insert_rowid")

        ServiceContainer.reset()
        container = get_container()
        container.initialize(
            db=manager,
            query_engine=None,
            embedding_service=None,
            memory_index=None,
            tag_index=None,
            cooccurrence=None,
            password="",
        )
        from webui.app import create_app

        app = create_app()
        async with app.test_app():
            client = app.test_client()
            response = await client.post(
                "/api/jargon/",
                json={"word": "synthetic-term", "meaning": "synthetic meaning", "group_id": "group-alpha"},
            )
            payload = await response.get_json()
        with sqlite3.connect(path) as reader:
            actual = reader.execute("SELECT id FROM jargon WHERE word='synthetic-term'").fetchone()
    finally:
        try:
            ServiceContainer.reset()
        finally:
            manager.close()
    contract_assert(response is not None and response.status_code == 503, reason, "legacy jargon mutation was not fail-closed")
    contract_assert(actual is None, reason, "legacy jargon mutation changed the database")
    contract_assert(
        isinstance(payload, dict) and payload.get("error", {}).get("code") == "anchored_jargon_command_unavailable",
        reason,
        f"legacy jargon mutation returned an unexpected payload: {payload!r}",
    )
