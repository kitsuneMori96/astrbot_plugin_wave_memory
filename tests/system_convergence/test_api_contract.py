"""R13 maintainable API contracts for paging, options, refs, traces, and state semantics."""

from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from engine.db.connection import ConnectionManager
from engine.db.learning_repository import LearningRepositories
from engine.db.memory_repo import MemoryRepo
from engine.db.outbox_repo import OutboxRepository
from services.injection.trace_store import InjectionTraceStore
from tests.system_convergence.contracts import (
    contract_assert,
    contract_fail,
    load_api_composition_adapter,
    require_module,
)
from webui.container import ServiceContainer, get_container


@dataclass(frozen=True)
class _RegistryInput:
    bots: tuple[Mapping[str, Any], ...]
    sessions: tuple[Mapping[str, Any], ...]
    channels: tuple[Mapping[str, Any], ...]
    failure: Exception | None = None


@dataclass
class _MutableRequestScopeInput:
    current: Any


class _ApiMemoryRepo(MemoryRepo):
    """Thin legacy WebUI facade over the real production MemoryRepo.

    The adapter contains no SQL or mutation policy: writes use the inherited production
    ``MemoryRepo.update_memory`` implementation, while detail lookup delegates to its public read.
    """

    @property
    def conn(self):
        return self.cm

    def get_memory_detail(self, memory_id: int):
        return self.get_memory_by_id(memory_id)


class _ApiWriteCoordinator:
    """Test adapter preserving the production transaction(callback) contract."""

    _consumer_names = ()

    def __init__(self, manager: ConnectionManager):
        self.manager = manager

    async def transaction(self, callback, *, actor=None):
        del actor
        with self.manager.write_transaction() as connection:
            return callback(connection)

    async def read(self, callback):
        return callback(self.manager.conn)


def _registry_input(mode: str) -> _RegistryInput:
    if mode == "error":
        return _RegistryInput((), (), (), RuntimeError("injected scope registry failure"))
    if mode == "empty":
        return _RegistryInput((), (), ())
    return _RegistryInput(
        bots=(
            {
                "db_id": "bot-alpha",
                "name": "Synthetic Bot",
                "qq_id": "900000001",
                "aliases": (),
                "status": "active",
            },
        ),
        sessions=(
            {
                "id": "qq:group:group-alpha",
                "platform_id": "qq",
                "kind": "group",
                "conversation_id": "group-alpha",
                "label": "Synthetic Group",
                "source": "runtime-registry",
                "count": 1,
            },
        ),
        channels=({"id": "memory_recall"}, {"id": "facts"}),
    )


def _runtime_scope(reason: str, *, bot_id: str = "bot-alpha"):
    module = require_module("domain.scope", ("SessionRef", "RuntimeScope"), reason)
    session = module.SessionRef(
        id="qq:group:group-alpha",
        platform_id="qq",
        kind="group",
        conversation_id="group-alpha",
    )
    return module.RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=session,
        subject_principal_id="qq:user:900000001",
    )


