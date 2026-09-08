"""Ruling R-04's coverage, made visible in one place.

R-04 names eleven rule ids that a Pydantic constraint would swallow: DS-017,
DS-020, DS-025, DS-026, DS-028, DS-029, DS-030, BP-001, BP-002, BP-008 and
BP-017. If a model enforces what one of those rules owns, the document never
reaches the validator, no rule id is emitted, and M2's gate fails on that case.

Coverage comes from three tables, and
:func:`test_ruling_r04_ids_are_all_covered` asserts their union is the whole
eleven, so a new ruling is added to a table rather than discovered missing by
someone else's suite:

1. `tests/fixtures/broken/manifest.json` - the 28 shipped corpus cases. It
   reaches seven of the eleven. It is deliberately incomplete: R-14 quotes
   `worked-example.md` calling it "a starting set, not the complete one", so
   depending on it alone is what let DS-020 hide.
2. :data:`EXTRA_PARSE_CASES` - hand-written, keyed by rule id, covering the ids
   the corpus does not reach.

:data:`STRICT_BY_DESIGN_CASES` is the third table and asserts the *opposite*:
these mutations must raise, because ruling R-23 requires strict numeric typing
so that lax coercion can never silently rewrite a stored value. That is not an
R-04 violation, because R-23 also settles that the validator runs against the
raw ``dict`` off ``json.loads`` before any model is constructed - so DS-020 and
BP-008's "is an integer" half fire there, with real corpus cases, and the strict
annotation is the defence-in-depth second layer.

Every parse case must also round-trip, since M2 reads pointers off the original
document and M7 needs export bytes to be stable.

Failures here are *this* package's bug, not the corpus's. A case that will not
parse means a model is enforcing something the catalogue owns.

The patch applier below is a deliberate minimum: the four RFC 6902 operations
these tables use, and nothing more. M2 builds the real corpus loader;
duplicating thirty lines is cheaper than either package importing the other's
test helpers.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from agentprops.models import Blueprint, Dataset
from conftest import FIXTURES_DIR

MANIFEST = json.loads((FIXTURES_DIR / "broken" / "manifest.json").read_text(encoding="utf-8"))

MODELS = {"blueprint": Blueprint, "dataset": Dataset}
BASES = {
    "blueprint": MANIFEST["base_blueprint"],
    "dataset": MANIFEST["base_dataset"],
}

CASES = [(case["id"], case) for case in MANIFEST["cases"]]

#: The rule ids ruling R-04 names as swallowable by a model constraint.
R04_RULE_IDS = frozenset(
    {
        "BP-001",  # agent_id pattern
        "BP-002",  # version is semver
        "BP-008",  # loop nodes declare max_iterations as an integer > 0
        "BP-017",  # kind: loop implies pool: true
        "DS-017",  # comparison is one of exact | schema | subset
        "DS-020",  # seed is an integer
        "DS-025",  # title present, non-whitespace, <= 120 chars
        "DS-026",  # intent present, >= 30 chars
        "DS-028",  # author.name present and non-whitespace
        "DS-029",  # author.handle pattern
        "DS-030",  # author.agent vocabulary
    }
)

#: Rule ids the shipped corpus reaches, read off its own `expect` lists rather
#: than from the case names, so a renamed case cannot fake coverage.
CORPUS_RULE_IDS = frozenset(rule for case in MANIFEST["cases"] for rule in case["expect"])

#: Hand-written cases for the R-04 ids `manifest.json` does not reach. Each must
#: parse: the catalogue owns the check, so the model must not raise first.
#: `(rule_id, target, mutations)`.
EXTRA_PARSE_CASES: list[tuple[str, str, list[dict[str, Any]]]] = [
    # DS-017: `comparison` is a plain `str`, never a Literal.
    ("DS-017", "dataset", [{"op": "replace", "path": "/expected/comparison", "value": "fuzzy"}]),
    # BP-002: `version` is a plain `str`, with no semver validation.
    ("BP-002", "blueprint", [{"op": "replace", "path": "/version", "value": "1.0"}]),
    # BP-008: `max_iterations` carries no `gt=0`, so the "> 0" half is the
    # catalogue's. Node 3 is `request_docs`, the loop node.
    ("BP-008", "blueprint", [{"op": "replace", "path": "/nodes/3/max_iterations", "value": 0}]),
    ("BP-008", "blueprint", [{"op": "replace", "path": "/nodes/3/max_iterations", "value": -1}]),
    # BP-017 again, spelled as `replace` rather than `remove`, because `pool` is
    # a required field: a `remove` mutation would raise instead of firing the
    # rule. Recorded here so the constraint on M2's corpus is visible.
    ("BP-017", "blueprint", [{"op": "replace", "path": "/nodes/3/pool", "value": False}]),
    # DS-025 with a title over 120 characters - the shipped case only covers the
    # whitespace half.
    ("DS-025", "dataset", [{"op": "replace", "path": "/provenance/title", "value": "x" * 400}]),
]

#: Cases that must RAISE, and are correct to raise, per ruling R-23. The
#: catalogue still owns each of these checks; it applies them to the raw
#: document before a model exists. `(rule_id, target, mutations)`.
STRICT_BY_DESIGN_CASES: list[tuple[str, str, list[dict[str, Any]]]] = [
    # DS-020: `seed` is `StrictInt`. Lax `int` would rewrite "42" to 42 and
    # `true` to 1 - a document that parses and then fails R-08's round trip.
    # DS-020 is implemented against the raw dict at M2 and gets a real corpus
    # case (`{"op": "replace", "path": "/seed", "value": "not-a-number"}`).
    ("DS-020", "dataset", [{"op": "replace", "path": "/seed", "value": "not-a-number"}]),
    ("DS-020", "dataset", [{"op": "replace", "path": "/seed", "value": 3.7}]),
    ("DS-020", "dataset", [{"op": "replace", "path": "/seed", "value": True}]),
    ("DS-020", "dataset", [{"op": "replace", "path": "/seed", "value": None}]),
    # BP-008's "is an integer" half, same reasoning. Its "> 0" half is in
    # EXTRA_PARSE_CASES above and does parse.
    ("BP-008", "blueprint", [{"op": "replace", "path": "/nodes/3/max_iterations", "value": "3"}]),
    # BP-017: `pool` is `StrictBool`, so 0/1 are not booleans.
    ("BP-017", "blueprint", [{"op": "replace", "path": "/nodes/3/pool", "value": 0}]),
]


def unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def resolve(document: Any, pointer: str) -> tuple[Any, str | int]:
    """Return ``(container, key)`` for an RFC 6901 pointer's final segment."""
    tokens = [unescape(t) for t in pointer.split("/")[1:]]
    container = document
    for token in tokens[:-1]:
        container = container[int(token)] if isinstance(container, list) else container[token]
    last = tokens[-1]
    if isinstance(container, list):
        return container, len(container) if last == "-" else int(last)
    return container, last


