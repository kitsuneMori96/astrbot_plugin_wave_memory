#!/usr/bin/env python3
"""Runtime smoke: QQ resolve + collapse + formal affinity read (readonly DB).

Does not write production. Used to strengthen 5-criteria C1/C2 acceptance.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

PROD = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
PLUGIN = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")
OUT = Path(
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
    "smoke_qq_person_and_collapse.json"
)


def main() -> int:
    sys.path.insert(0, str(PLUGIN))
    from domain.scope import RuntimeScope, SessionRef
    from engine.memory_collapse import collapse_memories, is_fanout_duplicate
    from tools.person_identity import is_qq_id, resolve_user_id

    conn = sqlite3.connect(f"file:{PROD.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    db = SimpleNamespace(conn=conn)

    scope = RuntimeScope(
        bot_id="yushu",
        visibility="group",
        session=SessionRef(
            id="羽书:group:398291136",
            platform_id="羽书",
            kind="group",
            conversation_id="398291136",
        ),
    )

    report: dict = {
        "mode": "runtime_smoke_qq_person_collapse",
        "generated_at": time.time(),
        "scope": scope.to_dict() if hasattr(scope, "to_dict") else str(scope),
    }

    # Sample real speakers from main group
    samples = conn.execute(
        """
        SELECT sender_id, sender_name, COUNT(*) AS n
          FROM memories
         WHERE group_id='398291136'
           AND COALESCE(sender_id,'') != ''
           AND COALESCE(sender_name,'') != ''
           AND sender_id GLOB '[0-9]*'
         GROUP BY sender_id
         ORDER BY n DESC
         LIMIT 5
        """
    ).fetchall()
    report["samples"] = [
        {"sender_id": r[0], "sender_name": r[1], "msgs": r[2]} for r in samples
    ]

    resolves = []
    for sid, sname, _n in samples:
        by_qq = resolve_user_id(db, str(sid), scope)
        by_name = resolve_user_id(db, str(sname), scope)
        resolves.append(
            {
                "input_qq": sid,
                "input_name": sname,
                "resolve_by_qq": by_qq,
                "resolve_by_name": by_name,
                "qq_ok": by_qq == str(sid) and is_qq_id(by_qq),
                "name_ok": by_name == str(sid) and is_qq_id(by_name),
            }
        )
    report["resolves"] = resolves
    report["resolve_qq_pass"] = all(r["qq_ok"] for r in resolves) and bool(resolves)
    report["resolve_name_pass_count"] = sum(1 for r in resolves if r["name_ok"])
    report["resolve_name_total"] = len(resolves)

    # Formal affinity row exists for first resolved user
    formal_hits = []
    for r in resolves[:3]:
        uid = r["resolve_by_qq"] or r["input_qq"]
        principal = f"羽书:user:{uid}"
        row = conn.execute(
            """
            SELECT affinity, state FROM scoped_soul_relationships
             WHERE bot_id='yushu' AND session_id='羽书:group:398291136'
               AND visibility='group' AND subject_principal_id=?
            """,
            (principal,),
        ).fetchone()
        formal_hits.append(
            {
                "principal": principal,
                "found": row is not None,
                "affinity": row[0] if row else None,
                "state": row[1] if row else None,
            }
        )
    report["formal_affinity_hits"] = formal_hits
    report["formal_hit_any"] = any(h["found"] for h in formal_hits)

    # Collapse: same family / same text+sender — current group preferred, fanout dropped
    collapse_case = {"ran": False}
    family = "legacy:smoke-family-1"
    shared_content = "这是谁？smoke-collapse-unique-content-398291136"
    shared_sender = "10001"
    mems = [
        {
            "id": 1,
            "content": shared_content,
            "group_id": "398291136",
            "sender_id": shared_sender,
            "provenance": json.dumps(
                {"projection_kind": "owned", "fanout_family_id": family}
            ),
            "score": 0.80,
            "timestamp": 100.0,
        },
        {
            "id": 2,
            "content": shared_content,
            "group_id": "150727649",
            "sender_id": shared_sender,
            "provenance": json.dumps(
                {
                    "projection_kind": "fanout_duplicate",
                    "fanout_family_id": family,
                }
            ),
            "score": 0.99,
            "timestamp": 101.0,
            "_fanout_duplicate": True,
        },
        {
            "id": 3,
            "content": shared_content,
            "group_id": "871953949",
            "sender_id": shared_sender,
            "provenance": json.dumps(
                {
                    "projection_kind": "fanout_duplicate",
                    "fanout_family_id": family,
                }
            ),
            "score": 0.95,
            "timestamp": 102.0,
        },
    ]
    collapsed = collapse_memories(mems, current_group_id="398291136")
    collapse_case = {
        "ran": True,
        "input_n": len(mems),
        "output_n": len(collapsed),
        "kept_ids": [m.get("id") for m in collapsed],
        "kept_groups": [m.get("group_id") for m in collapsed],
        "prefers_current_group": (
            len(collapsed) == 1 and str(collapsed[0].get("group_id")) == "398291136"
        ),
        "fanout_flag_on_foreign": is_fanout_duplicate(mems[1]),
    }
    report["collapse"] = collapse_case
    report["collapse_pass"] = bool(
        collapse_case.get("ran")
        and collapse_case.get("output_n") == 1
        and collapse_case.get("prefers_current_group")
    )

    report["c1_runtime_ok"] = bool(
        report["resolve_qq_pass"] and report["formal_hit_any"]
    )
    report["c2_runtime_ok"] = bool(report["collapse_pass"])
    report["ok"] = report["c1_runtime_ok"] and report["c2_runtime_ok"]
    report["writes_production"] = False
    report["phase2_promote_allowed"] = False

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    conn.close()
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
