from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from webui.blueprints.tags import build_tag_list_payload, build_tag_runtime_payload


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            tag_type TEXT,
            frequency INTEGER NOT NULL DEFAULT 0,
            confidence REAL
        );
        CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE memory_tags (memory_id INTEGER NOT NULL, tag_id INTEGER NOT NULL);

        INSERT INTO tags(id, name, tag_type, frequency, confidence) VALUES
            (1, '共同记忆', 'topic', 99, 0.9),
            (2, '羽书', 'person', 88, 0.8),
            (3, '空标签', 'topic', 77, 0.7);
        INSERT INTO memories(id, content) VALUES (10, 'a'), (11, 'b'), (12, 'c');
        INSERT INTO memory_tags(memory_id, tag_id) VALUES
            (10, 1), (11, 1), (999, 1),
            (12, 2), (998, 2);
        """
    )
    return conn


def test_tag_list_total_uses_the_same_search_and_type_filters():
    payload = build_tag_list_payload(
        _conn(), limit=25, offset=0, tag_type="topic", search="共同"
    )

    assert payload["total"] == 1
    assert [item["name"] for item in payload["items"]] == ["共同记忆"]
    assert payload["available_types"] == ["person", "topic"]


def test_tag_frequency_counts_only_existing_memories_and_sorts_by_live_count():
    payload = build_tag_list_payload(_conn(), limit=25, sort="frequency")

    assert [(item["name"], item["frequency"]) for item in payload["items"]] == [
        ("共同记忆", 2),
        ("羽书", 1),
        ("空标签", 0),
    ]


def test_tag_list_empty_page_preserves_filtered_total_and_capability():
    payload = build_tag_list_payload(_conn(), limit=25, offset=25, search="共同")

    assert payload["total"] == 1
    assert payload["items"] == []
    assert payload["readonly"] is True
    assert payload["capabilities"]["mutation"] == {
        "available": False,
        "reason_code": "legacy_mutation_disabled",
    }


def test_runtime_status_reports_semantic_rag_and_manifest_without_paths():
    manifest = SimpleNamespace(generation=7, db_watermark=123)
    index = SimpleNamespace(
        count=42,
        current_manifest=manifest,
        manifest_error=None,
    )
    extractor = SimpleNamespace(
        embedding_service=object(),
        tag_index=index,
        _reference_refresh_interval=200,
    )
    container = SimpleNamespace(
        tag_extractor=extractor,
        tag_index=index,
        plugin_config={"tag_llm_provider_id": "secret-provider-id"},
    )

    payload = build_tag_runtime_payload(container)

    assert payload["index"] == {
        "available": True,
        "health": "ready",
        "reason_code": None,
        "count": 42,
        "generation": 7,
        "db_watermark": 123,
    }
    assert payload["rag"]["mode"] == "semantic"
    assert payload["capabilities"]["extract"]["available"] is True
    assert "secret-provider-id" not in repr(payload)


def test_runtime_status_explains_static_fallback_without_inventing_health():
    index = SimpleNamespace(count=0, current_manifest=None, manifest_error=None)
    extractor = SimpleNamespace(
        embedding_service=None,
        tag_index=index,
        _reference_refresh_interval=200,
    )
    container = SimpleNamespace(
        tag_extractor=extractor,
        tag_index=index,
        plugin_config={"tag_llm_provider_id": "configured"},
    )

    payload = build_tag_runtime_payload(container)

    assert payload["rag"]["mode"] == "static"
    assert payload["rag"]["fallback_reason"] == "embedding_unavailable"
    assert payload["index"]["health"] == "legacy"
    assert payload["index"]["reason_code"] == "manifest_unavailable"
