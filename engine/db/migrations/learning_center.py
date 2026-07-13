"""通用学习中心四张核心表的幂等、纯增量 schema 迁移。"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from contextlib import contextmanager

from .book_experience import ensure_book_experience_schema

logger = logging.getLogger(__name__)

_TABLE_ORDER = (
    "learning_sources",
    "learning_jobs",
    "learning_candidates",
    "learning_promotions",
)
_SCHEMA_COMPONENT = "general_learning_center"
_SCHEMA_VERSION = 2
_LEDGER_TABLE = "learning_schema_ledger"
_INTEGRITY_STATE_TABLE = "learning_integrity_state"
_LEDGER_SQL = f"""
    CREATE TABLE IF NOT EXISTS {_LEDGER_TABLE} (
        component TEXT PRIMARY KEY,
        version INTEGER NOT NULL,
        schema_cookie INTEGER NOT NULL
    )
"""
_INTEGRITY_STATE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {_INTEGRITY_STATE_TABLE} (
        component TEXT PRIMARY KEY,
        dirty INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0, 1))
    )
"""
_RELATION_GUARD_TRIGGERS = {
    f"trg_{table}_schema_dirty_{event.lower()}": (event, table)
    for table in _TABLE_ORDER
    for event in ("INSERT", "UPDATE", "DELETE")
}

_CREATE_TABLES = {
    "learning_sources": """
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            config_json TEXT NOT NULL DEFAULT '{{}}',
            cursor_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """,
    "learning_jobs": """
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            candidate_type TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            schedule_json TEXT NOT NULL DEFAULT '{{}}',
            policy_json TEXT NOT NULL DEFAULT '{{}}',
            last_run_status TEXT NOT NULL DEFAULT 'never',
            last_started_at REAL,
            last_finished_at REAL,
            last_error TEXT,
            lease_token TEXT,
            lease_until REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(source_id) REFERENCES learning_sources(id)
        )
    """,
    "learning_candidates": """
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            source_id INTEGER,
            job_id INTEGER,
            candidate_type TEXT NOT NULL,
            content TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{{}}',
            reason TEXT,
            source_fingerprint TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (review_status IN ('pending','approved','rejected','ignored','delegated')),
            reviewer TEXT,
            reviewed_at REAL,
            review_note TEXT,
            legacy_kind TEXT,
            legacy_ref TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{{}}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(source_id) REFERENCES learning_sources(id),
            FOREIGN KEY(job_id) REFERENCES learning_jobs(id)
        )
    """,
    "learning_promotions": """
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            bot_id TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            promotion_status TEXT NOT NULL DEFAULT 'queued'
                CHECK (promotion_status IN ('queued','running','succeeded','retryable_failed','terminal_failed','delegated','waiting_dedicated_review')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            target_id TEXT,
            error_code TEXT,
            error_message TEXT,
            requested_by TEXT,
            started_at REAL,
            finished_at REAL,
            metadata_json TEXT NOT NULL DEFAULT '{{}}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES learning_candidates(id)
        )
    """,
}

