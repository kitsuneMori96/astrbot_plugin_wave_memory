"""System Blueprint — 系统状态、健康检查"""

from __future__ import annotations

from quart import Blueprint, jsonify

from ..container import get_container
from ..middleware.auth import require_auth

system_bp = Blueprint("system", __name__, url_prefix="/api")


def _get_services_health(c) -> list:
    """汇总所有服务的健康状态+降级原因（供前端可视化）。"""
    svcs = []

    def _add(name, obj, reason_if_none="未加载"):
        if obj:
            svcs.append({"name": name, "status": "ok", "reason": ""})
        else:
            svcs.append({"name": name, "status": "off", "reason": reason_if_none})

    # 核心引擎
    _add("向量索引", c.memory_index, "memory_index 未初始化")
    _add("Tag 索引", c.tag_index, "tag_index 未初始化")
    _add("共现矩阵", c.cooccurrence, "cooccurrence 未加载")
    _add("脉冲传播", c.spike_router, "spike_router 未加载")
    _add("残差金字塔", c.residual_pyramid, "residual_pyramid 未加载")
    _add("测地线重排", c.geodesic, "geodesic 未加载")
    _add("Embedding", c.embedding_service, "embedding_provider_id 未配置")
    _add("Tag 提取", c.tag_extractor, "tag_llm_provider_id 未配置")

    # EPA 特殊处理
    if c.epa and c.epa.initialized:
        svcs.append({"name": "EPA 基底", "status": "ok", "reason": ""})
    else:
        svcs.append({"name": "EPA 基底", "status": "degraded", "reason": f"需 ≥{c.epa.min_tags if c.epa else 20} 个 tag 向量"})

    # 灵魂层（从 plugin_config 拿不到实例,改用 db 表行数判断活跃度）
    try:
        belief_cnt = c.db.conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
        svcs.append({"name": "信念引擎", "status": "ok" if belief_cnt > 0 else "degraded", "reason": "" if belief_cnt else "尚无信念数据（等待 consolidation）"})
    except Exception:
        svcs.append({"name": "信念引擎", "status": "off", "reason": "beliefs 表不可用"})

    try:
        jargon_cnt = c.db.conn.execute("SELECT COUNT(*) FROM jargon WHERE is_jargon=1").fetchone()[0]
        svcs.append({"name": "黑话系统", "status": "ok" if jargon_cnt > 0 else "degraded", "reason": "" if jargon_cnt else "尚无黑话（需群内累积对话）"})
    except Exception:
        svcs.append({"name": "黑话系统", "status": "off", "reason": "jargon 表不可用"})

    try:
        concern_cnt = c.db.conn.execute("SELECT COUNT(*) FROM concerns").fetchone()[0]
        svcs.append({"name": "关切追踪", "status": "ok" if concern_cnt > 0 else "degraded", "reason": "" if concern_cnt else "尚无关切（需 MetaThinking 触发）"})
    except Exception:
        svcs.append({"name": "关切追踪", "status": "off", "reason": "concerns 表不可用"})

    return svcs


