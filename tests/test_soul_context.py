from __future__ import annotations

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.migrations.scoped_soul import ensure_scoped_soul_schema
from engine.db.scoped_soul_repo import ScopedSoulRepository
from services.soul_context import resolve_soul_context


def make_scope() -> RuntimeScope:
    return RuntimeScope(
        "bot-alpha",
        "group",
        SessionRef("qq:group:g1", "qq", "group", "g1"),
        subject_principal_id="qq:user:u1",
    )


def test_provider_context_is_normalized_and_scoped():
    scope = make_scope()

    class Provider:
        def get_soul_context(self, *, scope, now):
            assert scope is not None
            return {
                "timezone": "Asia/Shanghai",
                "circadian": {"phase": "evening"},
                "energy": 0.62,
                "sleepiness": 0.18,
                "source": "test-provider",
            }

    value = resolve_soul_context(Provider(), scope=scope, now=123.0)
    assert value["status"] == "available"
    assert value["timezone"] == "Asia/Shanghai"
    assert value["captured_at"] == 123.0
    assert value["source"] == "test-provider"


def test_repository_returns_unavailable_without_provider_and_available_with_provider(tmp_path):
    manager = ConnectionManager(str(tmp_path / "soul-context.db"))
    ensure_scoped_soul_schema(manager)
    repo = ScopedSoulRepository(manager)
    scope = make_scope()
    assert repo.get_state(scope)["soul_context"]["status"] == "unavailable"

    repo.set_soul_context_provider(lambda *, scope, now: {"energy": 0.7})
    context = repo.get_state(scope)["soul_context"]
    assert context["status"] == "available"
    assert context["energy"] == 0.7
    assert context["timezone"] is None
    manager.close()
