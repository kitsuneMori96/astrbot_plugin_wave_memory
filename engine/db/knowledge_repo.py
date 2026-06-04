"""KnowledgeRepo — facts + kv_store"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from .connection import ConnectionManager


class KnowledgeRepo:
    """知识数据仓库：事实三元组 + KV 存储。"""

    def __init__(self, cm: ConnectionManager):
        self.cm = cm
        self._create_tables()

    def _create_tables(self):
        self.cm.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                group_id TEXT,
                source_memory_id INTEGER,
                confidence REAL DEFAULT 1.0,
                valid_from REAL,
                valid_until REAL,
                created_at REAL,
                FOREIGN KEY (source_memory_id) REFERENCES memories(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT,
                vector BLOB
            );

            CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
            CREATE INDEX IF NOT EXISTS idx_facts_object ON facts(object);
        """)
        self.cm.commit()

    def put_kv(self, key: str, value: str, vector: Optional[np.ndarray] = None):
        vec_blob = vector.astype(np.float32).tobytes() if vector is not None else None
        self.cm.execute_write(
            "INSERT OR REPLACE INTO kv_store (key, value, vector) VALUES (?, ?, ?)",
            (key, value, vec_blob),
        )
        self.cm.commit()

    def get_kv(self, key: str) -> Optional[tuple]:
        row = self.cm.execute_read("SELECT value, vector FROM kv_store WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        vec = np.frombuffer(row[1], dtype=np.float32) if row[1] else None
        return (row[0], vec)

    def insert_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        group_id: str = None,
        source_memory_id: int = None,
        confidence: float = 0.8,
    ) -> int:
        existing = self.cm.execute_read(
            "SELECT id FROM facts WHERE subject = ? AND predicate = ? AND object = ?",
            (subject, predicate, obj),
        ).fetchone()
        if existing:
            self.cm.execute_write(
                "UPDATE facts SET confidence = MAX(confidence, ?), valid_from = ? WHERE id = ?",
                (confidence, time.time(), existing[0]),
            )
            return existing[0]
        cursor = self.cm.execute_write(
            """INSERT INTO facts (subject, predicate, object, group_id, source_memory_id, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (subject, predicate, obj, group_id, source_memory_id, confidence, time.time()),
        )
        self.cm.commit()
        return cursor.lastrowid

    def get_facts_by_subject(self, subject: str, limit: int = 20) -> list:
        rows = self.cm.execute_read(
            "SELECT id, subject, predicate, object, confidence, created_at FROM facts WHERE subject = ? ORDER BY confidence DESC LIMIT ?",
            (subject, limit),
        ).fetchall()
        return [{"id": r[0], "subject": r[1], "predicate": r[2], "object": r[3], "confidence": r[4], "created_at": r[5]} for r in rows]

    def memory_exists_by_hash(self, content_hash: str) -> bool:
        row = self.cm.execute_read(
            "SELECT 1 FROM kv_store WHERE key = ?", (f"hash:{content_hash}",)
        ).fetchone()
        return row is not None

    def mark_imported(self, content_hash: str):
        self.cm.execute_write(
            "INSERT OR IGNORE INTO kv_store (key, value) VALUES (?, ?)",
            (f"hash:{content_hash}", "1"),
        )
        self.cm.commit()
