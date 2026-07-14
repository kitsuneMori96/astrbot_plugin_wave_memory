"""由运行时 registry 提供真实 Bot、session 与 channel 选项。"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

from quart import Blueprint, current_app, jsonify

from ..api_contract import error_payload
from ..middleware.auth import require_auth

options_bp = Blueprint("options", __name__, url_prefix="/api/options")


def _objects(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise TypeError(f"{field_name} must be an iterable of objects")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError(f"{field_name} contains a non-object option")
        result.append(dict(item))
    return result


@options_bp.route("/scopes", methods=["GET"])
@require_auth
async def scope_options():
    composition = current_app.extensions.get("wave_api_contract", {})
    source = composition.get("scope_options_source")
    getter = getattr(source, "get_scope_options", None)
    if not callable(getter):
        payload = error_payload(
            "options_source_unavailable",
            "Scope options are temporarily unavailable",
            retryable=True,
        )
        payload["source"] = {"health": "error", "reason_code": "registry_unavailable"}
        return jsonify(payload), 503
    try:
        raw = getter()
        if not isinstance(raw, Mapping):
            raise TypeError("scope options provider returned a non-object")
        bots = _objects(raw.get("bots"), "bots")
        sessions = _objects(raw.get("sessions"), "sessions")
        channels = _objects(raw.get("channels"), "channels")
        legacy_groups = _objects(raw.get("legacy_groups"), "legacy_groups")
        source_meta = dict(raw.get("source")) if isinstance(raw.get("source"), Mapping) else {}
    except Exception:
        payload = error_payload(
            "options_source_unavailable",
            "Scope options are temporarily unavailable",
            retryable=True,
        )
        payload["source"] = {"health": "error", "reason_code": "registry_read_failed"}
        return jsonify(payload), 503

    empty = not bots and not sessions and not channels and not legacy_groups
    return jsonify(
        {
            "bots": bots,
            "sessions": sessions,
            "legacy_groups": legacy_groups,
            "channels": channels,
            "generated_at": time.time(),
            "source": {
                **source_meta,
                "health": "empty" if empty else "healthy",
                "reason_code": "registry_empty" if empty else None,
            },
        }
    )


__all__ = ["options_bp"]
