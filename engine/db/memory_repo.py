"""MemoryRepo — memories + memory_tags + memory_vectors 表操作"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import time
from typing import Any, Optional

import numpy as np

try:
    from ...domain.scope import RuntimeScope
except ImportError:  # pragma: no cover - repository tests import engine as top-level
    from domain.scope import RuntimeScope

from .connection import ConnectionManager
from .migrations.memories_v2 import MEMORIES_V2_VERSION


class MemoryScopeError(ValueError):
    """拒绝把新记忆写入未解析或非群聊 Scope 的稳定错误。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.reason_code = code
        super().__init__(message or code)


def _require_group_scope(scope: RuntimeScope | None, group_id: str) -> RuntimeScope:
    if not isinstance(scope, RuntimeScope):
        raise MemoryScopeError("scope_required", "group RuntimeScope is required for new memory writes")
    if scope.visibility != "group" or scope.session is None or scope.session.kind != "group":
        raise MemoryScopeError(
            "memory_scope_visibility_unsupported",
            "memory writes only accept group RuntimeScope values",
        )
    if not isinstance(group_id, str) or group_id != scope.session.conversation_id:
        raise MemoryScopeError(
            "scope_session_mismatch",
            "group_id must equal the RuntimeScope canonical group id",
        )
    return scope


