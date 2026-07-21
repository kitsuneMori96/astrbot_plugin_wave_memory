#!/usr/bin/env python3
"""Readonly inventory of active (not quarantined) unscoped memories + hold-group draft map.

Never writes production. Draft scope map is for operator review only.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

NUM = re.compile(r"^\d{5,}$")
SKIP = ("arc", "book_", "oni_", "private:")
NOISE = {"", "bot", "bot_remember"}


def _platform_for_bot(bot: str) -> str:
    if bot == "yushu":
        return "羽书"
    if bot == "baizz":
        return "白真真"
    return bot or "unknown"


def inventory(db: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    try:
        tabs = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        peers = {
            str(r[0])
            for r in conn.execute(
                """
                SELECT DISTINCT group_id FROM memories
                 WHERE COALESCE(bot_id,'')!=''
                   AND COALESCE(session_id,'')!=''
                   AND visibility='group'
                   AND COALESCE(group_id,'')!=''
                """
            )
        }
        rows = conn.execute(
            """
            SELECT id, group_id, sender_id, sender_name,
                   substr(COALESCE(content,''),1,40)
              FROM memories
             WHERE (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='')
               AND COALESCE(quarantine,0)=0
            """
        ).fetchall()
        buckets: Counter[str] = Counter()
        hold: Counter[str] = Counter()
        samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for mid, gid, sid, sname, content in rows:
            g = str(gid or "")
            sid_s = str(sid or "").strip()
            sname_s = str(sname or "")
            kind = None
            for p in SKIP:
                if g.startswith(p) or g.lower().startswith(p):
                    kind = f"prefix:{p}"
                    break
            if kind is None:
                if not g:
                    kind = "empty_group"
                elif not NUM.match(g):
                    kind = "non_numeric_group"
                elif sid_s in NOISE or sid_s.startswith("[") or ("经历" in sname_s):
                    kind = "noise_residual"
                elif g not in peers:
                    kind = "no_peer_numeric"
                    hold[g] += 1
                else:
                    kind = "has_peer_still_unscoped"
            buckets[kind] += 1
            if len(samples[kind]) < 3:
                samples[kind].append(
                    {
                        "id": mid,
                        "group_id": g,
                        "sender_id": sid_s,
                        "sender_name": sname_s,
                        "content": content,
                    }
                )

        hold_detail = []
        for g, n in hold.most_common(30):
            prof_by_bot: list[tuple[str, int]] = []
            if "user_profiles" in tabs:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(user_profiles)")]
                if "bot_id" in cols:
                    prof_by_bot = [
                        (str(b or ""), int(nn))
                        for b, nn in conn.execute(
                            "SELECT bot_id, COUNT(*) FROM user_profiles WHERE group_id=? GROUP BY bot_id",
                            (g,),
                        )
                    ]
                else:
                    nn = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM user_profiles WHERE group_id=?", (g,)
                        ).fetchone()[0]
                    )
                    prof_by_bot = [("", nn)]
            soul = []
            if "scoped_soul_relationships" in tabs:
                soul = [
                    (str(a), str(b), int(nn))
                    for a, b, nn in conn.execute(
                        """
                        SELECT bot_id, session_id, COUNT(*)
                          FROM scoped_soul_relationships
                         WHERE visibility='group' AND session_id LIKE ?
                         GROUP BY bot_id, session_id
                        """,
                        (f"%{g}%",),
                    )
                ]
            top_s = conn.execute(
                """
                SELECT sender_id, sender_name, COUNT(*) n FROM memories
                 WHERE (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='')
                   AND COALESCE(quarantine,0)=0 AND group_id=?
                 GROUP BY 1,2 ORDER BY n DESC LIMIT 5
                """,
                (g,),
            ).fetchall()
            rec: dict[str, Any]
            if soul:
                soul_sorted = sorted(soul, key=lambda x: (0 if x[0] == "yushu" else 1, -x[2]))
                a, b, _nn = soul_sorted[0]
                rec = {
                    "bot_id": a,
                    "session_id": b,
                    "source": "soul",
                    "confidence": "high",
                }
            elif prof_by_bot:
                bots = sorted(prof_by_bot, key=lambda x: -x[1])
                if len(bots) == 1 or (len(bots) > 1 and bots[0][1] >= max(1, bots[1][1]) * 3):
                    bot = bots[0][0] or "yushu"
                    rec = {
                        "bot_id": bot,
                        "session_id": f"{_platform_for_bot(bot)}:group:{g}",
                        "source": "profiles_majority",
                        "confidence": "medium" if len(bots) == 1 else "low_medium",
                    }
                else:
                    rec = {
                        "confidence": "ambiguous",
                        "options": [
                            {
                                "bot_id": b,
                                "session_id": f"{_platform_for_bot(b)}:group:{g}",
                                "profiles": n,
                            }
                            for b, n in bots[:3]
                        ],
                    }
            else:
                rec = {"confidence": "none", "note": "no soul/profile signal"}
            hold_detail.append(
                {
                    "group_id": g,
                    "active_unscoped_n": n,
                    "profiles_by_bot": [
                        {"bot_id": b, "n": nn} for b, nn in prof_by_bot
                    ],
                    "soul": [
                        {"bot_id": a, "session_id": b, "n": nn} for a, b, nn in soul
                    ],
                    "top_senders": [
                        {"sender_id": a, "sender_name": b, "n": int(nn)}
                        for a, b, nn in top_s
                    ],
                    "recommended_scope_map": rec,
                }
            )

        missing = 0
        summaries = 0
        if "scoped_soul_relationships" in tabs:
            summaries = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM scoped_soul_relationships
                     WHERE evidence LIKE '%historical_audit_summary%'
                    """
                ).fetchone()[0]
            )
            if "scoped_soul_relationship_legacy_events" in tabs:
                missing = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM scoped_soul_relationships r
                         WHERE (r.evidence IS NULL OR r.evidence NOT LIKE '%historical_audit_summary%')
                           AND EXISTS (
                             SELECT 1 FROM scoped_soul_relationship_legacy_events e
                              WHERE e.bot_id=r.bot_id AND e.session_id=r.session_id
                                AND e.visibility=r.visibility
                                AND e.subject_principal_id=r.subject_principal_id
                           )
                        """
                    ).fetchone()[0]
                )

        draft_groups: dict[str, Any] = {}
        for h in hold_detail:
            rec = h.get("recommended_scope_map") or {}
            if rec.get("confidence") in {"high", "medium"} and rec.get("bot_id") and rec.get(
                "session_id"
            ):
                draft_groups[h["group_id"]] = {
                    "bot_id": rec["bot_id"],
                    "session_id": rec["session_id"],
                    "visibility": "group",
                    "source": rec.get("source"),
                    "confidence": rec.get("confidence"),
                    "active_unscoped_n": h["active_unscoped_n"],
                }

        return {
            "mode": "post_quarantine_active_unscoped_inventory",
            "generated_at": time.time(),
            "active_unscoped": len(rows),
            "buckets": dict(buckets),
            "hold_groups": hold_detail,
            "samples": dict(samples),
            "evidence_summaries": summaries,
            "audit_missing_summary": missing,
            "draft_scope_map_groups": draft_groups,
            "writes_production": False,
            "phase2_promote_allowed": False,
            "auto_apply_hold_map": False,
            "note": "draft map is review-only; ambiguous groups require operator choice",
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    p.add_argument(
        "--report",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "post_quarantine_active_unscoped_inventory.json"
        ),
    )
    p.add_argument(
        "--draft-map",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "unscoped_owned_formalize_pilot/scope_map_hold_groups_draft.json"
        ),
    )
    args = p.parse_args(argv)
    report = inventory(Path(args.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(text + "\n", encoding="utf-8")
    draft = {
        "_meta": {
            "draft": True,
            "do_not_apply_without_auth": True,
            "generated_at": time.time(),
        },
        "groups": report.get("draft_scope_map_groups") or {},
    }
    Path(args.draft_map).parent.mkdir(parents=True, exist_ok=True)
    Path(args.draft_map).write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