@asynccontextmanager
async def _api_context(
    reason: str,
    *,
    repositories_override=None,
    seed_memory=False,
    seed_trace=False,
    registry_input=None,
    request_scope_input=None,
    use_api_binding=False,
    close_backend_before_request=False,
):
    temp_dir = tempfile.TemporaryDirectory(prefix="system-convergence-api-")
    manager = ConnectionManager(os.path.join(temp_dir.name, "api.sqlite3"))
    connection = manager.conn
    reset_error = None
    try:
        # The temporary facade only adapts the current WebUI interface; all reads and writes still
        # execute in the inherited production MemoryRepo against a real SQLite database.
        memory_repository = _ApiMemoryRepo(manager)
        repositories = LearningRepositories.from_connection(connection, now=lambda: 100.0)
        for ddl in (
            "ALTER TABLE memories ADD COLUMN bot_id TEXT",
            "ALTER TABLE memories ADD COLUMN session_id TEXT",
            "ALTER TABLE memories ADD COLUMN visibility TEXT",
            "ALTER TABLE memories ADD COLUMN resolution_state TEXT",
            "ALTER TABLE memories ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
        ):
            connection.execute(ddl)
        connection.executescript(
            """CREATE TABLE IF NOT EXISTS facts (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, predicate TEXT,
                   object TEXT, confidence REAL, group_id TEXT
               );
               CREATE TABLE IF NOT EXISTS user_profiles (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, group_id TEXT,
                   bot_id TEXT, affection REAL, metadata TEXT, updated_at REAL
               );
               CREATE TABLE IF NOT EXISTS person_registry (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, qq_id TEXT, display_name TEXT,
                   aliases TEXT, metadata TEXT
               );"""
        )
        if seed_memory:
            connection.execute(
                "INSERT INTO memories("
                "id,content,sender_id,sender_name,group_id,source,timestamp,"
                "bot_id,session_id,visibility,resolution_state,version"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    987654321,
                    "synthetic memory",
                    "qq:user:900000001",
                    "Synthetic",
                    "group-alpha",
                    "chat",
                    100.0,
                    "bot-alpha",
                    "qq:group:group-alpha",
                    "group",
                    "resolved",
                    1,
                ),
            )
        if seed_trace:
            trace_store = InjectionTraceStore(connection)
            trace_store.ensure_schema()
            trace_store.record(
                {
                    "trace_id": "trace-synthetic-1",
                    "timestamp": 100.0,
                    "mode": "full",
                    "group_id": "group-alpha",
                    "sender_id": "qq:user:900000001",
                    "bot_id": "bot-alpha",
                    "message": "synthetic request",
                    "final_text": "synthetic result",
                    "status": "ok",
                },
                [],
            )
        connection.commit()
        with manager.write_transaction() as transaction:
            OutboxRepository.migrate(transaction)

        ServiceContainer.reset()
        container = get_container()
        container.initialize(
            db=memory_repository,
            query_engine=None,
            embedding_service=None,
            memory_index=None,
            tag_index=None,
            cooccurrence=None,
            write_gateway=SimpleNamespace(coordinator=_ApiWriteCoordinator(manager)),
            password="",
        )
        container.configure_learning_services(
            repositories=repositories if repositories_override is None else repositories_override
        )
        from webui.app import create_app

        if use_api_binding:
            adapter = load_api_composition_adapter(reason)
            try:
                app = adapter.create_app(
                    create_app,
                    registry_input=registry_input,
                    request_scope_input=request_scope_input,
                )
            except TypeError as exc:
                contract_fail(
                    reason,
                    "missing_contract: webui.app.create_app keyword-only scope composition slots: "
                    f"{exc}",
                )
        else:
            app = create_app()
        async with app.test_app():
            if close_backend_before_request:
                manager.close()
            yield app.test_client(), repositories, connection
    finally:
        try:
            ServiceContainer.reset()
        except Exception as exc:
            reset_error = exc
        finally:
            manager.close()
            temp_dir.cleanup()
        contract_assert(reset_error is None, reason, f"container reset failed after DB close: {reset_error!r}")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _page_violations(label: str, status: int, payload, *, limit: int, offset: int) -> list[str]:
    violations: list[str] = []
    if status != 200:
        return [f"{label}: HTTP {status}"]
    if not isinstance(payload, dict):
        return [f"{label}: body is not object"]
    required_top = {"items", "page"}
    if not required_top.issubset(payload):
        violations.append(f"{label}: missing top keys {sorted(required_top - set(payload))}")
    forbidden_flat = {"total", "size", "limit", "offset", "has_more"} & set(payload)
    if forbidden_flat:
        violations.append(f"{label}: legacy flat paging keys {sorted(forbidden_flat)}")
    items = payload.get("items")
    page = payload.get("page")
    if not isinstance(items, list):
        violations.append(f"{label}: items is not list")
    if not isinstance(page, dict):
        violations.append(f"{label}: page is not object")
        return violations
    required_page = {
        "total", "total_status", "reason_code", "limit", "offset", "page", "page_count", "has_more"
    }
    missing = required_page - set(page)
    if missing:
        violations.append(f"{label}: page missing {sorted(missing)}")
        return violations
    if not _is_int(page["limit"]) or not _is_int(page["offset"]) or not _is_int(page["page"]):
        violations.append(f"{label}: paging integers invalid")
    elif page["limit"] != limit or page["offset"] != offset or page["page"] != offset // limit + 1:
        violations.append(f"{label}: paging arithmetic invalid")
    if not isinstance(page["has_more"], bool):
        violations.append(f"{label}: has_more is not bool")
    if isinstance(items, list) and len(items) > limit:
        violations.append(f"{label}: len(items) exceeds limit")
    if page["total_status"] == "exact":
        total = page["total"]
        if not _is_int(total) or total < 0 or page["reason_code"] is not None:
            violations.append(f"{label}: exact total semantics invalid")
        else:
            expected_count = math.ceil(total / limit) if total else 0
            if page["page_count"] != expected_count:
                violations.append(f"{label}: page_count arithmetic invalid")
            if isinstance(items, list) and page["has_more"] != (offset + len(items) < total):
                violations.append(f"{label}: has_more arithmetic invalid")
    elif page["total_status"] == "unavailable":
        if page["total"] is not None or page["page_count"] is not None or not page["reason_code"]:
            violations.append(f"{label}: unavailable total semantics invalid")
    else:
        violations.append(f"{label}: invalid total_status")
    return violations


