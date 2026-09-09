"""The three comparison helpers, against a table of cases including the tricky ones.

M8's second acceptance clause. The brief names two cases explicitly - **subset
with nested objects** and **schema with ``additionalProperties``** - because
those are the two where a naive implementation looks right and is wrong. The
table below carries both, plus the ones that answer the questions PRD 5.2's
one-line definitions leave open:

- **arrays**: is ``subset`` positional or set-like? Six rows decide it, and
  :func:`test_a_superset_array_is_not_a_subset` is the one that pins the
  decision *against* containment rather than merely for position.
- **``null`` versus absent**: ``{"a": None}`` against ``{}``. The naive
  ``actual.get(key) == expected[key]`` passes this, in the direction that looks
  like success.
- **numeric coercion**: ``1`` against ``1.0``, and ``True`` against ``1``. The
  first is the same JSON value spelled two ways; the second is Python's
  ``True == 1`` trap, which would let a grader pass an agent that returned the
  wrong type.
- **empty containers**: ``{}``, ``[]``, and the vacuity of ``subset({}, x)``.

The table is data, and every row carries the reason it exists
------------------------------------------------------------

:data:`CASES` is a tuple of :class:`Case`, parametrised into one test per row
with the row's ``why`` as the assertion message. So a failure names the property
that broke rather than an index, and adding a case is adding a row.

Two structural guards keep the table from rotting:
:func:`test_every_mode_is_covered` fails if a mode has no rows - which is what
would happen if ``schema`` were renamed - and
:func:`test_the_table_covers_both_answers_for_every_mode` fails if a mode has
only passing rows or only failing ones, because a helper that always answered
``ok`` would satisfy half a table and a helper that never did would satisfy the
other half.

And the cross-mode row
----------------------

:func:`test_the_same_documents_grade_differently_by_mode` is the product's
grading story in one assertion: one pair of documents, the golden blueprint's
own ``outcome_schema``, and three different verdicts. That is why
``expected.comparison`` is stored on the dataset - "carrying the mode on the
dataset means every consumer grades it the same way instead of each inventing
its own rule" (PRD 5.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pytest
from referencing.exceptions import Unresolvable

from agentprops_client import compare
from agentprops_client.compare import (
    COMPARISON_MODES,
    MODE_EXACT,
    MODE_SCHEMA,
    MODE_SUBSET,
    Comparison,
    exact,
    grade,
    schema,
    subset,
)
from conftest import load_document

GOLDEN_BLUEPRINT: Final = "blueprints/location-onboarding-1.0.0.json"
GOLDEN_DATASET: Final = "datasets/priya-missing-docs.json"

#: The golden blueprint's ``outcome_schema``, read off the fixture. It requires
#: ``onboarding_status`` and ``outstanding_tasks``, enumerates the first, bounds
#: the second at zero and sets ``additionalProperties: false`` - which is why it
#: is the right schema for the ``additionalProperties`` cases rather than a
#: hand-written one: the trap is in the *product's own* schema.
OUTCOME_SCHEMA: Final[dict[str, Any]] = load_document(GOLDEN_BLUEPRINT)["outcome_schema"]

#: `priya-missing-docs`'s ``expected.final`` and its declared mode.
EXPECTED_FINAL: Final[dict[str, Any]] = load_document(GOLDEN_DATASET)["expected"]["final"]
DECLARED_MODE: Final[str] = load_document(GOLDEN_DATASET)["expected"]["comparison"]


@dataclass(frozen=True)
class Case:
    """One row. ``schema`` mode reads ``expected`` as the JSON Schema."""

    name: str
    mode: str
    expected: Any
    actual: Any
    ok: bool
    why: str
    pointers: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def run(case: Case) -> Comparison:
    if case.mode == MODE_SCHEMA:
        return schema(case.actual, case.expected)
    return grade(case.mode, case.expected, case.actual)


CASES: Final[tuple[Case, ...]] = (
    # ---------------------------------------------------------------- exact
    Case(
        "exact-identical-nested",
        MODE_EXACT,
        {"store": {"id": "ST-4471", "tags": ["a", "b"]}},
        {"store": {"id": "ST-4471", "tags": ["a", "b"]}},
        True,
        "deep equality recurses through objects and arrays",
    ),
    Case(
        "exact-key-order",
        MODE_EXACT,
        {"a": 1, "b": 2},
        {"b": 2, "a": 1},
        True,
        "JSON objects are unordered, so key order is not part of equality",
    ),
    Case(
        "exact-extra-key",
        MODE_EXACT,
        {"onboarding_status": "complete"},
        {"onboarding_status": "complete", "notes": "extra"},
        False,
        "exact is not subset: an extra field in the actual is a difference",
        pointers=("/notes",),
        reasons=("unexpected",),
    ),
    Case(
        "exact-missing-key",
        MODE_EXACT,
        {"onboarding_status": "complete", "outstanding_tasks": 0},
        {"onboarding_status": "complete"},
        False,
        "a missing field is reported at the field, not at the document",
        pointers=("/outstanding_tasks",),
        reasons=("missing",),
    ),
    Case(
        "exact-int-vs-float",
        MODE_EXACT,
        {"outstanding_tasks": 1},
        {"outstanding_tasks": 1.0},
        True,
        "JSON has one number type; 1 and 1.0 differ only in how a decoder spelled them",
    ),
    Case(
        "exact-true-vs-one",
        MODE_EXACT,
        {"compliant": True},
        {"compliant": 1},
        False,
        "Python's True == 1 is the trap: boolean and number are different JSON types",
        pointers=("/compliant",),
        reasons=("type",),
    ),
    Case(
        "exact-false-vs-zero",
        MODE_EXACT,
        {"compliant": False},
        {"compliant": 0},
        False,
        "the other half of the same trap - False == 0 in Python",
        pointers=("/compliant",),
        reasons=("type",),
    ),
    Case(
        "exact-one-vs-true",
        MODE_EXACT,
        {"outstanding_tasks": 1},
        {"outstanding_tasks": True},
        False,
        "the trap in the other direction: a number expected, a boolean produced",
        pointers=("/outstanding_tasks",),
        reasons=("type",),
    ),
    Case(
        "exact-null-vs-absent",
        MODE_EXACT,
        {"escalated_to": None},
        {},
        False,
        "an expected null needs the key to exist; absence is not null",
        pointers=("/escalated_to",),
        reasons=("missing",),
    ),
    Case(
        "exact-null-vs-null",
        MODE_EXACT,
        {"escalated_to": None},
        {"escalated_to": None},
        True,
        "null equals null when the key is there",
    ),
    Case(
        "exact-null-vs-empty-string",
        MODE_EXACT,
        {"escalated_to": None},
        {"escalated_to": ""},
        False,
        "null and the empty string are different JSON types, and both are falsy in Python",
        pointers=("/escalated_to",),
        reasons=("type",),
    ),
    Case(
        "exact-empty-objects",
        MODE_EXACT,
        {},
        {},
        True,
        "two empty objects are equal - the empty-container base case",
    ),
    Case(
        "exact-empty-arrays",
        MODE_EXACT,
        {"findings": []},
        {"findings": []},
        True,
        "two empty arrays are equal",
    ),
    Case(
        "exact-empty-vs-nonempty-array",
        MODE_EXACT,
        {"findings": []},
        {"findings": ["late"]},
        False,
        "an empty array is not any array; the length is reported at the array",
        pointers=("/findings",),
        reasons=("length",),
    ),
    Case(
        "exact-array-order",
        MODE_EXACT,
        {"assigned_modules": ["pos-refresher", "local-compliance"]},
        {"assigned_modules": ["local-compliance", "pos-refresher"]},
        False,
        "JSON arrays are ordered, so a reordering is a difference",
        pointers=("/assigned_modules/0", "/assigned_modules/1"),
        reasons=("value", "value"),
    ),
    Case(
        "exact-object-vs-array",
        MODE_EXACT,
        {"store": {"id": "ST-1"}},
        {"store": [{"id": "ST-1"}]},
        False,
        "an object is not a one-element array",
        pointers=("/store",),
        reasons=("type",),
    ),
    Case(
        "exact-string-vs-number",
        MODE_EXACT,
        {"outstanding_tasks": "0"},
        {"outstanding_tasks": 0},
        False,
        "a stringified number is a type difference, which is worth telling apart from a value one",
        pointers=("/outstanding_tasks",),
        reasons=("type",),
    ),
    # --------------------------------------------------------------- subset
    Case(
        "subset-extra-top-level-field",
        MODE_SUBSET,
        {"onboarding_status": "complete"},
        {"onboarding_status": "complete", "outstanding_tasks": 0, "notes": "…"},
        True,
        "PRD 5.2: extra fields ignored",
    ),
    Case(
        "subset-nested-objects",
        MODE_SUBSET,
        {"store": {"id": "ST-4471"}},
        {"store": {"id": "ST-4471", "name": "Koramangala 2", "status": "docs_received"}},
        True,
        "THE NAMED CASE: subset recurses, so a nested extra is ignored too. An "
        "implementation comparing expected['store'] == actual['store'] fails here",
    ),
    Case(
        "subset-three-levels-deep",
        MODE_SUBSET,
        {"a": {"b": {"c": 1}}},
        {"a": {"b": {"c": 1, "d": 2}, "e": 3}, "f": 4},
        True,
        "the recursion is not one level deep - extras at every level are ignored",
    ),
    Case(
        "subset-nested-missing",
        MODE_SUBSET,
        {"store": {"id": "ST-4471", "status": "docs_received"}},
        {"store": {"id": "ST-4471"}},
        False,
        "a field missing inside a nested object is reported at its own pointer",
        pointers=("/store/status",),
        reasons=("missing",),
    ),
    Case(
        "subset-nested-differs",
        MODE_SUBSET,
        {"store": {"status": "docs_received"}},
        {"store": {"status": "pending", "name": "x"}},
        False,
        "recursing must still compare the leaf, not just find the key",
        pointers=("/store/status",),
        reasons=("value",),
    ),
    Case(
        "subset-empty-expectation",
        MODE_SUBSET,
        {},
        {"anything": True},
        True,
        "an empty expectation asserts nothing - correct, and the vacuity DS-021 prevents upstream",
    ),
    Case(
        "subset-null-vs-absent",
        MODE_SUBSET,
        {"escalated_to": None},
        {"onboarding_status": "complete"},
        False,
        "THE NAIVE FAILURE: actual.get('escalated_to') is None, which equals the expected None. "
        "Presence is required, so this must not match",
        pointers=("/escalated_to",),
        reasons=("missing",),
    ),
    Case(
        "subset-null-present",
        MODE_SUBSET,
        {"escalated_to": None},
        {"escalated_to": None, "onboarding_status": "complete"},
        True,
        "an expected null matches a present null",
    ),
    Case(
        "subset-null-vs-value",
        MODE_SUBSET,
        {"escalated_to": None},
        {"escalated_to": "ops@example.com"},
        False,
        "a present null is not a wildcard",
        pointers=("/escalated_to",),
        reasons=("type",),
    ),
    Case(
        "subset-array-elementwise",
        MODE_SUBSET,
        {"findings": [{"code": "late"}]},
        {"findings": [{"code": "late", "days": 4}]},
        True,
        "positional recursion is what makes subset useful for arrays of objects",
    ),
    Case(
        "subset-array-length",
        MODE_SUBSET,
        {"assigned_modules": ["pos-refresher", "local-compliance"]},
        {"assigned_modules": ["pos-refresher", "local-compliance", "extra-module"]},
        False,
        "THE ARRAY DECISION: an array is a value, not a bag of fields. Three modules where two "
        "were expected is a different answer, not a superset of the right one",
        pointers=("/assigned_modules",),
        reasons=("length",),
    ),
    Case(
        "subset-array-short",
        MODE_SUBSET,
        {"assigned_modules": ["pos-refresher", "local-compliance"]},
        {"assigned_modules": ["pos-refresher"]},
        False,
        "the other end of the length rule",
        pointers=("/assigned_modules",),
        reasons=("length",),
    ),
    Case(
        "subset-array-order",
        MODE_SUBSET,
        {"assigned_modules": ["pos-refresher", "local-compliance"]},
        {"assigned_modules": ["local-compliance", "pos-refresher"]},
        False,
        "positional means positional: a reordering is not a match",
        pointers=("/assigned_modules/0", "/assigned_modules/1"),
        reasons=("value", "value"),
    ),
    Case(
        "subset-array-empty",
        MODE_SUBSET,
        {"findings": []},
        {"findings": []},
        True,
        "an expected empty array matches an actual empty array",
    ),
    Case(
        "subset-array-empty-vs-filled",
        MODE_SUBSET,
        {"findings": []},
        {"findings": ["late"]},
        False,
        "an expected empty array is a claim that the array is empty",
        pointers=("/findings",),
        reasons=("length",),
    ),
    Case(
        "subset-int-vs-float",
        MODE_SUBSET,
        {"outstanding_tasks": 0},
        {"outstanding_tasks": 0.0, "notes": "…"},
        True,
        "the number rule is the same as exact's - subset only relaxes *fields*",
    ),
    Case(
        "subset-true-vs-one",
        MODE_SUBSET,
        {"compliant": True},
        {"compliant": 1, "notes": "…"},
        False,
        "the bool trap is not relaxed by subset either",
        pointers=("/compliant",),
        reasons=("type",),
    ),
    Case(
        "subset-scalar-root-equal",
        MODE_SUBSET,
        "complete",
        "complete",
        True,
        "a scalar expectation is just equality; subset is defined at every level",
    ),
    Case(
        "subset-scalar-root-differs",
        MODE_SUBSET,
        "complete",
        "escalated",
        False,
        "a difference at the root is reported at the RFC 6901 root, which is the empty pointer",
        pointers=("",),
        reasons=("value",),
    ),
    Case(
        "subset-object-vs-scalar",
        MODE_SUBSET,
        {"onboarding_status": "complete"},
        "complete",
        False,
        "an agent that returned a bare string where an object belonged has not produced a superset",
        pointers=("",),
        reasons=("type",),
    ),
    Case(
        "subset-pointer-escaping",
        MODE_SUBSET,
        {"a/b": 1, "c~d": 2},
        {"a/b": 9, "c~d": 9},
        False,
        "RFC 6901 escaping: / becomes ~1 and ~ becomes ~0, so a key cannot forge a segment",
        pointers=("/a~1b", "/c~0d"),
        reasons=("value", "value"),
    ),
    # --------------------------------------------------------------- schema
    Case(
        "schema-golden-outcome",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete", "outstanding_tasks": 0},
        True,
        "the golden blueprint's own outcome_schema accepts the golden expected.final",
    ),
    Case(
        "schema-additional-properties-false",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete", "outstanding_tasks": 0, "notes": "extra"},
        False,
        "THE NAMED CASE: the golden outcome_schema sets additionalProperties: false, so an extra "
        "key fails schema while passing subset",
        pointers=("",),
        reasons=("additionalProperties",),
    ),
    Case(
        "schema-additional-properties-default",
        MODE_SCHEMA,
        {"type": "object", "properties": {"onboarding_status": {"type": "string"}}},
        {"onboarding_status": "complete", "notes": "extra"},
        True,
        "additionalProperties defaults to true, so the same instance passes a schema without it",
    ),
    Case(
        "schema-additional-properties-under-allof",
        MODE_SCHEMA,
        {
            "type": "object",
            "allOf": [{"properties": {"onboarding_status": {"type": "string"}}}],
            "additionalProperties": False,
        },
        {"onboarding_status": "complete"},
        False,
        "the classic additionalProperties trap: it does not see properties declared in a sibling "
        "allOf, so this rejects everything. Delegating to jsonschema means we inherit the "
        "specified behaviour rather than a friendlier wrong one",
        pointers=("",),
        reasons=("additionalProperties",),
    ),
    Case(
        "schema-required",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete"},
        False,
        "a missing required property is reported as `required`, so a caller can branch on why",
        pointers=("",),
        reasons=("required",),
    ),
    Case(
        "schema-enum",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "vibes", "outstanding_tasks": 0},
        False,
        "the enum on onboarding_status, reported at the offending property",
        pointers=("/onboarding_status",),
        reasons=("enum",),
    ),
    Case(
        "schema-integer-accepts-a-whole-float",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete", "outstanding_tasks": 0.0},
        True,
        "JSON Schema says a number with a zero fractional part IS an integer - so schema mode is "
        "*more* permissive here than a Python type check, and matching exact's number rule was "
        "not a coincidence to be relied on",
    ),
    Case(
        "schema-integer-rejects-a-fraction",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete", "outstanding_tasks": 0.5},
        False,
        "the other end of the same rule",
        pointers=("/outstanding_tasks",),
        reasons=("type",),
    ),
    Case(
        "schema-integer-rejects-a-boolean",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete", "outstanding_tasks": True},
        False,
        "jsonschema excludes bool from integer, which is the same trap exact handles by hand",
        pointers=("/outstanding_tasks",),
        reasons=("type",),
    ),
    Case(
        "schema-minimum",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete", "outstanding_tasks": -1},
        False,
        "outstanding_tasks has minimum: 0",
        pointers=("/outstanding_tasks",),
        reasons=("minimum",),
    ),
    Case(
        "schema-nested-array-items",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {"onboarding_status": "complete", "outstanding_tasks": 0, "assigned_modules": [1]},
        False,
        "the pointer addresses the failing array element, not the array",
        pointers=("/assigned_modules/0",),
        reasons=("type",),
    ),
    Case(
        "schema-empty-schema",
        MODE_SCHEMA,
        {},
        {"anything": [1, None, True]},
        True,
        "the empty schema accepts everything - schema mode's own vacuity case",
    ),
    Case(
        "schema-empty-instance",
        MODE_SCHEMA,
        OUTCOME_SCHEMA,
        {},
        False,
        "an empty object fails both required properties, and both are reported",
        pointers=("", ""),
        reasons=("required", "required"),
    ),
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_a_comparison_case(case: Case) -> None:
    """One row of the table. The row's ``why`` is the failure message."""
    result = run(case)
    assert result.ok is case.ok, f"{case.name}: {case.why} (got {result})"
    assert bool(result) is case.ok, "Comparison must be truthy exactly when ok"
    assert result.mode == case.mode
    if case.pointers:
        assert tuple(item.pointer for item in result.differences) == case.pointers, case.why
    if case.reasons:
        assert tuple(item.reason for item in result.differences) == case.reasons, case.why
    if case.ok:
        assert result.differences == (), "an ok comparison reports no differences"
    else:
        assert result.differences, "a failed comparison that names nothing says nothing"


