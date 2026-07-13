import asyncio
import sqlite3
import unittest

from engine.db.learning_repository import LearningRepositories
from services.learning.job_runner import LearningJobRunner
from services.learning.source import LearningSourceAdapter, LearningSourceItem, LearningSourceRegistry
from services.learning.candidate_service import LearningCandidateService


class _Adapter(LearningSourceAdapter):
    source_type = "test"

    def __init__(self, items=None, error_at=None):
        self.items = list(items or [])
        self.error_at = error_at
        self.calls = 0

    async def collect(self, *, bot_id, source, job, cursor=None):
        self.calls += 1
        for index, item in enumerate(self.items):
            if self.error_at == index:
                raise RuntimeError("input failed")
            yield item


class LearningJobsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.repos = LearningRepositories.from_connection(self.connection)
        self.source_id = self.repos.sources.create(bot_id="bot-a", source_type="test", name="source")
        self.job_id = self.repos.jobs.create(
            bot_id="bot-a", source_id=self.source_id, candidate_type="fact", name="job"
        )
        self.registry = LearningSourceRegistry()

    def tearDown(self):
        self.connection.close()

    async def test_adapter_contract_normalizes_items_and_registry(self):
        item = LearningSourceItem(
            content="fact", evidence={"item_id": "1"}, source_fingerprint="fp-1", cursor={"offset": 1}
        )
        adapter = _Adapter([item])
        self.registry.register(adapter)
        self.assertIs(self.registry.resolve("test"), adapter)
        runner = LearningJobRunner(self.repos, self.registry)
        result = await runner.run_job(self.job_id, bot_id="bot-a")
        self.assertEqual(result.candidates_created, 1)
        candidate = self.repos.candidates.list(bot_id="bot-a")[0][0]
        self.assertEqual(candidate["evidence"], {"item_id": "1"})
        self.assertEqual(candidate["job_id"], self.job_id)
        self.assertEqual(self.repos.sources.get(self.source_id, bot_id="bot-a")["cursor"], {"offset": 1})

    async def test_disabled_job_is_skipped_without_adapter_call(self):
        self.repos.jobs.update_enabled(self.job_id, bot_id="bot-a", enabled=False)
        adapter = _Adapter([LearningSourceItem("x", {}, "x")])
        self.registry.register(adapter)
        result = await LearningJobRunner(self.repos, self.registry).run_job(self.job_id, bot_id="bot-a")
        self.assertEqual(result.status, "skipped")
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(self.repos.jobs.get(self.job_id, bot_id="bot-a")["last_run_status"], "skipped")

    async def test_concurrent_runs_are_serialized_by_lease(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class Blocking(_Adapter):
            async def collect(self, **kwargs):
                entered.set()
                await release.wait()
                yield LearningSourceItem("x", {}, "same")

        adapter = Blocking()
        self.registry.register(adapter)
        runner = LearningJobRunner(self.repos, self.registry, lease_seconds=30)
        first = asyncio.create_task(runner.run_job(self.job_id, bot_id="bot-a"))
        await entered.wait()
        second = await runner.run_job(self.job_id, bot_id="bot-a")
        self.assertEqual(second.status, "skipped")
        self.assertEqual(second.reason, "lease_unavailable")
        release.set()
        self.assertEqual((await first).status, "succeeded")

    async def test_failed_input_does_not_advance_cursor_and_recovers(self):
        class Recovering(_Adapter):
            async def collect(self, *, cursor=None, **kwargs):
                if cursor == {"offset": 1}:
                    yield LearningSourceItem("second", {}, "second", cursor={"offset": 2})
                    return
                yield LearningSourceItem("first", {}, "first", cursor={"offset": 1})
                raise RuntimeError("input failed")

        adapter = Recovering()
        self.registry.register(adapter)
        runner = LearningJobRunner(self.repos, self.registry)
        failed = await runner.run_job(self.job_id, bot_id="bot-a")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(self.repos.sources.get(self.source_id, bot_id="bot-a")["cursor"], {"offset": 1})
        recovered = await runner.run_job(self.job_id, bot_id="bot-a")
        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(self.repos.sources.get(self.source_id, bot_id="bot-a")["cursor"], {"offset": 2})

    async def test_candidate_idempotency_is_bot_scoped(self):
        service = LearningCandidateService(self.repos)
        first = service.create(
            bot_id="bot-a", candidate_type="fact", content="same", evidence={}, source_fingerprint="fp"
        )
        again = service.create(
            bot_id="bot-a", candidate_type="fact", content="changed", evidence={}, source_fingerprint="fp"
        )
        other = service.create(
            bot_id="bot-b", candidate_type="fact", content="same", evidence={}, source_fingerprint="fp"
        )
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)


if __name__ == "__main__":
    unittest.main()
