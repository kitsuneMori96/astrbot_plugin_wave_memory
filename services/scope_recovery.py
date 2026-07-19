"""Legacy -> formal Scope recovery planner and dry-run durable job.

The planner is intentionally read-only. It emits proposed formal projections and
rejects ambiguous legacy rows instead of guessing a Bot/session relationship.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

try:
    from .data_governance_jobs import (
        create_sqlite_snapshot,
        open_readonly_snapshot,
        snapshot_sha256,
    )
except ImportError:  # pragma: no cover - direct repository imports
    from services.data_governance_jobs import create_sqlite_snapshot, open_readonly_snapshot, snapshot_sha256


SCOPE_RECOVERY_PREVIEW_KIND = "maintenance.scope_recovery.preview.v1"
SCOPE_RECOVERY_RULE_VERSION = "scope-recovery-preview/2"

PURGE_TABLES_BY_DOMAIN: dict[str, tuple[str, ...]] = {
    "jargon": ("jargon", "group_jargon"),
    "beliefs": ("beliefs", "belief_system"),
    "soul": ("bot_mood", "mood_snapshots", "concerns"),
}
SKIPPED_TABLES: tuple[str, ...] = ("time_anchors", "book_communities", "book_community", "book_lore", "book_entities", "book_relations", "book_notes")
SHARED_DOMAINS: tuple[str, ...] = ("memories", "facts", "tags")


class ScopeRecoveryError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_bot_id(value: str) -> bool:
    return bool(value) and not value.isdecimal() and value.casefold() not in {"bot", "default"}


def _sample_limit(value: Any, default: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(parsed, 200))


def _canonical_target_scope(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    bot_id = _text(value.get("bot_id"))
    session_id = _text(value.get("session_id"))
    visibility = _text(value.get("visibility") or "group")
    group_id = _text(value.get("group_id"))
    parts = session_id.split(":", 2)
    if (
        not _valid_bot_id(bot_id)
        or visibility != "group"
        or len(parts) != 3
        or not parts[0]
        or parts[1] != "group"
        or not parts[2]
        or not group_id
        or group_id != parts[2]
    ):
        return None
    return {
        "bot_id": bot_id,
        "session_id": session_id,
        "visibility": "group",
        "group_id": parts[2],
    }


def normalize_target_scopes(value: Any) -> tuple[dict[str, str], ...]:
    """Normalize explicit target group Scopes; never invent a target group."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ScopeRecoveryError("target_scopes_must_be_array")
    result: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in value:
        scope = _canonical_target_scope(item)
        if scope is None:
            raise ScopeRecoveryError("target_scope_not_canonical")
        key = (scope["bot_id"], scope["session_id"], scope["visibility"])
        result[key] = scope
    return tuple(result.values())


