#!/usr/bin/env python3
"""Dry-run: derive shared_memory_grants candidates from fanout recovery map.

Default is **read-only**: never INSERT grants, never copy memories, never promote.

Ownership rule (same spirit as physical cleanup keeper):
  1. Prefer legacy_memory_id row if it exists and is not fanout_duplicate
  2. Else prefer the lowest target_memory_id that is not fanout_duplicate
  3. Else lowest target_memory_id (review bucket)

For each multi-target family, emit one grant candidate per *other* consumer
Scope that would need read access to the owner memory after fanout rows are gone.

Optional --apply writes grants only when:
  --apply --confirmation grant-from-fanout-map --writers-stopped
and only into a target DB that already has shared_memory_grants schema.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

CONFIRMATION = "grant-from-fanout-map"


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rw(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def _is_fanout_dup(provenance: str | None) -> bool:
    raw = provenance or ""
    return "fanout_duplicate" in raw or "group_bound_core_chat_fanout" in raw


def _parse_scope_key(key: str) -> dict[str, str] | None:
    parts = str(key or "").split("|")
    if len(parts) != 3:
        return None
    bot_id, session_id, visibility = parts[0], parts[1], parts[2]
    if visibility != "group" or not bot_id or not session_id:
        return None
    # session_id is typically "platform:group:<gid>" or "羽书:group:<gid>"
    group_id = session_id.rsplit(":", 1)[-1] if ":" in session_id else session_id
    return {
        "bot_id": bot_id,
        "session_id": session_id,
        "visibility": visibility,
        "group_id": str(group_id),
    }


def _load_memory_meta(conn: sqlite3.Connection, memory_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not memory_ids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    # chunk to avoid huge IN lists
    chunk = 800
    for i in range(0, len(memory_ids), chunk):
        part = memory_ids[i : i + chunk]
        ph = ",".join("?" * len(part))
        rows = conn.execute(
            f"""
            SELECT id, bot_id, session_id, visibility, group_id, provenance,
                   COALESCE(resolution_state, '')
              FROM memories WHERE id IN ({ph})
            """,
            part,
        ).fetchall()
        for r in rows:
            out[int(r[0])] = {
                "id": int(r[0]),
                "bot_id": str(r[1] or ""),
                "session_id": str(r[2] or ""),
                "visibility": str(r[3] or ""),
                "group_id": str(r[4] or ""),
                "provenance": r[5],
                "resolution_state": str(r[6] or ""),
                "is_fanout_duplicate": _is_fanout_dup(r[5] if isinstance(r[5], str) else None),
            }
    return out


def _is_formal_group(m: dict[str, Any]) -> bool:
    return bool(
        m.get("bot_id")
        and m.get("session_id")
        and m.get("visibility") == "group"
        and m.get("group_id")
    )


def choose_owner(
    legacy_id: int,
    targets: list[int],
    meta: dict[int, dict[str, Any]],
    *,
    preferred_bot_id: str = "yushu",
    preferred_group_id: str = "398291136",
) -> tuple[int | None, str]:
    """Return (owner_memory_id, reason_code).

    Grant owner must be a *formal* group-scoped memory row. Unscoped legacy
    rows may exist as content sources but cannot be grant owners until
    formalized (they lack bot/session/visibility).
    """
    leg = meta.get(int(legacy_id))
    if (
        leg
        and _is_formal_group(leg)
        and not leg["is_fanout_duplicate"]
    ):
        return int(legacy_id), "legacy_formal_unmarked"

    formal_targets = [
        tid
        for tid in targets
        if (m := meta.get(tid)) and _is_formal_group(m)
    ]
    unmarked = sorted(
        tid
        for tid in formal_targets
        if not meta[tid]["is_fanout_duplicate"]
    )
    if unmarked:
        # Prefer preferred scope among unmarked, else lowest id
        preferred = [
            tid
            for tid in unmarked
            if meta[tid].get("bot_id") == preferred_bot_id
            and meta[tid].get("group_id") == preferred_group_id
        ]
        if preferred:
            return preferred[0], "preferred_scope_unmarked_target"
        return unmarked[0], "lowest_unmarked_formal_target"

    if formal_targets:
        preferred = [
            tid
            for tid in formal_targets
            if meta[tid].get("bot_id") == preferred_bot_id
            and meta[tid].get("group_id") == preferred_group_id
        ]
        if preferred:
            return sorted(preferred)[0], "preferred_scope_fanout_keeper"
        return sorted(formal_targets)[0], "lowest_formal_fanout_keeper"

    if leg and not _is_formal_group(leg):
        return None, "legacy_unscoped_needs_formalization"
    if leg:
        return None, "legacy_formal_but_not_usable"
    return None, "no_memory_rows"


def filter_candidates(
    candidates: list[dict[str, Any]],
    *,
    same_bot_only: bool = False,
    exclude_cross_bot: bool = False,
) -> list[dict[str, Any]]:
    """Post-filter grant candidates. same_bot_only implies exclude_cross_bot."""
    if not same_bot_only and not exclude_cross_bot:
        return list(candidates)
    out: list[dict[str, Any]] = []
    for c in candidates:
        owner_bot = str((c.get("owner_scope") or {}).get("bot_id") or "")
        consumer_bot = str((c.get("consumer_scope") or {}).get("bot_id") or "")
        cross = bool(c.get("cross_bot")) or (owner_bot != consumer_bot)
        if same_bot_only or exclude_cross_bot:
            if cross:
                continue
        out.append(c)
    return out


def plan_grants(
    conn: sqlite3.Connection,
    *,
    family_limit: int = 0,
    sample_output: int = 20,
    same_bot_only: bool = False,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT legacy_memory_id,
               GROUP_CONCAT(target_scope_key) AS scopes,
               GROUP_CONCAT(target_memory_id) AS targets,
               COUNT(*) AS n
          FROM scope_recovery_memory_map
         GROUP BY legacy_memory_id
        HAVING COUNT(*) > 1
        ORDER BY legacy_memory_id
        """
    ).fetchall()
    if family_limit and family_limit > 0:
        rows = rows[: int(family_limit)]

    all_ids: set[int] = set()
    families: list[dict[str, Any]] = []
    for legacy_id, scopes_csv, targets_csv, n in rows:
        targets: list[int] = []
        for part in str(targets_csv or "").split(","):
            part = part.strip()
            if part:
                try:
                    targets.append(int(part))
                except ValueError:
                    continue
        scopes = [s for s in str(scopes_csv or "").split(",") if s.strip()]
        targets = sorted(set(targets))
        all_ids.add(int(legacy_id))
        all_ids.update(targets)
        families.append(
            {
                "legacy_memory_id": int(legacy_id),
                "targets": targets,
                "scope_keys": scopes,
                "map_n": int(n),
            }
        )

    meta = _load_memory_meta(conn, sorted(all_ids))
    owner_reasons: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    skipped_families = 0
    consumer_scopes: set[tuple[str, str, str]] = set()
    cross_bot = 0
    same_bot = 0

    for fam in families:
        owner_id, reason = choose_owner(fam["legacy_memory_id"], fam["targets"], meta)
        owner_reasons[reason] += 1
        if owner_id is None:
            skipped_families += 1
            continue
        owner = meta.get(owner_id)
        if not owner or not _is_formal_group(owner):
            skipped_families += 1
            continue
        owner_scope = {
            "bot_id": owner["bot_id"],
            "session_id": owner["session_id"],
            "visibility": owner["visibility"],
            "group_id": owner["group_id"],
        }

        # Consumer scopes: every map target scope except owner's scope
        seen_consumer: set[tuple[str, str, str]] = set()
        for sk in fam["scope_keys"]:
            parsed = _parse_scope_key(sk)
            if not parsed:
                continue
            key = (parsed["bot_id"], parsed["session_id"], parsed["visibility"])
            owner_key = (
                owner_scope["bot_id"],
                owner_scope["session_id"],
                owner_scope["visibility"],
            )
            if key == owner_key:
                continue
            if key in seen_consumer:
                continue
            seen_consumer.add(key)
            consumer_scopes.add(key)
            is_cross_bot = parsed["bot_id"] != owner_scope["bot_id"]
            if is_cross_bot:
                cross_bot += 1
            else:
                same_bot += 1
            candidates.append(
                {
                    "owner_memory_id": owner_id,
                    "owner_scope": owner_scope,
                    "consumer_scope": parsed,
                    "legacy_memory_id": fam["legacy_memory_id"],
                    "owner_reason": reason,
                    "cross_bot": is_cross_bot,
                    "fanout_family_id": f"legacy:{fam['legacy_memory_id']}",
                    "grant_quality": (
                        "review_cross_bot"
                        if is_cross_bot
                        else (
                            "ready_keeper"
                            if reason.startswith("preferred_") or reason.startswith("lowest_")
                            else "ready"
                        )
                    ),
                }
            )

    # Dedupe identical grants
    unique: dict[tuple, dict[str, Any]] = {}
    for c in candidates:
        k = (
            c["owner_memory_id"],
            c["owner_scope"]["bot_id"],
            c["owner_scope"]["session_id"],
            c["consumer_scope"]["bot_id"],
            c["consumer_scope"]["session_id"],
        )
        unique[k] = c
    candidates = list(unique.values())
    pre_filter_count = len(candidates)
    pre_cross = sum(1 for c in candidates if c.get("cross_bot"))
    pre_same = pre_filter_count - pre_cross
    if same_bot_only:
        candidates = filter_candidates(candidates, same_bot_only=True)
    quality = Counter(str(c.get("grant_quality") or "") for c in candidates)
    consumer_scopes = {
        (
            c["consumer_scope"]["bot_id"],
            c["consumer_scope"]["session_id"],
            c["consumer_scope"]["visibility"],
        )
        for c in candidates
    }

    return {
        "mode": "dry-run",
        "generated_at": time.time(),
        "families_scanned": len(families),
        "skipped_families": skipped_families,
        "owner_reason_counts": dict(owner_reasons),
        "same_bot_only": bool(same_bot_only),
        "grant_candidates_before_filter": pre_filter_count,
        "grant_candidates": len(candidates),
        "grant_quality_counts": dict(quality),
        "cross_bot_candidates_before_filter": pre_cross,
        "same_bot_candidates_before_filter": pre_same,
        "cross_bot_candidates": sum(1 for c in candidates if c.get("cross_bot")),
        "same_bot_candidates": sum(1 for c in candidates if not c.get("cross_bot")),
        "distinct_owner_memories": len({c["owner_memory_id"] for c in candidates}),
        "distinct_consumer_scopes": len(consumer_scopes),
        "notes": [
            "legacy multi-family rows in production are typically unscoped (no bot/session); "
            "grant owner is a formal fanout target keeper, not the legacy id",
            "cross_bot grants need product review (shared candidate vs identity pollution)",
            "use --same-bot-only for pilot; default does not write",
            "apply requires confirmation grant-from-fanout-map",
        ],
        "phase2_promote_allowed": False,
        "writes_memories": False,
        "sample": candidates[: max(0, int(sample_output))],
        "candidates": candidates,
    }


