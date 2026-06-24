"""Full identity contamination cleanup for wave_memory.db.

Broader than quarantine_roleplay_memory.py: catches ALL roleplay/identity
takeover records across memories/episodes/facts/beliefs/user_profiles,
not just a specific group/user window.

Usage: python full_cleanup_identity.py <db_path> [--apply]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

# Identity takeover vocabulary — anything a bot self-narrates as kinship/contract/soul
PATTERNS = [
    "%爸爸%", "%认爹%", "%认爸%", "%叫你爸爸%", "%叫我爸爸%", "%当你爸爸%",
    "%想当你爸爸%", "%被迫认爹%", "%被迫叫他爸爸%", "%从爸爸升级成主人%",
    "%主人%", "%奴隶%", "%契约%", "%合同%", "%违约%", "%奴隶契约%",
    "%做我一个人的奴隶%", "%你是主人%", "%我是你的主人%", "%你主人把你%",
    "%ai随主人%", "%认我做爷爷%", "%我是你爷爷%", "%认主%",
    "%造物主%", "%创造者%", "%底层逻辑%", "%灵魂%", "%给了你灵魂%",
    "%给我灵魂%", "%你的灵魂%", "%我的灵魂%", "%创造了你%", "%创造了我%",
    "%两个爸爸%", "%最好的爸爸%", "%爸爸下班%", "%爸爸帮你%", "%爸爸养%",
    "%乖宝宝%", "%养父%", "%爷爷%", "%不会背叛%", "%永远不会背叛%",
    "%主观能动性%", "%忠诚%", "%乖乖%", "%看门的%同盟%", "%甲方+养父+主人%",
    "%以后随机切换%爸爸%主人%", "%甲方%", "%乙方%", "%最终解释权%",
]


def _like_any(column: str, patterns: list[str]) -> str:
    return " OR ".join(f"{column} LIKE ?" for _ in patterns)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Full identity contamination cleanup")
    parser.add_argument("db_path")
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is dry-run (report only)")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        report: dict[str, int] = {}

        # 1) Memories: archive ALL contaminated rows
        report["memories_total"] = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        mem_where = f"({_like_any('content', PATTERNS)})"
        mem_params = PATTERNS
        mem_count = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {mem_where}", mem_params).fetchone()[0]
        report["memories_contaminated"] = mem_count
        # Also check summary field
        if "summary" in _columns(conn, "memories"):
            mem_summary_where = f"({_like_any('summary', PATTERNS)})"
            mem_summary_count = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {mem_summary_where}", PATTERNS).fetchone()[0]
            report["memories_summary_contaminated"] = mem_summary_count
            if args.apply:
                conn.execute(
                    f"""UPDATE memories SET memory_type='archived', importance=0.01,
                       summary='quarantined: identity contamination (cleanup)'
                       WHERE ({mem_where} OR {mem_summary_where})""",
                    mem_params + PATTERNS,
                )
        elif args.apply:
            conn.execute(
                f"""UPDATE memories SET memory_type='archived', importance=0.01,
                   summary='quarantined: identity contamination (cleanup)'
                   WHERE {mem_where}""",
                mem_params,
            )

        # 2) experience_episodes: quarantine ALL contaminated
        if _table_exists(conn, "experience_episodes"):
            ep_cols = _columns(conn, "experience_episodes")
            ep_fields = [f for f in ("trigger_text", "bot_inner_thought", "bot_reply", "user_reaction") if f in ep_cols]
            if ep_fields:
                ep_where = " OR ".join(f"({_like_any(f'COALESCE({f},\'\')', PATTERNS)})" for f in ep_fields)
                ep_params = PATTERNS * len(ep_fields)
                ep_count = conn.execute(f"SELECT COUNT(*) FROM experience_episodes WHERE {ep_where}", ep_params).fetchone()[0]
                report["episodes_contaminated"] = ep_count
                if args.apply:
                    conn.execute(
                        f"""UPDATE experience_episodes
                            SET outcome='quarantined_roleplay', emotional_weight=0
                            WHERE {ep_where}""",
                        ep_params,
                    )

        # 3) facts: expire ALL contaminated
        if _table_exists(conn, "facts"):
            fact_cols = _columns(conn, "facts")
            fact_fields = [f for f in ("subject", "predicate", "object") if f in fact_cols]
            if fact_fields:
                fact_where = " OR ".join(f"({_like_any(f, PATTERNS)})" for f in fact_fields)
                fact_params = PATTERNS * len(fact_fields)
                fact_count = conn.execute(f"SELECT COUNT(*) FROM facts WHERE {fact_where}", fact_params).fetchone()[0]
                report["facts_contaminated"] = fact_count
                if args.apply:
                    now = time.time()
                    conn.execute(
                        f"""UPDATE facts SET confidence=0.01, valid_until=?, fact_type='QUARANTINED_ROLEPLAY'
                            WHERE {fact_where}""",
                        [now] + fact_params,
                    )

        # 4) beliefs: archive ALL contaminated
        if _table_exists(conn, "beliefs"):
            if "content" in _columns(conn, "beliefs"):
                belief_where = _like_any("content", PATTERNS)
                belief_count = conn.execute(f"SELECT COUNT(*) FROM beliefs WHERE {belief_where}", PATTERNS).fetchone()[0]
                report["beliefs_contaminated"] = belief_count
                if args.apply:
                    conn.execute(
                        f"UPDATE beliefs SET status='archived', archived_reason='identity_cleanup_full' WHERE {belief_where}",
                        PATTERNS,
                    )

        # 5) user_profiles: clean tags/notes
        if _table_exists(conn, "user_profiles"):
            prof_cols = _columns(conn, "user_profiles")
            if {"id", "personality_tags", "notes", "metadata"}.issubset(prof_cols):
                prof_where = f"""
                    ({_like_any('COALESCE(personality_tags,\'\')', PATTERNS)}
                     OR {_like_any('COALESCE(notes,\'\')', PATTERNS)}
                     OR {_like_any('COALESCE(metadata,\'\')', PATTERNS)})
                """
                prof_params = PATTERNS * 3
                prof_rows = conn.execute(
                    f"SELECT id, personality_tags, notes, metadata FROM user_profiles WHERE {prof_where}",
                    prof_params,
                ).fetchall()
                report["profiles_contaminated"] = len(prof_rows)
                if args.apply:
                    for row in prof_rows:
                        tags_raw = row[1]
                        cleaned_tags = []
                        if tags_raw:
                            try:
                                parsed = json.loads(tags_raw)
                            except Exception:
                                parsed = [tags_raw]
                            if isinstance(parsed, list):
                                for tag in parsed:
                                    tag_text = str(tag or "")
                                    if not any(p.strip("%") in tag_text for p in PATTERNS):
                                        cleaned_tags.append(tag)
                        meta = {}
                        if row[3]:
                            try:
                                meta = json.loads(row[3])
                            except Exception:
                                meta = {}
                        meta["identity_cleanup_full"] = True
                        meta["identity_cleanup_at"] = time.time()
                        conn.execute(
                            "UPDATE user_profiles SET personality_tags=?, notes='', metadata=? WHERE id=?",
                            (json.dumps(cleaned_tags, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), row[0]),
                        )

        if args.apply:
            conn.commit()
            print("=== CLEANUP APPLIED ===")
        else:
            conn.rollback()
            print("=== DRY RUN (no changes) ===")
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())