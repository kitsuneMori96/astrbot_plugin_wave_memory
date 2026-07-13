"""Blackbox management API — v4.5 只读诊断与列表入口。"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

try:
    from quart import Blueprint, jsonify, request
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时的轻量兜底
    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    request = None  # type: ignore[assignment]

    def jsonify(value=None, **kwargs):  # type: ignore[no-redef]
        return value if value is not None else kwargs

try:
    from ..container import get_container
    from ..middleware.auth import require_auth
except Exception:  # pragma: no cover
    def get_container():  # type: ignore[no-redef]
        return None

    def require_auth(func):  # type: ignore[no-redef]
        return func


blackbox_bp = Blueprint("blackbox", __name__, url_prefix="/api/blackbox")

def _conn_from_container() -> Any | None:
    c = get_container()
    db = getattr(c, "db", None)
    if not db:
        return None
    if getattr(db, "closed", False):
        try:
            db.reopen()
        except Exception:
            return None
    return getattr(db, "conn", None)


def _conn_lore_from_container() -> Any | None:
    candidates = [
        "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/book_lore.db",
        "book_lore.db",
        "astrbot_plugin_wave_memory/book_lore.db",
        "data/plugin_data/astrbot_plugin_wave_memory/book_lore.db",
        "AstrBot-master/data/plugin_data/astrbot_plugin_wave_memory/book_lore.db",
    ]
    c = get_container()
    if c:
        p = getattr(c, "lore_db_path", None)
        if p:
            candidates.insert(0, p)
    for p in candidates:
        if os.path.exists(p):
            try:
                conn = sqlite3.connect(p)
                conn.row_factory = sqlite3.Row
                return conn
            except Exception:
                continue
    return None


def _table_exists(conn: Any, table: str) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _columns(conn: Any, table: str) -> list[str]:
    if not _table_exists(conn, table):
        return []
    try:
        return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        d = {}
        for key in row.keys():
            val = row[key]
            if isinstance(val, bytes):
                d[key] = f"<bytes:{len(val)}>"
            else:
                d[key] = val
        return d
    try:
        raw = dict(row)
        return {k: (f"<bytes:{len(v)}>" if isinstance(v, bytes) else v) for k, v in raw.items()}
    except Exception:
        return {}


def _safe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    text = str(value)
    try:
        return json.loads(text)
    except Exception:
        return text


def _count(conn: Any, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    try:
        return int(conn.execute(sql, params).fetchone()[0] or 0)
    except Exception:
        return 0


def _avg(conn: Any, table: str, column: str) -> float:
    if column not in _columns(conn, table):
        return 0.0
    try:
        return round(float(conn.execute(f"SELECT AVG({column}) FROM {table}").fetchone()[0] or 0.0), 4)
    except Exception:
        return 0.0


def _limit_offset(limit: Any = None, offset: Any = None) -> tuple[int, int]:
    try:
        limit_i = int(limit if limit is not None else 50)
    except Exception:
        limit_i = 50
    try:
        offset_i = int(offset if offset is not None else 0)
    except Exception:
        offset_i = 0
    return max(1, min(limit_i, 200)), max(0, offset_i)


def _request_args() -> dict[str, Any]:
    if request is None:
        return {}
    try:
        return dict(request.args)
    except Exception:
        return {}


def _list_table(
    conn: Any,
    table: str,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str = "",
    sort: str = "id",
    filter: str = "",
    search_columns: tuple[str, ...] = (),
    filter_column: str = "status",
) -> dict[str, Any]:
    limit, offset = _limit_offset(limit, offset)
    cols = _columns(conn, table)
    if not cols:
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "search": search, "sort": sort, "filter": filter}
    clauses: list[str] = []
    params: list[Any] = []
    usable_search_cols = [c for c in search_columns if c in cols]
    if search and usable_search_cols:
        clauses.append("(" + " OR ".join([f"{c} LIKE ?" for c in usable_search_cols]) + ")")
        params.extend([f"%{search}%"] * len(usable_search_cols))
    if filter and filter_column in cols:
        clauses.append(f"{filter_column} = ?")
        params.append(filter)
    where = " AND ".join(clauses)
    order_col = sort.lstrip("-") if sort else "id"
    if order_col not in cols:
        order_col = "id" if "id" in cols else cols[0]
    direction = "DESC" if str(sort).startswith("-") else "ASC"
    total = _count(conn, table, where, tuple(params))
    sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "") + f" ORDER BY {order_col} {direction} LIMIT ? OFFSET ?"
    try:
        rows = conn.execute(sql, tuple(params) + (limit, offset)).fetchall()
        items = [_row_dict(row) for row in rows]
    except Exception:
        items = []
    return {"items": items, "total": total, "limit": limit, "offset": offset, "search": search, "sort": sort, "filter": filter}


def _error_payload(code: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "readonly": True}


def build_book_lore_summary(conn: Any) -> dict[str, Any]:
    return {
        "readonly": True,
        "route_prefix": "/api/blackbox/book-lore",
        "counts": {
            "entities": _count(conn, "book_entities"),
            "relations": _count(conn, "book_relations"),
            "communities": _count(conn, "book_communities"),
            "notes": _count(conn, "book_notes"),
        },
        "index_health": {
            "hnsw_file": "unknown",
            "id_map": "unknown",
            "scope": "BookLore-only",
        },
        "safety": "只读诊断；重建索引等写操作需二次确认。",
    }


def build_fewshot_summary(conn: Any) -> dict[str, Any]:
    return {
        "readonly": True,
        "route_prefix": "/api/blackbox/fewshot",
        "counts": {
            "pending": _count(conn, "few_shot_examples", "status=?", ("pending",)),
            "approved": _count(conn, "few_shot_examples", "status=?", ("approved",)),
            "rejected": _count(conn, "few_shot_examples", "status=?", ("rejected",)),
            "total": _count(conn, "few_shot_examples"),
        },
        "average_score": _avg(conn, "few_shot_examples", "score"),
        "drift_detection": "readonly_probe_only",
        "safety": "风格范例库不是事实记忆；批准/拒绝属于后续写操作。",
    }


def build_facts_payload(conn: Any, *, limit: int = 50, offset: int = 0, search: str = "", sort: str = "id", filter: str = "") -> dict[str, Any]:
    payload = _list_table(
        conn,
        "facts",
        limit=limit,
        offset=offset,
        search=search,
        sort=sort,
        filter=filter,
        search_columns=("subject", "predicate", "object", "fact_type"),
        filter_column="fact_type",
    )
    payload.update({"readonly": True, "route_prefix": "/api/blackbox/facts"})
    return payload


def build_people_payload(conn: Any, *, limit: int = 50, offset: int = 0, search: str = "", sort: str = "qq_id", filter: str = "") -> dict[str, Any]:
    limit, offset = _limit_offset(limit, offset)
    if not _table_exists(conn, "person_registry") and not _table_exists(conn, "user_profiles"):
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "search": search, "sort": sort, "filter": filter, "readonly": True}

    profiles_by_user: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "user_profiles"):
        try:
            for row in conn.execute("SELECT * FROM user_profiles").fetchall():
                prof = _row_dict(row)
                profiles_by_user.setdefault(str(prof.get("user_id", "")), prof)
        except Exception:
            profiles_by_user = {}

    items: list[dict[str, Any]] = []
    if _table_exists(conn, "person_registry"):
        try:
            rows = conn.execute("SELECT * FROM person_registry").fetchall()
        except Exception:
            rows = []
        for row in rows:
            item = _row_dict(row)
            qq_id = str(item.get("qq_id") or item.get("user_id") or "")
            prof = profiles_by_user.get(qq_id, {})
            item.update({
                "user_id": prof.get("user_id", qq_id),
                "group_id": prof.get("group_id"),
                "bot_id": prof.get("bot_id"),
                "nickname": prof.get("nickname", item.get("display_name")),
                "affection": prof.get("affection"),
                "interaction_count": prof.get("interaction_count", item.get("message_count")),
                "metadata": _safe_json(prof.get("metadata")),
            })
            items.append(item)
    else:
        items = list(profiles_by_user.values())

    if search:
        needle = str(search)
        items = [i for i in items if any(needle in str(i.get(k, "")) for k in ("qq_id", "user_id", "display_name", "nickname", "aliases"))]
    if filter:
        items = [i for i in items if str(i.get("bot_id", "")) == str(filter) or str(i.get("group_id", "")) == str(filter)]
    reverse = str(sort).startswith("-")
    sort_key = str(sort).lstrip("-") or "qq_id"
    items.sort(key=lambda i: str(i.get(sort_key, "")), reverse=reverse)
    total = len(items)
    return {"items": items[offset:offset + limit], "total": total, "limit": limit, "offset": offset, "search": search, "sort": sort, "filter": filter, "readonly": True, "route_prefix": "/api/blackbox/people"}


def build_indexes_summary(conn: Any) -> dict[str, Any]:
    mem_cols = _columns(conn, "memories")
    tag_cols = _columns(conn, "tags")
    return {
        "readonly": True,
        "route_prefix": "/api/blackbox/indexes",
        "dangerous_operations_require_preview": True,
        "counts": {
            "memories": _count(conn, "memories"),
            "memory_tags": _count(conn, "memory_tags"),
            "tags": _count(conn, "tags"),
            "memories_missing_vector": _count(conn, "memories", "vector IS NULL") if "vector" in mem_cols else 0,
            "tags_missing_vector": _count(conn, "tags", "vector IS NULL") if "vector" in tag_cols else 0,
            "facts": _count(conn, "facts"),
            "book_entities": _count(conn, "book_entities"),
        },
        "health": {
            "memory_vector_index": "unknown" if "vector" not in mem_cols else "inspectable",
            "fts5_index": "present" if _table_exists(conn, "memories_fts") or _table_exists(conn, "memory_fts") else "missing_or_external",
            "epa_basis": "unknown",
            "book_lore_hnsw_index": "unknown",
        },
    }


def _query_options(default_sort: str = "id") -> dict[str, Any]:
    args = _request_args()
    limit, offset = _limit_offset(args.get("limit"), args.get("offset"))
    return {
        "limit": limit,
        "offset": offset,
        "search": str(args.get("search", "") or ""),
        "sort": str(args.get("sort", default_sort) or default_sort),
        "filter": str(args.get("filter", "") or ""),
    }


@blackbox_bp.route("/fewshot/examples/<int:example_id>", methods=["DELETE", "PUT"])
@require_auth
async def modify_fewshot_example(example_id: int):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    if not _table_exists(conn, "few_shot_examples"):
        return jsonify({"ok": False, "error": "table_missing"}), 404
    if request.method == "DELETE":
        conn.execute("DELETE FROM few_shot_examples WHERE id=?", (example_id,))
        conn.commit()
        return jsonify({"ok": True, "deleted": example_id})
    body = await request.get_json() or {}
    status = body.get("status")
    content = body.get("content")
    if status is not None:
        conn.execute("UPDATE few_shot_examples SET status=? WHERE id=?", (status, example_id))
    if content is not None:
        conn.execute("UPDATE few_shot_examples SET content=? WHERE id=?", (content, example_id))
    conn.commit()
    return jsonify({"ok": True, "updated": example_id})


@blackbox_bp.route("/facts/<int:fact_id>", methods=["DELETE", "PUT"])
@require_auth
async def modify_fact(fact_id: int):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    if not _table_exists(conn, "facts"):
        return jsonify({"ok": False, "error": "table_missing"}), 404
    if request.method == "DELETE":
        conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        conn.commit()
        return jsonify({"ok": True, "deleted": fact_id})
    body = await request.get_json() or {}
    confidence = body.get("confidence")
    predicate = body.get("predicate")
    obj = body.get("object")
    if confidence is not None:
        conn.execute("UPDATE facts SET confidence=? WHERE id=?", (float(confidence), fact_id))
    if predicate is not None:
        conn.execute("UPDATE facts SET predicate=? WHERE id=?", (str(predicate), fact_id))
    if obj is not None:
        conn.execute("UPDATE facts SET object=? WHERE id=?", (str(obj), fact_id))
    conn.commit()
    return jsonify({"ok": True, "updated": fact_id})


@blackbox_bp.route("/book-lore/<string:table_type>/<int:id_val>", methods=["DELETE"])
@require_auth
async def delete_book_lore_item(table_type: str, id_val: int):
    # 优先连接独立的 book_lore.db 专属书设库
    is_lore_db = True
    conn = _conn_lore_from_container()
    if not conn:
        is_lore_db = False
        conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    table_map = {
        "entities": "book_entities",
        "communities": "book_communities",
        "relations": "book_relations",
        "notes": "book_notes"
    }
    table = table_map.get(table_type)
    if not table or not _table_exists(conn, table):
        if is_lore_db:
            conn.close()
        return jsonify({"ok": False, "error": "invalid_table_or_missing"}), 400
    try:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (id_val,))
        conn.commit()
        return jsonify({"ok": True, "deleted": id_val, "table": table})
    finally:
        if is_lore_db:
            conn.close()


@blackbox_bp.route("/indexes/rebuild", methods=["POST"])
@require_auth
async def rebuild_indexes_action():
    """Compatibility boundary: create a durable Maintenance job, never rebuild inline."""
    # Legacy ``memory_index.rebuild`` execution was intentionally removed from this route.
    container = get_container()
    jobs = getattr(container, "durable_jobs", None) if container is not None else None
    if jobs is None:
        return jsonify({"ok": False, "error": "maintenance_unavailable"}), 503
    body = await request.get_json() or {}
    kind = str(body.get("kind") or "memory_index").strip()
    if kind not in {"memory_index", "tag_index", "cooccurrence"}:
        return jsonify({"ok": False, "error": "unsupported_repair_kind"}), 400
    preflight_token = str(body.get("preflight_token") or "").strip()
    confirmation = str(body.get("confirmation") or "").strip()
    if not preflight_token or confirmation != "rebuild":
        return jsonify({
            "ok": False,
            "error": "maintenance_confirmation_required",
            "required_confirmation": "rebuild",
        }), 409
    schedule_slot = str(body.get("schedule_slot") or preflight_token).strip()
    idempotency_key = str(
        body.get("idempotency_key")
        or f"maintenance:{kind}:{preflight_token}"
    )
    job_request = await jobs.create_request(
        idempotency_key=idempotency_key,
        kind=f"maintenance.{kind}.rebuild",
        scope={"kind": "system_maintenance"},
        payload={
            "kind": kind,
            "preflight_token": preflight_token,
            "requested_by": "webui",
        },
    )
    run = await jobs.schedule_run(
        request_id=job_request.request_id,
        schedule_slot=schedule_slot,
        cursor_generation=int(body.get("cursor_generation") or 0),
        cursor={"phase": "queued"},
    )
    return jsonify({
        "ok": True,
        "accepted": True,
        "request_id": job_request.request_id,
        "job_id": run.run_id,
        "status": run.status,
        "repair_kind": kind,
    }), 202


@blackbox_bp.route("/book-lore/summary", methods=["GET"])
@require_auth
async def get_book_lore_summary():
    is_lore_db = True
    conn = _conn_lore_from_container()
    if not conn:
        is_lore_db = False
        conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    try:
        return jsonify(build_book_lore_summary(conn))
    finally:
        if is_lore_db:
            conn.close()


@blackbox_bp.route("/book-lore/entities", methods=["GET"])
@require_auth
async def list_book_lore_entities():
    is_lore_db = True
    conn = _conn_lore_from_container()
    if not conn:
        is_lore_db = False
        conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    try:
        payload = _list_table(conn, "book_entities", **_query_options("name"), search_columns=("name", "summary", "source_book"))
        payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/entities"})
        return jsonify(payload)
    finally:
        if is_lore_db:
            conn.close()


@blackbox_bp.route("/book-lore/communities", methods=["GET"])
@require_auth
async def list_book_lore_communities():
    is_lore_db = True
    conn = _conn_lore_from_container()
    if not conn:
        is_lore_db = False
        conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    try:
        payload = _list_table(conn, "book_communities", **_query_options("title"), search_columns=("title", "summary"))
        payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/communities"})
        return jsonify(payload)
    finally:
        if is_lore_db:
            conn.close()


@blackbox_bp.route("/book-lore/relations", methods=["GET"])
@require_auth
async def list_book_lore_relations():
    is_lore_db = True
    conn = _conn_lore_from_container()
    if not conn:
        is_lore_db = False
        conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    try:
        payload = _list_table(conn, "book_relations", **_query_options("source"), search_columns=("source", "target", "relation"))
        payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/relations"})
        return jsonify(payload)
    finally:
        if is_lore_db:
            conn.close()


@blackbox_bp.route("/book-lore/notes", methods=["GET"])
@require_auth
async def list_book_lore_notes():
    is_lore_db = True
    conn = _conn_lore_from_container()
    if not conn:
        is_lore_db = False
        conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    try:
        payload = _list_table(conn, "book_notes", **_query_options("title"), search_columns=("title", "content"))
        payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/notes"})
        return jsonify(payload)
    finally:
        if is_lore_db:
            conn.close()


@blackbox_bp.route("/fewshot/summary", methods=["GET"])
@require_auth
async def get_fewshot_summary():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    return jsonify(build_fewshot_summary(conn))


@blackbox_bp.route("/fewshot/examples", methods=["GET"])
@require_auth
async def list_fewshot_examples():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    payload = _list_table(conn, "few_shot_examples", **_query_options("-score"), search_columns=("content", "traits", "status", "bot_id"))
    payload.update({"readonly": True, "route_prefix": "/api/blackbox/fewshot/examples"})
    return jsonify(payload)


@blackbox_bp.route("/facts", methods=["GET"])
@require_auth
async def list_facts():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    return jsonify(build_facts_payload(conn, **_query_options("id")))


@blackbox_bp.route("/facts/<int:fact_id>", methods=["GET"])
@require_auth
async def get_fact_detail(fact_id: int):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    if not _table_exists(conn, "facts"):
        return jsonify({"ok": False, "error": "facts_table_missing", "readonly": True}), 404
    row = conn.execute("SELECT * FROM facts WHERE id=? OR rowid=? LIMIT 1", (fact_id, fact_id)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "fact_not_found", "readonly": True}), 404
    item = _row_dict(row)
    item["readonly"] = True
    return jsonify(item)


@blackbox_bp.route("/people", methods=["GET"])
@require_auth
async def list_people():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    return jsonify(build_people_payload(conn, **_query_options("qq_id")))


@blackbox_bp.route("/people/<person_id>", methods=["GET"])
@require_auth
async def get_person_detail(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    payload = build_people_payload(conn, limit=200, search=person_id)
    for item in payload.get("items", []):
        if person_id in {str(item.get("qq_id", "")), str(item.get("user_id", ""))}:
            item["readonly"] = True
            return jsonify(item)
    return jsonify({"ok": False, "error": "person_not_found", "readonly": True}), 404


@blackbox_bp.route("/indexes/summary", methods=["GET"])
@require_auth
async def get_indexes_summary():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    return jsonify(build_indexes_summary(conn))


@blackbox_bp.route("/indexes/check", methods=["GET"])
@require_auth
async def check_indexes():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    payload = build_indexes_summary(conn)
    payload["ok"] = True
    payload["message"] = "只读诊断完成；重建、修复、清理等写操作必须二次确认。"
    return jsonify(payload)


__all__ = [
    "blackbox_bp",
    "build_book_lore_summary",
    "build_fewshot_summary",
    "build_facts_payload",
    "build_people_payload",
    "build_indexes_summary",
]
