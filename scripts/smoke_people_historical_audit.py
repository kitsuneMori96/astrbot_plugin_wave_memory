#!/usr/bin/env python3
"""Readonly smoke for scoped historical audit summary against production DB."""

from __future__ import annotations

import json
from types import SimpleNamespace

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.scoped_soul_repo import ScopedSoulRepository
from webui.blueprints.people import _historical_audit_summary_for_subject


def main() -> int:
    prod = "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"
    cm = ConnectionManager(prod)
    repo = ScopedSoulRepository(cm)
    scope = RuntimeScope(
        "yushu",
        "group",
        SessionRef("羽书:group:398291136", "羽书", "group", "398291136"),
    )
    subject = "羽书:user:1923563505"
    summary = _historical_audit_summary_for_subject(repo, scope, subject)
    formal = cm.execute_read(
        """SELECT affinity FROM scoped_soul_relationships
            WHERE bot_id=? AND session_id=? AND visibility='group'
              AND subject_principal_id=?""",
        ("yushu", "羽书:group:398291136", subject),
    ).fetchone()
    print(
        json.dumps(
            {
                "subject": subject,
                "summary_available": summary.get("available"),
                "summary_total": summary.get("total"),
                "by_type": summary.get("by_type"),
                "readonly": summary.get("readonly"),
                "affects_affinity": summary.get("affects_affinity"),
                "affinity": formal[0] if formal else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    ok = (
        summary.get("available") is True
        and int(summary.get("total") or 0) > 0
        and summary.get("affects_affinity") is False
        and formal is not None
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