_EXPECTED_COLUMNS = {
    "learning_sources": [
        ("id", "INTEGER", 0, None, 1), ("bot_id", "TEXT", 1, None, 0),
        ("source_type", "TEXT", 1, None, 0), ("name", "TEXT", 1, None, 0),
        ("enabled", "INTEGER", 1, "1", 0), ("config_json", "TEXT", 1, "'{}'", 0),
        ("cursor_json", "TEXT", 0, None, 0), ("created_at", "REAL", 1, None, 0),
        ("updated_at", "REAL", 1, None, 0),
    ],
    "learning_jobs": [
        ("id", "INTEGER", 0, None, 1), ("bot_id", "TEXT", 1, None, 0),
        ("source_id", "INTEGER", 1, None, 0), ("candidate_type", "TEXT", 1, None, 0),
        ("name", "TEXT", 1, None, 0), ("enabled", "INTEGER", 1, "1", 0),
        ("schedule_json", "TEXT", 1, "'{}'", 0), ("policy_json", "TEXT", 1, "'{}'", 0),
        ("last_run_status", "TEXT", 1, "'never'", 0), ("last_started_at", "REAL", 0, None, 0),
        ("last_finished_at", "REAL", 0, None, 0), ("last_error", "TEXT", 0, None, 0),
        ("lease_token", "TEXT", 0, None, 0), ("lease_until", "REAL", 0, None, 0),
        ("created_at", "REAL", 1, None, 0), ("updated_at", "REAL", 1, None, 0),
    ],
    "learning_candidates": [
        ("id", "INTEGER", 0, None, 1), ("bot_id", "TEXT", 1, None, 0),
        ("source_id", "INTEGER", 0, None, 0), ("job_id", "INTEGER", 0, None, 0),
        ("candidate_type", "TEXT", 1, None, 0), ("content", "TEXT", 1, None, 0),
        ("evidence_json", "TEXT", 1, "'{}'", 0), ("reason", "TEXT", 0, None, 0),
        ("source_fingerprint", "TEXT", 1, None, 0),
        ("review_status", "TEXT", 1, "'pending'", 0), ("reviewer", "TEXT", 0, None, 0),
        ("reviewed_at", "REAL", 0, None, 0), ("review_note", "TEXT", 0, None, 0),
        ("legacy_kind", "TEXT", 0, None, 0), ("legacy_ref", "TEXT", 0, None, 0),
        ("metadata_json", "TEXT", 1, "'{}'", 0), ("created_at", "REAL", 1, None, 0),
        ("updated_at", "REAL", 1, None, 0),
    ],
    "learning_promotions": [
        ("id", "INTEGER", 0, None, 1), ("candidate_id", "INTEGER", 1, None, 0),
        ("bot_id", "TEXT", 1, None, 0), ("target_kind", "TEXT", 1, None, 0),
        ("idempotency_key", "TEXT", 1, None, 0),
        ("promotion_status", "TEXT", 1, "'queued'", 0),
        ("attempt_count", "INTEGER", 1, "0", 0), ("target_id", "TEXT", 0, None, 0),
        ("error_code", "TEXT", 0, None, 0), ("error_message", "TEXT", 0, None, 0),
        ("requested_by", "TEXT", 0, None, 0), ("started_at", "REAL", 0, None, 0),
        ("finished_at", "REAL", 0, None, 0), ("metadata_json", "TEXT", 1, "'{}'", 0),
        ("created_at", "REAL", 1, None, 0), ("updated_at", "REAL", 1, None, 0),
    ],
}

_NO_ACTION_FK = ("NO ACTION", "NO ACTION", "NONE")
_EXPECTED_FOREIGN_KEYS = {
    "learning_sources": set(),
    "learning_jobs": {("learning_sources", "source_id", "id", *_NO_ACTION_FK)},
    "learning_candidates": {
        ("learning_sources", "source_id", "id", *_NO_ACTION_FK),
        ("learning_jobs", "job_id", "id", *_NO_ACTION_FK),
    },
    "learning_promotions": {("learning_candidates", "candidate_id", "id", *_NO_ACTION_FK)},
}

_STATUS_CHECKS = {
    "learning_candidates": (
        "review_status",
        {"pending", "approved", "rejected", "ignored", "delegated"},
    ),
    "learning_promotions": (
        "promotion_status",
        {
            "queued", "running", "succeeded", "retryable_failed", "terminal_failed",
            "delegated", "waiting_dedicated_review",
        },
    ),
}

_MANAGED_INDEXES = {
    "uq_learning_source_identity": ("learning_sources", True, True, ("bot_id", "source_type", "name")),
    "idx_learning_sources_bot_enabled": ("learning_sources", False, False, ("bot_id", "enabled", "source_type")),
    "idx_learning_jobs_bot_enabled": ("learning_jobs", False, False, ("bot_id", "enabled", "candidate_type")),
    "idx_learning_jobs_source": ("learning_jobs", False, False, ("bot_id", "source_id")),
    "idx_learning_jobs_run_status": ("learning_jobs", False, False, ("bot_id", "last_run_status", "lease_until")),
    "uq_learning_candidate_fingerprint": ("learning_candidates", True, True, ("bot_id", "candidate_type", "source_fingerprint")),
    "uq_learning_candidate_legacy": ("learning_candidates", True, True, ("legacy_kind", "legacy_ref")),
    "idx_learning_candidates_bot_review_time": ("learning_candidates", False, False, ("bot_id", "review_status", "created_at")),
    "idx_learning_candidates_bot_type_time": ("learning_candidates", False, False, ("bot_id", "candidate_type", "created_at")),
    "idx_learning_candidates_source_job": ("learning_candidates", False, False, ("bot_id", "source_id", "job_id")),
    "uq_learning_promotion_idempotency": ("learning_promotions", True, True, ("idempotency_key",)),
    "idx_learning_promotions_bot_status_time": ("learning_promotions", False, False, ("bot_id", "promotion_status", "created_at")),
    "idx_learning_promotions_candidate": ("learning_promotions", False, False, ("bot_id", "candidate_id", "target_kind")),
}

