"""Jargon Blueprint — 黑话管理 WebUI API (US-4.4)"""

from __future__ import annotations

import hashlib
import json
import os
from functools import wraps
import re
import time
from pathlib import Path

from quart import Blueprint, current_app, jsonify, request

from ..api_contract import error_payload, mutation_response, page_response
from ..container import get_container
from ..middleware.auth import require_auth

try:
    from ...domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from ...engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError
except ImportError:  # pragma: no cover - plugin root is sometimes imported directly
    from domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError

try:
    from ...services.jargon.holyman_assets import content_entries, is_generic_meaning
except ImportError:  # test/runtime fallback when plugin root is on sys.path
    from services.jargon.holyman_assets import content_entries, is_generic_meaning

jargon_bp = Blueprint("jargon", __name__, url_prefix="/api/jargon")


HOLYMAN_CATEGORY_LABELS = {
    "kfc": "疯狂星期四",
    "defense": "表达策略",
    "reaction": "反应词",
    "copypasta": "复制粘贴",
    "abstract": "抽象话术",
    "gaming": "游戏文化",
    "general": "通用口癖",
    "legacy": "旧版内置",
}

HOLYMAN_UPDATE_CHECK_CACHE_TTL_SECONDS = 30 * 60
_HOLYMAN_UPDATE_CHECK_CACHE: dict | None = None
_TECHNICAL_NOISE_WORDS = (
    "id", "ids", "json", "api", "url", "uri", "http", "https", "get", "post", "put", "patch", "delete",
    "from", "has", "object", "objects", "array", "list", "dict", "map", "set", "type", "types",
    "value", "values", "data", "item", "items", "key", "keys", "param", "params", "args", "kwargs",
    "none", "null", "true", "false", "bool", "str", "int", "float", "class", "method", "function",
    "return", "import", "async", "await", "self", "this", "const", "let", "var",
)


def _resolve_holyman_assets_dir() -> Path:

    """Resolve Holyman assets from runtime volume, dev checkout, or module-relative fallback."""
    for target_path in [
        "/AstrBot/data/plugins/astrbot_plugin_wave_memory/assets/holyman",
        os.path.join(os.getcwd(), "data/plugins/astrbot_plugin_wave_memory/assets/holyman"),
        os.path.join(os.getcwd(), "astrbot_plugin_wave_memory/assets/holyman"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../assets/holyman"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../assets/holyman"),
    ]:
        candidate = Path(target_path)
        if candidate.exists() and (candidate / "phrases.json").exists():
            return candidate
    return Path(os.path.dirname(os.path.abspath(__file__))) / ".." / ".." / "assets" / "holyman"


def _load_holyman_asset_json(name: str, default, assets_dir: Path | None = None):
    """Load a Holyman asset JSON file; shared by list/toggle/preview handlers."""
    path = (assets_dir or _resolve_holyman_assets_dir()) / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# Compatibility alias for older code paths and regression coverage.
_load_asset_json = _load_holyman_asset_json


def _normalize_holyman_phrase(word: str, value) -> dict:
    """兼容旧版字符串 value 和新版 Holyman 结构化 value。"""
    if isinstance(value, dict):
        category = str(value.get("category") or "unknown")
        meaning = str(value.get("meaning") or value.get("explanation") or "")
        source = str(value.get("source") or "")
        kind = str(value.get("kind") or "phrase")
        layer = str(value.get("layer") or "catchphrase")
        reference_only = bool(value.get("reference_only", True))
        runtime_match = value.get("runtime_match") is True
    else:
        category = "legacy"
        meaning = str(value or "")
        source = ""
        kind = "legacy"
        layer = "catchphrase"
        reference_only = True
        runtime_match = True
    return {
        "word": word,
        "meaning": meaning,
        "category": category,
        "category_label": HOLYMAN_CATEGORY_LABELS.get(category, category),
        "source": source,
        "kind": kind,
        "layer": layer,
        "reference_only": reference_only,
        "runtime_match": runtime_match,
        "is_activated": False,
        "db_id": None,
        "custom_meaning": None,
    }


def _merge_holyman_db_activation(item: dict, db_item: dict | None) -> dict:
    if not db_item:
        return item
    meaning = str(db_item.get("meaning") or "")
    item["is_activated"] = True
    item["db_id"] = db_item.get("id")
    if is_generic_meaning(meaning) or len(meaning.strip()) < 8:
        return item
    item.update({
        "custom_meaning": meaning,
    })
    if item.get("kind") in {"curated_phrase", "manual"} and str(item.get("source") or "").startswith("curated/"):
        item["meaning"] = meaning
    return item


