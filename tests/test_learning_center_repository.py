import sqlite3
import tempfile
import unittest
from pathlib import Path


class LearningCenterRepositoryTest(unittest.TestCase):
    def _database(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "learning.db"
        return path

    def test_schema_upgrades_old_database_idempotently_and_preserves_review_candidates(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE review_candidates (id INTEGER PRIMARY KEY, candidate_type TEXT, promoted INTEGER DEFAULT 0)"
        )
        conn.execute("INSERT INTO review_candidates (id, candidate_type, promoted) VALUES (1, 'fact', 0)")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        self.assertTrue(run_migration(str(path)))

        conn = sqlite3.connect(path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(
            {"learning_sources", "learning_jobs", "learning_candidates", "learning_promotions"}.issubset(tables)
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM review_candidates").fetchone()[0], 1)
        candidate_columns = {row[1] for row in conn.execute("PRAGMA table_info(learning_candidates)")}
        self.assertNotIn("promoted", candidate_columns)
        index_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("uq_learning_candidate_fingerprint", index_names)
        self.assertIn("uq_learning_promotion_idempotency", index_names)
        conn.close()

    def test_learning_modules_use_package_safe_engine_imports(self):
        learning_dir = Path(__file__).parents[1] / "services" / "learning"
        for module_path in learning_dir.glob("*.py"):
            source = module_path.read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"(?m)^from engine\\.",
                f"{module_path.name} must resolve engine through the plugin package first",
            )

    def test_schema_migration_preserves_preexisting_unrelated_foreign_key_orphans(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute(
            """CREATE TABLE tag_extraction_status (
                memory_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                FOREIGN KEY(memory_id) REFERENCES memories(id)
            )"""
        )
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("INSERT INTO tag_extraction_status (memory_id, status) VALUES (18, 'legacy')")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))

        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT memory_id, status FROM tag_extraction_status").fetchall(), [(18, "legacy")])
        self.assertTrue(
            {"learning_sources", "learning_jobs", "learning_candidates", "learning_promotions"}.issubset(
                {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            )
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchone()[0], "tag_extraction_status")

    def test_schema_adds_missing_columns_and_indexes_to_partial_tables(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE learning_sources (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.execute("CREATE TABLE learning_jobs (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.execute("CREATE TABLE learning_candidates (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.execute("CREATE TABLE learning_promotions (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        self.assertTrue(run_migration(str(path)))

        conn = sqlite3.connect(path)
        self.assertTrue(
            {"source_type", "name", "enabled", "config_json", "updated_at"}.issubset(
                {row[1] for row in conn.execute("PRAGMA table_info(learning_sources)")}
            )
        )
        self.assertTrue(
            {"candidate_type", "source_fingerprint", "review_status", "evidence_json"}.issubset(
                {row[1] for row in conn.execute("PRAGMA table_info(learning_candidates)")}
            )
        )
        self.assertTrue(
            {"candidate_id", "target_kind", "idempotency_key", "promotion_status"}.issubset(
                {row[1] for row in conn.execute("PRAGMA table_info(learning_promotions)")}
            )
        )
        conn.close()

    def test_partial_schema_rebuild_matches_fresh_constraints_and_rejects_invalid_writes(self):
        from engine.db.migrations.learning_center import run_migration

        fresh_path = self._database()
        upgraded_path = self._database()
        self.assertTrue(run_migration(str(fresh_path)))

        conn = sqlite3.connect(upgraded_path)
        conn.execute("CREATE TABLE learning_sources (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.execute("CREATE TABLE learning_jobs (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.execute("CREATE TABLE learning_candidates (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.execute("CREATE TABLE learning_promotions (id INTEGER PRIMARY KEY, bot_id TEXT)")
        conn.execute("INSERT INTO learning_sources (id, bot_id) VALUES (7, 'stable-bot')")
        conn.commit()
        conn.close()
        self.assertTrue(run_migration(str(upgraded_path)))

        fresh = sqlite3.connect(fresh_path)
        upgraded = sqlite3.connect(upgraded_path)
        self.addCleanup(fresh.close)
        self.addCleanup(upgraded.close)
        upgraded.execute("PRAGMA foreign_keys=ON")
        for table in ("learning_sources", "learning_jobs", "learning_candidates", "learning_promotions"):
            fresh_columns = [(r[1], r[2], r[3], r[4], r[5]) for r in fresh.execute(f"PRAGMA table_info({table})")]
            upgraded_columns = [(r[1], r[2], r[3], r[4], r[5]) for r in upgraded.execute(f"PRAGMA table_info({table})")]
            self.assertEqual(upgraded_columns, fresh_columns, table)
            self.assertEqual(
                list(upgraded.execute(f"PRAGMA foreign_key_list({table})")),
                list(fresh.execute(f"PRAGMA foreign_key_list({table})")),
                table,
            )
            fresh_indexes = {(r[1], r[2], r[4]) for r in fresh.execute(f"PRAGMA index_list({table})")}
            upgraded_indexes = {(r[1], r[2], r[4]) for r in upgraded.execute(f"PRAGMA index_list({table})")}
            self.assertEqual(upgraded_indexes, fresh_indexes, table)

        self.assertEqual(upgraded.execute("SELECT id, bot_id FROM learning_sources").fetchall(), [(7, "stable-bot")])
        with self.assertRaises(sqlite3.IntegrityError):
            upgraded.execute(
                """INSERT INTO learning_candidates
                   (bot_id, candidate_type, content, evidence_json, source_fingerprint,
                    review_status, metadata_json, created_at, updated_at)
                   VALUES ('stable-bot', 'fact', 'bad', '{}', 'bad-status', 'invalid', '{}', 1, 1)"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            upgraded.execute(
                """INSERT INTO learning_jobs
                   (bot_id, source_id, candidate_type, name, enabled, schedule_json, policy_json,
                    last_run_status, created_at, updated_at)
                   VALUES ('stable-bot', 999, 'fact', 'orphan', 1, '{}', '{}', 'never', 1, 1)"""
            )

    def test_rebuild_preserves_all_mappable_rows_across_four_tables(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO learning_sources VALUES (1, 'bot-a', 'agent', 'source', 1, '{}', NULL, 1, 1)")
        conn.execute("INSERT INTO learning_jobs VALUES (2, 'bot-a', 1, 'fact', 'job', 1, '{}', '{}', 'never', NULL, NULL, NULL, NULL, NULL, 2, 2)")
        conn.execute("INSERT INTO learning_candidates VALUES (3, 'bot-a', 1, 2, 'fact', 'content', '{}', 'reason', 'fp', 'approved', 'reviewer', 3, 'ok', NULL, NULL, '{}', 3, 3)")
        conn.execute("INSERT INTO learning_promotions VALUES (4, 3, 'bot-a', 'fact', 'idem', 'succeeded', 1, 'target', NULL, NULL, 'admin', 4, 4, '{}', 4, 4)")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in ("learning_promotions", "learning_candidates", "learning_jobs", "learning_sources"):
            conn.execute(f"CREATE TABLE __raw_{table} AS SELECT * FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE __raw_{table} RENAME TO {table}")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT id, bot_id, name FROM learning_sources").fetchall(), [(1, "bot-a", "source")])
        self.assertEqual(conn.execute("SELECT id, bot_id, source_id FROM learning_jobs").fetchall(), [(2, "bot-a", 1)])
        self.assertEqual(conn.execute("SELECT id, source_id, job_id, review_status FROM learning_candidates").fetchall(), [(3, 1, 2, "approved")])
        self.assertEqual(conn.execute("SELECT id, candidate_id, promotion_status FROM learning_promotions").fetchall(), [(4, 3, "succeeded")])

    def test_unmappable_rows_fail_migration_and_leave_old_schema_and_rows_unchanged(self):
        from engine.db.migrations.learning_center import run_migration

        for scenario in ("missing_job_source", "cross_bot_job", "missing_promotion_candidate"):
            with self.subTest(scenario=scenario):
                path = self._database()
                self.assertTrue(run_migration(str(path)))
                conn = sqlite3.connect(path)
                conn.execute("INSERT INTO learning_sources VALUES (1, 'bot-a', 'agent', 'source', 1, '{}', NULL, 1, 1)")
                if scenario == "missing_job_source":
                    conn.execute("DROP TABLE learning_jobs")
                    conn.execute("CREATE TABLE learning_jobs (id INTEGER PRIMARY KEY, bot_id TEXT)")
                    conn.execute("INSERT INTO learning_jobs VALUES (9, 'bot-a')")
                    target = "learning_jobs"
                elif scenario == "cross_bot_job":
                    conn.execute("INSERT INTO learning_jobs VALUES (9, 'bot-b', 1, 'fact', 'bad', 1, '{}', '{}', 'never', NULL, NULL, NULL, NULL, NULL, 1, 1)")
                    target = "learning_jobs"
                else:
                    conn.execute("DROP TABLE learning_promotions")
                    conn.execute("CREATE TABLE learning_promotions (id INTEGER PRIMARY KEY, bot_id TEXT)")
                    conn.execute("INSERT INTO learning_promotions VALUES (9, 'bot-a')")
                    target = "learning_promotions"
                conn.commit()
                before_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (target,)).fetchone()[0]
                before_rows = conn.execute(f"SELECT * FROM {target}").fetchall()
                conn.close()

                self.assertFalse(run_migration(str(path)), scenario)
                conn = sqlite3.connect(path)
                self.assertEqual(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (target,)).fetchone()[0], before_sql)
                self.assertEqual(conn.execute(f"SELECT * FROM {target}").fetchall(), before_rows)
                conn.close()

    def test_migration_repairs_existing_managed_indexes_with_wrong_definitions(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        conn.execute("DROP INDEX idx_learning_jobs_source")
        conn.execute("CREATE UNIQUE INDEX idx_learning_jobs_source ON learning_jobs(name)")
        conn.execute("DROP INDEX uq_learning_candidate_fingerprint")
        conn.execute("CREATE INDEX uq_learning_candidate_fingerprint ON learning_candidates(content)")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual([r[2] for r in conn.execute("PRAGMA index_info(idx_learning_jobs_source)")], ["bot_id", "source_id"])
        job_index = next(r for r in conn.execute("PRAGMA index_list(learning_jobs)") if r[1] == "idx_learning_jobs_source")
        self.assertEqual((job_index[2], job_index[4]), (0, 0))
        self.assertEqual([r[2] for r in conn.execute("PRAGMA index_info(uq_learning_candidate_fingerprint)")], ["bot_id", "candidate_type", "source_fingerprint"])
        candidate_index = next(r for r in conn.execute("PRAGMA index_list(learning_candidates)") if r[1] == "uq_learning_candidate_fingerprint")
        self.assertEqual((candidate_index[2], candidate_index[4]), (1, 1))

    def test_migration_repairs_incomplete_or_extra_status_check_sets(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=OFF")
        candidate_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='learning_candidates'").fetchone()[0]
        candidate_sql = candidate_sql.replace("CREATE TABLE learning_candidates", "CREATE TABLE bad_candidates", 1)
        candidate_sql = candidate_sql.replace("'delegated'))", "'delegated','bogus'))")
        conn.execute(candidate_sql)
        conn.execute("DROP TABLE learning_candidates")
        conn.execute("ALTER TABLE bad_candidates RENAME TO learning_candidates")
        promotion_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='learning_promotions'").fetchone()[0]
        promotion_sql = promotion_sql.replace("CREATE TABLE learning_promotions", "CREATE TABLE bad_promotions", 1)
        promotion_sql = promotion_sql.replace(",'waiting_dedicated_review'", "")
        conn.execute(promotion_sql)
        conn.execute("DROP TABLE learning_promotions")
        conn.execute("ALTER TABLE bad_promotions RENAME TO learning_promotions")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO learning_candidates (bot_id,candidate_type,content,evidence_json,source_fingerprint,review_status,metadata_json,created_at,updated_at) VALUES ('bot-a','fact','x','{}','bad','bogus','{}',1,1)")
        conn.execute("INSERT INTO learning_candidates (id,bot_id,candidate_type,content,evidence_json,source_fingerprint,review_status,metadata_json,created_at,updated_at) VALUES (1,'bot-a','fact','x','{}','ok','delegated','{}',1,1)")
        conn.execute("INSERT INTO learning_promotions (candidate_id,bot_id,target_kind,idempotency_key,promotion_status,metadata_json,created_at,updated_at) VALUES (1,'bot-a','fact','wait','waiting_dedicated_review','{}',1,1)")

    def test_migration_rejects_invalid_bot_ids_in_every_table_without_changes(self):
        from engine.db.migrations.learning_center import run_migration

        invalid_by_table = {
            "learning_sources": "   ",
            "learning_jobs": "123456789",
            "learning_candidates": None,
            "learning_promotions": "",
        }
        for target, invalid_bot_id in invalid_by_table.items():
            with self.subTest(table=target):
                path = self._database()
                self.assertTrue(run_migration(str(path)))
                conn = sqlite3.connect(path)
                conn.execute("INSERT INTO learning_sources VALUES (1, 'bot-a', 'agent', 'source', 1, '{}', NULL, 1, 1)")
                if target == "learning_candidates" or target == "learning_promotions":
                    conn.execute("INSERT INTO learning_candidates VALUES (3, 'bot-a', 1, NULL, 'fact', 'ok', '{}', '', 'fp', 'pending', NULL, NULL, NULL, NULL, NULL, '{}', 1, 1)")
                conn.execute(f"CREATE TABLE __raw AS SELECT * FROM {target}")
                conn.execute(f"DROP TABLE {target}")
                conn.execute(f"ALTER TABLE __raw RENAME TO {target}")
                if target == "learning_sources":
                    conn.execute("INSERT INTO learning_sources VALUES (9, ?, 'agent', 'bad', 1, '{}', NULL, 1, 1)", (invalid_bot_id,))
                elif target == "learning_jobs":
                    conn.execute("INSERT INTO learning_jobs VALUES (9, ?, 1, 'fact', 'bad', 1, '{}', '{}', 'never', NULL, NULL, NULL, NULL, NULL, 1, 1)", (invalid_bot_id,))
                elif target == "learning_candidates":
                    conn.execute("INSERT INTO learning_candidates VALUES (9, ?, NULL, NULL, 'fact', 'bad', '{}', '', 'bad', 'pending', NULL, NULL, NULL, NULL, NULL, '{}', 1, 1)", (invalid_bot_id,))
                else:
                    conn.execute("INSERT INTO learning_promotions VALUES (9, 3, ?, 'fact', 'bad', 'queued', 0, NULL, NULL, NULL, NULL, NULL, NULL, '{}', 1, 1)", (invalid_bot_id,))
                conn.commit()
                before_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (target,)).fetchone()[0]
                before_rows = conn.execute(f"SELECT * FROM {target} ORDER BY id").fetchall()
                conn.close()

                self.assertFalse(run_migration(str(path)), target)
                conn = sqlite3.connect(path)
                self.assertEqual(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (target,)).fetchone()[0], before_sql)
                self.assertEqual(conn.execute(f"SELECT * FROM {target} ORDER BY id").fetchall(), before_rows)
                conn.close()

    def test_migration_repairs_noncanonical_foreign_key_actions(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys=OFF")
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='learning_jobs'").fetchone()[0]
        sql = sql.replace("CREATE TABLE learning_jobs", "CREATE TABLE bad_jobs", 1)
        sql = sql.replace(
            "FOREIGN KEY(source_id) REFERENCES learning_sources(id)",
            "FOREIGN KEY(source_id) REFERENCES learning_sources(id) ON UPDATE CASCADE ON DELETE CASCADE MATCH FULL",
        )
        conn.execute(sql)
        conn.execute("DROP TABLE learning_jobs")
        conn.execute("ALTER TABLE bad_jobs RENAME TO learning_jobs")
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        fk = conn.execute("PRAGMA foreign_key_list(learning_jobs)").fetchone()
        self.assertEqual(
            (fk[2], fk[3], fk[4], fk[5], fk[6], fk[7]),
            ("learning_sources", "source_id", "id", "NO ACTION", "NO ACTION", "NONE"),
        )

    def test_migration_repairs_index_direction_collation_and_key_columns(self):
        from engine.db.migrations.learning_center import run_migration

        path = self._database()
        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        conn.execute("DROP INDEX idx_learning_candidates_bot_review_time")
        conn.execute(
            """CREATE INDEX idx_learning_candidates_bot_review_time
               ON learning_candidates(bot_id COLLATE NOCASE, review_status DESC, created_at ASC)"""
        )
        conn.commit()
        conn.close()

        self.assertTrue(run_migration(str(path)))
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        key_rows = [row for row in conn.execute("PRAGMA index_xinfo(idx_learning_candidates_bot_review_time)") if row[5] == 1]
        self.assertEqual(
            [(row[2], row[3], row[4], row[5]) for row in key_rows],
            [
                ("bot_id", 0, "BINARY", 1),
                ("review_status", 0, "BINARY", 1),
                ("created_at", 1, "BINARY", 1),
            ],
        )

    def test_connection_manager_enables_foreign_keys_initially_and_after_reopen(self):
        from engine.db.connection import ConnectionManager

        path = self._database()
        manager = ConnectionManager(str(path))
        self.addCleanup(manager.close)
        self.assertEqual(manager.execute_read("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(manager._write_conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        manager.reopen()
        self.assertEqual(manager.execute_read("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(manager._write_conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_repositories_isolate_every_list_and_lookup_by_bot(self):
        from engine.db.learning_repository import LearningRepositories

        path = self._database()
        repos = LearningRepositories.open(str(path), now=lambda: 100.0)
        self.addCleanup(repos.close)

        source_a = repos.sources.create(bot_id="bot-a", source_type="agent", name="shared")
        source_b = repos.sources.create(bot_id="bot-b", source_type="agent", name="shared")
        job_a = repos.jobs.create(
            bot_id="bot-a", source_id=source_a, candidate_type="fact", name="job-a"
        )
        job_b = repos.jobs.create(
            bot_id="bot-b", source_id=source_b, candidate_type="fact", name="job-b"
        )
        candidate_a = repos.candidates.create(
            bot_id="bot-a",
            source_id=source_a,
            candidate_type="fact",
            content="same",
            evidence={"message_id": "1"},
            source_fingerprint="same-fingerprint",
        )
        candidate_b = repos.candidates.create(
            bot_id="bot-b",
            source_id=source_b,
            job_id=job_b,
            candidate_type="fact",
            content="same",
            evidence={"message_id": "1"},
            source_fingerprint="same-fingerprint",
        )
        repos.promotions.create(
            bot_id="bot-a",
            candidate_id=candidate_a,
            target_kind="fact",
            idempotency_key="candidate-a:fact:v1",
        )

        for repository in (repos.sources, repos.jobs, repos.candidates, repos.promotions):
            items, total = repository.list(bot_id="bot-a", limit=10, offset=0)
            self.assertEqual(total, 1)
            self.assertEqual({item["bot_id"] for item in items}, {"bot-a"})
        self.assertIsNone(repos.sources.get(source_a, bot_id="bot-b"))
        with self.assertRaisesRegex(ValueError, "source_id does not belong"):
            repos.jobs.create(bot_id="bot-a", source_id=source_b, candidate_type="fact", name="cross-source")
        with self.assertRaisesRegex(ValueError, "source_id does not belong"):
            repos.candidates.create(
                bot_id="bot-a", source_id=source_b, candidate_type="fact", content="cross",
                evidence={}, source_fingerprint="cross-source",
            )
        with self.assertRaisesRegex(ValueError, "job_id does not belong"):
            repos.candidates.create(
                bot_id="bot-a", source_id=source_a, job_id=job_b, candidate_type="fact", content="cross",
                evidence={}, source_fingerprint="cross-job",
            )
        with self.assertRaisesRegex(ValueError, "candidate_id does not belong"):
            repos.promotions.create(
                bot_id="bot-a", candidate_id=candidate_b, target_kind="fact", idempotency_key="cross-candidate"
            )
        with self.assertRaisesRegex(ValueError, "idempotency_key is unavailable"):
            repos.promotions.create(
                bot_id="bot-b", candidate_id=candidate_b, target_kind="fact",
                idempotency_key="candidate-a:fact:v1",
            )
        with self.assertRaises((TypeError, ValueError)):
            repos.candidates.list(bot_id="", limit=10, offset=0)

    def test_enums_validation_json_tolerance_pagination_and_idempotency(self):
        from engine.db.learning_repository import LearningRepositories
        from engine.db.learning_types import CandidateType, PromotionStatus, ReviewStatus, TargetKind

        path = self._database()
        repos = LearningRepositories.open(str(path), now=lambda: 200.0)
        self.addCleanup(repos.close)
        source_id = repos.sources.create(
            bot_id="stable-db-id", source_type="agent", name="source", config={"schema_version": 1}
        )
        candidate_id = repos.candidates.create(
            bot_id="stable-db-id",
            source_id=source_id,
            candidate_type=CandidateType.FACT,
            content="fact candidate",
            evidence={"memory_ids": [1]},
            source_fingerprint="fp-1",
            review_status=ReviewStatus.PENDING,
        )
        duplicate_id = repos.candidates.create(
            bot_id="stable-db-id",
            source_id=source_id,
            candidate_type="fact",
            content="duplicate",
            evidence={},
            source_fingerprint="fp-1",
        )
        promotion_id = repos.promotions.create(
            bot_id="stable-db-id",
            candidate_id=candidate_id,
            target_kind=TargetKind.FACT,
            idempotency_key="promotion-key",
            promotion_status=PromotionStatus.QUEUED,
        )
        self.assertEqual(candidate_id, duplicate_id)
        self.assertEqual(
            promotion_id,
            repos.promotions.create(
                bot_id="stable-db-id",
                candidate_id=candidate_id,
                target_kind="fact",
                idempotency_key="promotion-key",
            ),
        )

        repos.connection.execute("UPDATE learning_sources SET config_json = '{bad json' WHERE id = ?", (source_id,))
        repos.connection.execute(
            "UPDATE learning_candidates SET evidence_json = '[wrong-shape]', metadata_json = '{bad' WHERE id = ?",
            (candidate_id,),
        )
        repos.connection.commit()
        self.assertEqual(repos.sources.get(source_id, bot_id="stable-db-id")["config"], {})
        candidate = repos.candidates.get(candidate_id, bot_id="stable-db-id")
        self.assertEqual(candidate["evidence"], {})
        self.assertEqual(candidate["metadata"], {})

        items, total = repos.candidates.list(bot_id="stable-db-id", limit=1, offset=0)
        self.assertEqual(len(items), 1)
        self.assertEqual(total, 1)
        with self.assertRaises(ValueError):
            repos.candidates.create(
                bot_id="stable-db-id",
                source_id=source_id,
                candidate_type="unknown",
                content="bad",
                evidence={},
                source_fingerprint="fp-bad",
            )


if __name__ == "__main__":
    unittest.main()
