"""学习任务执行器：租约、来源消费、候选创建和游标提交。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

try:
    from ...engine.db.learning_repository import LearningRepositories
except ImportError:  # 兼容独立测试/外部调用 services.learning
    from engine.db.learning_repository import LearningRepositories

from .candidate_service import LearningCandidateService
from .source import LearningSourceItem, LearningSourceRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LearningJobRunResult:
    job_id: int
    bot_id: str
    status: str
    reason: str = ""
    inputs_seen: int = 0
    candidates_created: int = 0
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


class LearningJobRunner:
    """按稳定 bot_id 执行学习任务；候选成功后才提交对应来源游标。"""

    def __init__(
        self,
        repositories: LearningRepositories,
        registry: LearningSourceRegistry,
        *,
        candidate_service: LearningCandidateService | None = None,
        bot_id: str | None = None,
        lease_seconds: float = 300,
        poll_interval: float = 60,
        now=None,
    ) -> None:
        self.repositories = repositories
        self.registry = registry
        self.candidate_service = candidate_service or LearningCandidateService(repositories)
        self.bot_id = self._normalize_bot_id(bot_id) if bot_id is not None else None
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.poll_interval = max(0.01, float(poll_interval))
        self.now = now or time.time
        self._running = False
        self._task: asyncio.Task | None = None
        self._stats: dict[int, dict[str, int]] = {}

    @staticmethod
    def _normalize_bot_id(bot_id: str) -> str:
        value = str(bot_id or "").strip()
        if not value:
            raise ValueError("bot_id (BotProfile.db_id) is required")
        if value.isdecimal():
            raise ValueError("bot_id must be BotProfile.db_id, not a QQ number")
        return value

    @property
    def running(self) -> bool:
        return self._running

    def start(self, *, bot_id: str | None = None) -> asyncio.Task:
        """启动周期扫描；同一 runner 重复 start 不创建第二个后台循环。"""
        if bot_id is not None:
            self.bot_id = self._normalize_bot_id(bot_id)
        if self.bot_id is None:
            raise ValueError("bot_id (BotProfile.db_id) is required")
        if self._running and self._task is not None:
            return self._task
        self._running = True
        self._task = asyncio.create_task(self._schedule_loop())
        return self._task

    def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    async def _schedule_loop(self) -> None:
        try:
            while self._running:
                jobs, _ = self.repositories.jobs.list(bot_id=self.bot_id, limit=500)
                for job in jobs:
                    if not self._running:
                        break
                    if self._job_due(job):
                        try:
                            await self.run_job(int(job["id"]), bot_id=self.bot_id)
                        except Exception:
                            logger.exception("[LearningJobRunner] scheduled job failed bot_id=%s job_id=%s", self.bot_id, job["id"])
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            raise
        finally:
            self._running = False

    def _job_due(self, job: dict[str, Any]) -> bool:
        if not job.get("enabled"):
            return False
        schedule = job.get("schedule") or {}
        if schedule.get("manual") is True:
            return False
        interval = schedule.get("interval_seconds", schedule.get("interval"))
        if interval is None:
            return False
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            return False
        last = job.get("last_finished_at")
        return last is None or float(self.now()) - float(last) >= max(0.0, interval)

    async def run_now(self, job_id: int, *, bot_id: str | None = None) -> LearningJobRunResult:
        return await self.run_job(job_id, bot_id=bot_id)

    async def run(self, job_id: int, *, bot_id: str | None = None) -> LearningJobRunResult:
        return await self.run_job(job_id, bot_id=bot_id)

    async def run_once(self, job_id: int, *, bot_id: str | None = None) -> LearningJobRunResult:
        return await self.run_job(job_id, bot_id=bot_id)

    async def manual_run(self, job_id: int, *, bot_id: str | None = None) -> LearningJobRunResult:
        return await self.run_job(job_id, bot_id=bot_id)

    async def run_job(self, job_id: int, *, bot_id: str | None = None) -> LearningJobRunResult:
        scope = self._normalize_bot_id(bot_id if bot_id is not None else self.bot_id)
        job = self.repositories.jobs.get(int(job_id), bot_id=scope)
        if not job:
            raise ValueError("job_id does not belong to bot_id")
        source = self.repositories.sources.get(int(job["source_id"]), bot_id=scope)
        if not source:
            raise ValueError("source_id does not belong to bot_id")
        if not job["enabled"] or not source["enabled"]:
            reason = "job_disabled" if not job["enabled"] else "source_disabled"
            self.repositories.jobs.record_skip(int(job_id), bot_id=scope, reason=reason)
            result = LearningJobRunResult(int(job_id), scope, "skipped", reason=reason)
            self._update_stats(result)
            return result

        token = uuid.uuid4().hex
        started = float(self.now())
        if not self.repositories.jobs.acquire_lease(
            int(job_id), bot_id=scope, lease_token=token, now=started, lease_seconds=self.lease_seconds
        ):
            result = LearningJobRunResult(int(job_id), scope, "skipped", reason="lease_unavailable")
            self._update_stats(result)
            return result

        inputs_seen = 0
        candidates_created = 0
        policy = job.get("policy") or {}
        max_items = policy.get("max_items", policy.get("batch_size"))
        try:
            max_items = int(max_items) if max_items is not None else None
        except (TypeError, ValueError):
            max_items = None
        if max_items is not None and max_items < 1:
            max_items = None
        status = "succeeded"
        error = None
        try:
            adapter = self.registry.resolve(source["source_type"])
            collector = getattr(adapter, "collect", None) or getattr(adapter, "fetch", None)
            if not callable(collector):
                raise TypeError("adapter must implement collect or fetch")
            output = collector(
                bot_id=scope, source=source, job=job, cursor=source.get("cursor")
            )
            if inspect.isawaitable(output):
                output = await output
            async for raw_item in self._aiter(output):
                item = LearningSourceItem.from_value(raw_item)
                inputs_seen += 1
                candidate_type = job["candidate_type"]
                # 仅允许书中经历适配器将证据不足的输入降级为 book_lore；
                # 通用适配器不能通过 metadata 任意改变任务候选类型。
                if candidate_type == "book_experience_episode":
                    requested_type = item.metadata.get("candidate_type")
                    if requested_type in {"book_experience_episode", "book_lore"}:
                        candidate_type = requested_type
                created = self.candidate_service.create_from_item(
                    item,
                    bot_id=scope,
                    candidate_type=candidate_type,
                    source_id=int(source["id"]),
                    job_id=int(job["id"]),
                )
                if inspect.isawaitable(created):
                    created = await created
                if created is not None:
                    candidates_created += 1
                # 游标提交必须位于候选成功之后；失败输入和其后的输入均不越过。
                if item.cursor is not None:
                    self.repositories.sources.update(
                        int(source["id"]), bot_id=scope, cursor=dict(item.cursor)
                    )
                if max_items is not None and inputs_seen >= max_items:
                    break
        except asyncio.CancelledError:
            status = "interrupted"
            error = "job interrupted"
            raise
        except Exception as exc:
            status = "failed"
            error = self._error_summary(exc)
        finally:
            finished = float(self.now())
            self.repositories.jobs.finish_run(
                int(job_id), bot_id=scope, lease_token=token, status=status,
                finished_at=finished, error=error,
            )
        result = LearningJobRunResult(
            int(job_id), scope, status, inputs_seen=inputs_seen,
            candidates_created=candidates_created, error=error,
            started_at=started, finished_at=finished,
        )
        self._update_stats(result)
        return result

    async def _aiter(self, value):
        if hasattr(value, "__aiter__"):
            async for item in value:
                yield item
            return
        for item in value or ():
            yield item

    @staticmethod
    def _error_summary(exc: BaseException) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        return f"{exc.__class__.__name__}: {message}"[:500]

    def _update_stats(self, result: LearningJobRunResult) -> None:
        stats = self._stats.setdefault(result.job_id, {
            "runs": 0, "succeeded": 0, "failed": 0, "skipped": 0,
            "inputs_seen": 0, "candidates_created": 0, "errors": 0,
        })
        stats["runs"] += 1
        stats[result.status] = stats.get(result.status, 0) + 1
        stats["inputs_seen"] += result.inputs_seen
        stats["candidates_created"] += result.candidates_created
        if result.error:
            stats["errors"] += 1

    def get_stats(self, job_id: int) -> dict[str, int]:
        return dict(self._stats.get(int(job_id), {
            "runs": 0, "succeeded": 0, "failed": 0, "skipped": 0,
            "inputs_seen": 0, "candidates_created": 0, "errors": 0,
        }))

    @property
    def stats(self) -> dict[int, dict[str, int]]:
        return {job_id: dict(value) for job_id, value in self._stats.items()}


__all__ = ["LearningJobRunResult", "LearningJobRunner"]
