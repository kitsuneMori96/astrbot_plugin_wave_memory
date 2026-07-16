from __future__ import annotations

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from engine.db.job_repo import JobRepository
from services.data_governance_jobs import (
    DATA_GOVERNANCE_PREVIEW_KIND,
    DATA_GOVERNANCE_RULE_VERSION,
    DataGovernancePreviewError,
    DataGovernancePreviewJobs,
    build_data_governance_handlers,
    build_preview_request,
    create_sqlite_snapshot,
    enqueue_preview_job,
    open_readonly_snapshot,
    snapshot_sha256,
)
from services.durable_jobs import DurableJobService
from webui.blueprints import maintenance


_SCOPE = {
    "bot_id": "bot-alpha",
    "visibility": "group",
    "session": {
        "id": "qq:group:g1",
        "platform_id": "qq",
        "kind": "group",
        "conversation_id": "g1",
    },
}


def _request_body(**overrides):
    body = {
        "idempotency_key": "preview-client-request-1",
        "scope": copy.deepcopy(_SCOPE),
        "chunk_size": 2,
        "sample_limit": 2,
        "count_limit": 100,
    }
    body.update(overrides)
    return body


def _create_source(path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                group_id TEXT,
                content TEXT,
                bot_id TEXT,
                session_id TEXT,
                visibility TEXT,
                resolution_state TEXT,
                quarantine INTEGER,
                source TEXT
            );
            CREATE TABLE scoped_memory_tags (
                bot_id TEXT, session_id TEXT, visibility TEXT,
                memory_id INTEGER, tag_id INTEGER
            );
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER);

            INSERT INTO memories VALUES
                (1, 'g1', '第一条符合候选规则的长期记忆', 'bot-alpha', 'qq:group:g1', 'group', 'resolved', 0, 'live'),
                (2, 'g1', '已经具有正式 scoped Tag 的记忆', 'bot-alpha', 'qq:group:g1', 'group', 'resolved', 0, 'live'),
                (3, 'g1', '短', 'bot-alpha', 'qq:group:g1', 'group', 'resolved', 0, 'live'),
                (4, 'g1', '处于隔离状态而不能进入候选的记忆', 'bot-alpha', 'qq:group:g1', 'group', 'resolved', 1, 'live'),
                (5, 'g1', '尚未完成 Scope 解析的旧记忆', 'bot-alpha', 'qq:group:g1', 'group', 'unresolved_legacy', 0, 'live'),
                (6, 'g1', '明确标记为噪音来源的长期记忆', 'bot-alpha', 'qq:group:g1', 'group', 'resolved', 0, 'noise'),
                (7, 'wrong-group', '会话与 group_id 不一致的长期记忆', 'bot-alpha', 'qq:group:g1', 'group', 'resolved', 0, 'live'),
                (8, 'g2', '另一个 Scope 的长期记忆', 'bot-beta', 'qq:group:g2', 'group', 'resolved', 0, 'live');
            INSERT INTO tags VALUES (10, '有效旧标签');
            INSERT INTO scoped_memory_tags VALUES ('bot-alpha', 'qq:group:g1', 'group', 2, 101);
            INSERT INTO memory_tags(memory_id, tag_id) VALUES
                (1, 10),
                (999, 10),
                (1, 999),
                (998, 998),
                (8, 997);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _business_rows(path):
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("memories", "scoped_memory_tags", "tags", "memory_tags")
        }
    finally:
        connection.close()


class _Service:
    def __init__(self, *, cancel_on_check=None, crash_after_update=None):
        self.cancel_on_check = cancel_on_check
        self.crash_after_update = crash_after_update
        self.cancel_checks = 0
        self.updates = []

    async def cancellation_requested(self, run_id):
        self.cancel_checks += 1
        return self.cancel_on_check is not None and self.cancel_checks >= self.cancel_on_check

    async def update_progress(self, run_id, **kwargs):
        self.updates.append({"run_id": run_id, **copy.deepcopy(kwargs)})
        if self.crash_after_update is not None and len(self.updates) >= self.crash_after_update:
            raise RuntimeError("simulated_worker_crash")
        return SimpleNamespace(cursor=kwargs["cursor"], progress=kwargs["progress"])


