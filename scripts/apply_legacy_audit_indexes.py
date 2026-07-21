
import json, sqlite3, time
from pathlib import Path
from engine.db.connection import ConnectionManager
from engine.db.migrations.scoped_relationship_calibration import ensure_scoped_relationship_calibration_schema
from services.legacy_relationship_migration import _ensure_audit_tables

prod = Path('/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db')
vac = Path('/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3')

def index_and_time(db_path: Path, label: str):
    # formal affinity fingerprint before
    ro = sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True, timeout=60)
    before_formal = ro.execute('SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships').fetchone()
    before_audit = ro.execute('SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events').fetchone()[0]
    t0 = time.time()
    n0 = ro.execute(
        "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events WHERE bot_id=? AND session_id=? AND visibility=? AND subject_principal_id=?",
        ('yushu','羽书:group:398291136','group','羽书:user:1923563505'),
    ).fetchone()[0]
    t_before = time.time() - t0
    ro.close()

    if label == 'prod':
        cm = ConnectionManager(str(db_path))
        ensure_scoped_relationship_calibration_schema(cm)
        # also ensure via migration helper path used by stage
        with cm.write_transaction() as tx:
            _ensure_audit_tables(tx)
        idxs = cm.execute_read(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='scoped_soul_relationship_legacy_events' ORDER BY name"
        ).fetchall()
        t1 = time.time()
        n1 = cm.execute_read(
            "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events WHERE bot_id=? AND session_id=? AND visibility=? AND subject_principal_id=?",
            ('yushu','羽书:group:398291136','group','羽书:user:1923563505'),
        ).fetchone()[0]
        t_after = time.time() - t1
        after_formal = cm.execute_read('SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships').fetchone()
        after_audit = cm.execute_read('SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events').fetchone()[0]
        return {
            'label': label,
            'indexes': [r[0] for r in idxs],
            'subject_count': n1,
            'query_seconds_before': round(t_before, 4),
            'query_seconds_after': round(t_after, 4),
            'formal_before': list(before_formal),
            'formal_after': list(after_formal),
            'audit_before': before_audit,
            'audit_after': after_audit,
            'affinity_unchanged': list(before_formal) == list(after_formal),
            'audit_unchanged': before_audit == after_audit,
        }
    else:
        # staged/vacuumed package: direct connection
        conn = sqlite3.connect(db_path.as_posix(), timeout=120)
        conn.execute('PRAGMA busy_timeout=120000')
        _ensure_audit_tables(conn)
        conn.commit()
        idxs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='scoped_soul_relationship_legacy_events' ORDER BY name"
        ).fetchall()
        t1 = time.time()
        n1 = conn.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events WHERE bot_id=? AND session_id=? AND visibility=? AND subject_principal_id=?",
            ('yushu','羽书:group:398291136','group','羽书:user:1923563505'),
        ).fetchone()[0]
        t_after = time.time() - t1
        after_formal = conn.execute('SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships').fetchone()
        after_audit = conn.execute('SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events').fetchone()[0]
        conn.close()
        return {
            'label': label,
            'indexes': [r[0] for r in idxs],
            'subject_count': n1,
            'query_seconds_before': round(t_before, 4),
            'query_seconds_after': round(t_after, 4),
            'formal_before': list(before_formal),
            'formal_after': list(after_formal),
            'audit_before': before_audit,
            'audit_after': after_audit,
            'affinity_unchanged': list(before_formal) == list(after_formal),
            'audit_unchanged': before_audit == after_audit,
        }

report = {
    'prod': index_and_time(prod, 'prod'),
    'vacuumed_package': index_and_time(vac, 'vac') if vac.exists() else {'exists': False},
}
print(json.dumps(report, ensure_ascii=False, indent=2))