# ------------------------------------------------------- the table's own guards


def test_the_table_is_not_empty_and_has_unique_names() -> None:
    """Every test below is vacuous without this, and a duplicated id hides a row."""
    assert len(CASES) >= 40, f"the case table has shrunk to {len(CASES)} rows"
    names = [case.name for case in CASES]
    assert len(names) == len(set(names)), "two rows share a name, so one of them is unreachable"


def test_every_mode_is_covered() -> None:
    """A mode with no rows is a mode nothing tests. Enumerated from the module."""
    covered = {case.mode for case in CASES}
    assert covered == COMPARISON_MODES, (
        f"these comparison modes have no cases: {sorted(COMPARISON_MODES - covered)}"
    )


def test_the_table_covers_both_answers_for_every_mode() -> None:
    """Both directions, per mode. Half a table passes against a constant helper.

    A ``subset`` that always returned ``ok`` would satisfy every passing row and
    one that never did would satisfy every failing row, so a mode with rows in
    only one direction is a mode whose helper could be a constant.
    """
    for mode in sorted(COMPARISON_MODES):
        answers = {case.ok for case in CASES if case.mode == mode}
        assert answers == {True, False}, f"{mode} has cases in only one direction"


def test_the_named_tricky_cases_are_present() -> None:
    """The two the brief names, by id, so neither can be quietly dropped.

    A rename is fine; a deletion is not, and without this the table could lose
    the nested-object row and stay green.
    """
    names = {case.name for case in CASES}
    assert "subset-nested-objects" in names
    assert "schema-additional-properties-false" in names
    assert "subset-null-vs-absent" in names
    assert "subset-array-length" in names


