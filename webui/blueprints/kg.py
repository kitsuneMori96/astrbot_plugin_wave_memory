"""KnowledgeGraph Blueprint — 统一知识图谱查询层 (M1)

从 facts + tag_relations 聚合语义图谱，替代 cooccurrence 统计共现。
不改底层表结构，纯查询层虚拟图。
"""

from __future__ import annotations

import json
import time
from functools import wraps

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

try:
    from ...domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from ...engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError
except ImportError:  # pragma: no cover - plugin root may be imported directly
    from domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError

kg_bp = Blueprint("kg", __name__, url_prefix="/api/kg")

# 全景图缓存（按 facts+relations 行数版本缓存）
_overview_cache: dict = {"version": None, "data": None, "ts": 0}
_CACHE_TTL = 120  # 2 分钟


def _table_exists(conn, table: str) -> bool:
    """检查表是否存在。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _table_columns(conn, table: str) -> set[str]:
    """返回表字段集合；旧库/缺表时安全降级。"""
    if not _table_exists(conn, table):
        return set()
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _json_loads_safe(value, default=None):
    """解析 JSON 字符串，失败时返回 default。"""
    if default is None:
        default = {}
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _scope_error(code: str, status: int):
    return jsonify({"error": {"code": code}}), status


def _scope_failure(exc: Exception):
    code = getattr(exc, "reason_code", None) or getattr(exc, "code", None) or "invalid_scope"
    return _scope_error(str(code), 400 if code in {"scope_required", "pagination_required"} else 422)


def _legacy_mutation_disabled(handler):
    """Keep legacy facts readable for migration only; formal APIs reject their mutation."""
    @wraps(handler)
    async def reject(*args, **kwargs):
        return _scope_error("legacy_mutation_disabled", 410)
    return reject


def _scoped_repo(container):
    repo = getattr(getattr(container, "db", None), "scoped_knowledge", None)
    if repo is None:
        raise ScopedKnowledgeScopeError("scoped_repository_unavailable")
    return repo


def _group_scope_from_query() -> RuntimeScope:
    required = ("bot_id", "session_id", "visibility")
    if any(request.args.get(field) is None for field in required):
        raise ScopedKnowledgeScopeError("scope_required")
    bot_id, session_id, visibility = (request.args.get(field) for field in required)
    if visibility != "group":
        raise ScopedKnowledgeScopeError("derived_scope_visibility_unsupported")
    try:
        platform_id, kind, conversation_id = str(session_id).split(":", 2)
    except ValueError as exc:
        raise ScopeValidationError("invalid_session_id", "session_id must be canonical") from exc
    return RuntimeScope(str(bot_id), visibility, SessionRef(str(session_id), platform_id, kind, conversation_id))


def _scope_from_envelope(body: dict) -> RuntimeScope:
    if "scope" not in body:
        raise ScopedKnowledgeScopeError("scope_required")
    scope = ScopeCodec.from_dict(body["scope"])
    if not isinstance(scope, RuntimeScope) or scope.visibility != "group":
        raise ScopedKnowledgeScopeError("derived_scope_visibility_unsupported")
    return scope


def _page_from_query() -> tuple[int, int]:
    if request.args.get("page") is None or request.args.get("page_size") is None:
        raise ScopedKnowledgeScopeError("pagination_required")
    try:
        page, page_size = int(request.args["page"]), int(request.args["page_size"])
    except (TypeError, ValueError) as exc:
        raise ScopedKnowledgeScopeError("invalid_pagination") from exc
    if page < 1 or page_size < 1 or page_size > 100:
        raise ScopedKnowledgeScopeError("invalid_pagination")
    return page, page_size


def _scoped_page(items: list[dict], *, page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "page": {"number": page, "page_size": page_size, "total": len(items),
                 "total_status": "exact", "has_next": start + page_size < len(items)},
    }


def _scope_params(scope: RuntimeScope) -> tuple[str, str, str]:
    assert scope.session is not None
    return scope.bot_id, scope.session.id, scope.visibility


def _find_scoped_fact(repo, scope: RuntimeScope, fact_id: int) -> dict:
    for row in repo.list_scoped_facts(scope, limit=10000):
        if row.get("id") == fact_id:
            return row
    raise LookupError("scoped_object_not_found")


def _list_scoped_relations(repo, scope: RuntimeScope) -> list[dict]:
    """关系仓储尚无 list 端口，仍经同一 scoped repo 的连接严格按 Scope 读取。"""
    cm = getattr(repo, "cm", None)
    if cm is None:
        raise ScopedKnowledgeScopeError("scoped_repository_unavailable")
    rows = cm.execute_read(
        """SELECT r.id, r.source_tag_id, r.target_tag_id, r.relation_type, r.weight, r.confidence,
                  r.metadata, r.created_at, r.updated_at, source.name, target.name
             FROM scoped_tag_relations r
             JOIN scoped_tags source ON source.id=r.source_tag_id
              AND source.bot_id=r.bot_id AND source.session_id=r.session_id AND source.visibility=r.visibility
             JOIN scoped_tags target ON target.id=r.target_tag_id
              AND target.bot_id=r.bot_id AND target.session_id=r.session_id AND target.visibility=r.visibility
             WHERE r.bot_id=? AND r.session_id=? AND r.visibility=?
             ORDER BY r.updated_at DESC, r.id DESC""",
        _scope_params(scope),
    ).fetchall()
    return [{"id": row[0], "source_tag_id": row[1], "target_tag_id": row[2], "relation_type": row[3],
             "weight": row[4], "confidence": row[5], "metadata": _json_loads_safe(row[6], {}),
             "created_at": row[7], "updated_at": row[8], "source": row[9], "target": row[10]}
            for row in rows]


def _delete_scoped(repo, scope: RuntimeScope, table: str, object_id: int) -> bool:
    if table not in {"scoped_facts", "scoped_tag_relations"}:
        raise ValueError("unsupported scoped table")
    cm = getattr(repo, "cm", None)
    if cm is None:
        raise ScopedKnowledgeScopeError("scoped_repository_unavailable")
    cur = cm.execute_write(
        f"DELETE FROM {table} WHERE id=? AND bot_id=? AND session_id=? AND visibility=?",
        (object_id, *_scope_params(scope)),
    )
    cm.commit()
    return bool(getattr(cur, "rowcount", 0))


@kg_bp.route("/facts", methods=["GET"])
@require_auth
async def list_scoped_facts():
    """正式 facts 列表；只读取 scoped_facts。"""
    try:
        scope = _group_scope_from_query()
        page, page_size = _page_from_query()
        rows = _scoped_repo(get_container()).list_scoped_facts(
            scope, subject=request.args.get("subject"), limit=10000,
        )
        payload = _scoped_page(rows, page=page, page_size=page_size)
        payload["scope"] = ScopeCodec.to_dict(scope)
        return jsonify(payload)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@kg_bp.route("/facts", methods=["POST"])
@require_auth
async def create_scoped_fact():
    """正式 facts 创建；写入 scoped_facts，拒绝 legacy fallback。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        fact_id = _scoped_repo(get_container()).upsert_scoped_fact(
            scope, subject=body.get("subject"), predicate=body.get("predicate"), object=body.get("object"),
            confidence=max(0.0, min(1.0, float(body.get("confidence", 0.8)))),
            status=str(body.get("status") or "pending"), source_memory_id=body.get("source_memory_id"),
            provenance=body.get("provenance") or {}, valid_from=body.get("valid_from"), valid_until=body.get("valid_until"),
        )
        clear_kg_cache()
        return jsonify({"ok": True, "fact_id": fact_id, "scope": ScopeCodec.to_dict(scope)})
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@kg_bp.route("/tag-relations", methods=["GET"])
@require_auth
async def list_scoped_relations():
    """正式关系列表；只读取 scoped_tag_relations。"""
    try:
        scope = _group_scope_from_query()
        page, page_size = _page_from_query()
        rows = _list_scoped_relations(_scoped_repo(get_container()), scope)
        payload = _scoped_page(rows, page=page, page_size=page_size)
        payload["scope"] = ScopeCodec.to_dict(scope)
        return jsonify(payload)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@kg_bp.route("/tag-relations", methods=["POST"])
