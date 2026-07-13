"""旧学习数据的可恢复幂等迁移与只读兼容投影。

这个模块刻意不改写 ``memories.source``、``experience_episodes`` 或旧的
``review_candidates``。旧数据先按稳定主键进入新候选视图，已经生效的历史
则只通过 :func:`read_legacy_projections` 双读展示。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable, Mapping

try:
    from ...engine.db.migrations.learning_center import (
        ensure_learning_schema,
        mark_learning_integrity_clean,
    )
except ImportError:  # 兼容独立脚本/外部调用 services.learning
    from engine.db.migrations.learning_center import (
        ensure_learning_schema,
        mark_learning_integrity_clean,
    )

logger = logging.getLogger(__name__)

LEGACY_BOT_ID = "baizz"
LEGACY_PENDING_SOURCE = "bzz_pending"
LEGACY_EVOLUTION_SOURCE = "bzz_evolution"
LEGACY_EXPERIENCE_SOURCE = "bzz_experience"
WORLDVIEW_INTERNALIZATION = "worldview_internalization"

# 这些是对账基线，不是迁移边界；实际处理范围由启动时快照和主键水位决定。
BASELINE_COUNTS: Mapping[str, int] = {
    "bzz_pending": 418,
    "bzz_evolution": 220,
    "bzz_experience": 1416,
    "experience_episodes": 334,
    "review_candidates": 0,
}

LEGACY_PROJECTION_LABELS: Mapping[str, str] = {
    "worldview_internalization": "世界观内化（非书中真实经历）",
    "effective_history": "已生效历史（只读）",
    "legacy_history_experience": "legacy 历史经历（只读）",
    "interaction_experience": "互动经历",
}

_RUN_TABLE = "learning_legacy_migration_runs"
_RUN_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_RUN_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    baseline_json TEXT NOT NULL,
    start_counts_json TEXT NOT NULL,
    watermarks_json TEXT NOT NULL,
    status TEXT NOT NULL,
    finished_at REAL,
    end_counts_json TEXT,
    result_json TEXT
)
"""


