"""Explore 产品入口的 scoped 只读 API。"""

from __future__ import annotations

from collections import deque

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

try:
    from ...domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from ...engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError
except ImportError:  # pragma: no cover - plugin root may be imported directly
    from domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError


explore_bp = Blueprint("explore", __name__, url_prefix="/api/explore")


def _scope_error(code: str, status: int = 400):
    return jsonify({"error": {"code": code}}), status


def _scope_failure(exc: Exception):
    code = getattr(exc, "reason_code", None) or getattr(exc, "code", None) or "invalid_scope"
    return _scope_error(str(code), 400 if code == "scope_required" else 422)


def _scope_from_query() -> RuntimeScope:
    required = ("bot_id", "session_id", "visibility")
    values = {field: request.args.get(field) for field in required}
    if any(value is None or str(value).strip() == "" for value in values.values()):
        raise ScopedKnowledgeScopeError("scope_required")
    if values["visibility"] != "group":
        raise ScopedKnowledgeScopeError("derived_scope_visibility_unsupported")
    try:
        platform_id, kind, conversation_id = str(values["session_id"]).split(":", 2)
    except ValueError as exc:
        raise ScopeValidationError("invalid_session_id", "session_id must be canonical") from exc
    return RuntimeScope(
        bot_id=str(values["bot_id"]),
        visibility="group",
        session=SessionRef(str(values["session_id"]), platform_id, kind, conversation_id),
    )


def _scope_params(scope: RuntimeScope) -> tuple[str, str, str]:
    assert scope.session is not None
    return scope.bot_id, scope.session.id, scope.visibility


def _conn():
    return getattr(getattr(get_container(), "db", None), "conn", None)


def _table_columns(conn, table: str) -> set[str]:
    if conn is None:
        return set()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return set()
        return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _canonical_memory_store(conn) -> bool:
    required = {
        "id", "sender_id", "sender_name", "content", "timestamp", "bot_id",
        "session_id", "visibility", "resolution_state", "quarantine",
    }
    return required <= _table_columns(conn, "memories")


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _empty_graph(scope: RuntimeScope) -> dict:
    return {"nodes": [], "edges": [], "scope": ScopeCodec.to_dict(scope), "read_only": True}


def _scoped_graph(conn, scope: RuntimeScope, *, min_confidence: float = 0.0) -> dict:
    params = _scope_params(scope)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    if {"bot_id", "session_id", "visibility"} <= _table_columns(conn, "scoped_facts"):
        rows = conn.execute(
            """SELECT id, subject, predicate, object, confidence, created_at
                 FROM scoped_facts
                WHERE bot_id=? AND session_id=? AND visibility=? AND confidence>=?
                ORDER BY updated_at DESC, id DESC LIMIT 2000""",
            (*params, min_confidence),
        ).fetchall()
        for row in rows:
            source, target = str(row[1] or "").strip(), str(row[3] or "").strip()
            if not source or not target:
                continue
            nodes.setdefault(source, {"id": source, "name": source, "type": "entity"})
            nodes.setdefault(target, {"id": target, "name": target, "type": "entity"})
            edges.append({
                "id": f"fact:{row[0]}", "source": source, "target": target,
                "label": row[2] or "relates", "weight": float(row[4] or 0),
                "confidence": float(row[4] or 0), "ts": row[5] or 0, "kind": "fact",
                "read_only": True,
            })
    required_relation = {"bot_id", "session_id", "visibility", "source_tag_id", "target_tag_id"}
    if required_relation <= _table_columns(conn, "scoped_tag_relations") and required_relation - {"source_tag_id", "target_tag_id"} <= _table_columns(conn, "scoped_tags"):
        rows = conn.execute(
            """SELECT r.id, source.name, r.relation_type, target.name, r.weight, r.confidence,
                      source.tag_type, target.tag_type, r.created_at
                 FROM scoped_tag_relations r
                 JOIN scoped_tags source ON source.id=r.source_tag_id
                  AND source.bot_id=r.bot_id AND source.session_id=r.session_id AND source.visibility=r.visibility
                 JOIN scoped_tags target ON target.id=r.target_tag_id
                  AND target.bot_id=r.bot_id AND target.session_id=r.session_id AND target.visibility=r.visibility
                WHERE r.bot_id=? AND r.session_id=? AND r.visibility=? AND r.confidence>=?
                ORDER BY r.updated_at DESC, r.id DESC LIMIT 2000""",
            (*params, min_confidence),
        ).fetchall()
        for row in rows:
            source, target = str(row[1] or "").strip(), str(row[3] or "").strip()
            if not source or not target:
                continue
            nodes.setdefault(source, {"id": source, "name": source, "type": row[6] or "topic"})
            nodes.setdefault(target, {"id": target, "name": target, "type": row[7] or "topic"})
            edges.append({
                "id": f"tagrel:{row[0]}", "source": source, "target": target,
                "label": row[2] or "relates", "weight": float(row[4] or 0),
                "confidence": float(row[5] or 0), "ts": row[8] or 0,
                "kind": "tag_relation", "read_only": True,
            })
    degree: dict[str, int] = {name: 0 for name in nodes}
    for edge in edges:
        degree[edge["source"]] = degree.get(edge["source"], 0) + 1
        degree[edge["target"]] = degree.get(edge["target"], 0) + 1
    for name, node in nodes.items():
        node["degree"] = degree.get(name, 0)
    return {
        "nodes": list(nodes.values()), "edges": edges,
        "scope": ScopeCodec.to_dict(scope), "read_only": True,
    }


