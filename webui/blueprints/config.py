"""Config Blueprint — 配置管理、热调参、Provider 列表"""

from __future__ import annotations

import json

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

config_bp = Blueprint("config", __name__, url_prefix="/api")


@config_bp.route("/config", methods=["GET"])
@require_auth
async def get_config():
    """返回当前插件运行配置。"""
    c = get_container()
    cfg = c.plugin_config
    return jsonify({
        "embedding_provider_id": cfg.get("embedding_provider_id", ""),
        "embedding_dimension": cfg.get("embedding_dimension", 1024),
        "tag_llm_provider_id": cfg.get("tag_llm_provider_id", ""),
        "query": cfg.get("Query_Settings", {}),
        "tags": cfg.get("Tag_Settings", {}),
        "storage": cfg.get("Storage_Settings", {}),
        "filter": cfg.get("Message_Filter", {}),
        "performance": cfg.get("Performance_Settings", {}),
        "lifecycle": cfg.get("Lifecycle_Settings", {}),
        "webui": {
            "enabled": cfg.get("WebUI_Settings", {}).get("webui_enabled", True),
            "host": cfg.get("WebUI_Settings", {}).get("webui_host", "0.0.0.0"),
            "port": cfg.get("WebUI_Settings", {}).get("webui_port", 7890),
        },
    })


@config_bp.route("/config", methods=["POST"])
@require_auth
async def update_config():
    """更新插件配置。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    cfg = c.plugin_config

    field_map = {
        "embedding_provider_id": "embedding_provider_id",
        "embedding_dimension": "embedding_dimension",
        "tag_llm_provider_id": "tag_llm_provider_id",
        "query": "Query_Settings",
        "tags": "Tag_Settings",
        "storage": "Storage_Settings",
        "filter": "Message_Filter",
        "performance": "Performance_Settings",
        "lifecycle": "Lifecycle_Settings",
    }

    changed = []
    for front_key, cfg_key in field_map.items():
        if front_key in body:
            val = body[front_key]
            if isinstance(val, dict):
                existing = cfg.get(cfg_key, {})
                existing.update(val)
                cfg[cfg_key] = existing
            else:
                cfg[cfg_key] = val
            changed.append(front_key)

    if changed and hasattr(cfg, "save_config"):
        cfg.save_config()

    return jsonify({"ok": True, "changed": changed, "message": "配置已保存，部分参数需重启生效"})


@config_bp.route("/config/hot", methods=["GET"])
@require_auth
async def get_hot_config():
    """返回可热调参数。"""
    from ...services.hot_config import HotConfig
    hot = HotConfig()
    params = hot.get_tunable_params()
    for p in params:
        p["current"] = hot.get(p["key"], p["default"])
    return jsonify({"params": params, "config": hot.get_all()})


@config_bp.route("/config/hot", methods=["POST"])
@require_auth
async def update_hot_config():
    """热更新参数。"""
    from ...services.hot_config import HotConfig
    body = await request.get_json(silent=True) or {}
    hot = HotConfig()
    params_meta = {p["key"]: p for p in hot.get_tunable_params()}
    validated = {}
    errors = []

    for key, value in body.items():
        if key not in params_meta:
            errors.append(f"Unknown param: {key}")
            continue
        meta = params_meta[key]
        try:
            if meta["type"] == "float":
                value = float(value)
            elif meta["type"] == "int":
                value = int(value)
            if value < meta["min"] or value > meta["max"]:
                errors.append(f"{key}: out of range [{meta['min']}, {meta['max']}]")
                continue
            validated[key] = value
        except (ValueError, TypeError) as e:
            errors.append(f"{key}: invalid value - {e}")

    if validated:
        hot.update(validated)
    return jsonify({"ok": len(errors) == 0, "updated": list(validated.keys()), "errors": errors})


@config_bp.route("/providers", methods=["GET"])
@require_auth
async def list_providers():
    """列出可用的 LLM/Embedding Provider。"""
    c = get_container()
    try:
        providers = []
        seen_ids = set()
        embed_id = c.plugin_config.get("embedding_provider_id", "")
        all_provs = c.embedding_service.context.get_all_providers()
        for prov in all_provs:
            try:
                meta = prov.meta()
                if meta.id not in seen_ids:
                    ptype = "embedding" if meta.id == embed_id else (meta.type or "unknown")
                    providers.append({"id": meta.id, "model": meta.model or "", "type": ptype})
                    seen_ids.add(meta.id)
            except Exception:
                pass
        if embed_id and embed_id not in seen_ids:
            providers.insert(0, {"id": embed_id, "model": embed_id.split("/")[-1] if "/" in embed_id else embed_id, "type": "embedding"})
            seen_ids.add(embed_id)
        tag_id = c.plugin_config.get("tag_llm_provider_id", "")
        if tag_id and tag_id not in seen_ids:
            providers.append({"id": tag_id, "model": tag_id.split("/")[-1] if "/" in tag_id else tag_id, "type": "llm"})
        return jsonify({"providers": providers})
    except Exception as e:
        return jsonify({"providers": [], "error": str(e)})