def _runner(service):
    return SimpleNamespace(
        service=service,
        lease_owner="preview-worker-1",
        lease_seconds=37.0,
    )


def _run(cursor=None):
    return SimpleNamespace(
        run_id="governance-run-1",
        cursor=cursor
        or {
            "phase": "queued",
            "rule_version": DATA_GOVERNANCE_RULE_VERSION,
            "after_memory_id": 0,
            "after_memory_tag_rowid": 0,
        },
    )


def _request(**body_overrides):
    return SimpleNamespace(payload=build_preview_request(_request_body(**body_overrides))["payload"])


def test_preview_request_is_fixed_scope_dry_run_and_idempotent():
    first = build_preview_request(_request_body())
    replay = build_preview_request(_request_body())

    assert replay == first
    assert first["kind"] == DATA_GOVERNANCE_PREVIEW_KIND
    assert first["payload"]["mode"] == "dry_run"
    assert first["payload"]["tag_policy"] == "missing_only"
    assert first["payload"]["rule_version"] == DATA_GOVERNANCE_RULE_VERSION
    assert first["payload"]["scope"] == {
        "bot_id": "bot-alpha",
        "session_id": "qq:group:g1",
        "visibility": "group",
        "group_id": "g1",
    }
    assert first["schedule_slot"] == "preview-client-request-1"

    with pytest.raises(DataGovernancePreviewError, match="idempotency_key_required"):
        build_preview_request(_request_body(idempotency_key=""))
    with pytest.raises(DataGovernancePreviewError, match="missing_only_required"):
        build_preview_request(_request_body(tag_policy="replace"))
    with pytest.raises(DataGovernancePreviewError, match="unsupported_rule_version"):
        build_preview_request(_request_body(rule_version="future"))


@pytest.mark.asyncio
async def test_enqueue_uses_existing_atomic_durable_facade_once_per_call():
    class Jobs:
        def __init__(self):
            self.calls = []
            self.by_key = {}

        async def enqueue(self, **kwargs):
            self.calls.append(kwargs)
            return self.by_key.setdefault(
                kwargs["idempotency_key"],
                SimpleNamespace(job_id="stable-run", request_id="stable-request"),
            )

    jobs = Jobs()
    first = await enqueue_preview_job(jobs, _request_body())
    replay = await enqueue_preview_job(jobs, _request_body())

    assert replay is first
    assert len(jobs.calls) == 2
    assert jobs.calls[0] == jobs.calls[1]
    assert jobs.calls[0]["kind"] == DATA_GOVERNANCE_PREVIEW_KIND


@pytest.mark.asyncio
async def test_preview_replay_returns_the_same_real_durable_request_and_run():
    connection = sqlite3.connect(":memory:")
    JobRepository.migrate(connection)

    class Coordinator:
        async def transaction(self, callback):
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = callback(connection)
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise

        async def read(self, callback):
            return callback(connection)

    jobs = DurableJobService(Coordinator())
    try:
        first = await enqueue_preview_job(jobs, _request_body())
        replay = await enqueue_preview_job(jobs, _request_body())

        assert replay == first
        assert connection.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM background_job_runs").fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_maintenance_api_fails_closed_until_handler_is_registered(monkeypatch):
    class Request:
        async def get_json(self):
            return _request_body()

    monkeypatch.setattr(
        maintenance,
        "get_container",
        lambda: SimpleNamespace(durable_jobs=object()),
    )
    monkeypatch.setattr(maintenance, "request", Request())
    monkeypatch.setattr(maintenance, "jsonify", lambda value: value)

    payload, status = await maintenance.schedule_data_governance_preview()

    assert status == 503
    assert payload["error"]["code"] == "data_governance_preview_unregistered"


