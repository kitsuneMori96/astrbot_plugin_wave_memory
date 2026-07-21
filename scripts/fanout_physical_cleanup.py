#!/usr/bin/env python3
"""Staged physical cleanup for fanout_duplicate memory rows.

Default is dry-run (readonly plan). Apply is allowed only on non-production
database copies with an explicit confirmation token.

Keeper rule:
  keep unmarked legacy_memory_id;
  delete multi-family map targets that are marked fanout_duplicate.

Never re-opens classified fanout promote.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

CONFIRMATION = "delete-fanout-duplicates"

# Refuse apply against live production DB paths (Windows/Linux).
_PRODUCTION_MARKERS = (
    "plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    "plugin_data\\astrbot_plugin_wave_memory\\wave_memory.db",
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
)

# (table, column) candidates for cascade cleanup of deleted memory ids.
_CASCADE_SPECS: tuple[tuple[str, str], ...] = (
    ("memory_tags", "memory_id"),
    ("scoped_memory_tags", "memory_id"),
    ("scoped_memory_effective_tags", "memory_id"),
    ("scoped_memory_tag_corrections", "memory_id"),
    ("memory_mentions", "memory_id"),
    ("memory_feedback", "memory_id"),
    ("tag_extraction_status", "memory_id"),
    ("facts", "source_memory_id"),
    ("scoped_facts", "source_memory_id"),
    ("scoped_fact_history", "source_memory_id"),
    ("jargon", "source_memory_id"),
    ("scoped_jargon", "source_memory_id"),
    ("scoped_beliefs", "source_memory_id"),
    ("relationship_events", "source_memory_id"),
    ("scoped_soul_relationship_events", "source_memory_id"),
    ("scope_recovery_memory_map", "target_memory_id"),
)


class FanoutPhysicalCleanupError(RuntimeError):
    """Raised when cleanup cannot proceed safely."""


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
        conn.execute("PRAGMA query_only=ON")
        return conn
    conn = sqlite3.connect(path.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    # Explicit memory-id cascades are applied by this script. Keep FK off so
    # missing optional parent tables (e.g. scoped_tags dictionary) do not block
    # staged sample DBs or partial schemas. Production still only deletes by id.
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def is_production_db_path(path: Path) -> bool:
    text = str(path.resolve()) if path.exists() else str(path)
    normalized = text.replace("\\", "/")
    for marker in _PRODUCTION_MARKERS:
        if marker.replace("\\", "/") in normalized:
            # staged copies under backups/ with different filename are OK
            if normalized.endswith("/wave_memory.db") or normalized.endswith("wave_memory.db"):
                # allow names like wave_memory.fanout-cleanup.sqlite3
                name = Path(normalized).name
                if name == "wave_memory.db":
                    return True
    return False


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _existing_cascades(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    present = _tables(conn)
    out: list[tuple[str, str]] = []
    for table, column in _CASCADE_SPECS:
        if table not in present:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column in cols:
            out.append((table, column))
    return out


def select_delete_ids(conn: sqlite3.Connection) -> list[int]:
    """Return memory ids safe to delete under the keeper rule."""
    rows = conn.execute(
        """
        SELECT DISTINCT map.target_memory_id
          FROM scope_recovery_memory_map map
          JOIN memories m ON m.id = map.target_memory_id
          JOIN memories leg ON leg.id = map.legacy_memory_id
         WHERE map.legacy_memory_id IN (
               SELECT legacy_memory_id
                 FROM scope_recovery_memory_map
                GROUP BY legacy_memory_id
               HAVING COUNT(*) > 1
         )
           AND m.provenance LIKE '%fanout_duplicate%'
           AND (leg.provenance NOT LIKE '%fanout_duplicate%' OR leg.provenance IS NULL)
           AND map.target_memory_id != map.legacy_memory_id
         ORDER BY map.target_memory_id
        """
    ).fetchall()
    return [int(r[0]) for r in rows if r and r[0] is not None]


def _count_refs(conn: sqlite3.Connection, table: str, column: str, ids: list[int]) -> int:
    if not ids:
        return 0
    total = 0
    batch = 800
    for i in range(0, len(ids), batch):
        chunk = ids[i : i + batch]
        placeholders = ",".join("?" for _ in chunk)
        total += int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders})",
                chunk,
            ).fetchone()[0]
        )
    return total


def plan_cleanup(db: Path) -> dict:
    if not db.is_file():
        raise FanoutPhysicalCleanupError(f"database_missing:{db}")
    conn = _connect(db, readonly=True)
    try:
        if "memories" not in _tables(conn) or "scope_recovery_memory_map" not in _tables(conn):
            raise FanoutPhysicalCleanupError("required_tables_missing")
        delete_ids = select_delete_ids(conn)
        cascades = _existing_cascades(conn)
        cascade_counts = {
            f"{table}.{column}": _count_refs(conn, table, column, delete_ids)
            for table, column in cascades
        }
        marked = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0]
        legacy_kept_sample = conn.execute(
            """
            SELECT map.legacy_memory_id
              FROM scope_recovery_memory_map map
              JOIN memories leg ON leg.id = map.legacy_memory_id
             WHERE map.legacy_memory_id IN (
                   SELECT legacy_memory_id FROM scope_recovery_memory_map
                    GROUP BY legacy_memory_id HAVING COUNT(*) > 1
                    LIMIT 5
             )
               AND (leg.provenance NOT LIKE '%fanout_duplicate%' OR leg.provenance IS NULL)
             GROUP BY map.legacy_memory_id
             LIMIT 5
            """
        ).fetchall()
        return {
            "mode": "dry-run",
            "db": str(db),
            "is_production_path": is_production_db_path(db),
            "delete_count": len(delete_ids),
            "marked_rows": int(marked or 0),
            "delete_ids_preview": delete_ids[:20],
            "legacy_kept_preview": [int(r[0]) for r in legacy_kept_sample],
            "cascade_counts": cascade_counts,
            "keeper_rule": "keep_unmarked_legacy_delete_all_marked_map_targets",
            "apply_allowed_here": not is_production_db_path(db),
            "confirmation_required": CONFIRMATION,
        }
    finally:
        conn.close()


def _delete_ids(conn: sqlite3.Connection, table: str, column: str, ids: list[int]) -> int:
    if not ids:
        return 0
    deleted = 0
    # Large vector-bearing rows are safer in smaller batches on multi-GB DBs.
    batch = 100 if table == "memories" else 400
    for i in range(0, len(ids), batch):
        chunk = ids[i : i + batch]
        placeholders = ",".join("?" for _ in chunk)
        try:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
                chunk,
            )
        except sqlite3.DatabaseError:
            # Fall back to one-by-one for the failing chunk to isolate bad rows.
            for mid in chunk:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE {column}=?",
                    (mid,),
                )
                deleted += int(cur.rowcount or 0)
            continue
        deleted += int(cur.rowcount or 0)
        if table == "memories" and i and i % 5000 == 0:
            conn.commit()
    return deleted


def _drop_memory_fts_triggers(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Drop memories FTS triggers so bulk deletes do not corrupt external-content FTS5."""
    rows = conn.execute(
        """
        SELECT name, sql FROM sqlite_master
         WHERE type='trigger' AND tbl_name='memories'
           AND name LIKE 'fts_memories_%'
        """
    ).fetchall()
    saved: list[tuple[str, str]] = []
    for name, sql in rows:
        if not name or not sql:
            continue
        saved.append((str(name), str(sql)))
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    return saved


