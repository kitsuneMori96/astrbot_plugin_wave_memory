#!/usr/bin/env python3
"""Cross-group same-person same-content dedupe: dry-run inventory + optional soft-delete apply.

Hard rules:
  - Default is dry-run only (report JSON).
  - Never fanout / never Phase2 promote.
  - Production APPLY requires --apply + confirmation token + --allow-production.
  - Apply is **soft-delete** only (memory_type='deleted', quarantine=1) — reversible.

Two inventory modes:
  - naive: sum(n-1) per (sender, content_prefix) with >=2 groups (aggressive account)
  - cluster (default for apply): within each family, time-cluster rows (window seconds);
    only clusters spanning >=2 groups drop extras. Same phrase days apart is kept.

Keeper rule:
  1. Prefer rows whose group_id is in --prefer-groups (order matters)
  2. Else prefer higher timestamp
  3. Else prefer lower id (stable)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

CONFIRMATION = "cross-group-same-content-dedupe"
NOISE_SENDERS = frozenset({"", "bot", "bot_remember"})
SOFT_DELETED_TYPE = "deleted"


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rw(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db.as_posix(), timeout=300)
    conn.execute("PRAGMA busy_timeout=300000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _is_prod_like(path: Path) -> bool:
    p = path.as_posix()
    return path.name == "wave_memory.db" and "plugin_data" in p and "backups" not in p


def _active_sql(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return (
        f"COALESCE({p}quarantine, 0)=0 "
        f"AND COALESCE({p}memory_type, 'message') NOT IN "
        f"('archived', 'evicted', 'deleted', 'noise') "
        f"AND COALESCE({p}source, '') != 'noise'"
    )


def _keep_key(
    row: tuple[Any, ...],
    prefer_rank: dict[str, int],
) -> tuple:
    mid, group_id, _bot, _sess, ts, _prev = row
    gid = str(group_id or "")
    pref = prefer_rank.get(gid, 10_000)
    try:
        ts_f = -float(ts or 0.0)
    except (TypeError, ValueError):
        ts_f = 0.0
    try:
        mid_i = int(mid)
    except (TypeError, ValueError):
        mid_i = 0
    return (pref, ts_f, mid_i)


def _cluster_drop_ids(
    members: list[tuple[Any, ...]],
    *,
    prefer_rank: dict[str, int],
    window_sec: float,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Return drop ids and cluster summaries for time-windowed multi-group clusters."""
    # members: id, group_id, bot_id, session_id, timestamp, preview
    decorated: list[tuple[float, tuple[Any, ...]]] = []
    for r in members:
        try:
            ts = float(r[4] or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        decorated.append((ts, r))
    decorated.sort(key=lambda x: (x[0], int(x[1][0])))

    drop_ids: list[int] = []
    clusters_out: list[dict[str, Any]] = []
    i = 0
    n = len(decorated)
    while i < n:
        j = i + 1
        while j < n and (decorated[j][0] - decorated[j - 1][0]) <= window_sec:
            j += 1
        cluster = [decorated[k][1] for k in range(i, j)]
        groups = {str(r[1] or "") for r in cluster}
        if len(cluster) >= 2 and len(groups) >= 2:
            ordered = sorted(cluster, key=lambda r: _keep_key(r, prefer_rank))
            keeper = ordered[0]
            drops = [int(r[0]) for r in ordered[1:]]
            drop_ids.extend(drops)
            clusters_out.append(
                {
                    "size": len(cluster),
                    "groups": sorted(groups),
                    "keeper_id": int(keeper[0]),
                    "keeper_group": str(keeper[1]),
                    "drop_count": len(drops),
                    "ts_span": float(decorated[j - 1][0] - decorated[i][0]),
                }
            )
        i = j
    return drop_ids, clusters_out


def inventory(
    conn: sqlite3.Connection,
    *,
    content_prefix: int,
    prefer_groups: list[str],
    top_families: int,
    min_groups: int,
    mode: str = "naive",
    window_sec: float = 600.0,
) -> dict[str, Any]:
    content_prefix = max(20, min(int(content_prefix), 500))
    min_groups = max(2, int(min_groups))
    prefer_rank = {gid: i for i, gid in enumerate(prefer_groups)}
    mode = (mode or "naive").strip().lower()
    window_sec = max(1.0, float(window_sec))

    fam_sql = f"""
    SELECT sender_id,
           substr(content, 1, ?) AS cprefix,
           COUNT(*) AS n,
           COUNT(DISTINCT group_id) AS g,
           MIN(id) AS min_id,
           MAX(id) AS max_id,
           MIN(timestamp) AS min_ts,
           MAX(timestamp) AS max_ts
      FROM memories
     WHERE {_active_sql()}
       AND COALESCE(sender_id, '') NOT IN ('', 'bot', 'bot_remember')
       AND COALESCE(sender_id, '') NOT LIKE '[%'
       AND COALESCE(content, '') != ''
       AND COALESCE(group_id, '') GLOB '[0-9]*'
       AND length(COALESCE(group_id, '')) >= 5
     GROUP BY sender_id, substr(content, 1, ?)
    HAVING g >= ? AND n >= 2
     ORDER BY n DESC
    """
    families = conn.execute(fam_sql, (content_prefix, content_prefix, min_groups)).fetchall()

    total_rows = 0
    total_extra_naive = 0
    total_extra_cluster = 0
    by_group_extra: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    all_drop_ids: list[int] = []
    collect_all_drops = mode == "cluster"

    for sender_id, cprefix, n, g, min_id, max_id, min_ts, max_ts in families:
        n_i, g_i = int(n), int(g)
        total_rows += n_i
        total_extra_naive += max(0, n_i - 1)

        need_members = collect_all_drops or len(samples) < top_families
        if not need_members:
            continue

        members = conn.execute(
            f"""
            SELECT id, group_id, bot_id, session_id, timestamp,
                   substr(content, 1, 80) AS preview
              FROM memories
             WHERE {_active_sql()}
               AND sender_id = ?
               AND substr(content, 1, ?) = ?
               AND COALESCE(group_id, '') GLOB '[0-9]*'
             ORDER BY timestamp ASC, id ASC
            """,
            (sender_id, content_prefix, cprefix),
        ).fetchall()

        if mode == "cluster":
            drop_ids, clusters = _cluster_drop_ids(
                members, prefer_rank=prefer_rank, window_sec=window_sec
            )
            total_extra_cluster += len(drop_ids)
            if collect_all_drops:
                all_drop_ids.extend(drop_ids)
            for r in members:
                if int(r[0]) in set(drop_ids):
                    by_group_extra[str(r[1] or "")] += 1
            if len(samples) < top_families and drop_ids:
                samples.append(
                    {
                        "sender_id": str(sender_id),
                        "content_prefix": str(cprefix)[:80],
                        "n": n_i,
                        "groups": g_i,
                        "group_ids": sorted({str(r[1]) for r in members}),
                        "mode": "cluster",
                        "window_sec": window_sec,
                        "drop_count": len(drop_ids),
                        "drop_ids_sample": drop_ids[:20],
                        "cluster_count": len(clusters),
                        "clusters_sample": clusters[:5],
                        "min_id": int(min_id),
                        "max_id": int(max_id),
                    }
                )
        else:
            ordered = sorted(members, key=lambda r: _keep_key(r, prefer_rank))
            keeper = ordered[0] if ordered else None
            drop_ids = [int(r[0]) for r in ordered[1:]] if ordered else []
            for r in ordered[1:]:
                by_group_extra[str(r[1] or "")] += 1
            if len(samples) < top_families:
                samples.append(
                    {
                        "sender_id": str(sender_id),
                        "content_prefix": str(cprefix)[:80],
                        "n": n_i,
                        "groups": g_i,
                        "group_ids": sorted({str(r[1]) for r in members}),
                        "mode": "naive",
                        "keeper_id": int(keeper[0]) if keeper else None,
                        "keeper_group": str(keeper[1]) if keeper else None,
                        "drop_count": len(drop_ids),
                        "drop_ids_sample": drop_ids[:20],
                        "min_id": int(min_id),
                        "max_id": int(max_id),
                    }
                )

    # naive mode still reports theoretical; cluster mode fills total_extra_cluster
    if mode != "cluster":
        # rough: no full cluster scan — leave cluster field null
        total_extra_cluster = -1
        all_drop_ids = []

    return {
        "content_prefix_chars": content_prefix,
        "min_groups": min_groups,
        "prefer_groups": prefer_groups,
        "mode": mode,
        "window_sec": window_sec if mode == "cluster" else None,
        "family_count": len(families),
        "family_row_sum": total_rows,
        "theoretical_extra_rows_naive": total_extra_naive,
        "theoretical_extra_rows": total_extra_naive if mode == "naive" else total_extra_cluster,
        "cluster_drop_rows": total_extra_cluster if mode == "cluster" else None,
        "extra_rows_by_group_top": by_group_extra.most_common(20),
        "top_families": samples,
        "drop_id_count": len(all_drop_ids) if collect_all_drops else None,
        "drop_ids": all_drop_ids if collect_all_drops else None,
        "note": (
            "naive: sum(n-1) keep one per family; "
            "cluster: only time-window multi-group clusters drop extras (safer). "
            "Apply soft-deletes drop_ids only."
        ),
    }


def purge_soft_deleted_from_fts(
    conn: sqlite3.Connection,
    *,
    batch_size: int = 2000,
    limit: int | None = None,
) -> dict[str, Any]:
    """Remove soft-deleted / quarantined rows from fts_memories (index only).

    Memories rows stay; only FTS postings are dropped so rank is not polluted.
    Safe after soft-delete (UPDATE does not remove content= external docs from rank).
    """
    sql = """
        SELECT id, content, sender_name, group_id
          FROM memories
         WHERE COALESCE(quarantine, 0) != 0
            OR COALESCE(memory_type, 'message') IN
               ('archived', 'evicted', 'deleted', 'noise')
         ORDER BY id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    purged = 0
    errors = 0
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset : offset + batch_size]
        for mid, content, sender_name, group_id in batch:
            try:
                conn.execute(
                    """
                    INSERT INTO fts_memories(
                        fts_memories, rowid, content, sender_name, group_id
                    ) VALUES ('delete', ?, ?, ?, ?)
                    """,
                    (int(mid), content, sender_name, group_id),
                )
                purged += 1
            except Exception:
                errors += 1
        conn.commit()
    return {"candidates": len(rows), "purged": purged, "errors": errors}


def mark_deleted_in_hot_hnsw(
    index_dir: Path,
    ids: list[int],
    *,
    dimension: int = 1024,
    max_elements: int = 100_000,
    save: bool = True,
) -> dict[str, Any]:
    """Mark labels deleted in on-disk hot memory.hnsw (optional new generation).

    Does not touch memories rows. Safe after soft-delete so knn slots free up
    without waiting for a full rebuild. Runtime process must reload/restart to
    see a newly saved generation.
    """
    if not ids:
        return {"requested": 0, "marked": 0, "saved": False, "generation": None}
    # Local import keeps dry-run import light when hnswlib is absent.
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from engine.vector_index import VectorIndex

    index_path = index_dir / "memory.hnsw"
    index = VectorIndex(
        dimension=int(dimension),
        max_elements=int(max_elements),
        index_path=str(index_path),
        kind="memory",
        allow_resize=False,
        strict_manifest=False,
    )
    unique = sorted({int(x) for x in ids})
    index.mark_deleted(unique)
    generation = None
    if save:
        manifest = index.save()
        generation = None if manifest is None else int(manifest.generation)
    return {
        "requested": len(unique),
        "marked": len(unique),
        "saved": bool(save),
        "generation": generation,
        "index_path": str(index_path),
    }


def soft_delete(
    conn: sqlite3.Connection,
    drop_ids: list[int],
    *,
    batch_size: int = 2000,
    reason: str = "cross_group_same_content_cluster_dedupe",
    purge_fts: bool = True,
    hnsw_index_dir: Path | None = None,
    hnsw_dimension: int = 1024,
    hnsw_max_elements: int = 100_000,
    hnsw_save: bool = True,
) -> dict[str, Any]:
    """Soft-delete: memory_type=deleted, quarantine=1. Idempotent on already deleted."""
    unique_ids = sorted({int(x) for x in drop_ids})
    updated = 0
    skipped = 0
    missing = 0
    stamp = time.time()
    purged_ids: list[tuple[Any, ...]] = []
    for offset in range(0, len(unique_ids), batch_size):
        batch = unique_ids[offset : offset + batch_size]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""SELECT id, quarantine, memory_type, provenance,
                       content, sender_name, group_id
                  FROM memories WHERE id IN ({placeholders})""",
            batch,
        ).fetchall()
        found = {int(r[0]): r for r in rows}
        for mid in batch:
            if mid not in found:
                missing += 1
                continue
            _id, q, mtype, prov_raw, content, sender_name, group_id = found[mid]
            if int(q or 0) != 0 or str(mtype or "") in (
                "archived",
                "evicted",
                "deleted",
                "noise",
            ):
                skipped += 1
                continue
            payload: dict[str, Any] = {}
            if prov_raw:
                try:
                    loaded = json.loads(prov_raw)
                    if isinstance(loaded, dict):
                        payload = dict(loaded)
                except Exception:
                    payload = {"previous_provenance_raw": str(prov_raw)[:500]}
            payload["soft_deleted_reason"] = reason
            payload["soft_deleted_at"] = stamp
            payload["projection_kind"] = payload.get(
                "projection_kind", "cross_group_same_content_duplicate"
            )
            new_prov = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            conn.execute(
                "UPDATE memories SET quarantine=1, memory_type=?, provenance=? WHERE id=?",
                (SOFT_DELETED_TYPE, new_prov, mid),
            )
            purged_ids.append((mid, content, sender_name, group_id))
            updated += 1
        conn.commit()

    fts_purged = 0
    fts_errors = 0
    if purge_fts and purged_ids:
        for mid, content, sender_name, group_id in purged_ids:
            try:
                conn.execute(
                    """
                    INSERT INTO fts_memories(
                        fts_memories, rowid, content, sender_name, group_id
                    ) VALUES ('delete', ?, ?, ?, ?)
                    """,
                    (int(mid), content, sender_name, group_id),
                )
                fts_purged += 1
            except Exception:
                fts_errors += 1
        conn.commit()

    hnsw_result: dict[str, Any] | None = None
    if hnsw_index_dir is not None and purged_ids:
        try:
            hnsw_result = mark_deleted_in_hot_hnsw(
                Path(hnsw_index_dir),
                [int(mid) for mid, *_rest in purged_ids],
                dimension=int(hnsw_dimension),
                max_elements=int(hnsw_max_elements),
                save=bool(hnsw_save),
            )
        except Exception as exc:
            hnsw_result = {"ok": False, "error": str(exc)}

    return {
        "requested": len(unique_ids),
        "updated": updated,
        "skipped_already_inactive": skipped,
        "missing": missing,
        "fts_purged": fts_purged,
        "fts_errors": fts_errors,
        "hnsw": hnsw_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--content-prefix", type=int, default=200)
    parser.add_argument("--min-groups", type=int, default=2)
    parser.add_argument("--top-families", type=int, default=30)
    parser.add_argument(
        "--prefer-groups",
        type=str,
        default="398291136,150727649",
        help="Comma-separated group_id preference for keeper ranking",
    )
    parser.add_argument(
        "--mode",
        choices=("naive", "cluster"),
        default="naive",
        help="naive=aggressive account; cluster=time-window multi-group (use for apply)",
    )
    parser.add_argument(
        "--window-sec",
        type=float,
        default=600.0,
        help="Cluster time window seconds (cluster mode / apply)",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", type=str, default="")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Required with confirmation for prod-like apply",
    )
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument(
        "--purge-fts-soft-deleted",
        action="store_true",
        help="Remove already soft-deleted/quarantined rows from fts_memories only",
    )
    parser.add_argument(
        "--hnsw-index-dir",
        type=Path,
        default=None,
        help="If set with --apply, mark soft-deleted ids in memory.hnsw under this dir",
    )
    parser.add_argument(
        "--hnsw-dimension",
        type=int,
        default=1024,
        help="HNSW dimension when --hnsw-index-dir is used (default 1024)",
    )
    parser.add_argument(
        "--hnsw-max-elements",
        type=int,
        default=100000,
        help="HNSW max_elements when loading index for mark_deleted",
    )
    parser.add_argument(
        "--no-hnsw-save",
        action="store_true",
        help="With --hnsw-index-dir: mark_deleted only, do not save a new generation",
    )
    args = parser.parse_args()

    prefer = [g.strip() for g in str(args.prefer_groups or "").split(",") if g.strip()]
    db: Path = args.db

    if not db.is_file():
        print(json.dumps({"ok": False, "error": "db_missing", "db": str(db)}, ensure_ascii=False))
        return 1

    if args.purge_fts_soft_deleted:
        if _is_prod_like(db) and not args.allow_production:
            print(
                json.dumps(
                    {"ok": False, "error": "allow_production_required", "db": str(db)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        if args.confirmation != CONFIRMATION:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "confirmation_mismatch",
                        "expected": CONFIRMATION,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        conn = _rw(db)
        try:
            result = purge_soft_deleted_from_fts(conn, batch_size=int(args.batch_size))
        finally:
            conn.close()
        out = {
            "ok": True,
            "mode": "purge_fts_soft_deleted",
            "generated_at": time.time(),
            "db": str(db),
            "prod_like": _is_prod_like(db),
            "phase2_promote_allowed": False,
            "fanout_allowed": False,
            "memories_rows_unchanged": True,
            **result,
        }
        text = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(
                json.dumps(
                    {"ok": True, "wrote": str(args.out), **result},
                    ensure_ascii=False,
                )
            )
        else:
            print(text)
        return 0

    if args.apply:
        if args.confirmation != CONFIRMATION:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "confirmation_mismatch",
                        "expected": CONFIRMATION,
                        "phase2_promote_allowed": False,
                        "fanout_allowed": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        if _is_prod_like(db) and not args.allow_production:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "allow_production_required",
                        "db": str(db),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2

        # Apply always uses cluster mode (safer).
        conn = _rw(db)
        try:
            report = inventory(
                conn,
                content_prefix=args.content_prefix,
                prefer_groups=prefer,
                top_families=args.top_families,
                min_groups=args.min_groups,
                mode="cluster",
                window_sec=args.window_sec,
            )
            drop_ids = list(report.get("drop_ids") or [])
            result = soft_delete(
                conn,
                drop_ids,
                batch_size=int(args.batch_size),
                hnsw_index_dir=args.hnsw_index_dir,
                hnsw_dimension=int(args.hnsw_dimension),
                hnsw_max_elements=int(args.hnsw_max_elements),
                hnsw_save=not bool(args.no_hnsw_save),
            )
        finally:
            conn.close()

        out = {
            "ok": True,
            "mode": "apply_soft_delete_cluster",
            "generated_at": time.time(),
            "db": str(db),
            "prod_like": _is_prod_like(db),
            "window_sec": args.window_sec,
            "phase2_promote_allowed": False,
            "fanout_allowed": False,
            "family_count": report["family_count"],
            "cluster_drop_planned": report.get("cluster_drop_rows"),
            "soft_delete": result,
            "hnsw_index_dir": str(args.hnsw_index_dir) if args.hnsw_index_dir else None,
            "top_families": report.get("top_families", [])[:10],
            "note": (
                "If hnsw mark_deleted saved a new generation, restart/reload "
                "AstrBot to load it. memories rows are soft-deleted only."
            ),
        }
        text = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(
                json.dumps(
                    {
                        "ok": True,
                        "wrote": str(args.out),
                        "updated": result["updated"],
                        "planned": report.get("cluster_drop_rows"),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(text)
        return 0

    # dry-run
    conn = _ro(db)
    try:
        report = inventory(
            conn,
            content_prefix=args.content_prefix,
            prefer_groups=prefer,
            top_families=args.top_families,
            min_groups=args.min_groups,
            mode=args.mode,
            window_sec=args.window_sec,
        )
    finally:
        conn.close()

    # strip full drop_ids from dry-run file unless small
    drop_ids = report.pop("drop_ids", None)
    if drop_ids is not None and len(drop_ids) <= 50:
        report["drop_ids"] = drop_ids
    elif drop_ids is not None:
        report["drop_ids_omitted"] = True
        report["drop_id_count"] = len(drop_ids)

    out = {
        "ok": True,
        "mode": f"dry-run-{args.mode}",
        "generated_at": time.time(),
        "db": str(db),
        "prod_like": _is_prod_like(db),
        "confirmation_for_apply": CONFIRMATION,
        "phase2_promote_allowed": False,
        "fanout_allowed": False,
        "apply_implemented": True,
        "apply_is_soft_delete": True,
        **report,
    }

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "wrote": str(args.out),
                    "family_count": out["family_count"],
                    "theoretical_extra_rows": out["theoretical_extra_rows"],
                    "mode": out["mode"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
