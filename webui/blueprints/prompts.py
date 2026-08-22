"""Prompts API — 提示词中心：人设库 / 三级绑定 / 架构模板。"""

from __future__ import annotations

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

    def jsonify(value=None, **kwargs):  # type: ignore[no-redef]
        return value if value is not None else kwargs

    class _Request:
        async def get_json(self, *args, **kwargs):
            return {}
    request = _Request()  # type: ignore[assignment]

try:
    from ..container import get_container
    from ..middleware.auth import require_auth
except Exception:  # pragma: no cover
    def get_container():  # type: ignore[no-redef]
        return None

    def require_auth(func):  # type: ignore[no-redef]
        return func

prompts_bp = Blueprint("prompts", __name__, url_prefix="/api/prompts")


def _repos():
    c = get_container()
    persona_repo = getattr(c, "persona_repo", None) if c else None
    prompt_repo = getattr(c, "prompt_repo", None) if c else None
    prompt_service = getattr(c, "prompt_service", None) if c else None
    if persona_repo is None or prompt_repo is None:
        return None, None, None
    return persona_repo, prompt_repo, prompt_service


# ─── 人设 ────────────────────────────────────────────────────────

@prompts_bp.route("/personas", methods=["GET"])
@require_auth
async def list_personas():
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    include_disabled = request.args.get("include_disabled", "1") != "0"
    return jsonify({"items": persona_repo.list_personas(include_disabled=include_disabled)})


@prompts_bp.route("/personas", methods=["POST"])
@require_auth
async def create_persona():
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    data = await request.get_json(silent=True) or {}
    name = str(data.get("name", "") or "").strip()
    system_prompt = str(data.get("system_prompt", "") or "")
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        pid = persona_repo.add_persona(
            name=name,
            system_prompt=system_prompt,
            begin_dialogs=data.get("begin_dialogs") or [],
            enabled=bool(data.get("enabled", True)),
        )
    except Exception as e:
        return jsonify({"error": f"create failed: {e}"}), 409 if "UNIQUE" in str(e) else 500
    _invalidate(prompt_repo)
    return jsonify({"id": pid})


@prompts_bp.route("/personas/<int:persona_id>", methods=["PUT"])
@require_auth
async def update_persona(persona_id: int):
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    data = await request.get_json(silent=True) or {}
    ok = persona_repo.update_persona(
        persona_id,
        name=data.get("name"),
        system_prompt=data.get("system_prompt"),
        begin_dialogs=data.get("begin_dialogs"),
        enabled=data.get("enabled"),
    )
    _invalidate(persona_repo)
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "not found"}), 404))


@prompts_bp.route("/personas/<int:persona_id>", methods=["DELETE"])
@require_auth
async def delete_persona(persona_id: int):
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    ok = persona_repo.delete_persona(persona_id)
    _invalidate(persona_repo)
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "not found"}), 404))


# ─── 绑定 ────────────────────────────────────────────────────────

@prompts_bp.route("/bindings", methods=["GET"])
@require_auth
async def list_bindings():
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    return jsonify({"items": persona_repo.list_bindings()})


@prompts_bp.route("/bindings", methods=["POST"])
@require_auth
async def set_binding():
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    data = await request.get_json(silent=True) or {}
    scope = str(data.get("scope", "")).strip()
    scope_id = str(data.get("scope_id", "") or "").strip()
    persona_id = data.get("persona_id")
    try:
        persona_id = int(persona_id)
    except (TypeError, ValueError):
        return jsonify({"error": "persona_id required"}), 400
    try:
        persona_repo.set_binding(scope, persona_id, scope_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _invalidate(persona_repo)
    return jsonify({"ok": True})


@prompts_bp.route("/bindings/<scope>", methods=["DELETE"])
@require_auth
async def remove_binding(scope: str):
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    scope_id = request.args.get("scope_id", "")
    ok = persona_repo.remove_binding(scope, scope_id)
    _invalidate(persona_repo)
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "not found"}), 404))


# ─── 架构模板 ────────────────────────────────────────────────────

@prompts_bp.route("/templates", methods=["GET"])
@require_auth
async def list_templates():
    _, prompt_repo, _ = _repos()
    if prompt_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    return jsonify({"items": prompt_repo.list_all()})


@prompts_bp.route("/templates/<key>", methods=["GET"])
@require_auth
async def get_template(key: str):
    _, prompt_repo, _ = _repos()
    if prompt_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    tpl = prompt_repo.get(key)
    return (jsonify(tpl) if tpl else (jsonify({"error": "not found"}), 404))


@prompts_bp.route("/templates/<key>", methods=["PUT"])
@require_auth
async def save_template(key: str):
    _, prompt_repo, _ = _repos()
    if prompt_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    data = await request.get_json(silent=True) or {}
    content = str(data.get("content", ""))
    try:
        ok = prompt_repo.save(key, content)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _invalidate()
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "not found"}), 404))


@prompts_bp.route("/templates/<key>/reset", methods=["POST"])
@require_auth
async def reset_template(key: str):
    _, prompt_repo, _ = _repos()
    if prompt_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    try:
        content = prompt_repo.reset(key)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _invalidate()
    return jsonify({"content": content})


# ─── AstrBot 人设导入 ────────────────────────────────────────────

@prompts_bp.route("/import_astrbot", methods=["POST"])
@require_auth
async def import_from_astrbot():
    """从 AstrBot personas 表导入人设到 wave 提示词中心。"""
    persona_repo, _, _ = _repos()
    if persona_repo is None:
        return jsonify({"error": "prompt center unavailable"}), 503
    c = get_container()
    db = getattr(c, "db", None)
    if db is None:
        return jsonify({"error": "db unavailable"}), 503
    imported, skipped = 0, []
    try:
        rows = db.conn.execute_read(
            "SELECT persona_id, system_prompt FROM personas"
        ).fetchall()
    except Exception as e:
        return jsonify({"error": f"read astrbot personas failed: {e}"}), 500
    for row in rows:
        name = (row[0] or "").strip()
        prompt = row[1] or ""
        if not name or not prompt.strip():
            skipped.append(name or "(empty)")
            continue
        if persona_repo.get_persona_by_name(name):
            skipped.append(f"{name}(已存在)")
            continue
        persona_repo.add_persona(name, prompt, built_in=False)
        imported += 1
    return jsonify({"imported": imported, "skipped": skipped})


def _invalidate(persona_repo: Any = None) -> None:
    """数据变更后失效运行时缓存。"""
    c = get_container()
    svc = getattr(c, "prompt_service", None) if c else None
    if svc is not None:
        try:
            svc.invalidate()
        except Exception:
            pass
