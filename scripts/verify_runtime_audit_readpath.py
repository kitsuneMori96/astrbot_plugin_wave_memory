#!/usr/bin/env python3
"""Verify plugin code + import surface for historical audit read path."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")


def main() -> int:
    out: dict = {"plugin_root": str(PLUGIN)}
    files = {
        "affinity_update": PLUGIN / "tools" / "affinity_update.py",
        "scoped_soul_repo": PLUGIN / "engine" / "db" / "scoped_soul_repo.py",
        "calibration_migration": PLUGIN
        / "engine"
        / "db"
        / "migrations"
        / "scoped_relationship_calibration.py",
    }
    for key, path in files.items():
        text = path.read_text(encoding="utf-8", errors="replace")
        out[key] = {
            "exists": path.is_file(),
            "has_audit_api": (
                "list_legacy_relationship_audit_summary" in text
                or "_legacy_audit_lines" in text
            ),
            "has_history_label": "历史事件审计" in text,
            "has_audit_index": "idx_legacy_rel_events_subject" in text,
        }

    sys.path.insert(0, str(PLUGIN))
    from engine.db.scoped_soul_repo import ScopedSoulRepository
    from tools.affinity_update import WaveMemoryAffinityTool

    out["import"] = {
        "tool_has_legacy_lines": hasattr(WaveMemoryAffinityTool, "_legacy_audit_lines"),
        "repo_has_summary": hasattr(
            ScopedSoulRepository, "list_legacy_relationship_audit_summary"
        ),
    }
    out["ready"] = all(
        [
            out["affinity_update"]["has_audit_api"],
            out["scoped_soul_repo"]["has_audit_api"],
            out["import"]["tool_has_legacy_lines"],
            out["import"]["repo_has_summary"],
        ]
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
