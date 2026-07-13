"""书中经历领域仓储；不读取或写入互动 experience_episodes。"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping

from .migrations.book_experience import ensure_book_experience_schema


class BookExperienceEpisodeRepository:
    """独立书中经历表的最小幂等仓储。"""

    def __init__(self, connection, *, now: Callable[[], float] | None = None):
        self.connection = connection
        self.now = now or time.time
        ensure_book_experience_schema(connection)

    def create(
        self,
        *,
        bot_id: str,
        group_id: str,
        user_id: str | None,
        content: str,
        evidence: Mapping[str, Any],
        idempotency_key: str,
        source_candidate_id: int | None = None,
    ) -> int:
        bot_id = str(bot_id or "").strip()
        group_id = str(group_id or "").strip()
        content = str(content or "").strip()
        key = str(idempotency_key or "").strip()
        if not bot_id or not group_id or not content or not key:
            raise ValueError("bot_id, group_id, content and idempotency_key are required")
        now = float(self.now())
        evidence_json = json.dumps(dict(evidence or {}), ensure_ascii=False, sort_keys=True)
        transaction_factory = getattr(self.connection, "write_transaction", None)
        if callable(transaction_factory):
            context = transaction_factory()
        else:
            context = None
        if context is None:
            # 原生 sqlite 连接和测试代理都支持 INSERT OR IGNORE；先执行后读取
            # 避免依赖 lastrowid 在重复写入时的实现差异。
            cur = self.connection.execute(
                """INSERT OR IGNORE INTO book_experience_episodes
                   (bot_id, group_id, user_id, content, evidence_json, source_candidate_id,
                    idempotency_key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (bot_id, group_id, user_id, content, evidence_json, source_candidate_id, key, now, now),
            )
            self.connection.commit()
        else:
            with context as tx:
                tx.execute(
                    """INSERT OR IGNORE INTO book_experience_episodes
                       (bot_id, group_id, user_id, content, evidence_json, source_candidate_id,
                        idempotency_key, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (bot_id, group_id, user_id, content, evidence_json, source_candidate_id, key, now, now),
                )
        row = self.connection.execute(
            "SELECT id FROM book_experience_episodes WHERE bot_id=? AND idempotency_key=?",
            (bot_id, key),
        ).fetchone()
        if not row:
            raise RuntimeError("book experience episode was not persisted")
        return int(row[0])

    def get(self, episode_id: int, *, bot_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT id, bot_id, group_id, user_id, content, evidence_json,
                      source_candidate_id, idempotency_key, created_at, updated_at
               FROM book_experience_episodes WHERE id=? AND bot_id=?""",
            (int(episode_id), str(bot_id or "").strip()),
        ).fetchone()
        if not row:
            return None
        try:
            evidence = json.loads(row[5] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = {}
        if not isinstance(evidence, dict):
            evidence = {}
        return {
            "id": row[0],
            "bot_id": row[1],
            "group_id": row[2],
            "user_id": row[3],
            "content": row[4],
            "evidence": evidence,
            "source_candidate_id": row[6],
            "idempotency_key": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    def list_for_scope(
        self, *, bot_id: str, group_id: str | None = None, user_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        conditions = ["bot_id=?"]
        params: list[Any] = [str(bot_id or "").strip()]
        if group_id is not None:
            conditions.append("group_id=?")
            params.append(str(group_id))
        if user_id is not None:
            conditions.append("user_id=?")
            params.append(str(user_id))
        params.append(int(limit))
        rows = self.connection.execute(
            """SELECT id FROM book_experience_episodes
               WHERE """ + " AND ".join(conditions) + " ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [item for row in rows if (item := self.get(int(row[0]), bot_id=bot_id))]


__all__ = ["BookExperienceEpisodeRepository"]
