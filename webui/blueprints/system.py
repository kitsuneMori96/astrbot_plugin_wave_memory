"""System Blueprint — 系统状态、健康检查"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

try:
    from quart import Blueprint, jsonify, request
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时的轻量兜底
    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    def jsonify(value=None, **kwargs):  # type: ignore[no-redef]
        return value if value is not None else kwargs

    class _Request:
        args: dict = {}
    request = _Request()  # type: ignore[assignment]

from ..container import get_container
try:
    from ..middleware.auth import require_auth
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时直接放行
    def require_auth(func):
        return func

system_bp = Blueprint("system", __name__, url_prefix="/api")


def _get_services_health(c) -> list:
    """从健康注册表获取所有服务状态（main.py 初始化时注册）。"""
    try:
        from ...utils.health_registry import get_all_services
        services = get_all_services()
        if services:
            return services
    except Exception:
        pass
    # fallback：如果 registry 为空,用旧逻辑
    svcs = []
    def _add(name, obj, reason_if_none="未加载"):
        if obj:
            svcs.append({"name": name, "status": "ok", "reason": ""})
        else:
            svcs.append({"name": name, "status": "off", "reason": reason_if_none})
    _add("向量索引", c.memory_index)
    _add("Embedding", c.embedding_service)
    _add("Tag 提取", c.tag_extractor)
    return svcs


@system_bp.route("/system", methods=["GET"])
@require_auth
async def system_status():
    """系统健康状态。"""
    c = get_container()
    total_mem = c.db.get_memory_count()
    with_vec = c.db.get_memory_count_with_vector()
    total_tags = c.db.get_tag_count()

    # DB 体积监控 (v1.1.0 #4.4)
    db_size_mb = 0.0
    try:
        db_file = getattr(c.db, 'db_path', '')
        if db_file and os.path.isfile(db_file):
            db_size_mb = round(os.path.getsize(db_file) / (1024 * 1024), 1)
            # WAL 文件也加上
            wal_file = db_file + '-wal'
            if os.path.isfile(wal_file):
                db_size_mb += round(os.path.getsize(wal_file) / (1024 * 1024), 1)
    except Exception:
        pass

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

    # v1.5.0: 认知体系统计
    active_users = c.db.conn.execute(
        "SELECT COUNT(*) FROM user_profiles WHERE interaction_count > 0"
    ).fetchone()[0]
    top_users = c.db.conn.execute(
        "SELECT nickname, interaction_count FROM user_profiles WHERE interaction_count > 0 ORDER BY interaction_count DESC LIMIT 5"
    ).fetchall()

    # 内心念头（来自 DesireEngine）
    unspoken_desire = None
    if getattr(c, "desire_engine", None):
        try:
            active = c.desire_engine._active_desires
            if active:
                strongest = max(active, key=lambda d: d.intensity)
                unspoken_desire = {
                    "topic": strongest.trigger,
                    "motive": strongest.type,
                    "intensity": strongest.intensity,
                }
        except Exception:
            pass

    return jsonify({
        "memories": {"total": total_mem, "with_vector": with_vec, "with_tags": tagged_memories},
        "tags": {"total": total_tags, "structured": structured_tags, "type_distribution": {r[0]: r[1] for r in type_dist}},
        "coverage": {
            "vector_pct": round(with_vec / total_mem * 100, 1) if total_mem > 0 else 0,
            "tag_pct": round(tagged_memories / total_mem * 100, 1) if total_mem > 0 else 0,
        },
        "cooccurrence": {"nodes": cooc_nodes, "edges": cooc_edges},
        "db_size_mb": db_size_mb,
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
            "active_users": active_users,
            "top_users": [{"name": r[0] or "?", "interactions": r[1]} for r in top_users],
            "active_moods": [{"group_id": m[0], "type": m[1], "intensity": m[2], "desc": m[3]} for m in active_moods],
            "unspoken_desire": unspoken_desire,
        },
    })


@system_bp.route("/errors", methods=["GET"])
@require_auth
async def recent_errors():
    """最近运行时错误/警告（供 WebUI 展示）。"""
    try:
        from ...utils.health_registry import get_recent_errors, error_count
        return jsonify({"errors": get_recent_errors(30), "total": error_count()})
    except Exception:
        return jsonify({"errors": [], "total": 0})


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


def _parse_injection_metric_range():
    """解析注入指标查询时间范围。"""
    now = time.time()
    preset = request.args.get("range", "7d")
    preset_seconds = {
        "1d": 86400,
        "3d": 3 * 86400,
        "7d": 7 * 86400,
        "1mo": 31 * 86400,
    }

    from_arg = request.args.get("from")
    to_arg = request.args.get("to")
    if from_arg and to_arg:
        try:
            start = datetime.strptime(from_arg, "%Y-%m-%d")
            end = datetime.strptime(to_arg, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
            from_ts = start.timestamp()
            to_ts = min(end.timestamp(), now)
            span = max(0, to_ts - from_ts)
            range_key = "custom"
        except Exception:
            span = preset_seconds["7d"]
            from_ts = now - span
            to_ts = now
            range_key = "7d"
    else:
        range_key = preset if preset in preset_seconds else "7d"
        span = preset_seconds[range_key]
        from_ts = now - span
        to_ts = now

    if range_key == "1d" or span <= 2 * 86400:
        bucket_seconds = 3600
    elif range_key == "3d":
        bucket_seconds = 3 * 3600
    elif range_key == "7d" or span <= 14 * 86400:
        bucket_seconds = 6 * 3600
    else:
        bucket_seconds = 86400
    return range_key, from_ts, to_ts, bucket_seconds


def build_injection_metrics_payload(db, stats: dict | None, *, range_key: str, from_ts: float, to_ts: float, bucket_seconds: int) -> dict:
    """构造旧聚合注入指标 API payload，便于在无 Quart 环境下集成测试。"""
    payload = dict(stats or {})
    try:
        persisted = db.get_injection_metrics(from_ts, to_ts, bucket_seconds)
        payload.update({
            "range": range_key,
            "from": from_ts,
            "to": to_ts,
            "bucket_seconds": bucket_seconds,
            "summary": persisted.get("summary", {}),
            "series": persisted.get("series", []),
            "ranking": persisted.get("ranking", []),
            "count": persisted.get("count", payload.get("count", 0)),
        })
    except Exception as e:
        payload.update({
            "range": range_key,
            "from": from_ts,
            "to": to_ts,
            "bucket_seconds": bucket_seconds,
            "summary": {},
            "series": [],
            "ranking": [],
            "error": str(e),
        })
    return payload


@system_bp.route("/metrics/injection", methods=["GET"])
@require_auth
async def metrics_injection():
    """inject_memory 各通道聚合统计 (US-3.2)。"""
    from ...utils.perf import get_perf_tracker
    tracker = get_perf_tracker()
    stats = tracker.get_injection_stats()
    c = get_container()
    range_key, from_ts, to_ts, bucket_seconds = _parse_injection_metric_range()
    return jsonify(build_injection_metrics_payload(
        c.db,
        stats,
        range_key=range_key,
        from_ts=from_ts,
        to_ts=to_ts,
        bucket_seconds=bucket_seconds,
    ))


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