@system_bp.route("/system", methods=["GET"])
@require_auth
async def system_status():
    """系统健康状态。"""
    c = get_container()
    total_mem = c.db.get_memory_count()
    with_vec = c.db.get_memory_count_with_vector()
    total_tags = c.db.get_tag_count()

    tagged_memories = c.db.conn.execute(
        "SELECT COUNT(DISTINCT memory_id) FROM memory_tags"
    ).fetchone()[0]

    structured_tags = c.db.conn.execute(
        "SELECT COUNT(*) FROM tags WHERE tag_type != 'keyword'"
    ).fetchone()[0]

    type_dist = c.db.conn.execute(
        "SELECT tag_type, COUNT(*) FROM tags GROUP BY tag_type ORDER BY COUNT(*) DESC"
    ).fetchall()

    cooc_nodes = c.cooccurrence.node_count if c.cooccurrence else 0
    cooc_edges = c.cooccurrence.edge_count if c.cooccurrence else 0

    facts_count = c.db.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    active_moods = c.db.conn.execute(
        "SELECT group_id, mood_type, intensity, description FROM bot_mood WHERE is_active = 1 AND typeof(start_time) IN ('real', 'integer')"
    ).fetchall()
    person_count = c.db.conn.execute("SELECT COUNT(*) FROM person_registry").fetchone()[0]
    user_profiles_count = c.db.conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]

    return jsonify({
        "memories": {"total": total_mem, "with_vector": with_vec, "with_tags": tagged_memories},
        "tags": {"total": total_tags, "structured": structured_tags, "type_distribution": {r[0]: r[1] for r in type_dist}},
        "coverage": {
            "vector_pct": round(with_vec / total_mem * 100, 1) if total_mem > 0 else 0,
            "tag_pct": round(tagged_memories / total_mem * 100, 1) if total_mem > 0 else 0,
        },
        "cooccurrence": {"nodes": cooc_nodes, "edges": cooc_edges},
        "epa": {
            "initialized": c.epa.initialized if c.epa else False,
            "reason": "" if (c.epa and c.epa.initialized) else (
                f"需要至少 {c.epa.min_tags} 个带向量的 tag（当前数据不足，持续聊天自动积累后就绪）" if c.epa else "EPA 模块未加载"
            ),
        },
        "services_health": _get_services_health(c),
        "lifecycle": {
            "facts": facts_count,
            "persons": person_count,
            "user_profiles": user_profiles_count,
            "active_moods": [{"group_id": m[0], "type": m[1], "intensity": m[2], "desc": m[3]} for m in active_moods],
        },
    })


@system_bp.route("/health", methods=["GET"])
@require_auth
async def health_check():
    """服务健康检查 (US-3.3)。"""
    c = get_container()
    services = {}

    # DB
    try:
        c.db.conn.execute("SELECT 1").fetchone()
        services["database"] = {"status": "ok"}
    except Exception as e:
        services["database"] = {"status": "error", "error": str(e)}

    # Embedding
    services["embedding"] = {"status": "ok" if c.embedding_service else "unavailable"}

    # Memory Index
    count = getattr(c.memory_index, "count", 0)
    services["memory_index"] = {"status": "ok", "count": count}

    # Tag Index
    tag_count = getattr(c.tag_index, "count", 0) if c.tag_index else 0
    services["tag_index"] = {"status": "ok" if c.tag_index else "unavailable", "count": tag_count}

    # Cooccurrence
    services["cooccurrence"] = {"status": "ok" if c.cooccurrence else "unavailable"}

    overall = "healthy" if all(s.get("status") == "ok" for s in services.values()) else "degraded"
    return jsonify({"status": overall, "services": services})


@system_bp.route("/metrics", methods=["GET"])
@require_auth
async def metrics():
    """运行指标 (US-1.5)。"""
    c = get_container()
    return jsonify({
        "memory_count": c.db.get_memory_count(),
        "tag_count": c.db.get_tag_count(),
        "index_count": getattr(c.memory_index, "count", 0),
        "cooccurrence_nodes": c.cooccurrence.node_count if c.cooccurrence else 0,
        "cooccurrence_edges": c.cooccurrence.edge_count if c.cooccurrence else 0,
    })


@system_bp.route("/metrics/injection", methods=["GET"])
@require_auth
async def metrics_injection():
    """inject_memory 各通道聚合统计 (US-3.2)。"""
    from ...utils.perf import get_perf_tracker
    tracker = get_perf_tracker()
    return jsonify(tracker.get_injection_stats())


@system_bp.route("/metrics/functions", methods=["GET"])
@require_auth
async def metrics_functions():
    """所有 @monitored 函数的 p50/p95 统计。"""
    from ...utils.perf import get_perf_tracker
    tracker = get_perf_tracker()
    return jsonify({"functions": tracker.get_all_stats()})


@system_bp.route("/metrics/cache", methods=["GET"])
@require_auth
async def metrics_cache():
    """缓存命中率统计。"""
    from ...utils.cache import get_cache_manager
    cache = get_cache_manager()
    return jsonify(cache.get_hit_rates())
