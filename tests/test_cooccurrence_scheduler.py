import asyncio
import threading
import unittest
from unittest.mock import patch

from engine.directed_cooccurrence import CooccurrenceScheduler


class _LiveMatrix:
    def __init__(self):
        self.db = object()
        self.pair_sim_service = None
        self.residual_map = {}
        self.semantic_gain_config = None
        self.forward = {}
        self.backward = {}
        self._tag_count = 10

    @property
    def node_count(self):
        return self._tag_count


class _RebuiltMatrix:
    rebuild_calls = 0
    rebuild_thread_ids = []

    def __init__(self, db, **kwargs):
        del db, kwargs
        self.forward = {1: {2: 1.0}}
        self.backward = {2: {1: 1.0}}
        self._tag_count = 12

    def rebuild(self):
        type(self).rebuild_calls += 1
        type(self).rebuild_thread_ids.append(threading.get_ident())


class CooccurrenceSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _RebuiltMatrix.rebuild_calls = 0
        _RebuiltMatrix.rebuild_thread_ids = []

    async def test_burst_of_tag_changes_schedules_one_background_rebuild(self):
        live = _LiveMatrix()
        scheduler = CooccurrenceScheduler(live, threshold_pct=0.1, cooldown_sec=0)

        with patch("engine.directed_cooccurrence.DirectedCooccurrence", _RebuiltMatrix):
            for _ in range(20):
                scheduler.notify_tag_change()
            task = scheduler._scheduled_task
            self.assertIsNotNone(task)
            await task

        self.assertEqual(_RebuiltMatrix.rebuild_calls, 1)
        self.assertFalse(scheduler._is_rebuilding)
        self.assertEqual(live.node_count, 12)

    async def test_rebuild_runs_outside_event_loop_thread(self):
        live = _LiveMatrix()
        scheduler = CooccurrenceScheduler(live, threshold_pct=0.1, cooldown_sec=0)
        event_loop_thread = threading.get_ident()

        with patch("engine.directed_cooccurrence.DirectedCooccurrence", _RebuiltMatrix):
            scheduler.notify_tag_change()
            await scheduler._scheduled_task

        self.assertEqual(_RebuiltMatrix.rebuild_calls, 1)
        self.assertNotEqual(_RebuiltMatrix.rebuild_thread_ids[0], event_loop_thread)


if __name__ == "__main__":
    unittest.main()