# -------------------------------------------------- properties, not table rows


def test_a_superset_array_is_not_a_subset() -> None:
    """The row that pins the array decision *against* set-like containment.

    Every expected element is present in the actual, in order, with one extra
    appended - so a containment implementation answers ``ok`` and a positional
    one does not. The table's length rows would pass against *both*; this one
    only passes against the decision that was actually made, which is what makes
    it worth stating separately.
    """
    expected = {"assigned_modules": ["pos-refresher", "local-compliance-bengaluru"]}
    actual = {"assigned_modules": ["pos-refresher", "local-compliance-bengaluru", "food-safety"]}
    result = subset(expected, actual)
    assert not result.ok, "subset over arrays is positional, not containment"
    assert result.differences[0].reason == "length"
    assert result.differences[0].expected == expected["assigned_modules"]
    assert result.differences[0].actual == actual["assigned_modules"]


def test_a_length_mismatch_reports_one_difference_not_a_diff_per_index() -> None:
    """One finding at the array, and the elements are not then walked.

    "Three where two were expected" is one fact. A positional walk of
    mismatched lists would bury it under index-by-index noise, and a caller
    reading the first difference would be told about an element rather than
    about the length.
    """
    result = subset({"xs": [1, 2]}, {"xs": [9, 9, 9]})
    assert [item.pointer for item in result.differences] == ["/xs"]


