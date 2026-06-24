"""迁移脚本：将 source='live' 的记忆分类为 core/chat/noise

使用方式：在 AstrBot 容器内执行
    python -c "from data.plugins.astrbot_plugin_wave_memory.scripts.migrate_sources import migrate; migrate()"

或在宿主机：
    docker exec astrbot python3 -c "
    import sys; sys.path.insert(0, '/AstrBot')
    from data.plugins.astrbot_plugin_wave_memory.scripts.migrate_sources import migrate
    migrate('/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db')
    "
"""

import sqlite3
import time

try:
    from sqlite_runtime_guard import assert_astrbot_stopped
except ModuleNotFoundError:  # package import from repository root
    from scripts.sqlite_runtime_guard import assert_astrbot_stopped


def migrate(db_path: str = None):
    """将 live 记忆分类为 core/chat/noise。"""
    assert_astrbot_stopped("migrate memory sources")
    if db_path is None:
        import os
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..", "data", "plugin_data", "astrbot_plugin_wave_memory", "wave_memory.db"
        )

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    total = conn.execute("SELECT COUNT(*) FROM memories WHERE source = 'live'").fetchone()[0]
    if total == 0:
        print("No 'live' memories to migrate. Done.")
        conn.close()
        return

    print(f"Migrating {total} 'live' memories...")

    # 1. noise: < 10 字
    noise_count = conn.execute(
        "UPDATE memories SET source='noise' WHERE source='live' AND LENGTH(content) < 10"
    ).rowcount
    conn.commit()
    print(f"  noise (<10 chars): {noise_count}")

    # 2. core: bot 自己发的
    core_bot = conn.execute(
        "UPDATE memories SET source='core' WHERE source='live' AND sender_id='bot'"
    ).rowcount
    conn.commit()
    print(f"  core (bot replies): {core_bot}")

    # 3. 剩余 live → chat
    chat_count = conn.execute(
        "UPDATE memories SET source='chat' WHERE source='live'"
    ).rowcount
    conn.commit()
    print(f"  chat (remaining): {chat_count}")

    # 验证
    remaining = conn.execute("SELECT COUNT(*) FROM memories WHERE source = 'live'").fetchone()[0]
    print(f"\nMigration complete. Remaining 'live': {remaining}")

    # 统计
    rows = conn.execute("SELECT source, COUNT(*) FROM memories GROUP BY source ORDER BY COUNT(*) DESC").fetchall()
    print("\nFinal distribution:")
    for source, cnt in rows:
        print(f"  {source}: {cnt}")

    conn.close()
    print("\nDone. You may want to rebuild HNSW index (remove noise entries).")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    migrate(path)
