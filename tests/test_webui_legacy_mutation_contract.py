from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from webui.blueprints import beliefs, jargon, tags


BLUEPRINT_ROOT = Path(__file__).parents[1] / "webui" / "blueprints"
TARGET_BLUEPRINTS = ("beliefs.py", "jargon.py", "tags.py")

LEGACY_MUTATION_HANDLERS = {
    beliefs: {
        "batch_archive": (),
        "batch_archive_selected_beliefs": (),
        "batch_approve_beliefs": (),
        "batch_delete_beliefs": (),
    },
    jargon: {
        "review_holyman_candidate": (1, "approve"),
        "batch_review_holyman_candidates": (),
        "edit_jargon": (1,),
        "delete_jargon": (1,),
        "toggle_global": (1,),
        "batch_review_jargon": (),
        "batch_delete_jargon": (),
        "toggle_holyman": (),
        "sync_holyman": (),
    },
    tags: {
        "retype_tag": (),
        "rename_tag": (),
        "batch_delete_tags": (),
        "resolve_audit_suggestion": (),
        "resolve_audit_batch": (),
    },
}


def _decorator_name(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _deepest_wrapped(handler):
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return handler


def test_blueprints_contain_no_direct_commit_or_inline_write_sql():
    """Blueprint 只能委托统一 writer/service，不能保留可复活的直写 SQL。"""
    write_sql_prefixes = ("INSERT INTO ", "INSERT OR ", "UPDATE ", "DELETE FROM ", "REPLACE INTO ")
    for filename in TARGET_BLUEPRINTS:
        tree = ast.parse((BLUEPRINT_ROOT / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "execute_write"}, (
                    f"{filename}:{node.lineno} contains direct transaction ownership"
                )
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                normalized = " ".join(node.value.upper().split())
                assert not normalized.startswith(write_sql_prefixes), (
                    f"{filename}:{node.lineno} contains inline write SQL"
                )


def test_all_declared_legacy_mutations_are_decorated_and_fail_closed(monkeypatch):
    for module, handlers in LEGACY_MUTATION_HANDLERS.items():
        monkeypatch.setattr(module, "jsonify", lambda payload: payload)
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name, args in handlers.items():
            assert "_legacy_mutation_disabled" in {
                _decorator_name(item) for item in functions[name].decorator_list
            }
            handler = getattr(module, name)
            for entrypoint in (handler.__wrapped__, _deepest_wrapped(handler)):
                payload, status = asyncio.run(entrypoint(*args))
                assert status == 410
                assert payload == {"error": {"code": "legacy_mutation_disabled"}}


def test_holyman_blocklist_post_fails_before_database_access(monkeypatch):
    monkeypatch.setattr(jargon, "jsonify", lambda payload: payload)
    monkeypatch.setattr(jargon, "request", SimpleNamespace(method="POST"))
    monkeypatch.setattr(
        jargon,
        "get_container",
        lambda: pytest.fail("Holyman blocklist apply must not touch the database"),
    )

    payload, status = asyncio.run(jargon.holyman_blocklist.__wrapped__())

    assert status == 410
    assert payload == {"error": {"code": "legacy_mutation_disabled"}}


def test_holyman_sync_preview_is_read_only_while_apply_is_disabled():
    tree = ast.parse(Path(jargon.__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    preview_calls = {
        node.func.attr
        for node in ast.walk(functions["preview_holyman_sync"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    apply_calls = {
        node.func.attr
        for node in ast.walk(functions["sync_holyman"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "preview_sync_from_github" in preview_calls
    assert "sync_from_github" not in preview_calls
    assert "sync_from_github" not in apply_calls


def test_legacy_tag_audit_resolver_helper_cannot_write():
    result = asyncio.run(tags._resolve_audit_suggestion(SimpleNamespace(), 1, "reject"))
    assert result == {"error": "legacy_mutation_disabled"}
