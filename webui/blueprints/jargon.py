"""Jargon Blueprint — 黑话管理 WebUI API (US-4.4)"""

from __future__ import annotations

import hashlib
import json
import os
from functools import wraps
import re
import time
from pathlib import Path

from quart import Blueprint, jsonify, request

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
        "page": {
            "number": page,
            "page_size": page_size,
            "total": len(items),
            "total_status": "exact",
            "has_next": start + page_size < len(items),
        },
    }


def _scope_failure(exc: Exception):
    code = getattr(exc, "reason_code", None) or getattr(exc, "code", None) or "invalid_scope"
    return _scope_error(str(code), 400 if code in {"scope_required", "pagination_required"} else 422)


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
    """正式 scoped 黑话列表；每次请求必须携带完整 Scope 与分页。"""
    try:
        scope = _group_scope_from_query()
        page, page_size = _page_from_query()
        repo = _scoped_repo(get_container())
        rows = repo.list_scoped_jargon(scope, status=request.args.get("status"), limit=page * page_size + 1)
        search = (request.args.get("search") or "").strip()
        if search:
            rows = [row for row in rows if search in row.get("word", "") or search in row.get("meaning", "")]
        payload = _scoped_page(rows, page=page, page_size=page_size)
        payload["scope"] = ScopeCodec.to_dict(scope)
        return jsonify(payload)
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
    """手动创建黑话词条。"""
    body = await request.get_json(silent=True) or {}

    # 兼容仍以 group_id 寻址的旧客户端；写入必须在单一事务中完成，
    # 且响应 ID 直接取自执行 INSERT 的写游标，不能跨连接查询 last_insert_rowid()。
    if "scope" not in body and "group_id" in body:
        c = get_container()
        if not _table_exists(c.db.conn, "jargon"):
            return jsonify({"ok": False, "error": "jargon table not found"}), 500
        word = body.get("word", "")
        meaning = body.get("meaning", "")
        group_id = body.get("group_id")
        if not isinstance(word, str) or not word.strip():
            return jsonify({"ok": False, "error": "Word is required"}), 400
        if not isinstance(meaning, str):
            return jsonify({"ok": False, "error": "invalid meaning"}), 400
        word = word.strip()
        group_id = None if group_id is None else str(group_id).strip()
        now = int(time.time())
        with c.db.conn.write_transaction() as tx:
            duplicate = tx.execute(
                "SELECT id FROM jargon WHERE word = ? AND (group_id = ? OR (group_id IS NULL AND ? IS NULL))",
                (word, group_id, group_id),
            ).fetchone()
            if duplicate:
                return jsonify({"ok": False, "error": f"Jargon '{word}' already exists"}), 400
            cursor = tx.execute(
                "INSERT INTO jargon (word, meaning, is_jargon, status, frequency, confidence, is_global, group_id, contexts, created_at, updated_at) VALUES (?, ?, 1, 'confirmed', 1, 1.0, ?, ?, '[]', ?, ?)",
                (word, meaning, int(group_id is None), group_id, now, now),
            )
            jargon_id = cursor.lastrowid
        if jargon_id is None:  # pragma: no cover - sqlite INSERT cursors always provide it
            raise RuntimeError("jargon insert did not return lastrowid")
        return jsonify({"ok": True, "id": int(jargon_id)})

    try:
        scope = _scope_from_envelope(body)
        word = body.get("word", "")
        meaning = body.get("meaning", "")
        if not isinstance(word, str) or not word.strip() or word != word.strip():
            raise ScopedKnowledgeScopeError("word_required")
        if not isinstance(meaning, str):
            raise ScopedKnowledgeScopeError("invalid_meaning")
        jargon_id = _scoped_repo(get_container()).upsert_scoped_jargon(
            scope,
            word=word,
            meaning=meaning,
            status=str(body.get("status") or "confirmed"),
            is_jargon=bool(body.get("is_jargon", True)),
            frequency=_safe_int(body.get("frequency"), 1),
            confidence=float(body.get("confidence", 1.0)),
            contexts=body.get("contexts") or [],
            source_memory_id=body.get("source_memory_id"),
            source_context=body.get("source_context"),
            provenance=body.get("provenance") or {},
        )
        return jsonify({"ok": True, "id": jargon_id, "scope": ScopeCodec.to_dict(scope)})
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
        return jsonify({"items": []})
    rows = c.db.conn.execute("SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates ORDER BY count DESC, word ASC").fetchall()
    return jsonify({"items": [{"id": r[0], "word": r[1], "reason": r[2], "count": r[3], "source": r[4], "status": r[5], "reject_reason": r[6]} for r in rows]})