async def _assert_page_endpoint(reason: str, label: str, url: str, *, use_api_binding: bool = False, request_scope_input=None) -> None:
    async with _api_context(
        reason,
        use_api_binding=use_api_binding,
        registry_input=_registry_input("healthy") if use_api_binding else None,
        request_scope_input=request_scope_input,
    ) as (client, _, _):
        response = await client.get(url)
        violations = _page_violations(
            label, response.status_code, await response.get_json(), limit=2, offset=0
        )
    contract_assert(not violations, reason, " | ".join(violations))


@pytest.mark.asyncio
async def test_memories_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_MEMORIES", "memories", "/api/memories?limit=2&offset=0"
    )


@pytest.mark.asyncio
async def test_learning_sources_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_LEARNING_SOURCES",
        "learning-sources",
        "/api/learning-center/sources?bot_id=bot-alpha&limit=2&offset=0",
    )


@pytest.mark.asyncio
async def test_learning_jobs_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_LEARNING_JOBS",
        "learning-jobs",
        "/api/learning-center/jobs?bot_id=bot-alpha&limit=2&offset=0",
    )


@pytest.mark.asyncio
async def test_learning_candidates_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_LEARNING_CANDIDATES",
        "learning-candidates",
        "/api/learning-center/candidates?bot_id=bot-alpha&limit=2&offset=0",
    )


@pytest.mark.asyncio
async def test_learning_promotions_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_LEARNING_PROMOTIONS",
        "learning-promotions",
        "/api/learning-center/promotions?bot_id=bot-alpha&limit=2&offset=0",
    )


@pytest.mark.asyncio
async def test_trace_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_TRACES", "traces", "/api/observatory/traces?limit=2&offset=0"
    )


@pytest.mark.asyncio
async def test_facts_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_FACTS", "facts", "/api/facts?limit=2&offset=0"
    )


@pytest.mark.asyncio
async def test_people_list_uses_complete_nested_page_response():
    await _assert_page_endpoint(
        "R13_PAGE_PEOPLE",
        "people",
        "/api/people?bot_id=bot-alpha&session_id=qq:group:group-alpha&visibility=group&limit=2&offset=0",
        use_api_binding=True,
        request_scope_input=_MutableRequestScopeInput(lambda: _runtime_scope("R13_PAGE_PEOPLE")),
    )


async def _get_options(reason: str, mode: str):
    async with _api_context(
        reason,
        registry_input=_registry_input(mode),
        use_api_binding=True,
    ) as (client, _, _):
        response = await client.get("/api/options/scopes")
        return response.status_code, await response.get_json()


