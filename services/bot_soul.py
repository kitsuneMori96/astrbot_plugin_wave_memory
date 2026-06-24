"""Per-bot soul runtime registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BotSoulRuntime:
    """Container for one configured bot's isolated soul state."""

    profile: Any
    lifecycle: Any = None
    belief_engine: Any = None
    concern_tracker: Any = None
    mood_trajectory: Any = None
    subjective_time: Any = None
    desire_engine: Any = None

    @property
    def qq_id(self) -> str:
        return getattr(self.profile, "qq_id", "") or ""

    @property
    def db_id(self) -> str:
        return getattr(self.profile, "db_id", "") or ""

    @property
    def name(self) -> str:
        return getattr(self.profile, "name", "") or ""


class BotSoulRegistry:
    """Lookup helper with explicit fallback to the first configured bot."""

    def __init__(self, runtimes: list[BotSoulRuntime] | None = None):
        self._runtimes = list(runtimes or [])
        self.by_qq_id = {r.qq_id: r for r in self._runtimes if r.qq_id}
        self.by_db = {r.db_id: r for r in self._runtimes if r.db_id}
        self.default = self._runtimes[0] if self._runtimes else None

    def by_qq(self, qq_id: str) -> BotSoulRuntime | None:
        """Return an exact configured bot runtime; unknown IDs must not inherit the default soul."""
        return self.by_qq_id.get(qq_id or "")

    def by_db_id(self, db_id: str) -> BotSoulRuntime | None:
        """Return an exact configured bot runtime by db_id; unknown IDs must not inherit the default soul."""
        return self.by_db.get(db_id or "")

    def values(self) -> list[BotSoulRuntime]:
        return list(self._runtimes)

    def db_ids(self) -> list[str]:
        return [r.db_id for r in self._runtimes if r.db_id]
