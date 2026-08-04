"""Resident memory budget policy for WaveMemory's bounded vector tiers.

Reducing rebuild frequency lowers *peak* churn but cannot bound the *steady*
resident set: the process may hold the hot memory HNSW, the Tag Catalog HNSW and
three optional BookLore HNSW indexes at the same time.  hnswlib preallocates for
``max_elements`` rather than the live row count, so capacity configuration - not
rebuild cadence - decides the floor.

This module turns one operator-facing budget into explicit per-index capacities.

Design rules:

- Budget is expressed in MiB of *resident process* memory, not container total.
  Container totals include reclaimable page cache and cannot be governed here.
- A fixed baseline is reserved for the interpreter, SQLite and runtime services.
- A rebuild headroom fraction is withheld so publishing a replacement generation
  cannot push the steady state over the budget.
- The remainder is split across tiers by weight and converted to vector counts
  using the real per-vector cost (payload + HNSW graph overhead).
- Every derived capacity stays positive so a tiny budget degrades rather than
  producing an unusable zero-capacity index.

The policy is pure and side-effect free so it can be unit tested without hnswlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# float32 payload bytes per dimension.
_BYTES_PER_DIMENSION = 4

# hnswlib's resident cost per element is the vector payload plus a fixed-width
# layer-0 neighbour list and label:
#   size_data_per_element = dim*4 + (M*2)*sizeof(tableint) + sizeof(linklistsizeint)
#                           + sizeof(labeltype)
# The runtime hot/Tag indexes build with M=12; BookLore uses M=16.  Upper layers
# hold only a small fraction of elements, so a modest multiplier covers them.
_DEFAULT_GRAPH_M = 12
_BYTES_PER_LINK = 4
_LINK_LIST_HEADER_BYTES = 4
_LABEL_BYTES = 8
# Upper-layer linklists plus allocator slack.  Kept small and explicit rather
# than folded into a single invented factor.
_UPPER_LAYER_FACTOR = 1.08

_MIB = 1024 * 1024

PROFILE_BALANCED_3G = "balanced_3g"
PROFILE_STRICT_2G = "strict_2g"
PROFILE_UNBOUNDED = "unbounded"

VALID_PROFILES = frozenset({PROFILE_BALANCED_3G, PROFILE_STRICT_2G, PROFILE_UNBOUNDED})

# Default resident budgets per profile, chosen to leave container headroom for
# page cache and short-lived allocations under the stated ceilings.
_PROFILE_BUDGET_MB = {
    PROFILE_BALANCED_3G: 2400,
    PROFILE_STRICT_2G: 1600,
}

# Non-index resident baseline: interpreter, SQLite connections, services, caches.
_PROFILE_BASELINE_MB = {
    PROFILE_BALANCED_3G: 700,
    PROFILE_STRICT_2G: 600,
}

# Withheld fraction of the index budget so a rebuild's second copy fits.
_PROFILE_REBUILD_HEADROOM = {
    PROFILE_BALANCED_3G: 0.35,
    PROFILE_STRICT_2G: 0.45,
}

# Relative weights across resident vector tiers.
_PROFILE_WEIGHTS = {
    # BookLore is deliberately absent: it grows with daily chapter ingestion and
    # must never be refused a write, so it is not a budget-clamped tier.  Its
    # resident cost is controlled by fitting to the real corpus and growing in
    # small steps (see ``engine/book_lore_index.py``).  A reserve is withheld here
    # so BookLore's slow growth does not silently eat the memory/Tag allocation.
    PROFILE_BALANCED_3G: {
        "hot_memory": 0.70,
        "tag_catalog": 0.20,
    },
    PROFILE_STRICT_2G: {
        "hot_memory": 0.74,
        "tag_catalog": 0.18,
    },
}

_MIN_CAPACITY = {
    "hot_memory": 2000,
    "tag_catalog": 500,
}

# Withheld from the clamped tiers to leave room for unbounded-but-slow growth
# (BookLore) plus other resident allocations that are not capacity-configurable.
_UNCLAMPED_TIER_RESERVE = 0.10

# Per-profile resident ceilings.  Weight math alone can still permit a very large
# hot tier once the budget is generous, but preallocation is paid whether or not
# the index is full, and rebuilds temporarily hold a second copy.  These ceilings
# encode the intent of each profile so a profile cannot silently behave like
# ``unbounded`` on a machine with a larger configured budget.
_PROFILE_MAX_CAPACITY = {
    PROFILE_BALANCED_3G: {
        "hot_memory": 40_000,
        "tag_catalog": 12_000,
    },
    PROFILE_STRICT_2G: {
        "hot_memory": 24_000,
        "tag_catalog": 8_000,
    },
}


def bytes_per_vector(dimension: int, graph_m: int = _DEFAULT_GRAPH_M) -> int:
    """Estimated resident bytes for one indexed vector, including graph overhead.

    Mirrors hnswlib's ``size_data_per_element`` rather than applying an invented
    multiplier, so budget math can be checked against real index files.
    """
    try:
        dim = int(dimension)
    except (TypeError, ValueError):
        return 0
    if dim <= 0:
        return 0
    try:
        m = int(graph_m)
    except (TypeError, ValueError):
        m = _DEFAULT_GRAPH_M
    if m <= 0:
        m = _DEFAULT_GRAPH_M
    per_element = (
        dim * _BYTES_PER_DIMENSION
        + (m * 2) * _BYTES_PER_LINK
        + _LINK_LIST_HEADER_BYTES
        + _LABEL_BYTES
    )
    return int(per_element * _UPPER_LAYER_FACTOR)


@dataclass(frozen=True, slots=True)
class MemoryBudgetPolicy:
    """Operator-facing resident budget for all bounded vector tiers."""

    profile: str = PROFILE_UNBOUNDED
    budget_mb: int = 0
    baseline_mb: int = 0
    rebuild_headroom: float = 0.0

    @property
    def enabled(self) -> bool:
        """Unbounded keeps historical explicit capacities untouched."""
        return self.profile in {PROFILE_BALANCED_3G, PROFILE_STRICT_2G} and self.budget_mb > 0

    @property
    def index_budget_mb(self) -> int:
        """Budget available to resident indexes after baseline and headroom."""
        if not self.enabled:
            return 0
        usable = self.budget_mb - self.baseline_mb
        if usable <= 0:
            return 0
        return int(usable * (1.0 - self.rebuild_headroom))

    @classmethod
    def from_settings(cls, section: Mapping[str, Any] | None) -> "MemoryBudgetPolicy":
        """Build a policy from ``Memory_Budget_Settings``; absent means unbounded."""
        settings = section if isinstance(section, Mapping) else {}
        raw_profile = str(settings.get("memory_profile", PROFILE_UNBOUNDED) or "").strip().casefold()
        profile = raw_profile if raw_profile in VALID_PROFILES else PROFILE_UNBOUNDED
        if profile == PROFILE_UNBOUNDED:
            return cls()

        budget_mb = _bounded_int(
            settings, "memory_budget_mb", _PROFILE_BUDGET_MB[profile], 256
        )
        baseline_mb = _bounded_int(
            settings, "baseline_reserved_mb", _PROFILE_BASELINE_MB[profile], 0
        )
        headroom = _bounded_fraction(
            settings, "rebuild_headroom", _PROFILE_REBUILD_HEADROOM[profile]
        )
        # A baseline at/over budget would leave no index budget at all; clamp it so
        # the profile still degrades to minimum capacities instead of zero.
        if baseline_mb >= budget_mb:
            baseline_mb = max(0, budget_mb - 128)
        return cls(
            profile=profile,
            budget_mb=budget_mb,
            baseline_mb=baseline_mb,
            rebuild_headroom=headroom,
        )

    def capacities(self, dimension: int) -> dict[str, int]:
        """Derive per-tier vector capacities for the configured budget."""
        if not self.enabled:
            return {}
        per_vector = bytes_per_vector(dimension)
        if per_vector <= 0:
            return {}
        available_bytes = self.index_budget_mb * _MIB * (1.0 - _UNCLAMPED_TIER_RESERVE)
        weights = _PROFILE_WEIGHTS[self.profile]
        ceilings = _PROFILE_MAX_CAPACITY[self.profile]
        capacities: dict[str, int] = {}
        for tier, weight in weights.items():
            tier_bytes = available_bytes * weight
            capacity = int(tier_bytes // per_vector)
            # Ceiling first, then floor: a tiny budget must still stay usable.
            capacity = min(capacity, ceilings[tier])
            capacities[tier] = max(_MIN_CAPACITY[tier], capacity)
        return capacities

    def bounded_capacity(self, tier: str, configured: int, dimension: int) -> int:
        """Return the effective capacity for one tier.

        The budget only ever *lowers* a configured value: an operator who already
        set a small explicit capacity keeps it, while an oversized legacy value is
        clamped to what the budget can actually hold.
        """
        try:
            requested = int(configured)
        except (TypeError, ValueError):
            requested = 0
        derived = self.capacities(dimension).get(tier)
        if derived is None:
            return max(1, requested)
        if requested <= 0:
            return max(1, derived)
        return max(1, min(requested, derived))

    def estimated_steady_mb(self, capacities: Mapping[str, int], dimension: int) -> float:
        """Estimated worst-case resident MiB for the given capacities.

        This assumes every capacity is fully allocated.  Static tiers normally sit
        well below their ceiling because they are fitted to the real corpus, so the
        real steady state is typically lower than this bound.
        """
        per_vector = bytes_per_vector(dimension)
        if per_vector <= 0:
            return float(self.baseline_mb)
        total_bytes = sum(max(0, int(count)) for count in capacities.values()) * per_vector
        return round(self.baseline_mb + total_bytes / _MIB, 1)

    def is_clamped_tier(self, tier: str) -> bool:
        """Whether this tier's capacity is governed by the budget.

        Growing tiers such as BookLore are intentionally not clamped: refusing
        their writes would break ongoing ingestion.
        """
        return str(tier) in _MIN_CAPACITY

    def describe(self, dimension: int) -> dict[str, Any]:
        """Structured summary for diagnostics and operator-facing surfaces."""
        capacities = self.capacities(dimension)
        return {
            "profile": self.profile,
            "enabled": self.enabled,
            "budget_mb": self.budget_mb,
            "baseline_mb": self.baseline_mb,
            "rebuild_headroom": self.rebuild_headroom,
            "index_budget_mb": self.index_budget_mb,
            "bytes_per_vector": bytes_per_vector(dimension),
            "capacities": capacities,
            "estimated_steady_mb": self.estimated_steady_mb(capacities, dimension),
        }


def _bounded_int(settings: Mapping[str, Any], key: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(float(settings.get(key, default))))
    except (TypeError, ValueError):
        return default


def _bounded_fraction(settings: Mapping[str, Any], key: str, default: float) -> float:
    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    if not 0.0 <= value < 0.95:
        return default
    return value


__all__ = [
    "MemoryBudgetPolicy",
    "PROFILE_BALANCED_3G",
    "PROFILE_STRICT_2G",
    "PROFILE_UNBOUNDED",
    "VALID_PROFILES",
    "bytes_per_vector",
]
