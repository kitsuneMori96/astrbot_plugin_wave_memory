"""Explore Blueprint — 神经云图多视角 API"""

from __future__ import annotations

from collections import defaultdict, deque

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

explore_bp = Blueprint("explore", __name__, url_prefix="/api/explore")


@explore_bp.route("/galaxy")
@require_auth
async def galaxy():
    """全局星图：社区聚类 + 核心节点。"""
    c = get_container()
    if not c.cooccurrence:
        return jsonify({"nodes": [], "edges": [], "communities": []})
    return jsonify(c.cooccurrence.get_galaxy_data(max_nodes=300, max_edges=800))


@explore_bp.route("/community/<int:community_id>")
@require_auth
async def community(community_id: int):
    """展开某个社区的详细节点。"""
    c = get_container()
    max_nodes = int(request.args.get("max_nodes", 50))
    max_nodes = max(10, min(200, max_nodes))

    if not c.cooccurrence:
        return jsonify({"nodes": [], "edges": []})

    communities = c.cooccurrence.detect_communities(min_community_size=5)
    if community_id not in communities:
        return jsonify({"nodes": [], "edges": []})

    members = communities[community_id]
    degree: dict = {}
    for m in members:
        d = len(c.cooccurrence.forward.get(m, {})) + len(c.cooccurrence.backward.get(m, {}))
        degree[m] = d

    sorted_members = sorted(members, key=lambda n: degree.get(n, 0), reverse=True)[:max_nodes]
    selected = set(sorted_members)

    edges = []
    for src in selected:
        for tgt, w in c.cooccurrence.forward.get(src, {}).items():
            if tgt in selected and w >= 0.03:
                edges.append({"source": src, "target": tgt, "weight": round(w, 3)})

    if selected:
        placeholders = ",".join("?" * len(selected))
        rows = c.db.conn.execute(
            f"SELECT id, name, tag_type FROM tags WHERE id IN ({placeholders})",
            list(selected),
        ).fetchall()
        nodes = [{"id": r[0], "name": r[1], "type": r[2], "degree": degree.get(r[0], 0), "community": community_id} for r in rows]
    else:
        nodes = []

    return jsonify({"nodes": nodes, "edges": edges})


@explore_bp.route("/person/<qq_id>")
@require_auth
async def person(qq_id: str):
    """人物记忆网络。"""
    c = get_container()
    max_memories = int(request.args.get("max_memories", 80))
    max_memories = max(10, min(200, max_memories))

    person_row = c.db.conn.execute(
        "SELECT qq_id, display_name, message_count FROM person_registry WHERE qq_id = ?",
        (qq_id,),
    ).fetchone()
    if not person_row:
        return jsonify({"person": None, "nodes": [], "edges": []})

    rows = c.db.conn.execute(
        """SELECT m.id, m.content, m.sender_name, m.timestamp
           FROM memories m WHERE m.sender_id = ?
           ORDER BY m.timestamp DESC LIMIT ?""",
        (qq_id, max_memories),
    ).fetchall()

    if not rows:
        return jsonify({"person": {"id": qq_id, "name": person_row[1], "count": person_row[2]}, "nodes": [], "edges": []})

    mem_ids = [r[0] for r in rows]
    nodes = [
        {"id": f"m{r[0]}", "memId": r[0], "name": (r[2] or "")[:6] + ": " + (r[1] or "")[:20],
         "content": r[1] or "", "sender": r[2] or "", "ts": r[3], "type": "memory"}
        for r in rows
    ]

    # Tag 关联
    placeholders = ",".join("?" * len(mem_ids))
    tag_rows = c.db.conn.execute(
        f"""SELECT mt.memory_id, t.id, t.name, t.tag_type
            FROM memory_tags mt JOIN tags t ON mt.tag_id = t.id
            WHERE mt.memory_id IN ({placeholders})""",
        mem_ids,
    ).fetchall()

    mem_tags: dict = defaultdict(set)
    tag_info: dict = {}
    for tr in tag_rows:
        mem_tags[tr[0]].add(tr[1])
        tag_info[tr[1]] = {"id": f"t{tr[1]}", "tagId": tr[1], "name": tr[2], "type": tr[3]}

    tag_count: dict = defaultdict(int)
    for tags in mem_tags.values():
        for t in tags:
            tag_count[t] += 1

    shared_tags = {t for t, cnt in tag_count.items() if cnt >= 2}
    for t in list(shared_tags)[:50]:
        if t in tag_info:
            nodes.append(tag_info[t])

    edges = []
    for mid, tags in mem_tags.items():
        for t in tags:
            if t in shared_tags:
                edges.append({"source": f"m{mid}", "target": f"t{t}", "weight": 0.5})

    return jsonify({
        "person": {"id": qq_id, "name": person_row[1], "count": person_row[2]},
        "nodes": nodes,
        "edges": edges,
    })


@explore_bp.route("/persons")
@require_auth
async def persons():
    """人物列表。"""
    c = get_container()
    limit = int(request.args.get("limit", 30))
    limit = max(5, min(100, limit))
    rows = c.db.conn.execute(
        "SELECT qq_id, display_name, message_count FROM person_registry ORDER BY message_count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return jsonify([{"id": r[0], "name": r[1], "count": r[2]} for r in rows])


@explore_bp.route("/path", methods=["POST"])
@require_auth
async def path_find():
    """路径查找：两个 Tag 之间的最短路径（BFS）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    source_id = body.get("source_id")
    target_id = body.get("target_id")
    max_depth = body.get("max_depth", 5)

    if not source_id or not target_id or not c.cooccurrence:
        return jsonify({"path": [], "nodes": [], "edges": []})

    visited = {source_id: None}
    queue = deque([(source_id, 0)])
    found = False

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        neighbors = set(c.cooccurrence.forward.get(current, {}).keys()) | set(c.cooccurrence.backward.get(current, {}).keys())
        for nb in neighbors:
            if nb not in visited:
                visited[nb] = current
                if nb == target_id:
                    found = True
                    break
                queue.append((nb, depth + 1))
        if found:
            break

    if not found:
        return jsonify({"path": [], "nodes": [], "edges": []})

    # 回溯路径
    path = []
    node = target_id
    while node is not None:
        path.append(node)
        node = visited[node]
    path.reverse()

    path_set = set(path)
    if path_set:
        placeholders = ",".join("?" * len(path_set))
        rows = c.db.conn.execute(
            f"SELECT id, name, tag_type FROM tags WHERE id IN ({placeholders})", list(path_set)
        ).fetchall()
        nodes = [{"id": r[0], "name": r[1], "type": r[2]} for r in rows]
    else:
        nodes = []

    edges = []
    for i in range(len(path) - 1):
        w = c.cooccurrence.forward.get(path[i], {}).get(path[i + 1], 0)
        if w == 0:
            w = c.cooccurrence.forward.get(path[i + 1], {}).get(path[i], 0)
        edges.append({"source": path[i], "target": path[i + 1], "weight": round(w, 3)})

    return jsonify({"path": path, "nodes": nodes, "edges": edges})
