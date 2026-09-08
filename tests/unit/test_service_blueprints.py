"""The five blueprint service functions, including both sides of every guard.

Three tests here exist specifically because a docstring claim without one is
worth nothing - the lesson the M3 fix rounds recorded twice:

- :func:`test_a_store_that_refuses_a_write_becomes_a_bp_016_envelope` and
  :func:`test_any_other_store_error_becomes_an_envelope_too` monkeypatch the
  store into raising, so the two ``except`` branches in ``_store`` are executed
  rather than argued to be unreachable;
- :func:`test_republishing_an_identical_document_is_a_no_op_success` is ruling
  R-29's *whole* point, and it is the case a literal reading of BP-016's
  catalogue row would reject.

And one test is the reachability proof for the ``status`` normalisation:
:func:`test_demoting_a_published_version_reports_bp_016_rather_than_raising`
is the sequence that, without normalising before validation, made BP-016 pass
and then ``put_blueprint`` raise.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agentprops.models import Blueprint
from agentprops.service import ServiceContext, blueprints
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_DOCUMENT_SHAPE,
    AP_NOT_FOUND,
    AP_STORE_REFUSED,
)
from agentprops.storage import PublishedVersionImmutableError, StoreError
from conftest import BLUEPRINT_FIXTURE, load_document
from envelopes import codes, data, findings, rules, warnings_of


def publish(context: ServiceContext, document: dict[str, Any]) -> Any:
    return blueprints.upsert(context, document, publish=True)


# ------------------------------------------------------------------- upsert


def test_publishing_the_golden_blueprint_stores_it_and_returns_the_document(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    envelope = publish(context, blueprint_document)
    assert envelope.ok is True
    stored = data(envelope)["blueprint"]
    assert stored["agent_id"] == "location-onboarding"
    assert stored["status"] == "published"
    assert context.store.get_blueprint("location-onboarding", "1.0.0") is not None


def test_the_returned_document_round_trips_the_submitted_one(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Ruling R-08's ``exclude_unset``: no optional field comes back as ``null``."""
    submitted = copy.deepcopy(blueprint_document)
    stored = data(publish(context, blueprint_document))["blueprint"]
    assert stored == submitted
    assert "tool_name" not in stored["nodes"][0] or stored["nodes"][0]["tool_name"] is not None