def _restore_memory_fts_triggers(conn: sqlite3.Connection, saved: list[tuple[str, str]]) -> None:
    for _name, sql in saved:
        conn.execute(sql)


def _rebuild_memory_fts(conn: sqlite3.Connection) -> str:
    tables = _tables(conn)
    if "fts_memories" not in tables:
        return "skipped_no_fts_memories"
    # external-content FTS5 rebuild from content table
    conn.execute("INSERT INTO fts_memories(fts_memories) VALUES('rebuild')")
    return "rebuilt"


def apply_cleanup(db: Path, *, confirmation: str) -> dict:
    if confirmation != CONFIRMATION:
        raise FanoutPhysicalCleanupError(
            f"confirmation_required:{CONFIRMATION}"
        )
    if is_production_db_path(db):
        raise FanoutPhysicalCleanupError(
            "production_apply_forbidden:copy_to_staged_db_first"
        )
    plan = plan_cleanup(db)
    # Re-select inside the write connection for a consistent apply set.
    conn = _connect(db, readonly=False)
    fts_triggers: list[tuple[str, str]] = []
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        ids = select_delete_ids(conn)
        cascades = _existing_cascades(conn)
        cascade_deleted: dict[str, int] = {}
        # Cascade first (map last among cascades is fine; memories last).
        for table, column in cascades:
            if table == "memories":
                continue
            cascade_deleted[f"{table}.{column}"] = _delete_ids(conn, table, column, ids)
            conn.commit()
        # Disable FTS external-content triggers for bulk memory deletes.
        fts_triggers = _drop_memory_fts_triggers(conn)
        conn.commit()
        memories_deleted = _delete_ids(conn, "memories", "id", ids)
        conn.commit()
        fts_status = _rebuild_memory_fts(conn)
        _restore_memory_fts_triggers(conn, fts_triggers)
        conn.commit()
        remaining_marked = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0]
        remaining_multi = conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT 1 FROM scope_recovery_memory_map
                    GROUP BY legacy_memory_id HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0]
        return {
            "mode": "apply",
            "db": str(db),
            "planned_delete_count": plan["delete_count"],
            "memories_deleted": memories_deleted,
            "cascade_deleted": cascade_deleted,
            "remaining_marked": int(remaining_marked or 0),
            "remaining_multi_target_families": int(remaining_multi or 0),
            "fts_triggers_dropped": [name for name, _sql in fts_triggers],
            "fts_status": fts_status,
            "completed_at": time.time(),
            "note": "vector rebuild and VACUUM remain separate follow-ups",
        }
    except Exception:
        conn.rollback()
        # Best-effort restore triggers if drop already happened in this connection.
        try:
            if fts_triggers:
                _restore_memory_fts_triggers(conn, fts_triggers)
                conn.commit()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="sqlite path (prod dry-run OK; apply only staged)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply deletes (non-production only)",
    )
    parser.add_argument(
        "--confirmation",
        default="",
        help=f"required for --apply: {CONFIRMATION}",
    )
    args = parser.parse_args(argv)
    db = Path(args.db)
    if args.apply:
        report = apply_cleanup(db, confirmation=args.confirmation)
    else:
        report = plan_cleanup(db)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
