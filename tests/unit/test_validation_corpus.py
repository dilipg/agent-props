"""The gate: every corpus case reports exactly its declared rule ids.

This is M2's acceptance criterion, table-driven from
`tests/fixtures/broken/manifest.json`. Three disciplines from
`docs/worked-example.md` section 6 and ruling R-13 are enforced here and are
worth stating, because each one closes a way a broken validator could pass:

**Exact sets, not membership.** ``assert reported == declared``, never ``in``. A
validator that stops at the first error passes a membership check and fails
this - which is exactly what ``MULTI-two-errors`` exists to catch.

**Rule ids, never messages.** Messages get reworded; nothing here reads
``message``. What *is* asserted beyond the id set is the envelope shape: a
severity from the two legal values, and an RFC 6901 pointer that resolves in the
submitted document.

**Warnings do not reject** (ruling R-13). A case whose declared rules are all
warning-severity must report ``ok is True`` *and* the warning. Asserting only
the rule id would pass even if the validator wrongly rejected the document.

**Whatever the validator accepts must parse.**
:func:`test_a_case_the_validator_accepts_also_parses` is the general form of the
defect M2's review found in ruling R-07's fault handling: a clean envelope
followed by a ``ValidationError`` is an exception where a rule id belongs, and
under R-23's validate-then-parse ordering it lands in `service/`.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentprops.models import Blueprint, Dataset
from agentprops.models.errors import ERROR_ENVELOPE_SEVERITIES, SEVERITY_ERROR, RuleError
from agentprops.validation import (
    RULE_REGISTRY,
    WARNING_RULES,
    parse_with_duplicate_keys,
    validate_blueprint,
    validate_dataset,
)
from agentprops.validation.pointers import SECTIONS
from corpus import (
    BLUEPRINT_RESOLVERS,
    CASE_IDS,
    CASES,
    DATASET_RESOLVER,
    GOLDEN_BLUEPRINT,
    GOLDEN_DATASET_PATHS,
    RAW_TEXT_CASE_IDS,
    RAW_TEXT_CASES,
    mutated,
    raw_text,
    resolve,
)

MODELS: dict[str, type[Blueprint] | type[Dataset]] = {"blueprint": Blueprint, "dataset": Dataset}


def run_case(case: dict[str, Any]) -> tuple[bool, list[RuleError]]:
    """Validate one mutation case, choosing the entry point and resolver for it."""
    document = mutated(case)
    if case["target"] == "blueprint":
        stub = BLUEPRINT_RESOLVERS[case.get("resolver", "default")]
        envelope = validate_blueprint(document, stub)
    else:
        envelope = validate_dataset(document, DATASET_RESOLVER)
    return envelope.ok, envelope.errors


def run_raw_text_case(case: dict[str, Any]) -> tuple[bool, list[RuleError]]:
    """Validate one raw-text case through the tool boundary's duplicate-key parse."""
    assert case["target"] == "dataset", (
        f"{case['id']}: raw-text cases go through validate_dataset; a blueprint target would be "
        "silently validated as a dataset"
    )
    document, duplicates = parse_with_duplicate_keys(raw_text(case))
    envelope = validate_dataset(document, DATASET_RESOLVER, duplicate_keys=duplicates)
    return envelope.ok, envelope.errors


def declared_ok(case: dict[str, Any]) -> bool:
    """What ``ok`` must be, given the case's declared rules (ruling R-13)."""
    return all(rule in WARNING_RULES for rule in case["expect"])


# --------------------------------------------------------------------------- #
# The valid fixtures pass clean.
# --------------------------------------------------------------------------- #


def test_golden_blueprint_validates_clean() -> None:
    envelope = validate_blueprint(GOLDEN_BLUEPRINT)
    assert [error.rule for error in envelope.errors] == []
    assert envelope.ok is True


@pytest.mark.parametrize("path", GOLDEN_DATASET_PATHS, ids=lambda p: p.stem)
def test_golden_dataset_validates_clean(path: Any) -> None:
    """Both golden datasets, including the one M0 hand-authored from prose.

    `priya-missing-docs.json` is the byte-exact fixture from the spec, so a
    failure here means the rule is wrong - rulings R-01 and R-02 exist because
    two rules as written reject it. `arun-escalated.json` had never been
    machine-checked against any rule before this test.
    """
    document, duplicates = parse_with_duplicate_keys(path.read_text(encoding="utf-8"))
    envelope = validate_dataset(document, DATASET_RESOLVER, duplicate_keys=duplicates)
    assert [(error.rule, error.pointer) for error in envelope.errors] == []
    assert envelope.ok is True


