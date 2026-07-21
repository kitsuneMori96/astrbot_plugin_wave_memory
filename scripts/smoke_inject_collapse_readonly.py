#!/usr/bin/env python3
"""Readonly smoke: FTS candidates + collapse (no DB writes)."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    db = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"
    )
    current_group = sys.argv[2] if len(sys.argv) > 2 else "398291136"
    query = sys.argv[3] if len(sys.argv) > 3 else "你又卡了吗"

    # Prefer container plugin path
    for p in (
        "/AstrBot/data/plugins/astrbot_plugin_wave_memory",
        str(Path(__file__).resolve().parents[1]),
    ):
        if p not in sys.path:
            sys.path.insert(0, p)

    from engine.memory_collapse import collapse_key, collapse_memories

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT m.id, m.group_id, m.sender_id, m.content, m.timestamp,
               m.provenance, m.origin_fingerprint
          FROM fts_memories f
          JOIN memories m ON m.id = f.rowid
         WHERE fts_memories MATCH ?
           AND COALESCE(m.quarantine, 0) = 0
           AND COALESCE(m.memory_type, 'message') NOT IN
               ('archived', 'evicted', 'deleted', 'noise')
           AND COALESCE(m.source, '') != 'noise'
         ORDER BY rank
         LIMIT 40
        """,
        (query,),
    ).fetchall()
    mems = []
    for i, r in enumerate(rows):
        mems.append(
            {
                "id": r[0],
                "group_id": r[1],
                "sender_id": r[2],
                "content": r[3],
                "timestamp": r[4],
                "provenance": r[5],
                "origin_fingerprint": r[6],
                "score": 1.0 - i * 0.01,
                "_is_cross_group": str(r[1] or "") != current_group,
            }
        )
    collapsed = collapse_memories(mems, current_group_id=current_group)
    report = {
        "db": str(db),
        "query": query,
        "current_group": current_group,
        "fts_raw": len(mems),
        "raw_groups": sorted({str(m.get("group_id") or "") for m in mems}),
        "raw_unique_collapse_keys": len(Counter(collapse_key(m) for m in mems)),
        "after_collapse": len(collapsed),
        "kept_sample": [
            {
                "id": m.get("id"),
                "group_id": m.get("group_id"),
                "content": str(m.get("content") or "")[:40],
            }
            for m in collapsed[:8]
        ],
        "text_before_origin_in_module": True,
    }
    # Prove module order from source
    src = Path(
        "/AstrBot/data/plugins/astrbot_plugin_wave_memory/engine/memory_collapse.py"
    )
    if not src.exists():
        src = Path(__file__).resolve().parents[1] / "engine" / "memory_collapse.py"
    text = src.read_text(encoding="utf-8")
    report["text_before_origin_in_module"] = (
        text.find("text:{sender}") >= 0
        and text.find("origin:{origin}") >= 0
        and text.find("text:{sender}") < text.find("origin:{origin}")
    )
    # Wiring presence
    qe = (
        Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory/engine/query_engine.py")
        if Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory/engine/query_engine.py").exists()
        else Path(__file__).resolve().parents[1] / "engine" / "query_engine.py"
    )
    fts = (
        Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory/services/injection/channels/fts5.py")
        if Path(
            "/AstrBot/data/plugins/astrbot_plugin_wave_memory/services/injection/channels/fts5.py"
        ).exists()
        else Path(__file__).resolve().parents[1]
        / "services"
        / "injection"
        / "channels"
        / "fts5.py"
    )
    report["query_engine_collapse"] = "collapse_memories" in qe.read_text(encoding="utf-8")
    report["fts_collapse"] = "collapse_memories" in fts.read_text(encoding="utf-8")
    report["ok"] = (
        report["text_before_origin_in_module"]
        and report["query_engine_collapse"]
        and report["fts_collapse"]
        and report["after_collapse"] <= report["fts_raw"]
        and report["after_collapse"] <= report["raw_unique_collapse_keys"]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    conn.close()
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
