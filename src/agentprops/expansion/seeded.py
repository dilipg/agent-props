"""The one seeded source, and at M5 its one method: ``uuid(salt)``.

`docs/contracts.md` section 9 and rulings R-10 and R-46.

Why this module exists at M5 rather than at M7
----------------------------------------------

The repo layout assigns `expansion/` to "M7+", where ``dataset_expand`` lands.
But ruling R-10 requires dataset and skeleton ids to come from
``Seeded.uuid()``, and M5 mints both kinds, so ruling R-46 starts the module
here with **``uuid()`` only**. ``int()``, ``choice()``, ``shuffled()`` and
``timestamp()`` arrive at M7 with the expansion that needs them: an unused
generator method is untested surface, and M7's `hypothesis` property test is
what establishes the determinism guarantees for the rest.

What "deterministic" has to mean here
-------------------------------------

Ground rule 9 applies from the first line: **no** ``uuid4()``, **no**
``random``, **no** clock inside this module. An id is a pure function of
``(seed, salt)``. Two consequences that are easy to get wrong:

**The digest has to be stable across processes and Python versions.** Python's
built-in ``hash()`` is randomised per process by ``PYTHONHASHSEED``, so a
``hash()``-derived id would differ between two runs of the same code - the
exact failure mode determinism exists to prevent. :mod:`hashlib` is specified
byte-for-byte, so ``blake2b`` is used here and the ``person`` parameter gives
this derivation its own domain: a future ``Seeded`` method deriving from the
same ``(seed, salt)`` cannot collide with an id.

**The salt encoding has to be unambiguous.** ``salt`` is caller-supplied text
and the derivation mixes more than one component, so a plain separator would
let two different component tuples produce one byte string - the classic
canonicalisation bug. Every component is therefore length-prefixed (see
:func:`_encode`), which is injective for any component values, including ones
containing the separator character, empty strings, and any Python ``int``.

The derivation is pinned by a test
----------------------------------

Ruling R-46: "pin the derivation with a test asserting a specific
``(seed, salt)`` yields a specific UUID". Ids minted before a later change
would otherwise silently stop being reproducible from their seed, and an id is
the one thing in a dataset that other records point at.
``tests/unit/test_seeded.py`` carries that test, with literal expected values.
**Changing anything in :func:`_encode` or :func:`Seeded.uuid` changes every id
this service has ever minted**, which is why the constants are named and the
test is a literal rather than a round trip.
"""

from __future__ import annotations

import hashlib
import uuid

__all__ = ["Seeded"]

#: Domain separation for the digest, so a future ``Seeded`` method that hashes
#: the same ``(seed, salt)`` for a different purpose cannot produce a value
#: that collides with an id. ``blake2b`` takes at most 16 bytes here.
_PERSON = b"agentprops:uuid"

#: A UUID is 16 bytes, so the digest is sized to it exactly and no bytes are
#: discarded.
_DIGEST_SIZE = 16

#: Bytes per component length prefix. Four is more than any salt this service
#: builds and keeps the encoding fixed-width, which is what makes it injective
#: without an escaping pass.
_LENGTH_BYTES = 4


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


class Seeded:
    """The seeded source. ``(seed, salt)`` in, deterministic values out.

    ``salt`` on the constructor scopes the whole stream - contracts section 9
    describes it as "the node id or field path, so two fields expanded from one
    seed do not correlate" - and it defaults to empty so that a caller with
    nothing to scope by writes ``Seeded(seed)``. :meth:`uuid` takes a second,
    per-value salt, which is ruling R-10's signature; the two are distinct
    components of the derivation, so ``Seeded(s, "a").uuid("")`` and
    ``Seeded(s).uuid("a")`` are different ids rather than an accidental
    collision.
    """

    def __init__(self, seed: int, salt: str = "") -> None:
        self.seed = seed
        self.salt = salt

    def uuid(self, salt: str) -> uuid.UUID:
        """An RFC 4122-shaped UUID derived from ``(seed, self.salt, salt)``.

        Version 4 and the RFC 4122 variant bits are stamped over the digest, so
        the value is a well-formed UUID that any consumer parses - matching the
        shape of the golden fixture's hand-built deterministic id,
        ``3f8c1a20-0000-4000-8000-000000000001`` (ruling R-10). It is *shaped*
        like a v4 while being nothing like random, which is the point: the
        column type is ``UUID``, and the value has to be reproducible from the
        seed.

        Stamping the two fields costs 6 bits of the 128 and is what stops a
        consumer's ``uuid`` parser from rejecting the value or a database from
        normalising it.
        """
        digest = bytearray(
            hashlib.blake2b(
                _encode(str(self.seed), self.salt, salt),
                digest_size=_DIGEST_SIZE,
                person=_PERSON,
            ).digest()
        )
        digest[6] = (digest[6] & 0x0F) | 0x40  # version 4
        digest[8] = (digest[8] & 0x3F) | 0x80  # RFC 4122 variant
        return uuid.UUID(bytes=bytes(digest))