_INDEX_DESC_COLUMNS = {
    "idx_learning_candidates_bot_review_time": {"created_at"},
    "idx_learning_candidates_bot_type_time": {"created_at"},
    "idx_learning_promotions_bot_status_time": {"created_at"},
}

_EXPECTED_INDEX_WHERE = {
    "uq_learning_source_identity": "bot_id!=''andsource_type!=''andname!=''",
    "uq_learning_candidate_fingerprint": "bot_id!=''andcandidate_type!=''andsource_fingerprint!=''",
    "uq_learning_candidate_legacy": "legacy_kindisnotnullandlegacy_refisnotnull",
    "uq_learning_promotion_idempotency": "idempotency_key!=''",
}

_INDEX_SQL = """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_source_identity
        ON learning_sources(bot_id, source_type, name)
        WHERE bot_id != '' AND source_type != '' AND name != '';
    CREATE INDEX IF NOT EXISTS idx_learning_sources_bot_enabled
        ON learning_sources(bot_id, enabled, source_type);
    CREATE INDEX IF NOT EXISTS idx_learning_jobs_bot_enabled
        ON learning_jobs(bot_id, enabled, candidate_type);
    CREATE INDEX IF NOT EXISTS idx_learning_jobs_source
        ON learning_jobs(bot_id, source_id);
    CREATE INDEX IF NOT EXISTS idx_learning_jobs_run_status
        ON learning_jobs(bot_id, last_run_status, lease_until);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_candidate_fingerprint
        ON learning_candidates(bot_id, candidate_type, source_fingerprint)
        WHERE bot_id != '' AND candidate_type != '' AND source_fingerprint != '';
    CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_candidate_legacy
        ON learning_candidates(legacy_kind, legacy_ref)
        WHERE legacy_kind IS NOT NULL AND legacy_ref IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_learning_candidates_bot_review_time
        ON learning_candidates(bot_id, review_status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_learning_candidates_bot_type_time
        ON learning_candidates(bot_id, candidate_type, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_learning_candidates_source_job
        ON learning_candidates(bot_id, source_id, job_id);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_promotion_idempotency
        ON learning_promotions(idempotency_key)
        WHERE idempotency_key != '';
    CREATE INDEX IF NOT EXISTS idx_learning_promotions_bot_status_time
        ON learning_promotions(bot_id, promotion_status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_learning_promotions_candidate
        ON learning_promotions(bot_id, candidate_id, target_kind);
"""


def _table_exists(connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _column_signature(connection, table: str) -> list[tuple]:
    return [(row[1], row[2], row[3], row[4], row[5]) for row in connection.execute(f"PRAGMA table_info({table})")]


def _foreign_key_signature(connection, table: str) -> set[tuple[str, str, str, str, str, str]]:
    return {
        (row[2], row[3], row[4], row[5], row[6], row[7])
        for row in connection.execute(f"PRAGMA foreign_key_list({table})")
    }


def _check_values(sql: str, column: str) -> list[str] | None:
    match = re.search(
        rf"CHECK\s*\(\s*{re.escape(column)}\s+IN\s*\((.*?)\)\s*\)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    quoted = re.findall(r"'([^']*)'", match.group(1))
    if quoted:
        return quoted
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def _schema_is_canonical(connection, table: str) -> bool:
    if _column_signature(connection, table) != _EXPECTED_COLUMNS[table]:
        return False
    if _foreign_key_signature(connection, table) != _EXPECTED_FOREIGN_KEYS[table]:
        return False
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    sql = sql_row[0] if sql_row else ""
    if table in {"learning_sources", "learning_jobs"}:
        enabled_values = _check_values(sql, "enabled")
        if enabled_values is None or set(enabled_values) != {"0", "1"} or len(enabled_values) != 2:
            return False
    if table in _STATUS_CHECKS:
        column, expected = _STATUS_CHECKS[table]
        actual = _check_values(sql, column)
        if actual is None or set(actual) != expected or len(actual) != len(expected):
            return False
    return True


class LearningSchemaMigrationError(RuntimeError):
    pass


def _fail(table: str, reason: str) -> None:
    raise LearningSchemaMigrationError(f"{table}: {reason}")


