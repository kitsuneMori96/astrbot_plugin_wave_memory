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
        quantize: bool = True,
        decay_class: str = "STATE",
    ) -> int:
        ts = timestamp or time.time()
        if vector is not None:
            if quantize:
                from ..vector_lifecycle import quantize_int8
                vec_blob = quantize_int8(vector)
            else:
                vec_blob = vector.astype(np.float32).tobytes()
        else:
            vec_blob = None
        cur = self.cm.execute_write(
            """INSERT INTO memories (group_id, sender_id, sender_name, content, vector, timestamp, importance, source, decay_class, retention_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 4)""",
            (group_id, sender_id, sender_name, content, vec_blob, ts, importance, source, decay_class),
        )
        self.cm.commit()
        return cur.lastrowid

    def get_retention_fields(self, memory_ids: list) -> list:
        """批量获取记忆衰减字段。返回 list of dicts。"""
        if not memory_ids:
            return []
        placeholders = ",".join("?" * len(memory_ids))
        rows = self.cm.execute_read(
            f"""SELECT id, source_msg_id, msg_score, stability_mult,
                       last_recall_at, decay_class, retention_state,
                       timestamp, LENGTH(content) AS content_len
                FROM memories WHERE id IN ({placeholders})""",
            memory_ids,
        ).fetchall()
        return [
            {"id": r[0], "source_msg_id": r[1], "msg_score": r[2],
             "stability_mult": r[3], "last_recall_at": r[4],
             "decay_class": r[5], "retention_state": r[6],
             "timestamp": r[7], "content_len": r[8]}
            for r in rows
        ]

    def get_memory_by_id(self, memory_id: int) -> Optional[dict]:
        row = self.cm.execute_read(
            "SELECT id, group_id, sender_id, sender_name, content, vector, timestamp, importance, access_count FROM memories WHERE id=?",
            (memory_id,),
        ).fetchone()
        if not row:
            return None
        from ..vector_lifecycle import decode_vector
        return {
            "id": row[0], "group_id": row[1], "sender_id": row[2],
            "sender_name": row[3], "content": row[4],
            "vector": decode_vector(row[5]) if row[5] else None,
            "timestamp": row[6], "importance": row[7], "access_count": row[8],
        }

    def get_all_memory_vectors(self, group_id: Optional[str] = None) -> list:
        from ..vector_lifecycle import decode_vector
        where = "vector IS NOT NULL AND memory_type = 'message' AND retention_state IN (3,4)"
        if group_id:
            rows = self.cm.execute_read(
                f"SELECT id, vector FROM memories WHERE group_id=? AND {where}", (group_id,)
            ).fetchall()
        else:
            rows = self.cm.execute_read(
                f"SELECT id, vector FROM memories WHERE {where}"
            ).fetchall()
        result = []
        for r in rows:
            vec = decode_vector(r[1])
            if vec is not None:
                result.append((r[0], vec))
        return result

    def get_memories_by_ids(self, ids: list) -> list:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.cm.execute_read(
            f"""SELECT id, group_id, sender_id, sender_name, content, timestamp, importance,
                       access_count, source, memory_type, source_msg_id, msg_score,
                       stability_mult, last_recall_at, decay_class, retention_state
                FROM memories WHERE id IN ({placeholders}) AND memory_type = 'message'""",
            ids,
        ).fetchall()
        return [
            {"id": r[0], "group_id": r[1], "sender_id": r[2], "sender_name": r[3],
             "content": r[4], "timestamp": r[5], "importance": r[6],
             "access_count": r[7] if len(r) > 7 else 0, "source": r[8], "memory_type": r[9],
             "source_msg_id": r[10], "msg_score": r[11], "stability_mult": r[12],
             "last_recall_at": r[13], "decay_class": r[14], "retention_state": r[15]}
            for r in rows
        ]

    def touch_memories(self, ids: list, importance_boost: float = 0.01, query_id: str = "", bot_id: str = ""):
        """标记记忆被访问 + 微量提升 importance + 写 recall_log（g=NULL 待回填）。"""
        now = time.time()
        for mid in ids:
            self.cm.execute_write(
                "UPDATE memories SET access_count = access_count + 1, last_accessed = ?, importance = MIN(3.0, importance + ?), last_decay_at = ? WHERE id = ?",
                (now, importance_boost, now, mid),
            )
        # 写 recall_log（g=NULL，apply_recall_boost 延迟回填）
        if query_id and ids:
            rows = [(query_id, "memory", mid, 0, None, 0, now, bot_id or None) for mid in ids]
            self.cm.executemany(
                "INSERT INTO recall_log (query_id, target_type, target_id, r_at_recall, g, content_len, ts, bot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        self.cm.commit()

    def get_pending_recall(self, max_age: float = 86400) -> dict:
        """获取待结算的 recall 记录。返回 {query_id: {bot_id, ts, group_id, ids}}。"""
        cutoff = time.time() - max_age
        rows = self.cm.execute_read(
            """SELECT r.query_id, r.bot_id, r.ts, r.target_id, m.group_id
               FROM recall_log r
               LEFT JOIN memories m ON m.id = r.target_id
               WHERE r.g IS NULL AND r.ts > ?
               ORDER BY r.ts""", (cutoff,),
        ).fetchall()
        pending = {}
        for qid, bot_id, ts, tid, gid in rows:
            if not gid:
                continue  # memory 已删除，跳过
            p = pending.setdefault(qid, {"bot_id": bot_id, "ts": ts, "group_id": gid, "ids": []})
            p["ids"].append(tid)
            p["ts"] = max(p["ts"], ts)
        return pending

    def get_bot_reply_after(self, group_id: str, ts: float, bot_id: str, upper_ts: float) -> Optional[str]:
        """查找 bot 在 (ts, upper_ts] 窗口内的最早回复。精确匹配 bot_id，无白名单兜底。"""
        row = self.cm.execute_read(
            """SELECT content FROM memories
               WHERE group_id=? AND sender_id=?
                 AND timestamp>? AND timestamp<=?
               ORDER BY timestamp LIMIT 1""",
            (group_id, bot_id, ts, upper_ts),
        ).fetchone()
        return row[0] if row else None

    def cleanup_stale_recall(self, max_age: float = 86400):
        """清理超时未结算的 recall_log 记录。"""
        cutoff = time.time() - max_age
        cur = self.cm.execute_write(
            "DELETE FROM recall_log WHERE g IS NULL AND ts < ?", (cutoff,),
        )
        self.cm.commit()
        return cur.rowcount

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
        from ..vector_lifecycle import decode_vector
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
            vec = decode_vector(row[1])
            if vec is not None and len(vec) > 0:
                result[row[0]] = vec
        # fallback: 从 memories.vector 列读
        missing = [mid for mid in memory_ids if mid not in result]
        if missing:
            ph2 = ",".join("?" * len(missing))
            rows2 = self.cm.execute_read(
                f"SELECT id, vector FROM memories WHERE id IN ({ph2}) AND vector IS NOT NULL",
                missing,
            ).fetchall()
            for row in rows2:
                vec = decode_vector(row[1])
                if vec is not None and len(vec) > 0:
                    result[row[0]] = vec
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
            "SELECT id FROM memories WHERE vector IS NULL "
            "AND source NOT IN ('noise', 'identity_quarantine') "
            "ORDER BY id DESC LIMIT ?",
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

    def apply_memory_decay(self, config: dict) -> dict:
        """对所有 activity='message' 的记忆执行连续重要性衰减。
        配置参数（_conf_schema.json Memory_Decay_Settings）：
          half_life_core, half_life_normal, half_life_fleeting, half_life_noise (天)
          archive_threshold, evict_threshold, review_boost_factor
        返回 {decayed, archived, evicted} 计数。
        """
        now = time.time()
        one_day_ago = now - 86400

        rows = self.cm.execute_read(
            "SELECT id, importance, access_count, timestamp, last_decay_at, memory_type "
            "FROM memories WHERE memory_type IN ('message', 'archived') AND last_decay_at < ?",
            (one_day_ago,),
        ).fetchall()

        hl_core = float(config.get("half_life_core_days", 90))
        hl_normal = float(config.get("half_life_normal_days", 30))
        hl_fleeting = float(config.get("half_life_fleeting_days", 3))
        hl_noise = float(config.get("half_life_noise_days", 1))
        archive_th = float(config.get("archive_threshold", 0.15))
        evict_th = float(config.get("evict_threshold", 0.05))
        review_factor = float(config.get("review_boost_factor", 0.3))

        decayed = archived = evicted = 0

        for mem_id, imp, acc_cnt, ts, last_decay, mtype in rows:
            try:
                imp = float(imp or 1.0)
                acc_cnt = int(acc_cnt or 0)
                last_decay = float(last_decay or 0)
                ts = float(ts or 0)

                days_since = (now - max(last_decay, ts)) / 86400
                if days_since < 1:
                    continue

                if imp >= 2.0:
                    base_hl = hl_core
                elif imp >= 1.0:
                    base_hl = hl_normal
                elif imp >= 0.3:
                    base_hl = hl_fleeting
                else:
                    base_hl = hl_noise

                effective_hl = base_hl * (1.0 + acc_cnt * review_factor)
                decay_factor = 0.5 ** (days_since / effective_hl)
                new_imp = max(0.01, imp * decay_factor)

                if new_imp < evict_th and (now - ts) > max(90 - imp * 100, 7) * 86400:
                    self.cm.execute_write(
                        "UPDATE memories SET importance = ?, memory_type = 'evicted', last_decay_at = ? WHERE id = ?",
                        (round(new_imp, 4), now, mem_id),
                    )
                    evicted += 1
                elif new_imp < archive_th and (now - ts) > max(30 - imp * 30, 3) * 86400:
                    self.cm.execute_write(
                        "UPDATE memories SET importance = ?, memory_type = 'archived', last_decay_at = ? WHERE id = ?",
                        (round(new_imp, 4), now, mem_id),
                    )
                    archived += 1
                else:
                    self.cm.execute_write(
                        "UPDATE memories SET importance = ?, last_decay_at = ? WHERE id = ?",
                        (round(new_imp, 4), now, mem_id),
                    )
                    decayed += 1
            except Exception:
                continue

        if decayed or archived or evicted:
            self.cm.commit()

        return {"decayed": decayed, "archived": archived, "evicted": evicted}

    def unarchive_memory(self, memory_id: int) -> bool:
        """将 archived/evicted 记忆恢复为 message，重置衰减时钟。"""
        now = time.time()
        row = self.cm.execute_read(
            "SELECT id, importance FROM memories WHERE id = ? AND memory_type IN ('archived', 'evicted')",
            (memory_id,),
        ).fetchone()
        if not row:
            return False
        new_imp = max(0.20, float(row[1] or 0))
        self.cm.execute_write(
            "UPDATE memories SET memory_type = 'message', importance = ?, last_decay_at = ? WHERE id = ?",
            (round(new_imp, 4), now, memory_id),
        )
        self.cm.commit()
        return True

    def apply_recall_boost(self, query_id: str, cited_memory_ids: list):
        """回填 recall_log.g + 更新 stability_mult。

        cited_memory_ids: 被回复引用的记忆 ID 列表（infer_cited 产出）。
        g=1.0(被引用) / g=0.3(未被引用)。
        """
        if not query_id:
            return
        now = time.time()
        G_CITED = 1.0
        G_NOT = 0.3

        rows = self.cm.execute_read(
            "SELECT id, target_id FROM recall_log WHERE query_id = ? AND g IS NULL",
            (query_id,),
        ).fetchall()
        if not rows:
            return

        cited_set = set(cited_memory_ids or [])
        α = 3.0

        for log_id, mem_id in rows:
            G = G_CITED if mem_id in cited_set else G_NOT
            self.cm.execute_write(
                "UPDATE recall_log SET g = ? WHERE id = ?",
                (G, log_id),
            )
            mem = self.cm.execute_read(
                "SELECT stability_mult, decay_class, msg_score, timestamp, last_recall_at FROM memories WHERE id = ?",
                (mem_id,),
            ).fetchone()
            if not mem:
                continue
            stab, decay_cls, msg_score, ts, last_recall = mem
            stab = float(stab or 1.0)
            msg_score = float(msg_score or 0.5)
            ts = float(ts or 0)
            last_recall = float(last_recall or 0)

            from ..retention import RetentionCalculator
            rc = RetentionCalculator()
            base_R = rc.calc_R({
                "decay_class": decay_cls,
                "msg_score": msg_score,
                "stability_mult": stab,
                "last_recall_at": last_recall,
                "timestamp": ts,
            })
            boost = 1 + α * (1 - base_R) * G
            new_stab = min(5.0, stab * boost)

            self.cm.execute_write(
                "UPDATE memories SET stability_mult = ?, last_recall_at = ? WHERE id = ?",
                (round(new_stab, 4), now, mem_id),
            )

        self.cm.commit()
