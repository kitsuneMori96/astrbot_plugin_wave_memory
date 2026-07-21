#!/usr/bin/env python3
"""Readonly verification: WebUI static entry + cutover hard gates."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")


def main() -> int:
    idx = PLUGIN / "webui" / "static" / "app" / "index.html"
    assets = PLUGIN / "webui" / "static" / "app" / "assets"
    text = idx.read_text(encoding="utf-8") if idx.exists() else ""
    js_refs = re.findall(r"assets/([^\"']+\.js)", text)
    css_refs = re.findall(r"assets/([^\"']+\.css)", text)

    people_page = (
        PLUGIN / "webui" / "frontend" / "src" / "pages" / "people" / "PeoplePage.tsx"
    )
    people_api = PLUGIN / "webui" / "blueprints" / "people.py"

    webui = {
        "index_exists": idx.exists(),
        "js_refs": js_refs,
        "css_refs": css_refs,
        "js_present": [
            {
                "name": name,
                "exists": (assets / name).exists(),
                "size": (assets / name).stat().st_size if (assets / name).exists() else None,
            }
            for name in js_refs
        ],
        "css_present": [
            {
                "name": name,
                "exists": (assets / name).exists(),
                "size": (assets / name).stat().st_size if (assets / name).exists() else None,
            }
            for name in css_refs
        ],
        "people_page_has_panel": (
            people_page.exists()
            and "HistoricalAuditPanel" in people_page.read_text(encoding="utf-8", errors="replace")
        ),
        "people_api_has_route": (
            people_api.exists()
            and "historical-audit" in people_api.read_text(encoding="utf-8", errors="replace")
        ),
        "built_bundle_mentions_audit": False,
    }
    # Grep latest built js for Chinese label if present.
    for item in webui["js_present"]:
        if not item["exists"]:
            continue
        blob = (assets / item["name"]).read_bytes()
        if "历史事件审计".encode("utf-8") in blob or b"historical-audit" in blob:
            webui["built_bundle_mentions_audit"] = True
            break

    # Cutover runbook gates via subprocess to reuse current script.
    runbook = subprocess.check_output(
        [
            sys.executable,
            str(PLUGIN / "scripts" / "fanout_cutover_runbook.py"),
        ],
        text=True,
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(PLUGIN)},
    )
    plan = json.loads(runbook)
    report = {
        "webui": webui,
        "cutover": {
            "package_safe_for_cutover": plan.get("package_safe_for_cutover"),
            "needs_refresh_before_cutover": plan.get("needs_refresh_before_cutover"),
            "hard_gates": plan.get("hard_gates"),
            "drift": {
                "audit_preserved": (plan.get("drift") or {}).get("audit_preserved"),
                "formal_delta": (plan.get("drift") or {}).get("formal_delta"),
                "vac_marked": (plan.get("drift") or {}).get("vac_marked"),
                "prod_non_fanout_timestamp_newer_than_vac_max_ts": (
                    plan.get("drift") or {}
                ).get("prod_non_fanout_timestamp_newer_than_vac_max_ts"),
            },
            "prod_audit_rows": (plan.get("prod") or {}).get("audit_rows"),
            "vac_audit_rows": (plan.get("vacuumed") or {}).get("audit_rows"),
            "production_apply_implemented": plan.get("production_apply_implemented"),
            "phase2_promote_allowed": plan.get("phase2_promote_allowed"),
        },
    }
    ready = all(
        [
            webui["index_exists"],
            all(x["exists"] for x in webui["js_present"]),
            all(x["exists"] for x in webui["css_present"]),
            webui["people_page_has_panel"],
            webui["people_api_has_route"],
            webui["built_bundle_mentions_audit"],
            report["cutover"]["package_safe_for_cutover"] is True,
            report["cutover"]["needs_refresh_before_cutover"] is False,
        ]
    )
    report["ready_for_user_observation"] = ready
    report["ready_for_cutover_without_auth"] = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