# --------------------------------------------------------------------------- #
# The corpus, case by case.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_reports_exactly_its_declared_rules(case: dict[str, Any]) -> None:
    ok, errors = run_case(case)
    reported = {error.rule for error in errors}
    assert reported == set(case["expect"]), (
        f"{case['id']}: declared {sorted(case['expect'])}, reported {sorted(reported)}"
    )
    assert ok is declared_ok(case), f"{case['id']}: ok should be {declared_ok(case)}"


@pytest.mark.parametrize("case", RAW_TEXT_CASES, ids=RAW_TEXT_CASE_IDS)
def test_raw_text_case_reports_exactly_its_declared_rules(case: dict[str, Any]) -> None:
    ok, errors = run_raw_text_case(case)
    reported = {error.rule for error in errors}
    assert reported == set(case["expect"]), (
        f"{case['id']}: declared {sorted(case['expect'])}, reported {sorted(reported)}"
    )
    assert ok is declared_ok(case)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_findings_carry_a_usable_envelope(case: dict[str, Any]) -> None:
    """Severity, pointer and section, per `docs/contracts.md` section 1.

    The pointer must resolve *in the submitted document*, which is the property
    ruling R-23 chose raw-document validation to preserve. A pointer to a
    container the rule reports as missing (``/nodes`` for DS-002) still
    resolves; a pointer at a key that is genuinely absent is checked only as far
    as its parent.
    """
    _, errors = run_case(case)
    document = mutated(case)
    for error in errors:
        assert error.severity in ERROR_ENVELOPE_SEVERITIES
        assert (error.severity == SEVERITY_ERROR) is (error.rule not in WARNING_RULES)
        assert error.pointer.startswith("/"), f"{case['id']}: {error.rule} has no pointer"
        container, _ = resolve(document, error.pointer)
        assert container is not None
        if case["target"] == "dataset":
            assert error.section is None or error.section in SECTIONS
        else:
            assert error.section is None, "a blueprint has no skeleton section"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_a_case_the_validator_accepts_also_parses(case: dict[str, Any]) -> None:
    """The composite invariant of R-23's ordering: validate, then construct.

    A document the validator accepts is one `service/` will hand to the model
    and store, so it must parse. If it does not, the service raises where a rule
    id belongs - which CLAUDE.md forbids and which M2's review found for real: a
    faulted fixture with no ``output`` returned zero findings and then raised
    ``('nodes','verify_compliance','output') missing``. Ruling R-07's second
    amendment fixed that instance; this test is the general gate, so the *class*
    of defect cannot come back through some other field.

    A ``parse_raises`` case is the deliberate inverse - the validator rejects it
    and the model would too - and is skipped here by the ``ok`` guard.
    """
    ok, _ = run_case(case)
    if not ok:
        return
    model = MODELS[case["target"]]
    parsed = model.model_validate(mutated(case))
    assert parsed is not None, case["id"]


def test_every_case_declares_at_most_the_registry() -> None:
    """A typo in an `expect` list is a rule id nothing implements."""
    unknown = {
        (case["id"], rule)
        for case in CASES + RAW_TEXT_CASES
        for rule in case["expect"]
        if rule not in RULE_REGISTRY
    }
    assert not unknown, f"cases declare rule ids that are not in the registry: {sorted(unknown)}"


def test_the_multi_cases_prove_one_pass_reporting() -> None:
    """Pins why the MULTI cases exist: more than one rule, from more than one file.

    If someone reduces one of these to a single expectation, the corpus stops
    proving that the validator does not stop at the first error.
    """
    multi = {case["id"]: case["expect"] for case in CASES if case["id"].startswith("MULTI-")}
    assert multi, "the corpus has no multi-error case left"
    for case_id, expect in multi.items():
        assert len(expect) >= 2, f"{case_id} declares fewer than two rules"
