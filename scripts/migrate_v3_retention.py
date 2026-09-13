#!/usr/bin/env python3
"""Phase 1 迁移脚本：retention 架构 + fact_sources + recall_log + threads。

用法：
  PYTHONPATH=/path/to/plugin /path/to/python scripts/migrate_v3_retention.py

幂等：可安全重跑（DDL 用 IF NOT EXISTS，UPDATE 只影响目标行）。
"""

import os
import sqlite3
import struct
import sys
import time

import numpy as np

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════
DB_PATH = os.environ.get(
    "WAVE_MEMORY_DB",
    "C:/Users/Administrator/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
)


# ═══════════════════════════════════════════════════════════════
# INT8 量化 / 反量化（从 vector_lifecycle.py 复制，避免导入依赖）
# ═══════════════════════════════════════════════════════════════

def quantize_int8(vec):
    import numpy as np
    vec = np.asarray(vec, dtype=np.float32).ravel()
    vmin, vmax = float(vec.min()), float(vec.max())
    scale = (vmax - vmin) / 255.0 if vmax > vmin else 1.0
    quantized = ((vec - vmin) / scale).clip(0, 255).astype(np.uint8)
    header = struct.pack("ff", vmin, vmax)
    return header + quantized.tobytes()


def dequantize_int8(blob):
    import numpy as np
    vmin, vmax = struct.unpack("ff", blob[:8])
    data = np.frombuffer(blob[8:], dtype=np.uint8).astype(np.float32)
    if vmax > vmin:
        data = data / 255.0 * (vmax - vmin) + vmin
    else:
        data[:] = vmin
    return data


def is_quantized(blob):
    if not blob or len(blob) < 9:
        return False
    try:
        vmin, vmax = struct.unpack("ff", blob[:8])
        return abs(vmin) < 10 and abs(vmax) < 10 and vmin <= vmax
    except struct.error:
        return False


def decode_vector(blob):
    import numpy as np
    if blob is None:
        return None
    if is_quantized(blob):
        return dequantize_int8(blob)
    if len(blob) % 4 == 0:
        return np.frombuffer(blob, dtype=np.float32)
    return None


# ═══════════════════════════════════════════════════════════════
# Step 1: DDL
# ═══════════════════════════════════════════════════════════════

