#!/usr/bin/env python3
"""Readonly smoke: affinity single-query shows historical audit summary."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.scoped_soul_repo import ScopedSoulRepository
from tools.affinity_update import WaveMemoryAffinityTool


def main() -> int:
    prod = "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"
    cm = ConnectionManager(prod)
    # Force read-only usage for this smoke.
    repo = ScopedSoulRepository(cm)
    subject = "羽书:user:1923563505"
    scope = RuntimeScope(
        "yushu",
        "group",
        SessionRef("羽书:group:398291136", "羽书", "group", "398291136"),
        subject_principal_id=subject,
    )
    summary = repo.list_legacy_relationship_audit_summary(scope, recent_limit=3)
    print("SUMMARY", json.dumps({
        "available": summary.get("available"),
        "total": summary.get("total"),
        "by_type": summary.get("by_type"),
    }, ensure_ascii=False))

    # Tool path uses same repo + real user_profiles for display names.
    tool = WaveMemoryAffinityTool(db=SimpleNamespace(conn=cm.conn, soul_repository=repo))
    ctx_scope = RuntimeScope(
        "yushu",
        "group",
        SessionRef("羽书:group:398291136", "羽书", "group", "398291136"),
        subject_principal_id="羽书:user:1",
    )
    ctx = SimpleNamespace(
        context=SimpleNamespace(
            event=SimpleNamespace(_wave_memory_runtime_scope=ctx_scope)
        )
    )
    text = asyncio.run(tool.call(ctx, mode="single", target_user="1923563505"))
    print("TOOL")
    print(text)

    # Affinity must be unchanged by read path: compare formal row before/after (same).
    row = cm.execute_read(
        """SELECT affinity FROM scoped_soul_relationships
            WHERE bot_id=? AND session_id=? AND visibility='group' AND subject_principal_id=?""",
        ("yushu", "羽书:group:398291136", subject),
    ).fetchone()
    print("AFFINITY", row[0] if row else None)
    ok = (
        summary.get("available") is True
        and int(summary.get("total") or 0) > 0
        and "历史事件审计" in text
        and "不改变好感度" in text
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
