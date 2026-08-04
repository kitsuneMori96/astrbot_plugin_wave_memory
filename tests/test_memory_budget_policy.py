"""Resident memory budget policy contracts.

These tests are intentionally pure: the policy must be verifiable without
hnswlib so capacity math can be trusted before any index is allocated.
"""

from __future__ import annotations

from services.memory_budget_policy import (
    PROFILE_BALANCED_3G,
    PROFILE_STRICT_2G,
    PROFILE_UNBOUNDED,
    MemoryBudgetPolicy,
    bytes_per_vector,
)


def test_absent_settings_stay_unbounded_and_do_not_clamp():
    policy = MemoryBudgetPolicy.from_settings(None)

    assert policy.profile == PROFILE_UNBOUNDED
    assert policy.enabled is False
    assert policy.capacities(1024) == {}
    # Historical explicit capacity must survive untouched.
    assert policy.bounded_capacity("hot_memory", 100_000, 1024) == 100_000


def test_unknown_profile_falls_back_to_unbounded():
    policy = MemoryBudgetPolicy.from_settings({"memory_profile": "tiny"})

    assert policy.profile == PROFILE_UNBOUNDED
    assert policy.bounded_capacity("tag_catalog", 40_000, 1024) == 40_000


def test_balanced_profile_clamps_oversized_hot_capacity():
    policy = MemoryBudgetPolicy.from_settings({"memory_profile": PROFILE_BALANCED_3G})

    assert policy.enabled is True
    bounded = policy.bounded_capacity("hot_memory", 100_000, 1024)

    assert bounded < 100_000
    assert bounded >= 2000


def test_budget_never_raises_a_smaller_operator_capacity():
    policy = MemoryBudgetPolicy.from_settings({"memory_profile": PROFILE_BALANCED_3G})

    assert policy.bounded_capacity("hot_memory", 5_000, 1024) == 5_000


def test_strict_profile_is_tighter_than_balanced_for_every_tier():
    balanced = MemoryBudgetPolicy.from_settings({"memory_profile": PROFILE_BALANCED_3G})
    strict = MemoryBudgetPolicy.from_settings({"memory_profile": PROFILE_STRICT_2G})

    balanced_caps = balanced.capacities(1024)
    strict_caps = strict.capacities(1024)

    assert set(balanced_caps) == set(strict_caps)
    for tier, strict_value in strict_caps.items():
        assert strict_value <= balanced_caps[tier], tier


def test_book_lore_is_not_a_budget_clamped_tier():
    """BookLore grows daily; clamping it would eventually refuse ingestion."""
    policy = MemoryBudgetPolicy.from_settings({"memory_profile": PROFILE_STRICT_2G})

    capacities = policy.capacities(1024)

    assert set(capacities) == {"hot_memory", "tag_catalog"}
    assert policy.is_clamped_tier("hot_memory") is True
    assert policy.is_clamped_tier("book_lore_entity") is False
    # An unclamped tier must pass through untouched rather than being reduced.
    assert policy.bounded_capacity("book_lore_entity", 4096, 1024) == 4096


def test_unclamped_reserve_is_withheld_from_clamped_tiers():
    """Budget must leave room for BookLore's slow growth."""
    policy = MemoryBudgetPolicy.from_settings(
        {"memory_profile": PROFILE_BALANCED_3G, "memory_budget_mb": 4000}
    )
    capacities = policy.capacities(1024)

    weighted_total = sum(capacities.values()) * policy.describe(1024)["bytes_per_vector"]

    assert weighted_total < policy.index_budget_mb * 1024 * 1024


def test_estimated_steady_memory_stays_within_each_profile_budget():
    for profile in (PROFILE_BALANCED_3G, PROFILE_STRICT_2G):
        policy = MemoryBudgetPolicy.from_settings({"memory_profile": profile})
        capacities = policy.capacities(1024)

        steady = policy.estimated_steady_mb(capacities, 1024)

        # Steady state must leave the configured rebuild headroom unused.
        assert steady <= policy.budget_mb, (profile, steady, policy.budget_mb)


def test_rebuild_headroom_is_withheld_from_index_budget():
    no_headroom = MemoryBudgetPolicy.from_settings(
        {"memory_profile": PROFILE_BALANCED_3G, "rebuild_headroom": 0.0}
    )
    with_headroom = MemoryBudgetPolicy.from_settings(
        {"memory_profile": PROFILE_BALANCED_3G, "rebuild_headroom": 0.5}
    )

    assert with_headroom.index_budget_mb < no_headroom.index_budget_mb


def test_malformed_numeric_settings_fall_back_to_profile_defaults():
    policy = MemoryBudgetPolicy.from_settings(
        {
            "memory_profile": PROFILE_STRICT_2G,
            "memory_budget_mb": "not-a-number",
            "rebuild_headroom": "nope",
        }
    )

    assert policy.budget_mb == 1600
    assert policy.rebuild_headroom == 0.45


def test_baseline_at_or_over_budget_still_yields_minimum_capacities():
    policy = MemoryBudgetPolicy.from_settings(
        {
            "memory_profile": PROFILE_STRICT_2G,
            "memory_budget_mb": 512,
            "baseline_reserved_mb": 4096,
        }
    )

    capacities = policy.capacities(1024)

    assert capacities["hot_memory"] >= 2000
    assert all(value > 0 for value in capacities.values())


def test_invalid_dimension_yields_no_derived_capacity():
    policy = MemoryBudgetPolicy.from_settings({"memory_profile": PROFILE_BALANCED_3G})

    assert bytes_per_vector(0) == 0
    assert policy.capacities(0) == {}
    # With no derivable capacity the configured value must be preserved.
    assert policy.bounded_capacity("hot_memory", 100_000, 0) == 100_000


def test_describe_exposes_operator_facing_budget_evidence():
    policy = MemoryBudgetPolicy.from_settings({"memory_profile": PROFILE_BALANCED_3G})

    described = policy.describe(1024)

    assert described["profile"] == PROFILE_BALANCED_3G
    assert described["enabled"] is True
    assert described["bytes_per_vector"] > 1024 * 4
    assert described["capacities"]["hot_memory"] > 0
    assert described["estimated_steady_mb"] <= described["budget_mb"]
