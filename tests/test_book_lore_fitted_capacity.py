"""Capacity sizing for the incrementally growing BookLore tier.

BookLore grows by roughly one or two extracted chapters per day, so it must never
refuse a write.  hnswlib nonetheless pays resident memory for ``max_elements``,
so the sizing rule is "fit the existing corpus, then grow in small steps" rather
than either a hard cap or the original ``needed + 10000`` jump.

This module loads only the pure helpers so the rule stays verifiable without
hnswlib installed.
"""

from __future__ import annotations

from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "engine" / "book_lore_index.py"


def _load_capacity_helpers():
    source = _MODULE_PATH.read_text(encoding="utf-8")
    marker = source.index("class BookLoreIndex")
    namespace: dict[str, object] = {}
    exec(compile(source[:marker], str(_MODULE_PATH), "exec"), namespace)
    return namespace


_helpers = _load_capacity_helpers()
fitted_capacity = _helpers["fitted_capacity"]
grown_capacity = _helpers["grown_capacity"]
growth_step = _helpers["growth_step"]
GROWTH_MARGIN_RATIO = _helpers["GROWTH_MARGIN_RATIO"]
MIN_GROWTH_MARGIN = _helpers["MIN_GROWTH_MARGIN"]


def test_existing_corpus_is_fitted_plus_one_growth_step():
    # 4000 entities -> 4000 + 15% == 4600, not a 50k preallocation.
    assert fitted_capacity(4000, initial=999_999) == 4600


def test_small_corpus_gets_the_minimum_absolute_step():
    assert fitted_capacity(10, initial=4096) == 10 + MIN_GROWTH_MARGIN


def test_empty_corpus_uses_the_initial_allocation():
    assert fitted_capacity(0, initial=4096) == 4096
    assert fitted_capacity(-3, initial=4096) == 4096


def test_initial_allocation_does_not_cap_an_existing_corpus():
    """A larger corpus than the initial size must still be fully accommodated."""
    assert fitted_capacity(50_000, initial=512) == 57_500


def test_growth_is_unbounded_so_daily_ingestion_is_never_refused():
    capacity = fitted_capacity(1000, initial=4096)

    # Simulate two years of ~2 chapters/day landing as new entities.
    for _ in range(730):
        capacity = grown_capacity(capacity, capacity + 2)

    assert capacity > 1000


def test_growth_steps_are_proportional_not_fixed_large_blocks():
    # The old implementation jumped by 10000 regardless of size.
    assert growth_step(1000) == 150
    assert growth_step(100_000) == 15_000
    # Tiny indexes still move by a usable absolute amount.
    assert growth_step(10) == MIN_GROWTH_MARGIN


def test_grown_capacity_reaches_a_large_requirement_in_finite_steps():
    assert grown_capacity(128, 10_000) >= 10_000
    assert grown_capacity(0, 500) >= 500


def test_grown_capacity_is_a_noop_when_already_sufficient():
    assert grown_capacity(4096, 100) == 4096
    assert grown_capacity(4096, 4096) == 4096


def test_malformed_inputs_stay_positive():
    assert fitted_capacity("nope", initial=800) == 800
    assert fitted_capacity(100, initial="nope") == 100 + MIN_GROWTH_MARGIN
    assert grown_capacity("nope", "nope") >= 1


def test_growth_ratio_constants_are_sane():
    assert 0.0 < GROWTH_MARGIN_RATIO <= 0.5
    assert MIN_GROWTH_MARGIN > 0
