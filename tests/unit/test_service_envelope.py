"""The two envelopes, the six ``AP-*`` boundary codes, and the warning conversion.

The load-bearing test here is
:func:`test_boundary_codes_are_disjoint_from_the_catalogue`. ``AP-*`` ids sit in
:attr:`RuleError.rule`, the same field a catalogue id sits in, and contracts
section 1 calls that field "the catalogue id". If one of them ever collided with
a real rule, or was registered as one, the drift test would start disagreeing
with the catalogue and the cause would be two files away.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agentprops.models import Blueprint, Dataset, RuleError
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_DOCUMENT_SHAPE,
    AP_NOT_FOUND,
    BOUNDARY_CODES,
    blocking,
    boundary,
    failure,
    field_pointer,
    findings_from_validation_error,
    not_found,
    success,
    warning,
    warnings_from,
)
from agentprops.validation import RULE_REGISTRY
from catalogue import parse_boundary_codes, parse_catalogue_ids, parse_runtime_codes
from conftest import FIXTURES_DIR

CATALOGUE_PATH = FIXTURES_DIR.parents[1] / "docs" / "contracts.md"


def test_success_wraps_the_payload_under_one_named_key() -> None:
    envelope = success("blueprint", {"agent_id": "x"})
    assert envelope.model_dump(mode="json") == {
        "ok": True,
        "data": {"blueprint": {"agent_id": "x"}},
        "warnings": [],
    }


def test_success_carries_warnings_without_becoming_a_failure() -> None:
    """Ground rule 3: a policy problem is a warning on a *successful* response."""
    envelope = success("blueprint", {}, [warning("BP-019", pointer="/nodes/0/notes")])
    dumped = envelope.model_dump(mode="json")
    assert dumped["ok"] is True
    assert dumped["warnings"] == [{"code": "BP-019", "detail": {"pointer": "/nodes/0/notes"}}]


def test_failure_is_ok_false_and_carries_every_finding() -> None:
    findings = [boundary(AP_ARGUMENT, "/agent_id", "nope"), boundary(AP_NOT_FOUND, "/id", "gone")]
    dumped = failure(findings).model_dump(mode="json")
    assert dumped["ok"] is False
    assert [item["rule"] for item in dumped["errors"]] == [AP_ARGUMENT, AP_NOT_FOUND]


def test_a_boundary_finding_carries_a_pointer_and_a_context() -> None:
    finding = boundary(AP_ARGUMENT, field_pointer("labels", "tier"), "bad", given_type="int")
    assert finding.pointer == "/labels/tier"
    assert finding.severity == "error"
    assert finding.section is None
    assert finding.context == {"given_type": "int"}


def test_field_pointer_escapes_a_token_that_contains_a_slash() -> None:
    """A dimension name is caller data, so it must not be able to forge a segment."""
    assert field_pointer("labels", "a/b") == "/labels/a~1b"
    assert field_pointer("labels", "a~b") == "/labels/a~0b"


def test_not_found_points_at_the_argument_that_named_the_missing_thing() -> None:
    envelope = not_found("dataset", field="dataset_id", dataset_id="abc", version=3)
    assert envelope.ok is False
    finding = envelope.errors[0]
    assert finding.rule == AP_NOT_FOUND
    assert finding.pointer == "/dataset_id"
    assert finding.context == {"dataset_id": "abc", "version": 3}


def test_not_found_takes_the_pointer_field_by_name_not_by_kwarg_order() -> None:
    """The pointer must not depend on which context kwarg happens to be first.

    It did once - ``next(iter(context), "id")`` - which was right for both
    callers and would have pointed at ``/version`` the first time anyone wrote
    the kwargs the other way round. This asserts the property directly, so the
    old implementation fails it.
    """
    reordered = not_found("dataset", field="dataset_id", version=3, dataset_id="abc")
    assert reordered.errors[0].pointer == "/dataset_id"
    assert reordered.errors[0].context == {"version": 3, "dataset_id": "abc"}


def test_warnings_from_keeps_only_the_warning_severity_findings() -> None:
    """Ruling R-13: the four warning rules ride back on a success envelope."""
    findings = [
        RuleError(rule="BP-005", severity="error", pointer="/nodes/2", message="unreachable"),
        RuleError(rule="BP-019", severity="warning", pointer="/nodes/0/notes", message="no notes"),
    ]
    converted = warnings_from(findings)
    assert [item.code for item in converted] == ["BP-019"]
    assert converted[0].detail["pointer"] == "/nodes/0/notes"
    assert converted[0].detail["section"] is None


def test_warnings_from_drops_nothing_the_validator_reported() -> None:
    finding = RuleError(
        rule="DS-027",
        severity="warning",
        pointer="/provenance/intent",
        message="intent equals narrative",
        section="provenance",
        context={"length": 42},
    )
    detail = warnings_from([finding])[0].detail
    assert detail == {
        "pointer": "/provenance/intent",
        "message": "intent equals narrative",
        "section": "provenance",
        "context": {"length": 42},
    }


def test_blocking_is_true_only_for_an_error_severity_finding() -> None:
    error = RuleError(rule="BP-005", severity="error", pointer="/", message="x")
    warn = RuleError(rule="BP-019", severity="warning", pointer="/", message="x")
    assert blocking([error]) is True
    assert blocking([warn]) is False
    assert blocking([]) is False
    assert blocking([warn, error]) is True


def test_a_validation_error_becomes_pointed_findings_rather_than_an_exception() -> None:
    """Rulings R-04 and R-23's residue: a shape no rule owns, reported as AP-003."""
    with pytest.raises(ValidationError) as raised:
        Blueprint.model_validate({"agent_id": "x", "unknown_key": 1})
    findings = findings_from_validation_error(raised.value)
    assert findings, "a rejected document must produce at least one finding"
    assert all(finding.rule == AP_DOCUMENT_SHAPE for finding in findings)
    assert "/unknown_key" in {finding.pointer for finding in findings}
    assert all(finding.severity == "error" for finding in findings)