def _canonical_scope(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    bot_id = _text(value.get("bot_id"))
    session_id = _text(value.get("session_id"))
    visibility = _text(value.get("visibility"))
    group_id = _text(value.get("group_id"))
    parts = session_id.split(":", 2)
    if (
        not _valid_bot_id(bot_id)
        or visibility != "group"
        or len(parts) != 3
        or parts[1] != "group"
        or not parts[0]
        or not parts[2]
        or (group_id and group_id != parts[2])
    ):
        return None
    return {
        "bot_id": bot_id,
        "session_id": session_id,
        "visibility": "group",
        "group_id": parts[2],
    }


def normalize_scope_mappings(value: Any) -> dict[str, tuple[dict[str, str], ...]]:
    """Normalize explicit old-group -> canonical Scope mappings.

    Multiple entries for a group are retained and classified as ambiguous; the
    recovery planner never silently picks the first BotProfile.
    """
    if value is None:
        return {}
    if not isinstance(value, list):
        raise ScopeRecoveryError("scope_mappings_must_be_array")
    result: dict[str, list[dict[str, str]]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise ScopeRecoveryError("scope_mapping_invalid")
        group_id = _text(item.get("group_id"))
        scope = _canonical_scope(item)
        if not group_id or scope is None or scope["group_id"] != group_id:
            raise ScopeRecoveryError("scope_mapping_not_canonical")
        result.setdefault(group_id, []).append(scope)
    return {key: tuple(items) for key, items in result.items()}


def build_recovery_request(body: Any) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ScopeRecoveryError("request_body_required")
    if _text(body.get("mode") or "dry_run") != "dry_run":
        raise ScopeRecoveryError("dry_run_required")
    key = _text(body.get("idempotency_key"))
    if not key or len(key) > 200:
        raise ScopeRecoveryError("idempotency_key_required")
    slot = _text(body.get("schedule_slot") or key)
    if not slot or len(slot) > 200:
        raise ScopeRecoveryError("schedule_slot_invalid")
    mappings = normalize_scope_mappings(body.get("scope_mappings"))
    target_scopes = normalize_target_scopes(body.get("target_scopes"))
    payload = {
        "mode": "dry_run",
        "rule_version": SCOPE_RECOVERY_RULE_VERSION,
        "scope_mappings": [scope for scopes in mappings.values() for scope in scopes],
        "target_scopes": list(target_scopes),
        "migration_policy": "shared_generic_v2",
        "sample_limit": _sample_limit(body.get("sample_limit", 50)),
    }
    return {
        "idempotency_key": key,
        "kind": SCOPE_RECOVERY_PREVIEW_KIND,
        "scope": {"kind": "scope_recovery_preview"},
        "payload": payload,
        "schedule_slot": slot,
        "cursor_generation": 0,
        "cursor": {"phase": "queued", "rule_version": SCOPE_RECOVERY_RULE_VERSION},
    }


async def enqueue_scope_recovery_preview(jobs: Any, body: Any) -> Any:
    if jobs is None or not callable(getattr(jobs, "enqueue", None)):
        raise ScopeRecoveryError("durable_jobs_unavailable")
    return await jobs.enqueue(**build_recovery_request(body))


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _scope_from_memory(row: Mapping[str, Any], mappings: Mapping[str, tuple[dict[str, str], ...]]) -> tuple[dict[str, str] | None, str]:
    persisted = _canonical_scope(row)
    if persisted is not None:
        return persisted, "persisted_scope"
    group_id = _text(row.get("group_id"))
    candidates = mappings.get(group_id, ())
    if len(candidates) == 1:
        return dict(candidates[0]), "explicit_group_mapping"
    if len(candidates) > 1:
        return None, "ambiguous_group_mapping"
    return None, "scope_mapping_required"


def _sample(sample: list[dict[str, Any]], payload: dict[str, Any], limit: int) -> None:
    if len(sample) < limit:
        sample.append(payload)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    if not _columns(connection, table):
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _table_counts(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {table: _table_count(connection, table) for table in tables if _columns(connection, table)}


def _scope_from_legacy_row(
    row: Mapping[str, Any],
    mappings: Mapping[str, tuple[dict[str, str], ...]],
) -> tuple[dict[str, str] | None, str]:
    """Classify a legacy row without treating bot-only data as session-scoped."""
    persisted = _canonical_scope(row)
    if persisted is not None:
        return persisted, "persisted_scope"
    group_id = _text(row.get("group_id"))
    candidates = mappings.get(group_id, ()) if group_id else ()
    bot_id = _text(row.get("bot_id"))
    if bot_id and candidates:
        matching = tuple(scope for scope in candidates if scope["bot_id"] == bot_id)
        if not matching:
            return None, "mapping_bot_mismatch"
        candidates = matching
    if len(candidates) == 1:
        return dict(candidates[0]), "explicit_group_mapping"
    if len(candidates) > 1:
        return None, "ambiguous_group_mapping"
    if bot_id and not group_id:
        return None, "bot_only_scope_unsupported"
    return None, "scope_mapping_required"


def _domain_template(name: str) -> dict[str, Any]:
    return {
        "domain": name,
        "status": "unavailable",
        "disposition": "review",
        "source_tables": [],
        "to_delete": 0,
        "to_migrate": 0,
        "skipped": 0,
        "scanned": 0,
        "formal_ready": 0,
        "recoverable": 0,
        "scope_mapping_required": 0,
        "ambiguous_group_mapping": 0,
        "mapping_bot_mismatch": 0,
        "bot_only_scope_unsupported": 0,
        "source_memory_requires_projection": 0,
        "source_memory_missing": 0,
        "tag_endpoint_missing": 0,
        "review_required": 0,
        "samples": {},
    }


def _record_domain_row(
    domain: dict[str, Any],
    *,
    row: Mapping[str, Any],
    scope: dict[str, str] | None,
    evidence: str,
    sample_limit: int,
    extra: Mapping[str, Any] | None = None,
) -> None:
    domain["scanned"] += 1
    sample: dict[str, Any] = {
        "legacy_id": row.get("id"),
        "evidence": evidence,
    }
    if extra:
        sample.update(dict(extra))
    if scope is not None:
        sample["formal_scope"] = scope
        if evidence == "persisted_scope":
            domain["formal_ready"] += 1
        else:
            domain["recoverable"] += 1
        _sample(domain["samples"].setdefault("recoverable", []), sample, sample_limit)
        return
    domain[evidence] = domain.get(evidence, 0) + 1
    _sample(domain["samples"].setdefault(evidence, []), sample, sample_limit)


def _finish_domain(domain: dict[str, Any]) -> dict[str, Any]:
    if domain["status"] in {"unavailable", "schema_unsupported"}:
        return domain
    if domain["review_required"]:
        domain["status"] = "review_required"
    elif domain["scanned"] == domain["formal_ready"] + domain["recoverable"]:
        domain["status"] = "ready"
    elif domain["recoverable"] or domain["formal_ready"]:
        domain["status"] = "partial"
    else:
        domain["status"] = "blocked"
    return domain


def _scan_simple_domain(
    connection: sqlite3.Connection,
    *,
    domain: dict[str, Any],
    table: str,
    required: set[str],
    optional: tuple[str, ...],
    mappings: Mapping[str, tuple[dict[str, str], ...]],
    sample_limit: int,
) -> None:
    columns = _columns(connection, table)
    if not columns:
        return
    domain["status"] = "available"
    domain["source_tables"].append(table)
    if not required <= columns:
        domain["status"] = "schema_unsupported"
        domain["review_required"] = 0
        return
    selected = ["id" if "id" in columns else "rowid AS id"]
    selected_names = ["id"]
    for name in tuple(dict.fromkeys((*required, *optional))):
        if name in columns:
            selected.append(name)
            selected_names.append(name)
    rows = connection.execute(f"SELECT {', '.join(selected)} FROM {table} ORDER BY id").fetchall()
    for raw in rows:
        row = {name: raw[index] for index, name in enumerate(selected_names)}
        scope, evidence = _scope_from_legacy_row(row, mappings)
        _record_domain_row(
            domain,
            row=row,
            scope=scope,
            evidence=evidence,
            sample_limit=sample_limit,
            extra={
                "source_table": table,
                **{key: _text(row.get(key)) for key in ("group_id", "bot_id", "user_id") if row.get(key) not in (None, "")},
            },
        )


def _source_memory_scope(
    memory_id: Any,
    memory_records: Mapping[int, tuple[dict[str, Any], dict[str, str] | None, str]],
) -> tuple[dict[str, str] | None, str]:
    try:
        key = int(memory_id)
    except (TypeError, ValueError):
        return None, "source_memory_missing"
    record = memory_records.get(key)
    if record is None:
        return None, "source_memory_missing"
    row, scope, evidence = record
    if scope is None:
        return None, "source_memory_requires_projection" if evidence == "explicit_group_mapping" else evidence
    if int(row.get("quarantine") or 0) or _text(row.get("resolution_state")) not in {"", "resolved"}:
        return None, "source_memory_requires_projection"
    if evidence == "persisted_scope":
        return scope, "source_memory_formal_scope"
    if evidence == "explicit_group_mapping":
        return scope, "source_memory_explicit_group_mapping"
    return None, "source_memory_requires_projection"


def _scan_memory_tag_links(
    connection: sqlite3.Connection,
    *,
    domain: dict[str, Any],
    tables: set[str],
    memory_records: Mapping[int, tuple[dict[str, Any], dict[str, str] | None, str]],
    sample_limit: int,
) -> None:
    if not {"memory_tags", "tags"} <= tables:
        return
    domain["status"] = "available"
    domain["source_tables"].extend(["memory_tags", "tags"])
    tag_columns = _columns(connection, "tags")
    tag_name = "t.name" if "name" in tag_columns else "NULL"
    rows = connection.execute(
        f"""SELECT mt.rowid AS id, mt.memory_id, mt.tag_id, {tag_name} AS tag_name
               FROM memory_tags mt LEFT JOIN tags t ON t.id=mt.tag_id ORDER BY mt.rowid"""
    ).fetchall()
    for raw in rows:
        row = {"id": raw[0], "memory_id": raw[1], "tag_id": raw[2], "tag_name": raw[3]}
        scope, evidence = _source_memory_scope(row["memory_id"], memory_records)
        if row["tag_name"] is None:
            scope, evidence = None, "tag_endpoint_missing"
        _record_domain_row(
            domain,
            row=row,
            scope=scope,
            evidence=evidence,
            sample_limit=sample_limit,
            extra={"memory_id": row["memory_id"], "tag_id": row["tag_id"], "tag_name": _text(row["tag_name"])},
        )
    unlinked_tag_count = int(connection.execute("""SELECT COUNT(*) FROM tags t
        WHERE NOT EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.tag_id=t.id)""").fetchone()[0])
    if unlinked_tag_count:
        domain["review_required"] += unlinked_tag_count
        _sample(domain["samples"].setdefault("review_required", []), {"table": "tags", "count": unlinked_tag_count, "reason": "tag_without_memory_evidence_requires_review"}, sample_limit)
    if "tag_relations" in tables:
        relation_count = int(connection.execute("SELECT COUNT(*) FROM tag_relations").fetchone()[0])
        domain["review_required"] += relation_count
        _sample(domain["samples"].setdefault("review_required", []), {"table": "tag_relations", "count": relation_count, "reason": "legacy_tag_relations_require_scoped_review"}, sample_limit)


def _scan_catalog_review(connection: sqlite3.Connection, *, domain: dict[str, Any], tables: set[str], sample_limit: int) -> None:
    candidates = ("book_communities", "book_community", "book_lore", "book_entities", "book_relations", "book_notes")
    present = [table for table in candidates if table in tables]
    if not present:
        return
    domain["status"] = "review_required"
    domain["source_tables"].extend(present)
    for table in present:
        count = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        domain["scanned"] += count
        domain["review_required"] += count
        _sample(domain["samples"].setdefault("review_required", []), {"table": table, "count": count, "reason": "catalog_scope_requires_reviewed_projection"}, sample_limit)


def plan_snapshot(connection: sqlite3.Connection, payload: Mapping[str, Any]) -> dict[str, Any]:
    mappings = normalize_scope_mappings(payload.get("scope_mappings"))
    target_scopes = normalize_target_scopes(payload.get("target_scopes"))
    sample_limit = _sample_limit(payload.get("sample_limit", 50))
    tables = _table_names(connection)
    memory_columns = _columns(connection, "memories")
    required = {"id", "group_id", "content"}
    if not required <= memory_columns:
        raise ScopeRecoveryError("snapshot_schema_missing_memories")
    selected = ["id", "group_id", "content"]
    for name in ("bot_id", "session_id", "visibility", "resolution_state", "quarantine", "source", "sender_id", "timestamp"):
        if name in memory_columns:
            selected.append(name)
    rows = connection.execute(f"SELECT {', '.join(selected)} FROM memories ORDER BY id").fetchall()
    counts = {
        "memories_scanned": 0,
        "formal_ready": 0,
        "recoverable": 0,
        "scope_mapping_required": 0,
        "ambiguous_group_mapping": 0,
        "quarantined_or_unresolved": 0,
        "relationship_profiles_scanned": 0,
        "relationship_profiles_recoverable": 0,
    }
    samples = {"recoverable": [], "scope_mapping_required": [], "ambiguous_group_mapping": []}
    memory_records: dict[int, tuple[dict[str, Any], dict[str, str] | None, str]] = {}
    domains = {name: _domain_template(name) for name in ("memories", "relationships", "affinity", "facts", "tags", "jargon", "beliefs", "soul", "book_lore")}
    for name in SHARED_DOMAINS:
        if name in domains:
            domains[name]["disposition"] = "shared"
    domains["relationships"]["disposition"] = "affinity"
    domains["affinity"]["disposition"] = "affinity"
    domains["jargon"]["disposition"] = "purge"
    domains["beliefs"]["disposition"] = "purge"
    domains["soul"]["disposition"] = "purge"
    domains["book_lore"]["disposition"] = "skipped"
    domains["memories"]["status"] = "available"
    domains["memories"]["source_tables"].append("memories")
    for raw in rows:
        row = {name: raw[index] for index, name in enumerate(selected)}
        counts["memories_scanned"] += 1
        if int(row.get("quarantine") or 0) or _text(row.get("resolution_state")) not in {"", "resolved"}:
            counts["quarantined_or_unresolved"] += 1
        scope, evidence = _scope_from_memory(row, mappings)
        memory_records[int(row["id"])] = (row, scope, evidence)
        healthy = not int(row.get("quarantine") or 0) and _text(row.get("resolution_state")) in {"", "resolved"}
        effective_scope = scope if healthy else None
        effective_evidence = "quarantined_or_unresolved" if not healthy and evidence == "persisted_scope" else evidence
        domain_evidence = "quarantined_or_unresolved" if not healthy else evidence
        _record_domain_row(domains["memories"], row=row, scope=effective_scope, evidence=domain_evidence, sample_limit=sample_limit, extra={"group_id": _text(row.get("group_id"))})
        if effective_evidence == "persisted_scope" and effective_scope is not None:
            counts["formal_ready"] += 1
        elif effective_scope is not None:
            counts["recoverable"] += 1
            _sample(samples["recoverable"], {
                "legacy_memory_id": int(row["id"]),
                "formal_scope": effective_scope,
                "evidence": effective_evidence,
                "origin_fingerprint": "sha256:" + hashlib.sha256(
                    f"memory:{row['id']}:{row.get('content') or ''}".encode("utf-8")
                ).hexdigest(),
            }, sample_limit)
        else:
            counts[effective_evidence] = counts.get(effective_evidence, 0) + 1
            _sample(samples.setdefault(effective_evidence, []), {"legacy_memory_id": int(row["id"]), "group_id": _text(row.get("group_id")), "reason": effective_evidence}, sample_limit)

    _scan_simple_domain(connection, domain=domains["relationships"], table="relationship_events", required={"group_id"}, optional=("bot_id", "user_id", "event_type", "dimension", "delta", "reason", "created_at"), mappings=mappings, sample_limit=sample_limit)
    _scan_simple_domain(connection, domain=domains["affinity"], table="user_profiles", required={"user_id", "group_id"}, optional=("bot_id", "affection", "interaction_count", "first_seen", "last_seen", "metadata"), mappings=mappings, sample_limit=sample_limit)
    _scan_simple_domain(connection, domain=domains["relationships"], table="experience_episodes", required={"group_id"}, optional=("bot_id", "user_id", "episode_type", "outcome", "emotional_weight", "created_at"), mappings=mappings, sample_limit=sample_limit)
    facts_columns = _columns(connection, "facts")
    if facts_columns:
        domains["facts"]["status"] = "available"
        domains["facts"]["source_tables"].append("facts")
        if not {"subject", "predicate", "object"} <= facts_columns:
            domains["facts"]["status"] = "schema_unsupported"
        else:
            selected = ["id" if "id" in facts_columns else "rowid AS id"]
            selected_names = ["id"]
            for name in ("subject", "predicate", "object", "group_id", "bot_id", "session_id", "visibility", "source_memory_id", "confidence", "fact_type"):
                if name in facts_columns:
                    selected.append(name); selected_names.append(name)
            for raw in connection.execute(f"SELECT {', '.join(selected)} FROM facts ORDER BY id").fetchall():
                row = {name: raw[index] for index, name in enumerate(selected_names)}
                scope, evidence = _source_memory_scope(row.get("source_memory_id"), memory_records) if row.get("source_memory_id") is not None else _scope_from_legacy_row(row, mappings)
                _record_domain_row(domains["facts"], row=row, scope=scope, evidence=evidence, sample_limit=sample_limit, extra={"subject": _text(row.get("subject")), "predicate": _text(row.get("predicate"))})

    _scan_memory_tag_links(connection, domain=domains["tags"], tables=tables, memory_records=memory_records, sample_limit=sample_limit)
    _scan_simple_domain(connection, domain=domains["jargon"], table="jargon", required={"word"}, optional=("group_id", "bot_id", "meaning", "status", "frequency", "confidence"), mappings=mappings, sample_limit=sample_limit)
    _scan_simple_domain(connection, domain=domains["jargon"], table="group_jargon", required=set(), optional=("group_id", "bot_id", "word", "term", "meaning", "definition", "status", "frequency", "confidence"), mappings=mappings, sample_limit=sample_limit)
    _scan_simple_domain(connection, domain=domains["beliefs"], table="beliefs", required={"content"}, optional=("group_id", "bot_id", "type", "status", "confidence"), mappings=mappings, sample_limit=sample_limit)
    _scan_simple_domain(connection, domain=domains["beliefs"], table="belief_system", required=set(), optional=("group_id", "bot_id", "content", "belief", "statement", "status", "confidence"), mappings=mappings, sample_limit=sample_limit)
    for table in ("concerns", "mood_snapshots", "bot_mood", "time_anchors"):
        _scan_simple_domain(connection, domain=domains["soul"], table=table, required=set(), optional=("bot_id", "group_id", "topic", "event_summary", "mood_type", "intensity", "description", "timestamp", "start_time", "end_time", "created_at"), mappings=mappings, sample_limit=sample_limit)
    _scan_catalog_review(connection, domain=domains["book_lore"], tables=tables, sample_limit=sample_limit)

    profile_targetable = 0
    if "user_profiles" in tables:
        profile_columns = _columns(connection, "user_profiles")
        required_profiles = {"user_id", "group_id"}
        if required_profiles <= profile_columns:
            profile_rows = connection.execute("SELECT user_id, group_id, bot_id FROM user_profiles" if "bot_id" in profile_columns else "SELECT user_id, group_id, '' FROM user_profiles").fetchall()
            counts["relationship_profiles_scanned"] = len(profile_rows)
            for user_id, group_id, bot_id in profile_rows:
                if _text(user_id) and _text(group_id) and _text(bot_id):
                    counts["relationship_profiles_recoverable"] += 1
                    if target_scopes and any(_text(group_id) == scope["group_id"] and _text(bot_id) == scope["bot_id"] for scope in target_scopes):
                        profile_targetable += 1
    domains["affinity"]["to_migrate"] = profile_targetable

    relationship_targetable = 0
    relationship_columns = _columns(connection, "relationship_events")
    if target_scopes and {"group_id", "bot_id", "user_id"} <= relationship_columns:
        for group_id, bot_id, user_id in connection.execute("SELECT group_id, bot_id, user_id FROM relationship_events").fetchall():
            if _text(user_id) and any(_text(group_id) == scope["group_id"] and _text(bot_id) == scope["bot_id"] for scope in target_scopes):
                relationship_targetable += 1
    domains["relationships"]["to_migrate"] = relationship_targetable

    purge_counts = {
        domain: _table_counts(connection, table_names)
        for domain, table_names in PURGE_TABLES_BY_DOMAIN.items()
    }
    for domain, table_counts in purge_counts.items():
        if domain in domains:
            domains[domain]["to_delete"] = sum(table_counts.values())
            domains[domain]["source_tables"].extend(table for table in table_counts if table not in domains[domain]["source_tables"])
    soul_skipped = _table_counts(connection, ("time_anchors",))
    domains["soul"]["skipped"] = sum(soul_skipped.values())
    if soul_skipped:
        domains["soul"]["source_tables"].extend(table for table in soul_skipped if table not in domains["soul"]["source_tables"])
        if domains["soul"]["to_delete"]:
            domains["soul"]["disposition"] = "purge_and_skip"
        else:
            domains["soul"]["disposition"] = "skipped"
    book_skipped = _table_counts(connection, SKIPPED_TABLES[1:])
    domains["book_lore"]["skipped"] = sum(book_skipped.values())
    if book_skipped:
        domains["book_lore"]["source_tables"].extend(table for table in book_skipped if table not in domains["book_lore"]["source_tables"])

    healthy_shared_memory_ids = {
        int(memory_id) for memory_id, (row, scope, _evidence) in memory_records.items()
        if scope is None and not int(row.get("quarantine") or 0)
        and _text(row.get("resolution_state")) in {"", "resolved"}
    }
    healthy_shared_memories = len(healthy_shared_memory_ids)
    targetable_fact_sources = 0
    facts_columns = _columns(connection, "facts")
    if facts_columns and {"subject", "predicate", "object"} <= facts_columns:
        fact_source_column = "source_memory_id" if "source_memory_id" in facts_columns else None
        for raw in connection.execute(
            "SELECT subject, predicate, object" + (", source_memory_id" if fact_source_column else "") + " FROM facts"
        ).fetchall():
            subject, predicate, object_value = raw[:3]
            source_memory_id = raw[3] if fact_source_column else None
            if not all(_text(value) for value in (subject, predicate, object_value)):
                continue
            if source_memory_id is None:
                targetable_fact_sources += 1
            else:
                try:
                    source_memory_key = int(source_memory_id)
                except (TypeError, ValueError):
                    continue
                if source_memory_key in healthy_shared_memory_ids:
                    targetable_fact_sources += 1
    targetable_tag_links = 0
    if {"memory_tags", "tags"} <= tables:
        for memory_id, _tag_id in connection.execute("SELECT memory_id, tag_id FROM memory_tags").fetchall():
            try:
                memory_key = int(memory_id)
            except (TypeError, ValueError):
                continue
            if memory_key in healthy_shared_memory_ids:
                targetable_tag_links += 1
    if target_scopes:
        domains["memories"]["to_migrate"] = healthy_shared_memories * len(target_scopes)
        domains["facts"]["to_migrate"] = targetable_fact_sources * len(target_scopes)
        domains["tags"]["to_migrate"] = targetable_tag_links * len(target_scopes)
    target_scope_notice = "" if target_scopes else "explicit_two_target_scopes_required_for_shared_migration"
    for domain in domains.values():
        _finish_domain(domain)
    proposed = counts["recoverable"] + counts["formal_ready"]
    migration = {
        "policy": "shared_generic_v2",
        "target_scopes": list(target_scopes),
        "target_scope_count": len(target_scopes),
        "target_scope_notice": target_scope_notice,
        "to_delete": purge_counts,
        "skipped": {"time_anchors": soul_skipped, "book_lore": book_skipped},
        "shared": {
            "source_memory_rows": healthy_shared_memories,
            "target_memory_rows": domains["memories"]["to_migrate"],
            "target_fact_rows": domains["facts"]["to_migrate"],
            "target_tag_link_rows": domains["tags"]["to_migrate"],
        },
        "affinity": {
            "profiles_scanned": counts["relationship_profiles_scanned"],
            "profiles_bot_preserving": counts["relationship_profiles_recoverable"],
            "profiles_targetable": profile_targetable,
            "relationship_event_rows": domains["relationships"]["scanned"],
            "relationship_event_targetable": relationship_targetable,
        },
    }
    return {
        "kind": SCOPE_RECOVERY_PREVIEW_KIND,
        "rule_version": SCOPE_RECOVERY_RULE_VERSION,
        "mode": "dry_run",
        "completed": True,
        "source_business_mutated": False,
        "mapping_count": sum(len(items) for items in mappings.values()),
        "counts": counts,
        "domains": domains,
        "migration": migration,
        "coverage": {
            "memory_rows_with_formal_scope_or_explicit_mapping": proposed,
            "memory_rows_scanned": counts["memories_scanned"],
            "ratio": round(proposed / max(1, counts["memories_scanned"]), 4),
        },
        "samples": samples,
        "next_step": "review_domain_results_and_run_explicit_backfill",
    }


class ScopeRecoveryPreviewJobs:
    """Durable read-only handler for Scope recovery planning."""

    def __init__(self, *, source_db_path: str | os.PathLike[str], snapshot_dir: str | os.PathLike[str]):
        self.source_db_path = Path(source_db_path).resolve()
        self.snapshot_dir = Path(snapshot_dir).resolve()

    def handlers(self) -> dict[str, Any]:
        return {SCOPE_RECOVERY_PREVIEW_KIND: self.preview}

    def _snapshot_path(self, run_id: str) -> Path:
        digest = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:32]
        return self.snapshot_dir / f"scope-recovery-{digest}.sqlite3"

    async def preview(self, run: Any, request: Any, runner: Any) -> dict[str, Any]:
        payload = request.payload
        if not isinstance(payload, Mapping) or payload.get("rule_version") != SCOPE_RECOVERY_RULE_VERSION:
            raise ScopeRecoveryError("invalid_scope_recovery_payload")
        snapshot = self._snapshot_path(run.run_id)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(create_sqlite_snapshot, self.source_db_path, snapshot)
        try:
            digest = await asyncio.to_thread(snapshot_sha256, snapshot)
            connection = open_readonly_snapshot(snapshot)
            try:
                result = plan_snapshot(connection, payload)
            finally:
                connection.close()
            result["snapshot_hash"] = digest
            result["snapshot_retained"] = False
            return result
        finally:
            snapshot.unlink(missing_ok=True)


def build_scope_recovery_handlers(*, source_db_path: str | os.PathLike[str], snapshot_dir: str | os.PathLike[str]) -> dict[str, Any]:
    return ScopeRecoveryPreviewJobs(source_db_path=source_db_path, snapshot_dir=snapshot_dir).handlers()


__all__ = [
    "PURGE_TABLES_BY_DOMAIN",
    "SHARED_DOMAINS",
    "SKIPPED_TABLES",
    "SCOPE_RECOVERY_PREVIEW_KIND",
    "SCOPE_RECOVERY_RULE_VERSION",
    "ScopeRecoveryError",
    "ScopeRecoveryPreviewJobs",
    "build_recovery_request",
    "build_scope_recovery_handlers",
    "enqueue_scope_recovery_preview",
    "normalize_scope_mappings",
    "normalize_target_scopes",
    "plan_snapshot",
]
