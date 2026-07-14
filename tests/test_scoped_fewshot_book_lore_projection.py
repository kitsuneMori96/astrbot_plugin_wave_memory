import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from domain.evidence import (
    EvidenceBinding,
    EvidenceDerivation,
    EvidenceRef,
    FULL_EVIDENCE_DERIVATION_CHAIN,
)
from domain.scope import CatalogScope, RuntimeScope, SessionRef


def _runtime_scope(group_id: str = "group-1", *, bot_id: str = "bot-a") -> RuntimeScope:
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(
            id=f"test:group:{group_id}",
            platform_id="test",
            kind="group",
            conversation_id=group_id,
        ),
        subject_principal_id="test:user:user-1",
    )


def _runtime_evidence(scope: RuntimeScope):
    ref = EvidenceRef(
        kind="raw_message",
        id="message:fewshot-1",
        content_hash="sha256:fewshot-1",
        captured_at=100.0,
        source_scope=scope,
        available=True,
    )
    binding = EvidenceBinding(
        evidence_id=ref.id,
        target_scope=scope,
        derivation_chain=("raw_chat", "reviewed_candidate"),
        policy_version="contract-v1",
    )
    return ref, binding


def _catalog_projection_evidence(target: RuntimeScope):
    source = CatalogScope(catalog_id="book-lore", corpus_id="corpus-a", version="v1")
    ref = EvidenceRef(
        kind="reviewed_book_lore_projection",
        id="community:7",
        content_hash="sha256:community-7",
        captured_at=100.0,
        source_scope=source,
        available=True,
    )
    binding = EvidenceBinding(
        evidence_id=ref.id,
        target_scope=target,
        derivation_chain=FULL_EVIDENCE_DERIVATION_CHAIN,
        policy_version="scope-derivation/v1",
    )
    derivation = EvidenceDerivation(
        kind="EvidenceDerivation",
        reviewed=True,
        review_status="reviewed",
        derivation_version="book-lore/v1",
        policy_version="scope-derivation/v1",
        source=source,
        target=target,
        derivation_chain=FULL_EVIDENCE_DERIVATION_CHAIN,
    )
    return source, ref, binding, derivation


def _fewshot_candidate(scope: RuntimeScope) -> dict:
    ref, binding = _runtime_evidence(scope)
    return {
        "id": 11,
        "candidate_type": "few_shot_style",
        "content": "先核实事实，再用简短而克制的方式回应。",
        "evidence": {
            "scope": scope.to_dict(),
            "target_scope": scope.to_dict(),
            "score": 0.92,
            "traits": ["克制"],
            "evidence_refs": [ref.to_dict()],
            "evidence_bindings": [binding.to_dict()],
        },
    }


def _book_lore_candidate(target: RuntimeScope) -> dict:
    source, ref, binding, derivation = _catalog_projection_evidence(target)
    return {
        "id": 22,
        "candidate_type": "book_lore",
        "content": "旧港：潮汐改变商路。",
        "evidence": {
            "scope": target.to_dict(),
            "target_scope": target.to_dict(),
            "catalog_scope": source.to_dict(),
            "community_id": 7,
            "title": "旧港",
            "summary_snapshot": "潮汐改变商路。",
            "rank": 8.5,
            "source_library_id": "lore-a",
            "evidence_refs": [ref.to_dict()],
            "evidence_bindings": [binding.to_dict()],
            "evidence_derivation": derivation.to_dict(),
        },
    }


def test_scoped_projection_migration_is_idempotent_and_does_not_create_raw_catalog(tmp_path):
    from engine.db.migrations.scoped_learning_projections import run_migration

    path = tmp_path / "scoped-projections.db"
    assert run_migration(str(path)) is True
    assert run_migration(str(path)) is True

    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    fewshot_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(scoped_few_shot_examples)")
    }
    book_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(reviewed_book_lore_projections)")
    }
    conn.close()

    assert {"scoped_few_shot_examples", "reviewed_book_lore_projections"} <= tables
    assert "book_communities" not in tables
    assert {
        "runtime_scope_json",
        "evidence_refs_json",
        "evidence_bindings_json",
        "candidate_json",
        "status",
        "revision",
    } <= fewshot_columns
    assert {
        "source_catalog_scope_json",
        "target_runtime_scope_json",
        "evidence_derivation_json",
        "evidence_refs_json",
        "evidence_bindings_json",
        "candidate_json",
        "status",
        "revision",
    } <= book_columns


