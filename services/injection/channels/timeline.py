"""Timeline 事件流注入通道。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

try:
    from ....domain.scope import RuntimeScope
except ImportError:  # 兼容独立测试/插件顶级加载
    from domain.scope import RuntimeScope

from ..channel_base import InjectionResult
from .safety import SafetyChannel, is_channel_allowed_in_mode


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _preview(text: str | None, limit: int = 120) -> str:
    compact = str(text or "").replace("\n", " ").strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("timeline", {}))


def _timeline_cfg(ctx: Any) -> Mapping[str, Any]:
    return _mapping(_mapping(getattr(ctx, "config", {})).get("timeline", {}))


def _group_scope(ctx: Any) -> RuntimeScope | None:
    """仅接受完整、已解析的 group RuntimeScope。"""
    scope = getattr(ctx, "scope", None)
    if not isinstance(scope, RuntimeScope):
        return None
    if (
        scope.visibility != "group"
        or scope.session is None
        or scope.session.kind != "group"
        or not scope.bot_id
        or not scope.session.id
        or not scope.session.conversation_id
    ):
        return None
    return scope


class TimelineChannel:
    """复用 memories.summary 的近期事件流通道。"""

    name = "timeline"

    def __init__(
        self,
        *,
        db: Any,
        safety_channel: SafetyChannel | None = None,
        cross_group_enabled: bool = True,
        shared_memory_grants_enabled: bool = False,
    ):
        self.db = db
        self.safety = safety_channel or SafetyChannel()
        self.cross_group_enabled = _as_bool(cross_group_enabled, True)
        self.shared_memory_grants_enabled = _as_bool(shared_memory_grants_enabled, False)

    def _memory_columns(self) -> set[str]:
        rows = self.db.conn.execute("PRAGMA table_info(memories)").fetchall()
        return {str(row[1]) for row in rows}

    def _grant_ids_for_scope(self, scope: RuntimeScope) -> list[int]:
        if self.cross_group_enabled or not self.shared_memory_grants_enabled:
            return []
        if scope.session is None:
            return []
        try:
            from engine.shared_grant_recall import load_active_grant_memory_ids
        except ImportError:  # pragma: no cover
            from ....engine.shared_grant_recall import load_active_grant_memory_ids
        return load_active_grant_memory_ids(
            self.db,
            bot_id=scope.bot_id,
            session_id=scope.session.id,
            visibility=scope.visibility,
            group_id=scope.session.conversation_id,
        )

    def _scope_predicate(
        self,
        scope: RuntimeScope,
        columns: set[str],
        *,
        grant_ids: list[int] | None = None,
    ) -> tuple[str, tuple[Any, ...]]:
        """Timeline read filter: active rows only; Scope is not a hard gate.

        Existing group Timeline semantics intentionally remain open within the
        current group (and may expand cross-group by configuration).  The only
        new restriction is that private formal and private:* legacy rows cannot
        enter either group path.
        """
        assert scope.session is not None
        parts = [
            "COALESCE(quarantine, 0) = 0",
            "COALESCE(visibility, '') != 'private'",
            "COALESCE(group_id, '') NOT LIKE 'private:%'",
        ]
        if "memory_type" in columns:
            parts.append(
                "COALESCE(memory_type, 'message') NOT IN "
                "('archived', 'evicted', 'deleted', 'noise')"
            )
        if "source" in columns:
            parts.append("COALESCE(source, '') != 'noise'")
        active = " AND ".join(parts)
        if self.cross_group_enabled:
            return f"({active})", ()
        current_group = scope.session.conversation_id
        local = f"({active}) AND COALESCE(group_id, '') = ?"
        params: list[Any] = [current_group]
        ids = list(grant_ids or [])
        if ids:
            try:
                from engine.shared_grant_recall import formal_grant_id_predicate
            except ImportError:  # pragma: no cover
                from ....engine.shared_grant_recall import formal_grant_id_predicate
            grant_sql, grant_params = formal_grant_id_predicate(ids, alias="")
            grant_safe = "COALESCE(visibility, '') != 'private' AND COALESCE(group_id, '') NOT LIKE 'private:%'"
            return f"(({local}) OR (({grant_sql}) AND {grant_safe}))", tuple(params) + grant_params
        return local, tuple(params)

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"timeline channel disabled in {mode} mode")

        channel_cfg = _channel_cfg(ctx)
        if not _as_bool(channel_cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="timeline channel disabled by config")
        max_items = _as_int(channel_cfg.get("max_items"), 5)
        if max_items <= 0:
            return InjectionResult.empty(self.name, reason="timeline max_items is zero")
        if not getattr(ctx, "sender_id", None):
            return InjectionResult.empty(self.name, reason="timeline requires sender_id")
        if _group_scope(ctx) is None:
            return InjectionResult.empty(self.name, reason="timeline requires resolved group RuntimeScope")

        try:
            # Over-fetch when cross-group is on so summary-level fanout clones can
            # be collapsed without starving unique events.
            fetch_n = max_items * 4 if self.cross_group_enabled else max_items
            items = self._query(ctx, max_items=fetch_n)
            scope = _group_scope(ctx)
            current_group = ""
            if scope is not None and scope.session is not None:
                current_group = scope.session.conversation_id
            items = self._collapse_summary_fanout(items, current_group_id=current_group)[:max_items]
            # Dual-track fallback: when consolidation has not produced summaries for
            # this speaker's recent activity, backfill with raw messages instead of
            # silently reaching further into the past.
            if len(items) < max_items:
                newest_summary_ts = 0.0
                for item in items:
                    try:
                        newest_summary_ts = max(newest_summary_ts, float(item.get("timestamp") or 0.0))
                    except (TypeError, ValueError):
                        continue
                raw_items = self._query_raw_messages(
                    ctx,
                    limit=max_items - len(items),
                    exclude_before=newest_summary_ts,
                )
                items = items + raw_items
            kept, filtered = self.safety.filter_items(items, ctx=ctx, text_fields=("summary",))
            if not kept:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no safe timeline summaries")
            text = self._render(kept)
            return InjectionResult.hit(
                self.name,
                text,
                items=[self._audit_item(item) for item in kept],
                filtered=[self._audit_filtered(item) for item in filtered],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    def _query(self, ctx: Any, *, max_items: int) -> list[dict[str, Any]]:
        scope = _group_scope(ctx)
        if scope is None or scope.session is None:
            return []
        cfg = _timeline_cfg(ctx)
        days = _as_int(cfg.get("days"), 0)
        now = float(getattr(ctx, "now", 0.0) or time.time())
        sender_name = str(getattr(ctx, "sender_name", "") or "")
        sender_id = str(getattr(ctx, "sender_id", "") or "")
        like_value = f"%{sender_name}%" if sender_name else f"%{sender_id}%"
        try:
            columns = self._memory_columns()
            required_columns = {"summary", "timestamp", "sender_id", "content"}
            if not required_columns.issubset(columns):
                return []

            scope_columns = {"bot_id", "session_id", "visibility", "resolution_state", "quarantine", "group_id"}
            if not scope_columns.issubset(columns):
                # No legacy group_id fallback: unresolved or partial Scope rows
                # must never be promoted into either exact or cross-group recall.
                return []
            grant_ids = self._grant_ids_for_scope(scope)
            scope_clause, scope_params = self._scope_predicate(
                scope, columns, grant_ids=grant_ids
            )

            days_clause = "AND timestamp > ?" if days > 0 else ""
            params = (*scope_params, sender_id, like_value)
            if days > 0:
                params = (*params, now - days * 86400)
            rows = self.db.conn.execute(
                f"""SELECT summary, group_id,
                          DATE(MAX(timestamp), 'unixepoch', 'localtime') AS day,
                          MAX(timestamp) AS ts
                     FROM memories
                    WHERE summary IS NOT NULL AND summary != '' AND summary != '日常灌水'
                      AND summary NOT LIKE 'quarantined%'
                      AND ({scope_clause})
                      AND (sender_id = ? OR content LIKE ?)
                      {days_clause}
                    GROUP BY summary, group_id
                    ORDER BY MAX(timestamp) DESC
                    LIMIT ?""",
                (*params, max_items),
            ).fetchall()
        except Exception:
            return []
        return [
            {
                "summary": row[0],
                "group_id": row[1],
                "day": row[2],
                "timestamp": row[3],
                "source": "timeline",
            }
            for row in rows
        ]

    def _query_raw_messages(
        self, ctx: Any, *, limit: int, exclude_before: float = 0.0
    ) -> list[dict[str, Any]]:
        """Fall back to the speaker's own recent messages when summaries are missing.

        Consolidation depends on an external LLM provider.  When that provider is
        unavailable the ``summary`` column stops being filled, and a summary-only
        timeline silently reaches further and further into the past.  Reading raw
        content keeps the timeline anchored to what actually happened, and the
        caller marks these rows as degraded so the outage stays visible in traces.
        """
        scope = _group_scope(ctx)
        if scope is None or scope.session is None or limit <= 0:
            return []
        sender_id = str(getattr(ctx, "sender_id", "") or "")
        if not sender_id:
            return []
        cfg = _timeline_cfg(ctx)
        days = _as_int(cfg.get("days"), 0)
        now = float(getattr(ctx, "now", 0.0) or time.time())
        try:
            columns = self._memory_columns()
            if not {"summary", "timestamp", "sender_id", "content"}.issubset(columns):
                return []
            scope_columns = {"bot_id", "session_id", "visibility", "resolution_state", "quarantine", "group_id"}
            if not scope_columns.issubset(columns):
                return []
            grant_ids = self._grant_ids_for_scope(scope)
            scope_clause, scope_params = self._scope_predicate(
                scope, columns, grant_ids=grant_ids
            )
            clauses = [f"({scope_clause})", "sender_id = ?", "content IS NOT NULL", "content != ''"]
            params: list[Any] = [*scope_params, sender_id]
            if days > 0:
                clauses.append("timestamp > ?")
                params.append(now - days * 86400)
            if exclude_before > 0:
                # Only fill the gap newer than the newest summary we already have.
                clauses.append("timestamp > ?")
                params.append(exclude_before)
            rows = self.db.conn.execute(
                f"""SELECT content, group_id, timestamp
                     FROM memories
                    WHERE {' AND '.join(clauses)}
                    ORDER BY timestamp DESC
                    LIMIT ?""",
                (*params, limit),
            ).fetchall()
        except Exception:
            return []
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for content, group_id, ts in rows:
            text = " ".join(str(content or "").split())
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                day = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
            except (TypeError, ValueError, OSError, OverflowError):
                day = ""
            items.append({
                "summary": text,
                "group_id": group_id,
                "day": day,
                "timestamp": ts,
                "source": "timeline_raw",
                "degraded_to_raw_content": True,
            })
        return items

    @staticmethod
    def _collapse_summary_fanout(
        items: list[dict[str, Any]],
        *,
        current_group_id: str = "",
    ) -> list[dict[str, Any]]:
        """Keep one timeline row per summary text, preferring the current group.

        Historical Phase-2 fanout copied the same summary into many groups. Grouping
        only by (summary, group_id) therefore re-injects the same event N times.
        """
        if not items:
            return []
        current = str(current_group_id or "").strip()

        def sort_key(item: Mapping[str, Any]) -> tuple:
            group_id = str(item.get("group_id") or "")
            in_current = 0 if current and group_id == current else 1
            try:
                ts = -float(item.get("timestamp") or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
            return (in_current, ts, group_id)

        ordered = sorted(items, key=sort_key)
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for item in ordered:
            summary = " ".join(str(item.get("summary") or "").split())
            key = summary.casefold() if summary else f"empty:{item.get('group_id')}:{item.get('timestamp')}"
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _render(items: list[dict[str, Any]]) -> str:
        """Render summaries and raw fallback rows as clearly separated sections.

        Raw messages are verbatim speech, not consolidated events.  Mixing them
        under one heading would invite the model to treat quotes as conclusions.
        """
        event_lines: list[str] = []
        raw_lines: list[str] = []
        for item in items:
            text = str(item.get("summary", ""))
            if item.get("degraded_to_raw_content"):
                raw_lines.append(f"- {item.get('day', '')}: {text[:60]}")
            else:
                group_id = item.get("group_id") or "unknown-group"
                event_lines.append(f"- {item.get('day', '')} [群 {group_id}]: {text[:60]}")
        sections: list[str] = []
        if event_lines:
            sections.append("[最近与此人的事件]\n" + "\n".join(event_lines))
        if raw_lines:
            sections.append(
                "[最近发言片段（尚未生成事件摘要）]\n" + "\n".join(raw_lines)
            )
        return "\n".join(sections)

    @staticmethod
    def _audit_item(item: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "summary": item.get("summary", ""),
            "group_id": item.get("group_id", ""),
            "day": item.get("day", ""),
            "timestamp": item.get("timestamp"),
            "preview": _preview(item.get("summary", "")),
        }
        if item.get("degraded_to_raw_content"):
            # Surface the consolidation outage in injection_trace_channels.details
            # so a stalled summary pipeline is observable instead of silent.
            payload["degraded_to_raw_content"] = True
            payload["source"] = item.get("source", "timeline_raw")
        return payload

    @staticmethod
    def _audit_filtered(item: Mapping[str, Any]) -> dict[str, Any]:
        payload = TimelineChannel._audit_item(item)
        payload["filter_reason"] = item.get("filter_reason", "filtered")
        payload["filter_channel"] = item.get("filter_channel", "timeline")
        return payload

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["TimelineChannel"]