def apply_grants(
    target_db: Path,
    candidates: list[dict[str, Any]],
    *,
    limit: int = 0,
) -> dict[str, Any]:
    """Write grant rows only. Never inserts memories."""
    from engine.db.connection import ConnectionManager
    from engine.db.migrations.shared_memory_grants import ensure_shared_memory_grants_schema
    from engine.db.shared_memory_grant_repo import SharedMemoryGrantRepository

    cm = ConnectionManager(str(target_db))
    try:
        ensure_shared_memory_grants_schema(cm)
        repo = SharedMemoryGrantRepository(cm)
        created = reactivated = skipped = 0
        batch = candidates if not limit else candidates[: int(limit)]
        for c in batch:
            try:
                r = repo.grant_read(
                    owner_scope=c["owner_scope"],
                    consumer_scope=c["consumer_scope"],
                    memory_id=int(c["owner_memory_id"]),
                    reason=f"fanout_map:{c.get('fanout_family_id')}",
                    actor="fanout_to_shared_grants_dryrun",
                    provenance={
                        "source": "scope_recovery_memory_map",
                        "legacy_memory_id": c.get("legacy_memory_id"),
                        "owner_reason": c.get("owner_reason"),
                    },
                )
            except ValueError:
                skipped += 1
                continue
            if r.get("created"):
                created += 1
            elif r.get("reactivated"):
                reactivated += 1
            else:
                skipped += 1
        return {
            "applied": True,
            "created": created,
            "reactivated": reactivated,
            "skipped": skipped,
            "batch": len(batch),
        }
    finally:
        cm.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument("--family-limit", type=int, default=0, help="0 = all multi-target families")
    parser.add_argument("--sample-output", type=int, default=15)
    parser.add_argument(
        "--same-bot-only",
        action="store_true",
        help="drop cross-bot grant candidates (recommended pilot filter)",
    )
    parser.add_argument(
        "--report",
        default="",
        help="optional JSON report path (candidates truncated if huge unless --include-all)",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="include full candidates list in report (can be large)",
    )
    parser.add_argument("--apply", action="store_true", help="write grants (requires flags)")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument(
        "--apply-db",
        default="",
        help="target DB for grants (default: same as --db). Use staged copy for safety.",
    )
    parser.add_argument("--apply-limit", type=int, default=0, help="max grants to write; 0=all")
    parser.add_argument(
        "--forbid-prod-apply",
        action="store_true",
        default=True,
        help="refuse apply when apply-db looks like live wave_memory.db (default on)",
    )
    parser.add_argument(
        "--allow-prod-apply",
        action="store_true",
        help="override forbid-prod-apply (dangerous; still needs confirmation)",
    )
    args = parser.parse_args()

    db = Path(args.db)
    conn = _ro(db)
    try:
        plan = plan_grants(
            conn,
            family_limit=int(args.family_limit),
            sample_output=int(args.sample_output),
            same_bot_only=bool(args.same_bot_only),
        )
    finally:
        conn.close()

    report = {k: v for k, v in plan.items() if k != "candidates"}
    if args.include_all:
        report["candidates"] = plan.get("candidates") or []
    # Keep candidates only in-memory for apply; never dump full list unless asked.

    if args.apply:
        if args.confirmation != CONFIRMATION or not args.writers_stopped:
            report["apply_error"] = "need --confirmation grant-from-fanout-map --writers-stopped"
            report["applied"] = False
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        target = Path(args.apply_db) if args.apply_db else db
        prod_like = target.name == "wave_memory.db" and "plugin_data" in target.as_posix()
        if prod_like and not args.allow_prod_apply:
            report["apply_error"] = "refuse_prod_apply: pass --apply-db staged path or --allow-prod-apply"
            report["applied"] = False
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        result = apply_grants(
            target,
            plan["candidates"],
            limit=int(args.apply_limit),
        )
        report["apply"] = result
        report["mode"] = "apply"
        report["target_db"] = str(target)
        report["same_bot_only"] = bool(args.same_bot_only)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        # compact report without full candidates unless requested
        Path(args.report).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
