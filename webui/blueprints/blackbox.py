"""Blackbox management API — v4.5 只读诊断与列表入口。"""

from __future__ import annotations

import json
import time
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
        return {key: row[key] for key in row.keys()}
    return dict(row)


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

    # 用复合键 (user_id, group_id, bot_id) 索引 user_profiles，保留所有分组信息
    profiles_by_key: dict[str, dict[str, Any]] = {}
    # 同时维护 user_id → profile 索引作为回退
    profiles_by_uid: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "user_profiles"):
        try:
            for row in conn.execute("SELECT * FROM user_profiles").fetchall():
                prof = _row_dict(row)
                uid = str(prof.get("user_id", ""))
                gid = str(prof.get("group_id", ""))
                bid = str(prof.get("bot_id", ""))
                key = f"{uid}\x00{gid}\x00{bid}"
                if key not in profiles_by_key:
                    profiles_by_key[key] = prof
                if uid not in profiles_by_uid:
                    profiles_by_uid[uid] = prof
        except Exception:
            profiles_by_key = {}
            profiles_by_uid = {}

    items: list[dict[str, Any]] = []
    if _table_exists(conn, "person_registry"):
        try:
            rows = conn.execute("SELECT * FROM person_registry").fetchall()
        except Exception:
            rows = []
        if rows:
            for row in rows:
                item = _row_dict(row)
                qq_id = str(item.get("qq_id") or item.get("user_id") or "")
                pgid = str(item.get("group_id", ""))
                pbid = str(item.get("bot_id", ""))
                prof = profiles_by_key.get(f"{qq_id}\x00{pgid}\x00{pbid}")
                if not prof:
                    prof = profiles_by_uid.get(qq_id)
                # 兜底：qq_id 与 user_id 可能带不同前缀
                if not prof and qq_id:
                    for p in profiles_by_uid.values():
                        puid = str(p.get("user_id", ""))
                        if puid and (puid == qq_id or puid.endswith(qq_id) or qq_id.endswith(puid)):
                            prof = p
                            break
                if not prof:
                    prof = {}
                item.update({
                    "user_id": prof.get("user_id", qq_id),
                    "group_id": prof.get("group_id", pgid),
                    "bot_id": prof.get("bot_id", pbid),
                    "nickname": prof.get("nickname", item.get("display_name")),
                    "affection": prof.get("affection"),
                    "interaction_count": prof.get("interaction_count", item.get("message_count")),
                    "metadata": _safe_json(prof.get("metadata")),
                })
                items.append(item)
        else:
            items = list(profiles_by_key.values())
    else:
        items = list(profiles_by_key.values())

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


def build_indexes_summary(conn: Any, container=None) -> dict[str, Any]:
    mem_cols = _columns(conn, "memories")
    tag_cols = _columns(conn, "tags")

    memory_index = getattr(container, "memory_index", None) if container else None
    epa = getattr(container, "epa", None) if container else None

    if memory_index is not None and memory_index.count:
        memory_vector_status = "present"
    elif "vector" in mem_cols:
        memory_vector_status = "inspectable"
    else:
        memory_vector_status = "unknown"

    fts5_present = _table_exists(conn, "fts_memories") or _table_exists(conn, "memories_fts") or _table_exists(conn, "memory_fts")

    epa_status = "present" if (epa and getattr(epa, "initialized", False)) else "missing"

    book_lore_vec = _count(conn, "book_entities", "vector IS NOT NULL")
    book_lore_status = "present" if book_lore_vec > 0 else "missing"

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
            "memory_vector_index": memory_vector_status,
            "fts5_index": "present" if fts5_present else "missing_or_external",
            "epa_basis": epa_status,
            "book_lore_hnsw_index": book_lore_status,
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


@blackbox_bp.route("/book-lore/summary", methods=["GET"])
@require_auth
async def get_book_lore_summary():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    return jsonify(build_book_lore_summary(conn))


@blackbox_bp.route("/book-lore/entities", methods=["GET"])
@require_auth
async def list_book_lore_entities():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    payload = _list_table(conn, "book_entities", **_query_options("name"), search_columns=("name", "summary", "source_book"))
    payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/entities"})
    return jsonify(payload)