@pytest.mark.asyncio
async def test_maintenance_api_only_enqueues_registered_durable_kind(monkeypatch):
    class Envelope:
        def to_dict(self):
            return {
                "accepted": True,
                "request_id": "request-1",
                "job_id": "run-1",
                "status": "queued",
                "operation": {
                    "id": "run-1",
                    "kind": DATA_GOVERNANCE_PREVIEW_KIND,
                    "status": "queued",
                },
            }

    class Jobs:
        def __init__(self):
            self.calls = []

        async def enqueue(self, **kwargs):
            self.calls.append(kwargs)
            return Envelope()

    class Request:
        async def get_json(self):
            return _request_body()

    jobs = Jobs()
    monkeypatch.setattr(
        maintenance,
        "get_container",
        lambda: SimpleNamespace(durable_jobs=jobs, data_governance_jobs=object()),
    )
    monkeypatch.setattr(maintenance, "request", Request())
    monkeypatch.setattr(maintenance, "jsonify", lambda value: value)

    payload, status = await maintenance.schedule_data_governance_preview()

    assert status == 202
    assert payload["job_id"] == "run-1"
    assert payload["dry_run"] is True
    assert payload["checkpoint_url"].endswith("/run-1/checkpoint")
    assert payload["cancel_url"].endswith("/run-1/cancel")
    assert jobs.calls[0]["kind"] == DATA_GOVERNANCE_PREVIEW_KIND
    assert "source_db_path" not in jobs.calls[0]["payload"]


def test_sqlite_snapshot_is_hashable_and_opened_query_only(tmp_path):
    source = tmp_path / "source.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    _create_source(source)

    create_sqlite_snapshot(source, snapshot)
    first_hash = snapshot_sha256(snapshot)
    connection = open_readonly_snapshot(snapshot)
    try:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 8
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM memories")
    finally:
        connection.close()

    assert len(first_hash) == 64
    assert snapshot_sha256(snapshot) == first_hash


@pytest.mark.asyncio
async def test_full_preview_reports_bounded_counts_samples_exclusions_and_no_business_writes(tmp_path):
    source = tmp_path / "source.sqlite3"
    snapshots = tmp_path / "snapshots"
    _create_source(source)
    before = _business_rows(source)
    service = _Service()
    jobs = DataGovernancePreviewJobs(source_db_path=source, snapshot_dir=snapshots)

    result = await jobs.preview(_run(), _request(sample_limit=1, count_limit=2), _runner(service))

    assert result["completed"] is True
    assert result["mode"] == "dry_run"
    assert result["source_business_mutated"] is False
    assert len(result["snapshot_hash"]) == 64
    assert result["counts"]["scoped_tag_candidates"] == {
        "value": 1,
        "limit": 2,
        "truncated": False,
    }
    assert result["counts"]["scoped_memories_scanned"]["value"] == 2
    assert result["counts"]["scoped_memories_scanned"]["truncated"] is True
    assert result["counts"]["legacy_memory_tag_orphans"]["value"] == 2
    assert result["counts"]["legacy_memory_tag_orphans"]["truncated"] is True
    assert result["exclusions"]["already_has_scoped_tags"]["value"] == 1
    assert result["exclusions"]["content_too_short"]["value"] == 1
    assert result["exclusions"]["quarantined"]["value"] == 1
    assert result["exclusions"]["resolution_state_not_resolved"]["value"] == 1
    assert result["exclusions"]["noise_source"]["value"] == 1
    assert result["exclusions"]["canonical_group_mismatch"]["value"] == 1
    assert result["legacy_orphan_reasons"]["missing_memory"]["value"] == 1
    assert result["legacy_orphan_reasons"]["missing_tag"]["value"] == 2
    assert result["legacy_orphan_reasons"]["missing_memory_and_tag"]["value"] == 1
    assert result["legacy_scope_impact"]["requested_scope"]["value"] == 1
    assert result["legacy_scope_impact"]["other_or_legacy_scope"]["value"] == 1
    assert result["legacy_scope_impact"]["unattributable_missing_memory"]["value"] == 2
    assert len(result["samples"]["scoped_tag_candidates"]) == 1
    assert len(result["samples"]["exclusions"]) == 1
    assert len(result["samples"]["legacy_memory_tag_orphans"]) == 1
    assert result["checkpoint"]["phase"] == "completed"
    assert result["checkpoint"]["after_memory_id"] == 7
    assert result["checkpoint"]["after_memory_tag_rowid"] == 5
    assert result["checkpoint"]["snapshot_retained"] is False
    assert _business_rows(source) == before
    assert list(snapshots.glob("*.sqlite3")) == []

    assert service.updates
    assert all(update["lease_owner"] == "preview-worker-1" for update in service.updates)
    assert all(update["lease_seconds"] == 37.0 for update in service.updates)
    assert all(update["progress"]["dry_run"] is True for update in service.updates)
    assert service.updates[-1]["cursor"]["completed"] is True


