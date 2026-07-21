#!/usr/bin/env python3
"""Write a readonly status board for Phase2/relationship residual work.

Never switches production. Captures cutover gates + audit/UI readiness snapshot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PLUGIN = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")
OUT = Path(
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
    "phase2_status_board.json"
)
OUT_MD = Path(
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
    "phase2_status_board.md"
)


def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(PLUGIN)}
    plan = json.loads(
        subprocess.check_output(
            [sys.executable, str(PLUGIN / "scripts" / "fanout_cutover_runbook.py")],
            text=True,
            env=env,
        )
    )
    webui = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(PLUGIN / "scripts" / "verify_webui_and_cutover_status.py"),
            ],
            text=True,
            env=env,
        )
    )
    runtime = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(PLUGIN / "scripts" / "verify_runtime_audit_readpath.py"),
            ],
            text=True,
            env=env,
        )
    )
    people = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(PLUGIN / "scripts" / "smoke_people_historical_audit.py"),
            ],
            text=True,
            env=env,
        )
    )

    board = {
        "generated_at": time.time(),
        "phase2_promote_allowed": False,
        "production_db_switched": False,
        "cutover": {
            "package_safe_for_cutover": plan.get("package_safe_for_cutover"),
            "needs_refresh_before_cutover": plan.get("needs_refresh_before_cutover"),
            "hard_gates": plan.get("hard_gates"),
            "prod_audit_rows": (plan.get("prod") or {}).get("audit_rows"),
            "vac_audit_rows": (plan.get("vacuumed") or {}).get("audit_rows"),
            "authorization_required": plan.get("authorization_required"),
        },
        "webui": webui.get("webui"),
        "runtime_audit_code": runtime,
        "people_audit_smoke": people,
        "blocked_remaining": [
            {
                "text": "Phase 2 strict Scope fanout 已回滚；待共享记忆语义重构后再评估 staged 迁移",
                "status": "blocked",
                "protected": True,
                "meaning": "fanout promote closed; physical cutover needs explicit auth",
            },
            {
                "text": "迁移旧版关系证据、数值与排行能力到正式 Scoped Relationship",
                "status": "blocked",
                "meaning": "historical audit imported as side-channel; live score replay needs product decision",
            },
        ],
        "completed_autonomous": [
            "anti-fanout gates + mark/collapse",
            "event_audit_only production import (91339 rows, affinity unchanged)",
            "affinity/API/People/Soul historical audit read path",
            "cutover package with audit preservation hard gates",
        ],
        "next_requires_user": [
            "explicit fanout DB cutover authorization",
            "or product decision on live evidence/score migration",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")

    md = f"""# Phase2 / Relationship 状态板

生成时间 unix: {board['generated_at']}

## 红线

- Phase2 promote: **禁止**
- 生产 DB 已切换: **否**

## Cutover 包

- package_safe_for_cutover: **{board['cutover']['package_safe_for_cutover']}**
- needs_refresh: **{board['cutover']['needs_refresh_before_cutover']}**
- audit prod/vac: **{board['cutover']['prod_audit_rows']} / {board['cutover']['vac_audit_rows']}**
- hard_gates: `{json.dumps(board['cutover']['hard_gates'], ensure_ascii=False)}`

## 只读链路

- WebUI bundle mentions audit: **{board['webui'].get('built_bundle_mentions_audit')}**
- runtime audit code ready: **{board['runtime_audit_code'].get('ready')}**
- people smoke affinity/total: **{board['people_audit_smoke'].get('affinity')} / {board['people_audit_smoke'].get('summary_total')}**

## 下一步（需用户）

1. 明确授权 fanout cutover，或
2. 明确历史事件是否允许写回 live formal 分
"""
    OUT_MD.write_text(md, encoding="utf-8")
    # also copy markdown into plugin docs for repo visibility if writable
    docs = PLUGIN / "docs" / "phase2-status-board.md"
    try:
        docs.write_text(md, encoding="utf-8")
    except OSError:
        pass
    print(json.dumps({"ok": True, "json": str(OUT), "md": str(OUT_MD), "board": board}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