@blackbox_bp.route("/book-lore/communities", methods=["GET"])
@require_auth
async def list_book_lore_communities():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    payload = _list_table(conn, "book_communities", **_query_options("title"), search_columns=("title", "summary"))
    payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/communities"})
    return jsonify(payload)


@blackbox_bp.route("/book-lore/relations", methods=["GET"])
@require_auth
async def list_book_lore_relations():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    payload = _list_table(conn, "book_relations", **_query_options("source"), search_columns=("source", "target", "relation"))
    payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/relations"})
    return jsonify(payload)


@blackbox_bp.route("/book-lore/notes", methods=["GET"])
@require_auth
async def list_book_lore_notes():
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    payload = _list_table(conn, "book_notes", **_query_options("title"), search_columns=("title", "content"))
    payload.update({"readonly": True, "route_prefix": "/api/blackbox/book-lore/notes"})
    return jsonify(payload)


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


@blackbox_bp.route("/people/<person_id>/detail", methods=["GET"])
@require_auth
async def get_person_aggregated(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503

    profiles = []
    if _table_exists(conn, "user_profiles"):
        try:
            for row in conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (person_id,)
            ).fetchall():
                profiles.append(_row_dict(row))
        except Exception:
            pass

    registry = None
    if _table_exists(conn, "person_registry"):
        try:
            row = conn.execute(
                "SELECT * FROM person_registry WHERE qq_id = ?", (person_id,)
            ).fetchone()
            if row:
                registry = _row_dict(row)
        except Exception:
            pass

    result = {
        "qq_id": person_id,
        "display_name": (registry or {}).get("display_name", ""),
        "aliases": _safe_json((registry or {}).get("aliases", "[]")) or [],
        "message_count": (registry or {}).get("message_count", 0),
        "groups": _safe_json((registry or {}).get("groups", "[]")) or [],
        "person_registry_tags": _safe_json((registry or {}).get("tag_ids", "[]")) or [],
        "profiles": profiles,
        "first_seen": min((p.get("first_seen") or 0 for p in profiles if p.get("first_seen")), default=0),
        "last_seen": max((p.get("last_seen") or 0 for p in profiles if p.get("last_seen")), default=0),
    }

    current = max(profiles, key=lambda p: p.get("affection") or 0) if profiles else {}
    meta = _safe_json(current.get("metadata", "{}")) or {}
    result.update({
        "affection": current.get("affection", 0),
        "interaction_count": current.get("interaction_count", 0),
        "nickname": current.get("nickname", ""),
        "attitude_level": meta.get("attitude_level", "neutral"),
        "dimensions": meta.get("dimensions", {}),
        "impression": meta.get("impression", ""),
        "tags": meta.get("tags", {}),
        "meta_updated": meta.get("meta_updated", ""),
    })

    # 三级回退：MetaThinking 未写入时从其他字段补数据
    if not result.get("impression"):
        # L2: user_profiles.notes
        imp = (current.get("notes") or "").strip()
        if imp:
            result["impression"] = imp
        else:
            # L3: personality_tags
            pt = (current.get("personality_tags") or "").strip()
            if pt:
                result["impression"] = pt[:200]

    if not result.get("tags"):
        # L2: person_registry.tag_ids → {name: "untagged", confidence: 0.5}
        pr_tags = result.get("person_registry_tags") or []
        if pr_tags:
            result["tags"] = {t: 1.0 for t in pr_tags if t}
        else:
            # L3: personality_tags → 逗号分隔
            pt = (current.get("personality_tags") or "").strip()
            if pt:
                result["tags"] = {t.strip(): 0.5 for t in pt.split(",") if t.strip()}
            else:
                # L4: memory_mentions → memory_tags → tags
                try:
                    rows = conn.execute(
                        "SELECT DISTINCT t.name FROM memory_mentions m "
                        "JOIN memory_tags mt ON m.memory_id = mt.memory_id "
                        "JOIN tags t ON mt.tag_id = t.id "
                        "WHERE m.qq_id = ? AND t.name IS NOT NULL",
                        (person_id,),
                    ).fetchall()
                    if rows:
                        result["tags"] = {r[0]: 0.6 for r in rows}
                except Exception:
                    pass

    return jsonify(result)


