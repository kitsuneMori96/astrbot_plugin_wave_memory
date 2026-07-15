"""MemoryRepo — memories + memory_tags + memory_vectors 表操作"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from .connection import ConnectionManager


class MemoryRepo:
    """记忆数据仓库：memories / memory_tags / memory_vectors 表。"""

    def __init__(self, cm: ConnectionManager):
        self.cm = cm
        self._create_tables()

    def _create_tables(self):
        self.cm.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                content TEXT NOT NULL,
                vector BLOB,
                timestamp REAL NOT NULL,
                importance REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                last_accessed REAL,
                memory_type TEXT DEFAULT 'message',
                source TEXT DEFAULT 'live',
                summary TEXT
            );

            CREATE TABLE IF NOT EXISTS memory_tags (
                memory_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                position INTEGER DEFAULT 0,
                relevance REAL DEFAULT 1.0,
                PRIMARY KEY (memory_id, tag_id),
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memory_vectors (
                memory_id INTEGER PRIMARY KEY,
                vector BLOB NOT NULL,
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_memories_group ON memories(group_id);
            CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp);
            CREATE INDEX IF NOT EXISTS idx_memory_tags_tag ON memory_tags(tag_id);
        """)
        self.cm.commit()

    def add_memory(
        self,
        group_id: str,
        content: str,
        vector: Optional[np.ndarray] = None,
        sender_id: str = "",
        sender_name: str = "",
        timestamp: Optional[float] = None,
        importance: float = 1.0,
        source: str = "live",
    ) -> int:
        ts = timestamp or time.time()
        vec_blob = vector.astype(np.float32).tobytes() if vector is not None else None
        cur = self.cm.execute_write(
            """INSERT INTO memories (group_id, sender_id, sender_name, content, vector, timestamp, importance, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (group_id, sender_id, sender_name, content, vec_blob, ts, importance, source),
        )
        self.cm.commit()
        return cur.lastrowid

    def get_memory_by_id(self, memory_id: int) -> Optional[dict]:
        row = self.cm.execute_read(
            "SELECT id, group_id, sender_id, sender_name, content, vector, timestamp, importance, access_count FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "group_id": row[1], "sender_id": row[2],
            "sender_name": row[3], "content": row[4],
            "vector": np.frombuffer(row[5], dtype=np.float32) if row[5] else None,
            "timestamp": row[6], "importance": row[7], "access_count": row[8],
        }

    def get_all_memory_vectors(self, group_id: Optional[str] = None) -> list:
        if group_id:
            rows = self.cm.execute_read(
                "SELECT id, vector FROM memories WHERE group_id=? AND vector IS NOT NULL AND memory_type = 'message'", (group_id,)
            ).fetchall()
        else:
            rows = self.cm.execute_read(
                "SELECT id, vector FROM memories WHERE vector IS NOT NULL AND memory_type = 'message'"
            ).fetchall()
        return [(r[0], np.frombuffer(r[1], dtype=np.float32)) for r in rows]

    def get_memories_by_ids(self, ids: list) -> list:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.cm.execute_read(
            f"""SELECT id, group_id, sender_id, sender_name, content, timestamp, importance, access_count, source, memory_type
                FROM memories WHERE id IN ({placeholders}) AND memory_type = 'message'""",
            ids,
        ).fetchall()
        return [
            {"id": r[0], "group_id": r[1], "sender_id": r[2], "sender_name": r[3],
             "content": r[4], "timestamp": r[5], "importance": r[6],
             "access_count": r[7] if len(r) > 7 else 0, "source": r[8], "memory_type": r[9]}
            for r in rows
        ]

    def touch_memories(self, ids: list, importance_boost: float = 0.01):
        """标记记忆被访问 + 微量提升 importance。"""
        now = time.time()
        for mid in ids:
            self.cm.execute_write(
                "UPDATE memories SET access_count = access_count + 1, last_accessed = ?, importance = MIN(3.0, importance + ?) WHERE id = ?",
                (now, importance_boost, mid),
            )
        self.cm.commit()

    def get_memory_count(self, group_id: Optional[str] = None) -> int:
        if group_id:
            return self.cm.execute_read(
                "SELECT COUNT(*) FROM memories WHERE group_id=?", (group_id,)
            ).fetchone()[0]
        return self.cm.execute_read("SELECT COUNT(*) FROM memories").fetchone()[0]

    def link_memory_tags(self, memory_id: int, tag_ids: list):
        for pos, tid in enumerate(tag_ids, 1):
            self.cm.execute_write(
                "INSERT OR IGNORE INTO memory_tags (memory_id, tag_id, position) VALUES (?, ?, ?)",
                (memory_id, tid, pos),
            )
        self.cm.commit()

    def get_memory_vectors(self, memory_ids: list) -> dict:
        """批量获取记忆向量。返回 {memory_id: np.ndarray}。"""
        if not memory_ids:
            return {}
        placeholders = ",".join("?" * len(memory_ids))
        # 先从 memory_vectors 表查
        rows = self.cm.execute_read(
            f"SELECT memory_id, vector FROM memory_vectors WHERE memory_id IN ({placeholders})",
            memory_ids,
        ).fetchall()
        result = {}
        for row in rows:
            try:
                vec = np.frombuffer(row[1], dtype=np.float32)
                if len(vec) > 0:
                    result[row[0]] = vec
            except Exception:
                continue
        # fallback: 从 memories.vector 列读
        missing = [mid for mid in memory_ids if mid not in result]
        if missing:
            ph2 = ",".join("?" * len(missing))
            rows2 = self.cm.execute_read(
                f"SELECT id, vector FROM memories WHERE id IN ({ph2}) AND vector IS NOT NULL",
                missing,
            ).fetchall()
            for row in rows2:
                try:
                    vec = np.frombuffer(row[1], dtype=np.float32)
                    if len(vec) > 0:
                        result[row[0]] = vec
                except Exception:
                    continue
        return result

    def delete_memory(self, memory_id: int) -> bool:
        existing = self.cm.execute_read("SELECT id FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not existing:
            return False
        self.cm.execute_write("DELETE FROM memory_tags WHERE memory_id=?", (memory_id,))
        self.cm.execute_write("DELETE FROM memories WHERE id=?", (memory_id,))
        self.cm.commit()
        self.cm._sync_index_delete([memory_id])
        return True

    def delete_memories(self, ids: list) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        self.cm.execute_write(f"DELETE FROM memory_tags WHERE memory_id IN ({placeholders})", ids)
        cursor = self.cm.execute_write(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
        self.cm.commit()
        self.cm._sync_index_delete(ids)
        return cursor.rowcount

    def update_memory(self, memory_id: int, content: str = None, importance: float = None) -> bool:
        updates = []
        params = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if importance is not None:
            updates.append("importance = ?")
            params.append(importance)
        if not updates:
            return False
        params.append(memory_id)
        self.cm.execute_write(f"UPDATE memories SET {', '.join(updates)} WHERE id=?", params)
        self.cm.commit()
        return True

    def update_memory_vector(self, memory_id: int, vector: np.ndarray):
        self.cm.execute_write(
            "UPDATE memories SET vector=? WHERE id=?",
            (vector.tobytes(), memory_id),
        )
        self.cm.commit()

    def get_memories_without_tags(self, limit: int = 100) -> list:
        rows = self.cm.execute_read(
            """SELECT id FROM memories
               WHERE id NOT IN (SELECT DISTINCT memory_id FROM memory_tags)
               AND LENGTH(content) >= 10
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_memories_without_vector(self, limit: int = 100) -> list:
        rows = self.cm.execute_read(
            "SELECT id FROM memories WHERE vector IS NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_cooccurrence_data(self) -> list:
        rows = self.cm.execute_read("""
            SELECT a.tag_id, b.tag_id, COUNT(*) as cnt
            FROM memory_tags a
            JOIN memory_tags b ON a.memory_id = b.memory_id AND a.tag_id < b.tag_id
            GROUP BY a.tag_id, b.tag_id
        """).fetchall()
        return rows