@explore_bp.route("/galaxy")
@require_auth
async def galaxy():
    try:
        scope = _scope_from_query()
        conn = _conn()
        if conn is None:
            return jsonify(_empty_graph(scope))
        return jsonify(_scoped_graph(conn, scope))
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@explore_bp.route("/community/<int:community_id>")
@require_auth
async def community(community_id: int):
    """Legacy community ID 没有 scoped 规范对象，终止该读路径。"""
    try:
        _scope_from_query()
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)
    return _scope_error("legacy_object_unavailable", 410)


@explore_bp.route("/person/<qq_id>")
@require_auth
async def person(qq_id: str):
    try:
        scope = _scope_from_query()
        conn = _conn()
        if not _canonical_memory_store(conn):
            return jsonify({"person": None, "nodes": [], "edges": [], "memories": [], "scope": ScopeCodec.to_dict(scope), "read_only": True})
        limit = _bounded_int("max_memories", 80, 1, 200)
        rows = conn.execute(
            """SELECT id, content, sender_name, timestamp, version
                 FROM memories
                WHERE sender_id=? AND bot_id=? AND session_id=? AND visibility=?
                  AND resolution_state='resolved' AND COALESCE(quarantine,0)=0
                ORDER BY timestamp DESC LIMIT ?""",
            (qq_id, *_scope_params(scope), limit),
        ).fetchall()
        display_name = next((row[2] for row in rows if row[2]), qq_id)
        person_node_id = f"p{qq_id}"
        memories = [
            {"id": row[0], "content": row[1] or "", "sender": row[2] or "", "ts": row[3], "revision": row[4]}
            for row in rows
        ]
        nodes = [{"id": person_node_id, "name": display_name, "type": "person", "degree": len(rows), "qq_id": qq_id, "isSeed": True}]
        nodes.extend({"id": f"m{row[0]}", "memId": row[0], "name": (row[1] or "")[:24], "content": row[1] or "", "sender": row[2] or "", "ts": row[3], "type": "memory"} for row in rows)
        edges = [{"source": person_node_id, "target": f"m{row[0]}", "label": "记忆", "weight": 1.0} for row in rows]
        return jsonify({
            "person": {"id": qq_id, "name": display_name, "count": len(rows)},
            "nodes": nodes, "edges": edges, "memories": memories,
            "scope": ScopeCodec.to_dict(scope), "read_only": True,
        })
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@explore_bp.route("/tag/<int:tag_id>/memories")
@require_auth
async def tag_memories(tag_id: int):
    try:
        scope = _scope_from_query()
        conn = _conn()
        params = _scope_params(scope)
        if not _canonical_memory_store(conn) or not {"bot_id", "session_id", "visibility"} <= _table_columns(conn, "scoped_memory_tags"):
            return jsonify({"tag": None, "memories": [], "scope": ScopeCodec.to_dict(scope), "read_only": True})
        limit = _bounded_int("limit", 20, 1, 50)
        tag = conn.execute(
            "SELECT id, name, tag_type, confidence FROM scoped_tags WHERE id=? AND bot_id=? AND session_id=? AND visibility=?",
            (tag_id, *params),
        ).fetchone()
        if tag is None:
            return jsonify({"tag": None, "memories": [], "scope": ScopeCodec.to_dict(scope), "read_only": True})
        rows = conn.execute(
            """SELECT m.id, m.content, m.sender_name, m.group_id, m.timestamp, m.version
                 FROM scoped_memory_tags mt
                 JOIN memories m ON m.id=mt.memory_id
                  AND m.bot_id=mt.bot_id AND m.session_id=mt.session_id AND m.visibility=mt.visibility
                WHERE mt.tag_id=? AND mt.bot_id=? AND mt.session_id=? AND mt.visibility=?
                  AND m.resolution_state='resolved' AND COALESCE(m.quarantine,0)=0
                ORDER BY m.timestamp DESC LIMIT ?""",
            (tag_id, *params, limit),
        ).fetchall()
        return jsonify({
            "tag": {"id": tag[0], "name": tag[1], "type": tag[2], "confidence": tag[3]},
            "memories": [{"id": row[0], "content": row[1] or "", "sender": row[2] or "", "group_id": row[3] or "", "ts": row[4], "revision": row[5]} for row in rows],
            "scope": ScopeCodec.to_dict(scope), "read_only": True,
        })
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@explore_bp.route("/persons")
@require_auth
async def persons():
    try:
        scope = _scope_from_query()
        conn = _conn()
        if not _canonical_memory_store(conn):
            return jsonify([])
        limit = _bounded_int("limit", 30, 1, 100)
        rows = conn.execute(
            """SELECT sender_id, MAX(sender_name), COUNT(*) AS cnt
                 FROM memories
                WHERE bot_id=? AND session_id=? AND visibility=?
                  AND resolution_state='resolved' AND COALESCE(quarantine,0)=0
                  AND sender_id IS NOT NULL AND sender_id!=''
                GROUP BY sender_id ORDER BY cnt DESC LIMIT ?""",
            (*_scope_params(scope), limit),
        ).fetchall()
        return jsonify([{"id": row[0], "name": row[1] or row[0], "count": row[2]} for row in rows])
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@explore_bp.route("/path", methods=["POST"])
@require_auth
async def path_find():
    try:
        scope = _scope_from_query()
        body = await request.get_json(silent=True) or {}
        source = str(body.get("source_id") or body.get("from") or "").strip()
        target = str(body.get("target_id") or body.get("to") or "").strip()
        if not source or not target:
            return jsonify({"path": [], "nodes": [], "edges": [], "scope": ScopeCodec.to_dict(scope), "read_only": True})
        graph = _scoped_graph(_conn(), scope)
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in graph["edges"]:
            adjacency.setdefault(str(edge["source"]), []).append((str(edge["target"]), str(edge["label"])))
            adjacency.setdefault(str(edge["target"]), []).append((str(edge["source"]), str(edge["label"])))
        visited: dict[str, tuple[str | None, str | None]] = {source: (None, None)}
        queue = deque([(source, 0)])
        max_depth = max(1, min(8, int(body.get("max_depth", 5))))
        while queue and target not in visited:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for neighbor, label in adjacency.get(current, []):
                if neighbor not in visited:
                    visited[neighbor] = (current, label)
                    queue.append((neighbor, depth + 1))
        if target not in visited:
            return jsonify({"path": [], "nodes": [], "edges": [], "scope": ScopeCodec.to_dict(scope), "read_only": True})
        names, edges = [], []
        node = target
        while node is not None:
            names.append(node)
            parent, label = visited[node]
            if parent is not None:
                edges.append({"source": parent, "target": node, "label": label})
            node = parent
        names.reverse()
        edges.reverse()
        return jsonify({
            "path": names,
            "nodes": [{"id": index + 1, "name": name, "type": "entity", "degree": 1} for index, name in enumerate(names)],
            "edges": edges, "scope": ScopeCodec.to_dict(scope), "read_only": True,
        })
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)
