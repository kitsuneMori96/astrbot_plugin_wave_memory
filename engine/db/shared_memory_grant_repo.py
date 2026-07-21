"""Shared-memory grant repository — read authorization without physical fanout.

Hard rules:
- grant_mode is always ``read``
- never copies memories rows into consumer Scope
- revoke is soft (status=revoked); no hard delete of grant history by default
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Mapping, Optional

from .connection import ConnectionManager


def _scope_tuple(scope: Mapping[str, Any]) -> tuple[str, str, str, str]:
    bot_id = str(scope.get("bot_id") or "").strip()
    session_id = str(scope.get("session_id") or "").strip()
    visibility = str(scope.get("visibility") or "group").strip() or "group"
    group_id = str(scope.get("group_id") or "").strip()
    if visibility != "group":
        raise ValueError("shared_memory_grants only support visibility=group")
    if not bot_id or not session_id or not group_id:
        raise ValueError("owner/consumer scope requires bot_id, session_id, group_id")
    return bot_id, session_id, visibility, group_id


class SharedMemoryGrantRepository:
    def __init__(self, cm: ConnectionManager):
        if not isinstance(cm, ConnectionManager):
            raise TypeError("cm must be a ConnectionManager")
        self._cm = cm

    def grant_read(
        self,
        *,
        owner_scope: Mapping[str, Any],
        consumer_scope: Mapping[str, Any],
        memory_id: int,
        reason: str = "",
        actor: str = "system",
        provenance: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        """Idempotently create an active read grant. Never writes memories rows."""
        mid = int(memory_id)
        if mid <= 0:
            raise ValueError("memory_id must be positive")
        o_bot, o_sess, o_vis, o_gid = _scope_tuple(owner_scope)
        c_bot, c_sess, c_vis, c_gid = _scope_tuple(consumer_scope)
        if (o_bot, o_sess, o_vis) == (c_bot, c_sess, c_vis):
            raise ValueError("consumer_scope must differ from owner_scope")
        ts = float(now if now is not None else time.time())
        prov = json.dumps(dict(provenance or {}), ensure_ascii=False)
        grant_id = f"smg_{uuid.uuid4().hex}"

        with self._cm.write_transaction() as tx:
            existing = tx.execute(
                """
                SELECT grant_id, status FROM shared_memory_grants
                 WHERE owner_bot_id=? AND owner_session_id=? AND owner_visibility=?
                   AND memory_id=?
                   AND consumer_bot_id=? AND consumer_session_id=? AND consumer_visibility=?
                   AND grant_mode='read'
                """,
                (o_bot, o_sess, o_vis, mid, c_bot, c_sess, c_vis),
            ).fetchone()
            if existing:
                gid, status = str(existing[0]), str(existing[1])
                if status == "active":
                    return {
                        "grant_id": gid,
                        "created": False,
                        "reactivated": False,
                        "status": "active",
                        "memory_id": mid,
                    }
                tx.execute(
                    """
                    UPDATE shared_memory_grants
                       SET status='active',
                           revoked_at=NULL,
                           reason=?,
                           actor=?,
                           provenance=?,
                           owner_group_id=?,
                           consumer_group_id=?,
                           created_at=?
                     WHERE grant_id=?
                    """,
                    (str(reason or ""), str(actor or "system"), prov, o_gid, c_gid, ts, gid),
                )
                return {
                    "grant_id": gid,
                    "created": False,
                    "reactivated": True,
                    "status": "active",
                    "memory_id": mid,
                }

            tx.execute(
                """
                INSERT INTO shared_memory_grants(
                    grant_id, owner_bot_id, owner_session_id, owner_visibility, owner_group_id,
                    memory_id, consumer_bot_id, consumer_session_id, consumer_visibility,
                    consumer_group_id, grant_mode, status, reason, actor, provenance,
                    created_at, revoked_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?, 'read', 'active', ?, ?, ?, ?, NULL)
                """,
                (
                    grant_id,
                    o_bot,
                    o_sess,
                    o_vis,
                    o_gid,
                    mid,
                    c_bot,
                    c_sess,
                    c_vis,
                    c_gid,
                    str(reason or ""),
                    str(actor or "system"),
                    prov,
                    ts,
                ),
            )
        return {
            "grant_id": grant_id,
            "created": True,
            "reactivated": False,
            "status": "active",
            "memory_id": mid,
        }

    def revoke(
        self,
        *,
        grant_id: str = "",
        owner_scope: Optional[Mapping[str, Any]] = None,
        consumer_scope: Optional[Mapping[str, Any]] = None,
        memory_id: Optional[int] = None,
        actor: str = "system",
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        ts = float(now if now is not None else time.time())
        with self._cm.write_transaction() as tx:
            if grant_id:
                cur = tx.execute(
                    """
                    UPDATE shared_memory_grants
                       SET status='revoked', revoked_at=?, actor=?
                     WHERE grant_id=? AND status='active'
                    """,
                    (ts, str(actor or "system"), str(grant_id)),
                )
                return {"revoked": int(cur.rowcount or 0), "by": "grant_id"}
            if not owner_scope or not consumer_scope or memory_id is None:
                raise ValueError("revoke requires grant_id or full owner/consumer/memory_id")
            o_bot, o_sess, o_vis, _ = _scope_tuple(owner_scope)
            c_bot, c_sess, c_vis, _ = _scope_tuple(consumer_scope)
            cur = tx.execute(
                """
                UPDATE shared_memory_grants
                   SET status='revoked', revoked_at=?, actor=?
                 WHERE owner_bot_id=? AND owner_session_id=? AND owner_visibility=?
                   AND memory_id=?
                   AND consumer_bot_id=? AND consumer_session_id=? AND consumer_visibility=?
                   AND grant_mode='read' AND status='active'
                """,
                (
                    ts,
                    str(actor or "system"),
                    o_bot,
                    o_sess,
                    o_vis,
                    int(memory_id),
                    c_bot,
                    c_sess,
                    c_vis,
                ),
            )
            return {"revoked": int(cur.rowcount or 0), "by": "scope_tuple"}

    def list_active_for_consumer(
        self,
        *,
        consumer_scope: Mapping[str, Any],
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        c_bot, c_sess, c_vis, _ = _scope_tuple(consumer_scope)
        lim = max(1, min(int(limit), 5000))
        rows = self._cm.execute_read(
            """
            SELECT grant_id, memory_id, owner_bot_id, owner_session_id, owner_visibility,
                   owner_group_id, consumer_group_id, reason, actor, created_at
              FROM shared_memory_grants
             WHERE consumer_bot_id=? AND consumer_session_id=? AND consumer_visibility=?
               AND status='active' AND grant_mode='read'
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (c_bot, c_sess, c_vis, lim),
        ).fetchall()
        return [
            {
                "grant_id": r[0],
                "memory_id": int(r[1]),
                "owner_bot_id": r[2],
                "owner_session_id": r[3],
                "owner_visibility": r[4],
                "owner_group_id": r[5],
                "consumer_group_id": r[6],
                "reason": r[7],
                "actor": r[8],
                "created_at": float(r[9]),
                "grant_mode": "read",
            }
            for r in rows
        ]

    def active_memory_ids_for_consumer(
        self,
        *,
        consumer_scope: Mapping[str, Any],
        limit: int = 5000,
    ) -> list[int]:
        grants = self.list_active_for_consumer(consumer_scope=consumer_scope, limit=limit)
        return [int(g["memory_id"]) for g in grants]


__all__ = ["SharedMemoryGrantRepository"]
