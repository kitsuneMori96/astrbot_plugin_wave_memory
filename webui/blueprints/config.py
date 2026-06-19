"""Config Blueprint — 配置管理、热调参、Provider 列表"""

from __future__ import annotations

import json
import os
from pathlib import Path

from quart import Blueprint, jsonify, request

from ..container import get_container


# HotConfig key → config.json section.key 映射
_HOT_TO_CONFIG_MAP = {
    "query.group_weight_current": ("Social_Settings", "group_weight_current"),
    "query.group_weight_cross": ("Social_Settings", "group_weight_cross"),
    "social.abuse_trigger_count": ("Social_Settings", "abuse_trigger_count"),
    "social.abuse_cooldown_base": ("Social_Settings", "abuse_cooldown_base"),
    "social.abuse_cooldown_max": ("Social_Settings", "abuse_cooldown_max"),
    "social.aba_window_seconds": ("Social_Settings", "aba_window_seconds"),
    "query.min_similarity": ("Query_Settings", "min_similarity"),
}


def _persist_hot_config_to_file(validated: dict):
    """将 HotConfig 变更写回 AstrBot config.json（持久化）。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        config_path = os.path.join(get_astrbot_data_path(), "config", "astrbot_plugin_wave_memory_config.json")
        if not os.path.isfile(config_path):
            return

        with open(config_path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)

        changed = False
        for hot_key, value in validated.items():
            if hot_key in _HOT_TO_CONFIG_MAP:
                section, field = _HOT_TO_CONFIG_MAP[hot_key]
                if section not in cfg:
                    cfg[section] = {}
                cfg[section][field] = value
                changed = True

        if changed:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
from ..middleware.auth import require_auth

config_bp = Blueprint("config", __name__, url_prefix="/api")

# 插件根目录下的 _conf_schema.json（webui/blueprints/config.py 往上 2 级）
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "_conf_schema.json"


def _load_schema() -> dict:
    """读取插件配置 schema。"""
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _coerce(value, type_name: str):
    """按 schema 声明的类型转换前端传入的值。"""
    try:
        if type_name == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if type_name == "int":
            if isinstance(value, bool):
                return int(value)
            return int(float(value)) if value != "" and value is not None else 0
        # string 类型：强制转为字符串（避免 float/int 值绕过 AstrBot 类型校验）
        if type_name == "string":
            if value is None:
                return ""
            return str(value)
        return value
    except (ValueError, TypeError):
        return value


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
    """热更新参数 + 持久化到 config.json。"""
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
        # 持久化到 AstrBot config.json
        _persist_hot_config_to_file(validated)
    return jsonify({"ok": len(errors) == 0, "updated": list(validated.keys()), "errors": errors})


@config_bp.route("/config/schema", methods=["GET"])
@require_auth
async def get_config_schema():
    """返回完整配置 schema + 当前实际值，供前端动态生成全量表单。"""
    c = get_container()
    cfg = c.plugin_config
    schema = _load_schema()

    groups = []
    for key, meta in schema.items():
        if not isinstance(meta, dict):
            continue
        mtype = meta.get("type", "string")
        if mtype == "object":
            items = []
            cur_group = cfg.get(key, {}) or {}
            for sub_key, sub_meta in (meta.get("items", {}) or {}).items():
                if not isinstance(sub_meta, dict):
                    continue
                default = sub_meta.get("default")
                items.append({
                    "key": sub_key,
                    "type": sub_meta.get("type", "string"),
                    "description": sub_meta.get("description", sub_key),
                    "hint": sub_meta.get("hint", ""),
                    "default": default,
                    "value": cur_group.get(sub_key, default),
                    "special": sub_meta.get("_special", ""),
                })
            groups.append({
                "key": key, "kind": "object",
                "description": meta.get("description", key),
                "hint": meta.get("hint", ""),
                "items": items,
            })
        else:
            default = meta.get("default")
            groups.append({
                "key": key, "kind": "scalar",
                "type": mtype,
                "description": meta.get("description", key),
                "hint": meta.get("hint", ""),
                "default": default,
                "value": cfg.get(key, default),
                "special": meta.get("_special", ""),
            })

    return jsonify({"groups": groups})


@config_bp.route("/config/full", methods=["POST"])
@require_auth
async def update_config_full():
    """按 schema 校验并保存任意配置项（全量编辑入口）。"""
    c = get_container()
    cfg = c.plugin_config
    schema = _load_schema()
    body = await request.get_json(silent=True) or {}

    changed = []
    errors = []

    for key, incoming in body.items():
        meta = schema.get(key)
        if not isinstance(meta, dict):
            errors.append(f"未知配置项: {key}")
            continue
        mtype = meta.get("type", "string")
        if mtype == "object":
            if not isinstance(incoming, dict):
                errors.append(f"{key} 应为对象")
                continue
            sub_schema = meta.get("items", {}) or {}
            existing = dict(cfg.get(key, {}) or {})
            for sub_key, sub_val in incoming.items():
                sub_meta = sub_schema.get(sub_key)
                if not isinstance(sub_meta, dict):
                    errors.append(f"未知配置项: {key}.{sub_key}")
                    continue
                existing[sub_key] = _coerce(sub_val, sub_meta.get("type", "string"))
            cfg[key] = existing
            changed.append(key)
        else:
            cfg[key] = _coerce(incoming, mtype)
            changed.append(key)

    if changed and hasattr(cfg, "save_config"):
        cfg.save_config()

    # 检测是否有需要重启的参数被修改
    restart_required = False
    for key in changed:
        meta = schema.get(key)
        if not isinstance(meta, dict):
            continue
        if meta.get("restart_required"):
            restart_required = True
            break
        # 检查 object 类型内部子项
        if meta.get("type") == "object":
            sub_schema = meta.get("items", {}) or {}
            for sub_key, sub_meta in sub_schema.items():
                if isinstance(sub_meta, dict) and sub_meta.get("restart_required"):
                    restart_required = True
                    break
            if restart_required:
                break

    return jsonify({
        "ok": len(errors) == 0,
        "changed": changed,
        "errors": errors,
        "restart_required": restart_required,
        "message": "配置已保存" + ("，部分参数需重启插件生效" if restart_required else ""),
    })


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