def test_subset_reports_every_difference_not_only_the_first() -> None:
    """A grader that stops at the first mismatch makes a developer iterate."""
    result = subset(
        {"onboarding_status": "complete", "outstanding_tasks": 0, "escalated_to": None},
        {"onboarding_status": "escalated", "outstanding_tasks": 3},
    )
    assert [item.pointer for item in result.differences] == [
        "/onboarding_status",
        "/outstanding_tasks",
        "/escalated_to",
    ]
    assert [item.reason for item in result.differences] == ["value", "value", "missing"]


def test_exact_reports_missing_and_unexpected_together() -> None:
    """Both halves of an exact key-set difference, in one answer."""
    result = exact({"a": 1, "b": 2}, {"a": 1, "c": 3})
    assert {(item.pointer, item.reason) for item in result.differences} == {
        ("/b", "missing"),
        ("/c", "unexpected"),
    }


def test_a_difference_carries_both_documents_at_the_pointer() -> None:
    """``expected`` and ``actual`` on the finding, so a caller need not re-walk."""
    result = subset({"store": {"status": "docs_received"}}, {"store": {"status": "pending"}})
    difference = result.differences[0]
    assert difference.pointer == "/store/status"
    assert difference.expected == "docs_received"
    assert difference.actual == "pending"


