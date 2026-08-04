from __future__ import annotations

from services.tag_admission import (
    CatalogNeighbor,
    admit_tag_batch,
    decide_tag_admission,
    normalize_admission_name,
)


def test_normalize_admission_name_collapses_particles_and_case():
    assert normalize_admission_name("  猫的  ") == "猫"
    assert normalize_admission_name("AI生图") == normalize_admission_name("ai生图")


def test_exact_catalog_reuse_prefers_existing_display_name():
    catalog = [
        CatalogNeighbor(
            catalog_id=7,
            normalized_name="白真真",
            display_name="白真真",
            tag_type="person",
        )
    ]
    decision = decide_tag_admission(
        {"name": "白真真的", "type": "person", "confidence": 0.9},
        catalog=catalog,
    )
    assert decision.action == "reuse"
    assert decision.catalog_id == 7
    assert decision.name == "白真真"
    assert decision.reason == "exact_normalized_match"


def test_semantic_reuse_collapses_near_duplicate_embeddings():
    catalog = [
        CatalogNeighbor(
            catalog_id=3,
            normalized_name="猫咪",
            display_name="猫咪",
            tag_type="topic",
            embedding=[1.0, 0.0, 0.0],
        )
    ]
    decision = decide_tag_admission(
        {
            "name": "小猫咪",
            "type": "topic",
            "confidence": 0.8,
            "embedding": [0.99, 0.01, 0.0],
        },
        catalog=catalog,
        semantic_reuse_threshold=0.9,
    )
    assert decision.action == "reuse"
    assert decision.catalog_id == 3
    assert decision.reason.startswith("semantic_reuse:")


def test_low_confidence_new_tag_is_rejected_not_created():
    decision = decide_tag_admission(
        {"name": "临时碎语", "type": "topic", "confidence": 0.42},
        catalog=(),
        min_create_confidence=0.55,
    )
    assert decision.action == "reject"
    assert decision.reason == "create_confidence_below_threshold"


def test_admit_tag_batch_dedupes_and_drops_stop_words():
    admitted, decisions = admit_tag_batch(
        [
            {"name": "什么", "type": "keyword", "confidence": 0.9},
            {"name": "白真真", "type": "person", "confidence": 0.9},
            {"name": "白真真的", "type": "person", "confidence": 0.9},
        ]
    )
    assert [item["name"] for item in admitted] == ["白真真"]
    assert any(item.action == "reject" and item.reason == "stop_word" for item in decisions)