def test_a_dataset_shape_finding_carries_the_section_it_belongs_to() -> None:
    """So a partial fill can be repaired the same way a rule finding can (R-06)."""
    document: dict[str, Any] = {"provenance": {"title": 5}}
    with pytest.raises(ValidationError) as raised:
        Dataset.model_validate(document)
    findings = findings_from_validation_error(raised.value)
    sections = {finding.pointer: finding.section for finding in findings}
    assert sections["/provenance/title"] == "provenance"
    assert sections["/narrative"] is None or sections["/narrative"] == "provenance"


def test_boundary_codes_are_disjoint_from_the_catalogue() -> None:
    """An ``AP-*`` id must never be, or become, a catalogue rule.

    Both halves matter. Registering one would make ``RULE_REGISTRY`` disagree
    with `docs/contracts.md` and break the drift test from a different file;
    documenting one would give the surface a code with two meanings.
    """
    documented = parse_catalogue_ids(CATALOGUE_PATH) | parse_runtime_codes(CATALOGUE_PATH)
    assert BOUNDARY_CODES & documented == set()
    assert BOUNDARY_CODES & set(RULE_REGISTRY) == set()


def test_the_boundary_codes_are_all_distinct_and_all_used() -> None:
    """One constant per value, and the set is the whole of them.

    Eight from M10: M4's five, M5's ``AP-006`` (ruling R-49(b)), M8's
    ``AP-007`` (ruling R-65) and M10's ``AP-008`` for a ``run_export`` whose
    collector refused or was not listening. The count is asserted rather than
    derived so that adding a code is a visible edit here as well as in
    `contracts.md`, which is what
    :func:`test_the_boundary_codes_match_the_documented_table` then compares.
    """
    assert len(BOUNDARY_CODES) == 8
    assert all(code.startswith("AP-") for code in BOUNDARY_CODES)


def test_the_boundary_codes_match_the_documented_table() -> None:
    """`docs/contracts.md` section 3.5 and :data:`BOUNDARY_CODES` are one set.

    The same drift guard `test_validation_drift.py` puts on the rule catalogue,
    applied to the codes M4 added. A code defined but undocumented is a code
    M5 will invent a second time; a code documented but undefined is a promise
    the surface does not keep.
    """
    documented = parse_boundary_codes(CATALOGUE_PATH)
    assert documented == BOUNDARY_CODES, (
        f"documented but not defined: {sorted(documented - BOUNDARY_CODES)}; "
        f"defined but not documented: {sorted(BOUNDARY_CODES - documented)}"
    )
