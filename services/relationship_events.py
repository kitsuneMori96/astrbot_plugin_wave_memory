"""Relationship event service — traceable affinity updates."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

try:  # 兼容插件包导入和仓库测试直接导入
    from ..domain.scope import RuntimeScope, ScopeValidationError
except ImportError:  # pragma: no cover - 由仓库测试直接导入 services 使用
    from domain.scope import RuntimeScope, ScopeValidationError


DIMENSION_WEIGHTS = {
    "familiarity": 0.25,
    "trust": 0.30,
    "fun": 0.20,
    "depth": 0.25,
}
HOSTILITY_WEIGHT = 0.5
DIM_RANGES = {
    "familiarity": (0, 100),
    "trust": (-50, 100),
    "fun": (0, 80),
    "hostility": (0, 100),
    "depth": (0, 80),
}
VALID_DIMENSIONS = set(DIM_RANGES)
VALID_EVENT_TYPES = {
    "message_seen",
    "direct_reply",
    "bot_praised",
    "bot_attacked",
    "correction",
    "gift_or_feed",
    "confession",
    "joke",
    "deep_talk",
    "ignored_boundary",
    "manual_adjustment",
}
DEFAULT_DIMS = {"familiarity": 0, "trust": 0, "fun": 0, "hostility": 0, "depth": 0}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_affection(dims: dict[str, float]) -> int:
    score = sum(float(dims.get(k, 0)) * w for k, w in DIMENSION_WEIGHTS.items())
    score -= float(dims.get("hostility", 0)) * HOSTILITY_WEIGHT
    return int(_clamp(score, -100, 100))


def get_attitude_level(affection: int) -> str:
    if affection >= 60:
        return "intimate"
    if affection >= 30:
        return "friendly"
    if affection >= 0:
        return "neutral"
    if affection >= -30:
        return "cold"
    return "hostile"


def _project_group_subject_scope(scope: RuntimeScope) -> tuple[str, str, str]:
    """Project an already-resolved group RuntimeScope into legacy relationship keys.

    This boundary never builds a Scope from bot/group/user strings.  Legacy callers
    may still supply those fields while their explicit projection adapters remain,
    but new callers must give the canonical Scope object.
    """
    if not isinstance(scope, RuntimeScope):
        raise ScopeValidationError("scope_required", "relationship event requires RuntimeScope")
    if scope.visibility != "group" or scope.session is None:
        raise ScopeValidationError(
            "scope_visibility_not_allowed",
            "relationship events currently require a group RuntimeScope",
        )
    principal = scope.subject_principal_id or ""
    prefix = f"{scope.session.platform_id}:user:"
    if not principal.startswith(prefix) or principal == prefix:
        raise ScopeValidationError(
            "scope_subject_required",
            "relationship event target must be a scoped platform user",
        )
    return scope.bot_id, scope.session.conversation_id, principal[len(prefix):]


@dataclass
class RelationshipEventResult:
    event_id: int
    bot_id: str
    group_id: str
    user_id: str
    dimension: str
    requested_delta: float
    applied_delta: float
    before_affection: int
    after_affection: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "bot_id": self.bot_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "dimension": self.dimension,
            "requested_delta": self.requested_delta,
            "applied_delta": self.applied_delta,
            "before_affection": self.before_affection,
            "after_affection": self.after_affection,
            "reason": self.reason,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


class RelationshipEventService:
    """Records relationship events and keeps user_profiles current state in sync."""

    def __init__(
        self,
        conn,
        single_delta_cap: float = 5,
        daily_delta_cap: float = 15,
        hostility_delta_cap: float = 8,
        target_profiles: dict[str, dict[str, str]] | None = None,
    ):
        self.conn = conn
        self.single_delta_cap = abs(float(single_delta_cap))
        self.daily_delta_cap = abs(float(daily_delta_cap))
        self.hostility_delta_cap = abs(float(hostility_delta_cap))
        self.target_profiles = target_profiles or {}

    def record_event(
        self,
        *,
        bot_id: str | None = None,
        group_id: str | None = None,
        user_id: str | None = None,
        scope: RuntimeScope | None = None,
        event_type: str,
        dimension: str,
        delta: float,
        reason: str,
        source_episode_id: int | None = None,
        source_memory_id: int | None = None,
        created_at: float | None = None,
    ) -> RelationshipEventResult:
        legacy_bot_id = (bot_id or "").strip()
        legacy_group_id = (group_id or "").strip()
        legacy_user_id = (user_id or "").strip()
        if scope is not None:
            scoped_bot_id, scoped_group_id, scoped_user_id = _project_group_subject_scope(scope)
            supplied = (legacy_bot_id, legacy_group_id, legacy_user_id)
            projected = (scoped_bot_id, scoped_group_id, scoped_user_id)
            if any(value for value in supplied) and supplied != projected:
                raise ScopeValidationError(
                    "scope_legacy_mismatch",
                    "relationship legacy keys must match the supplied RuntimeScope",
                )
            bot_id, group_id, user_id = projected
        else:
            bot_id, group_id, user_id = legacy_bot_id, legacy_group_id, legacy_user_id
        event_type = (event_type or "").strip()
        dimension = (dimension or "").strip()
        reason = (reason or "").strip()
        if not bot_id or not group_id or not user_id:
            raise ValueError("bot_id, group_id and user_id are required")
        if dimension not in VALID_DIMENSIONS:
            raise ValueError(f"invalid relationship dimension: {dimension}")
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"invalid relationship event_type: {event_type}")
        if not reason:
            raise ValueError("relationship event reason is required")

        now = float(created_at or time.time())
        requested_delta = float(delta)
        applied_delta = self._constrain_delta(
            bot_id=bot_id,
            group_id=group_id,
            user_id=user_id,
            dimension=dimension,
            event_type=event_type,
            delta=requested_delta,
            now=now,
        )

        dims, before_affection, existing_meta = self._load_dimensions(user_id, group_id, bot_id)
        dims[dimension] = dims.get(dimension, 0) + applied_delta
        lo, hi = DIM_RANGES[dimension]
        dims[dimension] = _clamp(dims[dimension], lo, hi)
        after_affection = compute_affection(dims)

        cur = self.conn.execute(
            """INSERT INTO relationship_events
               (bot_id, group_id, user_id, event_type, dimension, delta, reason,
                source_episode_id, source_memory_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bot_id,
                group_id,
                user_id,
                event_type,
                dimension,
                applied_delta,
                reason,
                source_episode_id,
                source_memory_id,
                now,
            ),
        )
        event_id = int(getattr(cur, "lastrowid", 0) or 0)

        existing_meta["dimensions"] = {k: round(float(v), 2) for k, v in dims.items()}
        existing_meta["attitude_level"] = get_attitude_level(after_affection)
        existing_meta["last_relationship_event_at"] = now
        existing_meta["last_relationship_event_reason"] = reason
        target_profile = self.target_profiles.get(user_id)
        if target_profile:
            existing_meta["target_type"] = "bot"
            existing_meta["target_bot_id"] = target_profile.get("db_id") or user_id
            existing_meta["target_name"] = target_profile.get("name") or target_profile.get("db_id") or user_id
        else:
            existing_meta.setdefault("target_type", "user")
        meta_str = json.dumps(existing_meta, ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO user_profiles
               (user_id, group_id, nickname, affection, interaction_count, first_seen, last_seen,
                personality_tags, notes, metadata, bot_id)
               VALUES (?, ?, '', ?, 0, ?, ?, '', '', ?, ?)
               ON CONFLICT(user_id, group_id, bot_id) DO UPDATE SET
                 affection = excluded.affection,
                 last_seen = excluded.last_seen,
                 metadata = excluded.metadata""",
            (user_id, group_id, after_affection, now, now, meta_str, bot_id),
        )
        self.conn.commit()

        return RelationshipEventResult(
            event_id=event_id,
            bot_id=bot_id,
            group_id=group_id,
            user_id=user_id,
            dimension=dimension,
            requested_delta=requested_delta,
            applied_delta=applied_delta,
            before_affection=before_affection,
            after_affection=after_affection,
            reason=reason,
        )

    def recent_events(self, bot_id: str, user_id: str, group_id: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT id, event_type, dimension, delta, reason, created_at
               FROM relationship_events
               WHERE bot_id=? AND user_id=? AND group_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (bot_id, user_id, group_id, int(limit)),
        ).fetchall()
        return [
            {
                "id": r[0],
                "event_type": r[1],
                "dimension": r[2],
                "delta": r[3],
                "reason": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    def _constrain_delta(
        self,
        *,
        bot_id: str,
        group_id: str,
        user_id: str,
        dimension: str,
        event_type: str,
        delta: float,
        now: float,
    ) -> float:
        cap = self.hostility_delta_cap if dimension == "hostility" and delta > 0 else self.single_delta_cap
        delta = _clamp(delta, -cap, cap)
        day_start = now - 86400
        row = self.conn.execute(
            """SELECT COALESCE(SUM(delta), 0) FROM relationship_events
               WHERE bot_id=? AND group_id=? AND user_id=? AND dimension=? AND created_at >= ?""",
            (bot_id, group_id, user_id, dimension, day_start),
        ).fetchone()
        daily_total = float(row[0] or 0) if row else 0.0
        if delta > 0:
            remaining = self.daily_delta_cap - max(daily_total, 0)
            delta = min(delta, max(0.0, remaining))
        elif delta < 0:
            remaining = self.daily_delta_cap - max(-daily_total, 0)
            delta = max(delta, -max(0.0, remaining))
        return round(delta, 2)

    def _load_dimensions(self, user_id: str, group_id: str, bot_id: str) -> tuple[dict[str, float], int, dict[str, Any]]:
        row = self.conn.execute(
            "SELECT affection, metadata FROM user_profiles WHERE user_id=? AND group_id=? AND bot_id=?",
            (user_id, group_id, bot_id),
        ).fetchone()
        meta: dict[str, Any] = {}
        before_affection = 0
        if row:
            before_affection = int(row[0] or 0)
            if row[1]:
                try:
                    meta = json.loads(row[1])
                except Exception:
                    meta = {"legacy_metadata_raw": row[1]}
        dims_raw = meta.get("dimensions") or {}
        dims = {k: float(dims_raw.get(k, DEFAULT_DIMS[k])) for k in DEFAULT_DIMS}
        return dims, before_affection, meta