@jargon_bp.route("/holyman/candidates/<int:candidate_id>/<action>", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def review_holyman_candidate(candidate_id: int, action: str):
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_candidates"):
        return jsonify({"ok": False, "error": "jargon_candidates table not found"}), 500
    if action not in {"approve", "reject"}:
        return jsonify({"ok": False, "error": "action must be approve or reject"}), 400
    result = _review_holyman_candidate_ids(c.db.conn, [candidate_id], action)
    if result.get("missing_ids"):
        return jsonify({"ok": False, "error": "candidate not found"}), 404
    return jsonify({"ok": True, "candidate_id": candidate_id, "action": action})


@jargon_bp.route("/holyman/candidates/batch-review", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_review_holyman_candidates():
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_candidates"):
        return jsonify({"ok": False, "error": "jargon_candidates table not found"}), 500
    body = await request.get_json() or {}
    ids = body.get("ids", [])
    words = body.get("words", [])
    action = str(body.get("action", "approve"))
    if action not in {"approve", "reject"}:
        return jsonify({"ok": False, "error": "action must be approve or reject"}), 400
    if not isinstance(ids, list):
        ids = []
    if not isinstance(words, list):
        words = []
    if not ids and not words:
        return jsonify({"ok": False, "error": "ids or words list is required"}), 400
    result = _review_holyman_candidate_ids(c.db.conn, ids, action, words=words)
    return jsonify({"ok": True, "reviewed_count": result["reviewed_count"], "action": action, "blocked_count": result["blocked_count"]})


def _review_holyman_candidate_ids(conn, ids, action: str, words: list | None = None) -> dict:
    normalized_ids = []
    for candidate_id in ids:
        try:
            normalized_ids.append(int(candidate_id))
        except (TypeError, ValueError):
            continue
    normalized_words = []
    for word in words or []:
        word = str(word or "").strip()
        if word and word not in normalized_words:
            normalized_words.append(word)
    if not normalized_ids and not normalized_words:
        return {"reviewed_count": 0, "blocked_count": 0, "missing_ids": []}

    clauses = []
    params = []
    if normalized_ids:
        clauses.append(f"id IN ({','.join('?' * len(normalized_ids))})")
        params.extend(normalized_ids)
    if normalized_words:
        clauses.append(f"word IN ({','.join('?' * len(normalized_words))})")
        params.extend(normalized_words)
    where_clause = " OR ".join(clauses)
    rows = conn.execute(
        f"SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates WHERE {where_clause}",
        params,
    ).fetchall()
    found_ids = {int(r[0]) for r in rows}
    missing_ids = [candidate_id for candidate_id in normalized_ids if candidate_id not in found_ids]
    if normalized_ids and missing_ids and not normalized_words:
        return {"reviewed_count": 0, "blocked_count": 0, "missing_ids": missing_ids}

    now = int(time.time())
    row_ids = [int(r[0]) for r in rows]
    if row_ids:
        row_placeholders = ",".join("?" * len(row_ids))
        if action == "approve":
            cur = conn.execute(
                f"UPDATE jargon_candidates SET status = 'approved', updated_at = ? WHERE id IN ({row_placeholders})",
                [now] + row_ids,
            )
            blocked_count = 0
        else:
            cur = conn.execute(
                f"UPDATE jargon_candidates SET status = 'rejected', reject_reason = 'manual_reject', updated_at = ? WHERE id IN ({row_placeholders})",
                [now] + row_ids,
            )
            blocked_count = 0
            for row in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO jargon_blocklist (word, reason, source, created_at) VALUES (?, ?, ?, ?)",
                    (row[1], "manual_reject", "holyman_review", now),
                )
                blocked_count += 1
        reviewed_count = getattr(cur, "rowcount", len(row_ids))
    else:
        reviewed_count = len(normalized_words)
        blocked_count = 0

    if action == "reject":
        existing_words = {str(r[1]) for r in rows}
        for word in normalized_words:
            if word in existing_words:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO jargon_blocklist (word, reason, source, created_at) VALUES (?, ?, ?, ?)",
                (word, "manual_reject", "holyman_review", now),
            )
            blocked_count += 1
    conn.commit()
    return {"reviewed_count": reviewed_count, "blocked_count": blocked_count, "missing_ids": []}