@pytest.mark.asyncio
async def test_recovery_reuses_snapshot_and_ignores_later_source_changes(tmp_path):
    source = tmp_path / "source.sqlite3"
    snapshots = tmp_path / "snapshots"
    _create_source(source)
    jobs = DataGovernancePreviewJobs(source_db_path=source, snapshot_dir=snapshots)
    crashing_service = _Service(crash_after_update=1)

    with pytest.raises(RuntimeError, match="simulated_worker_crash"):
        await jobs.preview(_run(), _request(chunk_size=1), _runner(crashing_service))

    checkpoint = crashing_service.updates[-1]["cursor"]
    original_hash = checkpoint["snapshot"]["sha256"]
    assert checkpoint["after_memory_id"] == 1
    assert list(snapshots.glob("*.sqlite3"))

    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "INSERT INTO memories VALUES (99, 'g1', '快照之后才写入的候选记忆', 'bot-alpha', "
            "'qq:group:g1', 'group', 'resolved', 0, 'live')"
        )
        connection.commit()
    finally:
        connection.close()

    recovery_service = _Service()
    result = await jobs.preview(_run(checkpoint), _request(chunk_size=1), _runner(recovery_service))

    assert result["completed"] is True
    assert result["snapshot_hash"] == original_hash
    assert result["counts"]["scoped_tag_candidates"]["value"] == 1
    assert result["checkpoint"]["after_memory_id"] == 7
    assert all(
        sample["memory_id"] != 99
        for sample in result["samples"]["scoped_tag_candidates"]
    )
    assert list(snapshots.glob("*.sqlite3")) == []


@pytest.mark.asyncio
async def test_cancellation_is_checked_between_chunks_and_removes_snapshot(tmp_path):
    source = tmp_path / "source.sqlite3"
    snapshots = tmp_path / "snapshots"
    _create_source(source)
    before = _business_rows(source)
    service = _Service(cancel_on_check=4)
    jobs = DataGovernancePreviewJobs(source_db_path=source, snapshot_dir=snapshots)

    result = await jobs.preview(_run(), _request(chunk_size=1), _runner(service))

    assert result["cancelled"] is True
    assert result["completed"] is False
    assert result["source_business_mutated"] is False
    assert service.cancel_checks >= 4
    assert service.updates == []
    assert list(snapshots.glob("*.sqlite3")) == []
    assert _business_rows(source) == before


def test_registration_helper_exposes_only_the_preview_kind(tmp_path):
    handlers = build_data_governance_handlers(
        source_db_path=tmp_path / "source.sqlite3",
        snapshot_dir=tmp_path / "snapshots",
    )

    assert set(handlers) == {DATA_GOVERNANCE_PREVIEW_KIND}
    assert callable(handlers[DATA_GOVERNANCE_PREVIEW_KIND])
