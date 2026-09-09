"""The three comparison helpers. **Pure functions, no network, no server.**

Ground rule 2: "the service never grades. It stores expectations and emits
evidence. There is no comparison logic in the server. The three comparison
helpers live in the Python client as pure functions." This module is that
sentence. It takes two documents - or one document and a JSON Schema - and
answers. It opens no socket, reads no clock, and knows nothing about a run.

`tests/unit/test_client_import_isolation.py` is the mechanical half of that
claim: it imports this module in a fresh interpreter and asserts the whole
transitive import set is a subset of what ``jsonschema`` alone imports. So a
future ``from agentprops_client.session import ...`` here fails a test rather
than quietly making a grading helper need a server.

The three modes, and where they come from
-----------------------------------------

PRD 5.2 defines them in one sentence each, and ``expected.comparison`` on the
dataset says which one the author meant: "``exact`` (deep equality), ``schema``
(validates against ``outcome_schema`` only), ``subset`` (every field in
``expected.final`` is present and equal in the actual, extra fields ignored)".
:func:`grade` dispatches on that stored value, which is the point of storing it
- "carrying the mode on the dataset means every consumer grades it the same way
instead of each inventing its own rule".

``semantic`` is deliberately absent: a judge is an LLM and neither the service
nor this client holds one.

The four decisions PRD 5.2's one-liners do not make
---------------------------------------------------

**Numbers compare across ``int``/``float``, and ``bool`` is not a number.**
JSON has one number type; ``1`` and ``1.0`` are the same JSON value and differ
only in how a decoder happened to spell them, so ``exact`` and ``subset`` treat
them as equal. ``True``, on the other hand, is a *different JSON type* from
``1`` - and Python's ``True == 1`` is the trap. `server/args.py` records the
same trap one layer out ("Booleans are not integers here") and
``validation.context.as_int`` excludes ``bool`` explicitly so DS-020 cannot
accept ``"seed": true``. A grader that called ``{"compliant": 1}`` a match for
``{"compliant": true}`` would pass an agent that returned the wrong type.

**``null`` is not absence.** ``subset`` requires an expected key to be
*present*: ``{"escalated_to": None}`` expects the key to exist and hold
``null``, and an actual without the key does **not** match. This is the case a
naive ``actual.get(key) == value`` gets wrong in the direction that looks like
success, so it is a row in the case table rather than a remark here.

**``subset`` recurses into objects and is positional over arrays.** A nested
object is subsetted - ``{"store": {"id": "ST-1"}}`` matches
``{"store": {"id": "ST-1", "name": "..."}}`` - because "extra fields ignored"
is about fields wherever they are, and a caller who wanted a whole-object match
has ``exact``. An **array**, by contrast, must have the same length, and
element *i* of the expected must subset element *i* of the actual. "Extra
fields ignored" is about object keys; an array is a value, and an agent that
assigned three training modules where two were expected got a different answer,
not a superset of the right one. Set-like containment was rejected for a second
reason as well: matching a multiset under subset semantics is a bipartite
matching problem, and a grader whose cost is not obvious from its contract is a
grader nobody trusts. ``DECISIONS.md`` carries the entry.

**``schema`` validates the actual and ignores the expected.** Its signature
takes an ``instance`` and a ``schema``, because that is what "validates against
``outcome_schema`` only" means - ``expected.final`` plays no part. :func:`grade`
therefore requires ``outcome_schema`` when the mode is ``schema`` and raises
``ValueError`` without one: that is a programming error in the caller, not a
failed comparison, and answering ``ok: False`` would report an agent's failure
where a harness has a bug.

Why the result is not a ``bool``
--------------------------------

:class:`Comparison` is truthy, so ``assert subset(expected, actual)`` reads the
way a caller wants. What it adds is *where* the two documents differ, as an RFC
6901 pointer per difference - the same pointer convention every finding in this
product uses (contracts section 1). A grader that answers only "no" makes a
developer diff two documents by eye, and the whole point of the product is that
the expectation was authored deliberately.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

__all__ = [
    "COMPARISON_MODES",
    "MODE_EXACT",
    "MODE_SCHEMA",
    "MODE_SUBSET",
    "Comparison",
    "Difference",
    "exact",
    "grade",
    "schema",
    "subset",
]

#: PRD 5.2's three phase-1 modes, and the values ``expected.comparison`` may
#: hold (DS-017's vocabulary). ``semantic`` is deliberately not among them.
MODE_EXACT: Final = "exact"
MODE_SCHEMA: Final = "schema"
MODE_SUBSET: Final = "subset"
COMPARISON_MODES: Final[frozenset[str]] = frozenset({MODE_EXACT, MODE_SCHEMA, MODE_SUBSET})

#: The pointer for a difference at the root of the two documents. RFC 6901
#: spells the whole document as the empty string, and every product surface
#: here uses RFC 6901 - so the root is ``""`` rather than ``"/"``, which would
#: mean the property named by the empty string.
ROOT: Final = ""


@dataclass(frozen=True, slots=True)
class Difference:
    """One place two documents disagree.

    ``pointer`` is RFC 6901 into the *expected* document's shape, which is also
    the actual's wherever both have the key. ``reason`` is a short machine-ish
    tag rather than a sentence, for the reason tests in this repo assert on rule
    ids and never on messages: a caller may branch on it.
    """

    pointer: str
    reason: str
    expected: Any = None
    actual: Any = None


@dataclass(frozen=True, slots=True)
class Comparison:
    """The answer, and what was wrong with it.

    Truthy when ``ok``, so ``assert exact(a, b)`` works, and iterable-free on
    purpose: a caller reads ``differences`` explicitly.
    """

    ok: bool
    mode: str
    differences: tuple[Difference, ...] = field(default=())

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        if self.ok:
            return f"{self.mode}: ok"
        parts = ", ".join(f"{item.pointer or '<root>'} {item.reason}" for item in self.differences)
        return f"{self.mode}: {len(self.differences)} difference(s) - {parts}"


def exact(expected: Any, actual: Any) -> Comparison:
    """PRD 5.2's ``exact``: deep equality of two JSON documents.

    Deep equality of *JSON* values, which differs from Python's ``==`` in the
    two places JSON and Python disagree - see the module docstring: a number
    equals a number of the other Python type, and ``bool`` equals only ``bool``.

    Key *order* is not part of it. Two objects with the same keys are equal
    whatever order a decoder produced them in, because JSON objects are
    unordered. (The byte-identity claims elsewhere in this product are about a
    *served fixture* round-tripping unchanged, which is a different property and
    is asserted with ``json.dumps``.)
    """
    return Comparison(
        ok=not (found := _exact_differences(expected, actual, ROOT)),
        mode=MODE_EXACT,
        differences=tuple(found),
    )


def subset(expected: Any, actual: Any) -> Comparison:
    """PRD 5.2's ``subset``: every field in ``expected`` present and equal in ``actual``.

    Recursive over objects, positional over arrays, and exact everywhere else.
    See the module docstring for why the array rule is length-plus-position
    rather than containment, and why an expected ``null`` needs the key to be
    there.

    ``subset({}, anything)`` is ``ok`` - an empty expectation asserts nothing.
    That is the correct answer and it is also a **vacuity hazard**: a dataset
    whose ``expected.final`` is empty passes every run. DS-021 requires
    ``expected.final`` to be a non-empty object, so the authoring flow is what
    prevents it; this function does not second-guess a document that reached the
    store (ground rule 7).
    """
    return Comparison(
        ok=not (found := _subset_differences(expected, actual, ROOT)),
        mode=MODE_SUBSET,
        differences=tuple(found),
    )


def schema(instance: Any, outcome_schema: Mapping[str, Any]) -> Comparison:
    """PRD 5.2's ``schema``: validate ``instance`` against ``outcome_schema``. Only that.

    ``expected.final`` plays no part - the mode's whole definition is "validates
    against ``outcome_schema`` only" - so the signature takes the schema rather
    than an expected document.

    **Draft 2020-12, pinned**, per CLAUDE.md's style rule and contracts 2.1: a
    blueprint's schemas are Draft 2020-12, and letting ``jsonschema`` infer a
    dialect from an absent ``$schema`` would make the answer depend on the
    installed library's default. Every failure is reported, not just the first,
    in the library's own deterministic order - so a caller sees the whole shape
    of the disagreement in one pass.

    Two things this deliberately does **not** do. It does not resolve a remote
    ``$ref``: the registry is empty, so a schema naming ``http://...`` reports
    an unresolvable reference instead of fetching it, which is what keeps this
    module's no-network claim true of its *behaviour* as well as its imports. And
    it does not check the schema itself for validity - a malformed schema raises
    ``jsonschema``'s own error, because that is an authoring defect the
    ``BP-*`` catalogue owns at write time, not a statement about the agent.
    """
    validator = Draft202012Validator(dict(outcome_schema))
    found = [_from_validation_error(error) for error in validator.iter_errors(instance)]
    return Comparison(ok=not found, mode=MODE_SCHEMA, differences=tuple(found))


def grade(
    mode: str,
    expected: Any,
    actual: Any,
    *,
    outcome_schema: Mapping[str, Any] | None = None,
) -> Comparison:
    """Apply the mode the **dataset** declared. ``expected.comparison``, dispatched.

    PRD 5.2: "``comparison`` declares intent, it does not trigger anything ...
    Whatever grades the run, a test runner, an eval platform, or a human, reads
    that bundle and applies the declared mode. Carrying the mode on the dataset
    means every consumer grades it the same way instead of each inventing its
    own rule." This is the function that makes that true for Python callers -
    hand it ``dataset["expected"]["comparison"]`` and stop choosing.

    ``ValueError`` for an unknown mode, and for ``schema`` with no
    ``outcome_schema``. Both are programming errors in the caller rather than
    verdicts about an agent, and reporting them as ``ok: False`` would say the
    agent failed when the harness is misconfigured. That is the same distinction
    `server/app.py::bound` draws for an unbound store.
    """
    if mode == MODE_EXACT:
        return exact(expected, actual)
    if mode == MODE_SUBSET:
        return subset(expected, actual)
    if mode == MODE_SCHEMA:
        if outcome_schema is None:
            raise ValueError(
                "grade(mode='schema') needs outcome_schema: the mode validates the actual "
                "against the blueprint's outcome_schema and ignores expected"
            )
        return schema(actual, outcome_schema)
    raise ValueError(
        f"unknown comparison mode {mode!r}; expected one of {sorted(COMPARISON_MODES)}"
    )


# ------------------------------------------------------------------ internals


def _exact_differences(expected: Any, actual: Any, pointer: str) -> list[Difference]:
    """Deep equality, as a list of disagreements rather than a bool.

    Objects and arrays recurse so the pointer names the *leaf* that differs; a
    top-level ``!=`` would report the whole document and tell a caller nothing
    it did not already know.
    """
    if _is_object(expected) and _is_object(actual):
        return _object_differences(expected, actual, pointer, _exact_differences, exact_keys=True)
    if _is_array(expected) and _is_array(actual):
        return _array_differences(expected, actual, pointer, _exact_differences)
    if _values_equal(expected, actual):
        return []
    return [Difference(pointer, _reason(expected, actual), expected, actual)]


def _subset_differences(expected: Any, actual: Any, pointer: str) -> list[Difference]:
    """``expected`` is a subset of ``actual``: recursive over objects, positional over arrays."""
    if _is_object(expected) and _is_object(actual):
        return _object_differences(expected, actual, pointer, _subset_differences, exact_keys=False)
    if _is_array(expected) and _is_array(actual):
        return _array_differences(expected, actual, pointer, _subset_differences)
    if _values_equal(expected, actual):
        return []
    return [Difference(pointer, _reason(expected, actual), expected, actual)]


def _object_differences(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    pointer: str,
    recurse: Any,
    *,
    exact_keys: bool,
) -> list[Difference]:
    """The one place the two modes actually differ, and it is ``exact_keys``.

    ``exact`` reports a key the actual has and the expected does not;
    ``subset`` ignores it. Everything else about an object comparison is shared,
    which is why this is one function with a flag rather than two that drift.

    A **missing** key is reported by both, and by ``subset`` in particular: this
    is where "``null`` is not absence" is decided, because the expected value is
    never consulted when the key is not there.
    """
    found: list[Difference] = []
    for key, value in expected.items():
        target = f"{pointer}/{_escape(key)}"
        if key not in actual:
            found.append(Difference(target, "missing", value, None))
            continue
        found += recurse(value, actual[key], target)
    if exact_keys:
        found += [
            Difference(f"{pointer}/{_escape(key)}", "unexpected", None, actual[key])
            for key in actual
            if key not in expected
        ]
    return found


def _array_differences(
    expected: Sequence[Any], actual: Sequence[Any], pointer: str, recurse: Any
) -> list[Difference]:
    """Same length, then element by element. See the module docstring for why.

    A length mismatch is reported at the array itself and the elements are
    **not** then compared: "the agent returned three where two were expected" is
    one finding, and a positional walk of mismatched lists would bury it under
    index-by-index noise.
    """
    if len(expected) != len(actual):
        return [Difference(pointer, "length", list(expected), list(actual))]
    found: list[Difference] = []
    for index, (want, got) in enumerate(zip(expected, actual, strict=True)):
        found += recurse(want, got, f"{pointer}/{index}")
    return found


def _values_equal(expected: Any, actual: Any) -> bool:
    """JSON equality for two scalars. The ``bool``/number trap lives here.

    ``isinstance(True, int)`` is true in Python, so a bare ``==`` makes
    ``True == 1`` and ``False == 0``. JSON has four scalar types and ``true`` is
    not ``1``, so the types are compared before the values - and only ``int``
    against ``float`` is allowed to cross.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected is actual
    if _is_number(expected) and _is_number(actual):
        return bool(expected == actual)
    if _json_type(expected) != _json_type(actual):
        return False
    return bool(expected == actual)