@pytest.mark.asyncio
async def test_scope_options_healthy_registry_payload():
    reason = "R13_OPTIONS_HEALTHY"
    status, payload = await _get_options(reason, "healthy")
    contract_assert(status == 200 and isinstance(payload, dict), reason, f"HTTP/body={status}/{payload!r}")
    bots = payload.get("bots")
    sessions = payload.get("sessions")
    channels = payload.get("channels")
    contract_assert(
        isinstance(bots, list) and all(isinstance(item, dict) for item in bots),
        reason,
        "bots must be a list of objects",
    )
    contract_assert(
        isinstance(sessions, list) and bool(sessions) and all(isinstance(item, dict) for item in sessions),
        reason,
        "sessions must be a non-empty list of objects",
    )
    contract_assert(
        isinstance(channels, list) and all(isinstance(item, dict) for item in channels),
        reason,
        "channels must be a list of objects",
    )
    contract_assert({item.get("db_id") for item in bots} == {"bot-alpha"}, reason, "bot db_id projection wrong")
    contract_assert(
        sessions[0].get("id") == "qq:group:group-alpha"
        and sessions[0].get("platform_id") == "qq"
        and sessions[0].get("kind") == "group"
        and sessions[0].get("conversation_id") == "group-alpha",
        reason,
        "Design SessionRef projection is incomplete",
    )
    contract_assert({item.get("id") for item in channels} == {"memory_recall", "facts"}, reason, "channels wrong")
    contract_assert(isinstance(payload.get("generated_at"), (int, float)), reason, "generated_at missing")
    contract_assert((payload.get("source") or {}).get("health") == "healthy", reason, "health not healthy")


@pytest.mark.asyncio
async def test_scope_options_empty_registry_is_explicit():
    reason = "R13_OPTIONS_EMPTY"
    status, payload = await _get_options(reason, "empty")
    contract_assert(isinstance(payload, dict), reason, "empty options body must be an object")
    source = payload.get("source", {})
    contract_assert(
        status == 200
        and payload.get("bots") == []
        and payload.get("sessions") == []
        and payload.get("channels") == []
        and source.get("health") == "empty"
        and source.get("reason_code") == "registry_empty",
        reason,
        f"empty registry semantics invalid: {status}/{payload!r}",
    )


@pytest.mark.asyncio
async def test_scope_options_provider_error_is_retryable():
    reason = "R13_OPTIONS_ERROR"
    status, payload = await _get_options(reason, "error")
    contract_assert(isinstance(payload, dict), reason, "error options body must be an object")
    error = payload.get("error", {})
    source = payload.get("source", {})
    contract_assert(
        status == 503
        and error.get("code") == "options_source_unavailable"
        and error.get("retryable") is True
        and source.get("health") == "error"
        and bool(source.get("reason_code")),
        reason,
        f"provider error semantics invalid: {status}/{payload!r}",
    )


def _first_item(payload, reason: str):
    items = payload.get("items") if isinstance(payload, dict) else None
    contract_assert(isinstance(items, list) and bool(items), reason, "seeded list item missing")
    contract_assert(isinstance(items[0], dict), reason, "seeded list item must be an object")
    return items[0]


def _middle_mutation(value: str) -> str:
    index = len(value) // 2
    replacement = "A" if value[index] != "A" else "B"
    return value[:index] + replacement + value[index + 1 :]


async def _load_working_object_ref(client, reason: str):
    listed = await client.get("/api/memories?limit=1&offset=0")
    contract_assert(listed.status_code == 200, reason, f"seeded list HTTP {listed.status_code}")
    item = _first_item(await listed.get_json(), reason)
    ref = item.get("ref")
    detail_url = item.get("detail_url")
    contract_assert(isinstance(ref, str) and len(ref) >= 3, reason, "server ref missing")
    contract_assert(isinstance(detail_url, str) and bool(detail_url), reason, "detail_url missing")
    baseline = await client.get(detail_url)
    baseline_body = await baseline.get_json()
    contract_assert(
        baseline.status_code == 200 and isinstance(baseline_body, dict),
        reason,
        f"baseline detail_url did not resolve: HTTP {baseline.status_code}",
    )
    return item, ref, detail_url, baseline_body


