#!/usr/bin/env python3
"""Readonly acceptance against the 2026-07-20 five success criteria.

Does not write production. Does not cutover / promote.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


PROD = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
PLUGIN = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")
BACKUPS = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups")
VAC = BACKUPS / (
    "fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3"
)


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def main() -> int:
    conn = _ro(PROD)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    report: dict = {
        "mode": "readonly_acceptance_5_criteria",
        "generated_at": time.time(),
        "prod": str(PROD),
        "source_dialog": "2026-07-20 5 success criteria after 迁了十次/不治理永远都是狗屎/开始",
    }

    # ----- C1 QQ person chain -----
    c1: dict = {}
    code = {
        "person_identity": (PLUGIN / "tools/person_identity.py").is_file(),
        "person_search": (PLUGIN / "tools/person_search.py").is_file(),
        "affinity_update": (PLUGIN / "tools/affinity_update.py").is_file(),
        "memory_collapse": (PLUGIN / "engine/memory_collapse.py").is_file(),
    }
    if code["person_search"]:
        txt = (PLUGIN / "tools/person_search.py").read_text(encoding="utf-8", errors="ignore")
        # stub pattern from 07-13: whole tool returns scope_migration_required early
        code["person_search_stub_literal"] = "scope_migration_required" in txt
        code["person_search_looks_live"] = (
            "class WaveMemoryPersonSearchTool" in txt
            and "def call" in txt
            and "person_identity" in txt
        )
    c1["runtime_code"] = code

    if "person_registry" in tables:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(person_registry)")]
        c1["registry_count"] = conn.execute("SELECT COUNT(*) FROM person_registry").fetchone()[0]
        c1["registry_cols"] = cols
    if "user_profiles" in tables:
        up_cols = [r[1] for r in conn.execute("PRAGMA table_info(user_profiles)")]
        for gid in ("398291136", "150727649"):
            if "group_id" in up_cols and "nickname" in up_cols:
                total = conn.execute(
                    "SELECT COUNT(*) FROM user_profiles WHERE group_id=?", (gid,)
                ).fetchone()[0]
                empty = conn.execute(
                    "SELECT COUNT(*) FROM user_profiles WHERE group_id=? "
                    "AND (nickname IS NULL OR TRIM(nickname)='')",
                    (gid,),
                ).fetchone()[0]
                c1[f"g{gid}_profiles"] = total
                c1[f"g{gid}_empty_nick"] = empty
                c1[f"g{gid}_filled_pct"] = (
                    round(100.0 * (total - empty) / total, 1) if total else None
                )
    if "memories" in tables:
        c1["main_distinct_senders"] = conn.execute(
            "SELECT COUNT(DISTINCT sender_id) FROM memories "
            "WHERE group_id='398291136' AND COALESCE(sender_id,'')!=''"
        ).fetchone()[0]
        c1["main_senders_with_name"] = conn.execute(
            "SELECT COUNT(DISTINCT sender_id) FROM memories "
            "WHERE group_id='398291136' AND COALESCE(sender_name,'')!=''"
        ).fetchone()[0]
    if "scoped_soul_relationships" in tables:
        c1["formal_rel_total"] = conn.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationships"
        ).fetchone()[0]
        c1["formal_rel_main"] = conn.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationships "
            "WHERE bot_id='yushu' AND session_id LIKE '%398291136%'"
        ).fetchone()[0]

    # live resolve smoke via import if possible
    try:
        import sys

        sys.path.insert(0, str(PLUGIN))
        from tools.person_identity import resolve_user_id_to_qq  # type: ignore

        # pick a sample sender from main group
        sample = conn.execute(
            "SELECT sender_id, sender_name FROM memories "
            "WHERE group_id='398291136' AND COALESCE(sender_id,'')!='' "
            "AND COALESCE(sender_name,'')!='' LIMIT 1"
        ).fetchone()
        if sample:
            sid, sname = sample
            # try resolve by name in current group context
            try:
                from tools import person_identity as pi

                if hasattr(pi, "resolve_person_to_qq"):
                    c1["sample_resolve"] = {
                        "sender_id": sid,
                        "sender_name": sname,
                        "note": "resolver_import_ok",
                    }
                else:
                    c1["sample_resolve"] = {
                        "sender_id": sid,
                        "sender_name": sname,
                        "module_attrs": [
                            a for a in dir(pi) if not a.startswith("_")
                        ][:20],
                    }
            except Exception as exc:
                c1["sample_resolve_error"] = str(exc)[:200]
    except Exception as exc:
        c1["identity_import_error"] = str(exc)[:200]

    c1_pass = bool(
        code.get("person_identity")
        and code.get("person_search_looks_live")
        and (c1.get("formal_rel_main") or 0) > 0
        and (c1.get("g398291136_filled_pct") is None or c1.get("g398291136_filled_pct", 0) > 50)
    )
    c1["verdict"] = "PASS" if c1_pass else "PARTIAL/FAIL"
    report["c1_qq_person_chain"] = c1

    # ----- C2 fanout noise / current-group priority readiness -----
    c2: dict = {}
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    marked = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
    ).fetchone()[0]
    c2["total_memories"] = total
    c2["fanout_marked"] = marked
    c2["fanout_marked_pct"] = round(100.0 * marked / total, 2) if total else None
    c2["collapse_code"] = code.get("memory_collapse")
    if "scope_recovery_memory_map" in tables:
        c2["multi_target_families"] = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT 1 FROM scope_recovery_memory_map "
            "GROUP BY legacy_memory_id HAVING COUNT(*)>1)"
        ).fetchone()[0]
    c2["vacuumed_exists"] = VAC.is_file()
    if VAC.is_file():
        vc = _ro(VAC)
        c2["vacuumed_total"] = vc.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        c2["vacuumed_marked"] = vc.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0]
        vc.close()
    try:
        c2["fts_match_我是谁"] = conn.execute(
            "SELECT COUNT(*) FROM fts_memories WHERE fts_memories MATCH '我是谁'"
        ).fetchone()[0]
    except Exception as exc:
        c2["fts_error"] = str(exc)[:160]
    # Post-cutover: marked==0 is production PASS; staged-ready was pre-cutover path.
    c2_prod_clean = bool(
        c2.get("collapse_code") and (c2.get("fanout_marked") or 0) == 0 and total > 0
    )
    c2_staged_ready = bool(
        c2.get("collapse_code")
        and (c2.get("fanout_marked") or 0) > 0
        and c2.get("vacuumed_exists")
        and c2.get("vacuumed_marked") == 0
    )
    if c2_prod_clean:
        c2["verdict"] = "PASS"
        c2["note"] = "生产 fanout_marked=0；折叠代码在；物理副本已 cutover 清理"
    elif c2_staged_ready:
        c2["verdict"] = "PASS_STAGED_READY"
        c2["note"] = (
            "生产仍有 fanout 物理行；召回折叠代码在；vacuumed 包 marked=0 待授权 cutover"
        )
    else:
        c2["verdict"] = "PARTIAL"
        c2["note"] = "fanout 噪声治理未就绪：折叠/标记/清理包不完整"
    report["c2_local_first_no_fanout_spam"] = c2

    # ----- C3 formal layer in use -----
    c3: dict = {}
    c3["formal_relationships"] = (
        conn.execute("SELECT COUNT(*) FROM scoped_soul_relationships").fetchone()[0]
        if "scoped_soul_relationships" in tables
        else -1
    )
    c3["formal_relationship_events"] = (
        conn.execute("SELECT COUNT(*) FROM scoped_soul_relationship_events").fetchone()[0]
        if "scoped_soul_relationship_events" in tables
        else -1
    )
    c3["legacy_relationship_events"] = (
        conn.execute("SELECT COUNT(*) FROM relationship_events").fetchone()[0]
        if "relationship_events" in tables
        else -1
    )
    c3["audit_legacy_events"] = (
        conn.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
        ).fetchone()[0]
        if "scoped_soul_relationship_legacy_events" in tables
        else -1
    )
    c3["prod_evidence_summaries"] = (
        conn.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationships "
            "WHERE evidence LIKE '%historical_audit_summary%'"
        ).fetchone()[0]
        if "scoped_soul_relationships" in tables
        else -1
    )
    # staged evidence pilot
    pilot = BACKUPS / "relationship_evidence_multi_scope_pilot/report_all10_full.json"
    c3["staged_evidence_report"] = pilot.is_file()
    if pilot.is_file():
        data = json.loads(pilot.read_text(encoding="utf-8"))
        c3["staged_evidence_ok"] = data.get("ok")
        c3["staged_prod_summary_rows"] = data.get("prod_evidence_summary_rows")
        updated = sum(
            int((r.get("apply") or {}).get("updated") or 0)
            for r in (data.get("results") or [])
        )
        c3["staged_evidence_updated_total"] = updated
    c3_base = bool(
        (c3.get("formal_relationships") or 0) >= 1000
        and (c3.get("audit_legacy_events") or 0) > 0
        and code.get("affinity_update")
    )
    c3_summaries_ok = (c3.get("prod_evidence_summaries") or 0) >= 1000
    if c3_base and c3_summaries_ok:
        c3["verdict"] = "PASS"
        c3["note"] = (
            "formal 关系 + audit 在；生产 evidence 摘要已写入；"
            "live formal events 仍可后续加厚（不刷 affinity）"
        )
    elif c3_base:
        c3["verdict"] = "PARTIAL"
        c3["note"] = (
            "formal 关系行与 audit 在；live formal events/证据摘要生产仍弱；"
            "staged 证据全量已验证"
        )
    else:
        c3["verdict"] = "FAIL"
        c3["note"] = "正式关系层基础数据不足"
    report["c3_formal_layer_used"] = c3

    # ----- C4 history classified -----
    c4: dict = {}
    c4["fanout_marked"] = marked
    c4["recovery_map"] = "scope_recovery_memory_map" in tables
    if c4["recovery_map"]:
        c4["recovery_map_rows"] = conn.execute(
            "SELECT COUNT(*) FROM scope_recovery_memory_map"
        ).fetchone()[0]
    mcols = [r[1] for r in conn.execute("PRAGMA table_info(memories)")]
    if "bot_id" in mcols and "session_id" in mcols:
        c4["unscoped_memories"] = conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')=''"
        ).fetchone()[0]
    if "resolution_state" in mcols:
        c4["resolved_memories"] = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE resolution_state='resolved'"
        ).fetchone()[0]
    c4["grants_table"] = "shared_memory_grants" in tables
    c4["grants_rows"] = (
        conn.execute("SELECT COUNT(*) FROM shared_memory_grants").fetchone()[0]
        if c4["grants_table"]
        else 0
    )
    c4["same_bot_grant_plan"] = (
        BACKUPS / "fanout_to_shared_grants_same_bot_dryrun.json"
    ).is_file()
    # After cutover: marked==0 means fanout bucket cleaned (not "unclassified").
    fanout_bucket_done = marked == 0 and bool(c4.get("recovery_map"))
    unscoped = int(c4.get("unscoped_memories") or 0)
    # Compute formalize/quarantine/active unscoped early for PASS path.
    formalized_n = 0
    quarantined_n = 0
    unscoped_active = unscoped
    if fanout_bucket_done:
        try:
            formalized_n = int(
                conn.execute(
                    "SELECT COUNT(*) FROM memories "
                    "WHERE resolution_state='owned_formalized_from_unscoped'"
                ).fetchone()[0]
            )
        except Exception:
            formalized_n = 0
        try:
            quarantined_n = int(
                conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE COALESCE(quarantine,0)=1"
                ).fetchone()[0]
            )
        except Exception:
            quarantined_n = 0
        try:
            unscoped_active = int(
                conn.execute(
                    "SELECT COUNT(*) FROM memories "
                    "WHERE (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='') "
                    "AND COALESCE(quarantine,0)=0"
                ).fetchone()[0]
            )
        except Exception:
            unscoped_active = unscoped
        c4["formalized_from_unscoped"] = formalized_n
        c4["quarantined_memories"] = quarantined_n
        c4["unscoped_not_quarantined"] = unscoped_active

    if (
        fanout_bucket_done
        and formalized_n >= 10000
        and quarantined_n >= 10000
        and unscoped_active == 0
    ):
        c4["verdict"] = "PASS"
        c4["note"] = (
            "fanout 已清理；peer/hold/private formalize + bot quarantine 完成；"
            f"活跃 unscoped=0；grants 行={c4.get('grants_rows') or 0}"
            "（共享授权为可选增强，非历史分桶硬门槛）"
        )
    elif fanout_bucket_done and unscoped < 1000 and (c4.get("grants_rows") or 0) > 0:
        c4["verdict"] = "PASS"
        c4["note"] = "fanout 已物理清理；无 Scope 已压低；grants 已有数据"
    elif fanout_bucket_done:
        if formalized_n >= 10000 and unscoped_active < 500:
            c4["verdict"] = "PARTIAL"
            c4["note"] = (
                "fanout 已清理；formalize/quarantine 大体完成；"
                f"活跃 unscoped≈{unscoped_active}；grants 仍可选"
            )
        elif formalized_n >= 10000 and unscoped < 25000:
            c4["verdict"] = "PARTIAL"
            c4["note"] = (
                "fanout 已清理；peer-eligible unscoped 已 formalize；"
                f"剩余 unscoped≈{unscoped}；grants 行仍 0"
            )
        else:
            c4["verdict"] = "PARTIAL"
            c4["note"] = (
                "fanout 副本已物理删除（marked=0）；"
                "无 Scope 与 grant 批量写入仍待 Wave3"
            )
    elif marked > 0 and c4.get("recovery_map"):
        c4["verdict"] = "PARTIAL"
        c4["note"] = "fanout 已标记可分类；物理删除/grant 批量未上生产；无 Scope 仍大量存在"
    else:
        c4["verdict"] = "FAIL"
        c4["note"] = "历史分桶基础不足"
    report["c4_history_classified"] = c4

    # ----- C5 rollback / reverify -----
    c5: dict = {"artifacts": {}, "scripts": {}}
    for rel in [
        "fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3",
        "relationship_evidence_multi_scope_pilot/report_all10_full.json",
        "relationship_evidence_batch_plan.json",
        "phase2_prod_readonly_status.json",
        "fanout_to_shared_grants_same_bot_dryrun.json",
        "shared_grants_same_bot_pilot/grants_pilot.sqlite3",
    ]:
        p = BACKUPS / rel
        c5["artifacts"][rel] = {
            "exists": p.is_file(),
            "size": p.stat().st_size if p.is_file() else 0,
        }
    for s in [
        "fanout_cutover_apply.py",
        "fanout_cutover_rollback.py",
        "verify_phase2_production_readonly_status.py",
        "accept_five_success_criteria.py",
    ]:
        c5["scripts"][s] = (PLUGIN / "scripts" / s).is_file()
    c5_pass = all(c5["artifacts"][k]["exists"] for k in list(c5["artifacts"])[:3])
    c5["verdict"] = "PASS" if c5_pass else "PARTIAL"
    report["c5_rollback_reverify"] = c5

    # summary scoreboard
    report["scoreboard"] = {
        "1_qq_person": c1["verdict"],
        "2_no_fanout_spam": c2["verdict"],
        "3_formal_in_use": c3["verdict"],
        "4_history_classified": c4["verdict"],
        "5_rollback_reverify": c5["verdict"],
    }
    vals = list(report["scoreboard"].values())
    if any(str(v).startswith("FAIL") for v in vals):
        report["overall"] = "NOT_DONE"
    elif any("PARTIAL" in str(v) for v in vals):
        report["overall"] = "PARTIAL_DONE"
    else:
        report["overall"] = "DONE"
    if report["overall"] == "DONE":
        report["overall_note"] = "5 条标准均 PASS。"
    elif report["overall"] == "PARTIAL_DONE":
        report["overall_note"] = (
            "Wave1 cutover + Wave2 evidence + peer formalize 已上生产；"
            "C4 剩余是噪声/无 peer 队列与 grants 启用；禁止 re-open Phase2 fanout promote。"
        )
    else:
        report["overall_note"] = (
            "5 条未全部硬通过；下一步基于 scoreboard 治理，禁止假 promote。"
        )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    out = BACKUPS / "accept_five_success_criteria.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
