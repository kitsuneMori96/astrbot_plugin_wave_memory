"""BookLore 向量索引 — 书设知识的独立 HNSW 索引"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional, List, Tuple

import numpy as np

try:
    import hnswlib
except ImportError:
    hnswlib = None


class BookLoreIndex:
    """书设实体和社区报告的独立向量索引。"""

    def __init__(self, dimension: int, data_dir: str, max_elements: int = 50000):
        if hnswlib is None:
            raise ImportError("hnswlib is required: pip install hnswlib")

        self.dimension = dimension
        self.data_dir = data_dir
        self.max_elements = max_elements
        self._lock = threading.Lock()

        # 确保 book_lore.db 中的 SQL 表存在
        # 注意：此数据库独立于主 wave_memory.db，BookLoreRepo 建表在主库中，
        # 而 StudyService/BookLoreChannel 等直接读 book_lore.db。
        self._ensure_sql_tables()

        # 实体索引
        self.entity_index_path = os.path.join(data_dir, "book_entities.hnsw")
        self.entity_index = hnswlib.Index(space="cosine", dim=dimension)
        if os.path.exists(self.entity_index_path):
            self.entity_index.load_index(self.entity_index_path, max_elements=max_elements)
        else:
            self.entity_index.init_index(max_elements=max_elements, ef_construction=200, M=16)
        self.entity_index.set_ef(50)

        # 社区索引
        self.community_index_path = os.path.join(data_dir, "book_communities.hnsw")
        self.community_index = hnswlib.Index(space="cosine", dim=dimension)
        if os.path.exists(self.community_index_path):
            self.community_index.load_index(self.community_index_path, max_elements=10000)
        else:
            self.community_index.init_index(max_elements=10000, ef_construction=200, M=16)
        self.community_index.set_ef(50)

        # 笔记索引
        self.notes_index_path = os.path.join(data_dir, "book_notes.hnsw")
        self.notes_index = hnswlib.Index(space="cosine", dim=dimension)
        if os.path.exists(self.notes_index_path):
            self.notes_index.load_index(self.notes_index_path, max_elements=5000)
        else:
            self.notes_index.init_index(max_elements=5000, ef_construction=200, M=16)
        self.notes_index.set_ef(50)

        # ID 映射：hnsw 内部用 int id，需要映射到 entity text id
        self._entity_id_map: dict[int, str] = {}  # int_id → entity_id
        self._entity_int_counter = 0
        self._community_id_map: dict[int, str] = {}
        self._community_int_counter = 0
        self._notes_id_map: dict[int, str] = {}
        self._notes_int_counter = 0

    def _ensure_sql_tables(self):
        """确保 book_lore.db 中的 book_communities 等 SQL 表存在。

        BookLoreRepo 建表在主 wave_memory.db 中，但 StudyService、
        BookLoreChannel、SelfReflect 等直接连接 book_lore.db 查询，
        因此需要在此独立建表。
        """
        lore_db_path = os.path.join(self.data_dir, "book_lore.db")
        try:
            conn = sqlite3.connect(lore_db_path)
            conn.executescript("""
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
                    title TEXT,
                    summary TEXT,
                    rank REAL DEFAULT 0.0,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS book_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT,
                    source TEXT,
                    vector BLOB,
                    created_at REAL
                );
            """)
            conn.commit()
            conn.close()
        except Exception:
            pass  # 失败不影响主索引初始化

    # ─── 实体索引操作 ─────────────────────────────────────────────────────────

    def add_entity(self, entity_id: str, vector: np.ndarray) -> int:
        """添加单个实体向量，返回内部 int id。"""
        int_id = self._entity_int_counter
        self._entity_int_counter += 1
        self._entity_id_map[int_id] = entity_id
        with self._lock:
            current = self.entity_index.get_current_count()
            if current + 1 > self.max_elements:
                self.entity_index.resize_index(self.max_elements + 10000)
                self.max_elements += 10000
            self.entity_index.add_items(
                vector.astype(np.float32).reshape(1, -1),
                np.array([int_id], dtype=np.int64),
            )
        return int_id

    def add_entities_batch(self, entity_ids: List[str], vectors: np.ndarray):
        """批量添加实体向量。vectors shape: (n, dim)"""
        n = len(entity_ids)
        int_ids = list(range(self._entity_int_counter, self._entity_int_counter + n))
        self._entity_int_counter += n
        for int_id, eid in zip(int_ids, entity_ids):
            self._entity_id_map[int_id] = eid
        with self._lock:
            current = self.entity_index.get_current_count()
            needed = current + n
            if needed > self.max_elements:
                self.entity_index.resize_index(needed + 10000)
                self.max_elements = needed + 10000
            self.entity_index.add_items(
                vectors.astype(np.float32),
                np.array(int_ids, dtype=np.int64),
            )

    def search_entities(self, query_vector: np.ndarray, k: int = 10) -> List[Tuple[str, float]]:
        """搜索最相似的实体。返回 [(entity_id, similarity_score), ...]"""
        if self.entity_index.get_current_count() == 0:
            return []
        k = min(k, self.entity_index.get_current_count())
        with self._lock:
            labels, distances = self.entity_index.knn_query(
                query_vector.astype(np.float32).reshape(1, -1), k=k
            )
        results = []
        for int_id, dist in zip(labels[0].tolist(), distances[0].tolist()):
            entity_id = self._entity_id_map.get(int_id, "")
            if entity_id:
                # cosine distance → similarity: 1 - dist
                results.append((entity_id, 1.0 - dist))
        return results

    # ─── 社区索引操作 ─────────────────────────────────────────────────────────

    def add_community(self, community_id: str, vector: np.ndarray) -> int:
        """添加社区报告向量。"""
        int_id = self._community_int_counter
        self._community_int_counter += 1
        self._community_id_map[int_id] = community_id
        with self._lock:
            self.community_index.add_items(
                vector.astype(np.float32).reshape(1, -1),
                np.array([int_id], dtype=np.int64),
            )
        return int_id

    def add_communities_batch(self, community_ids: List[str], vectors: np.ndarray):
        """批量添加社区向量。"""
        n = len(community_ids)
        int_ids = list(range(self._community_int_counter, self._community_int_counter + n))
        self._community_int_counter += n
        for int_id, cid in zip(int_ids, community_ids):
            self._community_id_map[int_id] = cid
        with self._lock:
            self.community_index.add_items(
                vectors.astype(np.float32),
                np.array(int_ids, dtype=np.int64),
            )

    def search_communities(self, query_vector: np.ndarray, k: int = 5) -> List[Tuple[str, float]]:
        """搜索最相关的社区报告。"""
        if self.community_index.get_current_count() == 0:
            return []
        k = min(k, self.community_index.get_current_count())
        with self._lock:
            labels, distances = self.community_index.knn_query(
                query_vector.astype(np.float32).reshape(1, -1), k=k
            )
        results = []
        for int_id, dist in zip(labels[0].tolist(), distances[0].tolist()):
            cid = self._community_id_map.get(int_id, "")
            if cid:
                results.append((cid, 1.0 - dist))
        return results

    # ─── 笔记索引操作 ─────────────────────────────────────────────────────────

    def add_notes_batch(self, note_ids: List[str], vectors: np.ndarray):
        """批量添加笔记向量。"""
        n = len(note_ids)
        int_ids = list(range(self._notes_int_counter, self._notes_int_counter + n))
        self._notes_int_counter += n
        for int_id, nid in zip(int_ids, note_ids):
            self._notes_id_map[int_id] = nid
        with self._lock:
            current = self.notes_index.get_current_count()
            if current + n > 5000:
                self.notes_index.resize_index(current + n + 1000)
            self.notes_index.add_items(
                vectors.astype(np.float32),
                np.array(int_ids, dtype=np.int64),
            )

    def search_notes(self, query_vector: np.ndarray, k: int = 5) -> List[Tuple[str, float]]:
        """搜索最相关的笔记。"""
        if self.notes_index.get_current_count() == 0:
            return []
        k = min(k, self.notes_index.get_current_count())
        with self._lock:
            labels, distances = self.notes_index.knn_query(
                query_vector.astype(np.float32).reshape(1, -1), k=k
            )
        results = []
        for int_id, dist in zip(labels[0].tolist(), distances[0].tolist()):
            nid = self._notes_id_map.get(int_id, "")
            if nid:
                results.append((nid, 1.0 - dist))
        return results

    @property
    def notes_count(self) -> int:
        return self.notes_index.get_current_count()

    # ─── 持久化 ───────────────────────────────────────────────────────────────

    def save(self):
        """保存索引到磁盘。"""
        import json
        with self._lock:
            if self.entity_index.get_current_count() > 0:
                self.entity_index.save_index(self.entity_index_path)
            if self.community_index.get_current_count() > 0:
                self.community_index.save_index(self.community_index_path)
            if self.notes_index.get_current_count() > 0:
                self.notes_index.save_index(self.notes_index_path)

        # 保存 ID 映射
        map_path = os.path.join(self.data_dir, "book_lore_id_maps.json")
        with open(map_path, "w") as f:
            json.dump({
                "entity_map": {str(k): v for k, v in self._entity_id_map.items()},
                "entity_counter": self._entity_int_counter,
                "community_map": {str(k): v for k, v in self._community_id_map.items()},
                "community_counter": self._community_int_counter,
                "notes_map": {str(k): v for k, v in self._notes_id_map.items()},
                "notes_counter": self._notes_int_counter,
            }, f)

    def load_id_maps(self):
        """从磁盘加载 ID 映射。如果有 npz 向量文件但没有 hnsw 索引，自动构建。"""
        import json
        map_path = os.path.join(self.data_dir, "book_lore_id_maps.json")
        if os.path.exists(map_path):
            with open(map_path, "r") as f:
                data = json.load(f)
            self._entity_id_map = {int(k): v for k, v in data.get("entity_map", {}).items()}
            self._entity_int_counter = data.get("entity_counter", 0)
            self._community_id_map = {int(k): v for k, v in data.get("community_map", {}).items()}
            self._community_int_counter = data.get("community_counter", 0)
            self._notes_id_map = {int(k): v for k, v in data.get("notes_map", {}).items()}
            self._notes_int_counter = data.get("notes_counter", 0)
            return

        # 如果没有 id_maps 但有 npz 文件，从 npz 构建索引
        entity_npz = os.path.join(self.data_dir, "book_lore_entity_vectors.npz")
        if os.path.exists(entity_npz) and self.entity_index.get_current_count() == 0:
            try:
                data = np.load(entity_npz, allow_pickle=True)
                ids = data["ids"].tolist()
                vectors = data["vectors"]
                self.add_entities_batch(ids, vectors)
            except Exception:
                pass

        community_npz = os.path.join(self.data_dir, "book_lore_community_vectors.npz")
        if os.path.exists(community_npz) and self.community_index.get_current_count() == 0:
            try:
                data = np.load(community_npz, allow_pickle=True)
                ids = data["ids"].tolist()
                vectors = data["vectors"]
                self.add_communities_batch(ids, vectors)
            except Exception:
                pass

        notes_npz = os.path.join(self.data_dir, "book_lore_notes_vectors.npz")
        if os.path.exists(notes_npz) and self.notes_index.get_current_count() == 0:
            try:
                data = np.load(notes_npz, allow_pickle=True)
                ids = data["ids"].tolist()
                vectors = data["vectors"]
                self.add_notes_batch(ids, vectors)
            except Exception:
                pass

        # 保存构建好的索引
        if self.entity_index.get_current_count() > 0 or self.community_index.get_current_count() > 0 or self.notes_index.get_current_count() > 0:
            self.save()

    @property
    def entity_count(self) -> int:
        return self.entity_index.get_current_count()

    @property
    def community_count(self) -> int:
        return self.community_index.get_current_count()