@jargon_bp.route("/holyman/blocklist", methods=["GET", "POST"])
@require_auth
async def holyman_blocklist():
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_blocklist"):
        return jsonify({"items": []})
    if getattr(request, "method", "GET") == "GET":
        rows = c.db.conn.execute("SELECT id, word, reason, source, created_at FROM jargon_blocklist ORDER BY created_at DESC, word ASC").fetchall()
        return jsonify({"items": [{"id": r[0], "word": r[1], "reason": r[2], "source": r[3], "created_at": r[4]} for r in rows]})
    return _scope_error("legacy_mutation_disabled", 410)


@jargon_bp.route("/<int:jargon_id>", methods=["PUT"])

@require_auth
@_legacy_mutation_disabled
async def edit_jargon(jargon_id: int):
    """编辑黑话词条/释义。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    body = await request.get_json(silent=True) or {}
    sets = []
    params = []
    if "word" in body:
        sets.append("word = ?")
        params.append(body["word"])
    if "meaning" in body:
        sets.append("meaning = ?")
        params.append(body["meaning"])
    if not sets:
        return jsonify({"error": "Nothing to update"}), 400
    sets.append("updated_at = ?")
    params.append(int(time.time()))
    params.append(jargon_id)
    try:
        c.db.conn.execute(f"UPDATE jargon SET {', '.join(sets)} WHERE id = ?", params)
        c.db.conn.commit()
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return jsonify({"error": "该群已存在同名词条，请使用其他名称"}), 409
        raise
    return jsonify({"ok": True, "jargon_id": jargon_id})


@jargon_bp.route("/<int:jargon_id>", methods=["DELETE"])
@require_auth
@_legacy_mutation_disabled
async def delete_jargon(jargon_id: int):
    """删除黑话。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    c.db.conn.execute("DELETE FROM jargon WHERE id = ?", (jargon_id,))
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted": jargon_id})


@jargon_bp.route("/<int:jargon_id>/toggle_global", methods=["POST"])
@jargon_bp.route("/<int:jargon_id>/toggle-global", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def toggle_global(jargon_id: int):
    """切换全局状态。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    row = c.db.conn.execute("SELECT is_global FROM jargon WHERE id = ?", (jargon_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    new_val = 0 if row[0] else 1
    c.db.conn.execute("UPDATE jargon SET is_global = ?, updated_at = ? WHERE id = ?", (new_val, int(time.time()), jargon_id))
    c.db.conn.commit()
    return jsonify({"ok": True, "jargon_id": jargon_id, "is_global": bool(new_val)})


@jargon_bp.route("/batch-review", methods=["POST"])
@jargon_bp.route("/batch/review", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_review_jargon():
    """批量审核确认/否决黑话词条（支持 all_matching 跨页全选）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500

    body = await request.get_json() or {}
    all_matching = body.get("all_matching", False)
    action = body.get("action", "approve")  # approve 或 reject
    if action not in {"approve", "reject"}:
        return jsonify({"ok": False, "error": "invalid action"}), 400

    now = int(time.time())
    
    if all_matching:
        group_id = body.get("group_id")
        status = body.get("status")
        search_q = body.get("search")
        
        where_parts = ["1=1"]
        params = []
        if group_id:
            where_parts.append("group_id = ?")
            params.append(group_id)
        if status:
            where_parts.append("COALESCE(status, 'pending') = ?")
            params.append(status)
        if search_q:
            where_parts.append("(word LIKE ? OR meaning LIKE ?)")
            sq = f"%{search_q.strip()}%"
            params.extend([sq, sq])
            
        where_sql = " AND ".join(where_parts)
        if action == "approve":
            cur = c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 1, status = 'confirmed', updated_at = ? WHERE {where_sql}",
                [now] + params,
            )
        else:
            cur = c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 0, status = 'rejected', reject_reason = 'webui_batch_rejected', updated_at = ? WHERE {where_sql}",
                [now] + params,
            )
        reviewed_count = cur.rowcount
    else:
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids list or all_matching is required"}), 400
            
        placeholders = ",".join("?" * len(ids))
        if action == "approve":
            c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 1, status = 'confirmed', updated_at = ? WHERE id IN ({placeholders})",
                [now] + ids,
            )
        else:
            c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 0, status = 'rejected', reject_reason = 'webui_batch_rejected', updated_at = ? WHERE id IN ({placeholders})",
                [now] + ids,
            )
        reviewed_count = len(ids)

    c.db.conn.commit()
    return jsonify({"ok": True, "reviewed_count": reviewed_count, "action": action})


