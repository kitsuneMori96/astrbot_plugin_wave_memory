"""Cleanup legacy WaveMemory social/jargon/belief/fact dirty data.

Safe by default: --dry-run only reports counts. --apply writes changes and creates a backup
unless --no-backup is explicitly used by tests or controlled tooling.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from sqlite_runtime_guard import assert_astrbot_stopped
except ModuleNotFoundError:  # package import from repository root
    from scripts.sqlite_runtime_guard import assert_astrbot_stopped

COMMON_WORDS = {
    "肚腩", "朋友圈", "今天", "明天", "昨天", "大家", "东西", "问题", "时候", "感觉",
    "可以", "不是", "没有", "这个", "那个", "什么", "一下", "真的", "因为", "所以",
}
BOT_SUBJECTS = {"羽书", "白真真", "yushu", "baizz", "2500447291", "1336495069", "626751255"}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if _table_exists(conn, table) and column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _json_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"legacy_metadata_value": value}
    except Exception:
        return {"legacy_metadata_raw": raw}


def analyze_database(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        report = {
            "affinity_legacy_neutral": 0,
            "affinity_legacy_unverified": 0,
            "jargon_empty_or_unknown": 0,
            "jargon_common_word": 0,
            "belief_pending_legacy": 0,
            "facts_suspicious": 0,
        }
        if _table_exists(conn, "user_profiles"):
            report["affinity_legacy_neutral"] = conn.execute(
                """SELECT COUNT(*) FROM user_profiles
                   WHERE affection=50 AND COALESCE(metadata, '{}')='{}'
                     AND COALESCE(interaction_count, 0)=0"""
            ).fetchone()[0]
            report["affinity_legacy_unverified"] = conn.execute(
                """SELECT COUNT(*) FROM user_profiles
                   WHERE affection=50 AND COALESCE(metadata, '{}')='{}'
                     AND COALESCE(interaction_count, 0)>0"""
            ).fetchone()[0]
        if _table_exists(conn, "jargon"):
            cols = _columns(conn, "jargon")
            meaning_col = "meaning" if "meaning" in cols else "''"
            is_jargon_expr = "is_jargon IS NULL" if "is_jargon" in cols else "1=1"
            report["jargon_empty_or_unknown"] = conn.execute(
                f"""SELECT COUNT(*) FROM jargon
                    WHERE ({meaning_col} IS NULL OR TRIM({meaning_col})='') OR {is_jargon_expr}"""
            ).fetchone()[0]
            if "word" in cols:
                placeholders = ",".join("?" for _ in COMMON_WORDS)
                report["jargon_common_word"] = conn.execute(
                    f"SELECT COUNT(*) FROM jargon WHERE word IN ({placeholders})",
                    tuple(COMMON_WORDS),
                ).fetchone()[0]
        if _table_exists(conn, "beliefs"):
            cols = _columns(conn, "beliefs")
            if "status" in cols and "sources" in cols:
                strength_filter = "AND COALESCE(strength, 0) <= 0.41" if "strength" in cols else ""
                report["belief_pending_legacy"] = conn.execute(
                    f"""SELECT COUNT(*) FROM beliefs
                       WHERE status='pending' {strength_filter}
                         AND COALESCE(sources,'') NOT IN ('', '[]')"""
                ).fetchone()[0]
        if _table_exists(conn, "facts"):
            cols = _columns(conn, "facts")
            if {"subject", "object"}.issubset(cols):
                placeholders = ",".join("?" for _ in BOT_SUBJECTS)
                report["facts_suspicious"] = conn.execute(
                    f"""SELECT COUNT(*) FROM facts
                        WHERE COALESCE(confidence, 1.0) <= 0.5
                          AND (subject IN ({placeholders}) OR LENGTH(COALESCE(object,'')) > 180)""",
                    tuple(BOT_SUBJECTS),
                ).fetchone()[0]
        return report
    finally:
        conn.close()


def _backup(db_path: str) -> str:
    src = Path(db_path)
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"pre_cleanup_{stamp}.db"
    shutil.copy2(src, dest)
    return str(dest)


def apply_cleanup(db_path: str, backup: bool = True) -> dict[str, Any]:
    backup_path = _backup(db_path) if backup else ""
    conn = sqlite3.connect(db_path)
    try:
        now = time.time()
        if _table_exists(conn, "user_profiles"):
            rows = conn.execute(
                """SELECT id, metadata FROM user_profiles
                   WHERE affection=50 AND COALESCE(metadata, '{}')='{}'
                     AND COALESCE(interaction_count, 0)=0"""
            ).fetchall()
            for row_id, meta_raw in rows:
                meta = _json_obj(meta_raw)
                meta.update({
                    "legacy_neutral": True,
                    "excluded_from_affinity_query": True,
                    "cleaned_at": now,
                    "clean_reason": "legacy default 50 without evidence",
                })
                conn.execute(
                    "UPDATE user_profiles SET affection=0, metadata=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False), row_id),
                )

            rows = conn.execute(
                """SELECT id, metadata FROM user_profiles
                   WHERE affection=50 AND COALESCE(metadata, '{}')='{}'
                     AND COALESCE(interaction_count, 0)>0"""
            ).fetchall()
            for row_id, meta_raw in rows:
                meta = _json_obj(meta_raw)
                meta.update({
                    "legacy_unverified": True,
                    "excluded_from_affinity_query": True,
                    "cleaned_at": now,
                    "clean_reason": "legacy 50 with interaction_count but no dimensions",
                })
                conn.execute(
                    "UPDATE user_profiles SET metadata=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False), row_id),
                )

        if _table_exists(conn, "jargon"):
            _ensure_column(conn, "jargon", "status", "TEXT DEFAULT 'pending'")
            _ensure_column(conn, "jargon", "reject_reason", "TEXT")
            cols = _columns(conn, "jargon")
            if "meaning" in cols:
                conn.execute(
                    "UPDATE jargon SET status='pending' WHERE meaning IS NULL OR TRIM(meaning)=''"
                )
            if "is_jargon" in cols:
                conn.execute("UPDATE jargon SET status='pending' WHERE is_jargon IS NULL")
            if "word" in cols:
                placeholders = ",".join("?" for _ in COMMON_WORDS)
                conn.execute(
                    f"UPDATE jargon SET status='rejected', is_jargon=0, reject_reason='common word cleanup' WHERE word IN ({placeholders})",
                    tuple(COMMON_WORDS),
                )

        if _table_exists(conn, "beliefs"):
            cols = _columns(conn, "beliefs")
            if "status" in cols and "sources" in cols:
                strength_filter = "AND COALESCE(strength, 0) <= 0.41" if "strength" in cols else ""
                conn.execute(
                    f"""UPDATE beliefs SET status='pending_legacy'
                       WHERE status='pending' {strength_filter}
                         AND COALESCE(sources,'') NOT IN ('', '[]')"""
                )

        if _table_exists(conn, "facts"):
            cols = _columns(conn, "facts")
            if "confidence" in cols and {"subject", "object"}.issubset(cols):
                placeholders = ",".join("?" for _ in BOT_SUBJECTS)
                conn.execute(
                    f"""UPDATE facts SET confidence=MIN(confidence, 0.2)
                        WHERE COALESCE(confidence, 1.0) <= 0.5
                          AND (subject IN ({placeholders}) OR LENGTH(COALESCE(object,'')) > 180)""",
                    tuple(BOT_SUBJECTS),
                )

        conn.commit()
        report = analyze_database(db_path)
        report["backup_path"] = backup_path
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean WaveMemory legacy social data")
    parser.add_argument("--db", required=True, help="Path to wave_memory.db")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Only report counts")
    mode.add_argument("--apply", action="store_true", help="Apply cleanup")
    parser.add_argument("--no-backup", action="store_true", help="Do not create backup (tests only)")
    args = parser.parse_args()

    if args.dry_run:
        report = analyze_database(args.db)
    else:
        assert_astrbot_stopped("apply cleanup legacy social data")
        report = apply_cleanup(args.db, backup=not args.no_backup)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
