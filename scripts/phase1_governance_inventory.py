"""Read-only Phase 1 governance inventory for dual-track WaveMemory DBs.

Does not mutate the source database. Writes a JSON report beside the DB under
data_governance_snapshots/.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path


PSEUDO_PREFIXES = ("book_lore", "oni_lore", "arc0", "arc1", "arc2", "arc3", "arc4", "arc5")


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def build_inventory(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        report: dict = {
            "generated_at": time.time(),
            "rule_version": "governance-inventory/phase1-2026-07-20",
            "database": db_path,
            "tables_present": sorted(tables),
            "domains": {},
            "notes": [],
            "prior_migrations": {},
        }

        total = _scalar(conn, "SELECT COUNT(*) FROM memories")
        legacy = _scalar(
            conn,
            """SELECT COUNT(*) FROM memories
                 WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='' OR visibility IS NULL""",
        )
        formal = _scalar(
            conn,
            """SELECT COUNT(*) FROM memories
                 WHERE COALESCE(bot_id,'')<>'' AND COALESCE(session_id,'')<>'' AND visibility='group'""",
        )
        partial = total - legacy - formal
        formal_vec_null = _scalar(
            conn,
            """SELECT COUNT(*) FROM memories
                 WHERE vector IS NULL
                   AND COALESCE(bot_id,'')<>'' AND COALESCE(session_id,'')<>'' AND visibility='group'
                   AND COALESCE(resolution_state,'') IN ('','resolved')
                   AND COALESCE(quarantine,0)=0
                   AND COALESCE(source,'') NOT IN ('noise','identity_quarantine')""",
        )
        legacy_vec_null = _scalar(
            conn,
            """SELECT COUNT(*) FROM memories
                 WHERE vector IS NULL
                   AND (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='' OR visibility IS NULL)""",
        )

        formal_sessions = conn.execute(
            """SELECT bot_id, session_id, group_id, COUNT(*) c
                 FROM memories
                WHERE COALESCE(bot_id,'')<>'' AND COALESCE(session_id,'')<>'' AND visibility='group'
                GROUP BY bot_id, session_id, group_id"""
        ).fetchall()
        sessions_by_group: dict[str, list[dict]] = defaultdict(list)
        for row in formal_sessions:
            sessions_by_group[str(row["group_id"] or "")].append(
                {
                    "bot_id": row["bot_id"],
                    "session_id": row["session_id"],
                    "count": int(row["c"]),
                }
            )

        legacy_groups = conn.execute(
            """SELECT group_id, COUNT(*) c,
                      SUM(CASE WHEN vector IS NULL THEN 1 ELSE 0 END) vec_null
                 FROM memories
                WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='' OR visibility IS NULL
                GROUP BY group_id
                ORDER BY c DESC"""
        ).fetchall()

        recoverable = review = catalog = 0
        samples = {"recover_to_scope": [], "fanout_to_targets": [], "review": [], "catalog": []}
        for row in legacy_groups:
            gid = str(row["group_id"] or "")
            count = int(row["c"])
            item = {
                "group_id": gid,
                "legacy_count": count,
                "vector_null": int(row["vec_null"] or 0),
            }
            if any(gid.startswith(p) or gid == p for p in PSEUDO_PREFIXES):
                catalog += count
                item["disposition"] = "catalog"
                item["reason"] = "pseudo_group_or_corpus"
                if len(samples["catalog"]) < 20:
                    samples["catalog"].append(item)
                continue
            mappings = sessions_by_group.get(gid, [])
            bots = {m["bot_id"] for m in mappings}
            if len(bots) == 1:
                recoverable += count
                item["disposition"] = "recover_to_scope"
                item["reason"] = "unique_formal_bot_for_group"
                item["target"] = mappings[0]
                if len(samples["recover_to_scope"]) < 20:
                    samples["recover_to_scope"].append(item)
            elif len(bots) > 1:
                recoverable += count
                item["disposition"] = "fanout_to_targets"
                item["reason"] = "multi_bot_formal_sessions_same_group"
                item["targets"] = mappings
                if len(samples["fanout_to_targets"]) < 20:
                    samples["fanout_to_targets"].append(item)
            else:
                review += count
                item["disposition"] = "review"
                item["reason"] = "no_formal_session_mapping"
                if len(samples["review"]) < 20:
                    samples["review"].append(item)

        report["domains"]["memories"] = {
            "total": total,
            "formal_group_scope": formal,
            "legacy_null_scope": legacy,
            "partial_scope": partial,
            "formal_vector_null_recoverable": formal_vec_null,
            "legacy_vector_null": legacy_vec_null,
            "legacy_disposition": {
                "recoverable_or_fanout": recoverable,
                "catalog_corpus": catalog,
                "review": review,
            },
            "legacy_group_count": len(legacy_groups),
            "samples": samples,
            "top_legacy_groups": [
                {
                    "group_id": str(r["group_id"] or ""),
                    "count": int(r["c"]),
                    "vector_null": int(r["vec_null"] or 0),
                }
                for r in legacy_groups[:15]
            ],
        }

        report["domains"]["tags"] = {
            "legacy_tags": _scalar(conn, "SELECT COUNT(*) FROM tags") if "tags" in tables else 0,
            "legacy_memory_tags": (
                _scalar(conn, "SELECT COUNT(*) FROM memory_tags") if "memory_tags" in tables else 0
            ),
            "scoped_tags": (
                _scalar(conn, "SELECT COUNT(*) FROM scoped_tags") if "scoped_tags" in tables else 0
            ),
            "scoped_memory_tags": (
                _scalar(conn, "SELECT COUNT(*) FROM scoped_memory_tags")
                if "scoped_memory_tags" in tables
                else 0
            ),
            "tag_catalog": (
                _scalar(conn, "SELECT COUNT(*) FROM tag_catalog") if "tag_catalog" in tables else 0
            ),
            "legacy_links_on_legacy_memories": (
                _scalar(
                    conn,
                    """SELECT COUNT(*) FROM memory_tags mt
                         JOIN memories m ON m.id=mt.memory_id
                        WHERE COALESCE(m.bot_id,'')='' OR COALESCE(m.session_id,'')='' OR m.visibility IS NULL""",
                )
                if "memory_tags" in tables
                else 0
            ),
        }

        up = _scalar(conn, "SELECT COUNT(*) FROM user_profiles") if "user_profiles" in tables else 0
        re = (
            _scalar(conn, "SELECT COUNT(*) FROM relationship_events")
            if "relationship_events" in tables
            else 0
        )
        ssr = (
            _scalar(conn, "SELECT COUNT(*) FROM scoped_soul_relationships")
            if "scoped_soul_relationships" in tables
            else 0
        )
        up_recoverable = up_review = 0
        if "user_profiles" in tables:
            up_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(user_profiles)")}
            if {"user_id", "group_id", "bot_id"} <= up_cols:
                for r in conn.execute("SELECT user_id, group_id, bot_id FROM user_profiles"):
                    if all(str(r[k] or "").strip() for k in ("user_id", "group_id", "bot_id")):
                        up_recoverable += 1
                    else:
                        up_review += 1
            else:
                up_review = up
                report["notes"].append("user_profiles schema incomplete for auto recovery")
        re_recoverable = re_review = 0
        if "relationship_events" in tables:
            re_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(relationship_events)")}
            if {"user_id", "group_id", "bot_id"} <= re_cols:
                re_recoverable = _scalar(
                    conn,
                    """SELECT COUNT(*) FROM relationship_events
                         WHERE COALESCE(user_id,'')<>'' AND COALESCE(group_id,'')<>''
                           AND COALESCE(bot_id,'')<>''""",
                )
                re_review = re - re_recoverable
            else:
                re_review = re
        report["domains"]["relationships"] = {
            "user_profiles": up,
            "relationship_events": re,
            "scoped_soul_relationships": ssr,
            "profiles_recoverable_fields": up_recoverable,
            "profiles_review": up_review,
            "events_recoverable_fields": re_recoverable,
            "events_review": re_review,
            "gap_events_vs_scoped": re - ssr,
        }

        facts = _scalar(conn, "SELECT COUNT(*) FROM facts") if "facts" in tables else 0
        sf = _scalar(conn, "SELECT COUNT(*) FROM scoped_facts") if "scoped_facts" in tables else 0
        jargon = _scalar(conn, "SELECT COUNT(*) FROM jargon") if "jargon" in tables else 0
        sj = _scalar(conn, "SELECT COUNT(*) FROM scoped_jargon") if "scoped_jargon" in tables else 0
        report["domains"]["facts"] = {"legacy_facts": facts, "scoped_facts": sf, "gap": facts - sf}
        report["domains"]["jargon"] = {
            "legacy_jargon": jargon,
            "scoped_jargon": sj,
            "gap": jargon - sj,
        }

        prior: dict = {}
        for table in (
            "scope_recovery_migrations",
            "scope_recovery_items",
            "scope_recovery_memory_map",
            "learning_legacy_migration_runs",
        ):
            if table in tables:
                prior[table] = _scalar(conn, f"SELECT COUNT(*) FROM {table}")
                try:
                    prior[f"{table}_status"] = {
                        str(status): int(count)
                        for status, count in conn.execute(
                            f"SELECT status, COUNT(*) FROM {table} GROUP BY status"
                        )
                    }
                except sqlite3.Error:
                    pass
        report["prior_migrations"] = prior

        if "tag_pair_similarity" in tables:
            pc = {str(r[1]) for r in conn.execute("PRAGMA table_info(tag_pair_similarity)")}
            report["pair_similarity"] = {
                "columns": sorted(pc),
                "schema": (
                    "canonical_tag_id"
                    if {"tag_id_a", "tag_id_b"} <= pc
                    else ("legacy_tag_a" if {"tag_a", "tag_b"} <= pc else "unknown")
                ),
                "rows": _scalar(conn, "SELECT COUNT(*) FROM tag_pair_similarity"),
            }

        report["summary"] = {
            "memories_legacy_pct": round(100.0 * legacy / total, 2) if total else 0,
            "memories_actionable_now": recoverable,
            "memories_need_review": review,
            "memories_catalog_not_group": catalog,
            "relationships_need_staged_migration": re_recoverable,
            "formal_vector_debt": formal_vec_null,
            "legacy_vector_debt": legacy_vec_null,
            "phase2_ready": formal_vec_null == 0 and recoverable > 0,
        }
        return report
    finally:
        conn.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument(
        "--out-dir",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/data_governance_snapshots",
    )
    args = parser.parse_args()
    report = build_inventory(args.db)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"inventory_phase1_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("legacy_disposition", report["domains"]["memories"]["legacy_disposition"])
    print(
        "relationships",
        {
            k: report["domains"]["relationships"][k]
            for k in (
                "user_profiles",
                "relationship_events",
                "scoped_soul_relationships",
                "events_recoverable_fields",
                "events_review",
            )
        },
    )
    print("tags", report["domains"]["tags"])
    print("pair", report.get("pair_similarity"))
    print("WROTE", out_path)
    print("SIZE", out_path.stat().st_size)


if __name__ == "__main__":
    main()
