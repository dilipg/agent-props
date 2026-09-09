"""The seeded id source: the derivation, pinned, and the claims its docstring makes.

Ruling R-46 asks for one specific thing here - "pin the derivation with a test
asserting a specific ``(seed, salt)`` yields a specific UUID" - and gives the
reason: ids minted before a later change would otherwise silently stop being
reproducible from their seed, and an id is the one field in a dataset that other
records point at. :data:`PINNED` is that test, with literal expected values.

**If you change `expansion/seeded.py`, these values change and every id this
service has ever minted stops being derivable.** That is not a test to update;
it is a migration to think about.

The rest of this module tests the two properties the module docstring claims,
rather than leaving them as prose: the encoding is injective (so two different
salts cannot mint one id) and the digest is stable across processes (so a
``hash()``-based implementation, which ``PYTHONHASHSEED`` randomises per
process, fails loudly instead of silently minting different ids on Tuesday).

M7 adds the other four methods and the ``hypothesis`` property test
`build-handoff.md` puts in exactly one place. Three things are pinned for them
too, for R-46 reasons that transfer unchanged:

- :data:`PINNED_DRAWS` fixes the value stream, so a future edit to
  :func:`_digest` or :meth:`Seeded.int` fails here rather than silently
  changing every fixture the expansion has ever produced;
- the property test asserts the two claims `build-handoff.md` names -
  identical ``(seed, salt)`` yields identical output, differing seeds usually
  diverge - over generated inputs rather than over the four examples an author
  thought of;
- the cross-process test covers the value stream as well as the id, because
  ``PYTHONHASHSEED`` is the failure that never shows up in one process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentprops.expansion.seeded import Seeded
from agentprops.service.limits import MAX_STORED_INT, MIN_STORED_INT

#: ``(seed, salt, expected)``. Literal values, deliberately: a round trip
#: through the implementation would pass against any derivation at all.
PINNED: Final[tuple[tuple[int, str, str], ...]] = (
    (20260908, "skeleton:location-onboarding:1.0.0", "18379d9d-be63-478c-8522-ef24befc6428"),
    (
        20260908,
        "dataset:3f8c1a20-0000-4000-8000-00000000aaaa",
        "78d101f1-5d9b-4805-a08c-881f38732d08",
    ),
    (0, "", "52801f0a-48c3-4fa1-b772-d7c461e2ed02"),
    (-1, "x", "cf14b31f-aa30-4b90-8fe8-c3ef1a393cf6"),
)


@pytest.mark.parametrize(("seed", "salt", "expected"), PINNED)
def test_the_derivation_is_pinned(seed: int, salt: str, expected: str) -> None:
    """Ruling R-46's requirement. See this module's docstring before touching it."""
    assert str(Seeded(seed).uuid(salt)) == expected


def test_an_id_is_shaped_like_an_rfc_4122_v4() -> None:
    """Version 4 and the RFC 4122 variant, matching the golden fixture's shape.

    Ruling R-10 asks for "an RFC 4122-shaped value ... matching the shape of the
    golden fixture's hand-built deterministic id
    (``3f8c1a20-0000-4000-8000-000000000001``)", whose 13th and 17th hex digits
    are ``4`` and ``8``. The value is *shaped* like a v4 while being nothing
    like random, which is the point: the column type is ``UUID`` and the value
    has to be reproducible from the seed.
    """
    for seed, salt, _ in PINNED:
        minted = Seeded(seed).uuid(salt)
        assert minted.version == 4
        assert minted.variant == uuid.RFC_4122
        assert str(minted)[14] == "4"
        assert str(minted)[19] in "89ab"


def test_the_same_seed_and_salt_always_give_the_same_id() -> None:
    assert Seeded(7).uuid("a") == Seeded(7).uuid("a")


def test_a_different_seed_or_salt_gives_a_different_id() -> None:
    """Not a probabilistic claim about a hash - a check that both inputs are read.

    An implementation that ignored the salt, or ignored the seed, would pass
    every other test in this module except the pinned values.
    """
    assert Seeded(7).uuid("a") != Seeded(8).uuid("a")
    assert Seeded(7).uuid("a") != Seeded(7).uuid("b")


def test_the_salt_encoding_is_injective() -> None:
    """The canonicalisation bug the length prefixes exist to prevent.

    With a separator-joined encoding, ``("a:b", "c")`` and ``("a", "b:c")``
    produce one byte string and therefore one id - so two different skeletons
    would collide, and ``put_skeleton`` would overwrite one with the other. The
    salts here are the pairs that collide under every separator this module
    might plausibly have used.
    """
    assert Seeded(1, "a:b").uuid("c") != Seeded(1, "a").uuid("b:c")
    assert Seeded(1, "a").uuid("") != Seeded(1, "").uuid("a")
    assert Seeded(1, "ab").uuid("") != Seeded(1, "a").uuid("b")
    assert Seeded(1).uuid("a\x1fb") != Seeded(1, "a").uuid("b")


def test_the_seed_is_read_as_a_value_not_as_text() -> None:
    """``Seeded`` takes an ``int``; a caller cannot smuggle a salt through it.

    ``str(seed)`` is what goes into the digest, so the pair below would collide
    if the seed were concatenated without its own length prefix.
    """
    assert Seeded(1).uuid("2:x") != Seeded(12).uuid(":x")


def test_the_derivation_is_stable_across_processes() -> None:
    """The claim the module docstring makes, tested rather than asserted in prose.

    Three subprocesses with different ``PYTHONHASHSEED`` values, including
    ``random``, which is the value that actually varies. Python's built-in
    ``hash()`` is randomised per process, so a ``hash()``-derived id would
    differ between two runs of identical code - which is exactly the failure
    determinism exists to prevent, and exactly the failure that would never
    appear in a single-process test suite.
    """
    script = "from agentprops.expansion.seeded import Seeded; print(Seeded(20260908).uuid('n'))"
    outputs = set()
    for hash_seed in ("0", "1", "random"):
        environment = {**os.environ, "PYTHONHASHSEED": hash_seed}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1, f"the derivation is process-dependent: {outputs}"
    assert outputs == {str(Seeded(20260908).uuid("n"))}


# --------------------------------------------------------------------------
# M7: the value stream - int(), choice(), shuffled(), timestamp()
# --------------------------------------------------------------------------


#: ``(seed, salt, lo, hi, expected)`` for the first draws of one stream.
#: Literal, for the reason :data:`PINNED` is: a round trip through the
#: implementation would pass against any derivation at all, and this stream is
#: what every expanded pool entry is chosen by.
PINNED_DRAWS: Final[tuple[tuple[int, str, int, int, list[int]], ...]] = (
    (20260908, "expand:request_docs", 0, 999, [126, 584, 762, 484, 911, 340]),
    (0, "", 1, 6, [5, 6, 4, 6, 1, 6, 4, 6]),
    (-1, "x", -10, 10, [-2, -9, -2, -7, 9]),
)


@pytest.mark.parametrize(("seed", "salt", "lo", "hi", "expected"), PINNED_DRAWS)
def test_the_value_stream_is_pinned(
    seed: int, salt: str, lo: int, hi: int, expected: list[int]
) -> None:
    """Ruling R-46 again, for the four methods it deferred to M7.

    The stream is what ``dataset_expand`` chooses every entry with, so changing
    it changes every expanded pool this service has ever written - the same
    class of consequence as changing an id derivation, and it gets the same
    literal pin.
    """
    source = Seeded(seed, salt)
    assert [source.int(lo, hi) for _ in expected] == expected


def test_two_fresh_sources_produce_the_same_sequence() -> None:
    """The determinism guarantee for a *stream* rather than for an address.

    The four value methods take no salt argument (contracts section 9), so each
    draw advances a counter. That makes the sequence a function of the call
    count, and two fresh instances therefore agree draw for draw.
    """
    first = Seeded(20260908, "n")
    second = Seeded(20260908, "n")
    assert [first.int(0, 10**9) for _ in range(20)] == [second.int(0, 10**9) for _ in range(20)]


def test_a_single_source_does_not_repeat_itself() -> None:
    """The other half: a stream that returned one value forever is not a stream.

    Not a probabilistic claim - an implementation that ignored the counter would
    return twenty identical values, which is the failure this catches.
    """
    source = Seeded(20260908, "n")
    drawn = [source.int(0, 10**12) for _ in range(20)]
    assert len(set(drawn)) == 20
    assert source.draws == 20


def test_uuid_is_addressed_and_does_not_touch_the_stream() -> None:
    """Ruling R-10 needs an id re-derivable from ``(seed, salt)`` alone.

    So ``uuid()`` must not consume a draw, and must not be perturbed by draws
    taken before it - otherwise an id would depend on how much expansion
    happened first, and no test could pin it.
    """
    source = Seeded(20260908, "n")
    before = source.uuid("d")
    for _ in range(5):
        source.int(0, 100)
    assert source.uuid("d") == before
    assert source.draws == 5, "uuid() advanced the value stream"


def test_int_is_inclusive_at_both_ends() -> None:
    """``int(lo, hi)`` covers ``hi``, and ``int(n, n)`` is ``n``.

    Both ends, because a half-open implementation passes every other assertion
    in this module - and ``choice`` is written as ``int(0, len - 1)``, so an
    exclusive upper bound would make the last element of every pool
    unreachable.
    """
    source = Seeded(20260908, "bounds")
    seen = {source.int(0, 1) for _ in range(40)}
    assert seen == {0, 1}
    assert Seeded(5).int(7, 7) == 7
    assert all(-3 <= Seeded(seed).int(-3, 3) <= 3 for seed in range(50))


def test_int_advances_the_stream_even_for_a_one_value_range() -> None:
    """A degenerate range still costs a draw, and that is deliberate.

    Otherwise the stream position would depend on how *wide* the ranges were
    rather than on how many values were drawn, and a caller that changed a
    range from ``(0, 0)`` to ``(0, 1)`` would silently shift every later draw.
    """
    source = Seeded(1)
    source.int(4, 4)
    assert source.draws == 1


def test_int_refuses_an_inverted_range() -> None:
    """A programming error, not a user-caused one: `service/` clamps first."""
    with pytest.raises(ValueError, match="lo <= hi"):
        Seeded(1).int(5, 4)


def test_int_covers_the_whole_storable_range() -> None:
    """The widest span `limits.py` allows, so rejection sampling is exercised at
    the one point the arithmetic could overflow a 64-bit draw."""
    value = Seeded(5).int(-(2**63), 2**63 - 1)
    assert -(2**63) <= value <= 2**63 - 1


def test_choice_picks_from_the_sequence_and_costs_one_draw() -> None:
    source = Seeded(20260908, "pick")
    items = ["a", "b", "c"]
    picked = [source.choice(items) for _ in range(30)]
    assert set(picked) == set(items), "at least one element is unreachable"
    assert source.draws == 30


def test_choice_reaches_the_last_element() -> None:
    """The off-by-one an exclusive ``int`` upper bound would introduce.

    Asserted separately from the set check above because a two-element sequence
    is where the bug is unmissable: an exclusive bound would return ``first``
    forever.
    """
    picked = {Seeded(seed).choice(["first", "last"]) for seed in range(40)}
    assert picked == {"first", "last"}


def test_choice_refuses_an_empty_sequence() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        Seeded(1).choice([])


def test_shuffled_permutes_without_mutating_its_input() -> None:
    original = [1, 2, 3, 4, 5]
    permuted = Seeded(20260908, "perm").shuffled(original)
    assert sorted(permuted) == original
    assert original == [1, 2, 3, 4, 5], "shuffled() mutated its argument"
    assert Seeded(20260908, "perm").shuffled(original) == permuted


def test_shuffled_actually_reorders_something() -> None:
    """Not a claim about randomness - a check that the swap loop runs.

    An implementation that returned ``list(xs)`` unchanged would satisfy every
    other assertion about ``shuffled``.
    """
    items = list(range(10))
    assert any(Seeded(seed).shuffled(items) != items for seed in range(10))


def test_shuffled_costs_one_draw_per_swap() -> None:
    """Fisher-Yates downward: ``len - 1`` draws, and nothing for a short list."""
    source = Seeded(1)
    source.shuffled([1, 2, 3, 4, 5])
    assert source.draws == 4
    empty = Seeded(1)
    empty.shuffled([])
    empty.shuffled(["only"])
    assert empty.draws == 0


def test_timestamp_drifts_forward_only_and_carries_the_timezone() -> None:
    """Forward, because a dataset timeline is ordered and DS-010 reads that order."""
    base = datetime(2026, 9, 8, 10, 14, 22, tzinfo=UTC)
    drifted = [Seeded(seed, "drift").timestamp(base, 600) for seed in range(40)]
    assert all(base <= moment <= base + timedelta(seconds=600) for moment in drifted)
    assert all(moment.tzinfo is UTC for moment in drifted)
    assert any(moment > base for moment in drifted), "the drift is always zero"


def test_timestamp_with_no_drift_returns_the_base_and_still_draws() -> None:
    base = datetime(2026, 9, 8, 10, 14, 22, tzinfo=UTC)
    source = Seeded(1)
    assert source.timestamp(base, 0) == base
    assert source.draws == 1


def test_timestamp_refuses_a_negative_drift() -> None:
    """Named here rather than left to surface from ``int`` as an inverted range."""
    with pytest.raises(ValueError, match="drift_s >= 0"):
        Seeded(1).timestamp(datetime(2026, 1, 1, tzinfo=UTC), -1)


def test_a_naive_base_stays_naive() -> None:
    """``timestamp`` moves a value; it does not choose a timezone (ruling R-09)."""
    naive = datetime(2026, 9, 8, 10, 14, 22)  # a naive base, which is the point
    assert Seeded(1).timestamp(naive, 60).tzinfo is None


def test_the_value_stream_is_stable_across_processes() -> None:
    """Clause 4's mechanism, at the source: ``PYTHONHASHSEED`` must not matter.

    The same subprocess pattern as the id test above, and the reason is the
    same one: a ``hash()``-derived stream differs between two runs of identical
    code, and no single-process test can see it. This is the guarantee that
    ``dataset_expand``'s cross-process test depends on, tested one layer down so
    a failure names the cause rather than the symptom.
    """
    script = (
        "from agentprops.expansion.seeded import Seeded\n"
        "s = Seeded(20260908, 'expand:request_docs')\n"
        "print([s.int(0, 999) for _ in range(6)], s.choice(list('abc')), s.shuffled([1,2,3,4]))\n"
    )
    outputs = set()
    for hash_seed in ("0", "1", "random"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
        )
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1, f"the value stream is process-dependent: {outputs}"


# --------------------------------------------------------------------------
# the one place `hypothesis` is worth it (build-handoff.md, testing strategy)
# --------------------------------------------------------------------------


SEEDS = st.integers(min_value=MIN_STORED_INT, max_value=MAX_STORED_INT)
SALTS = st.text(max_size=40)


@given(seed=SEEDS, salt=SALTS, draws=st.integers(min_value=1, max_value=12))
def test_identical_seed_and_salt_always_yield_identical_output(
    seed: int, salt: str, draws: int
) -> None:
    """The first of the two claims `build-handoff.md` assigns to `hypothesis`.

    "Identical ``(seed, salt)`` yields identical output" - over generated
    inputs, including the ones an author would not have thought of: an empty
    salt, a salt of astral-plane characters, a seed at either end of the
    storable range. Every method draws from one stream, so the assertion covers
    all four rather than one.
    """
    first, second = Seeded(seed, salt), Seeded(seed, salt)
    base = datetime(2026, 9, 8, tzinfo=UTC)
    for _ in range(draws):
        assert first.int(0, 10**9) == second.int(0, 10**9)
        assert first.choice("abcdef") == second.choice("abcdef")
        assert first.shuffled(range(6)) == second.shuffled(range(6))
        assert first.timestamp(base, 3600) == second.timestamp(base, 3600)
    assert first.uuid("x") == second.uuid("x")
    assert first.draws == second.draws


@given(seed=SEEDS, other=SEEDS, salt=SALTS)
def test_differing_seeds_usually_diverge(seed: int, other: int, salt: str) -> None:
    """The second claim, stated as `build-handoff.md` states it: **usually**.

    Not "always". Two seeds drawing from a 1000-value range collide one time in
    a thousand by construction, and a test asserting inequality on a single
    narrow draw would be flaky by design. So the comparison is over a wide draw
    plus a permutation plus an id - a space where an accidental agreement is far
    less likely than a wrong implementation - and the two seeds are required to
    differ before anything is asserted.
    """
    if seed == other:
        return
    mine, theirs = Seeded(seed, salt), Seeded(other, salt)
    assert (mine.int(0, 2**62), mine.shuffled(range(8)), mine.uuid("x")) != (
        theirs.int(0, 2**62),
        theirs.shuffled(range(8)),
        theirs.uuid("x"),
    )


@given(salt=SALTS, other=SALTS)
def test_differing_salts_usually_diverge(salt: str, other: str) -> None:
    """The salt half of the same claim.

    Contracts section 9 gives the salt one job - "so two fields expanded from
    one seed do not correlate" - and this is that job, asserted over generated
    salts rather than over the four in :data:`PINNED`.
    """
    if salt == other:
        return
    mine, theirs = Seeded(20260908, salt), Seeded(20260908, other)
    assert (mine.int(0, 2**62), mine.uuid("x")) != (theirs.int(0, 2**62), theirs.uuid("x"))


@given(seed=SEEDS, salt=SALTS, lo=st.integers(), width=st.integers(min_value=0, max_value=10**9))
def test_every_draw_lands_inside_the_requested_range(
    seed: int, salt: str, lo: int, width: int
) -> None:
    """``int(lo, hi)`` never leaves the interval, at any offset or width.

    The property rejection sampling exists to keep true, and the one an
    off-by-one in the ``ceiling`` arithmetic would break. ``lo`` is unbounded on
    purpose: an expansion that offset a range past ``2**63`` should still draw
    inside it, because the bound is the *column* width and not this method.
    """
    drawn = Seeded(seed, salt).int(lo, lo + width)
    assert lo <= drawn <= lo + width


@given(seed=SEEDS, salt=SALTS, size=st.integers(min_value=0, max_value=25))
def test_shuffled_is_always_a_permutation(seed: int, salt: str, size: int) -> None:
    """Fisher-Yates loses or duplicates an element if either index is wrong."""
    items = list(range(size))
    permuted = Seeded(seed, salt).shuffled(items)
    assert sorted(permuted) == items
    assert len(permuted) == size