def _reason(expected: Any, actual: Any) -> str:
    """``type`` when the two JSON types differ, ``value`` when only the value does.

    Worth distinguishing: "the agent returned a string where a number belonged"
    and "the agent returned the wrong number" are different defects, and a
    caller that branches on ``reason`` wants to tell them apart.
    """
    return "value" if _json_type(expected) == _json_type(actual) else "type"


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if _is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if _is_array(value):
        return "array"
    if _is_object(value):
        return "object"
    return type(value).__name__


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_object(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_array(value: Any) -> bool:
    """A JSON array. ``str`` and ``bytes`` are sequences and are not arrays."""
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _escape(token: str) -> str:
    """RFC 6901 escaping: ``~`` becomes ``~0`` and ``/`` becomes ``~1``, in that order.

    In that order, and not the other: escaping ``/`` first would turn a literal
    ``~1`` in a key into an unescapable collision with an escaped slash. The
    service's `validation/pointers.py` makes the same call for the same reason.
    """
    return token.replace("~", "~0").replace("/", "~1")


def _from_validation_error(error: ValidationError) -> Difference:
    """One ``jsonschema`` finding as a :class:`Difference`.

    The pointer is built from ``absolute_path`` so it addresses the instance,
    which is what a caller comparing documents needs - ``json_path`` is
    JSONPath and ``schema_path`` points into the schema. ``reason`` is the
    failing keyword (``required``, ``additionalProperties``, ``type``, ``enum``),
    so a caller can branch on *why* without parsing prose.
    """
    pointer = "".join(f"/{_escape(str(part))}" for part in error.absolute_path)
    return Difference(
        pointer=pointer,
        reason=str(error.validator),
        expected=error.validator_value,
        actual=error.instance,
    )