def _assert_non_leaking_not_found(response, body, forbidden: tuple[str, ...], reason: str) -> None:
    contract_assert(isinstance(body, dict), reason, "controlled not_found body must be an object")
    error = body.get("error")
    contract_assert(
        response.status_code == 404 and isinstance(error, dict) and error.get("code") == "not_found",
        reason,
        f"controlled not_found missing: HTTP {response.status_code}",
    )
    serialized = json.dumps(body, ensure_ascii=False).lower()
    protected = forbidden + (
        "bot_id",
        "group_id",
        "memory_id",
        "object_id",
        "aggregate_id",
        "version",
        "scope_ref",
        "scoperef",
    )
    leaked = [token for token in protected if token and token.lower() in serialized]
    contract_assert(
        "item" not in body and not leaked,
        reason,
        f"not_found leaked SQL locators, version, or ScopeRef: {leaked!r}",
    )


@pytest.mark.asyncio
async def test_server_object_ref_detail_url_opens_real_scoped_object():
    reason = "R13_OBJECT_REF_DETAIL"
    request_scope = _MutableRequestScopeInput(lambda: _runtime_scope(reason))
    async with _api_context(
        reason,
        seed_memory=True,
        request_scope_input=request_scope,
        use_api_binding=True,
    ) as (client, _, connection):
        _, ref, _, body = await _load_working_object_ref(client, reason)
        detail_item = body.get("item")
        contract_assert(isinstance(detail_item, dict), reason, "detail item must be an object")
        canonical = connection.execute(
            "SELECT content, version FROM memories WHERE id=?", (987654321,)
        ).fetchone()
        contract_assert(canonical is not None, reason, "seeded canonical memory is missing")
        contract_assert(detail_item.get("ref") == ref, reason, "detail did not preserve ref")
        contract_assert(
            detail_item.get("content") == canonical[0]
            and detail_item.get("version") == canonical[1],
            reason,
            f"detail does not represent canonical row: detail={detail_item!r}, canonical={canonical!r}",
        )


@pytest.mark.asyncio
async def test_middle_character_ref_tamper_is_non_leaking_404():
    reason = "R13_OBJECT_REF_TAMPER"
    request_scope = _MutableRequestScopeInput(lambda: _runtime_scope(reason))
    async with _api_context(
        reason,
        seed_memory=True,
        request_scope_input=request_scope,
        use_api_binding=True,
    ) as (client, _, _):
        _, ref, detail_url, _ = await _load_working_object_ref(client, reason)
        contract_assert(ref in detail_url, reason, "detail_url does not carry server ref")
        tampered_ref = _middle_mutation(ref)
        response = await client.get(detail_url.replace(ref, tampered_ref, 1))
        body = await response.get_json()
        _assert_non_leaking_not_found(
            response,
            body,
            ("987654321", ref, tampered_ref, "bot-alpha", "group-alpha", '"scope"', '"version"'),
            reason,
        )


@pytest.mark.asyncio
async def test_server_ref_is_rejected_after_real_request_scope_switch():
    reason = "R13_OBJECT_REF_SCOPE"
    request_scope = _MutableRequestScopeInput(
        lambda: _runtime_scope(reason, bot_id="bot-alpha")
    )
    async with _api_context(
        reason,
        seed_memory=True,
        request_scope_input=request_scope,
        use_api_binding=True,
    ) as (client, _, _):
        _, ref, detail_url, _ = await _load_working_object_ref(client, reason)
        request_scope.current = lambda: _runtime_scope(reason, bot_id="bot-beta")
        response = await client.get(detail_url)
        body = await response.get_json()
        _assert_non_leaking_not_found(
            response,
            body,
            ("987654321", ref, "bot-alpha", "bot-beta", "group-alpha", '"scope"', '"version"'),
            reason,
        )