def test_fewshot_repository_is_exact_scope_and_legacy_table_is_audit_only():
    from engine.db.scoped_learning_projection_repo import ScopedFewShotRepository
    from services.few_shot.service import FewShotService

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE few_shot_examples (
               id INTEGER PRIMARY KEY, content TEXT, score REAL, traits TEXT,
               status TEXT, bot_id TEXT, created_at INTEGER, approved_at INTEGER)"""
    )
    conn.execute(
        "INSERT INTO few_shot_examples VALUES (1, 'legacy 不得注入', 1, '[]', 'approved', 'bot-a', 1, 1)"
    )
    conn.commit()
    repository = ScopedFewShotRepository(conn, now=lambda: 100.0)
    service = FewShotService(
        db=SimpleNamespace(conn=conn),
        repository=repository,
        enabled=True,
        config={"max_inject": 3},
    )
    scope = _runtime_scope()
    other_scope = _runtime_scope("group-2")
    candidate = _fewshot_candidate(scope)
    ref, binding = _runtime_evidence(scope)

    example_id = repository.write_approved(
        scope=scope,
        candidate=candidate,
        evidence_refs=(ref,),
        evidence_bindings=(binding,),
        content=candidate["content"],
        score=0.92,
        traits=("克制",),
        source_candidate_id=11,
        idempotency_key="candidate:11",
    )

    text = service.get_injection(scope=scope, max_items=3)
    assert "先核实事实" in text
    assert "legacy 不得注入" not in text
    assert service.get_injection(scope=other_scope, max_items=3) == ""
    row = repository.get(example_id)
    assert row["scope"].bot_id == scope.bot_id
    assert row["scope"].session == scope.session
    assert row["scope"].subject_principal_id is None
    assert row["evidence_refs"][0] == ref
    assert row["evidence_bindings"][0] == binding
    assert row["candidate"]["id"] == 11
    assert row["status"] == "approved"
    assert row["revision"] == 1
    conn.close()


def test_fewshot_promotion_uses_injected_formal_writer_with_complete_scope_and_evidence():
    from services.learning.domain_promotions import FewShotStylePromotionService

    class Writer:
        def __init__(self):
            self.calls = []

        def write_approved(self, **kwargs):
            self.calls.append(kwargs)
            return 91

    scope = _runtime_scope()
    writer = Writer()
    result = FewShotStylePromotionService(writer).promote(
        candidate=_fewshot_candidate(scope), bot_id="bot-a", target_kind="few_shot"
    )

    assert result["target_id"] == "91"
    assert writer.calls[0]["scope"] == scope
    assert writer.calls[0]["candidate"]["id"] == 11
    assert writer.calls[0]["evidence_refs"][0].source_scope == scope
    assert writer.calls[0]["evidence_bindings"][0].target_scope == scope


def test_coordinator_writer_owns_transaction_and_repository_never_commits_external_tx():
    from engine.db.scoped_learning_projection_repo import (
        CoordinatorScopedProjectionWriter,
        ScopedFewShotRepository,
    )

    conn = sqlite3.connect(":memory:")
    repository = ScopedFewShotRepository(conn, now=lambda: 100.0)

    class NoCommitProxy:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args, **kwargs):
            return self.connection.execute(*args, **kwargs)

        def commit(self):
            raise AssertionError("repository must not commit coordinator-owned tx")

        def rollback(self):
            raise AssertionError("repository must not rollback coordinator-owned tx")

    class SpyCoordinator:
        def __init__(self, connection):
            self.connection = connection
            self.calls = 0

        def transaction_blocking(self, callback):
            self.calls += 1
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                result = callback(NoCommitProxy(self.connection))
            except BaseException:
                self.connection.rollback()
                raise
            self.connection.commit()
            return result

    coordinator = SpyCoordinator(conn)
    writer = CoordinatorScopedProjectionWriter(
        coordinator, fewshot_repository=repository
    )
    scope = _runtime_scope()
    candidate = _fewshot_candidate(scope)
    ref, binding = _runtime_evidence(scope)

    example_id = writer.write_approved(
        scope=scope,
        candidate=candidate,
        evidence_refs=(ref,),
        evidence_bindings=(binding,),
        content=candidate["content"],
        score=0.92,
        traits=("克制",),
        source_candidate_id=11,
        idempotency_key="candidate:11",
    )

    assert coordinator.calls == 1
    assert repository.get(example_id)["status"] == "approved"
    conn.close()


def test_reviewed_book_lore_repository_persists_full_derivation_and_revisions():
    from engine.db.scoped_learning_projection_repo import ReviewedBookLoreProjectionRepository

    conn = sqlite3.connect(":memory:")
    repository = ReviewedBookLoreProjectionRepository(conn, now=lambda: 100.0)
    target = _runtime_scope()
    candidate = _book_lore_candidate(target)
    source, ref, binding, derivation = _catalog_projection_evidence(target)

    projection_id = repository.write_reviewed_projection(
        source_scope=source,
        target_scope=target,
        candidate=candidate,
        evidence_refs=(ref,),
        evidence_bindings=(binding,),
        derivation=derivation,
        community_id="7",
        title="旧港",
        summary="潮汐改变商路。",
        content=candidate["content"],
        rank=8.5,
        status="approved",
        source_candidate_id=22,
        idempotency_key="candidate:22",
    )
    same_id = repository.write_reviewed_projection(
        source_scope=source,
        target_scope=target,
        candidate=candidate,
        evidence_refs=(ref,),
        evidence_bindings=(binding,),
        derivation=derivation,
        community_id="7",
        title="旧港",
        summary="潮汐改变了商路。",
        content="旧港：潮汐改变了商路。",
        rank=8.6,
        status="approved",
        source_candidate_id=22,
        idempotency_key="candidate:22",
    )

    row = repository.get(projection_id)
    assert same_id == projection_id
    assert row["source_scope"] == source
    assert row["target_scope"].bot_id == target.bot_id
    assert row["target_scope"].session == target.session
    assert row["target_scope"].subject_principal_id is None
    assert row["derivation"] == derivation
    assert row["evidence_refs"] == (ref,)
    assert row["evidence_bindings"] == (binding,)
    assert row["candidate"]["id"] == 22
    assert row["status"] == "approved"
    assert row["revision"] == 2
    conn.close()


def test_book_lore_promotion_requires_reviewed_derivation_and_uses_projection_writer():
    from services.learning.domain_promotions import BookLorePromotionService
    from services.learning.promotion import PromotionTerminalError

    class Writer:
        def __init__(self):
            self.calls = []

        def write_reviewed_projection(self, **kwargs):
            self.calls.append(kwargs)
            return 92

    target = _runtime_scope()
    candidate = _book_lore_candidate(target)
    writer = Writer()
    result = BookLorePromotionService(writer).promote(
        candidate=candidate, bot_id="bot-a", target_kind="book_lore"
    )

    assert result["target_id"] == "92"
    assert writer.calls[0]["source_scope"] == CatalogScope(
        catalog_id="book-lore", corpus_id="corpus-a", version="v1"
    )
    assert writer.calls[0]["target_scope"] == target
    assert writer.calls[0]["derivation"].reviewed is True

    invalid = _book_lore_candidate(target)
    invalid["evidence"].pop("evidence_derivation")
    with pytest.raises(PromotionTerminalError) as exc_info:
        BookLorePromotionService(writer).promote(
            candidate=invalid, bot_id="bot-a", target_kind="book_lore"
        )
    assert exc_info.value.code == "evidence_derivation_required"


def test_book_lore_channel_reads_only_current_runtime_approved_projection():
    from engine.db.scoped_learning_projection_repo import ReviewedBookLoreProjectionRepository
    from services.injection.channels.book_lore import BookLoreChannel
    from services.injection.context import InjectionContext

    conn = sqlite3.connect(":memory:")
    repository = ReviewedBookLoreProjectionRepository(conn, now=lambda: 100.0)
    current = _runtime_scope()
    other = _runtime_scope("group-2")

    for projection_target, candidate_id, title, status in (
        (current, 31, "当前群设定", "approved"),
        (current, 32, "待审核设定", "pending"),
        (other, 33, "其他群设定", "approved"),
    ):
        source, ref, binding, derivation = _catalog_projection_evidence(projection_target)
        repository.write_reviewed_projection(
            source_scope=source,
            target_scope=projection_target,
            candidate={"id": candidate_id, "content": title},
            evidence_refs=(ref,),
            evidence_bindings=(binding,),
            derivation=derivation,
            community_id=str(candidate_id),
            title=title,
            summary=f"{title}摘要",
            content=f"{title}：{title}摘要",
            rank=9.0,
            status=status,
            source_candidate_id=candidate_id,
            idempotency_key=f"candidate:{candidate_id}",
        )

    class ForbiddenRawDependency:
        def __getattr__(self, name):
            raise AssertionError(f"raw Catalog dependency must not be accessed: {name}")

    ctx = InjectionContext(
        event="event",
        req=object(),
        message="旧港",
        group_id="group-1",
        sender_id="user-1",
        sender_name="用户",
        bot_id="10001",
        bot_profile_id="bot-a",
        scope=current,
        recent_context=[],
        config={"channels": {"book_lore": {"top_k": 5}}},
        trace_id="trace-book-lore",
    )
    channel = BookLoreChannel(
        projection_repository=repository,
        book_lore_index=ForbiddenRawDependency(),
        embedding_service=ForbiddenRawDependency(),
        lore_store=ForbiddenRawDependency(),
        lore_db_path="must-not-open.db",
    )

    result = asyncio.run(channel.build(ctx))

    assert result.status == "hit"
    assert "当前群设定" in result.text
    assert "待审核设定" not in result.text
    assert "其他群设定" not in result.text
    assert result.items[0]["projection_id"] is not None
    conn.close()


@pytest.mark.asyncio
async def test_webui_lists_only_formal_projections_for_exact_runtime_scope():
    quart = pytest.importorskip("quart")
    if not hasattr(getattr(quart, "Quart", None), "test_client"):
        pytest.skip("Quart runtime client is unavailable")

    from engine.db.scoped_learning_projection_repo import (
        ReviewedBookLoreProjectionRepository,
        ScopedFewShotRepository,
    )
    from webui.app import create_app
    from webui.container import ServiceContainer, get_container
    from webui.scope_options import ExplicitRequestScopeProvider

    conn = sqlite3.connect(":memory:")
    fewshot = ScopedFewShotRepository(conn, now=lambda: 100.0)
    book_lore = ReviewedBookLoreProjectionRepository(conn, now=lambda: 100.0)
    current = _runtime_scope()
    other = _runtime_scope("group-2")

    for scope, candidate_id, content in (
        (current, 51, "当前群的克制风格"),
        (other, 52, "其他群的隐藏风格"),
    ):
        ref, binding = _runtime_evidence(scope)
        fewshot.write_approved(
            scope=scope,
            candidate={"id": candidate_id},
            evidence_refs=(ref,),
            evidence_bindings=(binding,),
            content=content,
            score=0.9,
            traits=("克制",),
            source_candidate_id=candidate_id,
            idempotency_key=f"candidate:{candidate_id}",
        )
        source, lore_ref, lore_binding, derivation = _catalog_projection_evidence(scope)
        book_lore.write_reviewed_projection(
            source_scope=source,
            target_scope=scope,
            candidate={"id": candidate_id},
            evidence_refs=(lore_ref,),
            evidence_bindings=(lore_binding,),
            derivation=derivation,
            community_id=str(candidate_id),
            title=content,
            summary=f"{content}摘要",
            content=f"{content}正文",
            rank=8.0,
            status="approved",
            source_candidate_id=candidate_id,
            idempotency_key=f"candidate:{candidate_id}",
        )

    ServiceContainer.reset()
    container = get_container()
    container.db = SimpleNamespace(conn=conn, closed=False)
    container.password = ""
    container.fewshot_repository = fewshot
    container.book_lore_repository = book_lore
    provider = ExplicitRequestScopeProvider(
        bot_registry={"bot-a": SimpleNamespace(db_id="bot-a")}
    )
    app = create_app(request_scope_provider=provider)
    client = app.test_client()
    query = "bot_id=bot-a&session_id=test:group:group-1&visibility=group"

    try:
        response = await client.get(f"/api/knowledge/few-shot?{query}")
        assert response.status_code == 200
        payload = await response.get_json()
        assert payload["source"] == "scoped_few_shot_examples"
        assert payload["page"]["total"] == 1
        assert [item["content"] for item in payload["items"]] == ["当前群的克制风格"]
        assert payload["items"][0]["session_id"] == "test:group:group-1"

        lore_response = await client.get(f"/api/knowledge/book-lore/projections?{query}")
        assert lore_response.status_code == 200
        lore_payload = await lore_response.get_json()
        assert lore_payload["source"] == "reviewed_book_lore_projections"
        assert lore_payload["page"]["total"] == 1
        assert [item["title"] for item in lore_payload["items"]] == ["当前群的克制风格"]

        missing_scope = await client.get("/api/knowledge/few-shot")
        assert missing_scope.status_code == 400
        assert (await missing_scope.get_json())["error"]["code"] == "scope_required"

        qq_bot = await client.get(
            "/api/knowledge/few-shot?bot_id=10001&session_id=test:group:group-1&visibility=group"
        )
        assert qq_bot.status_code == 400
        assert (await qq_bot.get_json())["error"]["code"] == "scope_required"
    finally:
        ServiceContainer.reset()
        conn.close()
