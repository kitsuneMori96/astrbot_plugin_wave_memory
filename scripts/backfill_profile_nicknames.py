#!/usr/bin/env python3
"""Backfill empty user_profiles.nickname from live chat + person_registry.

Default mode is dry-run.  Use --apply to write.  Always scoped to one bot/group
when those flags are provided; never invents cross-group identity.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    conn = sqlite3.connect(path.as_posix(), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def collect_candidates(conn: sqlite3.Connection, *, bot_id: str | None, group_id: str | None) -> list[dict]:
    profile_sql = """
        SELECT bot_id, group_id, user_id, COALESCE(nickname, ''), COALESCE(interaction_count, 0)
          FROM user_profiles
         WHERE COALESCE(TRIM(nickname), '') = ''
    """
    params: list[object] = []
    if bot_id:
        profile_sql += " AND bot_id = ?"
        params.append(bot_id)
    if group_id:
        profile_sql += " AND group_id = ?"
        params.append(group_id)
    profiles = conn.execute(profile_sql, params).fetchall()

    registry = {
        str(qq): str(name or "").strip()
        for qq, name in conn.execute(
            "SELECT qq_id, display_name FROM person_registry WHERE COALESCE(TRIM(display_name), '') != ''"
        ).fetchall()
    }

    # Best sender_name per (bot, group, sender_id)
    chat_names: dict[tuple[str, str, str], str] = {}
    chat_sql = """
        SELECT bot_id, group_id, sender_id, sender_name, COUNT(*) AS cnt
          FROM memories
         WHERE COALESCE(TRIM(sender_id), '') != ''
           AND COALESCE(TRIM(sender_name), '') != ''
           AND COALESCE(quarantine, 0) = 0
    """
    chat_params: list[object] = []
    if bot_id:
        chat_sql += " AND bot_id = ?"
        chat_params.append(bot_id)
    if group_id:
        chat_sql += " AND group_id = ?"
        chat_params.append(group_id)
    chat_sql += " GROUP BY bot_id, group_id, sender_id, sender_name"
    scores: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for row_bot, row_group, sender_id, sender_name, cnt in conn.execute(chat_sql, chat_params).fetchall():
        key = (str(row_bot or ""), str(row_group or ""), str(sender_id or ""))
        scores[key][str(sender_name)] += int(cnt or 0)
    for key, counter in scores.items():
        chat_names[key] = counter.most_common(1)[0][0]

    updates: list[dict] = []
    for row_bot, row_group, user_id, nickname, interactions in profiles:
        uid = str(user_id or "")
        if not uid:
            continue
        candidate = chat_names.get((str(row_bot or ""), str(row_group or ""), uid)) or registry.get(uid) or ""
        candidate = str(candidate).strip()
        if not candidate:
            continue
        updates.append({
            "bot_id": row_bot,
            "group_id": row_group,
            "user_id": uid,
            "old_nickname": nickname,
            "new_nickname": candidate,
            "interaction_count": int(interactions or 0),
            "source": "memories.sender_name" if (str(row_bot or ""), str(row_group or ""), uid) in chat_names else "person_registry",
        })
    updates.sort(key=lambda item: (-item["interaction_count"], item["bot_id"], item["group_id"], item["user_id"]))
    return updates


def apply_updates(conn: sqlite3.Connection, updates: list[dict]) -> int:
    changed = 0
    for item in updates:
        cur = conn.execute(
            """UPDATE user_profiles
                  SET nickname = ?
                WHERE bot_id = ? AND group_id = ? AND user_id = ?
                  AND COALESCE(TRIM(nickname), '') = ''""",
            (item["new_nickname"], item["bot_id"], item["group_id"], item["user_id"]),
        )
        changed += int(cur.rowcount or 0)
    conn.commit()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to wave_memory.db")
    parser.add_argument("--bot-id", default="", help="optional bot filter, e.g. yushu")
    parser.add_argument("--group-id", default="", help="optional group filter")
    parser.add_argument("--apply", action="store_true", help="write updates; default is dry-run")
    parser.add_argument("--limit", type=int, default=30, help="preview rows in dry-run output")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")

    conn = _connect(db_path, readonly=not args.apply)
    try:
        updates = collect_candidates(
            conn,
            bot_id=args.bot_id or None,
            group_id=args.group_id or None,
        )
        payload = {
            "mode": "apply" if args.apply else "dry-run",
            "candidate_count": len(updates),
            "preview": updates[: max(0, args.limit)],
        }
        if args.apply:
            payload["updated_rows"] = apply_updates(conn, updates)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