@pytest.mark.asyncio
async def test_old_server_ref_expires_after_scoped_server_url_update():
    reason = "R13_OBJECT_REF_VERSION"
    request_scope = _MutableRequestScopeInput(lambda: _runtime_scope(reason))
    async with _api_context(
        reason,
        seed_memory=True,
        request_scope_input=request_scope,
        use_api_binding=True,
    ) as (client, _, connection):
        item, ref, detail_url, body = await _load_working_object_ref(client, reason)
        detail_item = body.get("item") if isinstance(body.get("item"), dict) else {}
        mutation_url = item.get("mutation_url") or detail_item.get("mutation_url") or detail_url
        contract_assert(
            isinstance(mutation_url, str) and ref in mutation_url,
            reason,
            "server did not issue a scoped mutation URL carrying ObjectRef",
        )
        before = connection.execute(
            "SELECT content, version FROM memories WHERE id=?", (987654321,)
        ).fetchone()
        contract_assert(before is not None, reason, "seeded canonical memory is missing")
        updated = await client.put(mutation_url, json={"content": "updated synthetic memory"})
        contract_assert(
            updated.status_code == 200,
            reason,
            f"server-issued scoped update failed: {updated.status_code}",
        )
        after = connection.execute(
            "SELECT content, version FROM memories WHERE id=?", (987654321,)
        ).fetchone()
        contract_assert(
            after is not None
            and after[0] == "updated synthetic memory"
            and isinstance(after[1], int)
            and after[1] > before[1],
            reason,
            f"scoped mutation did not update canonical content/version: {before!r}->{after!r}",
        )
        listed = await client.get("/api/memories?limit=1&offset=0")
        contract_assert(listed.status_code == 200, reason, "updated object list lookup failed")
        refreshed_item = _first_item(await listed.get_json(), reason)
        refreshed_ref = refreshed_item.get("ref")
        refreshed_url = refreshed_item.get("detail_url")
        contract_assert(
            isinstance(refreshed_ref, str)
            and refreshed_ref != ref
            and isinstance(refreshed_url, str),
            reason,
            "updated canonical version did not receive a new server ObjectRef",
        )
        refreshed = await client.get(refreshed_url)
        refreshed_body = await refreshed.get_json()
        refreshed_detail = (
            refreshed_body.get("item") if isinstance(refreshed_body, dict) else None
        )
        contract_assert(
            refreshed.status_code == 200
            and isinstance(refreshed_detail, dict)
            and refreshed_detail.get("content") == after[0]
            and refreshed_detail.get("version") == after[1],
            reason,
            f"new ObjectRef did not open updated canonical version: {refreshed_body!r}",
        )
        expired = await client.get(detail_url)
        expired_body = await expired.get_json()
        _assert_non_leaking_not_found(
            expired,
            expired_body,
            ("987654321", ref, "bot-alpha", "group-alpha", '"scope"', '"version"'),
            reason,
        )


@pytest.mark.asyncio
async def test_bare_id_mutation_without_scope_or_ref_is_rejected_without_change():
    reason = "R13_OBJECT_REF_BARE_MUTATION"
    async with _api_context(reason, seed_memory=True) as (client, _, connection):
        before = connection.execute(
            "SELECT content, version FROM memories WHERE id=?", (987654321,)
        ).fetchone()
        response = await client.put(
            "/api/memories/987654321", json={"content": "forbidden bare mutation"}
        )
        body = await response.get_json()
        after = connection.execute(
            "SELECT content, version FROM memories WHERE id=?", (987654321,)
        ).fetchone()
    error = body.get("error", {}) if isinstance(body, dict) else {}
    violations = []
    if not (
        response.status_code in {400, 403, 404}
        and isinstance(error, dict)
        and error.get("code") in {"scope_required", "object_ref_required", "not_found"}
    ):
        violations.append(
            f"bare ID mutation was not a controlled rejection: {response.status_code}/{body!r}"
        )
    if after != before:
        violations.append(f"bare mutation changed content/version: {before!r}->{after!r}")
    contract_assert(not violations, reason, "; ".join(violations))


async def _trace_item(client, reason: str):
    response = await client.get("/api/observatory/traces?limit=10&offset=0")
    contract_assert(response.status_code == 200, reason, f"trace list HTTP {response.status_code}")
    return _first_item(await response.get_json(), reason)


@pytest.mark.asyncio
async def test_observatory_trace_list_exposes_real_trace_and_detail_url():
    reason = "R13_TRACE_LIST"
    async with _api_context(reason, seed_trace=True) as (client, _, _):
        item = await _trace_item(client, reason)
        contract_assert(item.get("trace_id") == "trace-synthetic-1", reason, f"real trace missing: {item!r}")
        contract_assert(isinstance(item.get("detail_url"), str), reason, "trace detail_url missing")