def _build_holyman_categories(items: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for item in items:
        category = item.get("category") or "unknown"
        counts[category] = counts.get(category, 0) + 1
        labels[category] = item.get("category_label") or HOLYMAN_CATEGORY_LABELS.get(category, category)
    return [
        {"id": category, "label": labels[category], "count": count}
        for category, count in sorted(counts.items(), key=lambda kv: (-kv[1], labels.get(kv[0], kv[0])))
    ]


def _holyman_content_entries(phrases: dict) -> dict:
    if not isinstance(phrases, dict):
        return {}
    return {
        key: value
        for key, value in phrases.items()
        if isinstance(key, str) and not key.startswith("_")
    }


def _holyman_content_hash(phrases: dict) -> str:
    payload = json.dumps(_holyman_content_entries(phrases), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _holyman_content_status(phrases: dict, local_count: int, remote_version: str, quality_report: dict | None = None) -> dict:
    """Judge Holyman asset health by quality report, not by phrase count alone."""
    quality_report = quality_report or {}
    content_hash = str(phrases.get("_content_hash") or _holyman_content_hash(phrases))
    content_count = _safe_int(phrases.get("_content_count"), local_count)
    remote_commit_version = phrases.get("_remote_commit_version") or phrases.get("_remote_version") or ""
    has_quality_report = bool(quality_report)
    report_status = str(quality_report.get("status") or "")
    if has_quality_report:
        asset_status = report_status
    elif content_count >= 300 and content_hash:
        # Compatibility for old tests/assets that already carry content-derived metadata.
        asset_status = "ready"
    else:
        asset_status = "legacy"
    is_update_available = asset_status != "ready"

    if asset_status == "ready" and remote_version and remote_version != "Unknown" and remote_commit_version:
        is_update_available = remote_version != remote_commit_version

    return {
        "content_hash": content_hash,
        "content_count": content_count,
        "remote_commit_version": remote_commit_version,
        "is_update_available": is_update_available,
        "asset_status": asset_status,
        "asset_type": "global_jargon_reference",
        "runtime_policy": "understanding_only",
    }


def _table_exists(conn, table: str) -> bool:
    """检查表是否存在。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _safe_int(val, default):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_json_list(val):
    try:
        parsed = json.loads(val or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _scope_error(code: str, status: int):
    return jsonify({"error": {"code": code}}), status


def _scoped_repo(container):
    repo = getattr(getattr(container, "db", None), "scoped_knowledge", None)
    if repo is None:
        raise ScopedKnowledgeScopeError("scoped_repository_unavailable")
    return repo


def _group_scope_from_query():
    args = request.args
    required = ("bot_id", "session_id", "visibility")
    if any(args.get(field) is None for field in required):
        raise ScopedKnowledgeScopeError("scope_required")
    bot_id, session_id, visibility = (args.get(field) for field in required)
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


def _pagination_from_query() -> tuple[int, int]:
    try:
        limit = int(request.args.get("limit", 25))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError) as exc:
        raise ScopedKnowledgeScopeError("invalid_pagination") from exc
    if limit not in {25, 50, 100} or offset < 0:
        raise ScopedKnowledgeScopeError("invalid_pagination")
    return limit, offset


def _scope_label(scope: RuntimeScope) -> str:
    return scope.session.id if scope.session else scope.bot_id


def _item_revision(item: dict) -> int:
    return max(1, int(float(item.get("updated_at") or item.get("created_at") or 1) * 1000))


def _object_ref_registry():
    try:
        return current_app.extensions.get("wave_api_contract", {}).get("object_refs")
    except RuntimeError:  # helper 单测没有 Quart app context
        return None


def _scope_query(scope: RuntimeScope) -> dict:
    query = {"bot_id": scope.bot_id, "visibility": scope.visibility}
    if scope.session is not None:
        query["session_id"] = scope.session.id
    if scope.subject_principal_id:
        query["subject_principal_id"] = scope.subject_principal_id
    return query


def _item_object_ref(kind: str, item: dict, scope: RuntimeScope) -> dict | None:
    """签发 opaque ObjectRef；未配置 registry 时诚实返回不可深链。"""
    registry = _object_ref_registry()
    if registry is None:
        return None
    revision = _item_revision(item)
    locator = int(item["id"])
    ref = registry.issue(kind=kind, locator=locator, scope=scope, revision=revision)
    return {
        "ref": ref,
        "kind": kind,
        "locator": locator,
        "scope_key": _scope_label(scope),
        "scope_query": _scope_query(scope),
        "version": revision,
    }


def _require_object_ref(body: dict, *, kind: str, locator: int, scope: RuntimeScope, item: dict) -> None:
    descriptor = body.get("object_ref") or body.get("ref")
    ref = descriptor.get("ref") if isinstance(descriptor, dict) else descriptor
    try:
        revision = int(body.get("revision"))
    except (TypeError, ValueError) as exc:
        raise ScopedKnowledgeScopeError("object_ref_revision_required") from exc
    registry = _object_ref_registry()
    binding = registry.resolve(ref, kind=kind, locator=locator, request_scope=scope) if registry else None
    if binding is None or binding.revision != revision or _item_revision(item) != revision:
        raise ScopedKnowledgeScopeError("object_ref_stale")


def _query_trace_valid(container, scope: RuntimeScope, body: dict) -> bool:
    trace_id = str(body.get("query_trace_id") or "").strip()
    store = getattr(container, "injection_trace_store", None)
    if not trace_id or store is None:
        return False
    try:
        return store.get_for_scope(trace_id, scope) is not None
    except Exception:
        return False


def _memory_evidence_available(container, scope: RuntimeScope, source_memory_id) -> bool:
    if source_memory_id is None or scope.session is None:
        return False
    conn = getattr(getattr(container, "db", None), "conn", None)
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if not {"id", "bot_id", "session_id", "visibility", "resolution_state", "quarantine"} <= columns:
            return False
        return conn.execute(
            "SELECT 1 FROM memories WHERE id=? AND bot_id=? AND session_id=? AND visibility=? "
            "AND resolution_state='resolved' AND COALESCE(quarantine,0)=0 LIMIT 1",
            (int(source_memory_id), scope.bot_id, scope.session.id, scope.visibility),
        ).fetchone() is not None
    except Exception:
        return False


def _formal_jargon(item: dict, scope: RuntimeScope, *, evidence_available: bool = False) -> dict:
    result = dict(item)
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    source_memory_id = result.get("source_memory_id")
    result.update({
        "bot_id": scope.bot_id,
        "session_id": scope.session.id if scope.session else None,
        "visibility": scope.visibility,
        "source": provenance.get("source") or "wave_memory",
        "rule_version": provenance.get("rule_version"),
        "review_status": result.get("status") or "pending",
        "promotion": provenance.get("promotion"),
        "revision": _item_revision(result),
        "anchors": ([{
            "type": "memory",
            "id": str(source_memory_id),
            "source_scope": _scope_label(scope),
            "availability": "available",
            "summary": "同一 Bot/会话内的正式 Jargon 锚点",
            "object_ref": None,
        }] if evidence_available else []),
        "object_ref": _item_object_ref("jargon", result, scope),
    })
    return result


def _fallback_contexts(item: dict) -> list[str]:
    """读取 scoped 黑话自带的回退上下文，不从 legacy group_id 猜测来源。"""
    raw = item.get("source_context")
    if isinstance(raw, list):
        contexts = raw
    elif isinstance(raw, str) and raw.strip():
        parsed = _safe_json_list(raw)
        contexts = parsed if parsed else [raw.strip()]
    else:
        contexts = item.get("contexts") if isinstance(item.get("contexts"), list) else []
    normalized = []
    for value in contexts:
        text = value.get("content") if isinstance(value, dict) else value
        text = str(text or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _scoped_jargon_evidence(container, scope: RuntimeScope, item: dict, *, before: int, after: int) -> dict:
    """只在同一 RuntimeScope 内还原黑话锚点及前后消息。"""
    conn = getattr(getattr(container, "db", None), "conn", None)
    fallback_contexts = _fallback_contexts(item)
    base_payload = {
        "ok": True,
        "jargon": {
            "id": item.get("id"),
            "word": item.get("word"),
            "meaning": item.get("meaning"),
            "revision": item.get("revision"),
        },
        "scope": ScopeCodec.to_dict(scope),
        "anchor": None,
        "messages": [],
        "fallback_contexts": fallback_contexts,
        "used_fallback": True,
    }
    if conn is None or scope.session is None or item.get("source_memory_id") is None:
        return base_payload

    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        required = {"id", "content", "timestamp", "bot_id", "session_id", "visibility", "resolution_state", "quarantine"}
        if not required <= columns:
            return base_payload

        group_column = "group_id" if "group_id" in columns else "session_id"
        sender_id_column = "sender_id" if "sender_id" in columns else "NULL"
        sender_name_column = "sender_name" if "sender_name" in columns else "NULL"
        select_columns = f"id, {group_column}, {sender_id_column}, {sender_name_column}, content, timestamp"
        scope_where = (
            "bot_id=? AND session_id=? AND visibility=? "
            "AND resolution_state='resolved' AND COALESCE(quarantine,0)=0"
        )
        scope_params = (scope.bot_id, scope.session.id, scope.visibility)
        anchor = conn.execute(
            f"SELECT {select_columns} FROM memories WHERE id=? AND {scope_where} LIMIT 1",
            (int(item["source_memory_id"]), *scope_params),
        ).fetchone()
        if not anchor:
            return base_payload

        message_filter = " AND memory_type='message'" if "memory_type" in columns else ""
        anchor_ts = float(anchor[5])
        before_rows = conn.execute(
            f"SELECT {select_columns} FROM memories WHERE {scope_where} AND timestamp < ?{message_filter} "
            "ORDER BY timestamp DESC LIMIT ?",
            (*scope_params, anchor_ts, before),
        ).fetchall()
        after_rows = conn.execute(
            f"SELECT {select_columns} FROM memories WHERE {scope_where} AND timestamp > ?{message_filter} "
            "ORDER BY timestamp ASC LIMIT ?",
            (*scope_params, anchor_ts, after),
        ).fetchall()
    except Exception:
        return base_payload

    def row_to_message(row, role: str) -> dict:
        return {
            "id": row[0],
            "group_id": row[1],
            "sender_id": row[2],
            "sender_name": row[3],
            "content": row[4],
            "timestamp": row[5],
            "role": role,
        }

    anchor_message = row_to_message(anchor, "anchor")
    return {
        **base_payload,
        "anchor": anchor_message,
        "messages": [
            *[row_to_message(row, "before") for row in reversed(before_rows)],
            anchor_message,
            *[row_to_message(row, "after") for row in after_rows],
        ],
        "used_fallback": False,
    }


def _deep_link_error(state: str):
    return jsonify(error_payload("not_found", "Resource not found")), 404


def _resolve_jargon_detail(scope: RuntimeScope, *, locator: int | None = None):
    ref = request.args.get("ref")
    if not ref:
        return None, (jsonify(error_payload("object_ref_required", "Object reference is required")), 400)
    registry = _object_ref_registry()
    if registry is None:
        return None, _deep_link_error("not-found")
    binding, state = registry.resolve_with_state(
        ref,
        kind="jargon",
        locator=locator,
        request_scope=scope,
    )
    if binding is None:
        return None, _deep_link_error(state)
    try:
        jargon_id = int(binding.locator)
        repo = _scoped_repo(get_container())
        row = next(
            (item for item in repo.list_scoped_jargon(scope, limit=10000) if int(item.get("id", -1)) == jargon_id),
            None,
        )
    except (TypeError, ValueError):
        row = None
    if row is None:
        return None, _deep_link_error("not-found")
    if _item_revision(row) != binding.revision:
        return None, _deep_link_error("version-stale")
    container = get_container()
    item = _formal_jargon(
        row,
        scope,
        evidence_available=_memory_evidence_available(container, scope, row.get("source_memory_id")),
    )
    return item, None


def _find_scoped_jargon(repo, scope: RuntimeScope, jargon_id: int, *, include_archived: bool = False) -> dict:
    kwargs = {"limit": 10000}
    if include_archived:
        kwargs["include_archived"] = True
    try:
        rows = repo.list_scoped_jargon(scope, **kwargs)
    except TypeError:
        rows = repo.list_scoped_jargon(scope, limit=10000)
    current = next((row for row in rows if int(row.get("id", -1)) == int(jargon_id)), None)
    if current is None:
        raise LookupError("scoped_object_not_found")
    return current


def _scope_failure(exc: Exception):
    code = getattr(exc, "reason_code", None) or getattr(exc, "code", None) or "invalid_scope"
    return _scope_error(str(code), 400 if code in {"scope_required", "pagination_required", "object_ref_revision_required"} else 422)


def _legacy_mutation_disabled(handler):
    """Legacy Jargon/Catalog rows stay auditable but cannot be mutated by formal routes."""
    @wraps(handler)
    async def reject(*args, **kwargs):
        return _scope_error("legacy_mutation_disabled", 410)
    return reject


def _normalize_holyman_corpus(corpus) -> list[dict]:
    """Normalize raw Holyman corpus into read-only display cards."""
    normalized: list[dict] = []
    if not isinstance(corpus, list):
        return normalized
    for idx, raw_item in enumerate(corpus, start=1):
        if isinstance(raw_item, dict):
            text = str(raw_item.get("text") or raw_item.get("content") or raw_item.get("raw") or "").strip()
            source = str(raw_item.get("source") or "神言.txt")
            line = raw_item.get("line") or raw_item.get("line_no") or raw_item.get("index") or idx
            linked_terms = raw_item.get("linked_terms") or raw_item.get("terms") or []
            tags = raw_item.get("tags") or []
            item_id = raw_item.get("id") or idx
        else:
            text = str(raw_item or "").strip()
            source = "神言.txt"
            line = idx
            linked_terms = []
            tags = []
            item_id = idx
        if not text:
            continue
        normalized.append({
            "id": item_id,
            "index": idx,
            "line": _safe_int(line, idx),
            "text": text,
            "preview": text[:180] + ("..." if len(text) > 180 else ""),
            "length": len(text),
            "source": source,
            "linked_terms": linked_terms if isinstance(linked_terms, list) else [],
            "tags": tags if isinstance(tags, list) else [],
            "layer": "corpus",
            "reference_only": True,
            "safe_for_prompt": False,
            "runtime_match": False,
        })
    return normalized


def _clamp_int(val, default, min_value=0, max_value=50):
    num = _safe_int(val, default)
    return max(min_value, min(max_value, num))


@jargon_bp.route("/", methods=["GET"])
@require_auth
async def list_jargon():
    """正式 scoped 黑话列表；使用唯一 PageResponse wire shape。"""
    try:
        scope = _group_scope_from_query()
        limit, offset = _pagination_from_query()
        repo = _scoped_repo(get_container())
        rows = repo.list_scoped_jargon(scope, status=request.args.get("status"), limit=10000)
        search = (request.args.get("search") or "").strip()
        if search:
            rows = [row for row in rows if search in row.get("word", "") or search in row.get("meaning", "")]
        # 防御旧/异常 producer：默认列表不把已分流 token 重新包装成候选。
        rows = [
            row for row in rows
            if (row.get("status") == "confirmed" or (row.get("provenance") or {}).get("candidate_type") not in {
                "technical_noise", "person_alias", "ordinary_word"
            })
        ]
        container = get_container()
        items = [
            _formal_jargon(
                row,
                scope,
                evidence_available=_memory_evidence_available(container, scope, row.get("source_memory_id")),
            )
            for row in rows[offset:offset + limit]
        ]
        payload = page_response(items, total=len(rows), limit=limit, offset=offset)
        payload["scope"] = ScopeCodec.to_dict(scope)
        service = getattr(container, "jargon_service", None)
        review_available = callable(getattr(service, "review", None))
        edit_available = callable(getattr(service, "update_meaning", None))
        archive_available = callable(getattr(service, "archive", None))
        payload["capabilities"] = {
            "review": {"available": review_available, "reason_code": None if review_available else "jargon_review_command_unavailable"},
            "batch_review": {"available": review_available, "reason_code": None if review_available else "jargon_review_command_unavailable"},
            "edit": {"available": edit_available, "reason_code": None if edit_available else "jargon_update_command_unavailable"},
            "archive": {"available": archive_available, "reason_code": None if archive_available else "jargon_archive_command_unavailable"},
            "evidence": {"available": True, "reason_code": None},
            "create": {"available": False, "reason_code": "anchored_jargon_command_unavailable"},
            "delete": {"available": False, "reason_code": "physical_delete_disabled"},
            "toggle_global": {"available": False, "reason_code": "scoped_global_toggle_unsupported"},
            "select_all_matching": {"available": False, "reason_code": "server_signed_object_refs_required"},
        }
        return jsonify(payload)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/resolve", methods=["GET"])
@require_auth
async def resolve_jargon():
    """仅凭 opaque ObjectRef 与显式当前 Scope 解析详情，不接受裸 ID。"""
    try:
        scope = _group_scope_from_query()
        item, failure = _resolve_jargon_detail(scope)
        if failure is not None:
            return failure
        return jsonify({"item": item, "resolution": {"state": "ready"}})
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/<int:jargon_id>", methods=["GET"])
@require_auth
async def get_scoped_jargon(jargon_id: int):
    """使用 ObjectRef+当前 Scope+canonical revision 读取单条详情。"""
    try:
        scope = _group_scope_from_query()
        item, failure = _resolve_jargon_detail(scope, locator=jargon_id)
        if failure is not None:
            return failure
        return jsonify({"item": item, "resolution": {"state": "ready"}})
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/<int:jargon_id>/context", methods=["GET"])
@jargon_bp.route("/<int:jargon_id>/evidence", methods=["GET"])
@require_auth
async def get_scoped_jargon_evidence(jargon_id: int):
    """按 ObjectRef 与完整 RuntimeScope 还原正式黑话证据上下文。"""
    try:
        scope = _group_scope_from_query()
        item, failure = _resolve_jargon_detail(scope, locator=jargon_id)
        if failure is not None:
            return failure
        before = _clamp_int(request.args.get("before"), 15)
        after = _clamp_int(request.args.get("after"), 15)
        return jsonify(_scoped_jargon_evidence(get_container(), scope, item, before=before, after=after))
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/commands/<int:jargon_id>/meaning", methods=["POST"])
@require_auth
async def update_scoped_jargon_meaning(jargon_id: int):
    """按 ObjectRef 更新释义并重新进入待审核。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        container = get_container()
        repo = _scoped_repo(container)
        current = _find_scoped_jargon(repo, scope, jargon_id)
        _require_object_ref(body, kind="jargon", locator=jargon_id, scope=scope, item=current)
        service = getattr(container, "jargon_service", None)
        update = getattr(service, "update_meaning", None)
        if not callable(update):
            return _scope_error("jargon_update_command_unavailable", 503)
        updated = update(scope, jargon_id, body.get("meaning"))
        item = _formal_jargon(
            updated,
            scope,
            evidence_available=_memory_evidence_available(container, scope, updated.get("source_memory_id")),
        )
        return jsonify(mutation_response(
            operation_kind="jargon.update_meaning",
            status="succeeded",
            revision=item["revision"],
            item=item,
            include_item=True,
        ))
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/commands/<int:jargon_id>/archive", methods=["POST"])
@require_auth
async def archive_scoped_jargon(jargon_id: int):
    """按 ObjectRef 归档黑话，不执行物理删除。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        container = get_container()
        repo = _scoped_repo(container)
        current = _find_scoped_jargon(repo, scope, jargon_id)
        _require_object_ref(body, kind="jargon", locator=jargon_id, scope=scope, item=current)
        service = getattr(container, "jargon_service", None)
        archive = getattr(service, "archive", None)
        if not callable(archive):
            return _scope_error("jargon_archive_command_unavailable", 503)
        result = archive(scope, jargon_id)
        return jsonify(mutation_response(
            operation_kind="jargon.archive",
            status="succeeded",
            revision=None,
            item={"id": result["id"], "status": result["status"]},
            include_item=True,
        ))
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/commands/batch-review", methods=["POST"])
@require_auth
async def batch_review_scoped_jargon():
    """批量审核列表签发的 ObjectRef；先全量校验，再执行领域命令。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        action = str(body.get("action") or "")
        entries = body.get("items")
        if action not in {"approve", "reject"}:
            raise ValueError("invalid_review_action")
        if not isinstance(entries, list) or not entries or len(entries) > 100:
            raise ValueError("invalid_batch_items")
        container = get_container()
        repo = _scoped_repo(container)
        service = getattr(container, "jargon_service", None)
        review = getattr(service, "review", None)
        if not callable(review):
            return _scope_error("jargon_review_command_unavailable", 503)

        validated: list[dict] = []
        seen: set[int] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("invalid_batch_item")
            jargon_id = int(entry.get("id"))
            if jargon_id in seen:
                continue
            seen.add(jargon_id)
            current = _find_scoped_jargon(repo, scope, jargon_id)
            _require_object_ref(entry, kind="jargon", locator=jargon_id, scope=scope, item=current)
            if action == "approve" and not _memory_evidence_available(container, scope, current.get("source_memory_id")):
                raise ScopedKnowledgeScopeError("jargon_anchor_unavailable")
            if action == "approve" and not _query_trace_valid(container, scope, body):
                raise ScopedKnowledgeScopeError("query_trace_required")
            validated.append(current)

        results = []
        for current in validated:
            try:
                result = review(scope, int(current["id"]), action, query_trace_id=body.get("query_trace_id"))
            except TypeError:
                result = review(scope, int(current["id"]), action)
            results.append({"id": int(result["id"]), "status": result["status"]})
        return jsonify({
            "ok": True,
            "operation": {"kind": f"jargon.batch.{action}", "status": "succeeded"},
            "reviewed_count": len(results),
            "items": results,
        })
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/legacy/audit", methods=["GET"])
@require_auth
async def legacy_list_jargon():
    """Legacy 审查/导出视图；只读且不属于正式 scoped API。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"items": [], "total": 0})
    group_id = request.args.get("group_id")
    status = request.args.get("status")  # confirmed / pending / rejected
    search_q = request.args.get("search")  # 搜索词条名或释义

    limit = _safe_int(request.args.get("limit") or request.args.get("size") or 50, 50)
    limit = max(1, min(limit, 500))
    if request.args.get("offset") is not None:
        offset = _safe_int(request.args.get("offset", 0), 0)
    elif request.args.get("page") is not None:
        offset = (max(1, _safe_int(request.args.get("page", 1), 1)) - 1) * limit
    else:
        offset = 0
    include_rejected = _truthy_query(request.args.get("include_rejected"))
    cols = {r[1] for r in c.db.conn.execute("PRAGMA table_info(jargon)").fetchall()}

    where_parts = ["1=1"]
    params = []
    if group_id:
        where_parts.append("group_id = ?")
        params.append(group_id)
    # 改用 status 字段筛选（COALESCE 处理 NULL → 'pending'）
    if status:
        where_parts.append("COALESCE(status, 'pending') = ?")
        params.append(status)
    elif not include_rejected:
        hidden_audit_conditions = []
        if "candidate_type" in cols:
            hidden_audit_conditions.append("COALESCE(candidate_type, 'jargon') IN ('technical_noise', 'person_alias', 'ordinary_word')")
        if "reject_reason" in cols:
            hidden_audit_conditions.append("COALESCE(reject_reason, '') IN ('person_alias_diverted', 'technical_noise_filtered', 'ordinary_word_filtered')")
        if hidden_audit_conditions:
            where_parts.append(f"NOT (COALESCE(status, 'pending') = 'rejected' AND ({' OR '.join(hidden_audit_conditions)}))")
        else:
            where_parts.append("COALESCE(status, 'pending') != 'rejected'")
    if not status and not include_rejected:
        noise_placeholders = ",".join("?" for _ in _TECHNICAL_NOISE_WORDS)
        where_parts.append(f"NOT (COALESCE(status, 'pending') != 'confirmed' AND LOWER(word) IN ({noise_placeholders}))")
        params.extend(_TECHNICAL_NOISE_WORDS)
    if search_q:
        where_parts.append("(word LIKE ? OR meaning LIKE ?)")
        sq = f"%{search_q.strip()}%"
        params.extend([sq, sq])

    extra_cols = [

        "source_memory_id" if "source_memory_id" in cols else "NULL AS source_memory_id",
        "source_message_ts" if "source_message_ts" in cols else "NULL AS source_message_ts",
        "source_sender_id" if "source_sender_id" in cols else "NULL AS source_sender_id",
        "source_context" if "source_context" in cols else "'[]' AS source_context",
        "source" if "source" in cols else "'wave_memory' AS source",
        "candidate_type" if "candidate_type" in cols else "'jargon' AS candidate_type",
        "reject_reason" if "reject_reason" in cols else "NULL AS reject_reason",
        "status" if "status" in cols else "'pending' AS status",
    ]
    where_sql = " AND ".join(where_parts)
    sql = f"""SELECT id, word, meaning, is_jargon, frequency, confidence, is_global, group_id,
              contexts, created_at, {', '.join(extra_cols)}
              FROM jargon WHERE {where_sql} ORDER BY frequency DESC LIMIT ? OFFSET ?"""
    params.extend([limit, offset])

    rows = c.db.conn.execute(sql, params).fetchall()
    # total COUNT 加 WHERE 条件（和列表查询一致）
    count_sql = f"SELECT COUNT(*) FROM jargon WHERE {where_sql}"
    total = c.db.conn.execute(count_sql, params[:-2]).fetchone()[0]
    items = [
        {"id": r[0], "word": r[1], "meaning": r[2], "is_jargon": r[3],
         "frequency": r[4], "confidence": r[5], "is_global": bool(r[6]),
         "group_id": r[7], "contexts": _safe_json_list(r[8]), "created_at": r[9],
         "source_memory_id": r[10], "source_message_ts": r[11], "source_sender_id": r[12],
         "source_context": _safe_json_list(r[13]), "source": r[14] or "wave_memory",
         "candidate_type": r[15] or "jargon", "reject_reason": r[16], "status": r[17] or "pending"}
        for r in rows
    ]
    pending_where = ["COALESCE(status, 'pending') = 'pending'"]
    pending_params = []
    noise_placeholders = ",".join("?" for _ in _TECHNICAL_NOISE_WORDS)
    pending_where.append(f"LOWER(word) NOT IN ({noise_placeholders})")
    pending_params.extend(_TECHNICAL_NOISE_WORDS)
    if "candidate_type" in cols:
        pending_where.append("COALESCE(candidate_type, 'jargon') NOT IN ('technical_noise', 'person_alias', 'ordinary_word')")
    pending_count = c.db.conn.execute(
        f"SELECT COUNT(*) FROM jargon WHERE {' AND '.join(pending_where)}",
        pending_params,
    ).fetchone()[0]
    return jsonify({
        "items": items,
        "total": total,
        "pending_count": pending_count,
        "legacy": True,
        "readonly": True,
        "page": {
            "number": offset // limit + 1,
            "page_size": limit,
            "total": total,
            "total_status": "exact",
            "has_next": offset + len(items) < total,
        },
    })


@jargon_bp.route("/", methods=["POST"])
@require_auth
async def create_jargon():
    """未落地带 anchor/QualityGate 的正式命令前，禁止 WebUI 手工直写。"""
    return _scope_error("anchored_jargon_command_unavailable", 503)


@jargon_bp.route("/<int:jargon_id>/review/<action>", methods=["POST"])
@require_auth
async def review_scoped_jargon(jargon_id: int, action: str):
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        container = get_container()
        repo = _scoped_repo(container)
        current = next((row for row in repo.list_scoped_jargon(scope, limit=10000) if int(row.get("id", -1)) == jargon_id), None)
        if current is None:
            return _scope_error("scoped_object_not_found", 404)
        _require_object_ref(body, kind="jargon", locator=jargon_id, scope=scope, item=current)
        if action == "approve" and not _memory_evidence_available(container, scope, current.get("source_memory_id")):
            return _scope_error("jargon_anchor_unavailable", 422)
        if action == "approve" and not _query_trace_valid(container, scope, body):
            return _scope_error("query_trace_required", 422)
        service = getattr(container, "jargon_service", None)
        review = getattr(service, "review", None)
        if not callable(review):
            return _scope_error("jargon_review_command_unavailable", 503)
        result = review(scope, jargon_id, action, query_trace_id=body.get("query_trace_id"))
        return jsonify(mutation_response(
            operation_kind=f"jargon.{action}",
            status="succeeded",
            revision=None,
            item={"id": result["id"], "status": result["status"]},
            include_item=True,
        ))
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@jargon_bp.route("/legacy/<int:jargon_id>/context", methods=["GET"])
@jargon_bp.route("/legacy/<int:jargon_id>/evidence", methods=["GET"])
@require_auth
async def get_jargon_context(jargon_id: int):
    """按黑话锚点动态截取原始聊天上下文。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    before = _clamp_int(request.args.get("before"), 5)
    after = _clamp_int(request.args.get("after"), 5)

    cols = {r[1] for r in c.db.conn.execute("PRAGMA table_info(jargon)").fetchall()}
    extra_cols = [
        "source_memory_id" if "source_memory_id" in cols else "NULL AS source_memory_id",
        "source_message_ts" if "source_message_ts" in cols else "NULL AS source_message_ts",
        "source_sender_id" if "source_sender_id" in cols else "NULL AS source_sender_id",
        "source_context" if "source_context" in cols else "'[]' AS source_context",
        "candidate_type" if "candidate_type" in cols else "'jargon' AS candidate_type",
    ]
    row = c.db.conn.execute(
        f"""SELECT id, word, meaning, group_id, contexts, {', '.join(extra_cols)}
            FROM jargon WHERE id = ?""",
        (jargon_id,),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "jargon not found"}), 404

    jargon = {
        "id": row[0], "word": row[1], "meaning": row[2], "group_id": row[3],
        "contexts": _safe_json_list(row[4]), "source_memory_id": row[5],
        "source_message_ts": row[6], "source_sender_id": row[7],
        "source_context": _safe_json_list(row[8]), "candidate_type": row[9] or "jargon",
    }

    anchor = None
    if jargon["source_memory_id"]:
        anchor = c.db.conn.execute(
            """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
               WHERE id = ?""",
            (jargon["source_memory_id"],),
        ).fetchone()

    if not anchor and jargon["source_message_ts"]:
        word_like = f"%{jargon['word']}%"
        sender_id = str(jargon.get("source_sender_id") or "")
        anchor = c.db.conn.execute(
            """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
               WHERE group_id = ? AND timestamp BETWEEN ? AND ?
                 AND (? = '' OR sender_id = ?)
                 AND content LIKE ?
               ORDER BY ABS(timestamp - ?) ASC LIMIT 1""",
            (jargon["group_id"], float(jargon["source_message_ts"]) - 60, float(jargon["source_message_ts"]) + 60,
             sender_id, sender_id, word_like, float(jargon["source_message_ts"])),
        ).fetchone()
        # Legacy audit lookup must stay read-only.  A recovered anchor can be
        # returned for inspection, but cannot retroactively assign Scope or mutate
        # the unresolved legacy row.
        if anchor:
            jargon["source_memory_id"] = anchor[0]

    def _row_to_msg(r, role):
        return {
            "id": r[0], "group_id": r[1], "sender_id": r[2], "sender_name": r[3],
            "content": r[4], "timestamp": r[5], "role": role,
        }

    fallback_contexts = jargon["source_context"] or jargon["contexts"]
    if not anchor:
        return jsonify({
            "ok": True,
            "jargon": jargon,
            "anchor": None,
            "messages": [],
            "fallback_contexts": fallback_contexts,
            "used_fallback": True,
        })

    anchor_msg = _row_to_msg(anchor, "anchor")
    anchor_ts = float(anchor[5])
    group_id = anchor[1]
    before_rows = c.db.conn.execute(
        """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
           WHERE group_id = ? AND timestamp < ? AND memory_type = 'message'
           ORDER BY timestamp DESC LIMIT ?""",
        (group_id, anchor_ts, before),
    ).fetchall()
    after_rows = c.db.conn.execute(
        """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
           WHERE group_id = ? AND timestamp > ? AND memory_type = 'message'
           ORDER BY timestamp ASC LIMIT ?""",
        (group_id, anchor_ts, after),
    ).fetchall()
    messages = [_row_to_msg(r, "before") for r in reversed(before_rows)] + [anchor_msg] + [_row_to_msg(r, "after") for r in after_rows]
    return jsonify({
        "ok": True,
        "jargon": jargon,
        "anchor": anchor_msg,
        "messages": messages,
        "fallback_contexts": fallback_contexts,
        "used_fallback": False,
    })


@jargon_bp.route("/holyman/candidates", methods=["GET"])
@require_auth
async def list_holyman_candidates():
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_candidates"):
        return jsonify({"items": [], "legacy": True, "readonly": True})
    rows = c.db.conn.execute("SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates ORDER BY count DESC, word ASC").fetchall()
    return jsonify({
        "items": [{"id": r[0], "word": r[1], "reason": r[2], "count": r[3], "source": r[4], "status": r[5], "reject_reason": r[6]} for r in rows],
        "legacy": True,
        "readonly": True,
    })


@jargon_bp.route("/holyman/candidates/<int:candidate_id>/<action>", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def review_holyman_candidate(candidate_id: int, action: str):
    """Holyman candidate 属于配置/资产审核；统一命令缺失时禁止 apply。"""
    return _scope_error("legacy_mutation_disabled", 410)

@jargon_bp.route("/holyman/candidates/batch-review", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_review_holyman_candidates():
    """Holyman candidate 批量审核没有统一配置/资产命令，永久禁用。"""
    return _scope_error("legacy_mutation_disabled", 410)

def _review_holyman_candidate_ids(conn, ids, action: str, words: list | None = None) -> dict:
    """兼容旧调用方的 fail-closed helper；不得写 candidate 或 blocklist。"""
    return {
        "error": "legacy_mutation_disabled",
        "reviewed_count": 0,
        "blocked_count": 0,
        "missing_ids": [],
    }

@jargon_bp.route("/holyman/blocklist", methods=["GET", "POST"])
@require_auth
async def holyman_blocklist():
    if getattr(request, "method", "GET") != "GET":
        return _scope_error("legacy_mutation_disabled", 410)
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_blocklist"):
        return jsonify({"items": [], "legacy": True, "readonly": True})
    rows = c.db.conn.execute("SELECT id, word, reason, source, created_at FROM jargon_blocklist ORDER BY created_at DESC, word ASC").fetchall()
    return jsonify({
        "items": [{"id": r[0], "word": r[1], "reason": r[2], "source": r[3], "created_at": r[4]} for r in rows],
        "legacy": True,
        "readonly": True,
    })


@jargon_bp.route("/<int:jargon_id>", methods=["PUT"])

@require_auth
@_legacy_mutation_disabled
async def edit_jargon(jargon_id: int):
    """旧 jargon 自由编辑绕过 Scope、ObjectRef 与 QualityGate，永久禁用。"""
    return _scope_error("legacy_mutation_disabled", 410)

@jargon_bp.route("/<int:jargon_id>", methods=["DELETE"])
@require_auth
@_legacy_mutation_disabled
async def delete_jargon(jargon_id: int):
    """旧 jargon 物理删除永久禁用。"""
    return _scope_error("legacy_mutation_disabled", 410)

@jargon_bp.route("/<int:jargon_id>/toggle_global", methods=["POST"])
@jargon_bp.route("/<int:jargon_id>/toggle-global", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def toggle_global(jargon_id: int):
    """旧 jargon 全局状态切换绕过 scoped review 命令，永久禁用。"""
    return _scope_error("legacy_mutation_disabled", 410)

@jargon_bp.route("/batch-review", methods=["POST"])
@jargon_bp.route("/batch/review", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_review_jargon():
    """旧 jargon 批量审核绕过 ObjectRef、证据门禁与统一 review，永久禁用。"""
    return _scope_error("legacy_mutation_disabled", 410)

@jargon_bp.route("/batch-delete", methods=["POST"])
@jargon_bp.route("/batch/delete", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_delete_jargon():
    """旧 jargon 批量物理删除永久禁用。"""
    return _scope_error("legacy_mutation_disabled", 410)

def _fetch_github_commit_info_sync() -> str:
    """同步阻塞式获取远程最新提交的版本哈希；将被托付给外部线程池。"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.github.com/repos/ykdeso/holyman-skills/commits/main",
            headers={"User-Agent": "WaveMemory-WebUI"}
        )
        with urllib.request.urlopen(req, timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
            sha = data.get("sha", "")[:7]
            date = data.get("commit", {}).get("committer", {}).get("date", "")[:10]
            if sha and date:
                return f"{date}-{sha}"
    except Exception:
        pass
    return "Unknown"


def _now_rfc3339() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _truthy_query(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _copy_holyman_update_cache(payload: dict, *, cached: bool, now: float | None = None) -> dict:
    copied = dict(payload)
    copied["cached"] = cached
    if cached and now is not None:
        try:
            copied["cache_age_seconds"] = max(0, int(now - float(payload.get("_cache_timestamp", now))))
        except Exception:
            copied["cache_age_seconds"] = 0
    copied.pop("_cache_timestamp", None)
    return copied


async def _check_holyman_update(force: bool = False) -> dict:
    """Lightweight Holyman GitHub update check with a kirors-style cache handshake."""
    global _HOLYMAN_UPDATE_CHECK_CACHE

    now = time.time()
    if not force and _HOLYMAN_UPDATE_CHECK_CACHE:
        cache_ts = float(_HOLYMAN_UPDATE_CHECK_CACHE.get("_cache_timestamp", 0) or 0)
        if now - cache_ts <= HOLYMAN_UPDATE_CHECK_CACHE_TTL_SECONDS:
            return _copy_holyman_update_cache(_HOLYMAN_UPDATE_CHECK_CACHE, cached=True, now=now)

    local_dir = _resolve_holyman_assets_dir()
    phrases = _load_holyman_asset_json("phrases.json", {}, local_dir)
    concepts = _load_holyman_asset_json("concepts.json", [], local_dir)
    examples = _load_holyman_asset_json("examples.json", [], local_dir)
    corpus = _load_holyman_asset_json("corpus.json", [], local_dir)
    candidates = _load_holyman_asset_json("candidates.json", [], local_dir)
    blocked = _load_holyman_asset_json("blocked.json", {}, local_dir)
    quality_report = _load_holyman_asset_json("quality_report.json", {}, local_dir)

    local_count = len(_holyman_content_entries(phrases))
    local_version = str(phrases.get("_version") or "Unknown") if isinstance(phrases, dict) else "Unknown"

    import asyncio
    warning = None
    try:
        remote_version = await asyncio.to_thread(_fetch_github_commit_info_sync)
    except Exception as exc:
        remote_version = "Unknown"
        warning = f"GitHub 远端版本检查失败：{exc}"

    if not remote_version or remote_version == "Unknown":
        remote_version = "Unknown"
        warning = warning or "无法获取 GitHub 远端 Holyman 版本，已显示本地缓存状态。"

    content_status = _holyman_content_status(phrases, local_count, remote_version, quality_report)
    remote_commit_version = str(content_status.get("remote_commit_version") or "")
    asset_status = str(content_status.get("asset_status") or "unknown")
    remote_reachable = remote_version != "Unknown"
    has_update = bool(content_status.get("is_update_available"))

    payload = {
        "ok": True,
        "asset_type": "global_jargon_reference",
        "runtime_policy": "understanding_only",
        "local_version": local_version,
        "remote_version": remote_version,
        "remote_commit_version": remote_commit_version,
        "content_hash": content_status.get("content_hash") or "",
        "local_content_hash": content_status.get("content_hash") or "",
        "content_count": content_status.get("content_count") or local_count,
        "local_count": local_count,
        "local_counts": {
            "phrases": local_count,
            "concepts": len(concepts) if isinstance(concepts, list) else 0,
            "examples": len(examples) if isinstance(examples, list) else 0,
            "corpus": len(corpus) if isinstance(corpus, list) else 0,
            "candidates": len(candidates) if isinstance(candidates, list) else 0,
            "blocked": len(blocked) if isinstance(blocked, dict) else 0,
        },
        "asset_status": asset_status,
        "has_update": has_update,
        "is_update_available": has_update,
        "update_available": has_update,
        "remote_reachable": remote_reachable,
        "checked_at": _now_rfc3339(),
        "cached": False,
        "cache_ttl_seconds": HOLYMAN_UPDATE_CHECK_CACHE_TTL_SECONDS,
        "source_url": "https://github.com/ykdeso/holyman-skills/commits/main",
    }
    if warning:
        payload["warning"] = warning

    _HOLYMAN_UPDATE_CHECK_CACHE = dict(payload)
    _HOLYMAN_UPDATE_CHECK_CACHE["_cache_timestamp"] = now
    return _copy_holyman_update_cache(_HOLYMAN_UPDATE_CHECK_CACHE, cached=False)


@jargon_bp.route("/holyman/update/check", methods=["GET"])
@require_auth
async def check_holyman_update():
    """轻量检查 Holyman GitHub 版本，带 30 分钟缓存；force=true 强制刷新。"""
    force = _truthy_query(getattr(request, "args", {}).get("force"))
    return jsonify(await _check_holyman_update(force=force))


@jargon_bp.route("/holyman", methods=["GET"])
@require_auth
async def get_holyman():
    """获取 Holyman 预设黑话及其数据库激活状态。"""
    c = get_container()
    
    # 1. 采用绝对物理隔离寻址，解决在装饰器、闭包或符号链接下 __file__ 盘符漂移的痛点
    local_dir = _resolve_holyman_assets_dir()

    phrases = _load_holyman_asset_json("phrases.json", {}, local_dir)
    concepts = _load_holyman_asset_json("concepts.json", [], local_dir)
    examples = _load_holyman_asset_json("examples.json", [], local_dir)
    corpus = _load_holyman_asset_json("corpus.json", [], local_dir)
    candidates = _load_holyman_asset_json("candidates.json", [], local_dir)
    blocked = _load_holyman_asset_json("blocked.json", {}, local_dir)
    manifest = _load_holyman_asset_json("manifest.json", {}, local_dir)
    quality_report = _load_holyman_asset_json("quality_report.json", {}, local_dir)
    raw_readme = (local_dir / "raw" / "README.md").read_text(encoding="utf-8", errors="ignore") if (local_dir / "raw" / "README.md").exists() else ""
    raw_corpus = (local_dir / "raw" / "神言.txt").read_text(encoding="utf-8", errors="ignore") if (local_dir / "raw" / "神言.txt").exists() else ""
    if not isinstance(candidates, list):
        candidates = []
    if not isinstance(blocked, dict):
        blocked = {}
    corpus_items = _normalize_holyman_corpus(corpus)

    # DB 审核状态优先覆盖资产快照，保证 approve/reject 后刷新页面立即可见。
    if c.db and hasattr(c.db, "conn") and c.db.conn:
        try:
            if _table_exists(c.db.conn, "jargon_candidates"):
                rows = c.db.conn.execute(
                    "SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates ORDER BY count DESC, word ASC"
                ).fetchall()
                db_candidates = [
                    {"id": r[0], "word": r[1], "reason": r[2], "count": r[3], "source": r[4], "status": r[5], "reject_reason": r[6]}
                    for r in rows
                ]
                by_word = {item.get("word"): dict(item) for item in candidates if isinstance(item, dict)}
                for item in db_candidates:
                    by_word[item["word"]] = item
                candidates = list(by_word.values())
            if _table_exists(c.db.conn, "jargon_blocklist"):
                rows = c.db.conn.execute("SELECT word, reason FROM jargon_blocklist ORDER BY word ASC").fetchall()
                for r in rows:
                    blocked[str(r[0])] = str(r[1] or "manual_block")
        except Exception:
            pass
            
    # 2. 查询数据库中已激活的条目 (增加 c.db 非空安全卫士防御，防止早期请求崩溃)
    db_items = {}
    if c.db and hasattr(c.db, "conn") and c.db.conn and _table_exists(c.db.conn, "jargon"):
        try:
            rows = c.db.conn.execute(
                "SELECT id, word, meaning, status FROM jargon WHERE scope = 'global' AND source = 'holyman_skills' AND is_jargon = 1 AND status = 'confirmed'"
            ).fetchall()
            for r in rows:
                db_items[r[1]] = {"id": r[0], "meaning": r[2], "status": r[3]}
        except Exception:
            pass
            
    # 3. 构造 items 组合结果：兼容旧版 string value 和新版 structured value
    items = []
    local_version = phrases.get("_version", "Unknown")
    for word, raw_value in content_entries(phrases).items():
        item = _normalize_holyman_phrase(word, raw_value)
        example = None
        for raw_example in examples:
            text = raw_example.get("text", "") if isinstance(raw_example, dict) else str(raw_example)
            linked_terms = raw_example.get("linked_terms", []) if isinstance(raw_example, dict) else []
            if word in linked_terms or (text and word in text):
                example = text.strip()
                if len(example) > 150:
                    example = example[:147] + "..."
                break

        item["example"] = example
        _merge_holyman_db_activation(item, db_items.get(word))
        items.append(item)

    categories = _build_holyman_categories(items)
    local_count = len(items)

    # 4. 轻量更新检查走独立缓存握手，避免每次加载完整列表都直连 GitHub。
    update_check = await _check_holyman_update(force=False)
    remote_version = update_check.get("remote_version") or "Unknown"
    content_status = {
        "content_hash": update_check.get("content_hash") or _holyman_content_hash(phrases),
        "content_count": update_check.get("content_count") or local_count,
        "remote_commit_version": update_check.get("remote_commit_version") or "",
        "is_update_available": bool(update_check.get("has_update")),
        "asset_status": update_check.get("asset_status") or "unknown",
    }
    declared_match = re.search(r"神言\.txt[（(]\s*(\d+)\s*条", raw_readme or "")
    try:
        declared_count = int(declared_match.group(1)) if declared_match else None
    except Exception:
        declared_count = None
    try:
        raw_corpus_items = json.loads(raw_corpus)
        source_items = raw_corpus_items.get("items", []) if isinstance(raw_corpus_items, dict) else raw_corpus_items
        source_count = sum(1 for item in source_items if ((item.get("text", "") if isinstance(item, dict) else str(item)).strip())) if isinstance(source_items, list) else 0
    except Exception:
        source_count = len(corpus) if isinstance(corpus, list) else 0
    corpus_summary = {
        "count": len(corpus_items),
        "safe_for_prompt": False,
        "reference_only": True,
    }
    layers = {
        "catchphrases": items,
        "concepts": concepts if isinstance(concepts, list) else [],
        "quotes_knowledge": examples if isinstance(examples, list) else [],
        "corpus": corpus_summary,
        "candidates": candidates if isinstance(candidates, list) else [],
        "blocked": blocked if isinstance(blocked, dict) else {},
    }
    corpus_counts = {
        "declared": declared_count if declared_count is not None else (quality_report.get("declared_corpus_count") if isinstance(quality_report, dict) else None),
        "source_items": source_count,
        "parsed": quality_report.get("parsed_corpus_count") if isinstance(quality_report, dict) else (len(corpus) if isinstance(corpus, list) else 0),
        "mismatch": quality_report.get("corpus_count_mismatch") if isinstance(quality_report, dict) else False,
        "note": quality_report.get("corpus_count_note") if isinstance(quality_report, dict) else "",
    }
    manifest_payload = manifest if isinstance(manifest, dict) else {}
    manifest_files = manifest_payload.get("files") if isinstance(manifest_payload.get("files"), list) else []
    parse_statuses: dict[str, int] = {}
    for source_file in manifest_files:
        if not isinstance(source_file, dict):
            continue
        parse_status = str(source_file.get("parse_status") or "unknown")
        parse_statuses[parse_status] = parse_statuses.get(parse_status, 0) + 1
    quality_payload = quality_report if isinstance(quality_report, dict) else {}
    quality_errors = quality_payload.get("errors") if isinstance(quality_payload.get("errors"), dict) else {}
    error_count = 0
    for value in quality_errors.values():
        try:
            error_count += int(value or 0)
        except (TypeError, ValueError):
            error_count += 1 if value else 0
    manifest_summary = {
        "source_count": len(manifest_files),
        "parse_statuses": parse_statuses,
        "repo": manifest_payload.get("repo") or manifest_payload.get("source") or "",
    }
    quality_summary = {
        "status": quality_payload.get("status") or asset_status,
        "declared_corpus_count": quality_payload.get("declared_corpus_count"),
        "parsed_corpus_count": quality_payload.get("parsed_corpus_count"),
        "error_count": error_count,
    }

    return jsonify({
        "items": items,
        "phrases": items,
        "concepts": concepts if isinstance(concepts, list) else [],
        "examples": examples if isinstance(examples, list) else [],
        "corpus": corpus_items,
        "corpus_summary": corpus_summary,
        "candidates": candidates if isinstance(candidates, list) else [],
        "blocked": blocked if isinstance(blocked, dict) else {},
        "manifest": manifest_payload,
        "manifest_summary": manifest_summary,
        "quality_report": quality_payload,
        "quality_summary": quality_summary,
        "categories": categories,
        "layers": layers,
        "corpus_counts": corpus_counts,
        "asset_type": "global_jargon_reference",
        "runtime_policy": "understanding_only",
        "local_count": local_count,
        "local_version": local_version,
        "remote_version": remote_version,
        "remote_commit_version": content_status["remote_commit_version"],
        "content_count": content_status["content_count"],
        "content_hash": content_status["content_hash"],
        "asset_status": content_status["asset_status"],
        "is_update_available": content_status["is_update_available"],
        "update_check": update_check,
        "checked_at": update_check.get("checked_at"),
        "update_cached": update_check.get("cached", False),
        "warning": update_check.get("warning"),
        # React WebUI compatibility aliases; v4.0.0 old frontend used direct layer lengths.
        "update_available": content_status["is_update_available"],
        "items_count": local_count,
        "concepts_count": len(concepts) if isinstance(concepts, list) else 0,
        "examples_count": len(examples) if isinstance(examples, list) else 0,
        "corpus_count": len(corpus_items),
        "candidates_count": len(candidates) if isinstance(candidates, list) else 0,
    })


@jargon_bp.route("/holyman/toggle", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def toggle_holyman():
    """Holyman 激活属于配置/资产变更；统一命令缺失时禁止 apply。"""
    return _scope_error("legacy_mutation_disabled", 410)

@jargon_bp.route("/holyman/sync/preview", methods=["POST"])
@require_auth
async def preview_holyman_sync():
    """预览 Holyman 同步差异；只读远端并在内存中构建对比，不写入本地资产。"""
    body = await request.get_json() or {}
    use_proxy = body.get("use_proxy", True)

    try:
        from ...services.jargon.sync import HolymanSyncService
    except ImportError:
        from services.jargon.sync import HolymanSyncService
    sync_service = HolymanSyncService()

    res = await sync_service.preview_sync_from_github(use_proxy=use_proxy)
    status = 200 if res.get("ok") else 500
    return jsonify(res), status


@jargon_bp.route("/holyman/sync", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def sync_holyman():
    """Holyman 资产同步 apply 没有统一安全命令；仅保留 preview。"""
    return _scope_error("legacy_mutation_disabled", 410)
