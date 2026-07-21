#!/usr/bin/env bash
set -euo pipefail
PILOT=/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/shared_grants_same_bot_pilot
PLUGIN=/AstrBot/data/plugins/astrbot_plugin_wave_memory
PROD=/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db
mkdir -p "$PILOT"
rm -f "$PILOT/grants_pilot.sqlite3"
python - <<'PY'
from pathlib import Path
import sqlite3
p = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/shared_grants_same_bot_pilot/grants_pilot.sqlite3")
sqlite3.connect(p.as_posix()).close()
print("pilot db ready")
PY
export PYTHONPATH="$PLUGIN"
python "$PLUGIN/scripts/fanout_to_shared_grants_dryrun.py" \
  --db "$PROD" \
  --same-bot-only \
  --family-limit 80 \
  --sample-output 3 \
  --apply \
  --confirmation grant-from-fanout-map \
  --writers-stopped \
  --apply-db "$PILOT/grants_pilot.sqlite3" \
  --apply-limit 200 \
  --report "$PILOT/pilot_apply_report.json"
python - <<'PY'
import sqlite3
from pathlib import Path

p = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/shared_grants_same_bot_pilot/grants_pilot.sqlite3")
c = sqlite3.connect(p.as_posix())
tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
n = c.execute("SELECT COUNT(*) FROM shared_memory_grants WHERE status='active'").fetchone()[0]
cross = c.execute(
    "SELECT COUNT(*) FROM shared_memory_grants WHERE status='active' AND owner_bot_id!=consumer_bot_id"
).fetchone()[0]
sample = c.execute(
    """SELECT memory_id, owner_group_id, consumer_group_id, owner_bot_id, consumer_bot_id
         FROM shared_memory_grants WHERE status='active' LIMIT 3"""
).fetchall()
print("tables", sorted(tables))
print("active_grants", n, "cross_bot", cross)
print("sample", sample)
assert "memories" not in tables
assert n > 0 and cross == 0
print("PILOT_OK")
c.close()

pc = sqlite3.connect(
    "file:/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db?mode=ro",
    uri=True,
)
pt = {r[0] for r in pc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "shared_memory_grants" in pt:
    pn = pc.execute("SELECT COUNT(*) FROM shared_memory_grants").fetchone()[0]
else:
    pn = -1
print("prod_shared_memory_grants_count", pn)
assert pn in (-1, 0)
print("PROD_UNTOUCHED")
pc.close()
PY
