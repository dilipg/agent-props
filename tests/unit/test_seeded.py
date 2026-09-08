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

``hypothesis`` is not here: `build-handoff.md` puts the property test on the
seeded expansion at M7, with the four methods M7 adds. This is the guarantee
that test generalises.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from typing import Final

import pytest

from agentprops.expansion.seeded import Seeded

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
