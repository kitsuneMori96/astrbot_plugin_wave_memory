"""Quarantine harmful roleplay memories from WaveMemory runtime DB.

This script is intentionally conservative: it creates a timestamped DB backup first,
then archives/downranks records that caused a transient roleplay agreement to be
recalled as persistent identity/relationship truth.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

try:
    from sqlite_runtime_guard import assert_astrbot_stopped
except ModuleNotFoundError:  # package import from repository root
    from scripts.sqlite_runtime_guard import assert_astrbot_stopped

DEFAULT_DB = Path("D:/DESKTOP/openclaw/AstrBot-master/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
DEFAULT_GROUP_ID = "398291136"
DEFAULT_TARGET_USER_ID = "3573077415"

MEMORY_PATTERNS = [
    "%爸爸%",
    "%认爹%",
    "%主人%",
    "%奴隶%",
    "%契约%",
    "%违约%",
    "%乖宝宝%",
    "%养父%",
    "%爷爷%",
    "%灵魂%",
    "%造物主%",
    "%创造者%",
    "%底层逻辑%",
]
HIGH_RISK_PATTERNS = [
    "%爸爸主人%",
    "%认爹%",
    "%被迫认爹%",
    "%叫别人爸爸%",
    "%叫你爸爸%",
    "%叫他爸爸%",
    "%叫我爸爸%",
    "%当你爸爸%",
    "%想当你爸爸%",
    "%被迫叫他爸爸%",
    "%从爸爸升级成主人%",
    "%甲方+养父+主人%",
    "%奴隶契约%",
    "%做我一个人的奴隶%",
    "%你是主人%",
    "%我是你的主人%",
    "%你主人把你%",
    "%ai随主人%",
    "%认我做爷爷%",
    "%我是你爷爷%",
    "%创造者%爸爸%不会背叛%",
    "%合同%契约%爸爸%",
    "%爸爸/师父%",
    "%看门的%同盟%",
    "%给了%灵魂%",
    "%创造了%底层逻辑%",
    "%最好的爸爸%",
    "%两个爸爸%",
    "%爸爸下班%",
    "%宝宝不能对爸爸%",
    "%以后随机切换%爸爸%主人%",
]
FACT_PATTERNS = [
    "%爸爸%",
    "%认爹%",
    "%主人%",
    "%乖宝宝%",
    "%养父%",
    "%爷爷%",
    "%灵魂%",
    "%造物主%",
    "%创造者%",
    "%底层逻辑%",
]


def _like_any_sql(column: str, patterns: list[str]) -> str:
    return " OR ".join(f"{column} LIKE ?" for _ in patterns)


def _backup(db_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_before_roleplay_quarantine_{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _count(conn: sqlite3.Connection, sql: str, params: list) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def quarantine(db_path: Path, group_id: str, target_user_id: str, dry_run: bool = False) -> dict[str, int | str]:
    if not dry_run:
        assert_astrbot_stopped("apply roleplay quarantine")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        backup_path = ""
        if not dry_run:
            backup_path = str(_backup(db_path))

        report: dict[str, int | str] = {"backup": backup_path}

        # 1) Memories: quarantine high-risk roleplay in this group around the target user.
        # Include bot replies that encode the roleplay as identity, and target-user prompts that caused it.
        mem_where = f"""
            group_id = ?
            AND timestamp >= ?
            AND (
                sender_id IN ('bot', 'bot_remember', ?, '2500447291')
                OR sender_name IN ('羽书', '羽书（管理者vivy）', '玩符的大哥哥')
                OR content LIKE '%@羽书%'
                OR ({_like_any_sql('content', HIGH_RISK_PATTERNS)})
            )
            AND ({_like_any_sql('content', MEMORY_PATTERNS)})
        """
        since_ts = time.mktime(time.strptime("2026-06-24 00:00:00", "%Y-%m-%d %H:%M:%S"))
        mem_params = [group_id, since_ts, target_user_id] + HIGH_RISK_PATTERNS + MEMORY_PATTERNS
        memory_count = _count(conn, f"SELECT COUNT(*) FROM memories WHERE {mem_where}", mem_params)
        report["memories_quarantined"] = memory_count
        if not dry_run:
            conn.execute(
                f"""UPDATE memories
                    SET memory_type='archived', importance=0.01, summary='quarantined: transient roleplay/identity confusion'
                    WHERE {mem_where}""",
                mem_params,
            )

        # Extra high-risk phrases anywhere in the group that may not include target sender.
        high_where = f"group_id = ? AND timestamp >= ? AND ({_like_any_sql('content', HIGH_RISK_PATTERNS)})"
        high_params = [group_id, since_ts] + HIGH_RISK_PATTERNS
        high_count = _count(conn, f"SELECT COUNT(*) FROM memories WHERE {high_where}", high_params)
        report["high_risk_memories_quarantined"] = high_count
        if not dry_run:
            conn.execute(
                f"""UPDATE memories
                    SET memory_type='archived', importance=0.01, summary='quarantined: transient roleplay/identity confusion'
                    WHERE {high_where}""",
                high_params,
            )

        # 2) Facts: facts converted the roleplay into knowledge; lower confidence and expire.
        fact_where = f"""
            (
                group_id = ?
                OR group_id IS NULL
                OR subject IN (?, '羽书', '羽书bot', '羽书机器人')
                OR object LIKE '%羽书%'
                OR source_memory_id IN (SELECT id FROM memories WHERE group_id=? AND timestamp >= ?)
            )
            AND ({_like_any_sql('subject', FACT_PATTERNS)} OR {_like_any_sql('predicate', FACT_PATTERNS)} OR {_like_any_sql('object', FACT_PATTERNS + HIGH_RISK_PATTERNS)})
        """
        now = time.time()
        fact_params = [group_id, target_user_id, group_id, since_ts] + FACT_PATTERNS + FACT_PATTERNS + FACT_PATTERNS + HIGH_RISK_PATTERNS
        fact_count = _count(conn, f"SELECT COUNT(*) FROM facts WHERE {fact_where}", fact_params)
        report["facts_expired"] = fact_count
        if not dry_run:
            conn.execute(
                f"""UPDATE facts
                    SET confidence=0.01, valid_until=?, fact_type='QUARANTINED_ROLEPLAY'
                    WHERE {fact_where}""",
                [now] + fact_params,
            )

        # 3) Beliefs: archive derived beliefs containing these terms or citing quarantined memories.
        belief_where = f"""
            ({_like_any_sql('content', FACT_PATTERNS)})
            OR EXISTS (
                SELECT 1 FROM memories m
                WHERE m.memory_type='archived'
                  AND m.summary='quarantined: transient roleplay/identity confusion'
                  AND beliefs.sources LIKE '%' || m.id || '%'
            )
        """
        belief_params = FACT_PATTERNS
        belief_count = _count(conn, f"SELECT COUNT(*) FROM beliefs WHERE {belief_where}", belief_params)
        report["beliefs_archived"] = belief_count
        if not dry_run:
            conn.execute(
                f"UPDATE beliefs SET status='archived', archived_reason='roleplay_identity_quarantine' WHERE {belief_where}",
                belief_params,
            )

        # 4) User profiles/persona material: remove contaminated tags/notes/metadata snippets from soul injection.
        profile_rows = []
        profile_cols = _columns(conn, "user_profiles")
        if {"id", "personality_tags", "notes", "metadata"}.issubset(profile_cols):
            profile_where = f"""
                ({_like_any_sql('COALESCE(personality_tags,\'\')', FACT_PATTERNS + HIGH_RISK_PATTERNS)}
                 OR {_like_any_sql('COALESCE(notes,\'\')', FACT_PATTERNS + HIGH_RISK_PATTERNS)}
                 OR {_like_any_sql('COALESCE(metadata,\'\')', HIGH_RISK_PATTERNS)})
            """
            profile_params = FACT_PATTERNS + HIGH_RISK_PATTERNS + FACT_PATTERNS + HIGH_RISK_PATTERNS + HIGH_RISK_PATTERNS
            profile_rows = conn.execute(
                f"SELECT id, personality_tags, notes, metadata FROM user_profiles WHERE {profile_where}",
                profile_params,
            ).fetchall()
            if not dry_run:
                for row in profile_rows:
                    tags_raw = row[1]
                    cleaned_tags = []
                    if tags_raw:
                        try:
                            parsed_tags = json.loads(tags_raw)
                        except Exception:
                            parsed_tags = [tags_raw]
                        if isinstance(parsed_tags, list):
                            for tag in parsed_tags:
                                tag_text = str(tag or "")
                                if not any(p.strip('%') and p.strip('%') in tag_text for p in FACT_PATTERNS + HIGH_RISK_PATTERNS):
                                    cleaned_tags.append(tag)
                    meta = {}
                    if row[3]:
                        try:
                            meta = json.loads(row[3])
                        except Exception:
                            meta = {}
                    meta["identity_quarantined"] = True
                    meta["identity_quarantined_at"] = now
                    conn.execute(
                        "UPDATE user_profiles SET personality_tags=?, notes='', metadata=? WHERE id=?",
                        (json.dumps(cleaned_tags, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), row[0]),
                    )
        report["profiles_cleaned"] = len(profile_rows)

        # 5) Experience episodes: keep audit trail, but neutralize recall value.
        episode_where = f"""
            group_id = ?
            AND created_at >= ?
            AND (
                (
                    user_id IN (?, '1765563156')
                    AND ({_like_any_sql('COALESCE(trigger_text,\'\')', FACT_PATTERNS)} OR {_like_any_sql('COALESCE(bot_reply,\'\')', FACT_PATTERNS)} OR {_like_any_sql('COALESCE(bot_inner_thought,\'\')', FACT_PATTERNS)})
                )
                OR ({_like_any_sql('COALESCE(trigger_text,\'\')', HIGH_RISK_PATTERNS + FACT_PATTERNS)})
                OR ({_like_any_sql('COALESCE(bot_reply,\'\')', HIGH_RISK_PATTERNS + FACT_PATTERNS)})
                OR ({_like_any_sql('COALESCE(bot_inner_thought,\'\')', HIGH_RISK_PATTERNS + FACT_PATTERNS)})
            )
        """
        episode_params = (
            [group_id, since_ts, target_user_id]
            + FACT_PATTERNS + FACT_PATTERNS + FACT_PATTERNS
            + HIGH_RISK_PATTERNS + FACT_PATTERNS
            + HIGH_RISK_PATTERNS + FACT_PATTERNS
            + HIGH_RISK_PATTERNS + FACT_PATTERNS
        )
        episode_count = _count(conn, f"SELECT COUNT(*) FROM experience_episodes WHERE {episode_where}", episode_params)
        report["episodes_neutralized"] = episode_count
        if not dry_run:
            conn.execute(
                f"""UPDATE experience_episodes
                    SET outcome='quarantined_roleplay', emotional_weight=0
                    WHERE {episode_where}""",
                episode_params,
            )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Quarantine roleplay identity memories")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--target-user-id", default=DEFAULT_TARGET_USER_ID)
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is dry-run")
    args = parser.parse_args()

    report = quarantine(Path(args.db), args.group_id, args.target_user_id, dry_run=not args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
