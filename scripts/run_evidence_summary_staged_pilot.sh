#!/usr/bin/env bash
set -euo pipefail
# Build a tiny staged relationship slice from production (read-only attach),
# apply evidence summaries with affinity guards, never touch live wave_memory.db.
PROD=/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db
PLUGIN=/AstrBot/data/plugins/astrbot_plugin_wave_memory
PILOT=/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/relationship_evidence_summary_pilot
mkdir -p "$PILOT"
STAGED="$PILOT/relationships_main_group_slice.sqlite3"
rm -f "$STAGED"
export PYTHONPATH="$PLUGIN"
python - <<'PY'
import sqlite3
from pathlib import Path

prod = "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"
staged = Path(
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
    "relationship_evidence_summary_pilot/relationships_main_group_slice.sqlite3"
)
bot, sess = "yushu", "羽书:group:398291136"
src = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
dst = sqlite3.connect(staged.as_posix())
dst.executescript(
    """
    CREATE TABLE scoped_soul_relationships(
        bot_id TEXT, session_id TEXT, visibility TEXT,
        subject_principal_id TEXT, affinity INTEGER, state TEXT,
        dimensions TEXT, revision INTEGER, evidence TEXT, updated_at REAL
    );
    CREATE TABLE scoped_soul_relationship_legacy_events(
        id INTEGER PRIMARY KEY, bot_id TEXT, session_id TEXT, visibility TEXT,
        subject_principal_id TEXT, event_type TEXT, reason TEXT, occurred_at REAL,
        legacy_event_id TEXT, scope_key TEXT, group_id TEXT, dimension TEXT,
        delta REAL, source_episode_id INTEGER, source_memory_id INTEGER,
        source_hash TEXT, event_hash TEXT, run_id TEXT, created_at REAL
    );
    """
)
# copy formal rows for main group
rows = src.execute(
    """
    SELECT bot_id, session_id, visibility, subject_principal_id, affinity, state,
           dimensions, revision, evidence, updated_at
      FROM scoped_soul_relationships
     WHERE bot_id=? AND session_id=? AND visibility='group'
    """,
    (bot, sess),
).fetchall()
dst.executemany(
    "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?,?,?,?,?)",
    rows,
)
# copy audit events for same scope (may be large but main-group only)
# use slim columns that exist
cols = [r[1] for r in src.execute("PRAGMA table_info(scoped_soul_relationship_legacy_events)")]
want = [
    "id",
    "bot_id",
    "session_id",
    "visibility",
    "subject_principal_id",
    "event_type",
    "reason",
    "occurred_at",
    "legacy_event_id",
    "scope_key",
    "group_id",
    "dimension",
    "delta",
    "source_episode_id",
    "source_memory_id",
    "source_hash",
    "event_hash",
    "run_id",
    "created_at",
]
sel = [c for c in want if c in cols]
# pad missing with nulls in insert order of dst table
dst_cols = [
    "id",
    "bot_id",
    "session_id",
    "visibility",
    "subject_principal_id",
    "event_type",
    "reason",
    "occurred_at",
    "legacy_event_id",
    "scope_key",
    "group_id",
    "dimension",
    "delta",
    "source_episode_id",
    "source_memory_id",
    "source_hash",
    "event_hash",
    "run_id",
    "created_at",
]
q = f"SELECT {', '.join(sel)} FROM scoped_soul_relationship_legacy_events WHERE bot_id=? AND session_id=? AND visibility='group'"
ev = src.execute(q, (bot, sess)).fetchall()
for row in ev:
    m = dict(zip(sel, row))
    vals = [m.get(c) for c in dst_cols]
    dst.execute(
        f"INSERT INTO scoped_soul_relationship_legacy_events ({','.join(dst_cols)}) VALUES ({','.join('?'*len(dst_cols))})",
        vals,
    )
dst.commit()
print("staged_formal", len(rows), "staged_audit", len(ev), "path", staged)
# fingerprint affinities before
fp = sorted(
    (r[0], r[1])
    for r in dst.execute(
        "SELECT subject_principal_id, affinity FROM scoped_soul_relationships"
    )
)
Path(str(staged) + ".affinity_before.json").write_text(
    __import__("json").dumps(fp, ensure_ascii=False), encoding="utf-8"
)
print("affinity_fp_count", len(fp))
src.close()
dst.close()
PY
python "$PLUGIN/scripts/relationship_evidence_summary_dryrun.py" \
  --db "$STAGED" \
  --limit 50 \
  --apply \
  --apply-db "$STAGED" \
  --apply-limit 30 \
  --report "$PILOT/apply_report.json"
python - <<'PY'
import json
import sqlite3
from pathlib import Path

staged = Path(
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
    "relationship_evidence_summary_pilot/relationships_main_group_slice.sqlite3"
)
before_list = json.loads(Path(str(staged) + ".affinity_before.json").read_text(encoding="utf-8"))
before = {str(a): b for a, b in before_list}
conn = sqlite3.connect(staged.as_posix())
after = {
    str(r[0]): r[1]
    for r in conn.execute(
        "SELECT subject_principal_id, affinity FROM scoped_soul_relationships"
    )
}
with_summary = conn.execute(
    """
    SELECT COUNT(*) FROM scoped_soul_relationships
     WHERE evidence LIKE '%historical_audit_summary%'
    """
).fetchone()[0]
sample = conn.execute(
    """
    SELECT subject_principal_id, affinity, substr(evidence,1,120)
      FROM scoped_soul_relationships
     WHERE evidence LIKE '%historical_audit_summary%'
     LIMIT 2
    """
).fetchall()
conn.close()
assert before == after, f"affinity fingerprint changed: {set(before.items()) ^ set(after.items())}"
assert with_summary >= 1
print("AFFINITY_UNCHANGED", len(after))
print("with_summary", with_summary)
print("sample", sample)
# production untouched: live evidence should still be machine-only for same subjects
pc = sqlite3.connect(
    "file:/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db?mode=ro",
    uri=True,
)
prod_summary = pc.execute(
    """
    SELECT COUNT(*) FROM scoped_soul_relationships
     WHERE bot_id='yushu' AND session_id='羽书:group:398291136' AND visibility='group'
       AND evidence LIKE '%historical_audit_summary%'
    """
).fetchone()[0]
pc.close()
print("prod_main_group_summary_count", prod_summary)
assert prod_summary == 0
print("PROD_UNTOUCHED")
print("EVIDENCE_PILOT_OK")
PY
