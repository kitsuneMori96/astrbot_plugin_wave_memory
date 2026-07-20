import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from engine.db.outbox_repo import OutboxEvent
from engine.directed_cooccurrence import CooccurrenceScheduler
from services.derived_projections import CooccurrenceProjection


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
    active_rebuilds = 0
    max_active_rebuilds = 0
    _state_lock = threading.Lock()

    def __init__(self, db, **kwargs):
        del db, kwargs
        self.forward = {1: {2: 1.0}}
        self.backward = {2: {1: 1.0}}
        self._tag_count = 12

    def rebuild(self):
        with type(self)._state_lock:
            type(self).rebuild_calls += 1
            type(self).rebuild_thread_ids.append(threading.get_ident())
            type(self).active_rebuilds += 1
            type(self).max_active_rebuilds = max(
                type(self).max_active_rebuilds,
                type(self).active_rebuilds,
            )
        try:
            time.sleep(0.01)
        finally:
            with type(self)._state_lock:
                type(self).active_rebuilds -= 1


class _RecordingScheduler:
    def __init__(self):
        self.calls = []
        self.rebuild_lock = None

    def set_rebuild_lock(self, rebuild_lock):
        self.rebuild_lock = rebuild_lock

    def notify_tag_change(self, count=1, *, reason="tag_change"):
        self.calls.append((count, reason))


def _event(event_type: str) -> OutboxEvent:
    return OutboxEvent(
        event_id=f"event-{event_type}",
        operation_id="operation-1",
        write_sequence=1,
        aggregate_kind="memory",
        aggregate_id="1",
        aggregate_version=1,
        event_type=event_type,
        payload_version=1,
        payload={},
        consumer_name="cooccurrence",
        attempt=1,
    )


class CooccurrenceSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _RebuiltMatrix.rebuild_calls = 0
        _RebuiltMatrix.rebuild_thread_ids = []
        _RebuiltMatrix.active_rebuilds = 0
        _RebuiltMatrix.max_active_rebuilds = 0

    async def test_projection_notifies_scheduler_without_inline_rebuild(self):
        live = _LiveMatrix()
        scheduler = _RecordingScheduler()
        projection = CooccurrenceProjection(live, scheduler=scheduler)

        await projection(_event("memory.tags_applied"))
        await projection(_event("memory.created"))

        self.assertEqual(scheduler.calls, [(1, "memory.tags_applied")])
        self.assertIs(scheduler.rebuild_lock, projection._lock)
        self.assertTrue(projection._dirty)
        self.assertEqual(
            projection.metrics_snapshot(),
            {
                "notifications_total": 1,
                "ignored_events_total": 1,
                "last_reason": "memory.tags_applied",
            },
        )

    async def test_projection_discovers_existing_scheduler_without_main_wiring(self):
        live = _LiveMatrix()
        scheduler = CooccurrenceScheduler(live, threshold_pct=1.0, cooldown_sec=60)
        projection = CooccurrenceProjection(live)

        await projection(_event("memory.tags_applied"))

        self.assertIs(projection.scheduler, scheduler)
        self.assertIs(scheduler._rebuild_lock, projection._lock)
        self.assertEqual(scheduler.metrics_snapshot()["notifications_total"], 1)
        self.assertIsNone(scheduler._scheduled_task)

    async def test_burst_of_tag_changes_schedules_one_background_rebuild(self):
        live = _LiveMatrix()
        scheduler = CooccurrenceScheduler(live, threshold_pct=0.1, cooldown_sec=0)

        with patch("engine.directed_cooccurrence.DirectedCooccurrence", _RebuiltMatrix):
            for _ in range(20):
                scheduler.notify_tag_change(reason="memory.tags_applied")
            task = scheduler._scheduled_task
            self.assertIsNotNone(task)
            await task

        self.assertEqual(_RebuiltMatrix.rebuild_calls, 1)
        self.assertFalse(scheduler._is_rebuilding)
        self.assertEqual(live.node_count, 12)
        metrics = scheduler.metrics_snapshot()
        self.assertEqual(metrics["notifications_total"], 20)
        self.assertEqual(metrics["rebuild_completed_total"], 1)
        self.assertEqual(metrics["last_rebuild"]["reasons"], ["memory.tags_applied"])

    async def test_cooldown_tail_rebuilds_without_a_later_event(self):
        live = _LiveMatrix()
        scheduler = CooccurrenceScheduler(live, threshold_pct=0.1, cooldown_sec=0.05)
        scheduler._last_rebuild_ts = time.time()

        with patch("engine.directed_cooccurrence.DirectedCooccurrence", _RebuiltMatrix):
            scheduler.notify_tag_change(reason="memory.tags_corrected")
            task = scheduler._scheduled_task
            self.assertIsNotNone(task)
            await asyncio.sleep(0.015)
            self.assertEqual(_RebuiltMatrix.rebuild_calls, 0)
            await asyncio.wait_for(task, timeout=0.5)

        self.assertEqual(_RebuiltMatrix.rebuild_calls, 1)
        self.assertEqual(
            scheduler.metrics_snapshot()["last_rebuild"]["reasons"],
            ["memory.tags_corrected"],
        )

    async def test_rebuild_runs_outside_event_loop_thread(self):
        live = _LiveMatrix()
        scheduler = CooccurrenceScheduler(live, threshold_pct=0.1, cooldown_sec=0)
        event_loop_thread = threading.get_ident()

        with patch("engine.directed_cooccurrence.DirectedCooccurrence", _RebuiltMatrix):
            scheduler.notify_tag_change()
            await scheduler._scheduled_task

        self.assertEqual(_RebuiltMatrix.rebuild_calls, 1)
        self.assertNotEqual(_RebuiltMatrix.rebuild_thread_ids[0], event_loop_thread)

    async def test_concurrent_force_rebuilds_share_projection_barrier(self):
        live = _LiveMatrix()
        scheduler = CooccurrenceScheduler(live, threshold_pct=0.1, cooldown_sec=60)
        projection = CooccurrenceProjection(live, scheduler=scheduler)

        with patch("engine.directed_cooccurrence.DirectedCooccurrence", _RebuiltMatrix):
            first, second = await asyncio.gather(
                projection.force_rebuild(reason="maintenance-a"),
                projection.force_rebuild(reason="maintenance-b"),
            )

        self.assertIs(scheduler._rebuild_lock, projection._lock)
        self.assertEqual(_RebuiltMatrix.rebuild_calls, 1)
        self.assertEqual(_RebuiltMatrix.max_active_rebuilds, 1)
        self.assertEqual(first["rebuild_completed_total"], 1)
        self.assertEqual(second["rebuild_completed_total"], 1)
        self.assertEqual(scheduler.metrics_snapshot()["force_requested_total"], 2)


if __name__ == "__main__":
    unittest.main()
