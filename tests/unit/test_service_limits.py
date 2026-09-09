"""The int64 range, and both ends of it.

This module exists because the `paginate` entry in `DECISIONS.md` reasoned
carefully about backend integer semantics **in the negative direction only** and
left the upper bound unclamped and untested. The residue was an `OverflowError`
escaping three tool paths. So the rule these tests encode is the one the review
drew from it: when a decision reasons about a boundary, test both ends of it.
"""

from __future__ import annotations

from agentprops.service.limits import (
    DEFAULT_PAGE_LIMIT,
    MAX_STORED_INT,
    MIN_STORED_INT,
    clamp,
    page,
    storable,
)


def test_the_range_is_signed_sixty_four_bit() -> None:
    """The exact boundary the reproduction found, stated as a constant."""
    assert MAX_STORED_INT == 2**63 - 1
    assert MIN_STORED_INT == -(2**63)


def test_clamp_leaves_a_value_inside_the_range_alone() -> None:
    for value in (0, 1, 50, MAX_STORED_INT):
        assert clamp(value) == value


def test_clamp_pulls_in_both_ends() -> None:
    assert clamp(-1) == 0
    assert clamp(-(10**40)) == 0
    assert clamp(MAX_STORED_INT + 1) == MAX_STORED_INT
    assert clamp(10**40) == MAX_STORED_INT


def test_clamp_takes_an_explicit_window() -> None:
    assert clamp(7, low=10, high=20) == 10
    assert clamp(70, low=10, high=20) == 20


def test_storable_is_true_exactly_inside_the_range() -> None:
    assert storable(MAX_STORED_INT) is True
    assert storable(MAX_STORED_INT + 1) is False
    assert storable(MIN_STORED_INT) is True
    assert storable(MIN_STORED_INT - 1) is False
    assert storable(0) is True


def test_page_defaults_and_clamps_in_one_place() -> None:
    """The two paging decisions ``dataset_find`` and ``run_find`` share.

    Both live here at M6 rather than being copied into the second find tool.
    The default is a page size and not a cap, so an explicit larger limit is
    honoured; a negative one is a per-backend accident (SQLite reads
    ``LIMIT -1`` as "no limit", Postgres rejects it) and clamps to zero.
    """
    assert page(None, None) == (DEFAULT_PAGE_LIMIT, 0)
    assert page(5000, 10) == (5000, 10)
    assert page(-3, -9) == (0, 0)
    assert page(MAX_STORED_INT, MAX_STORED_INT) == (MAX_STORED_INT, MAX_STORED_INT)
    assert page(MAX_STORED_INT + 1, 10**40) == (MAX_STORED_INT, MAX_STORED_INT)


def test_zero_is_a_meaningful_page_size_and_survives() -> None:
    """The one value the clamp must not confuse with "unset".

    ``limit=0`` is a caller asking for no rows, and ``page`` has to keep it
    distinct from ``limit=None``, which means "use the default". A single
    ``or``-style default would collapse the two.
    """
    assert page(0, 0) == (0, 0)
