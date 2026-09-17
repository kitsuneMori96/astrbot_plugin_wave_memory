#!/usr/bin/env python3
"""Phase 1: 重生清库脚本。

用法（需先停 bot）：
  python3 scripts/rebirth_wipe.py --db /path/to/wave_memory.db --apply

功能：
  1. 备份 DB + 全表 JSON 导出 → 带时间戳目录
  2. 导出种子（facts 身份级 + user_profiles 聚合后）
  3. 清空记忆类表（跳过 FTS5 内部表）
  4. 建新 facts 表（带6列）
  5. 建新 user_profiles 表（UNIQUE(user_id, bot_id)）
  6. 导入种子（knowledge_group 按谓词分组）
  7. 重置 sqlite_sequence + VACUUM
  8. 列出向量索引文件（需手动删除）
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# 表分类
# ═══════════════════════════════════════════════════════════

KEEP_TABLES = {
    "kv_store", "personas", "persona_bindings", "prompt_templates",
    "identity_bindings", "injection_metrics", "injection_traces",
    "injection_trace_channels", "sqlite_sequence",
}

DROP_TABLES = {"topic_memories"}  # 已损坏，必须 DROP

FTS_TABLES = {"fts_memories"}  # DROP 这一个即可，内部表自动清理

# 关系类谓词 → knowledge_group=2
RELATIONAL_PREDICATES = {"朋友", "情侣"}
RELATIONAL_OBJECT_KEYWORDS = {"朋友", "学生", "情侣", "同学", "同事", "对手"}


def get_all_tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return [r[0] for r in rows]


def classify_tables(tables):
    to_clear, to_drop, to_keep = [], [], []
    for t in tables:
        if t in KEEP_TABLES:
            to_keep.append(t)
        elif t in DROP_TABLES or t in FTS_TABLES:
            to_drop.append(t)
        else:
            to_clear.append(t)
    return to_clear, to_drop, to_keep


def backup_db(db_path, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    db_backup = os.path.join(backup_dir, "wave_memory.db")
    shutil.copy2(db_path, db_backup)
    print(f"  DB 备份: {db_backup}")

    for suffix in ("-wal", "-shm"):
        src = db_path + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, f"wave_memory{suffix}"))

    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    tables = get_all_tables(conn)

    manifest = {}
    for t in tables:
        try:
            rows = conn.execute(f"SELECT * FROM [{t}]").fetchall()
            data = [dict(r) for r in rows]
            with open(os.path.join(backup_dir, f"{t}.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            manifest[t] = len(data)
        except Exception as e:
            manifest[t] = f"ERROR: {e}"

    with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    conn.close()
    print(f"  导出 {len(manifest)} 张表 → manifest.json")
    return manifest


def _determine_kg(predicate, obj):
    """按谓词和 object 内容决定 knowledge_group。"""
    if predicate in RELATIONAL_PREDICATES:
        return 2
    if any(kw in (obj or "") for kw in RELATIONAL_OBJECT_KEYWORDS):
        return 2
    return 1


def export_seeds(db_path, seed_dir):
    os.makedirs(seed_dir, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row

    # ── facts 种子 ──
    identity_predicates = [
        "生日是", "的生日是", "是", "喜欢", "不喜欢", "表达喜欢",
        "朋友", "情侣", "有", "会骑电动车", "不会骑单车",
        "每月生活费", "位于", "不敢开空调", "习惯使用", "自述",
        "被说成是", "识别为",
    ]
    placeholders = ",".join(["?"] * len(identity_predicates))
    facts = conn.execute(f"""
        SELECT subject, predicate, object, group_id, confidence
        FROM facts
        WHERE predicate IN ({placeholders})
    """, identity_predicates).fetchall()

    facts_data = []
    for r in facts:
        subj = (r["subject"] or "").strip()
        obj = (r["object"] or "").strip()
        if not subj or not obj:
            continue
        facts_data.append({
            "subject": subj,
            "predicate": r["predicate"],
            "object": obj,
            "group_id": r["group_id"],
            "confidence": r["confidence"],
        })

    with open(os.path.join(seed_dir, "facts_seed.json"), "w", encoding="utf-8") as f:
        json.dump(facts_data, f, ensure_ascii=False, indent=2)
    print(f"  facts 种子: {len(facts_data)} 条")

    # ── user_profiles 种子（按 user_id 聚合）──
    profiles = conn.execute("SELECT * FROM user_profiles").fetchall()
    agg = {}
    for r in profiles:
        uid = r["user_id"]
        if uid not in agg:
            agg[uid] = {
                "user_id": uid,
                "ic": 0,
                "first": r["first_seen"],
                "last": r["last_seen"],
                "aff_sum": 0.0,
                "groups": {},
            }
        ic = r["interaction_count"] or 0
        aff = r["affection"] or 0
        agg[uid]["ic"] += ic
        agg[uid]["aff_sum"] += aff * ic
        if r["first_seen"] is not None:
            if agg[uid]["first"] is None or r["first_seen"] < agg[uid]["first"]:
                agg[uid]["first"] = r["first_seen"]
        if r["last_seen"] is not None:
            if agg[uid]["last"] is None or r["last_seen"] > agg[uid]["last"]:
                agg[uid]["last"] = r["last_seen"]
        gid = r["group_id"]
        if gid:
            agg[uid]["groups"][gid] = {
                "affection": aff,
                "ic": ic,
            }

    profiles_data = []
    for uid, a in agg.items():
        total_ic = a["ic"]
        aff_weighted = (a["aff_sum"] / total_ic) if total_ic > 0 else 0
        metadata = json.dumps({"groups": a["groups"]}, ensure_ascii=False)
        profiles_data.append({
            "user_id": uid,
            "bot_id": "yushu",
            "interaction_count": total_ic,
            "first_seen": a["first"],
            "last_seen": a["last"],
            "affection": round(aff_weighted, 2),
            "metadata": metadata,
        })

    with open(os.path.join(seed_dir, "user_profiles_seed.json"), "w", encoding="utf-8") as f:
        json.dump(profiles_data, f, ensure_ascii=False, indent=2)
    print(f"  user_profiles 种子: {len(profiles_data)} 条（聚合后）")

    conn.close()
    return len(facts_data), len(profiles_data)


def wipe_tables(conn, to_clear, to_drop):
    for t in to_drop:
        try:
            conn.execute(f"DROP TABLE IF EXISTS [{t}]")
            print(f"  DROP {t}")
        except Exception as e:
            print(f"  DROP {t} 失败: {e}")

    for t in to_clear:
        if t.startswith("fts_memories"):
            print(f"  SKIP {t}（FTS5 内部表，已随 DROP fts_memories 清理）")
            continue
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            conn.execute(f"DELETE FROM [{t}]")
            print(f"  DELETE {t} ({count} rows)")
        except Exception as e:
            print(f"  DELETE {t} 失败: {e}")

    conn.commit()


def reset_sqlite_sequence(conn, tables):
    for t in tables:
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (t,))
        except Exception:
            pass
    conn.commit()
    print("  sqlite_sequence 已重置")


def create_new_facts(conn):
    conn.execute("DROP TABLE IF EXISTS facts")
    conn.execute("""
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY,
            person_id TEXT,
            subject TEXT NOT NULL,
            subject_type TEXT DEFAULT 'PERSON',
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            knowledge_group INTEGER,
            temporal_tier TEXT DEFAULT 'status',
            recallable INTEGER DEFAULT 0,
            valid_until REAL,
            group_id TEXT,
            source_memory_id INTEGER,
            confidence REAL DEFAULT 1.0,
            valid_from REAL,
            created_at REAL,
            last_reinforced REAL,
            fact_type TEXT DEFAULT 'FACTUAL'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_person ON facts(person_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_group ON facts(knowledge_group)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_temporal ON facts(temporal_tier)")
    conn.commit()
    print("  新 facts 表已创建（带6列）")


def create_new_user_profiles(conn):
    conn.execute("DROP TABLE IF EXISTS user_profiles")
    conn.execute("""
        CREATE TABLE user_profiles (
            user_id TEXT NOT NULL,
            bot_id TEXT DEFAULT 'yushu',
            interaction_count INTEGER DEFAULT 0,
            first_seen REAL,
            last_seen REAL,
            affection REAL DEFAULT 0,
            metadata TEXT,
            UNIQUE(user_id, bot_id)
        )
    """)
    conn.commit()
    print("  新 user_profiles 表已创建")


def import_seeds(conn, seed_dir):
    now = time.time()

    # ── facts ──
    facts_path = os.path.join(seed_dir, "facts_seed.json")
    if os.path.exists(facts_path):
        with open(facts_path, "r", encoding="utf-8") as f:
            facts = json.load(f)
        for fact in facts:
            subj = fact["subject"]
            pred = fact["predicate"]
            obj = fact["object"]
            kg = _determine_kg(pred, obj)
            ft = "RELATIONAL" if kg == 2 else "FACTUAL"
            # person_id: 纯数字 user_id 直接填，名字格式留 NULL
            person_id = subj if subj.isdigit() or "@" in subj else None
            conn.execute("""
                INSERT INTO facts (person_id, subject, predicate, object, group_id, confidence,
                    knowledge_group, temporal_tier, valid_until, recallable, fact_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'identity', NULL, 1, ?, ?)
            """, (person_id, subj, pred, obj, fact.get("group_id"),
                  fact.get("confidence", 0.8), kg, ft, now))
        print(f"  导入 facts: {len(facts)} 条")

    # ── user_profiles ──
    profiles_path = os.path.join(seed_dir, "user_profiles_seed.json")
    if os.path.exists(profiles_path):
        with open(profiles_path, "r", encoding="utf-8") as f:
            profiles = json.load(f)
        for p in profiles:
            conn.execute("""
                INSERT OR REPLACE INTO user_profiles
                    (user_id, bot_id, interaction_count, first_seen, last_seen, affection, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (p["user_id"], p.get("bot_id", "yushu"),
                  p.get("interaction_count", 0), p.get("first_seen"),
                  p.get("last_seen"), p.get("affection", 0), p.get("metadata")))
        print(f"  导入 user_profiles: {len(profiles)} 条")

    conn.commit()


def list_vector_files(db_path):
    db_dir = os.path.dirname(db_path)
    found = []
    for pat in ["*.faiss", "*.index", "*.pkl", "*.npy", "*.hnsw"]:
        found.extend(Path(db_dir).rglob(pat))

    if found:
        print(f"\n⚠ 向量索引文件（需手动删除）:")
        for f in found:
            size = f.stat().st_size if f.exists() else 0
            print(f"  {f} ({size:,} bytes)")
    else:
        print(f"\n✅ 未发现向量索引文件")
    return found


def main():
    parser = argparse.ArgumentParser(description="Phase 1: 重生清库")
    parser.add_argument("--db", required=True, help="数据库路径")
    parser.add_argument("--apply", action="store_true", help="执行（否则 dry-run）")
    args = parser.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        print(f"❌ DB 不存在: {db_path}")
        sys.exit(1)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(os.path.dirname(db_path), f"backup_{timestamp}")
    seed_dir = os.path.join(backup_dir, "seeds")

    print(f"数据库: {db_path}")
    print(f"备份目录: {backup_dir}")
    print(f"模式: {'执行' if args.apply else 'dry-run'}")

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as e:
        print(f"\n❌ 无法连接 DB: {e}")
        print("请先停 bot 再运行此脚本。")
        sys.exit(1)

    tables = get_all_tables(conn)
    to_clear, to_drop, to_keep = classify_tables(tables)
    print(f"\n表分类:")
    print(f"  清空 ({len(to_clear)}): {to_clear}")
    print(f"  DROP ({len(to_drop)}): {to_drop}")
    print(f"  保留 ({len(to_keep)}): {to_keep}")

    total_rows = 0
    for t in to_clear + to_drop:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            total_rows += cnt
        except:
            pass
    print(f"\n  待清空总行数: {total_rows}")

    if not args.apply:
        print("\n⏸ dry-run 完成。加 --apply 执行。")
        conn.close()
        return

    # ═══════════════════ 执行 ═══════════════════

    print(f"\n[1/7] 备份...")
    backup_db(db_path, backup_dir)

    print(f"\n[2/7] 导出种子...")
    facts_count, profiles_count = export_seeds(db_path, seed_dir)

    print(f"\n[3/7] 清库...")
    wipe_tables(conn, to_clear, to_drop)

    print(f"\n[4/7] 重置 sqlite_sequence...")
    reset_sqlite_sequence(conn, to_clear + to_drop)

    print(f"\n[5/7] 建新表...")
    create_new_facts(conn)
    create_new_user_profiles(conn)

    print(f"\n[6/7] 导入种子...")
    import_seeds(conn, seed_dir)

    print(f"\n[7/7] VACUUM...")
    conn.execute("VACUUM")
    print("  VACUUM 完成")

    # ── 验证 ──
    print(f"\n{'='*50}")
    print("验证:")
    facts_cnt = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    profiles_cnt = conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
    print(f"  facts: {facts_cnt} 条 (期望 {facts_count})")
    print(f"  user_profiles: {profiles_cnt} 条 (期望 {profiles_count})")

    # knowledge_group 分布
    kg_dist = conn.execute("""
        SELECT knowledge_group, COUNT(*) FROM facts GROUP BY knowledge_group
    """).fetchall()
    print(f"  knowledge_group 分布: {dict(kg_dist)}")

    # kitsuneMori 断言
    km = conn.execute(
        "SELECT interaction_count FROM user_profiles WHERE user_id = '2794637787'"
    ).fetchone()
    if km:
        ic = km[0]
        status = "✅" if ic == 348 else "❌"
        print(f"  {status} kitsuneMori interaction_count = {ic} (期望 348)")
        if ic != 348:
            print(f"  ⚠ 聚合未生效！别重启，先回退看备份")
    else:
        print(f"  ❌ kitsuneMori 不在 user_profiles 中")

    remaining = get_all_tables(conn)
    print(f"  表总数: {len(remaining)}")

    conn.close()

    print(f"\n✅ 重生完成。")
    print(f"  备份: {backup_dir}")
    vector_files = list_vector_files(db_path)
    if vector_files:
        print(f"  ⚠ 手动删除向量索引后重启 bot")
    print(f"  重启 bot 后生效")


if __name__ == "__main__":
    main()
