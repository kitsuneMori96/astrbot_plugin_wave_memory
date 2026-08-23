"""Wave Memory 数据库层 — Facade 模式（组合 5 个 Repo）

对外保持所有 70+ 方法签名不变，内部委托给 repo。
"""

import json
import os
import time
from typing import Any, Optional

import numpy as np
from astrbot.api import logger

from .db.connection import ConnectionManager
from .db.memory_repo import MemoryRepo
from .db.tag_repo import TagRepo
from .db.social_repo import SocialRepo
from .db.knowledge_repo import KnowledgeRepo
from .db.booklore_repo import BookLoreRepo
from .db.belief_repo import BeliefRepo
from .metrics_store import InjectionMetricStore


class WaveMemoryDB:
    """SQLite 数据库 Facade —— 组合 5 个 Repo，对外接口不变。"""

    def __init__(self, db_path: str, dimension: int = 1024):
        self.db_path = db_path
        self.dimension = dimension

        # 核心连接管理器
        self._cm = ConnectionManager(db_path)

        # 初始化各 Repo
        self._memory_repo = MemoryRepo(self._cm)
        self._tag_repo = TagRepo(self._cm)
        self._social_repo = SocialRepo(self._cm)
        self._knowledge_repo = KnowledgeRepo(self._cm)
        self._booklore_repo = BookLoreRepo(self._cm)
        self._belief_repo = BeliefRepo(self._cm)
        self._injection_metrics = InjectionMetricStore(self._cm)

        # FTS5 + 其他迁移
        self._injection_metrics.ensure_schema()
        self._setup_fts5()
        self._setup_audit_table()
        self._setup_jargon_knowledge_tables()
        self._backfill_tag_relations_created_at()

    @property
    def conn(self):
        """返回带锁代理，确保所有通过 db.conn.execute() 的调用都序列化。"""
        return self._cm

    @property
    def memory_index(self):
        return self._cm.memory_index

    @memory_index.setter
    def memory_index(self, value):
        self._cm.memory_index = value

    @property
    def closed(self):
        return self._cm.closed

    def reopen(self):
        self._cm.reopen()

    def close(self):
        self._cm.close()

    def record_injection_metric(self, sample: dict) -> None:
        """持久化一次 inject_memory 指标样本。"""
        self._injection_metrics.record(sample)

    def get_injection_metrics(self, from_ts: float, to_ts: float, bucket_seconds: int) -> dict:
        """查询指定时间范围的 inject_memory 指标聚合。"""
        return self._injection_metrics.query(from_ts, to_ts, bucket_seconds)

    def cleanup_injection_metrics(self, retention_seconds: float = 31 * 86400) -> int:
        """清理过期 inject_memory 指标样本。"""
        return self._injection_metrics.cleanup(retention_seconds=retention_seconds)

    # ═══════════════════════════════════════════════════════
    # Memory 委托
    # ═══════════════════════════════════════════════════════

    def add_memory(self, group_id, content, vector=None, sender_id="", sender_name="", timestamp=None, importance=1.0, source="live"):
        return self._memory_repo.add_memory(group_id, content, vector, sender_id, sender_name, timestamp, importance, source)

    def get_memory_by_id(self, memory_id):
        return self._memory_repo.get_memory_by_id(memory_id)

    def get_all_memory_vectors(self, group_id=None):
        return self._memory_repo.get_all_memory_vectors(group_id)

    def get_memories_by_ids(self, ids):
        return self._memory_repo.get_memories_by_ids(ids)

    def touch_memories(self, ids, importance_boost: float = 0.01):
        return self._memory_repo.touch_memories(ids, importance_boost=importance_boost)

    def apply_memory_decay(self, config: dict) -> dict:
        return self._memory_repo.apply_memory_decay(config)

    def unarchive_memory(self, memory_id: int) -> bool:
        return self._memory_repo.unarchive_memory(memory_id)

    def get_memory_count(self, group_id=None):
        return self._memory_repo.get_memory_count(group_id)

    def link_memory_tags(self, memory_id, tag_ids):
        return self._memory_repo.link_memory_tags(memory_id, tag_ids)

    def get_memory_vectors(self, memory_ids):
        return self._memory_repo.get_memory_vectors(memory_ids)

    def delete_memory(self, memory_id):
        return self._memory_repo.delete_memory(memory_id)

    def update_source(self, memory_id: int, new_source: str):
        """更新记忆的 source 分类。"""
        self.conn.execute("UPDATE memories SET source = ? WHERE id = ?", (new_source, memory_id))
        self.conn.commit()

    def get_stale_memories(self, source: str, last_accessed_before: float) -> list[int]:
        """获取指定 source 中长时间未被访问的记忆 ID。"""
        cutoff = time.time() - last_accessed_before
        rows = self.conn.execute(
            "SELECT id FROM memories WHERE source = ? AND (last_accessed IS NULL OR last_accessed < ?)",
            (source, cutoff),
        ).fetchall()
        return [r[0] for r in rows]

    def delete_memories_by_source(self, source: str, older_than_seconds: float) -> int:
        """删除指定 source 中超过一定时间的记忆。返回删除数量。"""
        cutoff = time.time() - older_than_seconds
        rows = self.conn.execute(
            "SELECT id FROM memories WHERE source = ? AND timestamp < ?",
            (source, cutoff),
        ).fetchall()
        ids = [r[0] for r in rows]
        # 先清标签关联，避免孤儿行虚增「标签覆盖率」统计
        if ids:
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(
                f"DELETE FROM memory_tags WHERE memory_id IN ({placeholders})",
                ids,
            )
        cursor = self.conn.execute(
            "DELETE FROM memories WHERE source = ? AND timestamp < ?",
            (source, cutoff),
        )
        self.conn.commit()
        if ids:
            # 同步清理向量索引，避免 HNSW 幽灵条目
            self._sync_index_delete(ids)
        return cursor.rowcount

    def mark_evicted(self, memory_id: int):
        """标记记忆为已从索引中移除（保留 DB 数据）。"""
        self.conn.execute(
            "UPDATE memories SET memory_type = 'evicted' WHERE id = ?",
            (memory_id,),
        )
        self.conn.commit()

    def get_memory_ids_by_source(self, source: str) -> list[int]:
        """获取指定 source 的所有记忆 ID（用于索引重建）。"""
        rows = self.conn.execute(
            "SELECT id FROM memories WHERE source = ? AND vector IS NOT NULL",
            (source,),
        ).fetchall()
        return [r[0] for r in rows]

    def delete_memories(self, ids):
        return self._memory_repo.delete_memories(ids)

    def update_memory(self, memory_id, content=None, importance=None):
        return self._memory_repo.update_memory(memory_id, content, importance)

    def update_memory_vector(self, memory_id, vector):
        return self._memory_repo.update_memory_vector(memory_id, vector)

    def get_memories_without_tags(self, limit=100):
        return self._memory_repo.get_memories_without_tags(limit)

    def get_memories_without_vector(self, limit=100):
        return self._memory_repo.get_memories_without_vector(limit)

    def get_cooccurrence_data(self):
        return self._memory_repo.get_cooccurrence_data()

    # ═══════════════════════════════════════════════════════
    # Tag 委托
    # ═══════════════════════════════════════════════════════

    def add_tag(self, name, vector=None):
        return self._tag_repo.add_tag(name, vector)

    def add_tag_extended(self, name, tag_type="keyword", vector=None, parent_id=None, aliases=None, description="", confidence=1.0, metadata=None):
        return self._tag_repo.add_tag_extended(name, tag_type, vector, parent_id, aliases, description, confidence, metadata)

    def get_tag_count(self):
        return self._tag_repo.get_tag_count()

    def get_all_tag_vectors(self, limit: Optional[int] = None):
        return self._tag_repo.get_all_tag_vectors(limit)

    def get_tag_vectors_by_ids(self, ids: list[int]) -> dict:
        return self._tag_repo.get_tag_vectors_by_ids(ids)

    def add_tag_relation(self, source_tag_id, target_tag_id, relation_type, weight=1.0, confidence=1.0, metadata=None):
        return self._tag_repo.add_tag_relation(source_tag_id, target_tag_id, relation_type, weight, confidence, metadata)

    def get_tag_children(self, parent_id):
        return self._tag_repo.get_tag_children(parent_id)

    def get_tag_relations(self, tag_id):
        return self._tag_repo.get_tag_relations(tag_id)

    def find_tag_by_alias(self, alias):
        return self._tag_repo.find_tag_by_alias(alias)

    # ═══════════════════════════════════════════════════════
    # Social 委托
    # ═══════════════════════════════════════════════════════

    def set_mood(self, group_id, mood_type, intensity=0.5, description="", duration_hours=2.0):
        return self._social_repo.set_mood(group_id, mood_type, intensity, description, duration_hours)

    def get_active_mood(self, group_id):
        return self._social_repo.get_active_mood(group_id)

    def get_person_by_qq(self, qq_id):
        return self._social_repo.get_person_by_qq(qq_id)

    def find_person_by_name(self, name):
        return self._social_repo.find_person_by_name(name)

    def get_memories_by_person(self, qq_id, role=None, limit=50, offset=0):
        return self._social_repo.get_memories_by_person(qq_id, role, limit, offset)

    def get_person_cooccurrence(self, qq_id, top_k=10):
        return self._social_repo.get_person_cooccurrence(qq_id, top_k)

    def get_person_stats(self, qq_id):
        return self._social_repo.get_person_stats(qq_id)

    def resolve_canonical_id(self, local_id: str, bot_id: str = "yushu") -> str:
        return self._social_repo.resolve_canonical_id(local_id, bot_id)

    def add_binding(self, local_id: str, master_id: str, bot_id: str = "yushu", platform: str = "qq") -> dict:
        return self._social_repo.add_binding(local_id, master_id, bot_id, platform)

    def remove_binding(self, binding_id: int):
        self._social_repo.remove_binding(binding_id)

    def get_bindings(self, bot_id: Optional[str] = None, search: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        return self._social_repo.get_bindings(bot_id, search, limit, offset)

    def count_bindings(self, bot_id: Optional[str] = None, search: str = "") -> int:
        return self._social_repo.count_bindings(bot_id, search)

    # ═══════════════════════════════════════════════════════
    # Knowledge 委托
    # ═══════════════════════════════════════════════════════

    def put_kv(self, key, value, vector=None):
        return self._knowledge_repo.put_kv(key, value, vector)

    def get_kv(self, key):
        return self._knowledge_repo.get_kv(key)

    def insert_fact(self, subject, predicate, obj, group_id=None, source_memory_id=None, confidence=0.8, fact_type=None):
        return self._knowledge_repo.insert_fact(subject, predicate, obj, group_id, source_memory_id, confidence, fact_type)

    def get_facts_by_subject(self, subject, limit=20):
        return self._knowledge_repo.get_facts_by_subject(subject, limit)

    def set_facts_decay_rate(self, rate: float):
        """设置 facts 时间衰减速率。"""
        self._knowledge_repo.set_decay_rate(rate)

    def memory_exists_by_hash(self, content_hash):
        return self._knowledge_repo.memory_exists_by_hash(content_hash)

    def mark_imported(self, content_hash):
        return self._knowledge_repo.mark_imported(content_hash)

    # ═══════════════════════════════════════════════════════
    # WebUI + 兼容方法（直接在 facade 层实现）
    # ═══════════════════════════════════════════════════════

    def get_memory_count_with_vector(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE vector IS NOT NULL"
        ).fetchone()[0]

    def get_group_list(self) -> list:
        rows = self.conn.execute(
            "SELECT DISTINCT group_id FROM memories ORDER BY group_id"
        ).fetchall()
        return [r[0] for r in rows]

    def get_today_new_count(self) -> int:
        import datetime
        today_start = datetime.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        return self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE timestamp >= ?", (today_start,)
        ).fetchone()[0]

    def list_memories(self, offset=0, limit=20, group_id=None, sender=None, from_ts=None, to_ts=None, search=None, has_tags=None, has_vector=None):
        conditions = []
        params = []
        if group_id:
            conditions.append("m.group_id = ?")
            params.append(group_id)
        if sender:
            conditions.append("(m.sender_id = ? OR m.sender_name LIKE ?)")
            params.append(sender)
            params.append(f"%{sender}%")
        if from_ts:
            conditions.append("m.timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            conditions.append("m.timestamp <= ?")
            params.append(to_ts)
        if search:
            conditions.append("m.content LIKE ?")
            params.append(f"%{search}%")
        if has_tags is True:
            conditions.append("m.id IN (SELECT DISTINCT memory_id FROM memory_tags)")
        elif has_tags is False:
            conditions.append("m.id NOT IN (SELECT DISTINCT memory_id FROM memory_tags)")
        if has_vector is True:
            conditions.append("m.vector IS NOT NULL")
        elif has_vector is False:
            conditions.append("m.vector IS NULL")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(f"SELECT COUNT(*) FROM memories m {where}", params).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT m.id, m.group_id, m.sender_id, m.sender_name, m.content, m.vector IS NOT NULL,
                       m.timestamp, m.importance, m.access_count, m.last_accessed
                FROM memories m {where} ORDER BY m.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        items = []
        for r in rows:
            tags = self.conn.execute(
                """SELECT t.name, t.tag_type FROM tags t
                   JOIN memory_tags mt ON t.id = mt.tag_id
                   WHERE mt.memory_id = ? ORDER BY mt.position LIMIT 5""", (r[0],)
            ).fetchall()
            items.append({
                "id": r[0], "group_id": r[1], "sender_id": r[2], "sender_name": r[3],
                "content": r[4][:100] if r[4] else "", "has_vector": bool(r[5]),
                "timestamp": r[6], "importance": r[7], "access_count": r[8],
                "last_accessed": r[9], "tags": [{"name": t[0], "type": t[1]} for t in tags],
            })
        return items, total

    def get_memory_detail(self, memory_id):
        row = self.conn.execute(
            """SELECT id, group_id, sender_id, sender_name, content, source, vector IS NOT NULL,
                      timestamp, importance, access_count, last_accessed FROM memories WHERE id=?""",
            (memory_id,),
        ).fetchone()

        if not row:
            return None
        tags = self.conn.execute(
            """SELECT t.id, t.name, t.tag_type FROM tags t JOIN memory_tags mt ON t.id = mt.tag_id
               WHERE mt.memory_id = ? ORDER BY mt.position""", (memory_id,)
        ).fetchall()
        return {
            "id": row[0], "group_id": row[1], "sender_id": row[2], "sender_name": row[3],
            "content": row[4], "source": row[5], "has_vector": bool(row[6]), "timestamp": row[7],
            "importance": row[8], "access_count": row[9], "last_accessed": row[10],
            "tags": [{"id": t[0], "name": t[1], "type": t[2]} for t in tags],
        }

    def get_memory_brief(self, memory_id):
        row = self.conn.execute(
            "SELECT id, content, sender_name, group_id, timestamp FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        return {"memory_id": row[0], "content": row[1][:200] if row[1] else "", "sender_name": row[2], "group_id": row[3], "timestamp": row[4]}

    def list_tags(self, offset=0, limit=50):
        total = self.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        rows = self.conn.execute(
            """SELECT t.id, t.name, t.created_at, t.vector IS NOT NULL,
                      (SELECT COUNT(*) FROM memory_tags mt WHERE mt.tag_id = t.id) as mem_count
               FROM tags t ORDER BY mem_count DESC LIMIT ? OFFSET ?""", (limit, offset)
        ).fetchall()
        items = [{"id": r[0], "name": r[1], "created_at": r[2], "has_vector": bool(r[3]), "memory_count": r[4]} for r in rows]
        return items, total

    def get_tag_graph_data(self, max_nodes=200):
        edge_rows = self.conn.execute("""
            SELECT a.tag_id, b.tag_id, COUNT(*) as cnt
            FROM memory_tags a JOIN memory_tags b ON a.memory_id = b.memory_id AND a.tag_id < b.tag_id
            GROUP BY a.tag_id, b.tag_id ORDER BY cnt DESC LIMIT 500
        """).fetchall()
        edges = [{"from": r[0], "to": r[1], "value": r[2]} for r in edge_rows]
        tag_ids_in_edges = set()
        for r in edge_rows:
            tag_ids_in_edges.add(r[0])
            tag_ids_in_edges.add(r[1])
        if tag_ids_in_edges:
            limited_ids = list(tag_ids_in_edges)[:max_nodes]
            placeholders = ",".join("?" * len(limited_ids))
            tag_rows = self.conn.execute(
                f"SELECT t.id, t.name, (SELECT COUNT(*) FROM memory_tags mt WHERE mt.tag_id = t.id) as mem_count FROM tags t WHERE t.id IN ({placeholders})",
                limited_ids,
            ).fetchall()
        else:
            tag_rows = []
        nodes = [{"id": r[0], "label": r[1], "value": r[2]} for r in tag_rows]
        return nodes, edges

    def get_senders_list(self):
        rows = self.conn.execute(
            """SELECT sender_id,
                    (SELECT sender_name FROM memories m2
                     WHERE m2.sender_id = m.sender_id AND m2.sender_name IS NOT NULL AND m2.sender_name != ''
                     ORDER BY m2.timestamp DESC LIMIT 1) as latest_name,
                    COUNT(*) as cnt
               FROM memories m
               WHERE sender_id IS NOT NULL AND sender_id != '' AND sender_id != 'bot_self'
               GROUP BY sender_id ORDER BY cnt DESC LIMIT 100"""
        ).fetchall()
        return [{"id": r[0], "name": r[1] or r[0], "count": r[2]} for r in rows]

    # ─── _sync_index_delete 委托 ───
    def _sync_index_delete(self, ids):
        self._cm._sync_index_delete(ids)

    # ─── FTS5 + Audit ───

    @staticmethod
    def _fts_normalize(text) -> str:
        """CJK 单字切分：每个汉字两侧加空格，使 unicode61 分词器按单字成 token。

        中文连续文本在 unicode61 下会变成一整个巨型 token，导致关键词永远
        匹配不到。索引与查询两侧都用本函数处理即可做短语精确匹配。
        """
        out = []
        for ch in str(text or ""):
            code = ord(ch)
            if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
                # 前后都加空格：保证与英文/数字相邻时也切断（如「玩galgame」）
                out.append(" ")
                out.append(ch)
                out.append(" ")
            else:
                out.append(ch)
        return "".join(out)

    def _setup_fts5(self):
        try:
            # 归一化函数注册到写连接上（触发器在写入连接上触发）
            self._cm.register_scalar_function("fts_norm", self._fts_normalize)

            # 迁移检测：旧版触发器不含 fts_norm → 删掉重建
            trig = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='fts_memories_ai'"
            ).fetchone()
            needs_trigger_rebuild = bool(trig) and "fts_norm" not in (trig[0] or "")
            if needs_trigger_rebuild:
                for tname in ("fts_memories_ai", "fts_memories_ad", "fts_memories_au"):
                    self.conn.execute(f"DROP TRIGGER IF EXISTS {tname}")

            self.conn.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_memories USING fts5(
                    content, sender_name, group_id,
                    content='memories', content_rowid='id', tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS fts_memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO fts_memories(rowid, content, sender_name, group_id)
                    VALUES (new.id, fts_norm(new.content), new.sender_name, new.group_id);
                END;
                CREATE TRIGGER IF NOT EXISTS fts_memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO fts_memories(fts_memories, rowid, content, sender_name, group_id)
                    VALUES ('delete', old.id, fts_norm(old.content), old.sender_name, old.group_id);
                END;
                CREATE TRIGGER IF NOT EXISTS fts_memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO fts_memories(fts_memories, rowid, content, sender_name, group_id)
                    VALUES ('delete', old.id, fts_norm(old.content), old.sender_name, old.group_id);
                    INSERT INTO fts_memories(rowid, content, sender_name, group_id)
                    VALUES (new.id, fts_norm(new.content), new.sender_name, new.group_id);
                END;
            """)

            # 存量重建：触发器升级后全量重灌为归一化文本
            if needs_trigger_rebuild:
                total = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                self.conn.execute("DELETE FROM fts_memories")
                self.conn.execute(
                    """INSERT INTO fts_memories(rowid, content, sender_name, group_id)
                       SELECT id, fts_norm(content), sender_name, group_id FROM memories"""
                )
                self.conn.commit()
                logger.info(f"[WaveMemory] FTS 索引已迁移为 CJK 单字分词并全量重建 ({total} 条)")
            # FTS5 初始填充
            fts_count = self.conn.execute("SELECT COUNT(*) FROM fts_memories").fetchone()[0]
            if fts_count == 0:
                mem_count = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                if mem_count > 0:
                    self.conn.execute("""
                        INSERT INTO fts_memories(rowid, content, sender_name, group_id)
                        SELECT id, content, sender_name, group_id FROM memories WHERE content IS NOT NULL
                    """)
                    logger.info(f"[WaveMemory] FTS5 initial fill: {mem_count} memories indexed")
            self.conn.commit()
        except Exception as e:
            logger.warning(f"[WaveMemory] FTS5 setup failed: {e}")

    def _setup_audit_table(self):
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tag_audit_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    tag_ids TEXT NOT NULL,
                    target_name TEXT,
                    target_type TEXT,
                    reason TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at REAL,
                    resolved_at REAL
                )
            """)
            self.conn.commit()
        except Exception:
            pass

    def _setup_jargon_knowledge_tables(self):
        """建立 Holyman 黑话知识库分层表，兼容旧版 jargon 记录。"""
        try:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS jargon_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT,
                    example TEXT NOT NULL,
                    category TEXT,
                    source TEXT,
                    source_path TEXT,
                    safe_for_prompt INTEGER DEFAULT 0,
                    created_at REAL,
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS jargon_concepts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    concept_id TEXT UNIQUE,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source TEXT,
                    tags TEXT DEFAULT '[]',
                    confidence REAL DEFAULT 0.0,
                    created_at REAL,
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS jargon_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT NOT NULL,
                    reason TEXT,
                    count INTEGER DEFAULT 1,
                    source TEXT,
                    status TEXT DEFAULT 'pending_review',
                    reject_reason TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at REAL,
                    updated_at REAL
                );

                CREATE TABLE IF NOT EXISTS jargon_blocklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT UNIQUE NOT NULL,
                    reason TEXT NOT NULL,
                    source TEXT,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS jargon_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT UNIQUE NOT NULL,
                    repo TEXT,
                    remote_version TEXT,
                    local_version TEXT,
                    content_hash TEXT,
                    asset_status TEXT DEFAULT 'unknown',
                    manifest_json TEXT,
                    quality_json TEXT,
                    created_at REAL,
                    updated_at REAL
                );
            """)
            self.conn.commit()
        except Exception as e:
            logger.debug(f"[WaveMemory] Jargon knowledge tables setup note: {e}")

    def _upsert_jargon_knowledge_row(self, table: str, unique_col: str, unique_value: str, values: dict[str, Any]):
        cols = list(values.keys())
        placeholders = ", ".join(["?"] * len(cols))
        update_assignments = ", ".join([f"{col}=excluded.{col}" for col in cols])
        params = [values[col] for col in cols]
        params.insert(0, unique_value)
        sql = f"""
            INSERT INTO {table} ({unique_col}, {', '.join(cols)})
            VALUES (?, {placeholders})
            ON CONFLICT({unique_col}) DO UPDATE SET {update_assignments}
        """
        self.conn.execute(sql, params)

    def upsert_jargon_knowledge_snapshot(self, source_key: str, payload: dict[str, Any]):
        now = time.time()
        values = {
            "repo": payload.get("repo"),
            "remote_version": payload.get("remote_version"),
            "local_version": payload.get("local_version"),
            "content_hash": payload.get("content_hash"),
            "asset_status": payload.get("asset_status") or "unknown",
            "manifest_json": json.dumps(payload.get("manifest") or {}, ensure_ascii=False),
            "quality_json": json.dumps(payload.get("quality_report") or {}, ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        }
        self._upsert_jargon_knowledge_row("jargon_sources", "source_key", source_key, values)
        self.conn.commit()

    def replace_jargon_knowledge_table(self, table: str, rows: list[dict[str, Any]], *, unique_col: str = "word"):
        self.conn.execute(f"DELETE FROM {table}")
        table_cols = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        now = time.time()
        for row in rows:
            payload = {key: value for key, value in dict(row).items() if key in table_cols}
            if "created_at" in table_cols:
                payload.setdefault("created_at", now)
            if "updated_at" in table_cols:
                payload["updated_at"] = now
            cols = list(payload.keys())
            placeholders = ", ".join(["?"] * len(cols))
            self.conn.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                [payload[col] for col in cols],
            )
        self.conn.commit()

    def _backfill_tag_relations_created_at(self):
        """一次性补全 tag_relations.created_at NULL 行 (v1.1.0 #4.2)。"""
        try:
            null_count = self.conn.execute(
                "SELECT COUNT(*) FROM tag_relations WHERE created_at IS NULL"
            ).fetchone()[0]
            if null_count == 0:
                return
            self.conn.execute("""
                UPDATE tag_relations SET created_at = (
                    SELECT MIN(m.timestamp) FROM memories m
                    JOIN memory_tags mt ON m.id = mt.memory_id
                    WHERE mt.tag_id = tag_relations.source_tag_id
                ) WHERE created_at IS NULL
            """)
            self.conn.commit()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════
    # Belief 委托
    # ═══════════════════════════════════════════════════════

    def add_belief(self, content, belief_type, bot_id, strength=0.5, sources=None, status="active", evidence_type="memory", evidence_ids=None):
        return self._belief_repo.add_belief(content, belief_type, bot_id, strength, sources, status, evidence_type, evidence_ids)

    def get_beliefs(self, bot_id=None, belief_type=None, status="active", limit=50):
        return self._belief_repo.get_beliefs(bot_id, belief_type, status, limit)

    def get_belief_by_id(self, belief_id):
        return self._belief_repo.get_belief_by_id(belief_id)

    def reinforce_belief(self, belief_id, amount=0.05):
        return self._belief_repo.reinforce(belief_id, amount)

    def weaken_belief(self, belief_id, amount=0.1):
        return self._belief_repo.weaken(belief_id, amount)

    def archive_belief(self, belief_id, reason=""):
        return self._belief_repo.archive(belief_id, reason)

    def add_belief_source(self, belief_id, memory_id):
        return self._belief_repo.add_source(belief_id, memory_id)

    def search_beliefs(self, keywords, bot_id=None, limit=5):
        return self._belief_repo.search_by_content(keywords, bot_id, limit)

    def belief_count(self, bot_id=None):
        return self._belief_repo.count(bot_id)
