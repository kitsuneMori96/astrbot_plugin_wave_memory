"""Read-only diagnostics probes for WaveMemory derived state.

The service never creates or migrates a database and never invokes repair code. SQLite
sources are opened with ``mode=ro`` plus ``PRAGMA query_only=ON``; index manifests and
BookLore are observed as files only.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import numpy as np

try:
    from ..engine.index_manifest import (
        ManifestValidationError,
        latest_generation,
        manifest_path,
        read_index_manifest,
    )
except ImportError:  # pragma: no cover - focused tests import top-level packages
    from engine.index_manifest import (
        ManifestValidationError,
        latest_generation,
        manifest_path,
        read_index_manifest,
    )


HEALTH_VALUES = frozenset({
    "healthy",
    "empty",
    "not_configured",
    "probe_error",
    "drift",
    "repairing",
})

_BOOK_LORE_TABLES = (
    "book_entities",
    "book_relations",
    "book_communities",
    "book_notes",
)


@dataclass(frozen=True)
class IndexSource:
    """Runtime metadata needed to locate and compare one HNSW manifest."""

    path: str | Path | None
    kind: str
    dimension: int | None = None
    runtime_count: int | None = None
    runtime_ids: Sequence[int] | None = None
    search: Callable[[np.ndarray, int], Sequence[tuple[int, float]]] | None = None


class DiagnosticsService:
    """Collect bounded, read-only health evidence from configured sources."""

    def __init__(
        self,
        *,
        database_path: str | Path | None = None,
        memory_index: IndexSource | None = None,
        tag_index: IndexSource | None = None,
        book_lore_path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = _optional_path(database_path)
        self.memory_index = memory_index or IndexSource(None, "memory")
        self.tag_index = tag_index or IndexSource(None, "tags")
        self.book_lore_path = _optional_path(book_lore_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def collect(self) -> dict[str, Any]:
        checked_at = self._checked_at()
        checks, committed_watermark = self._database_checks(checked_at)
        checks.extend((
            self._probe_index_manifest(
                "memory_manifest",
                self.memory_index,
                checked_at,
                committed_watermark=committed_watermark,
            ),
            self._probe_index_manifest(
                "tag_manifest",
                self.tag_index,
                checked_at,
                committed_watermark=committed_watermark,
            ),
            self._probe_index_shadow("memory_index_shadow", self.memory_index, checked_at),
            self._probe_index_shadow("tag_index_shadow", self.tag_index, checked_at),
            self._probe_book_lore(checked_at),
        ))
        return {
            "health": _overall_health(checks),
            "source": "wave_memory_readonly_diagnostics",
            "checked_at": checked_at,
            "evidence": {
                "read_only": True,
                "probe_count": len(checks),
                "health_counts": dict(sorted(Counter(item["health"] for item in checks).items())),
            },
            "checks": checks,
        }

    def _checked_at(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _database_checks(self, checked_at: str) -> tuple[list[dict[str, Any]], int | None]:
        names = (
            "fts",
            "memory_vectors",
            "outbox_consumer_lag",
            "job_runs",
            "derived_projection",
        )
        if self.database_path is None:
            return ([
                _result(
                    name,
                    "not_configured",
                    "sqlite:not_configured",
                    checked_at,
                    {"reason": "database_path_not_configured"},
                )
                for name in names
            ], None)

        source = f"sqlite:{self.database_path}"
        try:
            with _readonly_connection(self.database_path) as connection:
                watermark = _committed_watermark(connection)
                probes = (
                    ("fts", self._probe_fts),
                    ("memory_vectors", self._probe_memory_vectors),
                    ("outbox_consumer_lag", self._probe_outbox_consumer_lag),
                    ("job_runs", self._probe_job_runs),
                    ("derived_projection", self._probe_derived_projection),
                )
                checks = [
                    self._safe_database_probe(name, probe, connection, source, checked_at)
                    for name, probe in probes
                ]
                return checks, watermark
        except Exception as exc:
            error = _error_evidence(exc)
            return ([
                _result(name, "probe_error", source, checked_at, error)
                for name in names
            ], None)

    @staticmethod
    def _safe_database_probe(
        name: str,
        probe: Callable[[sqlite3.Connection], tuple[str, dict[str, Any]]],
        connection: sqlite3.Connection,
        source: str,
        checked_at: str,
    ) -> dict[str, Any]:
        try:
            health, evidence = probe(connection)
            return _result(name, health, source, checked_at, evidence)
        except Exception as exc:
            return _result(name, "probe_error", source, checked_at, _error_evidence(exc))

    @staticmethod
    def _probe_fts(connection: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        existing = _existing_tables(connection)
        required = {"memories", "fts_memories"}
        missing_tables = sorted(required - existing)
        if missing_tables:
            return "not_configured", {
                "reason": "fts_schema_not_configured",
                "missing_tables": missing_tables,
            }

        memory_count = _scalar(
            connection,
            "SELECT COUNT(*) FROM memories WHERE content IS NOT NULL",
        )
        fts_count = _scalar(connection, "SELECT COUNT(*) FROM fts_memories")
        missing_rows = _scalar(
            connection,
            """SELECT COUNT(*) FROM (
                   SELECT id FROM memories WHERE content IS NOT NULL
                   EXCEPT SELECT rowid FROM fts_memories
               )""",
        )
        orphan_rows = _scalar(
            connection,
            """SELECT COUNT(*) FROM (
                   SELECT rowid FROM fts_memories
                   EXCEPT SELECT id FROM memories WHERE content IS NOT NULL
               )""",
        )
        trigger_rows = connection.execute(
            """SELECT name FROM sqlite_master
                 WHERE type='trigger' AND tbl_name='memories'
                   AND name IN ('fts_memories_ai','fts_memories_ad','fts_memories_au')
                 ORDER BY name"""
        ).fetchall()
        triggers = [str(row[0]) for row in trigger_rows]
        missing_triggers = sorted({
            "fts_memories_ai",
            "fts_memories_ad",
            "fts_memories_au",
        } - set(triggers))
        evidence = {
            "scope": "canonical_memory_rows",
            "memory_count": memory_count,
            "fts_count": fts_count,
            "missing_rows": missing_rows,
            "orphan_rows": orphan_rows,
            "triggers": triggers,
            "missing_triggers": missing_triggers,
        }
        if memory_count == 0 and fts_count == 0:
            return "empty", evidence
        if missing_rows or orphan_rows or missing_triggers or memory_count != fts_count:
            return "drift", evidence
        return "healthy", evidence

    @staticmethod
    def _probe_memory_vectors(connection: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        existing = _existing_tables(connection)
        if "memories" not in existing:
            return "not_configured", {
                "reason": "canonical_memory_schema_not_configured",
                "missing_tables": ["memories"],
            }

        canonical_vector_count = _scalar(
            connection,
            "SELECT COUNT(*) FROM memories WHERE vector IS NOT NULL",
        )
        canonical_blob_sizes = [
            {"bytes": int(row[0] or 0), "count": int(row[1] or 0)}
            for row in connection.execute(
                """SELECT length(vector), COUNT(*) FROM memories
                     WHERE vector IS NOT NULL
                     GROUP BY length(vector) ORDER BY COUNT(*) DESC LIMIT 5"""
            ).fetchall()
        ]
        if "memory_vectors" not in existing:
            evidence = {
                "scope": "canonical_memory_rows",
                "storage_mode": "memories.vector",
                "canonical_vector_count": canonical_vector_count,
                "legacy_table_present": False,
                "orphan_count": 0,
                "blob_sizes": canonical_blob_sizes,
            }
            return ("empty" if canonical_vector_count == 0 else "healthy"), evidence

        vector_count = _scalar(connection, "SELECT COUNT(*) FROM memory_vectors")
        orphan_count = _scalar(
            connection,
            """SELECT COUNT(*) FROM memory_vectors mv
                 LEFT JOIN memories m ON m.id=mv.memory_id
                WHERE m.id IS NULL""",
        )
        matched_count = _scalar(
            connection,
            """SELECT COUNT(*) FROM memory_vectors mv
                 JOIN memories m ON m.id=mv.memory_id""",
        )
        mismatched_count = _scalar(
            connection,
            """SELECT COUNT(*) FROM memory_vectors mv
                 JOIN memories m ON m.id=mv.memory_id
                WHERE m.vector IS NULL OR m.vector != mv.vector""",
        )
        legacy_blob_sizes = [
            {"bytes": int(row[0] or 0), "count": int(row[1] or 0)}
            for row in connection.execute(
                """SELECT length(vector), COUNT(*) FROM memory_vectors
                     GROUP BY length(vector) ORDER BY COUNT(*) DESC LIMIT 5"""
            ).fetchall()
        ]
        evidence = {
            "scope": "canonical_memory_ids",
            "storage_mode": "legacy_duplicate_table",
            "count": vector_count,
            "canonical_vector_count": canonical_vector_count,
            "matched_count": matched_count,
            "mismatched_count": mismatched_count,
            "orphan_count": orphan_count,
            "legacy_table_present": True,
            "blob_sizes": legacy_blob_sizes,
            "canonical_blob_sizes": canonical_blob_sizes,
        }
        if vector_count == 0 and canonical_vector_count == 0:
            return "empty", evidence
        if orphan_count or mismatched_count or vector_count != canonical_vector_count:
            return "drift", evidence
        return "healthy", evidence

    @staticmethod
    def _probe_outbox_consumer_lag(connection: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        existing = _existing_tables(connection)
        required = {"write_operations", "domain_outbox", "outbox_deliveries"}
        missing_tables = sorted(required - existing)
        if missing_tables:
            return "not_configured", {
                "reason": "outbox_schema_not_configured",
                "missing_tables": missing_tables,
            }

        rows = connection.execute(
            """SELECT d.consumer_name,
                      COUNT(*) AS delivery_count,
                      SUM(CASE WHEN d.processed_at IS NULL THEN 1 ELSE 0 END) AS lag_count,
                      SUM(CASE WHEN d.processed_at IS NULL AND d.state='processing' THEN 1 ELSE 0 END) AS processing_count,
                      SUM(CASE WHEN d.processed_at IS NULL AND d.state='retry' THEN 1 ELSE 0 END) AS retry_count,
                      SUM(CASE WHEN d.processed_at IS NULL AND d.state='pending' THEN 1 ELSE 0 END) AS pending_count,
                      MIN(CASE WHEN d.processed_at IS NULL THEN o.created_at END) AS oldest_pending_at,
                      MAX(w.write_sequence) AS latest_write_sequence,
                      MAX(CASE WHEN d.processed_at IS NOT NULL THEN w.write_sequence ELSE 0 END) AS applied_write_sequence
                 FROM outbox_deliveries d
                 JOIN domain_outbox o ON o.event_id=d.event_id
                 JOIN write_operations w ON w.operation_id=o.operation_id
                WHERE w.status='committed'
                GROUP BY d.consumer_name
                ORDER BY d.consumer_name"""
        ).fetchall()
        consumers = [
            {
                "consumer": str(row[0]),
                "delivery_count": int(row[1] or 0),
                "lag_count": int(row[2] or 0),
                "processing_count": int(row[3] or 0),
                "retry_count": int(row[4] or 0),
                "pending_count": int(row[5] or 0),
                "oldest_pending_at": row[6],
                "latest_write_sequence": int(row[7] or 0),
                "applied_write_sequence": int(row[8] or 0),
            }
            for row in rows
        ]
        total_lag = sum(item["lag_count"] for item in consumers)
        processing = sum(item["processing_count"] for item in consumers)
        retrying = sum(item["retry_count"] for item in consumers)
        pending = sum(item["pending_count"] for item in consumers)
        evidence = {
            "scope": "per_event_per_consumer",
            "consumer_count": len(consumers),
            "total_lag": total_lag,
            "processing_count": processing,
            "retry_count": retrying,
            "pending_count": pending,
            "consumers": consumers,
        }
        if not consumers:
            return "empty", evidence
        if total_lag == 0:
            return "healthy", evidence
        if processing == total_lag and retrying == 0 and pending == 0:
            return "repairing", evidence
        return "drift", evidence

    @staticmethod
    def _probe_job_runs(connection: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        if "background_job_runs" not in _existing_tables(connection):
            return "not_configured", {
                "reason": "job_run_schema_not_configured",
                "missing_tables": ["background_job_runs"],
            }

        status_rows = connection.execute(
            "SELECT status, COUNT(*) FROM background_job_runs GROUP BY status ORDER BY status"
        ).fetchall()
        statuses = {str(row[0]): int(row[1] or 0) for row in status_rows}
        active_states = {"queued", "pending", "running", "processing", "retry", "leased", "cancelling"}
        failed_states = {"failed", "error"}
        active_count = sum(count for status, count in statuses.items() if status in active_states)
        failed_count = sum(count for status, count in statuses.items() if status in failed_states)
        latest = connection.execute(
            """SELECT run_id, request_id, status, attempt, cursor_generation,
                      error_code, updated_at
                 FROM background_job_runs
                ORDER BY updated_at DESC, run_id DESC LIMIT 10"""
        ).fetchall()
        evidence = {
            "scope": "durable_background_job_runs",
            "run_count": sum(statuses.values()),
            "status_counts": statuses,
            "active_count": active_count,
            "failed_count": failed_count,
            "recent_runs": [
                {
                    "run_id": str(row[0]),
                    "request_id": str(row[1]),
                    "status": str(row[2]),
                    "attempt": int(row[3] or 0),
                    "cursor_generation": int(row[4] or 0),
                    "error_code": row[5],
                    "updated_at": row[6],
                }
                for row in latest
            ],
        }
        if not statuses:
            return "empty", evidence
        if failed_count:
            return "drift", evidence
        if active_count:
            return "repairing", evidence
        return "healthy", evidence

    @staticmethod
    def _probe_derived_projection(connection: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        existing = _existing_tables(connection)
        required = {"domain_outbox", "outbox_deliveries", "derived_projection_state"}
        missing_tables = sorted(required - existing)
        if missing_tables:
            return "not_configured", {
                "reason": "derived_projection_schema_not_configured",
                "missing_tables": missing_tables,
            }

        projection_count = _scalar(connection, "SELECT COUNT(*) FROM derived_projection_state")
        row = connection.execute(
            """SELECT COUNT(*) AS delivery_count,
                      SUM(CASE WHEN p.applied_version IS NULL OR p.applied_version < o.aggregate_version THEN 1 ELSE 0 END) AS lagged_count,
                      SUM(CASE WHEN (p.applied_version IS NULL OR p.applied_version < o.aggregate_version)
                                    AND d.processed_at IS NULL AND d.state='processing' THEN 1 ELSE 0 END) AS repairing_count,
                      MAX(COALESCE(p.generation, 0)) AS max_generation,
                      MAX(COALESCE(p.updated_at, 0)) AS latest_projection_at
                 FROM outbox_deliveries d
                 JOIN domain_outbox o ON o.event_id=d.event_id
                 LEFT JOIN derived_projection_state p
                   ON p.consumer_name=d.consumer_name
                  AND p.aggregate_kind=o.aggregate_kind
                  AND p.aggregate_id=o.aggregate_id"""
        ).fetchone()
        delivery_count = int(row[0] or 0)
        lagged_count = int(row[1] or 0)
        repairing_count = int(row[2] or 0)
        evidence = {
            "scope": "consumer_aggregate_version",
            "projection_count": projection_count,
            "delivery_count": delivery_count,
            "lagged_count": lagged_count,
            "repairing_count": repairing_count,
            "max_generation": int(row[3] or 0),
            "latest_projection_at": row[4] or None,
        }
        if projection_count == 0 and delivery_count == 0:
            return "empty", evidence
        if lagged_count == 0:
            return "healthy", evidence
        if repairing_count == lagged_count:
            return "repairing", evidence
        return "drift", evidence

    @staticmethod
    def _probe_index_manifest(
        name: str,
        index: IndexSource,
        checked_at: str,
        *,
        committed_watermark: int | None,
    ) -> dict[str, Any]:
        if index.path is None:
            return _result(
                name,
                "not_configured",
                "file:not_configured",
                checked_at,
                {"reason": "index_path_not_configured", "kind": index.kind},
            )

        index_path = Path(index.path).expanduser().resolve()
        source = f"file:{manifest_path(index_path)}"
        manifest_file = manifest_path(index_path)
        try:
            expected_kind = None if _is_tag_kind(index.kind) else index.kind
            manifest = read_index_manifest(
                index_path,
                expected_kind=expected_kind,
                expected_dimension=index.dimension,
                verify_checksum=True,
            )
            if manifest is None:
                generation = latest_generation(index_path)
                evidence = {
                    "kind": index.kind,
                    "index_path": str(index_path),
                    "manifest_path": str(manifest_file),
                    "legacy_index_exists": index_path.is_file(),
                    "latest_generation": generation,
                    "reason": "generation_manifest_missing",
                }
                health = "drift" if index_path.is_file() or generation > 0 else "empty"
                return _result(name, health, source, checked_at, evidence)

            if not _index_kinds_match(index.kind, manifest.kind):
                raise ManifestValidationError(
                    f"manifest kind mismatch: expected {index.kind!r}, got {manifest.kind!r}"
                )

            watermark_relation, watermark_delta = _watermark_relation(
                manifest.db_watermark,
                committed_watermark,
            )
            evidence = manifest.to_dict()
            evidence.update({
                "index_path": str(index_path),
                "manifest_path": str(manifest_file),
                "runtime_count": index.runtime_count,
                "checksum_verified": True,
                "committed_watermark": committed_watermark,
                "committed_write_sequence_watermark": committed_watermark,
                "watermark_relation": watermark_relation,
                "watermark_delta": watermark_delta,
            })
            drift_reasons: list[str] = []
            if index.runtime_count is not None and manifest.count != index.runtime_count:
                drift_reasons.append("runtime_count_mismatch")
            if watermark_relation == "lag":
                drift_reasons.append("db_watermark_lag")
            elif watermark_relation == "ahead":
                drift_reasons.append("db_watermark_ahead")
            evidence["drift_reasons"] = drift_reasons
            if drift_reasons:
                health = "drift"
            elif manifest.count == 0:
                health = "empty"
            else:
                health = "healthy"
            return _result(name, health, source, checked_at, evidence)
        except ManifestValidationError as exc:
            return _result(
                name,
                "drift",
                source,
                checked_at,
                {"reason": "invalid_generation_manifest", **_error_evidence(exc)},
            )
        except Exception as exc:
            return _result(name, "probe_error", source, checked_at, _error_evidence(exc))

    def _probe_index_shadow(
        self,
        name: str,
        index: IndexSource,
        checked_at: str,
    ) -> dict[str, Any]:
        """Compare live HNSW IDs and sampled self-recall against canonical vectors."""
        source = f"runtime:{index.kind}"
        if self.database_path is None or index.runtime_ids is None or index.search is None:
            return _result(
                name,
                "not_configured",
                source,
                checked_at,
                {"reason": "runtime_index_probe_not_available", "kind": index.kind},
            )
        table = "memories" if index.kind == "memory" else "tags" if _is_tag_kind(index.kind) else None
        if table is None:
            return _result(
                name,
                "not_configured",
                source,
                checked_at,
                {"reason": "unsupported_index_kind", "kind": index.kind},
            )
        try:
            with _readonly_connection(self.database_path) as connection:
                if table not in _existing_tables(connection) or "vector" not in _table_columns(connection, table):
                    return _result(
                        name,
                        "not_configured",
                        source,
                        checked_at,
                        {"reason": "canonical_vector_column_missing", "table": table},
                    )
                where = "vector IS NOT NULL"
                if table == "memories":
                    columns = _table_columns(connection, table)
                    if "resolution_state" in columns:
                        where += " AND resolution_state='resolved'"
                    if "quarantine" in columns:
                        where += " AND COALESCE(quarantine, 0)=0"
                    if "memory_type" in columns:
                        where += " AND COALESCE(memory_type, 'message') NOT IN ('archived','evicted','deleted')"
                canonical_ids = {
                    int(row[0])
                    for row in connection.execute(
                        f"SELECT id FROM {table} WHERE {where}"
                    ).fetchall()
                }
                runtime_ids = {int(value) for value in index.runtime_ids}
                sample_rows = connection.execute(
                    f"SELECT id, vector FROM {table} WHERE {where} ORDER BY id LIMIT 5"
                ).fetchall()

            recall = []
            for entity_id, vector_blob in sample_rows:
                vector = np.frombuffer(vector_blob, dtype=np.float32)
                results = index.search(vector, min(5, max(1, len(runtime_ids))))
                labels = [int(item[0]) for item in results]
                recall.append({
                    "id": int(entity_id),
                    "hit": int(entity_id) in labels,
                    "rank": labels.index(int(entity_id)) + 1 if int(entity_id) in labels else None,
                })
            hits = sum(1 for item in recall if item["hit"])
            evidence = {
                "kind": index.kind,
                "canonical_count": len(canonical_ids),
                "runtime_count": len(runtime_ids),
                "missing_ids": sorted(canonical_ids - runtime_ids)[:20],
                "orphan_ids": sorted(runtime_ids - canonical_ids)[:20],
                "missing_count": len(canonical_ids - runtime_ids),
                "orphan_count": len(runtime_ids - canonical_ids),
                "sample_size": len(recall),
                "sample_hits": hits,
                "sample_recall": 1.0 if not recall else hits / len(recall),
                "samples": recall,
            }
            if not canonical_ids and not runtime_ids:
                health = "empty"
            elif evidence["missing_count"] or evidence["orphan_count"] or hits != len(recall):
                health = "drift"
            else:
                health = "healthy"
            return _result(name, health, source, checked_at, evidence)
        except Exception as exc:
            return _result(name, "probe_error", source, checked_at, _error_evidence(exc))

    def _probe_book_lore(self, checked_at: str) -> dict[str, Any]:
        if self.book_lore_path is None:
            return _result(
                "book_lore_source",
                "not_configured",
                "sqlite:not_configured",
                checked_at,
                {"reason": "book_lore_path_not_configured", "scope": "catalog"},
            )

        source = f"sqlite:{self.book_lore_path}"
        try:
            with _readonly_connection(self.book_lore_path) as connection:
                existing = _existing_tables(connection)
                missing_tables = sorted(set(_BOOK_LORE_TABLES) - existing)
                counts = {
                    table: _scalar(connection, f'SELECT COUNT(*) FROM "{table}"')
                    for table in _BOOK_LORE_TABLES
                    if table in existing
                }
                evidence = {
                    "scope": "catalog",
                    "path": str(self.book_lore_path),
                    "size_bytes": self.book_lore_path.stat().st_size,
                    "tables": counts,
                    "missing_tables": missing_tables,
                    "count": sum(counts.values()),
                }
                if missing_tables:
                    health = "drift"
                elif evidence["count"] == 0:
                    health = "empty"
                else:
                    health = "healthy"
                return _result("book_lore_source", health, source, checked_at, evidence)
        except Exception as exc:
            return _result(
                "book_lore_source",
                "probe_error",
                source,
                checked_at,
                {"scope": "catalog", **_error_evidence(exc)},
            )


@contextmanager
def _readonly_connection(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise RuntimeError("sqlite_query_only_not_enabled")
        yield connection
    finally:
        connection.close()


def _optional_path(value: str | Path | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def _existing_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _scalar(connection: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> int:
    row = connection.execute(sql, tuple(params)).fetchone()
    return int(row[0] or 0) if row is not None else 0


def _committed_watermark(connection: sqlite3.Connection) -> int | None:
    if "write_operations" not in _existing_tables(connection):
        return None
    return _scalar(
        connection,
        "SELECT COALESCE(MAX(write_sequence), 0) FROM write_operations WHERE status='committed'",
    )


def _is_tag_kind(kind: str) -> bool:
    return str(kind).strip().lower() in {"tag", "tags"}


def _index_kinds_match(expected: str, actual: str) -> bool:
    if _is_tag_kind(expected):
        return _is_tag_kind(actual)
    return expected == actual


def _watermark_relation(
    index_watermark: int,
    committed_watermark: int | None,
) -> tuple[str, int | None]:
    if committed_watermark is None:
        return "unavailable", None
    delta = index_watermark - committed_watermark
    if delta < 0:
        return "lag", delta
    if delta > 0:
        return "ahead", delta
    return "equal", 0


def _result(
    name: str,
    health: str,
    source: str,
    checked_at: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if health not in HEALTH_VALUES:
        raise ValueError(f"unsupported diagnostics health: {health}")
    return {
        "name": name,
        "health": health,
        "source": source,
        "checked_at": checked_at,
        "evidence": dict(evidence),
    }


def _error_evidence(exc: BaseException) -> dict[str, str]:
    return {
        "error_type": type(exc).__name__,
        "error": str(exc)[:500],
    }


def _overall_health(checks: Sequence[Mapping[str, Any]]) -> str:
    if not checks:
        return "not_configured"
    healths = [str(item.get("health") or "probe_error") for item in checks]
    for value in ("probe_error", "drift", "repairing"):
        if value in healths:
            return value
    if "healthy" in healths:
        return "healthy"
    if "empty" in healths:
        return "empty"
    return "not_configured"


__all__ = ["DiagnosticsService", "HEALTH_VALUES", "IndexSource"]
