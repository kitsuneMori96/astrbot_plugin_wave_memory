import sqlite3
import tempfile
import unittest
from pathlib import Path


class InjectionMetricStoreTest(unittest.TestCase):
    def _store(self):
        from engine.metrics_store import InjectionMetricStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "metrics.db"
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        store = InjectionMetricStore(conn)
        store.ensure_schema()
        return store

    def test_query_groups_samples_into_expected_buckets(self):
        store = self._store()
        base = 1_700_000_000
        store.record({"total_tokens": 100, "memories_tokens": 60, "jargon_tokens": 10, "total_ms": 200}, ts=base)
        store.record({"total_tokens": 200, "memories_tokens": 80, "belief_tokens": 40, "total_ms": 400}, ts=base + 1800)
        store.record({"total_tokens": 300, "persona_tokens": 90, "total_ms": 600}, ts=base + 7200)

        result = store.query(base, base + 10_800, bucket_seconds=3600)

        self.assertEqual(result["count"], 3)
        self.assertEqual(len(result["series"]), 2)
        self.assertEqual(result["series"][0]["total_tokens"], 300)
        self.assertEqual(result["series"][0]["memories_tokens"], 140)
        self.assertEqual(result["series"][1]["total_tokens"], 300)
        self.assertEqual(result["summary"]["total_tokens"]["sum"], 600)
        self.assertEqual(result["summary"]["total_tokens"]["avg"], 200)

    def test_ranking_sorts_channels_by_token_sum_and_ratio(self):
        store = self._store()
        base = 1_700_000_000
        store.record({"total_tokens": 100, "memories_tokens": 70, "jargon_tokens": 30}, ts=base)
        store.record({"total_tokens": 100, "memories_tokens": 20, "belief_tokens": 80}, ts=base + 60)

        result = store.query(base, base + 3600, bucket_seconds=3600)

        ranking = result["ranking"]
        self.assertEqual(ranking[0]["key"], "memories_tokens")
        self.assertEqual(ranking[0]["sum"], 90)
        self.assertAlmostEqual(ranking[0]["ratio"], 0.45)
        self.assertEqual(ranking[1]["key"], "belief_tokens")
        self.assertEqual(ranking[2]["key"], "jargon_tokens")

    def test_cleanup_removes_rows_older_than_retention(self):
        store = self._store()
        now = 1_700_000_000
        store.record({"total_tokens": 1}, ts=now - 40 * 86400)
        store.record({"total_tokens": 2}, ts=now - 2 * 86400)

        deleted = store.cleanup(now=now, retention_seconds=31 * 86400)
        result = store.query(now - 60 * 86400, now, bucket_seconds=86400)

        self.assertEqual(deleted, 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["summary"]["total_tokens"]["sum"], 2)


if __name__ == "__main__":
    unittest.main()