def read(document: Any, pointer: str) -> Any:
    container, key = resolve(document, pointer)
    return container[key]


def apply_operation(document: Any, operation: dict[str, Any]) -> None:
    op = operation["op"]
    value = read(document, operation["from"]) if op == "copy" else operation.get("value")
    container, key = resolve(document, operation["path"])
    if op == "remove":
        del container[key]
    elif op == "add" and isinstance(container, list):
        container.insert(int(key), copy.deepcopy(value))
    else:
        container[key] = copy.deepcopy(value)


def mutated(case: dict[str, Any]) -> tuple[type[Blueprint] | type[Dataset], dict[str, Any]]:
    target = case["target"]
    base = json.loads((FIXTURES_DIR / BASES[target]).read_text(encoding="utf-8"))
    for operation in case["mutate"]:
        apply_operation(base, operation)
    return MODELS[target], base


def test_the_corpus_manifest_is_not_empty() -> None:
    assert len(CASES) >= 28, f"expected the shipped corpus, found {len(CASES)} cases"


@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _ in CASES])
def test_broken_case_still_parses(case_id: str, case: dict[str, Any]) -> None:
    """No mutation may raise ``ValidationError``. Every one must reach the rules."""
    model, document = mutated(case)
    parsed = model.model_validate(document)
    assert parsed is not None, case_id


@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _ in CASES])
def test_broken_case_still_round_trips(case_id: str, case: dict[str, Any]) -> None:
    """And no mutation may be silently normalised away on the way back out."""
    model, document = mutated(case)
    dumped = model.model_validate(document).model_dump(mode="json", exclude_unset=True)
    assert dumped == document, case_id


