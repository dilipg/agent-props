"""The one seeded source: an addressed id, and a stream of deterministic values.

`docs/contracts.md` section 9 and rulings R-10, R-46 and ground rule 9.

Why this module started at M5 and is finished at M7
---------------------------------------------------

The repo layout assigns `expansion/` to "M7+", where ``dataset_expand`` lands.
But ruling R-10 requires dataset and skeleton ids to come from
``Seeded.uuid()``, and M5 mints both kinds, so ruling R-46 started the module
there with **``uuid()`` only** and left ``int()``, ``choice()``, ``shuffled()``
and ``timestamp()`` to M7 - "an unused generator method is untested surface,
and M7's `hypothesis` property test is what will establish the determinism
guarantees for the others". This is that milestone; the four are here and
`tests/unit/test_seeded.py` carries both the pinned derivation and the
property test.

Two modes, and the reason there are two
---------------------------------------

:meth:`Seeded.uuid` is **addressed**: it takes an explicit salt and is a pure
function of ``(seed, self.salt, salt)``, with no hidden state. It has to be,
because ruling R-10 makes an id re-derivable from its seed and R-46 pins the
derivation with a literal test - an id that depended on how many other values
had been drawn first would not be re-derivable at all.

The four value generators are a **stream**: contracts section 9 gives them no
salt parameter, and expansion needs *N* distinct draws, so each draw advances a
per-instance counter that is mixed into the digest. Two fresh
``Seeded(seed, salt)`` instances therefore produce identical sequences, which is
the determinism guarantee, while a single instance does not repeat itself.
:attr:`Seeded.draws` exposes the counter so a test can assert on stream
position rather than inferring it.

Everything derives from one primitive
-------------------------------------

:meth:`Seeded.int` is the only method that hashes for a value.
:meth:`Seeded.choice`, :meth:`Seeded.shuffled` and :meth:`Seeded.timestamp` are
written in terms of it. That is deliberate: four independent derivations would
be four things to pin, four places for a bias to hide, and four ways for a
future edit to change one method output without changing the others.

What "deterministic" has to mean here
-------------------------------------

Ground rule 9 applies to every line: **no** ``uuid4()``, **no** ``random``,
**no** clock in this module. Three consequences that are easy to get wrong:

**The digest has to be stable across processes and Python versions.** Python
built-in ``hash()`` is randomised per process by ``PYTHONHASHSEED``, so a
``hash()``-derived value would differ between two runs of the same code - the
exact failure mode determinism exists to prevent. :mod:`hashlib` is specified
byte-for-byte, so ``blake2b`` is used here and its ``person`` parameter gives
each derivation its own domain: the id stream and the value stream cannot
collide even when they share a ``(seed, salt)``.

**The salt encoding has to be unambiguous.** ``salt`` is caller-supplied text
and the derivation mixes more than one component, so a plain separator would
let two different component tuples produce one byte string - the classic
canonicalisation bug. Every component is therefore length-prefixed (see
:func:`_encode`), which is injective for any component values, including ones
containing the separator character, empty strings, and any Python ``int``.

**A modulo is not a uniform draw.** :meth:`Seeded.int` uses rejection sampling
against the largest multiple of the span that fits in 64 bits, so every value
in ``[lo, hi]`` is equally likely. The loop has exactly one exit and terminates
because the acceptance probability is never below one half - which holds only
for a span of at most ``2**64``, so that is a **guard** and not a comment. A
wider span made this method hang; see the method.

The derivation is pinned by a test
----------------------------------

Ruling R-46: "pin the derivation with a test asserting a specific
``(seed, salt)`` yields a specific UUID". Ids minted before a later change
would otherwise silently stop being reproducible from their seed, and an id is
the one thing in a dataset that other records point at.
``tests/unit/test_seeded.py`` carries that test, with literal expected values,
for the id stream **and** for the value stream.
**Changing anything in :func:`_encode`, :func:`_digest`, :meth:`Seeded.uuid` or
:meth:`Seeded.int` changes every value this service has ever derived**, which is
why the constants are named and the tests are literals rather than round trips.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

__all__ = ["Seeded"]

#: Domain separation for the id digest, so the value stream below cannot
#: produce a number that collides with an id derived from the same
#: ``(seed, salt)``. ``blake2b`` takes at most 16 bytes here.
#:
#: **This constant is pinned.** Ruling R-46 requires a specific ``(seed, salt)``
#: to keep yielding a specific UUID, and changing this byte string changes every
#: id this service has ever minted.
_PERSON_UUID: Final = b"agentprops:uuid"

#: Domain separation for the value stream - :meth:`Seeded.int` and the three
#: methods written in terms of it. Shorter than :data:`_PERSON_UUID` because
#: the ``person`` parameter of ``blake2b`` is capped at 16 bytes and
#: ``agentprops:value`` would be seventeen; the id person keeps its longer
#: spelling because it is pinned and cannot be tidied.
_PERSON_VALUE: Final = b"ap:value"

#: A UUID is 16 bytes, so the id digest is sized to it exactly and no bytes are
#: discarded.
_UUID_BYTES: Final = 16

#: Bytes per value draw. Sixty-four bits is wide enough that rejection sampling
#: essentially never rejects for any span this service uses, and it is the width
#: of the integer columns `service/limits.py` bounds arguments to.
_DRAW_BYTES: Final = 8

#: ``2 ** 64``. The size of the space one draw covers.
_DRAW_SPACE: Final = 1 << (_DRAW_BYTES * 8)

#: Bytes per component length prefix. Four is more than any salt this service
#: builds and keeps the encoding fixed-width, which is what makes it injective
#: without an escaping pass.
_LENGTH_BYTES: Final = 4

#: ``int``, aliased so it survives the class body.
#:
#: Not decoration. :meth:`Seeded.int` is the name contracts section 9 gives the
#: method, and it shadows the builtin *inside the class scope* - so an
#: annotation on any member declared after it resolves to the method rather than
#: to the type, and ``mypy --strict`` reports "Function ... is not valid as a
#: type". Method *bodies* are unaffected, because class scope is not in the
#: lookup chain at call time; only annotations are. Renaming the method would
#: diverge from the contract, so the type gets the second name instead.
_Int = int


def _encode(*components: str) -> bytes:
    """The components as one unambiguous byte string.

    Length-prefixed rather than separator-joined. With a separator,
    ``("a:b", "c")`` and ``("a", "b:c")`` encode identically and two different
    salts mint one id; with a four-byte big-endian length in front of each
    UTF-8 component, the encoding is injective for every tuple of strings.
    """
    return b"".join(
        len(encoded).to_bytes(_LENGTH_BYTES, "big") + encoded
        for encoded in (component.encode("utf-8") for component in components)
    )


def _digest(person: bytes, size: int, *components: str) -> bytes:
    """``size`` bytes of ``blake2b`` over :func:`_encode`, in the ``person`` domain.

    One function, so the id stream and the value stream cannot drift into two
    slightly different derivations. ``person`` is what separates them.
    """
    return hashlib.blake2b(_encode(*components), digest_size=size, person=person).digest()


class Seeded:
    """The seeded source. ``(seed, salt)`` in, deterministic values out.

    ``salt`` on the constructor scopes the whole stream - contracts section 9
    describes it as "the node id or field path, so two fields expanded from one
    seed do not correlate" - and it defaults to empty so that a caller with
    nothing to scope by writes ``Seeded(seed)``. :meth:`uuid` takes a second,
    per-value salt, which is the signature ruling R-10 gives; the two are
    distinct components of the derivation, so ``Seeded(s, "a").uuid("")`` and
    ``Seeded(s).uuid("a")`` are different ids rather than an accidental
    collision.

    Instances are cheap and are meant to be. The idiomatic use for a
    positionally addressed expansion is one instance per position -
    ``Seeded(seed, f"expand:{node_id}:{index}")`` - which makes the output a
    function of the position rather than of the order the caller happened to
    generate in. `service/expansion.py` does exactly that and its docstring
    says why.
    """

    def __init__(self, seed: int, salt: str = "") -> None:
        self.seed = seed
        self.salt = salt
        self._drawn = 0

    @property
    def draws(self) -> int:
        """How many values this instance has drawn.

        Exposed so a test can assert on the stream position rather than infer
        it from the values, and so a caller can record how far a deterministic
        stream was consumed. :meth:`uuid` does not advance it: an id is
        addressed, not drawn.
        """
        return self._drawn

    def uuid(self, salt: str) -> uuid.UUID:
        """An RFC 4122-shaped UUID derived from ``(seed, self.salt, salt)``.

        **Addressed, not drawn**: a pure function of its three components, with
        no dependence on how many values this instance has produced. Ruling
        R-10 requires an id to be re-derivable from its seed, and R-46 pins the
        derivation with a literal test.

        Version 4 and the RFC 4122 variant bits are stamped over the digest, so
        the value is a well-formed UUID that any consumer parses - matching the
        shape of the hand-built deterministic id in the golden fixture,
        ``3f8c1a20-0000-4000-8000-000000000001`` (ruling R-10). It is *shaped*
        like a v4 while being nothing like random, which is the point: the
        column type is ``UUID``, and the value has to be reproducible from the
        seed.

        Stamping the two fields costs 6 bits of the 128 and is what stops a
        ``uuid`` parser from rejecting the value or a database from normalising
        it.
        """
        digest = bytearray(_digest(_PERSON_UUID, _UUID_BYTES, str(self.seed), self.salt, salt))
        digest[6] = (digest[6] & 0x0F) | 0x40  # version 4
        digest[8] = (digest[8] & 0x3F) | 0x80  # RFC 4122 variant
        return uuid.UUID(bytes=bytes(digest))

    def int(self, lo: int, hi: int) -> int:
        """A uniform integer in ``[lo, hi]``, **both ends inclusive**.

        The one primitive. :meth:`choice`, :meth:`shuffled` and
        :meth:`timestamp` are written in terms of it, so there is one
        derivation to pin and one place a bias could hide.

        Inclusive at both ends because contracts section 9 spells the signature
        ``int(self, lo: int, hi: int)`` with no half-open convention stated, and
        an inclusive range is the one a caller writing ``int(1, 6)`` means.
        ``lo == hi`` is legal and returns ``lo`` - while still advancing the
        stream, so the sequence a caller gets depends on how many values it
        drew and not on how wide the ranges were.

        **Rejection sampling, not a modulo.** ``drawn % span`` over-represents
        the first ``2**64 % span`` values of the range; the bias is tiny but it
        is a bias, and a generator whose whole job is reproducible fixtures
        should not also be quietly skewed. So a draw is rejected unless it falls
        below the largest multiple of ``span`` that fits in 64 bits.

        The loop has exactly **one** exit, deliberately - a retry loop with more
        than one is the shape that goes wrong - and it terminates because the
        acceptance probability is never below one half: ``ceiling`` is at least
        ``2**64 - span + 1``, and ``span`` is at most ``2**64`` **because the
        guard below enforces it**. For every span this service uses the
        acceptance probability is above ``1 - 2**-32``, so a second iteration is
        effectively unreachable.

        That precondition used to be stated as a fact and enforced by nothing,
        which made this method **hang** rather than fail for a wider span:
        ``_DRAW_SPACE % span`` is ``2**64`` when ``span`` is ``2**64 + 1``, so
        ``ceiling`` is ``0``, no draw is ever below it, and ``while True``
        never exits. No caller reaches it today - `service/limits.py` bounds
        every integer that gets here to 64 bits - but an unbounded ``drift_s``
        arriving at :meth:`timestamp` later would have produced a wedged process
        with no exception and no log, which is the most expensive shape of
        failure to diagnose. A termination proof whose premise nothing checks is
        not a proof, so the premise is now a guard.

        Raises ``ValueError`` for ``lo > hi`` and for a span wider than
        ``2**64``. Both are programming errors rather than values a user
        supplied: `service/` clamps a caller's integer through `limits.py`
        before it reaches here, so the alternative to raising is silently
        returning the wrong end in the first case and never returning at all in
        the second.
        """
        if lo > hi:
            raise ValueError(f"int() needs lo <= hi, got lo={lo}, hi={hi}")
        span = hi - lo + 1
        if span > _DRAW_SPACE:
            raise ValueError(
                f"int() needs a span of at most 2**{_DRAW_BYTES * 8}, got {span} (lo={lo}, hi={hi})"
            )
        ceiling = _DRAW_SPACE - (_DRAW_SPACE % span)
        while True:
            drawn = self._draw()
            if drawn < ceiling:
                return lo + drawn % span

    def choice[ItemT](self, xs: Sequence[ItemT]) -> ItemT:
        """One element of ``xs``, uniformly. Costs exactly one draw.

        Raises ``ValueError`` on an empty sequence, for the reason :meth:`int`
        raises: there is no element to return, and no caller in this service can
        reach it with an empty one - `service/expansion.py` reports the
        catalogue rule that owns "this node has no pool to expand" before it
        gets here.
        """
        if not xs:
            raise ValueError("choice() needs a non-empty sequence")
        return xs[self.int(0, len(xs) - 1)]

    def shuffled[ItemT](self, xs: Sequence[ItemT]) -> list[ItemT]:
        """A new list holding the elements of ``xs`` in a deterministic permutation.

        Fisher-Yates, downward, so the number of draws is exactly
        ``max(len(xs) - 1, 0)`` and a one-element sequence costs nothing. The
        input is never mutated - the name is ``shuffled``, not ``shuffle``, and
        contracts section 9 types the return as a new ``list``.
        """
        items = list(xs)
        for index in range(len(items) - 1, 0, -1):
            swap = self.int(0, index)
            items[index], items[swap] = items[swap], items[index]
        return items

    def timestamp(self, base: datetime, drift_s: _Int) -> datetime:
        """``base`` moved **forward** by a deterministic 0 to ``drift_s`` seconds.

        Forward rather than symmetric, and the choice is worth stating because
        contracts section 9 gives the signature and not the direction. A dataset
        encodes a timeline baked at authoring time - entity revisions are
        ordered by ``after_node``, and DS-010 reads that order - so a drift that
        could move a derived timestamp *before* its base is the one direction
        that can make an expanded timeline incoherent. A caller that genuinely
        wants a symmetric jitter of ``d`` writes
        ``timestamp(base - timedelta(seconds=d), 2 * d)``, which is explicit
        about what it is doing.

        ``drift_s == 0`` returns ``base`` unchanged, and still costs one draw.
        ``tzinfo`` is carried through untouched: this method moves a value, it
        does not read a clock or choose a timezone.

        Raises ``ValueError`` for a negative ``drift_s``, rather than letting it
        surface from :meth:`int` as an inverted range whose message names
        numbers the caller never wrote.
        """
        if drift_s < 0:
            raise ValueError(f"timestamp() needs drift_s >= 0, got {drift_s}")
        return base + timedelta(seconds=self.int(0, drift_s))

    def _draw(self) -> _Int:
        """The next 64-bit value from the stream, advancing the counter.

        The counter is a component of the digest rather than a state the digest
        is fed through, so draw *N* is a pure function of ``(seed, salt, N)``.
        Two consequences: a stream can be resumed at any position without
        replaying it, and a lost draw cannot corrupt the ones after it.
        """
        index = self._drawn
        self._drawn += 1
        return int.from_bytes(
            _digest(_PERSON_VALUE, _DRAW_BYTES, str(self.seed), self.salt, str(index)), "big"
        )