def _table_exists(connection: Any, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: Any, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


@contextmanager
def _write_transaction(connection: Any):
    """兼容 sqlite3.Connection 与 ConnectionManager/代理的写事务。"""
    factory = getattr(connection, "write_transaction", None)
    if callable(factory):
        with factory() as tx:
            yield tx
        return
    if bool(getattr(connection, "in_transaction", False)):
        raise RuntimeError("connection already has an active transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
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


def _count(connection: Any, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0])


def _max_id(connection: Any, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int | None:
    if not _table_exists(connection, table):
        return None
    columns = _columns(connection, table)
    if "id" not in columns:
        return None
    row = connection.execute(f"SELECT MAX(id) FROM {table} {where}", params).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _snapshot(connection: Any, bot_id: str) -> tuple[dict[str, int], dict[str, int | None]]:
    counts = {
        "bzz_pending": _count(connection, "memories", "WHERE source=?", (LEGACY_PENDING_SOURCE,)),
        "bzz_evolution": _count(connection, "memories", "WHERE source=?", (LEGACY_EVOLUTION_SOURCE,)),
        "bzz_experience": _count(connection, "memories", "WHERE source=?", (LEGACY_EXPERIENCE_SOURCE,)),
        "experience_episodes": _count(
            connection, "experience_episodes", "WHERE bot_id=?", (bot_id,)
        ),
        "review_candidates": _count(connection, "review_candidates"),
    }
    watermarks = {
        "bzz_pending_max_id": _max_id(
            connection, "memories", "WHERE source=?", (LEGACY_PENDING_SOURCE,)
        ),
        "review_candidates_max_id": _max_id(connection, "review_candidates"),
    }
    return counts, watermarks


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_run_start(
    connection: Any,
    *,
    started_at: float,
    counts: dict[str, int],
    watermarks: dict[str, int | None],
) -> int:
    with _write_transaction(connection) as tx:
        tx.execute(_RUN_TABLE_SQL)
        cursor = tx.execute(
            f"""INSERT INTO {_RUN_TABLE}
               (started_at, baseline_json, start_counts_json, watermarks_json, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (started_at, _json(dict(BASELINE_COUNTS)), _json(counts), _json(watermarks)),
        )
        return int(cursor.lastrowid)


def _record_run_finish(
    connection: Any,
    *,
    run_id: int,
    finished_at: float,
    status: str,
    end_counts: dict[str, int],
    result: dict[str, Any],
) -> None:
    with _write_transaction(connection) as tx:
        tx.execute(
            f"""UPDATE {_RUN_TABLE}
                SET status=?, finished_at=?, end_counts_json=?, result_json=?
                WHERE id=?""",
            (status, finished_at, _json(end_counts), _json(result), int(run_id)),
        )


def _memory_rows(
    connection: Any,
    *,
    source: str,
    max_id: int | None = None,
) -> list[tuple[Any, ...]]:
    if not _table_exists(connection, "memories"):
        return []
    columns = _columns(connection, "memories")
    if "id" not in columns or "source" not in columns:
        return []
    content = "content" if "content" in columns else "'' AS content"
    timestamp = "timestamp" if "timestamp" in columns else "NULL AS timestamp"
    where = ["source=?"]
    params: list[Any] = [source]
    if max_id is not None:
        where.append("id<=?")
        params.append(int(max_id))
    return connection.execute(
        f"SELECT id, {content}, {timestamp} FROM memories WHERE {' AND '.join(where)} ORDER BY id",
        params,
    ).fetchall()


def _insert_pending_candidate(connection: Any, row: tuple[Any, ...], *, now: float) -> str:
    memory_id, content, source_timestamp = row
    legacy_ref = f"memories:{int(memory_id)}"
    if not str(content or "").strip():
        return "skipped"
    timestamp = source_timestamp if source_timestamp is not None else now
    evidence = {
        "legacy": True,
        "legacy_kind": LEGACY_PENDING_SOURCE,
        "legacy_ref": legacy_ref,
        "source": LEGACY_PENDING_SOURCE,
        "source_memory_id": int(memory_id),
        "traceability": "unavailable",
        "traceability_reason": "legacy bzz_pending has no precise community/chapter/original/participant/perspective reference",
    }
    metadata = {
        "legacy": True,
        "legacy_source": LEGACY_PENDING_SOURCE,
        "projection_label": LEGACY_PROJECTION_LABELS["worldview_internalization"],
        "schema_version": 1,
    }
    cursor = connection.execute(
        """INSERT OR IGNORE INTO learning_candidates
           (bot_id, candidate_type, content, evidence_json, reason, source_fingerprint,
            review_status, legacy_kind, legacy_ref, metadata_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
        (
            LEGACY_BOT_ID,
            WORLDVIEW_INTERNALIZATION,
            str(content),
            _json(evidence),
            "legacy bzz_pending；来源不可精确追溯，需人工审核",
            legacy_ref,
            LEGACY_PENDING_SOURCE,
            legacy_ref,
            _json(metadata),
            float(timestamp),
            float(timestamp),
        ),
    )
    return "created" if int(getattr(cursor, "rowcount", 0) or 0) == 1 else "existing"


def migrate_legacy(
    connection: Any,
    *,
    bot_id: str = LEGACY_BOT_ID,
    now: Callable[[], float] | None = None,
    batch_size: int = 100,
    after_snapshot: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """按启动快照回填旧 ``bzz_pending``，并返回可审计对账报告。

    ``bzz_pending`` 的最大 id 在迁移开始时固定，后续批次始终带该水位条件，
    因此迁移期间新写入的记录留给下一次运行。每批独立提交，进程中断后可重复
    执行；唯一 ``legacy_ref`` 让重复运行只读到既有候选而不会重复创建。
    """
    if not str(bot_id or "").strip():
        raise ValueError("bot_id is required")
    if int(batch_size) < 1:
        raise ValueError("batch_size must be positive")
    now_fn = now or time.time
    started_at = float(now_fn())

    # schema 先完成，旧表始终保留；调用方也可以先自行初始化 schema。
    ensure_learning_schema(connection)
    start_counts, watermarks = _snapshot(connection, LEGACY_BOT_ID)
    differences = {
        key: start_counts[key] - int(BASELINE_COUNTS[key]) for key in BASELINE_COUNTS
    }
    run_id = _record_run_start(
        connection,
        started_at=started_at,
        counts=start_counts,
        watermarks=watermarks,
    )
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "baseline": dict(BASELINE_COUNTS),
        "start_counts": start_counts,
        "actual_counts": dict(start_counts),
        "differences": differences,
        "watermarks": watermarks,
        "created_candidates": 0,
        "existing_candidates": 0,
        "skipped": [],
        "status": "running",
    }

    try:
        if after_snapshot is not None:
            after_snapshot(connection)
        max_id = watermarks["bzz_pending_max_id"]
        rows = _memory_rows(connection, source=LEGACY_PENDING_SOURCE, max_id=max_id)
        size = max(1, int(batch_size))
        for start in range(0, len(rows), size):
            batch = rows[start : start + size]
            with _write_transaction(connection) as tx:
                for row in batch:
                    outcome = _insert_pending_candidate(tx, row, now=started_at)
                    if outcome == "created":
                        report["created_candidates"] += 1
                    elif outcome == "existing":
                        report["existing_candidates"] += 1
                    else:
                        report["skipped"].append(
                            {
                                "legacy_ref": f"memories:{int(row[0])}",
                                "reason": "legacy memory content is empty; mapping cannot be proven",
                            }
                        )
                # 直接 SQL 回填也遵守学习中心关系完整性状态协议。
                mark_learning_integrity_clean(tx)

        end_counts, _ = _snapshot(connection, LEGACY_BOT_ID)
        report["end_counts"] = end_counts
        report["status"] = "succeeded"
        report["finished_at"] = float(now_fn())
        _record_run_finish(
            connection,
            run_id=run_id,
            finished_at=report["finished_at"],
            status="succeeded",
            end_counts=end_counts,
            result=report,
        )
        logger.info(
            "[LearningCenter] legacy migration run=%s pending=%s created=%s existing=%s differences=%s",
            run_id,
            start_counts["bzz_pending"],
            report["created_candidates"],
            report["existing_candidates"],
            differences,
        )
        return report
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = str(exc)[:500]
        report["finished_at"] = float(now_fn())
        try:
            end_counts, _ = _snapshot(connection, LEGACY_BOT_ID)
            report["end_counts"] = end_counts
            _record_run_finish(
                connection,
                run_id=run_id,
                finished_at=report["finished_at"],
                status="failed",
                end_counts=end_counts,
                result=report,
            )
        except Exception:
            logger.exception("[LearningCenter] failed to record legacy migration failure run=%s", run_id)
        raise


def _memory_projection_rows(connection: Any, source: str) -> list[dict[str, Any]]:
    rows = _memory_rows(connection, source=source)
    return [
        {
            "id": int(row[0]),
            "content": row[1],
            "timestamp": row[2],
            "source": source,
        }
        for row in rows
    ]


def _episode_projection_rows(connection: Any, bot_id: str) -> list[dict[str, Any]]:
    if not _table_exists(connection, "experience_episodes"):
        return []
    columns = _columns(connection, "experience_episodes")
    if "id" not in columns or "bot_id" not in columns:
        return []
    selected = ["id", "bot_id"]
    for name in (
        "group_id", "user_id", "episode_type", "trigger_text", "bot_reply",
        "user_reaction", "outcome", "source_memory_ids", "created_at",
    ):
        if name in columns:
            selected.append(name)
    rows = connection.execute(
        f"SELECT {', '.join(selected)} FROM experience_episodes WHERE bot_id=? ORDER BY id",
        (bot_id,),
    ).fetchall()
    return [dict(zip(selected, row)) | {
        "projection_kind": "interaction_experience",
        "display_label": LEGACY_PROJECTION_LABELS["interaction_experience"],
    } for row in rows]


def read_legacy_projections(connection: Any, *, bot_id: str = LEGACY_BOT_ID) -> dict[str, list[dict[str, Any]]]:
    """只读返回旧生效历史、legacy 经历和指定 Bot 的互动经历。

    不创建投影表、不复制 memory、不更新索引；``experience_episodes`` 只按
    稳定 BotProfile.db_id 过滤，避免把其他 Bot 的互动经历混入白真真视图。
    """
    evolution = _memory_projection_rows(connection, LEGACY_EVOLUTION_SOURCE)
    for item in evolution:
        item.update(
            projection_kind="effective_history",
            display_label=LEGACY_PROJECTION_LABELS["effective_history"],
        )
    legacy_experience = _memory_projection_rows(connection, LEGACY_EXPERIENCE_SOURCE)
    for item in legacy_experience:
        item.update(
            projection_kind="legacy_history_experience",
            display_label=LEGACY_PROJECTION_LABELS["legacy_history_experience"],
        )
    return {
        "evolution_history": evolution,
        "legacy_experience_history": legacy_experience,
        "interaction_experiences": _episode_projection_rows(connection, bot_id),
    }


class LegacyMigrationService:
    """面向生命周期/脚本调用的薄封装；核心逻辑保持可直接测试。"""

    def __init__(self, connection: Any, *, bot_id: str = LEGACY_BOT_ID, now=None):
        self.connection = connection
        self.bot_id = bot_id
        self.now = now

    def migrate(self, **kwargs) -> dict[str, Any]:
        return migrate_legacy(self.connection, bot_id=self.bot_id, now=self.now, **kwargs)

    def projections(self) -> dict[str, list[dict[str, Any]]]:
        return read_legacy_projections(self.connection, bot_id=self.bot_id)


LegacyMigration = LegacyMigrationService


def run_legacy_migration(db_path: str, **kwargs) -> dict[str, Any]:
    """按路径执行一次 legacy 迁移；供离线维护脚本使用。"""
    connection = sqlite3.connect(db_path)
    try:
        return migrate_legacy(connection, **kwargs)
    finally:
        connection.close()


__all__ = [
    "BASELINE_COUNTS",
    "LEGACY_BOT_ID",
    "LEGACY_PENDING_SOURCE",
    "LEGACY_EVOLUTION_SOURCE",
    "LEGACY_EXPERIENCE_SOURCE",
    "LEGACY_PROJECTION_LABELS",
    "LegacyMigration",
    "LegacyMigrationService",
    "migrate_legacy",
    "read_legacy_projections",
    "run_legacy_migration",
]
