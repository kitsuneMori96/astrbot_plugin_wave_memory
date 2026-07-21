#!/usr/bin/env python3
"""Mark historical fanout clones in provenance without deleting rows.

Source of truth: scope_recovery_memory_map where one legacy_memory_id maps to
multiple target_memory_id rows. Projected rows get:

  provenance.projection_kind = "fanout_duplicate"
  provenance.fanout_family_id = "legacy:<legacy_memory_id>"
  provenance.legacy_memory_id = <legacy_memory_id>

Default mode is dry-run. Use --apply to write. Updates are batched.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    conn = sqlite3.connect(path.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _load_families(conn: sqlite3.Connection) -> list[tuple[int, list[int]]]:
    rows = conn.execute(
        """
        SELECT legacy_memory_id,
               GROUP_CONCAT(target_memory_id) AS targets,
               COUNT(*) AS n
          FROM scope_recovery_memory_map
         GROUP BY legacy_memory_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    families: list[tuple[int, list[int]]] = []
    for legacy_id, targets, _n in rows:
        ids: list[int] = []
        for part in str(targets or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                continue
        ids = sorted(set(ids))
        if len(ids) > 1:
            families.append((int(legacy_id), ids))
    return families


def _merge_provenance(raw: str | None, *, family_id: str, legacy_id: int) -> str:
    payload: dict = {}
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = dict(loaded)
        except Exception:
            payload = {"previous_provenance_raw": raw}
    if (
        payload.get("projection_kind") == "fanout_duplicate"
        and payload.get("fanout_family_id") == family_id
        and int(payload.get("legacy_memory_id") or -1) == int(legacy_id)
    ):
        return ""  # already marked
    payload["projection_kind"] = "fanout_duplicate"
    payload["fanout_family_id"] = family_id
    payload["legacy_memory_id"] = int(legacy_id)
    payload["fanout_mark_source"] = "scope_recovery_memory_map"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def plan_marks(conn: sqlite3.Connection) -> dict:
    families = _load_families(conn)
    target_ids = sorted({mid for _legacy, ids in families for mid in ids})
    already = 0
    if target_ids:
        sample = target_ids[:5000]
        placeholders = ",".join("?" for _ in sample)
        already = conn.execute(
            f"""SELECT COUNT(*) FROM memories
                WHERE id IN ({placeholders})
                  AND provenance LIKE '%"projection_kind":"fanout_duplicate"%'""",
            sample,
        ).fetchone()[0]
    return {
        "multi_target_families": len(families),
        "target_rows": len(target_ids),
        "already_marked_sample_5k": already,
        "preview_families": [
            {
                "legacy_memory_id": legacy,
                "target_memory_ids": ids[:8],
                "degree": len(ids),
            }
            for legacy, ids in families[:10]
        ],
    }


def apply_marks(conn: sqlite3.Connection, *, batch_size: int = 2000) -> dict:
    families = _load_families(conn)
    # Flatten: memory_id -> (legacy_id, family_id)
    target_to_family: dict[int, tuple[int, str]] = {}
    for legacy_id, target_ids in families:
        family_id = f"legacy:{legacy_id}"
        for memory_id in target_ids:
            target_to_family[memory_id] = (legacy_id, family_id)

    all_ids = sorted(target_to_family)
    updated = 0
    unchanged = 0
    missing = 0

    for offset in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[offset:offset + batch_size]
        placeholders = ",".join("?" for _ in batch_ids)
        rows = conn.execute(
            f"SELECT id, provenance FROM memories WHERE id IN ({placeholders})",
            batch_ids,
        ).fetchall()
        found = {int(row[0]): row[1] for row in rows}
        payload_rows: list[tuple[str, int]] = []
        for memory_id in batch_ids:
            if memory_id not in found:
                missing += 1
                continue
            legacy_id, family_id = target_to_family[memory_id]
            new_prov = _merge_provenance(
                found[memory_id],
                family_id=family_id,
                legacy_id=legacy_id,
            )
            if not new_prov:
                unchanged += 1
                continue
            payload_rows.append((new_prov, memory_id))
        if payload_rows:
            conn.executemany(
                "UPDATE memories SET provenance=? WHERE id=?",
                payload_rows,
            )
            updated += len(payload_rows)
        conn.commit()

    return {
        "multi_target_families": len(families),
        "target_rows": len(all_ids),
        "updated_rows": updated,
        "already_marked_or_unchanged": unchanged,
        "missing_target_rows": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=2000)
    args = parser.parse_args()
    path = Path(args.db)
    if not path.is_file():
        raise SystemExit(f"database not found: {path}")

    conn = _connect(path, readonly=not args.apply)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "scope_recovery_memory_map" not in tables:
            raise SystemExit("scope_recovery_memory_map missing; nothing to mark")
        if args.apply:
            result = apply_marks(conn, batch_size=max(100, int(args.batch_size)))
            result["mode"] = "apply"
        else:
            result = plan_marks(conn)
            result["mode"] = "dry-run"
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