def test_schema_reports_the_failing_keyword_as_the_reason() -> None:
    """``reason`` is the JSON Schema keyword, so a caller can branch without prose."""
    result = schema({"onboarding_status": "vibes"}, OUTCOME_SCHEMA)
    assert sorted(item.reason for item in result.differences) == ["enum", "required"]


def test_schema_does_not_fetch_a_remote_ref() -> None:
    """The no-network claim as *behaviour*, not only as an import graph.

    A schema naming ``https://...`` is handed an empty reference registry, so
    the reference is unresolvable rather than fetched. If this ever started
    passing, ``schema`` would be making an outbound request from a function
    documented as pure - and the import guard would not catch it, because
    ``urllib`` would already be in ``jsonschema``'s own graph.
    """
    with pytest.raises(Unresolvable):
        schema({}, {"$ref": "https://example.invalid/outcome.json"})


def test_the_helpers_do_not_mutate_their_arguments() -> None:
    """Pure means pure. A grader that normalised in place would corrupt the evidence."""
    expected = {"store": {"id": "ST-1"}, "xs": [1, 2]}
    actual = {"store": {"id": "ST-1", "name": "x"}, "xs": [1, 2], "extra": True}
    before = (repr(expected), repr(actual))
    assert subset(expected, actual).ok
    assert not exact(expected, actual).ok
    assert (repr(expected), repr(actual)) == before


