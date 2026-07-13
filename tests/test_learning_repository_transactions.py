import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock


class LearningRepositoryTransactionTest(unittest.TestCase):
    def _path(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name) / "transactions.db"

    def test_connection_manager_write_transaction_cannot_interleave_or_cross_commit(self):
        from engine.db.connection import ConnectionManager

        manager = ConnectionManager(str(self._path()))
        self.addCleanup(manager.close)
        manager.execute_write("CREATE TABLE tx_items (value TEXT)")
        manager.commit()

        rendezvous = threading.Barrier(2)
        second_attempted = threading.Event()
        order = []
        errors = []

        class RollbackFirst(Exception):
            pass

        def first_writer():
            try:
                with manager.write_transaction() as tx:
                    tx.execute("INSERT INTO tx_items VALUES ('first')")
                    rendezvous.wait()
                    if not second_attempted.wait(timeout=5):
                        raise AssertionError("second writer did not attempt transaction")
                    self.assertEqual(tx.execute("SELECT value FROM tx_items").fetchall(), [("first",)])
                    raise RollbackFirst()
            except RollbackFirst:
                order.append("first_rolled_back")
            except BaseException as exc:
                errors.append(exc)
                try:
                    rendezvous.abort()
                except Exception:
                    pass

        def second_writer():
            try:
                rendezvous.wait()
                second_attempted.set()
                with manager.write_transaction() as tx:
                    order.append("second_acquired")
                    tx.execute("INSERT INTO tx_items VALUES ('second')")
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=first_writer, daemon=True)
        second = threading.Thread(target=second_writer, daemon=True)
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(order, ["first_rolled_back", "second_acquired"])
        self.assertEqual(manager.execute_read("SELECT value FROM tx_items").fetchall(), [("second",)])
        self.assertFalse(manager.in_transaction)

    def test_idempotent_creates_and_domain_errors_always_end_transactions(self):
        from engine.db.learning_repository import (
            LearningIdempotencyConflict,
            LearningRepositories,
            LearningRepositoryIntegrityError,
        )

        path = self._path()
        repos = LearningRepositories.open(str(path), now=lambda: 10.0)
        self.addCleanup(repos.close)
        source_a = repos.sources.create(bot_id="bot-a", source_type="agent", name="source")
        self.assertEqual(source_a, repos.sources.create(bot_id="bot-a", source_type="agent", name="source"))
        candidate_a = repos.candidates.create(
            bot_id="bot-a", source_id=source_a, candidate_type="fact", content="one",
            evidence={}, source_fingerprint="fingerprint",
        )
        self.assertEqual(candidate_a, repos.candidates.create(
            bot_id="bot-a", source_id=source_a, candidate_type="fact", content="duplicate",
            evidence={}, source_fingerprint="fingerprint",
        ))
        promotion_a = repos.promotions.create(
            bot_id="bot-a", candidate_id=candidate_a, target_kind="fact", idempotency_key="global-key"
        )
        self.assertEqual(promotion_a, repos.promotions.create(
            bot_id="bot-a", candidate_id=candidate_a, target_kind="fact", idempotency_key="global-key"
        ))

        source_b = repos.sources.create(bot_id="bot-b", source_type="agent", name="source")
        candidate_b = repos.candidates.create(
            bot_id="bot-b", source_id=source_b, candidate_type="fact", content="two",
            evidence={}, source_fingerprint="fingerprint",
        )
        with self.assertRaises(LearningIdempotencyConflict) as conflict:
            repos.promotions.create(
                bot_id="bot-b", candidate_id=candidate_b, target_kind="fact", idempotency_key="global-key"
            )
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        self.assertFalse(repos.connection.in_transaction)

        repos.connection.execute(
            """CREATE TRIGGER reject_source BEFORE INSERT ON learning_sources
               WHEN NEW.name='trigger-failure' BEGIN SELECT RAISE(ABORT, 'custom trigger failure'); END"""
        )
        repos.connection.commit()
        with self.assertRaises(LearningRepositoryIntegrityError) as unknown:
            repos.sources.create(bot_id="bot-a", source_type="agent", name="trigger-failure")
        self.assertEqual(unknown.exception.code, "integrity_error")
        self.assertIsInstance(unknown.exception.__cause__, sqlite3.IntegrityError)
        self.assertFalse(repos.connection.in_transaction)

        second = sqlite3.connect(path, timeout=0.1)
        self.addCleanup(second.close)
        second.execute("BEGIN IMMEDIATE")
        second.execute("INSERT INTO learning_sources (bot_id,source_type,name,enabled,config_json,created_at,updated_at) VALUES ('bot-c','agent','writer',1,'{}',1,1)")
        second.commit()

    def test_native_transaction_commit_failure_rolls_back_and_releases_writer(self):
        from engine.db.learning_repository import LearningSourceRepository
        from engine.db.migrations.learning_center import ensure_learning_schema

        path = self._path()
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        ensure_learning_schema(connection)
        marker = RuntimeError("injected commit failure")

        class CommitFailConnection:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            @property
            def in_transaction(self):
                return self.wrapped.in_transaction

            def execute(self, *args, **kwargs):
                return self.wrapped.execute(*args, **kwargs)

            def commit(self):
                raise marker

            def rollback(self):
                return self.wrapped.rollback()

        repository = LearningSourceRepository(CommitFailConnection(connection), now=lambda: 1.0)
        with self.assertRaises(RuntimeError) as raised:
            repository.create(bot_id="bot-a", source_type="agent", name="commit-fails")
        self.assertIs(raised.exception, marker)
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM learning_sources").fetchone()[0], 0)

        second = sqlite3.connect(path, timeout=0.1)
        self.addCleanup(second.close)
        second.execute("BEGIN IMMEDIATE")
        second.execute(
            """INSERT INTO learning_sources
               (bot_id,source_type,name,enabled,config_json,created_at,updated_at)
               VALUES ('bot-b','agent','second-writer',1,'{}',1,1)"""
        )
        second.commit()

    def test_integrity_errors_have_stable_domain_codes_and_preserve_causes(self):
        from engine.db.learning_repository import (
            LearningRepositoryIntegrityError,
            _integrity_error,
        )

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """CREATE TABLE parents (id INTEGER PRIMARY KEY);
               CREATE TABLE constrained (
                   id INTEGER PRIMARY KEY,
                   parent_id INTEGER NOT NULL REFERENCES parents(id),
                   value INTEGER NOT NULL UNIQUE CHECK(value > 0)
               );
               INSERT INTO parents VALUES (1);
               INSERT INTO constrained VALUES (1, 1, 1);"""
        )
        connection.commit()
        cases = (
            ("duplicate", "INSERT INTO constrained VALUES (2, 1, 1)"),
            ("foreign_key", "INSERT INTO constrained VALUES (2, 999, 2)"),
            ("check_constraint", "INSERT INTO constrained VALUES (2, 1, -1)"),
            ("not_null", "INSERT INTO constrained VALUES (2, 1, NULL)"),
        )
        for expected_code, sql in cases:
            with self.subTest(code=expected_code):
                try:
                    connection.execute(sql)
                except sqlite3.IntegrityError as exc:
                    with self.assertRaises(LearningRepositoryIntegrityError) as raised:
                        _integrity_error(exc, "test operation")
                else:
                    self.fail("fixture did not raise sqlite3.IntegrityError")
                self.assertEqual(raised.exception.code, expected_code)
                self.assertIsInstance(raised.exception.__cause__, sqlite3.IntegrityError)
                connection.rollback()

    def test_repository_errors_are_exported_from_public_db_package(self):
        from engine.db import (
            LearningIdempotencyConflict,
            LearningRepositoryError,
            LearningRepositoryIntegrityError,
        )

        self.assertTrue(issubclass(LearningIdempotencyConflict, LearningRepositoryError))
        self.assertTrue(issubclass(LearningRepositoryIntegrityError, LearningRepositoryError))

    def test_source_update_none_keeps_existing_values_for_backward_compatibility(self):
        from engine.db.learning_repository import LearningRepositories

        repos = LearningRepositories.open(str(self._path()), now=lambda: 5.0)
        self.addCleanup(repos.close)
        source_id = repos.sources.create(
            bot_id="bot-a",
            source_type="agent",
            name="source",
            enabled=False,
            config={"key": "value"},
            cursor={"page": 3},
        )
        updated = repos.sources.update(
            source_id,
            bot_id="bot-a",
            enabled=None,
            config=None,
            cursor=None,
        )
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["config"], {"key": "value"})
        self.assertEqual(updated["cursor"], {"page": 3})

    def test_proxy_migration_failure_rolls_back_and_restores_connection_state(self):
        from engine.db.connection import ConnectionManager
        from engine.db.migrations import learning_center

        path = self._path()
        manager = ConnectionManager(str(path))
        self.addCleanup(manager.close)
        learning_center.ensure_learning_schema(manager.conn)
        manager.execute_write("DROP TABLE learning_jobs")
        manager.execute_write("CREATE TABLE learning_jobs (id INTEGER PRIMARY KEY, bot_id TEXT)")
        manager.commit()
        original_sql = manager.execute_read(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_jobs'"
        ).fetchone()[0]
        manager._write_conn.execute("PRAGMA foreign_keys=OFF")

        marker = RuntimeError("injected index failure")
        with mock.patch.object(learning_center, "_create_indexes", side_effect=marker):
            with self.assertRaises(RuntimeError) as raised:
                learning_center.ensure_learning_schema(manager.conn)
        self.assertIs(raised.exception, marker)
        self.assertFalse(manager.in_transaction)
        self.assertEqual(manager._write_conn.execute("PRAGMA foreign_keys").fetchone()[0], 0)
        self.assertEqual(
            manager.execute_read("SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_jobs'").fetchone()[0],
            original_sql,
        )
        self.assertEqual(
            manager.execute_read("SELECT name FROM sqlite_master WHERE name LIKE '__%_canonical'").fetchall(),
            [],
        )
        second = sqlite3.connect(path, timeout=0.1)
        self.addCleanup(second.close)
        second.execute("BEGIN IMMEDIATE")
        second.execute("CREATE TABLE second_writer (id INTEGER)")
        second.commit()

    def test_ensure_schema_rejects_callers_active_transaction_without_committing_it(self):
        from engine.db.migrations.learning_center import ensure_learning_schema

        path = self._path()
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE caller_owned (value TEXT)")
        connection.commit()
        connection.execute("BEGIN")
        connection.execute("INSERT INTO caller_owned VALUES ('pending')")

        with self.assertRaisesRegex(RuntimeError, "active transaction"):
            ensure_learning_schema(connection)
        self.assertTrue(connection.in_transaction)
        self.assertEqual(connection.execute("SELECT * FROM caller_owned").fetchall(), [("pending",)])
        connection.rollback()
        self.assertEqual(connection.execute("SELECT * FROM caller_owned").fetchall(), [])

    def test_learning_repositories_open_closes_created_connection_when_schema_fails(self):
        from engine.db import learning_repository

        created = []
        real_connect = sqlite3.connect

        def tracking_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            created.append(connection)
            return connection

        with mock.patch.object(learning_repository.sqlite3, "connect", side_effect=tracking_connect):
            with mock.patch.object(
                learning_repository, "ensure_learning_schema", side_effect=RuntimeError("migration failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "migration failed"):
                    learning_repository.LearningRepositories.open(str(self._path()))
        self.assertEqual(len(created), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            created[0].execute("SELECT 1")

    def test_wave_memory_db_closes_owned_manager_when_learning_initialization_fails(self):
        api_module = types.ModuleType("astrbot.api")
        api_module.logger = mock.MagicMock()
        astrbot_module = types.ModuleType("astrbot")
        astrbot_module.api = api_module
        with mock.patch.dict(sys.modules, {"astrbot": astrbot_module, "astrbot.api": api_module}):
            from engine import database

        created = []
        real_manager = database.ConnectionManager

        def tracking_manager(*args, **kwargs):
            manager = real_manager(*args, **kwargs)
            created.append(manager)
            return manager

        with mock.patch.object(database, "ConnectionManager", side_effect=tracking_manager):
            with mock.patch.object(
                database.LearningRepositories,
                "from_connection",
                side_effect=RuntimeError("learning initialization failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "learning initialization failed"):
                    database.WaveMemoryDB(str(self._path()))
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_completed_schema_uses_lightweight_fast_path_without_full_table_scans(self):
        from engine.db.migrations.learning_center import ensure_learning_schema

        connection = sqlite3.connect(self._path())
        self.addCleanup(connection.close)
        ensure_learning_schema(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        ensure_learning_schema(connection)
        connection.set_trace_callback(None)

        expensive_tokens = ("COUNT(", " JOIN ", " GROUP BY ")
        traced = "\n".join(statement.upper() for statement in statements)
        self.assertFalse(any(token in traced for token in expensive_tokens), traced)

    def test_normal_repository_writes_preserve_schema_ledger_fast_path(self):
        from engine.db.learning_repository import LearningRepositories
        from engine.db.migrations.learning_center import ensure_learning_schema

        connection = sqlite3.connect(self._path())
        self.addCleanup(connection.close)
        repos = LearningRepositories.from_connection(connection, now=lambda: 1.0)
        repos.sources.create(bot_id="bot-a", source_type="agent", name="normal-write")
        ledger = connection.execute(
            """SELECT version, schema_cookie FROM learning_schema_ledger
               WHERE component='general_learning_center'"""
        ).fetchone()
        self.assertIsNotNone(ledger)

        statements = []
        connection.set_trace_callback(statements.append)
        ensure_learning_schema(connection)
        connection.set_trace_callback(None)
        expensive_tokens = ("COUNT(", " JOIN ", " GROUP BY ")
        traced = "\n".join(statement.upper() for statement in statements)
        self.assertFalse(any(token in traced for token in expensive_tokens), traced)


if __name__ == "__main__":
    unittest.main()
