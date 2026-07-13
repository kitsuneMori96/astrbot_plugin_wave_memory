"""学习中心四类实体的 SQLite 仓储。所有读取必须显式提供 BotProfile.db_id。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from .learning_types import CandidateType, PromotionStatus, ReviewStatus, TargetKind, enum_value
from .migrations.learning_center import (
    ensure_learning_schema,
    learning_integrity_is_clean,
    mark_learning_integrity_clean,
)
from .book_experience_repo import BookExperienceEpisodeRepository
from .migrations.book_experience import ensure_book_experience_schema


class LearningRepositoryError(RuntimeError):
    code = "repository_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class LearningRepositoryIntegrityError(LearningRepositoryError):
    pass


class LearningIdempotencyConflict(LearningRepositoryError, ValueError):
    """幂等键已被不同领域对象占用；兼容既有 ValueError 调用方。"""

    code = "idempotency_conflict"


_NATIVE_WRITE_LOCKS: dict[int, threading.RLock] = {}
_NATIVE_WRITE_LOCKS_GUARD = threading.Lock()


def _native_write_lock(connection) -> threading.RLock:
    key = id(connection)
    with _NATIVE_WRITE_LOCKS_GUARD:
        return _NATIVE_WRITE_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _write_transaction(connection):
    transaction_factory = getattr(connection, "write_transaction", None)
    if callable(transaction_factory):
        with transaction_factory() as tx:
            was_integrity_clean = learning_integrity_is_clean(tx)
            yield tx
            if was_integrity_clean:
                mark_learning_integrity_clean(tx)
        return
    # sqlite3.Connection 本身允许跨线程使用（check_same_thread=False），但同一
    # connection 的 BEGIN/COMMIT 不能交错；为原生连接补一把进程内写锁。
    with _native_write_lock(connection):
        if bool(getattr(connection, "in_transaction", False)):
            raise RuntimeError("connection already has an active transaction")
        connection.execute("BEGIN IMMEDIATE")
        was_integrity_clean = learning_integrity_is_clean(connection)
        try:
            yield connection
            if was_integrity_clean:
                mark_learning_integrity_clean(connection)
        except BaseException:
            try:
                connection.rollback()
            except BaseException:
                pass
            raise
        else:
            try:
                connection.commit()
            except BaseException:
                try:
                    connection.rollback()
                except BaseException:
                    pass
                raise


def _integrity_error(exc: sqlite3.IntegrityError, operation: str):
    message = str(exc).upper()
    error_name = str(getattr(exc, "sqlite_errorname", "") or "").upper()
    if "FOREIGNKEY" in error_name or "FOREIGN KEY" in message:
        code = "foreign_key"
    elif "CHECK" in error_name or "CHECK" in message:
        code = "check_constraint"
    elif "NOTNULL" in error_name or "NOT NULL" in message:
        code = "not_null"
    elif (
        "UNIQUE" in error_name
        or "PRIMARYKEY" in error_name
        or "UNIQUE" in message
    ):
        code = "duplicate"
    else:
        code = "integrity_error"
    raise LearningRepositoryIntegrityError(
        f"{operation} failed database integrity validation", code=code
    ) from exc


def _bot_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("bot_id (BotProfile.db_id) is required")
    if normalized.isdecimal():
        raise ValueError("bot_id must be BotProfile.db_id, not a QQ number")
    return normalized


def _text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _page(limit: int, offset: int) -> tuple[int, int]:
    try:
        normalized_limit = int(limit)
        normalized_offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers") from exc
    if normalized_limit < 1 or normalized_limit > 500:
        raise ValueError("limit must be between 1 and 500")
    if normalized_offset < 0:
        raise ValueError("offset must be non-negative")
    return normalized_limit, normalized_offset


def _json_dump(value: Any, expected_type: type, field_name: str) -> str:
    if value is None:
        value = expected_type()
    if not isinstance(value, expected_type):
        raise ValueError(f"{field_name} must be {expected_type.__name__}")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any, expected_type: type):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return expected_type()
    return parsed if isinstance(parsed, expected_type) else expected_type()


class _Repository:
    def __init__(self, connection, now: Callable[[], float] | None = None):
        self.connection = connection
        self.now = now or time.time

    def _timestamp(self) -> float:
        return float(self.now())


class LearningSourceRepository(_Repository):
    _SELECT = "id, bot_id, source_type, name, enabled, config_json, cursor_json, created_at, updated_at"

    def create(
        self,
        *,
        bot_id: str,
        source_type: str,
        name: str,
        enabled: bool = True,
        config: dict[str, Any] | None = None,
        cursor: dict[str, Any] | None = None,
    ) -> int:
        bot_id = _bot_id(bot_id)
        source_type = _text(source_type, "source_type")
        name = _text(name, "name")
        now = self._timestamp()
        try:
            with _write_transaction(self.connection) as tx:
                row = tx.execute(
                    """INSERT INTO learning_sources
                       (bot_id, source_type, name, enabled, config_json, cursor_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(bot_id, source_type, name)
                       WHERE bot_id != '' AND source_type != '' AND name != ''
                       DO NOTHING RETURNING id""",
                    (
                        bot_id, source_type, name, 1 if enabled else 0,
                        _json_dump(config, dict, "config"),
                        _json_dump(cursor, dict, "cursor") if cursor is not None else None,
                        now, now,
                    ),
                ).fetchone()
                if row:
                    return int(row[0])
                existing = tx.execute(
                    "SELECT id FROM learning_sources WHERE bot_id=? AND source_type=? AND name=?",
                    (bot_id, source_type, name),
                ).fetchone()
                if existing:
                    return int(existing[0])
                raise LearningIdempotencyConflict("source identity conflict")
        except sqlite3.IntegrityError as exc:
            _integrity_error(exc, "create learning source")

    def get(self, source_id: int, *, bot_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {self._SELECT} FROM learning_sources WHERE id=? AND bot_id=?",
            (int(source_id), _bot_id(bot_id)),
        ).fetchone()
        return self._row(row) if row else None

    def list(self, *, bot_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        bot_id = _bot_id(bot_id)
        limit, offset = _page(limit, offset)
        total = self.connection.execute(
            "SELECT COUNT(*) FROM learning_sources WHERE bot_id=?", (bot_id,)
        ).fetchone()[0]
        rows = self.connection.execute(
            f"SELECT {self._SELECT} FROM learning_sources WHERE bot_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (bot_id, limit, offset),
        ).fetchall()
        return [self._row(row) for row in rows], int(total)

    def update(
        self,
        source_id: int,
        *,
        bot_id: str,
        enabled: bool | None = None,
        config: dict[str, Any] | None = None,
        cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """更新来源；为兼容既有调用，None 表示省略字段，JSON 清空请显式传入空字典。"""
        bot_id = _bot_id(bot_id)
        sets: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            sets.append("enabled=?")
            params.append(1 if enabled else 0)
        if config is not None:
            sets.append("config_json=?")
            params.append(_json_dump(config, dict, "config"))
        if cursor is not None:
            sets.append("cursor_json=?")
            params.append(_json_dump(cursor, dict, "cursor"))
        if not sets:
            return self.get(source_id, bot_id=bot_id)
        sets.append("updated_at=?")
        params.extend([self._timestamp(), int(source_id), bot_id])
        with _write_transaction(self.connection) as tx:
            tx.execute(
                f"UPDATE learning_sources SET {', '.join(sets)} WHERE id=? AND bot_id=?", params
            )
        return self.get(source_id, bot_id=bot_id)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {
            "id": row[0], "bot_id": row[1], "source_type": row[2], "name": row[3],
            "enabled": bool(row[4]), "config": _json_load(row[5], dict),
            "cursor": _json_load(row[6], dict), "created_at": row[7], "updated_at": row[8],
        }


class LearningJobRepository(_Repository):
    _SELECT = (
        "id, bot_id, source_id, candidate_type, name, enabled, schedule_json, policy_json, "
        "last_run_status, last_started_at, last_finished_at, last_error, lease_token, lease_until, created_at, updated_at"
    )

    def __init__(self, connection, sources: LearningSourceRepository, now=None):
        super().__init__(connection, now)
        self.sources = sources

    def create(
        self,
        *,
        bot_id: str,
        source_id: int,
        candidate_type: str | CandidateType,
        name: str,
        enabled: bool = True,
        schedule: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> int:
        bot_id = _bot_id(bot_id)
        candidate_type = enum_value(candidate_type, CandidateType, "candidate_type")
        name = _text(name, "name")
        schedule_json = _json_dump(schedule, dict, "schedule")
        policy_json = _json_dump(policy, dict, "policy")
        now = self._timestamp()
        try:
            with _write_transaction(self.connection) as tx:
                source = tx.execute(
                    "SELECT 1 FROM learning_sources WHERE id=? AND bot_id=?",
                    (int(source_id), bot_id),
                ).fetchone()
                if not source:
                    raise ValueError("source_id does not belong to bot_id")
                result = tx.execute(
                    """INSERT INTO learning_jobs
                       (bot_id, source_id, candidate_type, name, enabled, schedule_json, policy_json,
                        last_run_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'never', ?, ?)""",
                    (
                        bot_id, int(source_id), candidate_type, name, 1 if enabled else 0,
                        schedule_json, policy_json, now, now,
                    ),
                )
                return int(result.lastrowid)
        except sqlite3.IntegrityError as exc:
            _integrity_error(exc, "create learning job")

    def get(self, job_id: int, *, bot_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {self._SELECT} FROM learning_jobs WHERE id=? AND bot_id=?",
            (int(job_id), _bot_id(bot_id)),
        ).fetchone()
        return self._row(row) if row else None

    def update_enabled(self, job_id: int, *, bot_id: str, enabled: bool) -> dict[str, Any] | None:
        return self.update(job_id, bot_id=bot_id, enabled=enabled)

    def update(
        self,
        job_id: int,
        *,
        bot_id: str,
        enabled: bool | None = None,
        name: str | None = None,
        schedule: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """更新任务可配置字段，并保持 Bot 作用域。"""
        bot_id = _bot_id(bot_id)
        sets: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            sets.append("enabled=?")
            params.append(1 if enabled else 0)
        if name is not None:
            sets.append("name=?")
            params.append(_text(name, "name"))
        if schedule is not None:
            sets.append("schedule_json=?")
            params.append(_json_dump(schedule, dict, "schedule"))
        if policy is not None:
            sets.append("policy_json=?")
            params.append(_json_dump(policy, dict, "policy"))
        if not sets:
            return self.get(job_id, bot_id=bot_id)
        sets.append("updated_at=?")
        params.extend([self._timestamp(), int(job_id), bot_id])
        with _write_transaction(self.connection) as tx:
            tx.execute(
                f"UPDATE learning_jobs SET {', '.join(sets)} WHERE id=? AND bot_id=?", params
            )
        return self.get(job_id, bot_id=bot_id)

    def acquire_lease(
        self,
        job_id: int,
        *,
        bot_id: str,
        lease_token: str,
        now: float | None = None,
        lease_seconds: float = 300,
    ) -> bool:
        """原子获取任务租约；来源必须属于同一 bot，过期租约可接管。"""
        bot_id = _bot_id(bot_id)
        token = _text(lease_token, "lease_token")
        timestamp = float(self.now() if now is None else now)
        until = timestamp + max(1.0, float(lease_seconds))
        with _write_transaction(self.connection) as tx:
            result = tx.execute(
                """UPDATE learning_jobs
                   SET last_run_status='running', last_started_at=?, last_finished_at=NULL,
                       last_error=NULL, lease_token=?, lease_until=?, updated_at=?
                   WHERE id=? AND bot_id=? AND enabled=1
                     AND EXISTS (SELECT 1 FROM learning_sources s
                                 WHERE s.id=learning_jobs.source_id AND s.bot_id=? AND s.enabled=1)
                     AND (lease_until IS NULL OR lease_until<=?)""",
                (timestamp, token, until, timestamp, int(job_id), bot_id, bot_id, timestamp),
            )
            return result.rowcount == 1

    def finish_run(
        self,
        job_id: int,
        *,
        bot_id: str,
        lease_token: str,
        status: str,
        finished_at: float | None = None,
        error: str | None = None,
    ) -> bool:
        bot_id = _bot_id(bot_id)
        token = _text(lease_token, "lease_token")
        timestamp = float(self.now() if finished_at is None else finished_at)
        with _write_transaction(self.connection) as tx:
            result = tx.execute(
                """UPDATE learning_jobs
                   SET last_run_status=?, last_finished_at=?, last_error=?,
                       lease_token=NULL, lease_until=NULL, updated_at=?
                   WHERE id=? AND bot_id=? AND lease_token=?""",
                (str(status), timestamp, error, timestamp, int(job_id), bot_id, token),
            )
            return result.rowcount == 1

    def record_skip(self, job_id: int, *, bot_id: str, reason: str) -> dict[str, Any] | None:
        bot_id = _bot_id(bot_id)
        timestamp = self._timestamp()
        with _write_transaction(self.connection) as tx:
            tx.execute(
                """UPDATE learning_jobs SET last_run_status='skipped', last_started_at=?,
                   last_finished_at=?, last_error=?, lease_token=NULL, lease_until=NULL, updated_at=?
                   WHERE id=? AND bot_id=?""",
                (timestamp, timestamp, str(reason)[:500], timestamp, int(job_id), bot_id),
            )
        return self.get(job_id, bot_id=bot_id)

    def list(self, *, bot_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        bot_id = _bot_id(bot_id)
        limit, offset = _page(limit, offset)
        total = self.connection.execute(
            "SELECT COUNT(*) FROM learning_jobs WHERE bot_id=?", (bot_id,)
        ).fetchone()[0]

        rows = self.connection.execute(
            f"SELECT {self._SELECT} FROM learning_jobs WHERE bot_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (bot_id, limit, offset),
        ).fetchall()
        return [self._row(row) for row in rows], int(total)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {
            "id": row[0], "bot_id": row[1], "source_id": row[2], "candidate_type": row[3],
            "name": row[4], "enabled": bool(row[5]), "schedule": _json_load(row[6], dict),
            "policy": _json_load(row[7], dict), "last_run_status": row[8],
            "last_started_at": row[9], "last_finished_at": row[10], "last_error": row[11],
            "lease_token": row[12], "lease_until": row[13], "created_at": row[14], "updated_at": row[15],
        }


class LearningCandidateRepository(_Repository):
    _SELECT = (
        "id, bot_id, source_id, job_id, candidate_type, content, evidence_json, reason, source_fingerprint, "
        "review_status, reviewer, reviewed_at, review_note, legacy_kind, legacy_ref, metadata_json, created_at, updated_at"
    )

    def __init__(self, connection, sources: LearningSourceRepository, jobs: LearningJobRepository, now=None):
        super().__init__(connection, now)
        self.sources = sources
        self.jobs = jobs

    def create(
        self,
        *,
        bot_id: str,
        candidate_type: str | CandidateType,
        content: str,
        evidence: dict[str, Any],
        source_fingerprint: str,
        source_id: int | None = None,
        job_id: int | None = None,
        reason: str | None = None,
        review_status: str | ReviewStatus = ReviewStatus.PENDING,
        legacy_kind: str | None = None,
        legacy_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        bot_id = _bot_id(bot_id)
        candidate_type = enum_value(candidate_type, CandidateType, "candidate_type")
        fingerprint = _text(source_fingerprint, "source_fingerprint")
        content = _text(content, "content")
        evidence_json = _json_dump(evidence, dict, "evidence")
        metadata_json = _json_dump(metadata, dict, "metadata")
        review_status = enum_value(review_status, ReviewStatus, "review_status")
        now = self._timestamp()
        try:
            with _write_transaction(self.connection) as tx:
                if source_id is not None and not tx.execute(
                    "SELECT 1 FROM learning_sources WHERE id=? AND bot_id=?",
                    (int(source_id), bot_id),
                ).fetchone():
                    raise ValueError("source_id does not belong to bot_id")
                if job_id is not None and not tx.execute(
                    "SELECT 1 FROM learning_jobs WHERE id=? AND bot_id=?",
                    (int(job_id), bot_id),
                ).fetchone():
                    raise ValueError("job_id does not belong to bot_id")
                row = tx.execute(
                    """INSERT INTO learning_candidates
                       (bot_id, source_id, job_id, candidate_type, content, evidence_json, reason,
                        source_fingerprint, review_status, legacy_kind, legacy_ref, metadata_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(bot_id, candidate_type, source_fingerprint)
                       WHERE bot_id != '' AND candidate_type != '' AND source_fingerprint != ''
                       DO NOTHING RETURNING id""",
                    (
                        bot_id, source_id, job_id, candidate_type, content, evidence_json,
                        str(reason or ""), fingerprint, review_status, legacy_kind, legacy_ref,
                        metadata_json, now, now,
                    ),
                ).fetchone()
                if row:
                    return int(row[0])
                existing = tx.execute(
                    """SELECT id FROM learning_candidates
                       WHERE bot_id=? AND candidate_type=? AND source_fingerprint=?""",
                    (bot_id, candidate_type, fingerprint),
                ).fetchone()
                if existing:
                    return int(existing[0])
                raise LearningIdempotencyConflict("candidate fingerprint conflict")
        except sqlite3.IntegrityError as exc:
            _integrity_error(exc, "create learning candidate")

    def get(self, candidate_id: int, *, bot_id: str) -> dict[str, Any] | None:
        with _native_write_lock(self.connection):
            row = self.connection.execute(
                f"SELECT {self._SELECT} FROM learning_candidates WHERE id=? AND bot_id=?",
                (int(candidate_id), _bot_id(bot_id)),
            ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        *,
        bot_id: str,
        limit: int = 50,
        offset: int = 0,
        candidate_type: str | CandidateType | None = None,
        review_status: str | ReviewStatus | None = None,
        promotion_status: str | PromotionStatus | None = None,
        source: str | int | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """按 Bot 分页列出候选，并在仓储边界执行筛选。

        ``source`` 同时接受来源 ID、source_type 或来源名称；晋升状态通过
        ``EXISTS`` 查询，避免一个候选有多个目标时重复返回。
        """
        bot_id = _bot_id(bot_id)
        limit, offset = _page(limit, offset)
        where = ["c.bot_id=?"]
        params: list[Any] = [bot_id]
        if candidate_type is not None:
            where.append("c.candidate_type=?")
            params.append(enum_value(candidate_type, CandidateType, "candidate_type"))
        if review_status is not None:
            where.append("c.review_status=?")
            params.append(enum_value(review_status, ReviewStatus, "review_status"))
        if promotion_status is not None:
            where.append(
                "EXISTS (SELECT 1 FROM learning_promotions p WHERE p.candidate_id=c.id AND p.bot_id=c.bot_id AND p.promotion_status=?)"
            )
            params.append(enum_value(promotion_status, PromotionStatus, "promotion_status"))
        if source is not None and str(source).strip():
            source_value = str(source).strip()
            where.append("(CAST(c.source_id AS TEXT)=? OR s.source_type=? OR s.name=?)")
            params.extend([source_value, source_value, source_value])
        if since is not None:
            where.append("c.created_at>=?")
            params.append(float(since))
        if until is not None:
            where.append("c.created_at<=?")
            params.append(float(until))
        where_sql = " AND ".join(where)
        total = self.connection.execute(
            "SELECT COUNT(*) FROM learning_candidates c LEFT JOIN learning_sources s ON s.id=c.source_id AND s.bot_id=c.bot_id WHERE " + where_sql,
            params,
        ).fetchone()[0]
        selected = ", ".join(f"c.{column.strip()}" for column in self._SELECT.split(","))
        rows = self.connection.execute(
            f"SELECT {selected} FROM learning_candidates c "
            "LEFT JOIN learning_sources s ON s.id=c.source_id AND s.bot_id=c.bot_id "
            f"WHERE {where_sql} ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row(row) for row in rows], int(total)

    def update_review(
        self,
        candidate_id: int,
        *,
        bot_id: str,
        review_status: str | ReviewStatus,
        reviewer: str | None = None,
        reviewed_at: float | None = None,
        review_note: str | None = None,
    ) -> dict[str, Any] | None:
        bot_id = _bot_id(bot_id)
        status = enum_value(review_status, ReviewStatus, "review_status")
        timestamp = float(reviewed_at if reviewed_at is not None else self._timestamp())
        with _write_transaction(self.connection) as tx:
            tx.execute(
                """UPDATE learning_candidates
                   SET review_status=?, reviewer=?, reviewed_at=?, review_note=?, updated_at=?
                   WHERE id=? AND bot_id=?""",
                (status, reviewer, timestamp, review_note, self._timestamp(), int(candidate_id), bot_id),
            )
        return self.get(candidate_id, bot_id=bot_id)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {
            "id": row[0], "bot_id": row[1], "source_id": row[2], "job_id": row[3],
            "candidate_type": row[4], "content": row[5], "evidence": _json_load(row[6], dict),
            "reason": row[7] or "", "source_fingerprint": row[8], "review_status": row[9],
            "reviewer": row[10], "reviewed_at": row[11], "review_note": row[12],
            "legacy_kind": row[13], "legacy_ref": row[14], "metadata": _json_load(row[15], dict),
            "created_at": row[16], "updated_at": row[17],
        }


class LearningPromotionRepository(_Repository):
    _SELECT = (
        "id, candidate_id, bot_id, target_kind, idempotency_key, promotion_status, attempt_count, target_id, "
        "error_code, error_message, requested_by, started_at, finished_at, metadata_json, created_at, updated_at"
    )

    def __init__(self, connection, candidates: LearningCandidateRepository, now=None):
        super().__init__(connection, now)
        self.candidates = candidates

    def create(
        self,
        *,
        bot_id: str,
        candidate_id: int,
        target_kind: str | TargetKind,
        idempotency_key: str,
        promotion_status: str | PromotionStatus = PromotionStatus.QUEUED,
        requested_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        bot_id = _bot_id(bot_id)
        key = _text(idempotency_key, "idempotency_key")
        target_kind = enum_value(target_kind, TargetKind, "target_kind")
        promotion_status = enum_value(promotion_status, PromotionStatus, "promotion_status")
        metadata_json = _json_dump(metadata, dict, "metadata")
        now = self._timestamp()
        try:
            with _write_transaction(self.connection) as tx:
                candidate = tx.execute(
                    "SELECT 1 FROM learning_candidates WHERE id=? AND bot_id=?",
                    (int(candidate_id), bot_id),
                ).fetchone()
                if not candidate:
                    raise ValueError("candidate_id does not belong to bot_id")
                row = tx.execute(
                    """INSERT INTO learning_promotions
                       (candidate_id, bot_id, target_kind, idempotency_key, promotion_status,
                        attempt_count, requested_by, metadata_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                       ON CONFLICT(idempotency_key) WHERE idempotency_key != ''
                       DO NOTHING RETURNING id""",
                    (
                        int(candidate_id), bot_id, target_kind, key, promotion_status,
                        requested_by, metadata_json, now, now,
                    ),
                ).fetchone()
                if row:
                    return int(row[0])
                existing = tx.execute(
                    "SELECT id FROM learning_promotions WHERE idempotency_key=? AND bot_id=?",
                    (key, bot_id),
                ).fetchone()
                if existing:
                    return int(existing[0])
                raise LearningIdempotencyConflict(
                    "idempotency_key is unavailable for this bot_id"
                )
        except sqlite3.IntegrityError as exc:
            _integrity_error(exc, "create learning promotion")

    def get(self, promotion_id: int, *, bot_id: str) -> dict[str, Any] | None:
        with _native_write_lock(self.connection):
            row = self.connection.execute(
                f"SELECT {self._SELECT} FROM learning_promotions WHERE id=? AND bot_id=?",
                (int(promotion_id), _bot_id(bot_id)),
            ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        *,
        bot_id: str,
        limit: int = 50,
        offset: int = 0,
        candidate_type: str | CandidateType | None = None,
        promotion_status: str | PromotionStatus | None = None,
        target_kind: str | TargetKind | None = None,
        source: str | int | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """按 Bot 分页列出晋升历史，并可联表筛选候选类型/来源。"""
        bot_id = _bot_id(bot_id)
        limit, offset = _page(limit, offset)
        where = ["p.bot_id=?"]
        params: list[Any] = [bot_id]
        if candidate_type is not None:
            where.append("c.candidate_type=?")
            params.append(enum_value(candidate_type, CandidateType, "candidate_type"))
        if promotion_status is not None:
            where.append("p.promotion_status=?")
            params.append(enum_value(promotion_status, PromotionStatus, "promotion_status"))
        if target_kind is not None:
            where.append("p.target_kind=?")
            params.append(enum_value(target_kind, TargetKind, "target_kind"))
        if source is not None and str(source).strip():
            source_value = str(source).strip()
            where.append("(CAST(c.source_id AS TEXT)=? OR s.source_type=? OR s.name=?)")
            params.extend([source_value, source_value, source_value])
        if since is not None:
            where.append("p.created_at>=?")
            params.append(float(since))
        if until is not None:
            where.append("p.created_at<=?")
            params.append(float(until))
        where_sql = " AND ".join(where)
        total = self.connection.execute(
            "SELECT COUNT(*) FROM learning_promotions p "
            "JOIN learning_candidates c ON c.id=p.candidate_id AND c.bot_id=p.bot_id "
            "LEFT JOIN learning_sources s ON s.id=c.source_id AND s.bot_id=c.bot_id WHERE " + where_sql,
            params,
        ).fetchone()[0]
        selected = ", ".join(f"p.{column.strip()}" for column in self._SELECT.split(","))
        rows = self.connection.execute(
            f"SELECT {selected} FROM learning_promotions p "
            "JOIN learning_candidates c ON c.id=p.candidate_id AND c.bot_id=p.bot_id "
            "LEFT JOIN learning_sources s ON s.id=c.source_id AND s.bot_id=c.bot_id "
            f"WHERE {where_sql} ORDER BY p.created_at DESC, p.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row(row) for row in rows], int(total)


    def update_status(
        self,
        promotion_id: int,
        *,
        bot_id: str,
        promotion_status: str | PromotionStatus,
        target_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        increment_attempt: bool = False,
        started_at: float | None = None,
        finished_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        bot_id = _bot_id(bot_id)
        status = enum_value(promotion_status, PromotionStatus, "promotion_status")
        attempt_sql = "attempt_count + 1" if increment_attempt else "attempt_count"
        metadata_json = _json_dump(metadata, dict, "metadata") if metadata is not None else None
        with _write_transaction(self.connection) as tx:
            tx.execute(
                f"""UPDATE learning_promotions
                   SET promotion_status=?, target_id=COALESCE(?, target_id), error_code=?, error_message=?,
                       attempt_count={attempt_sql}, started_at=COALESCE(?, started_at),
                       finished_at=COALESCE(?, finished_at), metadata_json=COALESCE(?, metadata_json), updated_at=?
                   WHERE id=? AND bot_id=?""",
                (
                    status, target_id, error_code, error_message, started_at, finished_at,
                    metadata_json, self._timestamp(), int(promotion_id), bot_id,
                ),
            )
        return self.get(promotion_id, bot_id=bot_id)

    def claim(
        self,
        promotion_id: int,
        *,
        bot_id: str,
        started_at: float | None = None,
    ) -> dict[str, Any] | None:
        """原子取得晋升执行权，避免双击或多进程重复调用目标服务。"""
        bot_id = _bot_id(bot_id)
        timestamp = float(self.now() if started_at is None else started_at)
        with _write_transaction(self.connection) as tx:
            result = tx.execute(
                """UPDATE learning_promotions
                   SET promotion_status='running', attempt_count=attempt_count + 1,
                       started_at=?, finished_at=NULL, error_code=NULL, error_message=NULL, updated_at=?
                   WHERE id=? AND bot_id=? AND promotion_status IN ('queued', 'retryable_failed')""",
                (timestamp, timestamp, int(promotion_id), bot_id),
            )
            if result.rowcount != 1:
                return None
        return self.get(promotion_id, bot_id=bot_id)

    def recover_stale(
        self,
        *,
        bot_id: str,
        now: float | None = None,
        timeout: float = 300.0,
    ) -> int:
        """将进程中断后遗留的 running 晋升恢复为可重试失败。"""
        bot_id = _bot_id(bot_id)
        timestamp = float(self.now() if now is None else now)
        cutoff = timestamp - max(0.0, float(timeout))
        with _write_transaction(self.connection) as tx:
            rows = tx.execute(
                """SELECT id, target_id, metadata_json FROM learning_promotions
                   WHERE bot_id=? AND promotion_status='running'
                     AND started_at IS NOT NULL AND started_at<=?""",
                (bot_id, cutoff),
            ).fetchall()
            for promotion_id, target_id, metadata_json in rows:
                metadata = _json_load(metadata_json, dict)
                metadata["interrupted"] = True
                metadata["refresh_pending"] = bool(target_id or metadata.get("target_written"))
                tx.execute(
                    """UPDATE learning_promotions
                       SET promotion_status='retryable_failed', error_code='interrupted',
                           error_message='promotion interrupted before completion', finished_at=?,
                           metadata_json=?, updated_at=?
                       WHERE id=? AND bot_id=? AND promotion_status='running'""",
                    (
                        timestamp, _json_dump(metadata, dict, "metadata"), timestamp,
                        int(promotion_id), bot_id,
                    ),
                )
            return len(rows)

    def list_for_candidate(self, candidate_id: int, *, bot_id: str) -> list[dict[str, Any]]:
        bot_id = _bot_id(bot_id)
        with _native_write_lock(self.connection):
            rows = self.connection.execute(
                f"SELECT {self._SELECT} FROM learning_promotions "
                "WHERE candidate_id=? AND bot_id=? ORDER BY id",
                (int(candidate_id), bot_id),
            ).fetchall()
        return [self._row(row) for row in rows]


    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {
            "id": row[0], "candidate_id": row[1], "bot_id": row[2], "target_kind": row[3],
            "idempotency_key": row[4], "promotion_status": row[5], "attempt_count": row[6],
            "target_id": row[7], "error_code": row[8], "error_message": row[9],
            "requested_by": row[10], "started_at": row[11], "finished_at": row[12],
            "metadata": _json_load(row[13], dict), "created_at": row[14], "updated_at": row[15],
        }


@dataclass
class LearningRepositories:
    connection: Any
    sources: LearningSourceRepository
    jobs: LearningJobRepository
    candidates: LearningCandidateRepository
    promotions: LearningPromotionRepository
    book_experiences: BookExperienceEpisodeRepository | None = None
    _owns_connection: bool = False

    @classmethod
    def from_connection(cls, connection, *, now: Callable[[], float] | None = None):
        # 先建独立书中经历表，再记录学习中心 schema cookie，避免每次普通写入
        # 都因新增表改变 cookie 而走昂贵的全量 schema 校验路径。
        ensure_book_experience_schema(connection)
        ensure_learning_schema(connection)
        sources = LearningSourceRepository(connection, now)
        jobs = LearningJobRepository(connection, sources, now)
        candidates = LearningCandidateRepository(connection, sources, jobs, now)
        promotions = LearningPromotionRepository(connection, candidates, now)
        book_experiences = BookExperienceEpisodeRepository(connection, now=now)
        return cls(
            connection, sources, jobs, candidates, promotions,
            book_experiences=book_experiences, _owns_connection=False,
        )

    @classmethod
    def open(cls, db_path: str, *, now: Callable[[], float] | None = None):
        connection = sqlite3.connect(db_path, check_same_thread=False)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            repos = cls.from_connection(connection, now=now)
        except BaseException:
            connection.close()
            raise
        repos._owns_connection = True
        return repos

    def review_candidate(
        self,
        candidate_id: int,
        *,
        bot_id: str,
        review_status: str | ReviewStatus,
        reviewer: str,
        reviewed_at: float,
        review_note: str | None = None,
        promotions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """在同一事务中写审核审计并幂等创建晋升记录。

        审核 service 不需要接触 SQL；这里同时完成候选状态与晋升队列的原子提交。
        """
        bot_id = _bot_id(bot_id)
        status = enum_value(review_status, ReviewStatus, "review_status")
        reviewer = _text(reviewer, "reviewer")
        note = None if review_note is None else str(review_note)
        promotions = list(promotions or [])
        now = float(reviewed_at)
        promotion_rows: list[int] = []
        try:
            with _write_transaction(self.connection) as tx:
                row = tx.execute(
                    f"SELECT {self.candidates._SELECT} FROM learning_candidates "
                    "WHERE id=? AND bot_id=?",
                    (int(candidate_id), bot_id),
                ).fetchone()
                if not row:
                    raise ValueError("candidate not found for bot_id")
                current_status = row[9]
                if current_status not in {"pending", status}:
                    raise ValueError(
                        f"candidate review already {current_status}; cannot change to {status}"
                    )
                if current_status == "pending":
                    tx.execute(
                        """UPDATE learning_candidates
                           SET review_status=?, reviewer=?, reviewed_at=?, review_note=?, updated_at=?
                           WHERE id=? AND bot_id=? AND review_status='pending'""",
                        (status, reviewer, now, note, now, int(candidate_id), bot_id),
                    )
                if status in {ReviewStatus.APPROVED.value, ReviewStatus.DELEGATED.value}:
                    for spec in promotions:
                        target_kind = enum_value(spec["target_kind"], TargetKind, "target_kind")
                        key = _text(spec["idempotency_key"], "idempotency_key")
                        promotion_status = enum_value(
                            spec.get("promotion_status", PromotionStatus.QUEUED),
                            PromotionStatus,
                            "promotion_status",
                        )
                        metadata_json = _json_dump(spec.get("metadata"), dict, "metadata")
                        inserted = tx.execute(
                            """INSERT INTO learning_promotions
                               (candidate_id, bot_id, target_kind, idempotency_key, promotion_status,
                                attempt_count, target_id, requested_by, metadata_json, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                               ON CONFLICT(idempotency_key) WHERE idempotency_key != '' DO NOTHING RETURNING id""",
                            (
                                int(candidate_id), bot_id, target_kind, key, promotion_status,
                                spec.get("target_id"), reviewer, metadata_json, now, now,
                            ),
                        ).fetchone()
                        if inserted:
                            promotion_rows.append(int(inserted[0]))
                        else:
                            existing = tx.execute(
                                "SELECT id FROM learning_promotions WHERE idempotency_key=?",
                                (key,),
                            ).fetchone()
                            if not existing:
                                raise LearningIdempotencyConflict(
                                    "idempotency_key is unavailable"
                                )
                            promotion_rows.append(int(existing[0]))
        except sqlite3.IntegrityError as exc:
            _integrity_error(exc, "review learning candidate")
        candidate = self.candidates.get(candidate_id, bot_id=bot_id)
        return {
            "candidate": candidate,
            "promotions": [self.promotions.get(item, bot_id=bot_id) for item in promotion_rows],
        }

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()
            self._owns_connection = False


__all__ = [
    "LearningCandidateRepository",
    "LearningIdempotencyConflict",
    "LearningJobRepository",
    "LearningPromotionRepository",
    "LearningRepositories",
    "LearningRepositoryError",
    "LearningRepositoryIntegrityError",
    "LearningSourceRepository",
]
