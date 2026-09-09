"""``dataset_export`` and ``dataset_import``, on one store.

The cross-backend crossing - the actual acceptance clause - is in
`tests/integration/test_promotion_crossing.py`, where there are two real stores.
This file is the part that needs no container: the bundle's shape, the selection
rules, what a malformed bundle answers, and the ordering that makes an export
byte-stable.

The one thing worth reading before changing anything here: **an export is
defined as a set of datasets, so a truncated bundle is the failure mode.** A
missing dataset does not show up until someone imports the bundle on another
machine and wonders where it went. That is why an unknown ``dataset_ids`` entry
is a refusal rather than a silent omission, and why ``_discoverable`` asks the
store for an unbounded page instead of taking the default one.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from agentprops.models import Dataset, DatasetQuery
from agentprops.service import ServiceContext, blueprints
from agentprops.service.promotion import (
    BUNDLE_FORMAT,
    BUNDLE_FORMAT_VERSION,
    export_bundle,
    import_bundle,
)
from conftest import load_document

AGENT: Final = "location-onboarding"
PRIYA: Final = "datasets/priya-missing-docs.json"
ARUN: Final = "datasets/arun-escalated.json"


@pytest.fixture
def authored(context: ServiceContext) -> ServiceContext:
    """The published blueprint and both golden datasets, each at version 1."""
    assert blueprints.upsert(
        context, load_document("blueprints/location-onboarding-1.0.0.json"), publish=True
    ).ok
    for relative in (PRIYA, ARUN):
        context.store.put_dataset(Dataset.model_validate(load_document(relative)))
    return context


def payload(reply: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = reply.model_dump(mode="json")
    return dumped


def bundle_of(context: ServiceContext, ids: list[str] | None = None) -> dict[str, Any]:
    reply = export_bundle(context, AGENT, ids)
    assert reply.ok, reply
    found: dict[str, Any] = payload(reply)["data"]["bundle"]
    return found


def identifier(relative: str) -> str:
    found: str = load_document(relative)["id"]
    return found


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def test_the_bundle_is_self_describing_and_carries_its_blueprints(
    authored: ServiceContext,
) -> None:
    """contracts section 4: "Portable bundle: blueprint version plus datasets".

    The blueprint has to travel, and not for tidiness: DS-001 requires a dataset
    to name an *existing published* blueprint, so a bundle of datasets alone
    fails its own validation on arrival at any store that has not already seen
    the blueprint.
    """
    bundle = bundle_of(authored)
    assert list(bundle) == ["format", "format_version", "agent_id", "blueprints", "datasets"]
    assert bundle["format"] == BUNDLE_FORMAT
    assert bundle["format_version"] == BUNDLE_FORMAT_VERSION
    assert bundle["agent_id"] == AGENT
    assert [item["version"] for item in bundle["blueprints"]] == ["1.0.0"]
    assert len(bundle["datasets"]) == 2


def test_two_exports_of_one_store_are_the_same_bytes(authored: ServiceContext) -> None:
    """The property the key order and the two orderings exist for.

    A bundle is a file a human moves between machines and diffs. Byte stability
    needs the dataset order (``created_at``, ``id``), the blueprint order
    (semver) and the bundle's own key order all to be decided rather than
    incidental - and a dict assembled in two places is a dict that eventually
    differs.
    """
    assert bundle_of(authored) == bundle_of(authored)


def test_the_datasets_are_in_dataset_find_order(authored: ServiceContext) -> None:
    """``(created_at, id)``, which is total (ruling R-35) and authored (R-09).

    `priya`'s ``provenance.created_at`` is 10:14:22 and `arun`'s is 11:02:47, so
    the order is not the insertion order and not alphabetical - it is the one
    ``dataset_find`` promises, which is what makes an export reproducible after
    a re-import.
    """
    bundle = bundle_of(authored)
    assert [item["id"] for item in bundle["datasets"]] == [
        identifier(PRIYA),
        identifier(ARUN),
    ]


def test_naming_ids_exports_those_in_the_order_given(authored: ServiceContext) -> None:
    """An explicit list is a caller saying what it wants, in the order it wants."""
    bundle = bundle_of(authored, [identifier(ARUN), identifier(PRIYA)])
    assert [item["id"] for item in bundle["datasets"]] == [
        identifier(ARUN),
        identifier(PRIYA),
    ]


def test_an_archived_dataset_is_excluded_by_default_and_included_by_name(
    authored: ServiceContext,
) -> None:
    """``dataset_get``'s asymmetry, applied to the bundle.

    Omitting ``dataset_ids`` means "everything discoverable", and an archived
    lineage is by definition not discoverable. Naming one is a caller saying
    which dataset it means, and refusing to export it would be a policy
    judgement this layer does not make.
    """
    authored.store.set_archived(identifier(ARUN), True)
    assert [item["id"] for item in bundle_of(authored)["datasets"]] == [identifier(PRIYA)]

    named = bundle_of(authored, [identifier(ARUN)])
    assert [item["id"] for item in named["datasets"]] == [identifier(ARUN)]
    assert named["datasets"][0]["archived"] is True, "the archive flag did not travel"


def test_an_unknown_id_is_refused_rather_than_omitted(authored: ServiceContext) -> None:
    """A truncated bundle is invisible until it is imported somewhere else.

    So a named id that resolves to nothing is ``AP-004`` and the whole export
    fails, rather than a bundle that quietly contains one dataset where the
    caller asked for two.
    """
    body = payload(export_bundle(authored, AGENT, [identifier(PRIYA), "not-a-uuid"]))
    assert body["ok"] is False
    assert [finding["rule"] for finding in body["errors"]] == ["AP-004"]
    assert body["errors"][0]["pointer"] == "/dataset_ids"
    assert body["errors"][0]["context"]["dataset_id"] == "not-a-uuid"


def test_a_dataset_belonging_to_another_agent_is_refused(authored: ServiceContext) -> None:
    """The bundle names one agent and carries that agent's blueprints.

    A foreign dataset would arrive without the blueprint DS-001 needs, so it is
    a miss rather than a silent inclusion - and reporting it against the id the
    caller wrote is more useful than reporting DS-001 on the far machine.
    """
    body = payload(export_bundle(authored, "some-other-agent", [identifier(PRIYA)]))
    assert body["ok"] is False
    assert [finding["rule"] for finding in body["errors"]] == ["AP-004"]


def test_an_agent_with_nothing_exports_an_empty_bundle(authored: ServiceContext) -> None:
    """An empty answer, not an error. The bundle says so in its own shape.

    ``dataset_export`` is a read, and a read that matched nothing is not a
    failure - ``dataset_find`` returns ``[]`` for the same query. A caller that
    wanted to know whether the agent exists asks ``agent_list``.
    """
    bundle = bundle_of(authored, [])
    assert bundle["datasets"] == []
    assert bundle["blueprints"] == [], "an empty export carried a blueprint anyway"


def test_an_empty_id_list_is_not_the_same_as_no_id_list(authored: ServiceContext) -> None:
    """Absence and emptiness are different answers, and the tool depends on it.

    Omitted means "every discoverable dataset"; ``[]`` means "these zero
    datasets". Collapsing them would make an empty array quietly export
    everything, which is the wrong direction for a tool whose output another
    machine imports.
    """
    assert bundle_of(authored, [])["datasets"] == []
    assert len(bundle_of(authored, None)["datasets"]) == 2


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def test_a_round_trip_through_one_store_is_a_new_version(authored: ServiceContext) -> None:
    """Copy-on-write, so re-importing into the store it came from is safe.

    Each dataset becomes version 2 of its own lineage rather than overwriting
    version 1, and ``dataset_find`` still returns one row per lineage. The
    blueprint is a byte-identical re-publish, which ruling R-29 makes a no-op
    success rather than a BP-016 failure.
    """
    bundle = bundle_of(authored)
    body = payload(import_bundle(authored, bundle))
    assert body["ok"] is True
    assert [row["version"] for row in body["data"]["imported"]["datasets"]] == [2, 2]
    assert len(authored.store.find_datasets(DatasetQuery(agent_id=AGENT))) == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("format", "something.else"),
        ("format", None),
        ("format_version", 2),
        ("format_version", "1"),
    ],
    ids=["wrong-format", "no-format", "future-version", "version-as-text"],
)
def test_a_document_that_is_not_a_bundle_is_ap_001_at_the_key(
    authored: ServiceContext, key: str, value: object
) -> None:
    """Told once, at the key, rather than as the catalogue's opinion of a stranger.

    A bundle is a file a human moves between machines, so the one thing worth
    being strict about is whether this *is* one. The alternative - letting
    ``validate_dataset`` run against whatever arrived - produces a page of
    ``DS-*`` findings for a document that was never a dataset.
    """
    bundle = {**bundle_of(authored), key: value}
    body = payload(import_bundle(authored, bundle))
    assert body["ok"] is False
    assert [finding["rule"] for finding in body["errors"]] == ["AP-001"]
    assert body["errors"][0]["pointer"] == f"/bundle/{key}"


@pytest.mark.parametrize("key", ["blueprints", "datasets"])
def test_a_bundle_whose_arrays_are_not_arrays_is_ap_001(authored: ServiceContext, key: str) -> None:
    bundle = {**bundle_of(authored), key: {"not": "an array"}}
    body = payload(import_bundle(authored, bundle))
    assert body["ok"] is False
    assert [finding["rule"] for finding in body["errors"]] == ["AP-001"]
    assert body["errors"][0]["pointer"] == f"/bundle/{key}"


def test_something_that_is_not_an_object_at_all_is_ap_001(authored: ServiceContext) -> None:
    """The argument boundary, before the bundle boundary. Same envelope either way."""
    candidates: tuple[object, ...] = (None, [], "a bundle", 7)
    for payload_in in candidates:
        body = payload(import_bundle(authored, payload_in))
        assert body["ok"] is False
        assert [finding["rule"] for finding in body["errors"]] == ["AP-001"]


def test_a_finding_names_which_document_in_the_bundle(authored: ServiceContext) -> None:
    """The ``/datasets/1`` prefix, which is what RFC 6901 is for.

    Without it every finding in a five-dataset bundle points at
    ``/provenance/intent`` and the caller has to guess. The rule id, the
    severity, the section and the context are untouched - only the pointer
    gains the prefix.
    """
    bundle = bundle_of(authored)
    bundle["datasets"][1]["provenance"]["intent"] = "too short"

    body = payload(import_bundle(authored, bundle))
    assert body["ok"] is False
    (finding,) = [item for item in body["errors"] if item["rule"] == "DS-026"]
    assert finding["pointer"] == "/datasets/1/provenance/intent"
    assert finding["section"] == "provenance", "the section was lost in the re-pointing"


def test_a_refused_bundle_writes_no_dataset(authored: ServiceContext) -> None:
    """ "Nothing is written until all of it passes", on the half that is checkable here.

    One bad dataset refuses the whole bundle, including the *good* one beside
    it - because a bundle is a set, and importing three of five and reporting a
    failure leaves a store nobody can reason about.
    """
    bundle = bundle_of(authored)
    bundle["datasets"][1]["provenance"]["title"] = "  "

    assert payload(import_bundle(authored, bundle))["ok"] is False
    rows = authored.store.find_datasets(DatasetQuery(agent_id=AGENT))
    assert [row.version for row in rows] == [1, 1], "a refused bundle wrote a version"


def test_a_bundle_carrying_a_conflicting_blueprint_is_bp_016(authored: ServiceContext) -> None:
    """The normal shape of "two people edited 1.0.0", answered by the rule that owns it.

    BP-016 makes a published version immutable, and a bundle whose blueprint
    differs from the one already published at that version is exactly that
    violation. Reported as BP-016 rather than as a boundary code, so a caller
    does not have to learn a second vocabulary for the same problem.
    """
    bundle = bundle_of(authored)
    bundle["blueprints"][0]["description"] = "edited somewhere else"

    body = payload(import_bundle(authored, bundle))
    assert body["ok"] is False
    assert "BP-016" in {finding["rule"] for finding in body["errors"]}


def test_a_bundle_whose_blueprint_is_a_draft_is_published_anyway(
    authored: ServiceContext,
) -> None:
    """``status`` is normalised to ``published``, as ``blueprint_upsert`` does.

    A bundle carries a blueprint so its datasets can reference it, and DS-001
    requires the referenced version to be *published*. A draft in a bundle would
    otherwise import and then fail every dataset in the same bundle - a
    rejection whose cause is three steps away from its symptom.
    """
    bundle = bundle_of(authored)
    bundle["blueprints"][0]["status"] = "draft"
    assert payload(import_bundle(authored, bundle))["ok"] is True


def test_warning_findings_ride_back_on_the_success_envelope(
    authored: ServiceContext,
) -> None:
    """Ruling R-13: a warning-only document stores, and the warning is not lost.

    DS-007 warns about an entity nothing references. It must not block the
    import, and it must not vanish either - the bundle a colleague sends is
    exactly where a reviewer wants to see it.
    """
    bundle = bundle_of(authored)
    bundle["datasets"][0]["entities"]["orphan"] = {"base": {"unreferenced": True}}

    body = payload(import_bundle(authored, bundle))
    assert body["ok"] is True
    assert "DS-007" in {item["code"] for item in body["warnings"]}
    assert any(item["detail"]["pointer"].startswith("/datasets/0/") for item in body["warnings"]), (
        "a warning lost its bundle position"
    )
