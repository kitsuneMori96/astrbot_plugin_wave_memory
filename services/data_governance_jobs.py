"""Read-only durable previews for scoped Tag and legacy reference governance.

This module deliberately owns no business writer.  It copies the live SQLite database
through a read-only source connection, scans only that immutable snapshot, and stores
resume state exclusively through the existing durable-job progress API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping, MutableMapping
from urllib.parse import quote


DATA_GOVERNANCE_PREVIEW_KIND = "maintenance.data_governance.preview.v1"
DATA_GOVERNANCE_RULE_VERSION = "data-governance-preview/2026-07-05.v1"
TAG_POLICY = "missing_only"
MIN_CONTENT_LENGTH = 10
DEFAULT_CHUNK_SIZE = 250
MAX_CHUNK_SIZE = 1000
DEFAULT_SAMPLE_LIMIT = 20
MAX_SAMPLE_LIMIT = 50
DEFAULT_COUNT_LIMIT = 100_000
MAX_COUNT_LIMIT = 1_000_000

_REQUIRED_SCHEMA = {
    "memories": {
        "id",
        "group_id",
        "content",
        "bot_id",
        "session_id",
        "visibility",
        "resolution_state",
        "quarantine",
        "source",
    },
    "scoped_memory_tags": {"bot_id", "session_id", "visibility", "memory_id"},
    "memory_tags": {"memory_id", "tag_id"},
    "tags": {"id"},
}


class DataGovernancePreviewError(ValueError):
    """The immutable preview contract or snapshot is invalid."""


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise DataGovernancePreviewError(f"{name}_invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DataGovernancePreviewError(f"{name}_invalid") from exc
    if result < minimum or result > maximum:
        raise DataGovernancePreviewError(f"{name}_out_of_range")
    return result


def normalize_preview_scope(value: Any) -> dict[str, str]:
    """Return the exact persisted group scope accepted by the fixed rule set."""

    if not isinstance(value, Mapping):
        raise DataGovernancePreviewError("runtime_scope_required")
    bot_id = str(value.get("bot_id") or "").strip()
    visibility = str(value.get("visibility") or "").strip()
    session_value = value.get("session")
    if isinstance(session_value, Mapping):
        session_id = str(session_value.get("id") or "").strip()
        conversation_id = str(session_value.get("conversation_id") or "").strip()
        session_kind = str(session_value.get("kind") or "").strip()
    else:
        session_id = str(value.get("session_id") or "").strip()
        conversation_id = ""
        session_kind = ""

    parts = session_id.split(":", 2)
    if (
        not bot_id
        or visibility != "group"
        or len(parts) != 3
        or parts[1] != "group"
        or not parts[0]
        or not parts[2]
    ):
        raise DataGovernancePreviewError("canonical_group_scope_required")
    if session_kind and session_kind != "group":
        raise DataGovernancePreviewError("canonical_group_scope_required")
    if conversation_id and conversation_id != parts[2]:
        raise DataGovernancePreviewError("canonical_group_scope_required")
    return {
        "bot_id": bot_id,
        "session_id": session_id,
        "visibility": "group",
        "group_id": parts[2],
    }


def build_preview_request(body: Any) -> dict[str, Any]:
    """Build immutable durable-job arguments without touching SQLite."""

    if not isinstance(body, Mapping):
        raise DataGovernancePreviewError("request_body_required")
    requested_rule = str(body.get("rule_version") or DATA_GOVERNANCE_RULE_VERSION)
    if requested_rule != DATA_GOVERNANCE_RULE_VERSION:
        raise DataGovernancePreviewError("unsupported_rule_version")
    if str(body.get("mode") or "dry_run") != "dry_run":
        raise DataGovernancePreviewError("dry_run_required")
    if str(body.get("tag_policy") or TAG_POLICY) != TAG_POLICY:
        raise DataGovernancePreviewError("missing_only_required")

    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if not idempotency_key or len(idempotency_key) > 200:
        raise DataGovernancePreviewError("idempotency_key_required")
    schedule_slot = str(body.get("schedule_slot") or idempotency_key).strip()
    if not schedule_slot or len(schedule_slot) > 200:
        raise DataGovernancePreviewError("schedule_slot_invalid")

    scope = normalize_preview_scope(body.get("scope"))
    payload = {
        "mode": "dry_run",
        "rule_version": DATA_GOVERNANCE_RULE_VERSION,
        "tag_policy": TAG_POLICY,
        "scope": scope,
        "analyses": ["scoped_tag_candidates", "legacy_memory_tag_orphans"],
        "chunk_size": _bounded_int(
            body.get("chunk_size"),
            default=DEFAULT_CHUNK_SIZE,
            minimum=1,
            maximum=MAX_CHUNK_SIZE,
            name="chunk_size",
        ),
        "sample_limit": _bounded_int(
            body.get("sample_limit"),
            default=DEFAULT_SAMPLE_LIMIT,
            minimum=0,
            maximum=MAX_SAMPLE_LIMIT,
            name="sample_limit",
        ),
        "count_limit": _bounded_int(
            body.get("count_limit"),
            default=DEFAULT_COUNT_LIMIT,
            minimum=1,
            maximum=MAX_COUNT_LIMIT,
            name="count_limit",
        ),
    }
    return {
        "idempotency_key": idempotency_key,
        "kind": DATA_GOVERNANCE_PREVIEW_KIND,
        "scope": {
            "kind": "data_governance_preview",
            "bot_id": scope["bot_id"],
            "session_id": scope["session_id"],
            "visibility": scope["visibility"],
        },
        "payload": payload,
        "schedule_slot": schedule_slot,
        "cursor_generation": 0,
        "cursor": {
            "phase": "queued",
            "rule_version": DATA_GOVERNANCE_RULE_VERSION,
            "after_memory_id": 0,
            "after_memory_tag_rowid": 0,
        },
    }


async def enqueue_preview_job(jobs: Any, body: Any) -> Any:
    """Create one idempotent run through the existing atomic durable facade."""

    if jobs is None or not callable(getattr(jobs, "enqueue", None)):
        raise DataGovernancePreviewError("durable_jobs_unavailable")
    return await jobs.enqueue(**build_preview_request(body))


def _readonly_uri(path: Path) -> str:
    return f"file:{quote(str(path.resolve()).replace(os.sep, '/'), safe='/:')}?mode=ro"


def open_readonly_snapshot(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_readonly_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def create_sqlite_snapshot(source_path: Path, snapshot_path: Path) -> None:
    """Copy a transactionally consistent SQLite image from a read-only source."""

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = snapshot_path.with_suffix(snapshot_path.suffix + ".partial")
    temporary.unlink(missing_ok=True)
    source = sqlite3.connect(_readonly_uri(source_path), uri=True)
    destination = sqlite3.connect(str(temporary))
    try:
        source.execute("PRAGMA query_only=ON")
        source.backup(destination, pages=256)
        destination.commit()
    finally:
        destination.close()
        source.close()
    os.replace(temporary, snapshot_path)


def snapshot_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _schema_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def validate_snapshot_schema(connection: sqlite3.Connection) -> None:
    missing: dict[str, list[str]] = {}
    for table, required in _REQUIRED_SCHEMA.items():
        absent = sorted(required - _schema_columns(connection, table))
        if absent:
            missing[table] = absent
    if missing:
        detail = json.dumps(missing, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        raise DataGovernancePreviewError(f"snapshot_schema_incomplete:{detail}")


def _new_counter(limit: int) -> dict[str, Any]:
    return {"value": 0, "limit": limit, "truncated": False}


def _increment(counter: MutableMapping[str, Any], amount: int = 1) -> None:
    limit = int(counter["limit"])
    value = int(counter["value"])
    remaining = max(0, limit - value)
    counter["value"] = value + min(max(0, amount), remaining)
    if amount > remaining:
        counter["truncated"] = True


def _reason_counter(container: MutableMapping[str, Any], reason: str, limit: int) -> dict[str, Any]:
    counter = container.get(reason)
    if not isinstance(counter, dict):
        counter = _new_counter(limit)
        container[reason] = counter
    return counter


def _initial_state(*, snapshot_hash: str, count_limit: int, sample_limit: int) -> dict[str, Any]:
    return {
        "phase": "scoped_tag_candidates",
        "rule_version": DATA_GOVERNANCE_RULE_VERSION,
        "snapshot": {
            "sha256": snapshot_hash,
            "immutable": True,
            "read_only": True,
            "retained": True,
        },
        "after_memory_id": 0,
        "after_memory_tag_rowid": 0,
        "chunks_completed": 0,
        "counts": {
            "scoped_memories_scanned": _new_counter(count_limit),
            "scoped_tag_candidates": _new_counter(count_limit),
            "legacy_memory_tag_refs_scanned": _new_counter(count_limit),
            "legacy_memory_tag_orphans": _new_counter(count_limit),
        },
        "exclusions": {},
        "legacy_orphan_reasons": {},
        "legacy_scope_impact": {},
        "samples": {
            "scoped_tag_candidates": [],
            "exclusions": [],
            "legacy_memory_tag_orphans": [],
        },
        "sample_limit": sample_limit,
        "count_limit": count_limit,
        "completed": False,
        "dry_run": True,
    }


def _restore_state(cursor: Any, *, snapshot_hash: str, count_limit: int, sample_limit: int) -> dict[str, Any]:
    if not isinstance(cursor, Mapping) or not cursor.get("snapshot"):
        return _initial_state(
            snapshot_hash=snapshot_hash,
            count_limit=count_limit,
            sample_limit=sample_limit,
        )
    state = json.loads(json.dumps(cursor))
    if state.get("rule_version") != DATA_GOVERNANCE_RULE_VERSION:
        raise DataGovernancePreviewError("checkpoint_rule_version_mismatch")
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, Mapping) or snapshot.get("sha256") != snapshot_hash:
        raise DataGovernancePreviewError("snapshot_hash_mismatch")
    if int(state.get("count_limit", -1)) != count_limit or int(state.get("sample_limit", -1)) != sample_limit:
        raise DataGovernancePreviewError("checkpoint_bounds_mismatch")
    return state


def _add_sample(state: MutableMapping[str, Any], bucket: str, sample: dict[str, Any]) -> None:
    samples = state["samples"][bucket]
    if len(samples) < int(state["sample_limit"]):
        samples.append(sample)


def scan_scoped_tag_candidate_chunk(
    connection: sqlite3.Connection,
    *,
    scope: Mapping[str, str],
    state: MutableMapping[str, Any],
    chunk_size: int,
) -> bool:
    """Scan one ``memory.id`` page. Return true when this phase is exhausted."""

    rows = connection.execute(
        """SELECT m.id, m.group_id, m.content, m.resolution_state,
                  COALESCE(m.quarantine, 0) AS quarantine,
                  COALESCE(m.source, '') AS source,
                  EXISTS (
                      SELECT 1 FROM scoped_memory_tags smt
                      WHERE smt.memory_id=m.id AND smt.bot_id=m.bot_id
                        AND smt.session_id=m.session_id AND smt.visibility=m.visibility
                  ) AS has_scoped_tags
           FROM memories m
           WHERE m.id>? AND m.bot_id=? AND m.session_id=? AND m.visibility=?
           ORDER BY m.id ASC LIMIT ?""",
        (
            int(state.get("after_memory_id", 0)),
            scope["bot_id"],
            scope["session_id"],
            scope["visibility"],
            chunk_size,
        ),
    ).fetchall()
    if not rows:
        return True

    count_limit = int(state["count_limit"])
    for row in rows:
        memory_id = int(row["id"])
        content_length = len(str(row["content"] or ""))
        _increment(state["counts"]["scoped_memories_scanned"])
        reason = None
        if str(row["resolution_state"] or "") != "resolved":
            reason = "resolution_state_not_resolved"
        elif int(row["quarantine"] or 0) != 0:
            reason = "quarantined"
        elif str(row["group_id"] or "") != scope["group_id"]:
            reason = "canonical_group_mismatch"
        elif str(row["source"] or "") == "noise":
            reason = "noise_source"
        elif content_length < MIN_CONTENT_LENGTH:
            reason = "content_too_short"
        elif int(row["has_scoped_tags"] or 0) != 0:
            reason = "already_has_scoped_tags"

        if reason is None:
            _increment(state["counts"]["scoped_tag_candidates"])
            _add_sample(
                state,
                "scoped_tag_candidates",
                {"memory_id": memory_id, "content_length": content_length},
            )
        else:
            _increment(_reason_counter(state["exclusions"], reason, count_limit))
            _add_sample(
                state,
                "exclusions",
                {"memory_id": memory_id, "reason": reason},
            )
        state["after_memory_id"] = memory_id
    state["chunks_completed"] = int(state.get("chunks_completed", 0)) + 1
    return len(rows) < chunk_size


def scan_legacy_orphan_chunk(
    connection: sqlite3.Connection,
    *,
    scope: Mapping[str, str],
    state: MutableMapping[str, Any],
    chunk_size: int,
) -> bool:
    """Scan one legacy ``memory_tags.rowid`` page without mutating orphan rows."""

    rows = connection.execute(
        """SELECT mt.rowid AS ref_rowid, mt.memory_id, mt.tag_id,
                  m.id AS live_memory_id, m.bot_id, m.session_id, m.visibility,
                  t.id AS live_tag_id
           FROM memory_tags mt
           LEFT JOIN memories m ON m.id=mt.memory_id
           LEFT JOIN tags t ON t.id=mt.tag_id
           WHERE mt.rowid>?
           ORDER BY mt.rowid ASC LIMIT ?""",
        (int(state.get("after_memory_tag_rowid", 0)), chunk_size),
    ).fetchall()
    if not rows:
        return True

    count_limit = int(state["count_limit"])
    for row in rows:
        ref_rowid = int(row["ref_rowid"])
        _increment(state["counts"]["legacy_memory_tag_refs_scanned"])
        missing_memory = row["live_memory_id"] is None
        missing_tag = row["live_tag_id"] is None
        if missing_memory or missing_tag:
            if missing_memory and missing_tag:
                reason = "missing_memory_and_tag"
            elif missing_memory:
                reason = "missing_memory"
            else:
                reason = "missing_tag"
            if missing_memory:
                scope_impact = "unattributable_missing_memory"
            elif (
                str(row["bot_id"] or "") == scope["bot_id"]
                and str(row["session_id"] or "") == scope["session_id"]
                and str(row["visibility"] or "") == scope["visibility"]
            ):
                scope_impact = "requested_scope"
            else:
                scope_impact = "other_or_legacy_scope"

            _increment(state["counts"]["legacy_memory_tag_orphans"])
            _increment(_reason_counter(state["legacy_orphan_reasons"], reason, count_limit))
            _increment(_reason_counter(state["legacy_scope_impact"], scope_impact, count_limit))
            _add_sample(
                state,
                "legacy_memory_tag_orphans",
                {
                    "rowid": ref_rowid,
                    "memory_id": int(row["memory_id"]),
                    "tag_id": int(row["tag_id"]),
                    "reason": reason,
                    "scope_impact": scope_impact,
                },
            )
        state["after_memory_tag_rowid"] = ref_rowid
    state["chunks_completed"] = int(state.get("chunks_completed", 0)) + 1
    return len(rows) < chunk_size


def _progress(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "phase": state.get("phase"),
        "rule_version": DATA_GOVERNANCE_RULE_VERSION,
        "dry_run": True,
        "snapshot_hash": state.get("snapshot", {}).get("sha256"),
        "counts": state.get("counts"),
        "exclusions": state.get("exclusions"),
        "chunks_completed": state.get("chunks_completed", 0),
        "checkpoint": {
            "after_memory_id": state.get("after_memory_id", 0),
            "after_memory_tag_rowid": state.get("after_memory_tag_rowid", 0),
        },
    }


def _result(state: Mapping[str, Any], *, cancelled: bool = False) -> dict[str, Any]:
    return {
        "kind": DATA_GOVERNANCE_PREVIEW_KIND,
        "rule_version": DATA_GOVERNANCE_RULE_VERSION,
        "mode": "dry_run",
        "tag_policy": TAG_POLICY,
        "cancelled": cancelled,
        "completed": bool(state.get("completed")) and not cancelled,
        "source_business_mutated": False,
        "snapshot_hash": state.get("snapshot", {}).get("sha256"),
        "counts": state.get("counts"),
        "exclusions": state.get("exclusions"),
        "legacy_orphan_reasons": state.get("legacy_orphan_reasons"),
        "legacy_scope_impact": state.get("legacy_scope_impact"),
        "samples": state.get("samples"),
        "checkpoint": {
            "phase": state.get("phase"),
            "after_memory_id": state.get("after_memory_id", 0),
            "after_memory_tag_rowid": state.get("after_memory_tag_rowid", 0),
            "chunks_completed": state.get("chunks_completed", 0),
            "snapshot_retained": state.get("snapshot", {}).get("retained", False),
        },
    }


class DataGovernancePreviewJobs:
    """Durable handlers for immutable, bounded governance previews."""

    def __init__(self, *, source_db_path: str | os.PathLike[str], snapshot_dir: str | os.PathLike[str]):
        self.source_db_path = Path(source_db_path).resolve()
        self.snapshot_dir = Path(snapshot_dir).resolve()

    def handlers(self) -> dict[str, Any]:
        return {DATA_GOVERNANCE_PREVIEW_KIND: self.preview}

    def _snapshot_path(self, run_id: str) -> Path:
        snapshot_id = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:32]
        return self.snapshot_dir / f"{snapshot_id}.sqlite3"

    @staticmethod
    async def _cancelled(run_id: str, runner: Any) -> bool:
        return bool(await runner.service.cancellation_requested(run_id))

    @staticmethod
    async def _checkpoint(run_id: str, runner: Any, state: Mapping[str, Any]) -> None:
        await runner.service.update_progress(
            run_id,
            lease_owner=runner.lease_owner,
            lease_seconds=runner.lease_seconds,
            progress=_progress(state),
            cursor=state,
        )

    async def preview(self, run: Any, request: Any, runner: Any) -> dict[str, Any]:
        payload = request.payload
        if not isinstance(payload, Mapping):
            raise DataGovernancePreviewError("preview_payload_required")
        if payload.get("rule_version") != DATA_GOVERNANCE_RULE_VERSION:
            raise DataGovernancePreviewError("unsupported_rule_version")
        if payload.get("mode") != "dry_run" or payload.get("tag_policy") != TAG_POLICY:
            raise DataGovernancePreviewError("dry_run_missing_only_required")

        scope = normalize_preview_scope(payload.get("scope"))
        chunk_size = _bounded_int(
            payload.get("chunk_size"),
            default=DEFAULT_CHUNK_SIZE,
            minimum=1,
            maximum=MAX_CHUNK_SIZE,
            name="chunk_size",
        )
        sample_limit = _bounded_int(
            payload.get("sample_limit"),
            default=DEFAULT_SAMPLE_LIMIT,
            minimum=0,
            maximum=MAX_SAMPLE_LIMIT,
            name="sample_limit",
        )
        count_limit = _bounded_int(
            payload.get("count_limit"),
            default=DEFAULT_COUNT_LIMIT,
            minimum=1,
            maximum=MAX_COUNT_LIMIT,
            name="count_limit",
        )

        snapshot_path = self._snapshot_path(run.run_id)
        cursor_snapshot = (run.cursor or {}).get("snapshot") if isinstance(run.cursor, Mapping) else None
        if isinstance(cursor_snapshot, Mapping):
            if not snapshot_path.is_file():
                if bool((run.cursor or {}).get("completed")):
                    state = json.loads(json.dumps(run.cursor))
                    state["snapshot"]["retained"] = False
                    return _result(state)
                raise DataGovernancePreviewError("snapshot_missing_for_resume")
        else:
            if await self._cancelled(run.run_id, runner):
                return {"cancelled": True, "completed": False, "source_business_mutated": False}
            await asyncio.to_thread(create_sqlite_snapshot, self.source_db_path, snapshot_path)

        snapshot_hash = await asyncio.to_thread(snapshot_sha256, snapshot_path)
        state = _restore_state(
            run.cursor,
            snapshot_hash=snapshot_hash,
            count_limit=count_limit,
            sample_limit=sample_limit,
        )
        state["snapshot"]["retained"] = True

        if await self._cancelled(run.run_id, runner):
            snapshot_path.unlink(missing_ok=True)
            state["snapshot"]["retained"] = False
            return _result(state, cancelled=True)

        cancelled = False
        connection = open_readonly_snapshot(snapshot_path)
        try:
            validate_snapshot_schema(connection)

            while not state.get("completed") and state["phase"] == "scoped_tag_candidates":
                if await self._cancelled(run.run_id, runner):
                    cancelled = True
                    break
                exhausted = scan_scoped_tag_candidate_chunk(
                    connection,
                    scope=scope,
                    state=state,
                    chunk_size=chunk_size,
                )
                if exhausted:
                    state["phase"] = "legacy_memory_tag_orphans"
                if await self._cancelled(run.run_id, runner):
                    cancelled = True
                    break
                await self._checkpoint(run.run_id, runner, state)

            while (
                not cancelled
                and not state.get("completed")
                and state["phase"] == "legacy_memory_tag_orphans"
            ):
                if await self._cancelled(run.run_id, runner):
                    cancelled = True
                    break
                exhausted = scan_legacy_orphan_chunk(
                    connection,
                    scope=scope,
                    state=state,
                    chunk_size=chunk_size,
                )
                if exhausted:
                    state["phase"] = "completed"
                    state["completed"] = True
                if await self._cancelled(run.run_id, runner):
                    cancelled = True
                    break
                await self._checkpoint(run.run_id, runner, state)
        finally:
            connection.close()

        snapshot_path.unlink(missing_ok=True)
        state["snapshot"]["retained"] = False
        if cancelled:
            return _result(state, cancelled=True)
        await self._checkpoint(run.run_id, runner, state)
        return _result(state)


def build_data_governance_handlers(
    *, source_db_path: str | os.PathLike[str], snapshot_dir: str | os.PathLike[str]
) -> dict[str, Any]:
    """Pure registration helper for the main durable runner wiring."""

    return DataGovernancePreviewJobs(
        source_db_path=source_db_path,
        snapshot_dir=snapshot_dir,
    ).handlers()


__all__ = [
    "DATA_GOVERNANCE_PREVIEW_KIND",
    "DATA_GOVERNANCE_RULE_VERSION",
    "DataGovernancePreviewError",
    "DataGovernancePreviewJobs",
    "build_data_governance_handlers",
    "build_preview_request",
    "create_sqlite_snapshot",
    "enqueue_preview_job",
    "normalize_preview_scope",
    "open_readonly_snapshot",
    "scan_legacy_orphan_chunk",
    "scan_scoped_tag_candidate_chunk",
    "snapshot_sha256",
    "validate_snapshot_schema",
]
