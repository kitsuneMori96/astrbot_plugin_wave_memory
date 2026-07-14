"""规范 People API：按 ``(user_id, group_id, bot_id)`` 投影人物画像。"""

from __future__ import annotations

import json
from typing import Any

from quart import Blueprint, current_app, jsonify, request

from ..api_contract import current_runtime_scope, error_payload, page_response
from ..container import get_container
from ..middleware.auth import require_auth

people_bp = Blueprint("people", __name__, url_prefix="/api")


def _page_args() -> tuple[int, int]:
    limit = max(1, min(500, int(request.args.get("limit", request.args.get("size", 25)))))
    if request.args.get("offset") is not None:
        offset = max(0, int(request.args.get("offset", 0)))
    else:
        page = max(1, int(request.args.get("page", 1)))
        offset = (page - 1) * limit
    return limit, offset


def _connection():
    db = getattr(get_container(), "db", None)
    if db is None:
        return None
    if getattr(db, "closed", False):
        try:
            db.reopen()
        except Exception:
            return None
    return getattr(db, "conn", None)


def _table_rows(conn: Any, table: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f'SELECT * FROM "{table}"')
    names = [str(column[0]) for column in cursor.description or ()]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _table_exists(conn: Any, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _json(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _registry_by_principal(conn: Any) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "person_registry"):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in _table_rows(conn, "person_registry"):
        principal = str(row.get("qq_id") or row.get("user_id") or "").strip()
        if not principal or principal in result:
            continue
        item = dict(row)
        item["aliases"] = _json(item.get("aliases"), [])
        item["groups"] = _json(item.get("groups"), [])
        item["tag_ids"] = _json(item.get("tag_ids"), [])
        item["metadata"] = _json(item.get("metadata"), {})
        result[principal] = item
    return result


def _request_scope():
    """只接受由统一请求 Scope provider 解析出的 RuntimeScope。"""
    try:
        provider = current_app.extensions.get("wave_api_contract", {}).get("request_scope_provider")
    except RuntimeError:
        provider = None
    return current_runtime_scope(provider)


def _profile_item(profile: dict[str, Any], registry: dict[str, dict[str, Any]], scope) -> dict[str, Any] | None:
    user_id = str(profile.get("user_id") or "").strip()
    group_id = str(profile.get("group_id") or "").strip()
    bot_id = str(profile.get("bot_id") or "").strip()
    if not user_id or not group_id or not bot_id:
        return None
    if scope is None or scope.session is None or scope.visibility != "group":
        return None
    if bot_id != scope.bot_id or group_id != scope.session.conversation_id:
        return None

    person = registry.get(user_id, {})
    item = dict(profile)
    item.pop("affection", None)
    item["scope"] = {"user_id": user_id, "group_id": group_id, "bot_id": bot_id}
    item["scope_key"] = f"{user_id}|{group_id}|{bot_id}"
    item["display_name"] = person.get("display_name") or item.get("nickname") or user_id
    item["aliases"] = person.get("aliases", [])
    item["registry_metadata"] = person.get("metadata", {})
    item["metadata"] = _json(item.get("metadata"), {})
    item["person_registry"] = {
        key: person.get(key)
        for key in ("qq_id", "display_name", "first_seen", "last_seen", "message_count", "groups", "tag_ids")
        if key in person
    }
    # 旧 affection 不是经复合 RuntimeScope 验证的 affinity projection，不得伪装为好感度。
    item["affinity"] = None
    item["affinity_status"] = "unavailable"
    item["affinity_reason_code"] = "scoped_affinity_projection_unavailable"
    return item


@people_bp.route("/people/legacy/audit", methods=["GET"])
@require_auth
async def list_legacy_people_audit():
    """按 legacy (bot_id, group_id) 查看人物；不声称具有 canonical SessionRef。"""
    try:
        limit, offset = _page_args()
        conn = _connection()
        if conn is None:
            return jsonify(error_payload("service_unavailable", "People store is unavailable", retryable=True)), 503
        if not _table_exists(conn, "user_profiles"):
            payload = page_response([], total=0, limit=limit, offset=offset)
            payload.update({"legacy": True, "readonly": True, "scope_status": "legacy_group_key"})
            return jsonify(payload)
        bot_id = str(request.args.get("bot_id") or "").strip()
        group_id = str(request.args.get("group_id") or "").strip()
        search = str(request.args.get("search") or "").strip().casefold()
        registry = _registry_by_principal(conn)
        items = []
        for profile in _table_rows(conn, "user_profiles"):
            if bot_id and str(profile.get("bot_id") or "") != bot_id:
                continue
            if group_id and str(profile.get("group_id") or "") != group_id:
                continue
            user_id = str(profile.get("user_id") or "").strip()
            if not user_id:
                continue
            person = registry.get(user_id, {})
            item = dict(profile)
            item.pop("affection", None)
            item.update({
                "display_name": person.get("display_name") or item.get("nickname") or user_id,
                "aliases": person.get("aliases", []),
                "metadata": _json(item.get("metadata"), {}),
                "legacy": True,
                "readonly": True,
                "scope": None,
                "scope_status": "legacy_group_key",
                "scope_reason": "canonical_platform_and_session_unavailable",
                "affinity": None,
                "affinity_status": "unavailable",
                "affinity_reason_code": "scoped_affinity_projection_unavailable",
                "object_ref": None,
                "actions": {},
            })
            if search and not any(
                search in str(item.get(field, "")).casefold()
                for field in ("user_id", "display_name", "nickname", "aliases", "bot_id", "group_id")
            ):
                continue
            items.append(item)
        items.sort(key=lambda item: (
            str(item.get("bot_id") or ""), str(item.get("group_id") or ""),
            str(item.get("display_name") or "").casefold(), str(item.get("user_id") or ""),
        ))
        total = len(items)
        payload = page_response(items[offset:offset + limit], total=total, limit=limit, offset=offset)
        payload.update({
            "legacy": True,
            "readonly": True,
            "scope": None,
            "scope_status": "legacy_group_key",
            "reason_code": "canonical_platform_and_session_unavailable",
        })
        return jsonify(payload)
    except (TypeError, ValueError):
        return jsonify(error_payload("invalid_pagination", "Invalid pagination parameters")), 400
    except Exception:
        return jsonify(error_payload("service_unavailable", "Legacy people audit is unavailable", retryable=True)), 503


@people_bp.route("/people/legacy/relationships", methods=["GET"])
@require_auth
async def list_legacy_relationships_audit():
    """只读 legacy relationship events；筛选键只用于审计，不构造 RuntimeScope。"""
    try:
        limit, offset = _page_args()
        conn = _connection()
        if conn is None:
            return jsonify(error_payload("service_unavailable", "Relationship store is unavailable", retryable=True)), 503
        if not _table_exists(conn, "relationship_events"):
            payload = page_response([], total=0, limit=limit, offset=offset)
            payload.update({"legacy": True, "readonly": True, "scope": None, "scope_status": "legacy_group_key"})
            return jsonify(payload)
        bot_id = str(request.args.get("bot_id") or "").strip()
        group_id = str(request.args.get("group_id") or request.args.get("session_id") or "").strip()
        user_id = str(request.args.get("user_id") or "").strip()
        search = str(request.args.get("search") or "").strip()
        where = ["1=1"]
        params: list[Any] = []
        for column, value in (("bot_id", bot_id), ("group_id", group_id), ("user_id", user_id)):
            if value:
                where.append(f"{column}=?")
                params.append(value)
        if search:
            where.append("(user_id LIKE ? OR event_type LIKE ? OR dimension LIKE ? OR reason LIKE ?)")
            params.extend([f"%{search}%"] * 4)
        where_sql = " AND ".join(where)
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM relationship_events WHERE {where_sql}", params
        ).fetchone()[0])
        cursor = conn.execute(
            "SELECT id,bot_id,group_id,user_id,event_type,dimension,delta,reason,"
            "source_episode_id,source_memory_id,created_at FROM relationship_events "
            f"WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        names = [str(column[0]) for column in cursor.description or ()]
        items = []
        for row in cursor.fetchall():
            item = dict(zip(names, row))
            item.update({
                "legacy": True,
                "readonly": True,
                "scope": None,
                "scope_status": "legacy_group_key",
                "scope_reason": "canonical_platform_and_session_unavailable",
                "object_ref": None,
                "actions": {},
            })
            items.append(item)
        payload = page_response(items, total=total, limit=limit, offset=offset)
        payload.update({
            "legacy": True,
            "readonly": True,
            "scope": None,
            "scope_status": "legacy_group_key",
            "reason_code": "canonical_platform_and_session_unavailable",
        })
        return jsonify(payload)
    except (TypeError, ValueError):
        return jsonify(error_payload("invalid_pagination", "Invalid pagination parameters")), 400
    except Exception:
        return jsonify(error_payload("service_unavailable", "Legacy relationship audit is unavailable", retryable=True)), 503


@people_bp.route("/people", methods=["GET"])
@require_auth
async def list_people():
    try:
        limit, offset = _page_args()
        conn = _connection()
        if conn is None:
            return jsonify(error_payload("service_unavailable", "People store is unavailable", retryable=True)), 503
        if not _table_exists(conn, "user_profiles"):
            return jsonify(page_response([], total=0, limit=limit, offset=offset))

        scope = _request_scope()
        if scope is None or scope.session is None or scope.visibility != "group":
            return jsonify(error_payload("scope_required", "A complete request Scope is required")), 400
        registry = _registry_by_principal(conn)
        items = [
            item
            for profile in _table_rows(conn, "user_profiles")
            if (item := _profile_item(profile, registry, scope)) is not None
        ]

        search = str(request.args.get("search") or "").strip().casefold()
        if search:
            items = [
                item for item in items
                if any(
                    search in str(item.get(field, "")).casefold()
                    for field in ("user_id", "display_name", "nickname", "aliases", "scope_key")
                )
            ]
        for field in ("bot_id", "group_id", "user_id"):
            value = str(request.args.get(field) or "").strip()
            if value:
                items = [item for item in items if str(item.get(field) or "") == value]

        items.sort(key=lambda item: (
            str(item.get("display_name") or "").casefold(),
            str(item.get("bot_id") or ""),
            str(item.get("group_id") or ""),
            str(item.get("user_id") or ""),
        ))
        total = len(items)
        return jsonify(page_response(items[offset:offset + limit], total=total, limit=limit, offset=offset))
    except (TypeError, ValueError):
        return jsonify(error_payload("invalid_pagination", "Invalid pagination parameters")), 400
    except Exception:
        return jsonify(error_payload("service_unavailable", "People store is unavailable", retryable=True)), 503


__all__ = ["people_bp"]