def test_status_is_derived_from_publish_and_may_be_omitted(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    del blueprint_document["status"]
    envelope = blueprints.upsert(context, blueprint_document, publish=False)
    assert envelope.ok is True
    assert data(envelope)["blueprint"]["status"] == "draft"


def test_a_draft_is_freely_overwritten(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    blueprints.upsert(context, blueprint_document, publish=False)
    edited = load_document(BLUEPRINT_FIXTURE)
    edited["description"] = "reworded"
    envelope = blueprints.upsert(context, edited, publish=False)
    assert envelope.ok is True
    assert data(envelope)["blueprint"]["description"] == "reworded"


def test_republishing_an_identical_document_is_a_no_op_success(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Ruling R-29. A CI pipeline that publishes on every run is the normal case."""
    first = publish(context, copy.deepcopy(blueprint_document))
    second = publish(context, copy.deepcopy(blueprint_document))
    assert first.ok is True
    assert second.ok is True
    assert data(second)["blueprint"] == data(first)["blueprint"]


def test_republishing_a_differing_document_reports_bp_016(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    publish(context, copy.deepcopy(blueprint_document))
    edited = load_document(BLUEPRINT_FIXTURE)
    edited["description"] = "changed after publishing"
    envelope = publish(context, edited)
    assert envelope.ok is False
    assert rules(envelope) == ["BP-016"]
    assert findings(envelope)[0].pointer == "/version"


def test_demoting_a_published_version_reports_bp_016_rather_than_raising(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """The sequence the ``status`` normalisation exists for.

    Submitting an otherwise-identical document with ``publish=False`` is a
    demotion, which is a modification of a published version. Before the
    service normalised ``status`` ahead of validation, BP-016 compared two
    identical documents, reported nothing, and ``put_blueprint`` then raised
    ``PublishedVersionImmutableError`` - a user-caused exception.
    """
    publish(context, copy.deepcopy(blueprint_document))
    envelope = blueprints.upsert(context, copy.deepcopy(blueprint_document), publish=False)
    assert envelope.ok is False
    assert rules(envelope) == ["BP-016"]


def test_a_catalogue_violation_is_reported_and_nothing_is_stored(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    blueprint_document["agent_id"] = "NOT_VALID"
    envelope = publish(context, blueprint_document)
    assert envelope.ok is False
    assert "BP-001" in rules(envelope)
    assert context.store.list_blueprints(None) == []


def test_a_warning_only_document_stores_and_carries_the_warning(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Ruling R-13 on a write path: BP-019 does not block, it rides along."""
    del blueprint_document["nodes"][0]["notes"]
    envelope = publish(context, blueprint_document)
    assert envelope.ok is True
    assert codes(envelope) == ["BP-019"]
    assert context.store.get_blueprint("location-onboarding", "1.0.0") is not None


def test_a_non_object_payload_is_a_boundary_envelope(context: ServiceContext) -> None:
    envelope = blueprints.upsert(context, ["not", "an", "object"], publish=False)
    assert envelope.ok is False
    assert rules(envelope) == [AP_ARGUMENT]


def test_a_shape_the_catalogue_does_not_own_is_ap_003(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """The R-04/R-23 residue: every rule passes, and the model still refuses."""
    blueprint_document["surprise_key"] = True
    envelope = publish(context, blueprint_document)
    assert envelope.ok is False
    assert rules(envelope) == [AP_DOCUMENT_SHAPE]
    assert findings(envelope)[0].pointer == "/surprise_key"
    assert context.store.list_blueprints(None) == []


def test_a_store_that_refuses_a_write_becomes_a_bp_016_envelope(
    context: ServiceContext, blueprint_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The losing side of the second-line guard, executed rather than reasoned about.

    ``put_blueprint`` raises ``PublishedVersionImmutableError`` for a differing
    write over a published version. The service should already have reported
    BP-016 through the resolver, so this branch is defence in depth - and
    defence nobody has run is not defence.
    """

    def refuse(bp: Blueprint, publish_flag: bool) -> Blueprint:
        raise PublishedVersionImmutableError("blueprint x@1.0.0 is published and differs (BP-016)")

    monkeypatch.setattr(context.store, "put_blueprint", refuse)
    envelope = publish(context, blueprint_document)
    assert envelope.ok is False
    assert rules(envelope) == ["BP-016"]


def test_any_other_store_error_becomes_an_envelope_too(
    context: ServiceContext, blueprint_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store guard with no rule id is ``AP-005``, never an escaping exception."""

    def refuse(bp: Blueprint, publish_flag: bool) -> Blueprint:
        raise StoreError("agent_id is not a UUID")

    monkeypatch.setattr(context.store, "put_blueprint", refuse)
    envelope = publish(context, blueprint_document)
    assert envelope.ok is False
    assert rules(envelope) == [AP_STORE_REFUSED]


# ---------------------------------------------------------------------- get


def test_get_returns_the_exact_version_when_named(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    publish(context, blueprint_document)
    envelope = blueprints.get(context, "location-onboarding", "1.0.0")
    assert envelope.ok is True
    assert data(envelope)["blueprint"]["version"] == "1.0.0"


def test_get_returns_the_latest_published_when_no_version_is_named(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    publish(context, copy.deepcopy(blueprint_document))
    newer = load_document(BLUEPRINT_FIXTURE)
    newer["version"] = "1.10.0"
    publish(context, newer)
    envelope = blueprints.get(context, "location-onboarding", None)
    assert data(envelope)["blueprint"]["version"] == "1.10.0", "semver, not lexicographic"


def test_get_reports_ap_004_for_an_unknown_blueprint(context: ServiceContext) -> None:
    envelope = blueprints.get(context, "no-such-agent", None)
    assert envelope.ok is False
    assert rules(envelope) == [AP_NOT_FOUND]


# --------------------------------------------------------------------- list


def test_list_is_ordered_by_agent_then_semver(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Ruling R-35's order, passed through unchanged: ``1.10.0`` after ``1.9.0``."""
    for version in ("1.9.0", "1.10.0", "1.0.0"):
        document = load_document(BLUEPRINT_FIXTURE)
        document["version"] = version
        publish(context, document)
    rows = data(blueprints.list_summaries(context, None))["blueprints"]
    assert [row["version"] for row in rows] == ["1.0.0", "1.9.0", "1.10.0"]


def test_list_filters_by_status(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    publish(context, copy.deepcopy(blueprint_document))
    draft = load_document(BLUEPRINT_FIXTURE)
    draft["version"] = "1.1.0"
    blueprints.upsert(context, draft, publish=False)
    assert len(data(blueprints.list_summaries(context, "published"))["blueprints"]) == 1
    assert len(data(blueprints.list_summaries(context, "draft"))["blueprints"]) == 1
    assert len(data(blueprints.list_summaries(context, None))["blueprints"]) == 2


def test_an_unknown_status_is_an_empty_list_rather_than_an_error(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """The service never gates: a nonsense filter matches nothing."""
    publish(context, blueprint_document)
    envelope = blueprints.list_summaries(context, "retired")
    assert envelope.ok is True
    assert data(envelope)["blueprints"] == []


# ----------------------------------------------------------------- validate


def test_validate_passes_the_golden_blueprint_and_stores_nothing(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    envelope = blueprints.validate(context, blueprint_document)
    assert envelope.ok is True
    assert findings(envelope) == []
    assert context.store.list_blueprints(None) == []


def test_validate_reports_a_warning_with_ok_true(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Ruling R-13's exact shape: ``ok: true`` with the warning in ``errors``."""
    del blueprint_document["nodes"][0]["notes"]
    envelope = blueprints.validate(context, blueprint_document)
    assert envelope.ok is True
    assert rules(envelope) == ["BP-019"]
    assert findings(envelope)[0].severity == "warning"


def test_validate_sees_the_published_version_so_bp_016_can_fire(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """The real resolver, not ``NullResolver`` - which is the useful answer here."""
    publish(context, copy.deepcopy(blueprint_document))
    edited = load_document(BLUEPRINT_FIXTURE)
    edited["description"] = "different"
    assert rules(blueprints.validate(context, edited)) == ["BP-016"]


def test_validate_of_a_non_object_is_an_error_envelope(context: ServiceContext) -> None:
    envelope = blueprints.validate(context, "a string")
    assert envelope.ok is False
    assert rules(envelope) == [AP_ARGUMENT]


# --------------------------------------------------------------------- diff


def test_diff_between_two_stored_versions_is_a_success_with_no_warnings(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    publish(context, copy.deepcopy(blueprint_document))
    newer = load_document(BLUEPRINT_FIXTURE)
    newer["version"] = "1.1.0"
    newer["nodes"][0]["kind"] = "llm"
    publish(context, newer)
    envelope = blueprints.diff(context, "location-onboarding", "1.0.0", "1.1.0")
    assert envelope.ok is True
    assert warnings_of(envelope) == []
    assert data(envelope)["diff"]["nodes"]["changed"][0]["changes"][0]["to"] == "llm"


def test_diff_against_a_missing_version_is_a_warning_not_a_failure(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """contracts section 4: informational, **never a failure signal**."""
    publish(context, blueprint_document)
    envelope = blueprints.diff(context, "location-onboarding", "1.0.0", "9.9.9")
    assert envelope.ok is True
    assert codes(envelope) == ["blueprint_version_missing"]
    assert warnings_of(envelope)[0].detail["side"] == "to"
    assert data(envelope)["diff"]["to"]["present"] is False


def test_diff_with_both_versions_missing_is_still_a_success(context: ServiceContext) -> None:
    envelope = blueprints.diff(context, "no-such-agent", "1.0.0", "2.0.0")
    assert envelope.ok is True
    assert [item.detail["side"] for item in warnings_of(envelope)] == ["from", "to"]


def test_diff_includes_a_draft_version(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """``get_blueprint`` with an explicit version ignores status, and diff should too."""
    publish(context, copy.deepcopy(blueprint_document))
    draft = load_document(BLUEPRINT_FIXTURE)
    draft["version"] = "2.0.0"
    blueprints.upsert(context, draft, publish=False)
    envelope = blueprints.diff(context, "location-onboarding", "1.0.0", "2.0.0")
    assert warnings_of(envelope) == []
    assert data(envelope)["diff"]["to"]["status"] == "draft"