def _require_mapping(value: Mapping[str, Any] | None, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping when provided")
    return dict(value)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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

    def _memories_columns(self) -> set[str]:
        return {
            row[1]
            for row in self.cm.execute_read("PRAGMA table_info(memories)").fetchall()
        }

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
        *,
        scope: RuntimeScope | None = None,
        provenance: Mapping[str, Any] | None = None,
        origin_metadata: Mapping[str, Any] | None = None,
        quarantine: bool = False,
    ) -> int:
        """写入一条 v2 记忆；新写入必须携带已解析的群聊 Scope。

        ``group_id`` 保留为 legacy 外形，但仅作为对 RuntimeScope canonical
        conversation ID 的断言，不能再独立决定新记录的归属。
        """
        scope = _require_group_scope(scope, group_id)
        metadata = _require_mapping(provenance, "provenance")
        origin = _require_mapping(origin_metadata, "origin_metadata")
        ts = timestamp or time.time()
        vec_blob = vector.astype(np.float32).tobytes() if vector is not None else None
        scope_payload = {
            "bot_id": scope.bot_id,
            "session_id": scope.session.id,
            "visibility": scope.visibility,
            "group_id": scope.session.conversation_id,
        }
        origin_payload = {
            "kind": "wave_memory_origin",
            "version": MEMORIES_V2_VERSION,
            "scope": scope_payload,
            "content": content,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "timestamp": ts,
            "source": source,
            "metadata": origin,
        }
        origin_fingerprint = hashlib.sha256(
            _canonical_json(origin_payload).encode("utf-8")
        ).hexdigest()
        provenance_payload = {
            "kind": "wave_memory_provenance",
            "version": MEMORIES_V2_VERSION,
            "fingerprint_algorithm": "sha256",
            "origin_fingerprint": origin_fingerprint,
            "scope": scope_payload,
            "metadata": metadata,
        }
        cur = self.cm.execute_write(
            """INSERT INTO memories (
                    group_id, sender_id, sender_name, content, vector, timestamp, importance, source,
                    bot_id, session_id, visibility, origin_fingerprint, provenance, version,
                    quarantine, resolution_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                group_id, sender_id, sender_name, content, vec_blob, ts, importance, source,
                scope.bot_id, scope.session.id, scope.visibility, origin_fingerprint,
                _canonical_json(provenance_payload), MEMORIES_V2_VERSION, int(bool(quarantine)),
                "resolved",
            ),
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
        quarantine_filter = (
            " AND COALESCE(quarantine, 0)=0"
            if "quarantine" in self._memories_columns()
            else ""
        )
        if group_id:
            rows = self.cm.execute_read(
                "SELECT id, vector FROM memories WHERE group_id=? AND vector IS NOT NULL "
                "AND memory_type = 'message'" + quarantine_filter,
                (group_id,),
            ).fetchall()
        else:
            rows = self.cm.execute_read(
                "SELECT id, vector FROM memories WHERE vector IS NOT NULL "
                "AND memory_type = 'message'" + quarantine_filter
            ).fetchall()
        return [(r[0], np.frombuffer(r[1], dtype=np.float32)) for r in rows]

    def get_memories_by_ids(
        self,
        ids: list,
        *,
        scope: RuntimeScope | None = None,
    ) -> list:
        """获取 HNSW 候选；带 Scope 时严格后过滤，绝不接受跨 Scope 命中。"""
        if not ids:
            return []
        if not isinstance(scope, RuntimeScope):
            # This method is the vector/ID recall boundary.  A legacy bare-ID read
            # cannot prove a Bot/session, so it must not return automatic recall data.
            return []
        scope = _require_group_scope(scope, scope.session.conversation_id if scope.session else "")

        columns = self._memories_columns()
        placeholders = ",".join("?" * len(ids))
        required_v2 = {"bot_id", "session_id", "visibility", "resolution_state", "quarantine"}
        missing_v2 = required_v2 - columns
        if missing_v2:
            raise RuntimeError(f"memories v2 schema is missing columns: {', '.join(sorted(missing_v2))}")
        # HNSW ID 命中后的最终授权边界：在 SQL 基表层过滤，legacy NULL 和
        # 任一 Scope 字段不精确的记录都不会离开 repository。
        rows = self.cm.execute_read(
            f"""SELECT id, group_id, sender_id, sender_name, content, timestamp, importance,
                       access_count, source, memory_type, bot_id, session_id, visibility,
                       resolution_state, quarantine
                FROM memories
               WHERE id IN ({placeholders}) AND memory_type = 'message'
                 AND group_id=? AND bot_id=? AND session_id=? AND visibility=?
                 AND resolution_state='resolved' AND quarantine=0""",
            [
                *ids,
                scope.session.conversation_id,
                scope.bot_id,
                scope.session.id,
                scope.visibility,
            ],
        ).fetchall()
        return [
            {
                "id": r[0], "group_id": r[1], "sender_id": r[2], "sender_name": r[3],
                "content": r[4], "timestamp": r[5], "importance": r[6],
                "access_count": r[7], "source": r[8], "memory_type": r[9],
                "bot_id": r[10], "session_id": r[11], "visibility": r[12],
                "resolution_state": r[13], "quarantine": r[14],
            }
            for r in rows
        ]

    def find_recent_duplicate_memory(
        self,
        *,
        scope: RuntimeScope,
        normalized_content: str,
        since_ts: float,
    ) -> int | None:
        """仅在完整 v2 Scope 内查询近期重复记忆，legacy NULL 永不命中。"""
        if not isinstance(scope, RuntimeScope):
            raise MemoryScopeError(
                "scope_required",
                "group RuntimeScope is required for scoped duplicate lookup",
            )
        scope = _require_group_scope(scope, scope.session.conversation_id if scope.session else "")
        if not isinstance(normalized_content, str) or not normalized_content:
            return None
        required_v2 = {"bot_id", "session_id", "visibility", "resolution_state", "quarantine"}
        if required_v2 - self._memories_columns():
            return None
        row = self.cm.execute_read(
            """SELECT id FROM memories
               WHERE group_id=?
                 AND bot_id=?
                 AND session_id=?
                 AND visibility=?
                 AND resolution_state='resolved'
                 AND COALESCE(quarantine, 0)=0
                 AND content=?
                 AND timestamp>=?
                 AND memory_type='message'
               ORDER BY timestamp DESC, id DESC
               LIMIT 1""",
            (
                scope.session.conversation_id,
                scope.bot_id,
                scope.session.id,
                scope.visibility,
                normalized_content,
                since_ts,
            ),
        ).fetchone()
        return int(row[0]) if row else None

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
