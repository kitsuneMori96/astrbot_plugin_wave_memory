"""Legacy relationship-event belief emergence quarantine boundary."""

from __future__ import annotations

from typing import Any

from astrbot.api import logger


class BeliefEmergenceService:
    """Refuse unscoped legacy relationship-event promotion.

    Relationship events currently retain bot_id/group_id/user_id but not a
    canonical RuntimeScope.  They may remain auditable, but cannot create a
    formal belief until the scoped candidate pipeline is implemented.
    """

    def __init__(self, db: Any, llm_client: Any = None, bot_id: str = ""):
        self.db = db
        self.llm = llm_client
        self.bot_id = bot_id
        self.legacy_scope_skip_total = 0

    async def emerge_recent(self, days: int = 14, limit: int = 3) -> list[dict]:
        """Fail closed instead of minting a legacy belief without Scope evidence."""
        self.legacy_scope_skip_total += 1
        if self.legacy_scope_skip_total == 1:
            logger.warning(
                "[BeliefEmergence] skipped: relationship-event rows lack canonical RuntimeScope"
            )
        return []
