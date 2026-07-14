"""Per-consumer durable outbox dispatcher."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Mapping
from typing import Any

try:
    from ..engine.db.outbox_repo import OutboxEvent, OutboxRepository
except ImportError:  # pragma: no cover - repository tests import top-level packages
    from engine.db.outbox_repo import OutboxEvent, OutboxRepository


class OutboxDispatcher:
    def __init__(self, coordinator: Any, consumers: Mapping[str, Callable[[OutboxEvent], Any]], clock: Any):
        self._coordinator = coordinator
        self._consumers = dict(consumers)
        self._consumer_names = tuple(sorted(self._consumers))
        self._clock = clock
        self._lease_owner = f"dispatcher-{uuid.uuid4().hex}"
        self._closed = False

    async def drain_to_watermark(self, watermark: int) -> None:
        if self._closed:
            return
        attempted: set[tuple[str, str]] = set()
        while True:
            now = float(self._clock.now())
            event = await self._coordinator.transaction(
                lambda conn: OutboxRepository.claim_next(
                    conn, consumer_names=self._consumer_names, watermark=int(watermark),
                    now=now, lease_owner=self._lease_owner, excluded=attempted,
                )
            )
            if event is None:
                return
            attempted.add((event.event_id, event.consumer_name))
            applied = await self._coordinator.read(
                lambda conn: OutboxRepository.applied_version(conn, event)
            )
            if applied is not None and applied >= event.aggregate_version:
                await self._coordinator.transaction(
                    lambda conn: OutboxRepository.mark_stale(conn, event, float(self._clock.now()))
                )
                continue
            try:
                result = self._consumers[event.consumer_name](event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                await self._coordinator.transaction(
                    lambda conn: OutboxRepository.mark_failed(
                        conn, event, float(self._clock.now()), detail
                    )
                )
            else:
                await self._coordinator.transaction(
                    lambda conn: OutboxRepository.mark_succeeded(
                        conn, event, float(self._clock.now())
                    )
                )

    async def advance_clock_to_next_attempt(self) -> None:
        timestamp = await self._coordinator.read(
            lambda conn: OutboxRepository.next_attempt_at(conn, self._consumer_names)
        )
        if timestamp is not None:
            self._clock.advance_to(timestamp)

    async def close(self) -> None:
        self._closed = True