@blackbox_bp.route("/people/<person_id>/events", methods=["GET"])
@require_auth
async def get_person_events(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    args = _request_args()
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM relationship_events WHERE user_id = ?", (person_id,)
        ).fetchone()
        total = row[0] if row else 0
        rows = conn.execute(
            """SELECT id, bot_id, group_id, event_type, dimension, delta, reason,
                      source_episode_id, source_memory_id, created_at
               FROM relationship_events
               WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (person_id, limit, offset),
        ).fetchall()
        items = [_row_dict(r) for r in rows]
    except Exception:
        items = []
        total = 0
    return jsonify({"items": items, "total": total, "limit": limit, "offset": offset})


@blackbox_bp.route("/people/<person_id>/dimension-trend", methods=["GET"])
@require_auth
async def get_person_dimension_trend(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    args = _request_args()
    days = int(args.get("days", 30))
    days = max(7, min(365, days))
    since = time.time() - days * 86400

    try:
        rows = conn.execute(
            """SELECT dimension, delta, created_at
               FROM relationship_events
               WHERE user_id = ? AND created_at >= ?
               ORDER BY created_at ASC""",
            (person_id, since),
        ).fetchall()
    except Exception:
        rows = []

    daily: dict[str, dict[str, float]] = {}
    for dim, delta, ts in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if day not in daily:
            daily[day] = {}
        daily[day][dim] = daily[day].get(dim, 0) + (delta or 0)

    baseline = {}
    try:
        meta_row = conn.execute(
            "SELECT metadata FROM user_profiles WHERE user_id = ? LIMIT 1", (person_id,)
        ).fetchone()
        if meta_row and meta_row[0]:
            meta = json.loads(meta_row[0])
            baseline = meta.get("dimensions", {})
    except Exception:
        pass

    dim_names = ["familiarity", "trust", "fun", "hostility", "depth"]
    cum: dict[str, float] = {d: baseline.get(d, 0) for d in dim_names}
    points: list[dict[str, Any]] = []
    sorted_days = sorted(daily.keys())
    for day in sorted_days:
        for dim in dim_names:
            cum[dim] = cum.get(dim, 0) + daily[day].get(dim, 0)
        score = sum(cum.get(d, 0) * w for d, w in [("familiarity", 0.25), ("trust", 0.30), ("fun", 0.20), ("depth", 0.25)])
        score -= cum.get("hostility", 0) * 0.5
        points.append({
            "date": day,
            "affection": round(max(-100, min(100, score)), 1),
            "familiarity": round(cum["familiarity"], 2),
            "trust": round(cum["trust"], 2),
            "fun": round(cum["fun"], 2),
            "hostility": round(cum["hostility"], 2),
            "depth": round(cum["depth"], 2),
        })

    return jsonify({"points": points, "days": days})


@blackbox_bp.route("/people/<person_id>/expression", methods=["GET"])
@require_auth
async def get_person_expression(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    try:
        row = conn.execute(
            "SELECT expression, group_id FROM expression_patterns WHERE situation = ? LIMIT 1",
            (f"user:{person_id}",),
        ).fetchone()
        if row and row[0]:
            return jsonify({
                "expression": _safe_json(row[0]),
                "group_id": row[1],
                "found": True,
            })
    except Exception:
        pass
    return jsonify({"found": False, "expression": None, "group_id": None})


@blackbox_bp.route("/people/<person_id>/notes", methods=["PUT"])
@require_auth
async def update_person_notes(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    body = await request.get_json(silent=True) or {}
    notes = str(body.get("notes", "") or "")
    bot_id = str(body.get("bot_id", "yushu") or "yushu")
    group_id = str(body.get("group_id", "") or "")
    if not group_id:
        return jsonify({"ok": False, "error": "group_id_required"}), 400
    try:
        conn.execute(
            "UPDATE user_profiles SET notes = ? WHERE user_id = ? AND group_id = ? AND bot_id = ?",
            (notes, person_id, group_id, bot_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@blackbox_bp.route("/people/<person_id>/impression", methods=["PUT"])
@require_auth
async def update_person_impression(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    body = await request.get_json(silent=True) or {}
    impression = str(body.get("impression", "") or "")
    bot_id = str(body.get("bot_id", "yushu") or "yushu")
    group_id = str(body.get("group_id", "") or "")
    if not group_id:
        return jsonify({"ok": False, "error": "group_id_required"}), 400
    try:
        row = conn.execute(
            "SELECT metadata FROM user_profiles WHERE user_id = ? AND group_id = ? AND bot_id = ?",
            (person_id, group_id, bot_id),
        ).fetchone()
        meta = json.loads(row[0]) if row and row[0] else {}
        meta["impression"] = impression
        meta["meta_updated"] = time.strftime("%Y-%m-%d %H:%M")
        conn.execute(
            "UPDATE user_profiles SET metadata = ? WHERE user_id = ? AND group_id = ? AND bot_id = ?",
            (json.dumps(meta, ensure_ascii=False), person_id, group_id, bot_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@blackbox_bp.route("/people/<person_id>/tags", methods=["PUT"])
@require_auth
async def update_person_tags(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    body = await request.get_json(silent=True) or {}
    tags = body.get("tags", {})
    if not isinstance(tags, dict):
        return jsonify({"ok": False, "error": "tags_must_be_dict"}), 400
    bot_id = str(body.get("bot_id", "yushu") or "yushu")
    group_id = str(body.get("group_id", "") or "")
    if not group_id:
        return jsonify({"ok": False, "error": "group_id_required"}), 400
    try:
        row = conn.execute(
            "SELECT metadata FROM user_profiles WHERE user_id = ? AND group_id = ? AND bot_id = ?",
            (person_id, group_id, bot_id),
        ).fetchone()
        meta = json.loads(row[0]) if row and row[0] else {}
        meta["tags"] = {k: max(0, int(v)) for k, v in tags.items()}
        meta["meta_updated"] = time.strftime("%Y-%m-%d %H:%M")
        conn.execute(
            "UPDATE user_profiles SET metadata = ? WHERE user_id = ? AND group_id = ? AND bot_id = ?",
            (json.dumps(meta, ensure_ascii=False), person_id, group_id, bot_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@blackbox_bp.route("/people/<person_id>/aliases", methods=["PUT"])
@require_auth
async def update_person_aliases(person_id: str):
    conn = _conn_from_container()
    if not conn:
        return jsonify(_error_payload("database_unavailable")), 503
    body = await request.get_json(silent=True) or {}
    action = str(body.get("action", "add") or "add")
    alias = str(body.get("alias", "") or "").strip()
    if not alias:
        return jsonify({"ok": False, "error": "alias_required"}), 400
    if len(alias) > 20:
        return jsonify({"ok": False, "error": "alias_too_long"}), 400
    try:
        row = conn.execute(
            "SELECT aliases, display_name FROM person_registry WHERE qq_id = ?", (person_id,)
        ).fetchone()
        aliases: list[str] = _safe_json(row[0]) if row and row[0] else []
        display_name = row[1] if row else ""
        if action == "add":
            if alias not in aliases:
                aliases.append(alias)
        elif action == "remove":
            aliases = [a for a in aliases if a != alias]
        elif action == "set_display":
            display_name = alias
        else:
            return jsonify({"ok": False, "error": "invalid_action"}), 400
        conn.execute(
            "INSERT OR REPLACE INTO person_registry (qq_id, display_name, aliases, last_seen) VALUES (?, ?, ?, ?)",
            (person_id, display_name, json.dumps(aliases, ensure_ascii=False), time.time()),
        )
        conn.commit()
        return jsonify({"ok": True, "aliases": aliases, "display_name": display_name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
    c = get_container()
    db = getattr(c, "db", None)
    if not db:
        return jsonify(_error_payload("database_unavailable")), 503
    if getattr(db, "closed", False):
        try:
            db.reopen()
        except Exception:
            return jsonify(_error_payload("database_unavailable")), 503
    return jsonify(build_indexes_summary(db.conn, container=c))


@blackbox_bp.route("/indexes/check", methods=["GET"])
@require_auth
async def check_indexes():
    c = get_container()
    db = getattr(c, "db", None)
    if not db:
        return jsonify(_error_payload("database_unavailable")), 503
    if getattr(db, "closed", False):
        try:
            db.reopen()
        except Exception:
            return jsonify(_error_payload("database_unavailable")), 503
    payload = build_indexes_summary(db.conn, container=c)
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
