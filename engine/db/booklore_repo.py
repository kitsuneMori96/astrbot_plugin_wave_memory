"""BookLoreRepo — book_entities / book_relations / book_communities"""

from __future__ import annotations

import time
from typing import Optional

from .connection import ConnectionManager


class BookLoreRepo:
    """书籍知识图谱仓库（预留）。"""

    def __init__(self, cm: ConnectionManager):
        self.cm = cm
        self._create_tables()

    def _create_tables(self):
        self.cm.executescript("""
            CREATE TABLE IF NOT EXISTS book_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT DEFAULT 'concept',
                description TEXT,
                source_book TEXT,
                vector BLOB,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS book_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                context TEXT,
                created_at REAL,
                FOREIGN KEY (source_id) REFERENCES book_entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES book_entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS book_communities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                entity_ids TEXT,
                summary TEXT,
                created_at REAL
            );
        """)
        self.cm.commit()
