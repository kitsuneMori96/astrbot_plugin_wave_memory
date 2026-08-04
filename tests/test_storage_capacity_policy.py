from __future__ import annotations

from services.storage_capacity_policy import (
    StorageCapacityPolicy,
    decide_storage_admission,
)


def test_under_capacity_keeps_vectors():
    policy = StorageCapacityPolicy(max_active_memories=100, enabled=True)
    decision = decide_storage_admission(
        source="chat",
        active_count=10,
        policy=policy,
        has_vector=True,
    )
    assert decision.keep_vector is True
    assert decision.over_capacity is False
    assert decision.reason == "under_capacity"


def test_over_capacity_chat_is_cold_demoted():
    policy = StorageCapacityPolicy(
        max_active_memories=100,
        cold_chat_when_over_capacity=True,
        enabled=True,
    )
    decision = decide_storage_admission(
        source="chat",
        active_count=100,
        policy=policy,
        has_vector=True,
    )
    assert decision.over_capacity is True
    assert decision.keep_vector is False
    assert decision.demoted_to_cold is True
    assert decision.reason == "over_capacity_chat_cold"


def test_durable_core_keeps_vector_even_when_over_capacity():
    policy = StorageCapacityPolicy(max_active_memories=1, enabled=True)
    decision = decide_storage_admission(
        source="core",
        active_count=50,
        policy=policy,
        has_vector=True,
    )
    assert decision.over_capacity is True
    assert decision.keep_vector is True
    assert decision.reason == "durable_source_keeps_vector"


def test_policy_from_settings_respects_explicit_false():
    policy = StorageCapacityPolicy.from_settings({
        "max_memories": "50000",
        "canonical_capacity_enabled": False,
        "cold_chat_when_over_capacity": "0",
    })
    assert policy.max_active_memories == 50000
    assert policy.enabled is False
    assert policy.cold_chat_when_over_capacity is False
    decision = decide_storage_admission(
        source="chat",
        active_count=999999,
        policy=policy,
        has_vector=True,
    )
    assert decision.keep_vector is True
    assert decision.reason == "capacity_policy_disabled"


def test_noise_never_vectorized_when_over_capacity():
    policy = StorageCapacityPolicy(max_active_memories=1, enabled=True)
    decision = decide_storage_admission(
        source="noise",
        active_count=10,
        policy=policy,
        has_vector=True,
    )
    assert decision.keep_vector is False
    assert decision.reason == "noise_never_vectorized"