def step1_ddl(conn):
    print("[Step 1] DDL: 加字段 + 建表...")
    cur = conn.cursor()

    # memories 扩展（幂等：ALTER TABLE ADD COLUMN 在 SQLite 中如果列已存在会报错，用 try 忽略）
    memories_columns = [
        ("source_msg_id", "TEXT DEFAULT ''"),
        ("msg_score", "REAL DEFAULT 0.5"),
        ("stability_mult", "REAL DEFAULT 1.0"),
        ("last_recall_at", "REAL"),
        ("decay_class", "TEXT DEFAULT 'STATE'"),
        ("retention_state", "INTEGER DEFAULT 4"),
    ]
    existing = {row[1] for row in cur.execute("PRAGMA table_info(memories)").fetchall()}
    for col, typedef in memories_columns:
        if col not in existing:
            cur.execute(f"ALTER TABLE memories ADD COLUMN {col} {typedef}")
            print(f"  + memories.{col}")
        else:
            print(f"  = memories.{col} (already exists)")

    # fact_sources
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_sources (
            fact_id INTEGER NOT NULL,
            memory_id INTEGER NOT NULL,
            PRIMARY KEY (fact_id, memory_id),
            FOREIGN KEY (fact_id) REFERENCES facts(id) ON DELETE CASCADE,
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fact_sources_mem ON fact_sources(memory_id)")
    print("  + fact_sources")

    # recall_log
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recall_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            r_at_recall REAL NOT NULL,
            g REAL,
            content_len INTEGER,
            ts REAL NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_recall_log_query ON recall_log(query_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_recall_log_target ON recall_log(target_type, target_id)")
    print("  + recall_log")

    # threads
    cur.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            summary TEXT,
            members TEXT DEFAULT '[]',
            status TEXT DEFAULT 'ACTIVE',
            last_active REAL,
            closed_at REAL,
            created_at REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_threads_group ON threads(group_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status)")
    print("  + threads")

    conn.commit()
    print("[Step 1] Done.")


# ═══════════════════════════════════════════════════════════════
# Step 2: retention_state 映射
# ═══════════════════════════════════════════════════════════════

def step2_retention_state(conn):
    print("[Step 2] 映射 retention_state <- memory_type...")
    cur = conn.cursor()
    cur.execute("""
        UPDATE memories SET retention_state = CASE
            WHEN memory_type = 'message'  THEN 4
            WHEN memory_type = 'archived' THEN 2
            ELSE 0
        END
    """)
    print(f"  Updated {cur.rowcount} rows.")
    conn.commit()
    print("[Step 2] Done.")


# ═══════════════════════════════════════════════════════════════
# Step 3: decay_class 映射
# ═══════════════════════════════════════════════════════════════

def step3_decay_class(conn):
    print("[Step 3] 映射 decay_class <- importance (message only)...")
    cur = conn.cursor()
    cur.execute("""
        UPDATE memories SET decay_class = CASE
            WHEN importance >= 2.0 THEN 'DURATIVE'
            WHEN importance >= 1.0 THEN 'EVENT'
            WHEN importance >= 0.3 THEN 'STATE'
            ELSE 'NONE'
        END WHERE memory_type = 'message'
    """)
    print(f"  Updated {cur.rowcount} rows.")
    conn.commit()
    print("[Step 3] Done.")


# ═══════════════════════════════════════════════════════════════
# Step 4: evicted 清理 + VACUUM
# ═══════════════════════════════════════════════════════════════

def step4_evicted_cleanup(conn):
    print("[Step 4] 清理 evicted 内容...")
    cur = conn.cursor()
    cur.execute("UPDATE memories SET content = '', vector = NULL WHERE retention_state = 0")
    print(f"  Cleared {cur.rowcount} evicted rows.")
    conn.commit()

    print("[Step 4] VACUUM...")
    conn.isolation_level = None
    conn.execute("VACUUM")
    conn.isolation_level = ""
    print("[Step 4] Done.")


# ═══════════════════════════════════════════════════════════════
# Step 5: tags float32 -> int8 + HNSW 重建 + VACUUM
# ═══════════════════════════════════════════════════════════════

def _infer_dim_and_format(blob):
    """从 blob 推导维度和格式。"""
    n = len(blob)
    if n % 4 == 0 and n >= 4096:
        return n // 4, "fp32"
    return n - 8, "int8"


def step5_tags_int8(conn):
    print("[Step 5] Tags int8 量化...")
    cur = conn.cursor()

    sample = cur.execute("SELECT vector FROM tags WHERE vector IS NOT NULL LIMIT 1").fetchone()
    if not sample or sample[0] is None:
        print("  No tags with vectors, skipping.")
        return

    dim, fmt = _infer_dim_and_format(sample[0])
    print(f"  Detected: dim={dim}, format={fmt}")

    if fmt == "int8":
        print("  Tags already quantized, skipping.")
        return

    rows = cur.execute("SELECT id, vector FROM tags WHERE vector IS NOT NULL").fetchall()
    print(f"  Quantizing {len(rows)} tags...")
    for tag_id, blob in rows:
        vec = np.frombuffer(blob, dtype=np.float32)
        new_blob = quantize_int8(vec)
        cur.execute("UPDATE tags SET vector = ? WHERE id = ?", (new_blob, tag_id))
    conn.commit()
    print(f"  Quantized {len(rows)} tags.")

    # 重建 HNSW
    print("  Rebuilding tags HNSW index...")
    try:
        from engine.vector_index import VectorIndex

        tag_rows = cur.execute("SELECT id, vector FROM tags WHERE vector IS NOT NULL").fetchall()
        if tag_rows:
            ids = []
            vecs = []
            for tid, blob in tag_rows:
                decoded = decode_vector(blob)
                if decoded is not None and len(decoded) == dim:
                    ids.append(tid)
                    vecs.append(decoded)
            if ids:
                vecs_np = np.array(vecs, dtype=np.float32)
                hnsw_path = os.path.join(os.path.dirname(DB_PATH), "tags.hnsw")
                tag_index = VectorIndex(dimension=dim, index_path=None)
                tag_index.index_path = hnsw_path
                tag_index.add(ids, vecs_np)
                tag_index.save()
                print(f"  Rebuilt tags HNSW with {len(ids)} vectors.")
            else:
                print("  No valid vectors to index.")
        else:
            print("  No tag vectors found.")
    except ImportError:
        print("  WARNING: hnswlib not available, skipping HNSW rebuild.")
    except Exception as e:
        print(f"  WARNING: HNSW rebuild failed: {e}")

    # VACUUM
    print("[Step 5] VACUUM...")
    conn.isolation_level = None
    conn.execute("VACUUM")
    conn.isolation_level = ""
    print("[Step 5] Done.")


# ═══════════════════════════════════════════════════════════════
# Step 6: 验收
# ═══════════════════════════════════════════════════════════════

def step6_verify(conn):
    print("[Step 6] 验收...")
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    print(f"  Total memories: {total}")

    # 1. 字段就位
    null_decay = cur.execute("SELECT COUNT(*) FROM memories WHERE decay_class IS NULL").fetchone()[0]
    print(f"  decay_class NULL: {null_decay} (should be 0)")

    # 2. evicted 未复活
    evicted复活 = cur.execute(
        "SELECT COUNT(*) FROM memories WHERE memory_type='evicted' AND retention_state > 0"
    ).fetchone()[0]
    print(f"  evicted with retention_state > 0: {evicted复活} (should be 0)")

    # 3. 映射生效
    print("  decay_class distribution:")
    for row in cur.execute("SELECT decay_class, COUNT(*) FROM memories GROUP BY decay_class").fetchall():
        print(f"    {row[0]}: {row[1]}")

    print("  retention_state distribution:")
    for row in cur.execute("SELECT retention_state, COUNT(*) FROM memories GROUP BY retention_state").fetchall():
        print(f"    {row[0]}: {row[1]}")

    # 4. DB size
    db_size = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"  DB size: {db_size:.1f} MB")

    print("[Step 6] Done.")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def _fts_normalize(text) -> str:
    """CJK 单字切分：每个汉字两侧加空格。"""
    out = []
    for ch in str(text or ""):
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            out.append(" ")
            out.append(ch)
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


if __name__ == "__main__":
    print(f"DB: {DB_PATH}")
    print(f"Size before: {os.path.getsize(DB_PATH) / 1024 / 1024:.1f} MB")
    print()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # FK 默认 OFF，保持兼容
    conn.create_function("fts_norm", 1, _fts_normalize, deterministic=True)

    try:
        step1_ddl(conn)
        step2_retention_state(conn)
        step3_decay_class(conn)
        step4_evicted_cleanup(conn)
        step5_tags_int8(conn)
        step6_verify(conn)
    finally:
        conn.close()

    print()
    print(f"Size after: {os.path.getsize(DB_PATH) / 1024 / 1024:.1f} MB")
    print("Migration complete.")
