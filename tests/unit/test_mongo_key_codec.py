"""The Mongo key codec: injective, and injective for the keys that break Mongo.

`storage/mongo.py` claims :func:`encode_keys` is "provably injective" and gives
the argument in prose. This is the test of that claim, because a safety claim in
a docstring is worth nothing next to a test of the losing side - and the losing
side here is a silently mangled object key inside an authored fixture, which
nothing downstream would notice.

Why the codec exists at all, in one line each:

- ``Skeleton.parts`` is keyed by section id, and two of ruling R-06's five
  section ids are ``nodes.core`` and ``nodes.branches``. Mongo reads
  ``parts.nodes.core`` as two levels of nesting.
- every *authored* document this service stores - a fixture's ``output``, an
  entity's ``state``, a step's ``served`` - is arbitrary caller JSON whose keys
  this adapter does not get to constrain, and a leading ``$`` looks like an
  operator.

The adversarial cases are the ones where a naive escape stops being reversible:
a key that *already contains* the escape sequence. ``%2E`` must survive as a
literal ``%2E`` rather than decoding back to a dot, and the order of the three
replacements is the only reason it does. :data:`ADVERSARIAL` is that set, and
the `hypothesis` property test generalises it over generated keys drawn from an
alphabet made almost entirely of the characters involved.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentprops.models import SECTION_IDS
from agentprops.storage.mongo import decode_keys, encode_keys

#: Keys a naive escape gets wrong, each for a different reason.
ADVERSARIAL: Final[tuple[str, ...]] = (
    "nodes.core",  # the section id that forced the codec to exist
    "nodes.branches",
    "%2E",  # already looks like an encoded dot
    "%24",  # already looks like an encoded dollar
    "%25",  # already looks like an encoded percent
    "%252E",  # what an encoded "%2E" looks like
    "%",
    "%%2E",
    ".",
    "..",
    "$",
    "$set",  # would read as an operator unescaped
    "$$NOW",
    "a.b$c%d",
    "",  # legal JSON, and the one case Mongo itself is uneasy about
    "already%20encoded",
)


@pytest.mark.parametrize("key", ADVERSARIAL, ids=lambda key: repr(key))
def test_the_codec_round_trips_an_adversarial_key(key: str) -> None:
    """Encode then decode is the identity, for every key that could break it."""
    assert decode_keys(encode_keys({key: 1})) == {key: 1}


@pytest.mark.parametrize("key", ADVERSARIAL, ids=lambda key: repr(key))
def test_an_encoded_key_is_addressable_by_mongo(key: str) -> None:
    """The property the codec exists for: no dot and no leading ``$``.

    A dot anywhere makes the key unaddressable by a dot path, and a ``$``
    anywhere is escaped rather than only a leading one - because "only the
    first character matters" is a rule that has changed between server versions
    and is not worth depending on.
    """
    (encoded,) = encode_keys({key: 1})
    assert "." not in encoded
    assert "$" not in encoded


def test_every_section_id_survives_the_codec() -> None:
    """Ruling R-06's five ids, by name, because ``parts`` is keyed by them.

    Not a re-run of the parametrised case above: this reads
    :data:`SECTION_IDS` from the model, so a section id gaining a dot in a
    later milestone is covered without an edit here.
    """
    parts: dict[str, Any] = {section: {"filled": True} for section in SECTION_IDS}
    assert decode_keys(encode_keys(parts)) == parts
    assert all("." not in key for key in encode_keys(parts))


def test_only_keys_are_transformed() -> None:
    """A *value* that looks like an escape comes back exactly as it went in.

    This is what makes the codec safe to apply to authored content: a narrative
    that mentions ``%2E``, or an ``output`` whose value is the string
    ``"$total"``, is not content the adapter gets to rewrite.
    """
    document = {"a.b": "value.with.dots", "c": ["%2E", "$total", "a.b"], "d": {"e.f": "$$NOW"}}
    encoded = encode_keys(document)
    assert encoded["c"] == ["%2E", "$total", "a.b"]
    assert decode_keys(encoded) == document


def test_nested_structures_are_walked() -> None:
    """Objects inside arrays inside objects, which is the shape ``manifest`` has."""
    document = {"a.b": [{"c.d": [{"e.f": 1}]}], "g": [[{"h.i": 2}]]}
    encoded = encode_keys(document)
    assert list(encoded) == ["a%2Eb", "g"]
    assert decode_keys(encoded) == document


def test_a_non_object_passes_through() -> None:
    """``runs.model`` and ``run_steps.actual`` are nullable, and a scalar is legal JSON."""
    for value in (None, 1, "x", True, [1, 2], []):
        assert encode_keys(value) == value
        assert decode_keys(value) == value


KEY_ALPHABET: Final = st.text(alphabet="%.$2E45abc", max_size=12)


@given(keys=st.lists(KEY_ALPHABET, max_size=6, unique=True))
def test_the_codec_is_injective_over_generated_keys(keys: list[str]) -> None:
    """The claim `storage/mongo.py` makes, over an alphabet built to break it.

    Nine of the ten characters are the ones the escape sequences are made of -
    ``%``, ``.``, ``$``, ``2``, ``E``, ``4``, ``5`` - so a generated key is far
    more likely to collide with an escape than any realistic one. Two
    properties, and both matter:

    - **round trip**: decode(encode(x)) == x, for every key;
    - **injective**: two distinct keys never encode to one, which is the failure
      that would make ``parts`` lose a section rather than mangle it.
    """
    document = dict.fromkeys(keys, 1)
    encoded = encode_keys(document)
    assert len(encoded) == len(document), "two distinct keys encoded to one"
    assert decode_keys(encoded) == document


@given(key=KEY_ALPHABET)
def test_an_encoded_key_never_carries_a_dot_or_a_dollar(key: str) -> None:
    """The other half: the output is always something Mongo can address."""
    (encoded,) = encode_keys({key: 1})
    assert "." not in encoded and "$" not in encoded