@pytest.mark.asyncio
async def test_observatory_trace_detail_follows_server_url():
    reason = "R13_TRACE_DETAIL"
    async with _api_context(reason, seed_trace=True) as (client, _, _):
        item = await _trace_item(client, reason)
        detail_url = item.get("detail_url")
        contract_assert(isinstance(detail_url, str), reason, "trace detail_url missing")
        response = await client.get(detail_url)
        body = await response.get_json()
        contract_assert(isinstance(body, dict), reason, "trace detail body must be an object")
        contract_assert(response.status_code == 200 and body.get("trace_id") == "trace-synthetic-1", reason, f"trace detail invalid: {response.status_code}/{body!r}")


@pytest.mark.asyncio
async def test_trace_without_feedback_has_explicit_none_state():
    reason = "R13_TRACE_FEEDBACK"
    async with _api_context(reason, seed_trace=True) as (client, _, _):
        item = await _trace_item(client, reason)
        detail_url = item.get("detail_url")
        contract_assert(isinstance(detail_url, str), reason, "trace detail_url missing")
        response = await client.get(detail_url)
        body = await response.get_json()
        contract_assert(response.status_code == 200 and isinstance(body, dict), reason, "feedback detail must be HTTP 200 object")
        contract_assert(body.get("feedback_status") == "none" and body.get("feedback") == [], reason, f"feedback none state invalid: {body!r}")


@pytest.mark.asyncio
async def test_unknown_trace_id_is_controlled_non_leaking_404():
    reason = "R13_TRACE_UNKNOWN"
    missing_id = "trace-does-not-exist"
    async with _api_context(reason, seed_trace=True) as (client, _, _):
        response = await client.get(f"/api/observatory/traces/{missing_id}")
        body = await response.get_json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        contract_assert(
            response.status_code == 404
            and error.get("code") == "not_found"
            and missing_id not in json.dumps(body, ensure_ascii=False),
            reason,
            f"unknown trace response invalid: {response.status_code}/{body!r}",
        )


class _ErrorCandidates:
    def list(self, **kwargs):
        raise RuntimeError("injected backend unavailable")


class _UnknownCandidates:
    def list(self, **kwargs):
        return [], None


async def _candidate_state(reason: str, repositories_override=None):
    async with _api_context(reason, repositories_override=repositories_override) as (client, _, _):
        response = await client.get("/api/learning-center/candidates?bot_id=bot-alpha&limit=5&offset=0")
        return response.status_code, await response.get_json()


@pytest.mark.asyncio
async def test_empty_state_is_known_exact_zero():
    reason = "R13_STATE_EMPTY"
    status, payload = await _candidate_state(reason)
    contract_assert(isinstance(payload, dict), reason, "empty state body must be an object")
    page = payload.get("page", {})
    contract_assert(
        status == 200
        and payload.get("items") == []
        and page.get("total") == 0
        and page.get("total_status") == "exact"
        and page.get("reason_code") is None,
        reason,
        f"empty semantics invalid: {status}/{payload!r}",
    )


@pytest.mark.asyncio
async def test_backend_error_state_is_retryable_service_unavailable():
    reason = "R13_STATE_ERROR"
    status, payload = await _candidate_state(reason, SimpleNamespace(candidates=_ErrorCandidates()))
    contract_assert(isinstance(payload, dict), reason, "error state body must be an object")
    error = payload.get("error", {})
    contract_assert(
        status == 503 and error.get("code") == "service_unavailable" and error.get("retryable") is True and "items" not in payload,
        reason,
        f"error semantics invalid: {status}/{payload!r}",
    )


@pytest.mark.asyncio
async def test_unknown_total_state_is_successful_unavailable_total():
    reason = "R13_STATE_UNKNOWN"
    status, payload = await _candidate_state(reason, SimpleNamespace(candidates=_UnknownCandidates()))
    contract_assert(isinstance(payload, dict), reason, "unknown state body must be an object")
    page = payload.get("page", {})
    contract_assert(
        status == 200
        and payload.get("items") == []
        and page.get("total") is None
        and page.get("total_status") == "unavailable"
        and page.get("reason_code") == "source_unknown",
        reason,
        f"unknown semantics invalid: {status}/{payload!r}",
    )