def test_the_mutations_actually_changed_something() -> None:
    """Guard against a patch applier that quietly no-ops.

    ``DS-008-constant-entity-drift`` is a known exception: ruling R-14 records
    that its first operation replaces a value with itself, and one of its two
    operations is therefore a genuine no-op. The case as a whole still changes
    the document, which is all this asserts.
    """
    unchanged: list[str] = []
    for case_id, case in CASES:
        target = case["target"]
        base = json.loads((FIXTURES_DIR / BASES[target]).read_text(encoding="utf-8"))
        _, after = mutated(case)
        if after == base:
            unchanged.append(case_id)
    assert not unchanged, f"these cases mutate nothing, so they prove nothing: {unchanged}"


# --------------------------------------------------------------------------- #
# The hand-written tables. See the module docstring.
# --------------------------------------------------------------------------- #


def table_id(entry: tuple[str, str, list[dict[str, Any]]]) -> str:
    rule, target, mutations = entry
    pointer = mutations[0]["path"]
    value = mutations[0].get("value")
    shown = f"{value!r}"[:24]
    return f"{rule}-{target}{pointer.replace('/', '.')}={shown}"


@pytest.mark.parametrize("entry", EXTRA_PARSE_CASES, ids=table_id)
def test_extra_r04_case_still_parses_and_round_trips(
    entry: tuple[str, str, list[dict[str, Any]]],
) -> None:
    """The catalogue owns these checks, so the model must not raise first."""
    rule, target, mutations = entry
    model, document = mutated({"target": target, "mutate": mutations})
    dumped = model.model_validate(document).model_dump(mode="json", exclude_unset=True)
    assert dumped == document, f"{rule}: parsed but did not round-trip"


@pytest.mark.parametrize("entry", STRICT_BY_DESIGN_CASES, ids=table_id)
def test_strict_by_design_case_raises(entry: tuple[str, str, list[dict[str, Any]]]) -> None:
    """Ruling R-23: strict numerics, so coercion never rewrites a stored value.

    These raise on purpose. The rule id in each entry still fires at M2, against
    the raw ``dict`` before any model exists, so nothing is lost - and a value
    that lax Pydantic would have silently rewritten cannot reach the store.
    """
    rule, target, mutations = entry
    model, document = mutated({"target": target, "mutate": mutations})
    with pytest.raises(ValidationError):
        model.model_validate(document)
    assert rule in R04_RULE_IDS


def test_no_strict_by_design_case_is_silently_coerced() -> None:
    """The failure mode R-23 actually forbids: parses, then stops round-tripping.

    Before the R-23 hardening, ``seed: "42"`` and ``seed: true`` parsed and were
    rewritten to ``42`` and ``1``. Neither may now parse at all - and if either
    ever parses again, it must at least still round-trip.
    """
    coerced: list[str] = []
    for value in ("42", True, 3.0):
        model, document = mutated(
            {"target": "dataset", "mutate": [{"op": "replace", "path": "/seed", "value": value}]}
        )
        try:
            dumped = model.model_validate(document).model_dump(mode="json", exclude_unset=True)
        except ValidationError:
            continue
        if dumped != document:
            coerced.append(f"seed={value!r} parsed as {dumped['seed']!r}")
    assert not coerced, f"lax coercion broke the round trip (ruling R-23): {coerced}"


def test_ruling_r04_ids_are_all_covered() -> None:
    """Every id R-04 names is exercised by one of the three tables.

    This is the test that would have caught the original gap: the shipped corpus
    reaches only seven of the eleven, and nothing said so.
    """
    from_extra = {rule for rule, _, _ in EXTRA_PARSE_CASES}
    from_strict = {rule for rule, _, _ in STRICT_BY_DESIGN_CASES}
    covered = CORPUS_RULE_IDS | from_extra | from_strict
    missing = R04_RULE_IDS - covered
    assert not missing, (
        f"ruling R-04 names these ids but no table exercises them: {sorted(missing)}. "
        "Add a case to EXTRA_PARSE_CASES (must parse) or STRICT_BY_DESIGN_CASES (must raise)."
    )


def test_the_corpus_alone_does_not_cover_ruling_r04() -> None:
    """Pins *why* the hand-written tables exist, so nobody deletes them.

    If a future corpus extension (R-14) does reach all eleven ids, this test
    fails and should be deleted along with the redundant table entries - which
    is the right way to find that out.
    """
    uncovered = R04_RULE_IDS - CORPUS_RULE_IDS
    assert sorted(uncovered) == ["BP-002", "BP-008", "DS-017", "DS-020"]
