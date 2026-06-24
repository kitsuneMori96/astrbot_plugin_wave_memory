"""Belief emergence from experience and relationship evidence."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from astrbot.api import logger

from .identity_safety import is_identity_contamination


class BeliefEmergenceService:
    """Conservative evidence-backed belief candidate generator."""

    def __init__(self, db: Any, llm_client: Any = None, bot_id: str = ""):
        self.db = db
        self.llm = llm_client
        self.bot_id = bot_id

    async def emerge_recent(self, days: int = 14, limit: int = 3) -> list[dict]:
        """Create pending beliefs from repeated relationship/experience evidence.

        First pass is rule-based and conservative; LLM refinement can be added later.
        """
        since = time.time() - days * 86400
        high_quality_types = ("bot_praised", "bot_attacked", "correction", "gift_or_feed", "confession", "joke", "deep_talk", "ignored_boundary", "manual_adjustment")
        placeholders = ",".join(["?"] * len(high_quality_types))
        rows = self.db.conn.execute(
            f"""SELECT id, user_id, group_id, event_type, dimension, delta, reason, created_at
               FROM relationship_events
               WHERE bot_id=? AND created_at>=? AND event_type IN ({placeholders})
               ORDER BY created_at DESC LIMIT 300""",
            (self.bot_id, since, *high_quality_types),
        ).fetchall()
        grouped: dict[tuple[str, str], list] = defaultdict(list)
        for r in rows:
            if is_identity_contamination(r[6] or ""):
                continue
            grouped[(r[1], r[4])].append(r)

        created = []
        for (user_id, dimension), events in grouped.items():
            if len(created) >= limit:
                break
            total = sum(float(e[5] or 0) for e in events)
            if len(events) < 3 and abs(total) < 8:
                continue
            content = self._make_belief(user_id, dimension, total, len(events))
            if not content or self._duplicate(content):
                continue
            source_ids = [int(e[0]) for e in events[:10]]
            belief_id = self.db.add_belief(
                content=content,
                belief_type="person_judgment",
                bot_id=self.bot_id,
                strength=min(0.3 + min(abs(total), 20) / 100, 0.55),
                sources=source_ids,
                status="pending",
            )
            created.append({"id": belief_id, "content": content, "evidence_event_ids": source_ids})
            logger.info("[BeliefEmergence] Pending belief: %s", content[:80])
        return created

    def _make_belief(self, user_id: str, dimension: str, total: float, count: int) -> str:
        if dimension == "trust" and total > 0:
            return f"我逐渐更信任 {user_id}，因为多次互动让信任维度上升。"
        if dimension == "hostility" and total > 0:
            return f"我对 {user_id} 有一些防备，因为多次互动增加了敌意。"
        if dimension == "fun" and total > 0:
            return f"我觉得和 {user_id} 互动有趣，因为多次玩笑或投喂带来正向趣味。"
        if dimension == "depth" and total > 0:
            return f"我觉得 {user_id} 愿意和我深入交流，因为相关互动反复增加深度。"
        if total < 0:
            return f"我对 {user_id} 的 {dimension} 感受有所下降，这是多次关系事件累积的结果。"
        return ""

    def _duplicate(self, content: str) -> bool:
        rows = self.db.get_beliefs(bot_id=self.bot_id, status="pending", limit=50)
        rows += self.db.get_beliefs(bot_id=self.bot_id, status="active", limit=50)
        chars = set(content)
        for b in rows:
            other = set(b.get("content", ""))
            if chars and len(chars & other) / max(len(chars | other), 1) > 0.65:
                return True
        return False