# -------------------------------------------------------------------- grade


def test_the_same_documents_grade_differently_by_mode() -> None:
    """The product's grading story in one assertion, on the golden documents.

    One actual - ``expected.final`` plus a field the agent added - and three
    verdicts: ``subset`` passes because extra fields are ignored, ``exact``
    fails because they are not, and ``schema`` fails because the golden
    ``outcome_schema`` sets ``additionalProperties: false``. That divergence is
    exactly why ``expected.comparison`` is authored on the dataset instead of
    each consumer choosing.
    """
    actual = {**EXPECTED_FINAL, "notes": "the agent added a field"}

    assert grade(MODE_SUBSET, EXPECTED_FINAL, actual).ok
    assert not grade(MODE_EXACT, EXPECTED_FINAL, actual).ok
    assert not grade(MODE_SCHEMA, EXPECTED_FINAL, actual, outcome_schema=OUTCOME_SCHEMA).ok


def test_grade_dispatches_on_the_mode_the_dataset_declared() -> None:
    """``expected.comparison`` read off the fixture and handed straight to ``grade``.

    The dataset says ``subset``, so this is the call the M8 script's step 9
    makes - and the mode is read from the document rather than written here, so
    a fixture that changed its declared mode would change what this asserts.
    """
    assert DECLARED_MODE == MODE_SUBSET, "the golden dataset's declared mode moved"
    generous = {**EXPECTED_FINAL, "audit_log_id": "AL-9"}
    assert grade(DECLARED_MODE, EXPECTED_FINAL, generous).ok
    assert grade(DECLARED_MODE, EXPECTED_FINAL, generous).mode == MODE_SUBSET