@require_auth
async def create_scoped_relation():
    """正式关系创建；两端 tag 必须属于同一 Scope。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        relation_id = _scoped_repo(get_container()).upsert_scoped_tag_relation(
            scope, source_tag_id=body.get("source_tag_id"), target_tag_id=body.get("target_tag_id"),
            relation_type=body.get("relation_type", body.get("type")), weight=float(body.get("weight", 1.0)),
            confidence=float(body.get("confidence", 0.0)), metadata=body.get("metadata") or {},
        )
        clear_kg_cache()
        return jsonify({"ok": True, "relation_id": relation_id, "scope": ScopeCodec.to_dict(scope)})
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


def _legacy_audit_payload(rows: list[dict], *, page: int, page_size: int) -> dict:
    for row in rows:
        row.update({"legacy": True, "unresolved_legacy": True, "scope": None})
    payload = _scoped_page(rows, page=page, page_size=page_size)
    payload.update({"legacy": True, "unresolved_legacy": True, "scope": None, "readonly": True})
    return payload


@kg_bp.route("/legacy/audit/facts", methods=["GET"])
@require_auth
async def legacy_audit_facts():
    """旧 facts 表的只读审查页。"""
    c = get_container()
    if not _table_exists(c.db.conn, "facts"):
        return jsonify(_legacy_audit_payload([], page=1, page_size=50))
    page = max(1, int(request.args.get("page", 1) or 1))
    page_size = max(1, min(500, int(request.args.get("page_size", request.args.get("limit", 50)) or 50)))
    rows = c.db.conn.execute(
        "SELECT id, subject, predicate, object, confidence, created_at FROM facts ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return jsonify(_legacy_audit_payload([
        {"id": row[0], "subject": row[1], "predicate": row[2], "object": row[3], "confidence": row[4], "created_at": row[5]}
        for row in rows
    ], page=page, page_size=page_size))


@kg_bp.route("/legacy/audit/relations", methods=["GET"])
@require_auth
async def legacy_audit_relations():
    """旧 tag_relations 表的只读审查页。"""
    c = get_container()
    if not _table_exists(c.db.conn, "tag_relations"):
        return jsonify(_legacy_audit_payload([], page=1, page_size=50))
    page = max(1, int(request.args.get("page", 1) or 1))
    page_size = max(1, min(500, int(request.args.get("page_size", request.args.get("limit", 50)) or 50)))
    rows = c.db.conn.execute(
        """SELECT r.id, source.name, r.relation_type, target.name, r.weight
             FROM tag_relations r JOIN tags source ON source.id=r.source_tag_id
             JOIN tags target ON target.id=r.target_tag_id ORDER BY r.id DESC"""
    ).fetchall()
    return jsonify(_legacy_audit_payload([
        {"id": row[0], "source": row[1], "relation_type": row[2], "target": row[3], "weight": row[4]}
        for row in rows
    ], page=page, page_size=page_size))


@kg_bp.route("/overview")
@require_auth
async def overview():
    """全景知识图谱（可配置）。

    参数：
    - max_nodes: 最大节点数(50-500, default 150)
    - min_weight: 最小边权重阈值(0-5, default 0.5)
    - min_confidence: 关系置信度门限(0-1, default 0.0)
    - relation_types: 逗号分隔的关系类型筛选(空=全部)
    - node_types: 逗号分隔的节点类型(空=全部)
    - days: 时间范围(7/30/90/0=全部, default 0)
    """
    c = get_container()
    try:
        max_nodes = int(request.args.get("max_nodes", 150))
    except (ValueError, TypeError):
        max_nodes = 150
    max_nodes = max(30, min(1000, max_nodes)) # 向上支持 1000 节点高承载
    try:
        min_weight = float(request.args.get("min_weight", 0.5))
    except (ValueError, TypeError):
        min_weight = 0.5
    try:
        min_confidence = float(request.args.get("min_confidence", 0.0))
    except (ValueError, TypeError):
        min_confidence = 0.0
    relation_types_raw = request.args.get("relation_types", "")
    node_types_raw = request.args.get("node_types", "")
    try:
        days = int(request.args.get("days", 0))
    except (ValueError, TypeError):
        days = 0

    relation_filter = set(relation_types_raw.split(",")) - {""} if relation_types_raw else None
    node_filter = set(node_types_raw.split(",")) - {""} if node_types_raw else None

    # 版本缓存 key 包含参数
    now = time.time()
    cache_key = f"{max_nodes}:{min_weight}:{min_confidence}:{relation_types_raw}:{node_types_raw}:{days}"
    if not _table_exists(c.db.conn, "facts") or not _table_exists(c.db.conn, "tag_relations"):
        return jsonify({"nodes": [], "edges": []})
    try:
        version = c.db.conn.execute(
            "SELECT (SELECT COUNT(*) FROM facts) + (SELECT COUNT(*) FROM tag_relations)"
        ).fetchone()[0]
    except Exception:
        version = 0
    full_version = f"{version}:{cache_key}"
    if _overview_cache["version"] == full_version and _overview_cache["data"] and (now - _overview_cache["ts"]) < _CACHE_TTL:
        return jsonify(_overview_cache["data"])

    # 时间过滤
    time_cond = ""
    time_param: list = []
    if days > 0:
        cutoff = now - days * 86400
        time_cond = " AND created_at >= ?"
        time_param = [cutoff]

    # Step 1: 从 facts 提取实体和边
    fact_rows = c.db.conn.execute(
        f"SELECT subject, predicate, object, confidence FROM facts WHERE confidence >= ?{time_cond} ORDER BY confidence DESC LIMIT 3000",
        [min_confidence] + time_param,
    ).fetchall()

    # Step 2: 从 tag_relations 提取实体和边
    tag_rel_cols = _table_columns(c.db.conn, "tag_relations")
    rel_confidence_cond = " AND tr.confidence >= ?" if "confidence" in tag_rel_cols else ""
    rel_params = [min_confidence] if "confidence" in tag_rel_cols else []
    rel_weight_cond = f" AND tr.weight >= {min_weight}" if min_weight > 0 else ""
    rel_rows = c.db.conn.execute(
        f"""SELECT t1.name, tr.relation_type, t2.name, tr.weight, t1.tag_type, t2.tag_type
           FROM tag_relations tr
           JOIN tags t1 ON tr.source_tag_id = t1.id
           JOIN tags t2 ON tr.target_tag_id = t2.id
           WHERE 1=1{rel_weight_cond}{rel_confidence_cond}
           ORDER BY tr.weight DESC LIMIT 3000""",
        rel_params,
    ).fetchall()

    # Step 3: 构建实体度数表
    entity_degree: dict[str, int] = {}
    entity_type: dict[str, str] = {}
    edges_raw: list[tuple] = []

    for subj, pred, obj, conf in fact_rows:
        if not subj or not obj:
            continue
        subj = subj.strip()[:30]
        obj = obj.strip()[:30]
        label = pred.strip()[:20] if pred else "relates"
        if relation_filter and label not in relation_filter and "fact" not in relation_filter:
            continue
        entity_degree[subj] = entity_degree.get(subj, 0) + 1
        entity_degree[obj] = entity_degree.get(obj, 0) + 1
        entity_type.setdefault(subj, "entity")
        entity_type.setdefault(obj, "entity")
        edges_raw.append((subj, obj, label, float(conf or 1)))

    for src_name, rel_type, tgt_name, weight, src_type, tgt_type in rel_rows:
        if not src_name or not tgt_name:
            continue
        src_name = src_name.strip()[:30]
        tgt_name = tgt_name.strip()[:30]
        label = rel_type or "relates"
        if relation_filter and label not in relation_filter:
            continue
        entity_degree[src_name] = entity_degree.get(src_name, 0) + 1
        entity_degree[tgt_name] = entity_degree.get(tgt_name, 0) + 1
        entity_type.setdefault(src_name, src_type or "topic")
        entity_type.setdefault(tgt_name, tgt_type or "topic")
        edges_raw.append((src_name, tgt_name, label, float(weight or 1)))

    # ═══ 以边为中心构图（修复稀疏图"一坨"问题）═══
    # 先选 top 边 → 再从边端点建节点 → 保证每个节点至少有一条边 → 图有结构

    # Step 4: 实体消歧（name→QQ 合并）
    name_to_qq: dict[str, str] = {}
    qq_to_main: dict[str, str] = {}  # qq → 主名
    try:
        sender_rows = c.db.conn.execute(
            """SELECT sender_name, sender_id, COUNT(*) as cnt FROM memories
               WHERE sender_id != '' AND sender_name != ''
               GROUP BY sender_name, sender_id ORDER BY cnt DESC"""
        ).fetchall()
        for sname, sid, cnt in sender_rows:
            key = sname.strip()[:30]
            name_to_qq[key] = sid
            if sid not in qq_to_main:
                qq_to_main[sid] = key  # 第一个（最高频）作为主名
    except Exception:
        pass

    def resolve_name(n: str) -> str:
        """消歧：同 QQ 的名字合并到主名。"""
        qq = name_to_qq.get(n)
        if qq:
            return qq_to_main.get(qq, n)
        return n

    # Step 5: 构建边（消歧后），按权重排序取 top
    max_edges = max_nodes * 2
    edge_list: list[tuple[str, str, str, float]] = []  # (src, tgt, label, weight)
    seen_pairs: set = set()

    for src, tgt, label, weight in edges_raw:
        src_r = resolve_name(src)
        tgt_r = resolve_name(tgt)
        if src_r == tgt_r:
            continue  # 自环（消歧后同一实体）
        pair = (src_r, tgt_r, label)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        edge_list.append((src_r, tgt_r, label, weight))

    # 按权重排序取 top
    edge_list.sort(key=lambda x: x[3], reverse=True)
    edge_list = edge_list[:max_edges]

    # Step 6: 从边端点构建节点集
    node_degree: dict[str, int] = {}
    for src, tgt, _, _ in edge_list:
        node_degree[src] = node_degree.get(src, 0) + 1
        node_degree[tgt] = node_degree.get(tgt, 0) + 1

    # 限制节点数（优先保留高度数）
    if len(node_degree) > max_nodes:
        sorted_nd = sorted(node_degree.items(), key=lambda x: x[1], reverse=True)[:max_nodes]
        top_set = {n for n, _ in sorted_nd}
        # 过滤边：只保留两端都在 top 里的
        edge_list = [(s, t, l, w) for s, t, l, w in edge_list if s in top_set and t in top_set]
        # 重算度数
        node_degree = {}
        for src, tgt, _, _ in edge_list:
            node_degree[src] = node_degree.get(src, 0) + 1
            node_degree[tgt] = node_degree.get(tgt, 0) + 1
    else:
        top_set = set(node_degree.keys())

    # Step 7: 构建节点（按 node_filter 筛选）
    nodes = []
    name_to_id: dict[str, int] = {}
    for idx, (name, degree) in enumerate(sorted(node_degree.items(), key=lambda x: x[1], reverse=True)):
        etype = "person" if name in name_to_qq else entity_type.get(name, "entity")
        if node_filter and etype not in node_filter:
            continue
        nid = idx + 1
        name_to_id[name] = nid
        nodes.append({"id": nid, "name": name, "type": etype, "degree": degree})

    # Step 8: 构建边（映射到 node id）
    edges = []
    for src, tgt, label, weight in edge_list:
        src_id = name_to_id.get(src)
        tgt_id = name_to_id.get(tgt)
        if src_id and tgt_id:
            edges.append({"source": src_id, "target": tgt_id, "label": label, "weight": round(weight, 2)})

    data = {"nodes": nodes, "edges": edges}
    _overview_cache.update({"version": full_version, "data": data, "ts": now})
    return jsonify(data)


@kg_bp.route("/entity/<entity_name>")
@require_auth
async def entity_detail(entity_name: str):
    """实体详情：该实体相关的 facts + tag_relations + 关联记忆 + 人物画像(若为人物)。"""
    c = get_container()
    from urllib.parse import unquote
    name = unquote(entity_name).strip()
    limit = int(request.args.get("limit", 15))

    # 人物检测：通过 sender_name 反查 QQ
    person = None
    person_row = c.db.conn.execute(
        "SELECT sender_id, COUNT(*) FROM memories WHERE sender_name = ? AND sender_id != '' GROUP BY sender_id ORDER BY 2 DESC LIMIT 1",
        (name,),
    ).fetchone()
    if person_row:
        qq_id = person_row[0]
        # 聚合所有别名
        aliases = [r[0] for r in c.db.conn.execute(
            "SELECT DISTINCT sender_name FROM memories WHERE sender_id = ? AND sender_name != ''", (qq_id,)
        ).fetchall()]
        msg_count = c.db.conn.execute("SELECT COUNT(*) FROM memories WHERE sender_id = ?", (qq_id,)).fetchone()[0]
        # 好感度 + personality_tags（取最新/最高）
        profile = c.db.conn.execute(
            "SELECT affection, personality_tags, nickname FROM user_profiles WHERE user_id = ? ORDER BY affection DESC LIMIT 1",
            (qq_id,),
        ).fetchone()
        person = {
            "qq_id": qq_id,
            "name": name,
            "aliases": [a for a in aliases if a != name],
            "msg_count": msg_count,
            "affection": profile[0] if profile else 0,
            "personality_tags": json.loads(profile[1] or "[]") if profile and profile[1] else [],
        }

    # 相关 facts（如果是人物，搜所有别名）
    search_names = [name] + (person["aliases"] if person else [])
    facts_all = []
    for n in search_names[:5]:
        rows = c.db.conn.execute(
            "SELECT rowid, subject, predicate, object, confidence FROM facts WHERE subject = ? OR object = ? LIMIT ?",
            (n, n, limit),
        ).fetchall()
        facts_all.extend(rows)
    # 去重
    seen = set()
    facts = []
    for r in facts_all:
        key = (r[1], r[2], r[3])
        if key not in seen:
            seen.add(key)
            facts.append(r)

    # 相关 tag_relations
    relations_all = []
    for n in search_names[:5]:
        rows = c.db.conn.execute(
            """SELECT t1.name, tr.relation_type, t2.name, tr.weight
               FROM tag_relations tr
               JOIN tags t1 ON tr.source_tag_id = t1.id
               JOIN tags t2 ON tr.target_tag_id = t2.id
               WHERE t1.name = ? OR t2.name = ?
               LIMIT ?""",
            (n, n, limit),
        ).fetchall()
        relations_all.extend(rows)
    relations = list({(r[0],r[1],r[2]): r for r in relations_all}.values())

    # 关联记忆（人物按 QQ 查更准）
    if person:
        memories = c.db.conn.execute(
            "SELECT id, content, sender_name, timestamp FROM memories WHERE sender_id = ? ORDER BY timestamp DESC LIMIT ?",
            (person["qq_id"], limit),
        ).fetchall()
    else:
        memories = c.db.conn.execute(
            """SELECT m.id, m.content, m.sender_name, m.timestamp
               FROM memories m JOIN memory_tags mt ON m.id = mt.memory_id JOIN tags t ON mt.tag_id = t.id
               WHERE t.name = ? ORDER BY m.timestamp DESC LIMIT ?""",
            (name, limit),
        ).fetchall()

    return jsonify({
        "name": name,
        "person": person,
        "facts": [{"id": r[0], "subject": r[1], "predicate": r[2], "object": r[3], "confidence": r[4]} for r in facts[:limit]],
        "relations": [{"source": r[0], "type": r[1], "target": r[2], "weight": r[3]} for r in relations[:limit]],
        "memories": [{"id": r[0], "content": r[1], "sender": r[2] or "", "ts": r[3]} for r in memories],
    })


@kg_bp.route("/add-fact", methods=["POST"])
@require_auth
async def add_fact():
    """兼容别名；仍执行正式 scoped 创建，不允许写入 legacy facts。"""
    return await create_scoped_fact()


@kg_bp.route("/payment", methods=["POST"])
@require_auth
async def payment_webhook():
    """好感符 webhook：手机收款通知推送到这里，bot 确认并加好感。

    POST body: {"amount": 5.0, "note": "微信支付到账", "raw": "完整通知文本"}
    无需 auth（手机 Tasker/MacroDroid 直接调，用 secret token 验证）。
    """
    c = get_container()
    body = await request.get_json(silent=True) or {}
    amount = float(body.get("amount", 0))
    note = body.get("note", "")
    raw = body.get("raw", "")
    secret = body.get("secret", "")

    # 简单 token 验证（防止恶意调用）
    expected_secret = (c.plugin_config or {}).get("payment_secret", "wavemoney")
    if secret != expected_secret:
        return jsonify({"ok": False, "error": "invalid secret"}), 403

    if amount <= 0:
        return jsonify({"ok": False, "error": "amount must be > 0"})

    # 好感度映射
    if amount >= 100:
        bonus = 30
    elif amount >= 50:
        bonus = 15
    elif amount >= 10:
        bonus = 5
    elif amount >= 5:
        bonus = 3
    else:
        bonus = 1

    # scope-less 外部 webhook 不能创建事实：没有 canonical RuntimeScope、证据
    # 或目标主体时，写入 legacy facts 会把跨会话归属伪装成可信知识。

    # 返回结果（实际加好感需要知道是谁付的——由前端/群内认领机制处理）
    return jsonify({
        "ok": True,
        "amount": amount,
        "bonus": bonus,
        "message": f"收到 {amount} 元，好感 +{bonus}",
        "note": note,
    })


@kg_bp.route("/stats")
@require_auth
async def kg_stats():
    """图谱统计。"""
    c = get_container()
    conn = c.db.conn
    def _safe_count(table, cond=""):
        if not _table_exists(conn, table):
            return 0
        try:
            sql = f"SELECT COUNT(*) FROM {table}"
            if cond:
                sql += f" WHERE {cond}"
            return conn.execute(sql).fetchone()[0]
        except Exception:
            return 0
    return jsonify({
        "facts": _safe_count("facts"),
        "tag_relations": _safe_count("tag_relations"),
        "beliefs": _safe_count("beliefs"),
        "concerns": _safe_count("concerns"),
        "jargon": _safe_count("jargon", "is_jargon=1"),
        "persons": conn.execute("SELECT COUNT(DISTINCT sender_id) FROM memories WHERE sender_id!=''").fetchone()[0] if _table_exists(conn, "memories") else 0,
    })


@kg_bp.route("/config")
@require_auth
async def kg_config():
    """图谱可用的筛选选项(供前端配置面板)。"""
    c = get_container()
    rel_types = []
    if _table_exists(c.db.conn, "tag_relations"):
        rel_types = [r[0] for r in c.db.conn.execute(
            "SELECT DISTINCT relation_type FROM tag_relations WHERE relation_type IS NOT NULL ORDER BY relation_type"
        ).fetchall()]
    rel_types += ["fact"]
    node_types = ["person", "topic", "entity", "event", "emotion", "fact", "location", "time", "memory", "belief", "concern", "jargon", "community", "affinity", "source"]
    return jsonify({
        "relation_types": rel_types,
        "node_types": node_types,
        "defaults": {"max_nodes": 150, "min_weight": 0.5, "days": 0},
    })


def clear_kg_cache() -> None:
    """清空 KG 查询缓存，用于事实编辑后刷新和测试。"""
    _overview_cache.clear()
    _overview_cache.update({"version": None, "data": None, "ts": 0})


def _safe_table_state(conn, table: str, time_columns: tuple[str, ...] = ()) -> tuple[int, float]:
    """返回表的轻量版本状态：行数 + 可用时间列最大值。表/列缺失时安全降级。"""
    if not _table_exists(conn, table):
        return (0, 0.0)
    try:
        count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
    except Exception:
        count = 0
    max_ts = 0.0
    for column in time_columns:
        try:
            row = conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()
            if row and row[0] is not None:
                max_ts = max(max_ts, float(row[0] or 0))
        except Exception:
            continue
    return (count, max_ts)


def _full_graph_cache_version(conn, layers: set[str]) -> tuple:
    """按请求图层涉及的数据表生成版本戳，避免非 facts 图层在 TTL 内陈旧。"""
    watched: list[tuple[str, tuple[str, ...]]] = [("memories", ("timestamp",))]
    if "facts" in layers:
        watched.extend([("facts", ("created_at", "last_reinforced")), ("tag_relations", ("created_at",)), ("tags", ("updated_at", "last_seen", "created_at"))])
    if "beliefs" in layers:
        watched.append(("beliefs", ("last_reinforced", "created_at")))
    if "concerns" in layers:
        watched.append(("concerns", ("last_triggered", "created_at")))
    if "jargon" in layers:
        watched.append(("jargon", ("updated_at", "created_at")))
    if "affinity" in layers:
        watched.append(("user_profiles", ("last_seen", "updated_at", "created_at")))
    if "communities" in layers:
        watched.extend([("tags", ("updated_at", "last_seen", "created_at")), ("tag_relations", ("created_at",))])
    return tuple((table, *_safe_table_state(conn, table, columns)) for table, columns in watched)


def build_full_graph_data(layers_raw: str = "facts", *, use_cache: bool = True, min_confidence: float = 0.0) -> dict:
    """构建全量知识图谱数据；供 HTTP API 和启动 warmup 共用，避免启动期 HTTP 自请求。"""
    c = get_container()
    layers = set(layers_raw.split(",")) - {""}
    if not layers:
        layers = {"facts"}
        layers_raw = "facts"

    now = time.time()
    base_cache_key = f"full:{layers_raw}"
    cache_key = f"{base_cache_key}:{min_confidence}"
    conn = c.db.conn
    version = _full_graph_cache_version(conn, layers)
    if (
        use_cache
        and _overview_cache.get(cache_key)
        and _overview_cache.get(f"{cache_key}_version") == version
        and (now - _overview_cache.get(f"{cache_key}_ts", 0)) < 300
    ):
        return _overview_cache[cache_key]

    # 消歧映射
    name_to_qq: dict[str, str] = {}
    qq_to_main: dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT sender_name, sender_id, COUNT(*) FROM memories WHERE sender_id!='' AND sender_name!='' GROUP BY sender_name, sender_id ORDER BY 3 DESC"
        ).fetchall()
        for sname, sid, _ in rows:
            key = (sname or "").strip()[:25]
            if not key or not sid:
                continue
            name_to_qq[key] = sid
            if sid not in qq_to_main:
                qq_to_main[sid] = key
    except Exception:
        pass

    def resolve(n: str) -> str:
        qq = name_to_qq.get(n)
        return qq_to_main.get(qq, n) if qq else n

    edges = []
    seen = set()

    # ─── 图层: facts (facts + tag_relations) ───
    if "facts" in layers:
        tag_rel_cols = _table_columns(conn, "tag_relations")
        fact_cols = _table_columns(conn, "facts")
        try:
            tr_confidence = "tr.confidence" if "confidence" in tag_rel_cols else "NULL"
            tr_metadata = "tr.metadata" if "metadata" in tag_rel_cols else "NULL"
            tr_created = "tr.created_at" if "created_at" in tag_rel_cols else "0"
            for r in conn.execute(
                f"""SELECT tr.id, t1.name, tr.relation_type, t2.name, tr.weight,
                          t1.tag_type, t2.tag_type, {tr_created}, tr.source_tag_id,
                          tr.target_tag_id, {tr_confidence}, {tr_metadata}
                   FROM tag_relations tr
                   JOIN tags t1 ON tr.source_tag_id=t1.id
                   JOIN tags t2 ON tr.target_tag_id=t2.id"""
            ).fetchall():
                conf_val = float(r[10] or 1.0) if r[10] is not None else 1.0
                if conf_val < min_confidence:
                    continue
                s, t = resolve((r[1] or "").strip()[:25]), resolve((r[3] or "").strip()[:25])
                if not s or not t or s == t:
                    continue
                key = (s, t, r[2], "tag_relation", r[0])
                if key not in seen:
                    seen.add(key)
                    edges.append({
                        "id": f"tagrel:{r[0]}",
                        "kind": "tag_relation",
                        "relation_id": r[0],
                        "source_tag_id": r[8],
                        "target_tag_id": r[9],
                        "s": s,
                        "t": t,
                        "l": r[2] or "relates",
                        "w": round(r[4] or 1, 2),
                        "st": r[5] or "topic",
                        "tt": r[6] or "topic",
                        "ts": r[7] or 0,
                        "confidence": conf_val,
                        "metadata": _json_loads_safe(r[11], {}),
                        "editable": True,
                        "layer": "facts",
                    })
        except Exception:
            pass

        try:
            fact_group = "group_id" if "group_id" in fact_cols else "NULL"
            fact_source = "source_memory_id" if "source_memory_id" in fact_cols else "NULL"
            fact_created = "created_at" if "created_at" in fact_cols else "0"
            fact_reinforced = "last_reinforced" if "last_reinforced" in fact_cols else "NULL"
            fact_type = "fact_type" if "fact_type" in fact_cols else "NULL"
            for r in conn.execute(
                f"""SELECT id, subject, predicate, object, confidence, {fact_created},
                          {fact_group}, {fact_source}, {fact_reinforced}, {fact_type}
                   FROM facts WHERE confidence >= ?""",
                (min_confidence,)
            ):
                if not r[1] or not r[3]:
                    continue
                s, t = resolve(r[1].strip()[:25]), resolve(r[3].strip()[:25])
                if not s or not t or s == t:
                    continue
                label = (r[2] or "relates").strip()[:15]
                key = (s, t, label, "fact", r[0])
                if key not in seen:
                    seen.add(key)
                    edges.append({
                        "id": f"fact:{r[0]}",
                        "kind": "fact",
                        "fact_id": r[0],
                        "s": s,
                        "t": t,
                        "l": label,
                        "w": round(r[4] or 1, 2),
                        "st": "entity",
                        "tt": "entity",
                        "ts": r[5] or 0,
                        "group_id": r[6],
                        "source_memory_id": r[7],
                        "last_reinforced": r[8],
                        "fact_type": r[9] or "FACTUAL",
                        "confidence": r[4],
                        "editable": True,
                        "layer": "facts",
                    })
        except Exception:
            pass

    # ─── 图层: beliefs ───
    if "beliefs" in layers:
        try:
            for r in conn.execute("SELECT content, type, strength, bot_id FROM beliefs WHERE status='active'"):
                bot = qq_to_main.get("bot", "bot") if r[3] == "bot" else resolve(r[3] or "bot")
                edges.append({"s": bot, "t": (r[0] or "")[:25], "l": "believes", "w": round(r[2] or 0.5, 2), "st": "person", "tt": "belief", "ts": 0, "layer": "beliefs"})
        except Exception:
            pass

    # ─── 图层: concerns ───
    if "concerns" in layers:
        try:
            for r in conn.execute("SELECT topic, intensity, bot_id FROM concerns"):
                bot = resolve(r[2] or "bot")
                edges.append({"s": bot, "t": (r[0] or "")[:25], "l": "关注", "w": round(r[1] or 0.5, 2), "st": "person", "tt": "concern", "ts": 0, "layer": "concerns"})
        except Exception:
            pass

    # ─── 图层: jargon ───
    if "jargon" in layers:
        try:
            for r in conn.execute("SELECT word, meaning, frequency, group_id FROM jargon WHERE is_jargon=1"):
                edges.append({"s": f"群{r[3]}" if r[3] else "全局", "t": r[0], "l": "黑话", "w": min(r[2] or 1, 10), "st": "entity", "tt": "jargon", "ts": 0, "layer": "jargon"})
        except Exception:
            pass

    # ─── 图层: affinity (好感度) ───
    if "affinity" in layers:
        try:
            for r in conn.execute("SELECT user_id, nickname, affection, bot_id FROM user_profiles WHERE affection != 0 LIMIT 200"):
                person = resolve(r[1] or r[0])
                bot = resolve(r[3] or "bot")
                label = f"好感{r[2]}"
                edges.append({"s": bot, "t": person, "l": label, "w": abs(r[2]) / 20.0, "st": "person", "tt": "person", "ts": 0, "layer": "affinity"})
        except Exception:
            pass

    # ─── 图层: communities ───
    if "communities" in layers and c.cooccurrence:
        try:
            communities = c.cooccurrence.detect_communities(min_community_size=5)
            for cid, members in list(communities.items())[:20]:
                hub = members[0] if members else f"社区{cid}"
                try:
                    row = conn.execute("SELECT name FROM tags WHERE id=?", (hub,)).fetchone()
                    hub_name = row[0] if row else f"tag#{hub}"
                except Exception:
                    hub_name = f"tag#{hub}"
                for mid in members[1:5]:
                    try:
                        row = conn.execute("SELECT name FROM tags WHERE id=?", (mid,)).fetchone()
                        member_name = row[0] if row else f"tag#{mid}"
                    except Exception:
                        member_name = f"tag#{mid}"
                    edges.append({"s": hub_name, "t": member_name, "l": "同社区", "w": 1.0, "st": "topic", "tt": "topic", "ts": 0, "layer": "communities"})
        except Exception:
            pass

    # 标记人物
    for e in edges:
        if e["s"] in name_to_qq:
            e["st"] = "person"
        if e["t"] in name_to_qq:
            e["tt"] = "person"

    data = {"edges": edges, "total": len(edges), "layers": list(layers)}
    _overview_cache[cache_key] = data
    _overview_cache[f"{cache_key}_version"] = version
    _overview_cache[f"{cache_key}_ts"] = now
    # Compatibility aliases for older tests/plugins that keyed full graph cache only by layer set.
    _overview_cache[base_cache_key] = data
    _overview_cache[f"{base_cache_key}_version"] = version
    _overview_cache[f"{base_cache_key}_ts"] = now
    return data


def warmup_kg_cache(layers: str = "facts") -> dict:
    """启动期预热 KG 全量图缓存。失败向上抛给调用方记录 warning。"""
    started = time.perf_counter()
    data = build_full_graph_data(layers, use_cache=False)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {"ok": True, "layers": layers, "edges": int(data.get("total", 0)), "elapsed_ms": elapsed_ms}


@kg_bp.route("/full")
@require_auth
async def kg_full():
    """全量知识图谱数据（按图层返回）。

    参数 layers: 逗号分隔(facts,beliefs,concerns,jargon,affinity,communities)
    默认只返回 facts 图层。前端可勾选多图层叠加。
    """
    layers_raw = request.args.get("layers", "facts")
    return jsonify(build_full_graph_data(layers_raw, use_cache=True))


@kg_bp.route("/entity/<entity_name>/timeline")
@require_auth
async def entity_timeline(entity_name: str):
    """实体时间线：该实体相关的 facts + memories 按时间排列。"""
    c = get_container()
    from urllib.parse import unquote
    name = unquote(entity_name).strip()
    limit = int(request.args.get("limit", 30))

    # 查该实体所有别名（人物消歧）
    names = [name]
    qq_row = c.db.conn.execute(
        "SELECT sender_id FROM memories WHERE sender_name = ? AND sender_id != '' LIMIT 1", (name,)
    ).fetchone()
    if qq_row:
        aliases = c.db.conn.execute(
            "SELECT DISTINCT sender_name FROM memories WHERE sender_id = ? AND sender_name != ''", (qq_row[0],)
        ).fetchall()
        names = list({name} | {a[0] for a in aliases})

    # Facts 时间线
    events = []
    for n in names[:5]:
        rows = c.db.conn.execute(
            "SELECT subject, predicate, object, created_at, source_memory_id FROM facts WHERE (subject=? OR object=?) AND created_at IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (n, n, limit),
        ).fetchall()
        for r in rows:
            events.append({"type": "fact", "ts": r[3], "subject": r[0], "predicate": r[1], "object": r[2], "source_id": r[4]})

    # 关键记忆（按时间）
    if qq_row:
        mem_rows = c.db.conn.execute(
            "SELECT id, content, sender_name, timestamp FROM memories WHERE sender_id = ? ORDER BY timestamp DESC LIMIT ?",
            (qq_row[0], limit),
        ).fetchall()
    else:
        mem_rows = c.db.conn.execute(
            "SELECT m.id, m.content, m.sender_name, m.timestamp FROM memories m JOIN memory_tags mt ON m.id=mt.memory_id JOIN tags t ON mt.tag_id=t.id WHERE t.name=? ORDER BY m.timestamp DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    for r in mem_rows:
        events.append({"type": "memory", "ts": r[3], "id": r[0], "content": r[1], "sender": r[2] or ""})

    # 按时间排序
    events.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    return jsonify({"name": name, "events": events[:limit]})


@kg_bp.route("/path", methods=["POST"])
@require_auth
async def kg_path():
    """多跳路径：两个实体间的最短关系链（BFS on tag_relations + facts）。

    每跳返回关系类型 + 两端实体名，用户能看到 A→关系→B→关系→C 的完整语义链。
    """
    c = get_container()
    body = await request.get_json(silent=True) or {}
    from_name = (body.get("from") or "").strip()
    to_name = (body.get("to") or "").strip()
    max_depth = int(body.get("max_depth", 5))

    if not from_name or not to_name:
        return jsonify({"path": [], "edges": [], "error": "from and to required"})

    # 构建邻接表（name→name，带关系标签）from tag_relations + facts
    adj: dict[str, list[tuple[str, str]]] = {}  # name → [(neighbor, label)]

    # tag_relations
    rel_rows = c.db.conn.execute(
        """SELECT t1.name, tr.relation_type, t2.name FROM tag_relations tr
           JOIN tags t1 ON tr.source_tag_id=t1.id JOIN tags t2 ON tr.target_tag_id=t2.id"""
    ).fetchall()
    for src, rtype, tgt in rel_rows:
        adj.setdefault(src, []).append((tgt, rtype or "relates"))
        adj.setdefault(tgt, []).append((src, rtype or "relates"))

    # facts
    fact_rows = c.db.conn.execute("SELECT subject, predicate, object FROM facts").fetchall()
    for subj, pred, obj in fact_rows:
        if subj and obj:
            adj.setdefault(subj, []).append((obj, pred or "relates"))
            adj.setdefault(obj, []).append((subj, pred or "relates"))

    # BFS
    from collections import deque
    visited: dict[str, tuple] = {from_name: (None, None)}  # name → (parent, edge_label)
    queue = deque([(from_name, 0)])
    found = False

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor, label in adj.get(current, []):
            if neighbor not in visited:
                visited[neighbor] = (current, label)
                if neighbor == to_name:
                    found = True
                    break
                queue.append((neighbor, depth + 1))
        if found:
            break

    if not found:
        return jsonify({"path": [], "edges": [], "nodes": []})

    # 回溯路径
    path_names = []
    path_edges = []
    node = to_name
    while node is not None:
        path_names.append(node)
        parent, label = visited[node]
        if parent is not None:
            path_edges.append({"source": parent, "target": node, "label": label or "relates"})
        node = parent
    path_names.reverse()
    path_edges.reverse()

    # 构建节点（for graph rendering）
    nodes = [{"id": i+1, "name": n, "type": "entity", "degree": 1} for i, n in enumerate(path_names)]

    return jsonify({"path": path_names, "edges": path_edges, "nodes": nodes})


@kg_bp.route("/facts/<int:fact_id>", methods=["DELETE"])
@require_auth
async def delete_fact(fact_id: int):
    """删除 scoped fact；跨 Scope 或 legacy ID 一律不可见。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        _find_scoped_fact(repo, scope, fact_id)
        _delete_scoped(repo, scope, "scoped_facts", fact_id)
        clear_kg_cache()
        return jsonify({"ok": True, "deleted": fact_id, "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@kg_bp.route("/facts/<int:fact_id>", methods=["PUT"])
@require_auth
async def update_fact(fact_id: int):
    """修改 scoped fact；更新保持其原 Scope 与证据归属。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        current = _find_scoped_fact(repo, scope, fact_id)
        updated_id = repo.upsert_scoped_fact(
            scope, subject=body.get("subject", current["subject"]),
            predicate=body.get("predicate", current["predicate"]), object=body.get("object", current["object"]),
            confidence=max(0.0, min(1.0, float(body.get("confidence", current["confidence"])))),
            status=str(body.get("status", current["status"])),
            source_memory_id=body.get("source_memory_id", current.get("source_memory_id")),
            provenance=body.get("provenance", current.get("provenance") or {}),
            valid_from=body.get("valid_from", current.get("valid_from")), valid_until=body.get("valid_until", current.get("valid_until")),
        )
        clear_kg_cache()
        return jsonify({"ok": True, "fact_id": updated_id, "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@kg_bp.route("/tag-relations/<int:rel_id>", methods=["DELETE"])
@require_auth
async def delete_tag_relation(rel_id: int):
    """删除 scoped tag relation；不触及 legacy tag_relations。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        if not any(row["id"] == rel_id for row in _list_scoped_relations(repo, scope)):
            raise LookupError("scoped_object_not_found")
        _delete_scoped(repo, scope, "scoped_tag_relations", rel_id)
        clear_kg_cache()
        return jsonify({"ok": True, "deleted": rel_id, "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@kg_bp.route("/tag-relations/<int:rel_id>", methods=["PUT"])
@require_auth
async def update_tag_relation(rel_id: int):
    """修改 scoped tag relation；关系端点始终保持当前 Scope。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        current = next((row for row in _list_scoped_relations(repo, scope) if row["id"] == rel_id), None)
        if current is None:
            raise LookupError("scoped_object_not_found")
        relation_id = repo.upsert_scoped_tag_relation(
            scope, source_tag_id=current["source_tag_id"], target_tag_id=current["target_tag_id"],
            relation_type=body.get("relation_type", body.get("type", current["relation_type"])),
            weight=float(body.get("weight", current["weight"])), confidence=float(body.get("confidence", current["confidence"])),
            metadata=body.get("metadata", current.get("metadata") or {}),
        )
        clear_kg_cache()
        return jsonify({"ok": True, "relation_id": relation_id, "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@kg_bp.route("/tags/<int:tag_id>", methods=["PUT"])
@require_auth
@_legacy_mutation_disabled
async def update_tag(tag_id: int):
    """修改 tag 节点元信息（name/tag_type/description/aliases）。"""
    c = get_container()
    conn = c.db.conn
    if not _table_exists(conn, "tags"):
        return jsonify({"ok": False, "error": "tags table not found"})
    body = await request.get_json(silent=True) or {}
    columns = _table_columns(conn, "tags")
    sets: list[str] = []
    params: list = []
    if "name" in body and body["name"] is not None:
        name = str(body["name"]).strip()
        if not name:
            return jsonify({"ok": False, "error": "name cannot be empty"}), 400
        sets.append("name = ?")
        params.append(name)
    if "tag_type" in body and body["tag_type"] is not None and "tag_type" in columns:
        tag_type = str(body["tag_type"]).strip() or "keyword"
        sets.append("tag_type = ?")
        params.append(tag_type)
    if "description" in body and "description" in columns:
        sets.append("description = ?")
        params.append(str(body.get("description") or "").strip())
    if "aliases" in body and "aliases" in columns:
        aliases = body.get("aliases") or []
        if isinstance(aliases, list):
            aliases_value = ",".join(str(a).strip() for a in aliases if str(a).strip())
        else:
            aliases_value = str(aliases).strip()
        sets.append("aliases = ?")
        params.append(aliases_value)
    if not sets:
        return jsonify({"ok": False, "error": "No valid fields to update"}), 400
    if "updated_at" in columns:
        sets.append("updated_at = ?")
        params.append(time.time())
    params.append(tag_id)
    try:
        cur = conn.execute(f"UPDATE tags SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if (cur.rowcount or 0) == 0:
        return jsonify({"ok": False, "error": "tag not found"}), 404
    clear_kg_cache()
    return jsonify({"ok": True, "tag_id": tag_id})


@kg_bp.route("/entities/rename-preview", methods=["POST"])
@require_auth
async def rename_entity_preview():
    """预览实体改名会影响的 facts 数量。"""
    c = get_container()
    conn = c.db.conn
    if not _table_exists(conn, "facts"):
        return jsonify({"ok": False, "error": "facts table not found"})
    body = await request.get_json(silent=True) or {}
    old_name = str(body.get("from") or body.get("old") or body.get("source") or "").strip()
    new_name = str(body.get("to") or body.get("new") or body.get("target") or "").strip()
    if not old_name or not new_name:
        return jsonify({"ok": False, "error": "from/to required"}), 400
    subject_count = int(conn.execute("SELECT COUNT(*) FROM facts WHERE subject = ?", (old_name,)).fetchone()[0] or 0)
    object_count = int(conn.execute("SELECT COUNT(*) FROM facts WHERE object = ?", (old_name,)).fetchone()[0] or 0)
    tag_matches = 0
    if _table_exists(conn, "tags"):
        tag_matches = int(conn.execute("SELECT COUNT(*) FROM tags WHERE name = ?", (old_name,)).fetchone()[0] or 0)
    return jsonify({
        "ok": True,
        "from": old_name,
        "to": new_name,
        "subject_facts": subject_count,
        "object_facts": object_count,
        "affected_facts": subject_count + object_count,
        "tag_matches": tag_matches,
        "note": "默认只改 facts；传 sync_tags=true 时会同步改名同名 tag。",
    })


@kg_bp.route("/entities/rename", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def rename_entity():
    """事务性重命名 facts 中的实体 subject/object。"""
    c = get_container()
    conn = c.db.conn
    if not _table_exists(conn, "facts"):
        return jsonify({"ok": False, "error": "facts table not found"})
    body = await request.get_json(silent=True) or {}
    old_name = str(body.get("from") or body.get("old") or body.get("source") or "").strip()
    new_name = str(body.get("to") or body.get("new") or body.get("target") or "").strip()
    if not old_name or not new_name:
        return jsonify({"ok": False, "error": "from/to required"}), 400
    if old_name == new_name:
        return jsonify({"ok": True, "updated_facts": 0, "from": old_name, "to": new_name})
    sync_tags = bool(body.get("sync_tags"))
    try:
        cur_subject = conn.execute("UPDATE facts SET subject = ? WHERE subject = ?", (new_name, old_name))
        cur_object = conn.execute("UPDATE facts SET object = ? WHERE object = ?", (new_name, old_name))
        updated_tags = 0
        if sync_tags and _table_exists(conn, "tags"):
            tag_cols = _table_columns(conn, "tags")
            if "updated_at" in tag_cols:
                cur_tags = conn.execute("UPDATE tags SET name = ?, updated_at = ? WHERE name = ?", (new_name, time.time(), old_name))
            else:
                cur_tags = conn.execute("UPDATE tags SET name = ? WHERE name = ?", (new_name, old_name))
            updated_tags = int(cur_tags.rowcount or 0)
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    updated = int((cur_subject.rowcount or 0) + (cur_object.rowcount or 0))
    clear_kg_cache()
    return jsonify({"ok": True, "updated_facts": updated, "updated_tags": updated_tags, "from": old_name, "to": new_name})