@jargon_bp.route("/batch-delete", methods=["POST"])
@jargon_bp.route("/batch/delete", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_delete_jargon():
    """批量删除黑话词条（支持 all_matching 跨页全选）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500

    body = await request.get_json() or {}
    all_matching = body.get("all_matching", False)
    
    if all_matching:
        group_id = body.get("group_id")
        status = body.get("status")
        search_q = body.get("search")
        
        where_parts = ["1=1"]
        params = []
        if group_id:
            where_parts.append("group_id = ?")
            params.append(group_id)
        if status:
            where_parts.append("COALESCE(status, 'pending') = ?")
            params.append(status)
        if search_q:
            where_parts.append("(word LIKE ? OR meaning LIKE ?)")
            sq = f"%{search_q.strip()}%"
            params.extend([sq, sq])
            
        where_sql = " AND ".join(where_parts)
        cur = c.db.conn.execute(f"DELETE FROM jargon WHERE {where_sql}", params)
    else:
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids list or all_matching is required"}), 400
            
        placeholders = ",".join("?" * len(ids))
        cur = c.db.conn.execute(f"DELETE FROM jargon WHERE id IN ({placeholders})", ids)
        
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted_count": cur.rowcount})


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
    """激活或去激活预设 Holyman 词条。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
        
    body = await request.get_json() or {}
    word = body.get("word", "").strip()
    meaning = body.get("meaning", "").strip()
    activate = body.get("activate", False)
    
    if not word:
        return jsonify({"ok": False, "error": "word is required"}), 400
        
    now = int(time.time())
    
    if activate:
        phrases = _load_asset_json("phrases.json", {})
        phrase_item = phrases.get(word) if isinstance(phrases, dict) else None
        if not (isinstance(phrase_item, dict) and phrase_item.get("runtime_match") is True and phrase_item.get("layer") == "catchphrase"):
            return jsonify({"ok": False, "error": "only runtime-match catchphrases can be enabled"}), 400
        # 双重检查是否已存在
        dup = c.db.conn.execute(
            "SELECT id FROM jargon WHERE word = ? AND scope = 'global' AND source = 'holyman_skills'", 
            (word,)
        ).fetchone()
        
        if dup:
            return jsonify({"ok": True, "db_id": dup[0]})
            
        c.db.conn.execute(
            "INSERT INTO jargon (word, meaning, is_jargon, status, frequency, confidence, is_global, group_id, contexts, created_at, updated_at, scope, source) VALUES (?, ?, 1, 'confirmed', 5, 0.9, 1, 'global_fallback', '[]', ?, ?, 'global', 'holyman_skills')",
            (word, meaning, now, now)
        )
        c.db.conn.commit()
        new_id = c.db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return jsonify({"ok": True, "db_id": new_id})
    else:
        c.db.conn.execute(
            "DELETE FROM jargon WHERE word = ? AND scope = 'global' AND source = 'holyman_skills'",
            (word,)
        )
        c.db.conn.commit()
        return jsonify({"ok": True})


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
async def sync_holyman():
    """同步 Holyman 词库。"""
    global _HOLYMAN_UPDATE_CHECK_CACHE
    body = await request.get_json() or {}
    use_proxy = body.get("use_proxy", True)
    
    try:
        from ...services.jargon.sync import HolymanSyncService
    except ImportError:
        from services.jargon.sync import HolymanSyncService
    sync_service = HolymanSyncService()
    
    res = await sync_service.sync_from_github(use_proxy=use_proxy)
    if res.get("ok"):
        _HOLYMAN_UPDATE_CHECK_CACHE = None
        c = get_container()
        if hasattr(c, "jargon_service") and c.jargon_service:
            if hasattr(c.jargon_service, "_holyman") and c.jargon_service._holyman:
                c.jargon_service._holyman.reload()
                
    return jsonify(res)
