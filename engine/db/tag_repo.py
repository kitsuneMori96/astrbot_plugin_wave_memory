"""TagRepo — tags + tag_relations + tag_extraction_status + tag_intrinsic_residuals + tag_pair_similarity"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from .connection import ConnectionManager


class TagRepo:
    """标签数据仓库。"""

    def __init__(self, cm: ConnectionManager):
        self.cm = cm
        self._create_tables()

    def _create_tables(self):
        self.cm.executescript("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                tag_type TEXT DEFAULT 'keyword',
                vector BLOB,
                parent_id INTEGER,
                aliases TEXT,
                description TEXT,
                frequency INTEGER DEFAULT 0,
                last_seen REAL,
                confidence REAL DEFAULT 1.0,
                is_core BOOLEAN DEFAULT 0,
                metadata TEXT,
                created_at REAL NOT NULL,
                updated_at REAL,
                FOREIGN KEY (parent_id) REFERENCES tags(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS tag_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_tag_id INTEGER NOT NULL,
                target_tag_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                confidence REAL DEFAULT 1.0,
                metadata TEXT,
                created_at REAL,
                FOREIGN KEY (source_tag_id) REFERENCES tags(id) ON DELETE CASCADE,
                FOREIGN KEY (target_tag_id) REFERENCES tags(id) ON DELETE CASCADE,
                UNIQUE(source_tag_id, target_tag_id, relation_type)
            );

            CREATE TABLE IF NOT EXISTS tag_extraction_status (
                memory_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS tag_intrinsic_residuals (
                tag_id INTEGER PRIMARY KEY,
                residual_energy REAL NOT NULL,
                computed_at REAL NOT NULL,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tag_pair_similarity (
                tag_a INTEGER NOT NULL,
                tag_b INTEGER NOT NULL,
                similarity REAL NOT NULL,
                computed_at REAL,
                PRIMARY KEY (tag_a, tag_b)
            );

            CREATE INDEX IF NOT EXISTS idx_tags_type ON tags(tag_type);
            CREATE INDEX IF NOT EXISTS idx_tags_parent ON tags(parent_id);
            CREATE INDEX IF NOT EXISTS idx_tag_relations_source ON tag_relations(source_tag_id);
            CREATE INDEX IF NOT EXISTS idx_tag_relations_target ON tag_relations(target_tag_id);
        """)
        self.cm.commit()

    def add_tag(self, name: str, vector: Optional[np.ndarray] = None) -> int:
        vec_blob = vector.astype(np.float32).tobytes() if vector is not None else None
        cur = self.cm.execute_write(
            "INSERT OR IGNORE INTO tags (name, vector, created_at) VALUES (?, ?, ?)",
            (name, vec_blob, time.time()),
        )
        if cur.lastrowid == 0:
            row = self.cm.execute_read("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            if row:
                if vec_blob:
                    self.cm.execute_write("UPDATE tags SET vector=? WHERE id=?", (vec_blob, row[0]))
                self.cm.commit()
                return row[0]
        self.cm.commit()
        return cur.lastrowid

    def add_tag_extended(
        self,
        name: str,
        tag_type: str = "keyword",
        vector: Optional[np.ndarray] = None,
        parent_id: Optional[int] = None,
        aliases: Optional[list] = None,
        description: str = "",
        confidence: float = 1.0,
        metadata: Optional[dict] = None,
    ) -> int:
        import json as _json
        vec_blob = vector.astype(np.float32).tobytes() if vector is not None else None
        aliases_str = ",".join(aliases) if aliases else None
        meta_str = _json.dumps(metadata, ensure_ascii=False) if metadata else None
        now = time.time()

        cur = self.cm.execute_write(
            "INSERT OR IGNORE INTO tags (name, tag_type, vector, parent_id, aliases, description, frequency, last_seen, confidence, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (name, tag_type, vec_blob, parent_id, aliases_str, description, now, confidence, meta_str, now, now),
        )
        if cur.lastrowid == 0:
            row = self.cm.execute_read("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            if row:
                updates = ["frequency = frequency + 1", "last_seen = ?", "updated_at = ?"]
                params = [now, now]
                if vec_blob:
                    updates.append("vector = ?")
                    params.append(vec_blob)
                if tag_type != "keyword":
                    updates.append("tag_type = ?")
                    params.append(tag_type)
                if description:
                    updates.append("description = ?")
                    params.append(description)
                params.append(row[0])
                self.cm.execute_write(f"UPDATE tags SET {', '.join(updates)} WHERE id = ?", params)
                self.cm.commit()
                return row[0]
        self.cm.commit()
        return cur.lastrowid

    def get_tag_count(self) -> int:
        return self.cm.execute_read("SELECT COUNT(*) FROM tags").fetchone()[0]

    def get_all_tag_vectors(self, limit: Optional[int] = None) -> list:
        if limit is not None:
            rows = self.cm.execute_read(
                "SELECT id, name, vector FROM tags WHERE vector IS NOT NULL ORDER BY frequency DESC LIMIT ?",
                (limit,)
            ).fetchall()
        else:
            rows = self.cm.execute_read(
                "SELECT id, name, vector FROM tags WHERE vector IS NOT NULL"
            ).fetchall()
        return [(r[0], r[1], np.frombuffer(r[2], dtype=np.float32)) for r in rows]

    def get_tag_vectors_by_ids(self, ids: list[int]) -> dict[int, np.ndarray]:
        """按需批量查询指定 tag_id 的向量，避免全量加载到内存。"""
        if not ids:
            return {}
        result: dict[int, np.ndarray] = {}
        CHUNK = 900
        unique_ids = list({int(i) for i in ids})
        for i in range(0, len(unique_ids), CHUNK):
            chunk = unique_ids[i:i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self.cm.execute_read(
                f"SELECT id, vector FROM tags WHERE id IN ({placeholders}) AND vector IS NOT NULL",
                chunk,
            ).fetchall()
            for r in rows:
                result[r[0]] = np.frombuffer(r[1], dtype=np.float32)
        return result

    def get_legacy_tag_vectors_by_ids(self, ids: list[int]) -> dict[int, np.ndarray]:
        """Return vectors in the legacy ``tags.id`` label space only."""
        return self.get_tag_vectors_by_ids(ids)

    def list_legacy_memory_tags(self, memory_ids: list[int]) -> dict[int, list[dict]]:
        """Read existing legacy tag evidence without creating or rewriting links."""
        if not memory_ids:
            return {}
        columns = {
            str(row[1]) for row in self.cm.execute_read("PRAGMA table_info(memory_tags)").fetchall()
        }
        if not {"memory_id", "tag_id"} <= columns:
            return {}
        try:
            ids = sorted({int(value) for value in memory_ids})
        except (TypeError, ValueError):
            return {}
        if not ids:
            return {}
        relevance = "COALESCE(relevance, 1.0)" if "relevance" in columns else "1.0"
        result: dict[int, list[dict]] = {}
        for offset in range(0, len(ids), 900):
            chunk = ids[offset : offset + 900]
            placeholders = ",".join("?" * len(chunk))
            rows = self.cm.execute_read(
                f"SELECT memory_id, tag_id, {relevance} AS relevance "
                f"FROM memory_tags WHERE memory_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for memory_id, tag_id, tag_relevance in rows:
                result.setdefault(int(memory_id), []).append({
                    "tag_id": int(tag_id),
                    "relevance": float(tag_relevance if tag_relevance is not None else 1.0),
                })
        return result

    def add_tag_relation(
        self,
        source_tag_id: int,
        target_tag_id: int,
        relation_type: str,
        weight: float = 1.0,
        confidence: float = 1.0,
        metadata: Optional[dict] = None,
    ):
        import json as _json
        meta_str = _json.dumps(metadata, ensure_ascii=False) if metadata else None
        self.cm.execute_write(
            """INSERT OR REPLACE INTO tag_relations (source_tag_id, target_tag_id, relation_type, weight, confidence, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_tag_id, target_tag_id, relation_type, weight, confidence, meta_str, time.time()),
        )
        self.cm.commit()

    def get_tag_children(self, parent_id: int) -> list:
        rows = self.cm.execute_read(
            "SELECT id, name, tag_type, frequency FROM tags WHERE parent_id = ?", (parent_id,)
        ).fetchall()
        return [{"id": r[0], "name": r[1], "tag_type": r[2], "frequency": r[3]} for r in rows]

    def get_tag_relations(self, tag_id: int) -> list:
        rows = self.cm.execute_read(
            """SELECT tr.id, tr.target_tag_id, t.name, tr.relation_type, tr.weight, tr.confidence
               FROM tag_relations tr
               JOIN tags t ON t.id = tr.target_tag_id
               WHERE tr.source_tag_id = ?
               UNION
               SELECT tr.id, tr.source_tag_id, t.name, tr.relation_type, tr.weight, tr.confidence
               FROM tag_relations tr
               JOIN tags t ON t.id = tr.source_tag_id
               WHERE tr.target_tag_id = ?""",
            (tag_id, tag_id),
        ).fetchall()
        return [
            {"id": r[0], "related_tag_id": r[1], "related_tag_name": r[2],
             "relation_type": r[3], "weight": r[4], "confidence": r[5]}
            for r in rows
        ]

    def find_tag_by_alias(self, alias: str) -> Optional[int]:
        row = self.cm.execute_read(
            "SELECT id FROM tags WHERE name = ? OR aliases LIKE ?",
            (alias, f"%{alias}%"),
        ).fetchone()
        return row[0] if row else None