def test_grade_refuses_an_unknown_mode() -> None:
    """A programming error in the caller, not a verdict about an agent.

    ``ok: False`` would say the agent failed when the harness is misconfigured -
    the same distinction ``server/app.py::bound`` draws for an unbound store.
    """
    with pytest.raises(ValueError, match="unknown comparison mode"):
        grade("vibes", {}, {})
    with pytest.raises(ValueError, match="semantic"):
        grade("semantic", {}, {})


def test_grade_in_schema_mode_needs_the_outcome_schema() -> None:
    """The mode validates against ``outcome_schema``; without one there is nothing to do."""
    with pytest.raises(ValueError, match="outcome_schema"):
        grade(MODE_SCHEMA, EXPECTED_FINAL, EXPECTED_FINAL)


def test_grade_in_schema_mode_ignores_the_expected_document() -> None:
    """ "Validates against ``outcome_schema`` **only**" - so ``expected`` plays no part.

    The same instance and schema, with two unrelated expected documents, must
    give the identical answer. Without this, a ``schema`` mode that quietly also
    compared the two documents would pass every other test in this file.
    """
    instance = {"onboarding_status": "complete", "outstanding_tasks": 0}
    first = grade(MODE_SCHEMA, {"nothing": "like it"}, instance, outcome_schema=OUTCOME_SCHEMA)
    second = grade(MODE_SCHEMA, instance, instance, outcome_schema=OUTCOME_SCHEMA)
    assert first == second and first.ok


def test_the_mode_constants_are_the_documented_vocabulary() -> None:
    """DS-017's vocabulary, and ``semantic`` deliberately absent (PRD 5.2)."""
    assert set(COMPARISON_MODES) == {"exact", "schema", "subset"}
    assert "semantic" not in COMPARISON_MODES
    assert compare.MODE_EXACT == "exact"


def test_a_comparison_prints_where_it_disagreed() -> None:
    """``__str__`` is what a caller puts in an assertion message.

    Not asserted on for *content* anywhere else - the repo's rule is to assert
    on ids and shapes, never on prose - but a result whose repr said only
    "False" would make every failing grade a guessing game.
    """
    ok = subset({"a": 1}, {"a": 1, "b": 2})
    assert str(ok) == "subset: ok"
    bad = str(subset({"a": 1, "c": None}, {"a": 2}))
    assert "/a value" in bad and "/c missing" in bad and "2 difference" in bad
    assert str(subset("x", "y")).count("<root>") == 1, "the RFC 6901 root prints readably"