def _row_count(connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _require_data_columns(connection, table: str, required: set[str]) -> set[str]:
    columns = _columns(connection, table)
    if _row_count(connection, table):
        missing = sorted(required - columns)
        if missing:
            _fail(table, f"rows cannot be mapped because required columns are missing: {', '.join(missing)}")
    return columns


def _reject_duplicates(connection, table: str, columns: tuple[str, ...], where: str, label: str) -> None:
    column_sql = ", ".join(columns)
    row = connection.execute(
        f"SELECT {column_sql}, COUNT(*) FROM {table} WHERE {where} "
        f"GROUP BY {column_sql} HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if row:
        _fail(table, f"duplicate {label} cannot be preserved under canonical unique constraint")


def _validate_stable_bot_ids(connection, table: str) -> None:
    if not _row_count(connection, table):
        return
    columns = _columns(connection, table)
    if "bot_id" not in columns:
        _fail(table, "rows cannot be mapped because bot_id is missing")
    row_identifier = "id" if "id" in columns else "rowid"
    bad = connection.execute(
        f"""SELECT {row_identifier} FROM {table}
            WHERE bot_id IS NULL
               OR TRIM(CAST(bot_id AS TEXT))=''
               OR CAST(bot_id AS TEXT)!=TRIM(CAST(bot_id AS TEXT))
               OR TRIM(CAST(bot_id AS TEXT)) NOT GLOB '*[^0-9]*'
            LIMIT 1"""
    ).fetchone()
    if bad:
        _fail(table, f"row {bad[0]} has invalid bot_id; BotProfile.db_id is required, not name or QQ number")


def _preflight_existing_data(connection, rebuild_tables: set[str]) -> None:
    """仅扫描待重建表，在任何 DDL 前证明其旧记录可一一映射。"""
    existing = {table for table in _TABLE_ORDER if _table_exists(connection, table)}
    for table in _TABLE_ORDER:
        if table in existing and table in rebuild_tables:
            _validate_stable_bot_ids(connection, table)
    source_columns = _columns(connection, "learning_sources") if "learning_sources" in existing else set()
    if "learning_sources" in rebuild_tables:
        source_columns = _require_data_columns(
            connection, "learning_sources", {"id", "bot_id"}
        )
        if {"bot_id", "source_type", "name"}.issubset(source_columns):
            _reject_duplicates(
                connection, "learning_sources", ("bot_id", "source_type", "name"),
                "bot_id!='' AND source_type!='' AND name!=''", "source identity",
            )
        if "enabled" in source_columns:
            bad = connection.execute(
                "SELECT id FROM learning_sources WHERE enabled IS NULL OR enabled NOT IN (0,1) LIMIT 1"
            ).fetchone()
            if bad:
                _fail("learning_sources", f"invalid enabled value for row {bad[0]}")

    if "learning_jobs" in rebuild_tables:
        job_columns = _require_data_columns(
            connection,
            "learning_jobs",
            {"id", "bot_id", "source_id", "candidate_type", "name"},
        )
        if _row_count(connection, "learning_jobs"):
            if "learning_sources" not in existing or not {"id", "bot_id"}.issubset(source_columns):
                _fail("learning_jobs", "source association cannot be resolved")
            bad = connection.execute(
                """SELECT j.id FROM learning_jobs j LEFT JOIN learning_sources s ON s.id=j.source_id
                   WHERE j.source_id IS NULL OR s.id IS NULL OR s.bot_id!=j.bot_id LIMIT 1"""
            ).fetchone()
            if bad:
                _fail("learning_jobs", f"source association is missing or crosses bot_id for row {bad[0]}")
        if "enabled" in job_columns:
            bad = connection.execute(
                "SELECT id FROM learning_jobs WHERE enabled IS NULL OR enabled NOT IN (0,1) LIMIT 1"
            ).fetchone()
            if bad:
                _fail("learning_jobs", f"invalid enabled value for row {bad[0]}")

    if "learning_candidates" in rebuild_tables:
        candidate_columns = _require_data_columns(
            connection,
            "learning_candidates",
            {"id", "bot_id", "candidate_type", "content", "source_fingerprint"},
        )
        if _row_count(connection, "learning_candidates"):
            for foreign_column, target in (("source_id", "learning_sources"), ("job_id", "learning_jobs")):
                if foreign_column not in candidate_columns:
                    continue
                target_columns = _columns(connection, target) if target in existing else set()
                if target not in existing or not {"id", "bot_id"}.issubset(target_columns):
                    non_null = connection.execute(
                        f"SELECT id FROM learning_candidates WHERE {foreign_column} IS NOT NULL LIMIT 1"
                    ).fetchone()
                    if non_null:
                        _fail("learning_candidates", f"{foreign_column} association cannot be resolved")
                    continue
                bad = connection.execute(
                    f"""SELECT c.id FROM learning_candidates c LEFT JOIN {target} t ON t.id=c.{foreign_column}
                        WHERE c.{foreign_column} IS NOT NULL AND (t.id IS NULL OR t.bot_id!=c.bot_id) LIMIT 1"""
                ).fetchone()
                if bad:
                    _fail(
                        "learning_candidates",
                        f"{foreign_column} association is missing or crosses bot_id for row {bad[0]}",
                    )
        if {"bot_id", "candidate_type", "source_fingerprint"}.issubset(candidate_columns):
            _reject_duplicates(
                connection, "learning_candidates", ("bot_id", "candidate_type", "source_fingerprint"),
                "bot_id!='' AND candidate_type!='' AND source_fingerprint!=''", "candidate fingerprint",
            )
        if {"legacy_kind", "legacy_ref"}.issubset(candidate_columns):
            _reject_duplicates(
                connection, "learning_candidates", ("legacy_kind", "legacy_ref"),
                "legacy_kind IS NOT NULL AND legacy_ref IS NOT NULL", "legacy reference",
            )
        if "review_status" in candidate_columns:
            allowed = sorted(_STATUS_CHECKS["learning_candidates"][1])
            placeholders = ",".join("?" for _ in allowed)
            bad = connection.execute(
                f"SELECT id FROM learning_candidates WHERE review_status IS NULL OR review_status NOT IN ({placeholders}) LIMIT 1",
                allowed,
            ).fetchone()
            if bad:
                _fail("learning_candidates", f"invalid review_status for row {bad[0]}")

    if "learning_promotions" in rebuild_tables:
        promotion_columns = _require_data_columns(
            connection,
            "learning_promotions",
            {"id", "bot_id", "candidate_id", "target_kind", "idempotency_key"},
        )
        if _row_count(connection, "learning_promotions"):
            candidate_target_columns = (
                _columns(connection, "learning_candidates") if "learning_candidates" in existing else set()
            )
            if "learning_candidates" not in existing or not {"id", "bot_id"}.issubset(candidate_target_columns):
                _fail("learning_promotions", "candidate association cannot be resolved")
            bad = connection.execute(
                """SELECT p.id FROM learning_promotions p
                   LEFT JOIN learning_candidates c ON c.id=p.candidate_id
                   WHERE p.candidate_id IS NULL OR c.id IS NULL OR c.bot_id!=p.bot_id LIMIT 1"""
            ).fetchone()
            if bad:
                _fail("learning_promotions", f"candidate association is missing or crosses bot_id for row {bad[0]}")
        if "idempotency_key" in promotion_columns:
            _reject_duplicates(
                connection, "learning_promotions", ("idempotency_key",),
                "idempotency_key!=''", "promotion idempotency key",
            )
        if "promotion_status" in promotion_columns:
            allowed = sorted(_STATUS_CHECKS["learning_promotions"][1])
            placeholders = ",".join("?" for _ in allowed)
            bad = connection.execute(
                f"SELECT id FROM learning_promotions WHERE promotion_status IS NULL OR promotion_status NOT IN ({placeholders}) LIMIT 1",
                allowed,
            ).fetchone()
            if bad:
                _fail("learning_promotions", f"invalid promotion_status for row {bad[0]}")


def _value(columns: set[str], column: str, fallback: str, *, coalesce: bool = False) -> str:
    if column not in columns:
        return fallback
    return f"COALESCE({column}, {fallback})" if coalesce else column


def _copy_plan(table: str, columns: set[str]) -> tuple[list[str], list[str], str]:
    target_columns = [item[0] for item in _EXPECTED_COLUMNS[table]]
    if table == "learning_sources":
        values = [
            _value(columns, "id", "rowid"), _value(columns, "bot_id", "NULL"),
            _value(columns, "source_type", "''", coalesce=True), _value(columns, "name", "''", coalesce=True),
            f"CASE WHEN {_value(columns, 'enabled', '1')} IN (0,1) THEN {_value(columns, 'enabled', '1')} ELSE 1 END",
            _value(columns, "config_json", "'{}'", coalesce=True), _value(columns, "cursor_json", "NULL"),
            _value(columns, "created_at", "0", coalesce=True), _value(columns, "updated_at", "0", coalesce=True),
        ]
        return target_columns, values, ""
    if table == "learning_jobs":
        values = [
            _value(columns, "id", "rowid"), _value(columns, "bot_id", "NULL"),
            _value(columns, "source_id", "0", coalesce=True), _value(columns, "candidate_type", "''", coalesce=True),
            _value(columns, "name", "''", coalesce=True),
            f"CASE WHEN {_value(columns, 'enabled', '1')} IN (0,1) THEN {_value(columns, 'enabled', '1')} ELSE 1 END",
            _value(columns, "schedule_json", "'{}'", coalesce=True), _value(columns, "policy_json", "'{}'", coalesce=True),
            _value(columns, "last_run_status", "'never'", coalesce=True), _value(columns, "last_started_at", "NULL"),
            _value(columns, "last_finished_at", "NULL"), _value(columns, "last_error", "NULL"),
            _value(columns, "lease_token", "NULL"), _value(columns, "lease_until", "NULL"),
            _value(columns, "created_at", "0", coalesce=True), _value(columns, "updated_at", "0", coalesce=True),
        ]
        return target_columns, values, ""
    if table == "learning_candidates":
        bot_expr = _value(columns, "bot_id", "NULL")
        source_expr = _value(columns, "source_id", "NULL")
        job_expr = _value(columns, "job_id", "NULL")
        raw_status = _value(columns, "review_status", "'pending'", coalesce=True)
        status_expr = f"CASE WHEN {raw_status} IN ('pending','approved','rejected','ignored','delegated') THEN {raw_status} ELSE 'pending' END"
        values = [
            _value(columns, "id", "rowid"), bot_expr, source_expr, job_expr,
            _value(columns, "candidate_type", "''", coalesce=True), _value(columns, "content", "''", coalesce=True),
            _value(columns, "evidence_json", "'{}'", coalesce=True), _value(columns, "reason", "NULL"),
            _value(columns, "source_fingerprint", "''", coalesce=True), status_expr,
            _value(columns, "reviewer", "NULL"), _value(columns, "reviewed_at", "NULL"),
            _value(columns, "review_note", "NULL"), _value(columns, "legacy_kind", "NULL"),
            _value(columns, "legacy_ref", "NULL"), _value(columns, "metadata_json", "'{}'", coalesce=True),
            _value(columns, "created_at", "0", coalesce=True), _value(columns, "updated_at", "0", coalesce=True),
        ]
        return target_columns, values, ""
    raw_candidate = _value(columns, "candidate_id", "0", coalesce=True)
    bot_expr = _value(columns, "bot_id", "NULL")
    raw_status = _value(columns, "promotion_status", "'queued'", coalesce=True)
    status_expr = (
        f"CASE WHEN {raw_status} IN ('queued','running','succeeded','retryable_failed','terminal_failed',"
        f"'delegated','waiting_dedicated_review') THEN {raw_status} ELSE 'queued' END"
    )
    values = [
        _value(columns, "id", "rowid"), raw_candidate, bot_expr,
        _value(columns, "target_kind", "''", coalesce=True),
        _value(columns, "idempotency_key", "''", coalesce=True), status_expr,
        _value(columns, "attempt_count", "0", coalesce=True), _value(columns, "target_id", "NULL"),
        _value(columns, "error_code", "NULL"), _value(columns, "error_message", "NULL"),
        _value(columns, "requested_by", "NULL"), _value(columns, "started_at", "NULL"),
        _value(columns, "finished_at", "NULL"), _value(columns, "metadata_json", "'{}'", coalesce=True),
        _value(columns, "created_at", "0", coalesce=True), _value(columns, "updated_at", "0", coalesce=True),
    ]
    return target_columns, values, ""


def _rebuild_table(connection, table: str) -> None:
    temporary = f"__{table}_canonical"
    connection.execute(f"DROP TABLE IF EXISTS {temporary}")
    connection.execute(_CREATE_TABLES[table].format(table=temporary))
    columns = _columns(connection, table)
    targets, values, where = _copy_plan(table, columns)
    connection.execute(
        f"INSERT INTO {temporary} ({', '.join(targets)}) SELECT {', '.join(values)} FROM {table}{where}"
    )
    connection.execute(f"DROP TABLE {table}")
    connection.execute(f"ALTER TABLE {temporary} RENAME TO {table}")
    logger.info("[LearningCenter] rebuilt non-canonical table %s", table)


def _managed_index_matches(connection, name: str, definition: tuple) -> bool:
    table, expected_unique, expected_partial, expected_columns = definition
    index_row = next(
        (row for row in connection.execute(f"PRAGMA index_list({table})") if row[1] == name),
        None,
    )
    if not index_row:
        return False
    actual_columns = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({name})"))
    expected_xinfo = tuple(
        (column, column in _INDEX_DESC_COLUMNS.get(name, set()), "BINARY", True)
        for column in expected_columns
    )
    actual_xinfo = tuple(
        (row[2], bool(row[3]), str(row[4] or "").upper(), bool(row[5]))
        for row in connection.execute(f"PRAGMA index_xinfo({name})")
        if row[5]
    )
    if (
        bool(index_row[2]) != expected_unique
        or bool(index_row[4]) != expected_partial
        or actual_columns != expected_columns
        or actual_xinfo != expected_xinfo
    ):
        return False
    if expected_partial:
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        sql = sql_row[0] if sql_row and sql_row[0] else ""
        match = re.search(r"\bWHERE\b(.*)$", sql, flags=re.IGNORECASE | re.DOTALL)
        actual_where = re.sub(r"\s+", "", match.group(1).lower()) if match else ""
        if actual_where != _EXPECTED_INDEX_WHERE[name]:
            return False
    return True


def _create_indexes(connection) -> None:
    for name, definition in _MANAGED_INDEXES.items():
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if existing and not _managed_index_matches(connection, name, definition):
            connection.execute(f"DROP INDEX {name}")
            logger.info("[LearningCenter] dropped non-canonical managed index %s", name)
    for statement in _INDEX_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


def _schema_cookie(connection) -> int:
    return int(connection.execute("PRAGMA schema_version").fetchone()[0])


def _ledger_is_current(connection) -> bool:
    if not _table_exists(connection, _LEDGER_TABLE):
        return False
    row = connection.execute(
        f"SELECT version, schema_cookie FROM {_LEDGER_TABLE} WHERE component=?",
        (_SCHEMA_COMPONENT,),
    ).fetchone()
    return bool(
        row
        and int(row[0]) == _SCHEMA_VERSION
        and int(row[1]) == _schema_cookie(connection)
    )


def _record_ledger(connection) -> None:
    connection.execute(_LEDGER_SQL)
    cookie = _schema_cookie(connection)
    connection.execute(
        f"""INSERT INTO {_LEDGER_TABLE} (component, version, schema_cookie)
            VALUES (?, ?, ?)
            ON CONFLICT(component) DO UPDATE SET
                version=excluded.version,
                schema_cookie=excluded.schema_cookie""",
        (_SCHEMA_COMPONENT, _SCHEMA_VERSION, cookie),
    )


def learning_integrity_is_clean(connection) -> bool:
    """结构 ledger 与数据完整性状态分离；缺少状态按 dirty 处理。"""
    if not _table_exists(connection, _INTEGRITY_STATE_TABLE):
        return False
    row = connection.execute(
        f"SELECT dirty FROM {_INTEGRITY_STATE_TABLE} WHERE component=?",
        (_SCHEMA_COMPONENT,),
    ).fetchone()
    return bool(row and int(row[0]) == 0)


def mark_learning_integrity_clean(connection) -> None:
    """仅供已完成领域校验的 repository/迁移事务确认本次 DML 合法。"""
    if not _table_exists(connection, _INTEGRITY_STATE_TABLE):
        return
    connection.execute(
        f"""INSERT INTO {_INTEGRITY_STATE_TABLE} (component, dirty)
            VALUES (?, 0)
            ON CONFLICT(component) DO UPDATE SET dirty=0""",
        (_SCHEMA_COMPONENT,),
    )


def _drop_relation_guards(connection) -> None:
    for name in _RELATION_GUARD_TRIGGERS:
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")


def _create_relation_guards(connection) -> None:
    """普通 DML 仅标记数据待校验，绝不删除或伪造结构版本 ledger。"""
    for name, (event, table) in _RELATION_GUARD_TRIGGERS.items():
        connection.execute(
            f"""CREATE TRIGGER IF NOT EXISTS {name}
                AFTER {event} ON {table}
                BEGIN
                    INSERT INTO {_INTEGRITY_STATE_TABLE} (component, dirty)
                    VALUES ('{_SCHEMA_COMPONENT}', 1)
                    ON CONFLICT(component) DO UPDATE SET dirty=1;
                END"""
        )


def _validate_relations(connection) -> None:
    checks = (
        (
            "learning_jobs",
            """SELECT j.id FROM learning_jobs j LEFT JOIN learning_sources s ON s.id=j.source_id
               WHERE j.source_id IS NULL OR s.id IS NULL OR s.bot_id!=j.bot_id LIMIT 1""",
            "source association is missing or crosses bot_id",
        ),
        (
            "learning_candidates",
            """SELECT c.id FROM learning_candidates c
               LEFT JOIN learning_sources s ON s.id=c.source_id
               LEFT JOIN learning_jobs j ON j.id=c.job_id
               WHERE (c.source_id IS NOT NULL AND (s.id IS NULL OR s.bot_id!=c.bot_id))
                  OR (c.job_id IS NOT NULL AND (j.id IS NULL OR j.bot_id!=c.bot_id)) LIMIT 1""",
            "source/job association is missing or crosses bot_id",
        ),
        (
            "learning_promotions",
            """SELECT p.id FROM learning_promotions p
               LEFT JOIN learning_candidates c ON c.id=p.candidate_id
               WHERE p.candidate_id IS NULL OR c.id IS NULL OR c.bot_id!=p.bot_id LIMIT 1""",
            "candidate association is missing or crosses bot_id",
        ),
    )
    for table, sql, reason in checks:
        if not _table_exists(connection, table):
            continue
        bad = connection.execute(sql).fetchone()
        if bad:
            _fail(table, f"{reason} for row {bad[0]}")


def _foreign_key_violations(connection) -> set[tuple[object, ...]]:
    """返回迁移前已有的外键违规，供兼容旧库时做差分校验。"""
    return {tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()}


@contextmanager
def _migration_transaction(connection):
    """统一原生连接与 ConnectionManager 代理的迁移事务语义。"""
    transaction_factory = getattr(connection, "migration_transaction", None)
    if callable(transaction_factory):
        with transaction_factory() as tx:
            yield tx
        return
    if bool(getattr(connection, "in_transaction", False)):
        raise RuntimeError("connection already has an active transaction")
    original_foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
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
    finally:
        active_error = sys.exc_info()[0] is not None
        if connection.in_transaction:
            try:
                connection.rollback()
            except BaseException:
                if not active_error:
                    raise
        try:
            connection.execute(
                f"PRAGMA foreign_keys={'ON' if original_foreign_keys else 'OFF'}"
            )
        except BaseException:
            if not active_error:
                raise


def ensure_learning_schema(connection) -> None:
    """在单一 IMMEDIATE 事务中幂等建立 canonical schema。"""
    if bool(getattr(connection, "in_transaction", False)):
        raise RuntimeError("connection already has an active transaction")
    # 旧库可能已经存在与学习中心无关的孤儿元数据；迁移不能删除这些数据，
    # 但新建/重建的 learning_* 关系仍必须做完整外键校验。
    preexisting_unmanaged_violations = {
        violation
        for violation in _foreign_key_violations(connection)
        if str(violation[0]) not in _TABLE_ORDER
    }
    with _migration_transaction(connection) as tx:
        schema_current = _ledger_is_current(tx)
        integrity_clean = learning_integrity_is_clean(tx)
        if schema_current and integrity_clean:
            return

        existing = {table for table in _TABLE_ORDER if _table_exists(tx, table)}
        rebuild_tables = {
            table for table in existing if not _schema_is_canonical(tx, table)
        }
        preflight_tables = rebuild_tables if integrity_clean else existing
        if preflight_tables:
            _preflight_existing_data(tx, preflight_tables)
        _drop_relation_guards(tx)

        for table in _TABLE_ORDER:
            if table not in existing:
                tx.execute(_CREATE_TABLES[table].format(table=table))
            elif table in rebuild_tables:
                _rebuild_table(tx, table)
        _create_indexes(tx)
        _validate_relations(tx)
        tx.execute(_LEDGER_SQL)
        tx.execute(_INTEGRITY_STATE_SQL)
        _create_relation_guards(tx)
        violations = tx.execute("PRAGMA foreign_key_check").fetchall()
        new_violations = [
            tuple(violation)
            for violation in violations
            if tuple(violation) not in preexisting_unmanaged_violations
        ]
        if new_violations:
            table, rowid, parent, _ = new_violations[0]
            _fail(str(table), f"foreign key check failed for row {rowid} referencing {parent}")
        mark_learning_integrity_clean(tx)
        _record_ledger(tx)


def run_migration(db_path: str) -> bool:
    """运行 schema 迁移；兼容旧 ``review_candidates``，本任务不做 backfill。"""
    connection = sqlite3.connect(db_path)
    try:
        ensure_book_experience_schema(connection)
        ensure_learning_schema(connection)
        logger.info("[LearningCenter] schema migration completed")
        return True
    except Exception:
        connection.rollback()
        logger.exception("[LearningCenter] schema migration failed")
        return False
    finally:
        connection.close()


__all__ = [
    "ensure_learning_schema",
    "learning_integrity_is_clean",
    "mark_learning_integrity_clean",
    "run_migration",
]
