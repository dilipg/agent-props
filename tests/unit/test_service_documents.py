"""Document resolution, ruling R-20's raw-text seam, and the R-23 parse step.

Two of these tests are the *evidence* for a claim made in a docstring rather
than a restatement of it, which is the distinction the M3 fix rounds were spent
learning:

- :func:`test_a_duplicate_label_key_survives_only_in_the_raw_text` shows DS-013
  firing through ``dataset_json`` and **not** firing through the parsed
  argument, so "the parsed path cannot reach DS-013" is demonstrated rather
  than asserted;
- :func:`test_the_sdk_collapses_a_duplicate_key_in_a_json_string_argument` is
  the finding about R-20's feasibility, run against the installed SDK: a JSON
  *string* handed to a parameter annotated anything other than ``str`` is
  pre-parsed by ``MCPServer`` with a bare ``json.loads``, so the duplicate is
  gone before any agentprops code sees it.
"""

from __future__ import annotations

import json
from typing import Any

from agentprops.models import Blueprint, Dataset
from agentprops.service.documents import parse, read_document
from agentprops.service.envelope import AP_ARGUMENT, AP_DOCUMENT_SHAPE, AP_MALFORMED_JSON
from conftest import DATASET_FIXTURE, load_document, load_text

DUPLICATE_TEXT = '{"labels": {"persona": "a", "persona": "b"}}'


def test_an_object_payload_resolves_with_no_duplicates_to_report() -> None:
    document = read_document("dataset", {"seed": 1})
    assert document.ok
    assert document.value == {"seed": 1}
    assert document.duplicate_keys == ()


def test_raw_text_resolves_and_reports_its_duplicate_keys() -> None:
    document = read_document("dataset", None, text_field="dataset_json", text=DUPLICATE_TEXT)
    assert document.ok
    assert document.value == {"labels": {"persona": "b"}}
    assert document.duplicate_keys == ("/labels/persona",)


def test_giving_both_forms_is_a_boundary_finding_rather_than_a_silent_choice() -> None:
    document = read_document("dataset", {"a": 1}, text_field="dataset_json", text="{}")
    assert not document.ok
    assert [finding.rule for finding in document.findings] == [AP_ARGUMENT]
    assert document.findings[0].pointer == "/dataset_json"


def test_giving_neither_form_is_a_boundary_finding() -> None:
    document = read_document("dataset", None, text_field="dataset_json", text="")
    assert not document.ok
    assert document.findings[0].rule == AP_ARGUMENT
    assert document.findings[0].pointer == "/dataset"


def test_a_non_object_payload_is_a_boundary_finding_with_its_json_type() -> None:
    for payload, expected in (([1], "array"), (7, "number"), ("x", "string"), (True, "boolean")):
        document = read_document("blueprint", payload)
        assert not document.ok, payload
        assert document.findings[0].rule == AP_ARGUMENT
        assert document.findings[0].context["given_type"] == expected


def test_malformed_raw_text_is_ap_002_and_not_a_catalogue_violation() -> None:
    document = read_document("dataset", None, text_field="dataset_json", text="{not json")
    assert not document.ok
    finding = document.findings[0]
    assert finding.rule == AP_MALFORMED_JSON
    assert finding.context["line"] == 1


def test_raw_text_that_parses_to_a_non_object_is_a_boundary_finding() -> None:
    document = read_document("dataset", None, text_field="dataset_json", text="[1, 2]")
    assert not document.ok
    assert document.findings[0].rule == AP_ARGUMENT
    assert document.findings[0].context["given_type"] == "array"


def test_parse_returns_the_model_when_the_document_is_well_shaped() -> None:
    model, findings = parse(Dataset, load_document(DATASET_FIXTURE))
    assert findings == []
    assert model is not None
    assert model.blueprint.agent_id == "location-onboarding"


def test_parse_returns_findings_instead_of_raising() -> None:
    """Ruling R-23's second step, and CLAUDE.md's no-exceptions rule together."""
    model, findings = parse(Blueprint, {"agent_id": "x"})
    assert model is None
    assert findings
    assert all(finding.rule == AP_DOCUMENT_SHAPE for finding in findings)


def test_parse_reports_a_faulted_fixture_with_no_output_as_valid() -> None:
    """Ruling R-07's second amendment: ``output`` is optional when ``fault`` is set."""
    document: dict[str, Any] = load_document(DATASET_FIXTURE)
    document["nodes"]["verify_compliance"] = {
        "entity_refs": [],
        "fault": {"kind": "timeout", "after_ms": 3000},
    }
    model, findings = parse(Dataset, document)
    assert findings == []
    assert model is not None
    assert model.nodes["verify_compliance"].output is None


def test_a_duplicate_label_key_survives_only_in_the_raw_text() -> None:
    """The two halves of ruling R-20, side by side.

    Text form: the duplicate is reported. Parsed form: there is nothing to
    report, because ``json.loads`` already collapsed it - which is why R-20
    moved detection to the boundary and why ``dataset_json`` exists.
    """
    text = load_text(DATASET_FIXTURE).replace(
        '"labels": {', '"labels": {"persona": "corporate-admin",', 1
    )
    from_text = read_document("dataset", None, text_field="dataset_json", text=text)
    assert from_text.duplicate_keys == ("/labels/persona",)

    from_object = read_document("dataset", json.loads(text))
    assert from_object.duplicate_keys == ()
    assert from_object.value == from_text.value


def test_the_sdk_collapses_a_duplicate_key_in_a_json_string_argument() -> None:
    """R-20's premise, tested against the installed SDK rather than assumed.

    ``MCPServer`` pre-parses a string argument whose annotation is not literally
    ``str`` (``func_metadata.pre_parse_json``). This asserts what that pre-parse
    does to a duplicate key, so the reason ``dataset_json`` must be annotated
    ``str`` is pinned by a test: if a future SDK release ever passed the string
    through, or parsed it with a hook, this fails and the workaround can go.
    """
    from mcp.server.mcpserver.utilities.func_metadata import func_metadata

    def probe(doc: object) -> None: ...

    pre_parsed = func_metadata(probe).pre_parse_json({"doc": DUPLICATE_TEXT})
    assert pre_parsed["doc"] == {"labels": {"persona": "b"}}, (
        "the SDK no longer collapses duplicate keys in a JSON-string argument; "
        "re-check whether dataset_json still needs a str annotation"
    )

    def strict_probe(doc: str) -> None: ...

    untouched = func_metadata(strict_probe).pre_parse_json({"doc": DUPLICATE_TEXT})
    assert untouched["doc"] == DUPLICATE_TEXT, (
        "a str-annotated parameter must still receive the raw text; ruling R-20's "
        "boundary detection depends on it"
    )
