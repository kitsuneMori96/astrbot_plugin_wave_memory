"""Experience episode service — records bot-lived events."""

from __future__ import annotations

import json
import time
from typing import Any

from .identity_safety import quarantine_episode_kwargs, is_identity_contamination


class ExperienceEpisodeService:
    """Small DB helper for experience_episodes."""

    def __init__(self, conn):
        self.conn = conn

    def record_episode(
        self,
        *,
        bot_id: str,
        group_id: str,
        episode_type: str,
        user_id: str | None = None,
        trigger_text: str | None = None,
        bot_inner_thought: str | None = None,
        bot_action: str | None = None,
        bot_reply: str | None = None,
        user_reaction: str | None = None,
        outcome: str | None = None,
        source_memory_ids: list[int] | None = None,
        emotional_weight: float = 0,
        created_at: float | None = None,
    ) -> int:
        bot_id = (bot_id or "").strip()
        group_id = (group_id or "").strip()
        episode_type = (episode_type or "").strip()
        if not bot_id or not group_id or not episode_type:
            raise ValueError("bot_id, group_id and episode_type are required")

        payload = quarantine_episode_kwargs({
            "trigger_text": trigger_text,
            "bot_inner_thought": bot_inner_thought,
            "bot_action": bot_action,
            "bot_reply": bot_reply,
            "user_reaction": user_reaction,
            "outcome": outcome,
            "emotional_weight": emotional_weight,
        })
        trigger_text = payload.get("trigger_text")
        bot_inner_thought = payload.get("bot_inner_thought")
        bot_action = payload.get("bot_action")
        bot_reply = payload.get("bot_reply")
        user_reaction = payload.get("user_reaction")
        outcome = payload.get("outcome")
        emotional_weight = payload.get("emotional_weight")
        now = float(created_at or time.time())
        cur = self.conn.execute(
            """INSERT INTO experience_episodes
               (bot_id, group_id, user_id, episode_type, trigger_text, bot_inner_thought,
                bot_action, bot_reply, user_reaction, outcome, source_memory_ids,
                emotional_weight, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bot_id,
                group_id,
                user_id,
                episode_type,
                trigger_text,
                bot_inner_thought,
                bot_action,
                bot_reply,
                user_reaction,
                outcome,
                json.dumps(source_memory_ids or [], ensure_ascii=False),
                float(emotional_weight or 0),
                now,
            ),
        )
        self.conn.commit()
        return int(getattr(cur, "lastrowid", 0) or 0)

    def recent_episodes(
        self,
        *,
        bot_id: str,
        user_id: str | None = None,
        group_id: str | None = None,
        limit: int = 20,
        since: float | None = None,
    ) -> list[dict[str, Any]]:
        conditions = ["bot_id = ?"]
        params: list[Any] = [bot_id]
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        if since is not None:
            conditions.append("created_at >= ?")
            params.append(float(since))
        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"""SELECT id, bot_id, group_id, user_id, episode_type, trigger_text,
                       bot_inner_thought, bot_action, bot_reply, user_reaction, outcome,
                       source_memory_ids, emotional_weight, created_at
                FROM experience_episodes
                WHERE {where}
                ORDER BY created_at DESC LIMIT ?""",
            params + [int(limit)],
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def last_bot_reply(self, *, bot_id: str, group_id: str, user_id: str | None = None) -> str:
        """Return the latest reply from this specific bot in this group.

        Prefer a reply associated with the current user; fall back to the bot's
        latest group reply. This avoids reading memories.sender_id='bot', which
        mixes replies from different bot identities.
        """
        bot_id = (bot_id or "").strip()
        group_id = (group_id or "").strip()
        user_id = (user_id or "").strip() if user_id is not None else ""
        if not bot_id or not group_id:
            return ""

        if user_id:
            row = self.conn.execute(
                """SELECT bot_reply FROM experience_episodes
                   WHERE bot_id=? AND group_id=? AND user_id=?
                     AND episode_type='bot_reply' AND COALESCE(bot_reply, '') != ''
                     AND COALESCE(outcome, '') != 'quarantined_roleplay'
                   ORDER BY created_at DESC LIMIT 1""",
                (bot_id, group_id, user_id),
            ).fetchone()
            if row and row[0] and not is_identity_contamination(row[0]):
                return row[0]

        row = self.conn.execute(
            """SELECT bot_reply FROM experience_episodes
               WHERE bot_id=? AND group_id=?
                 AND episode_type='bot_reply' AND COALESCE(bot_reply, '') != ''
                 AND COALESCE(outcome, '') != 'quarantined_roleplay'
               ORDER BY created_at DESC LIMIT 1""",
            (bot_id, group_id),
        ).fetchone()
        return row[0] if row and row[0] and not is_identity_contamination(row[0]) else ""

    def _row_to_dict(self, row) -> dict[str, Any]:
        try:
            source_ids = json.loads(row[11] or "[]")
        except Exception:
            source_ids = []
        return {
            "id": row[0],
            "bot_id": row[1],
            "group_id": row[2],
            "user_id": row[3],
            "episode_type": row[4],
            "trigger_text": row[5],
            "bot_inner_thought": row[6],
            "bot_action": row[7],
            "bot_reply": row[8],
            "user_reaction": row[9],
            "outcome": row[10],
            "source_memory_ids": source_ids,
            "emotional_weight": row[12],
            "created_at": row[13],
        }
