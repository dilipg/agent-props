"""The skeleton pipeline at the service layer: the manifest, the fill, the submit.

`test_worked_example_fill.py` drives the same pipeline through the MCP tool
surface and is where M5's acceptance criteria 1 and 6 are checked. This module
is the layer underneath: the parts the tool surface cannot reach and the losing
sides of the two races the design has.

Three groups are here because of a specific instruction rather than for
completeness:

**The losing side of every concurrency claim.** ``skeleton_id`` is
caller-supplied on two of the three tools and the partial state is mutable
across calls, so whatever this milestone claims about concurrency needs a test
of the *bad* interleaving, not a docstring. Three milestones running, the
blocking review finding sat behind a confident record entry with no test behind
the residue. So: a double submit, a fill after a submit, and two interleaved
fills - the last of which **loses data**, and says so.

**Both ends of every boundary the record reasons about.** ``_claim``'s
generation walk has three cases (free, unsubmitted, submitted) and an
exhaustion; all four are here, the last with the bound monkeypatched down so it
is reachable at all.

**Ruling R-45's distinction, all four cases.** A never-filled provenance is
SK-004; a filled provenance with a blank title is DS-025, with a trivial intent
DS-026, with partial labels DS-024. The milestone's gate names three of those
and R-45 corrects the first, so all four are pinned together - the point is
which rule fires, and one case cannot show that.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import pytest

from agentprops.models import Blueprint, DatasetQuery, Section, Skeleton
from agentprops.service import (
    FrozenClock,
    ServiceContext,
    blueprints,
    datasets,
    skeletons,
    sqlite_context,
)
from agentprops.service.skeletons import (
    DATASET_FIELD_ORDER,
    SECTION_FIELDS,
    assemble,
    instructions_for,
    manifest_for,
    scaffold_for,
)
from agentprops.storage import SqlStore
from agentprops.validation.context import BlueprintView
from agentprops.validation.pointers import SECTIONS, section_for_pointer
from conftest import FROZEN_NOW, load_document
from envelopes import codes, data, findings, rules

AGENT: Final = "location-onboarding"
VERSION: Final = "1.0.0"
SEED: Final = 20260908

#: The golden dataset's labels: complete, and inside the vocabulary.
LABELS: Final[dict[str, str]] = {
    "persona": "multi-unit-operator",
    "scenario": "missing-documents",
    "tier": "regional",
    "outcome": "success",
    "edge_case": "none",
}


@pytest.fixture
def published(context: ServiceContext, blueprint_document: dict[str, Any]) -> BlueprintView:
    """The golden blueprint, published, since every skeleton needs a parent."""
    reply = blueprints.upsert(context, blueprint_document, publish=True)
    return BlueprintView(data(reply)["blueprint"])


def sections_of(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``document`` split into the five section contents ruling R-06 defines.

    The inverse of :func:`~agentprops.service.skeletons.assemble`, and the only
    place in the suite that knows how to take the golden dataset apart. If the
    manifest partition were wrong, this function could not put the fixture back
    together - which is criterion 6, checked in `test_worked_example_fill.py`.
    """
    return {
        section_id: {field: document[field] for field in SECTION_FIELDS[section_id]}
        for section_id in SECTIONS
    }


def start(context: ServiceContext, **overrides: Any) -> str:
    """``dataset_skeleton``, returning the ``skeleton_id``."""
    arguments: dict[str, Any] = {
        "agent_id": AGENT,
        "version": VERSION,
        "labels": LABELS,
        "seed": SEED,
    }
    arguments.update(overrides)
    reply = skeletons.skeleton(context, **arguments)
    skeleton_id: str = data(reply)["skeleton"]["skeleton_id"]
    return skeleton_id


def fill_all(context: ServiceContext, skeleton_id: str, parts: dict[str, dict[str, Any]]) -> None:
    """Fill every section in manifest order, asserting each call succeeded."""
    for section_id in SECTIONS:
        reply = skeletons.fill_part(context, skeleton_id, section_id, parts[section_id])
        assert section_id in data(reply)["fill"]["filled"]


def filled(
    context: ServiceContext,
    dataset_document: dict[str, Any],
    edits: Mapping[str, dict[str, Any]] | None = None,
    *,
    seed: int = SEED,
) -> str:
    """A skeleton with all five sections filled from the golden dataset, plus ``edits``.

    ``edits`` is a positional mapping rather than ``**kwargs`` so that a section
    id containing a dot (``nodes.core``) is expressible at all, and so the
    keyword-only ``seed`` keeps its own type.

    ``seed`` is a parameter because a skeleton id is *derived* from the request:
    two calls with identical arguments resume one skeleton, so a test that wants
    two independent skeletons without submitting the first has to vary an input.
    That is the resume behaviour working, not a workaround for it.
    """
    parts = sections_of(dataset_document)
    parts.update(edits or {})
    skeleton_id = start(context, seed=seed)
    fill_all(context, skeleton_id, parts)
    return skeleton_id


# --------------------------------------------------------------------------- #
# The manifest, the instructions and the scaffold.
# --------------------------------------------------------------------------- #


def test_the_manifest_is_ruling_r_06s_five_sections_in_order(published: BlueprintView) -> None:
    manifest = manifest_for(published)
    assert [section.id for section in manifest] == [
        "provenance",
        "entities",
        "nodes.core",
        "nodes.branches",
        "expected",
    ]
    assert manifest[0].id == "provenance", "provenance is first (M5's gate says so)"
    assert all(section.required for section in manifest)
    assert all(section.description for section in manifest)


def test_every_manifest_pointer_maps_back_to_its_section(published: BlueprintView) -> None:
    """The tie between the manifest and M2's pointer-to-section mapping.

    ``section_for_pointer`` decides which section a ``DS-*`` finding is scoped
    to; the manifest decides which section a caller fills. If those two
    disagreed, a rejection would name a section that does not own the field, and
    the repair loop PRD 6 flow B describes would send the LLM to the wrong
    place. Nothing else in the suite compares them.
    """
    for section in manifest_for(published):
        for target in section.pointers:
            assert section_for_pointer(target, pool_nodes=published.pool_node_ids) == section.id


def test_the_five_sections_partition_every_authored_dataset_field(
    dataset_document: dict[str, Any],
) -> None:
    """Nothing authored is unassigned, and nothing is claimed twice.

    The three fields no section owns are the ones ruling R-06 makes
    ``dataset_skeleton`` *inputs* plus the two the store owns, so the assertion
    names them explicitly rather than subtracting a set and hoping.
    """
    owned = [field for fields in SECTION_FIELDS.values() for field in fields]
    assert len(owned) == len(set(owned)), "a field is owned by two sections"
    service_owned = {"id", "version", "archived", "blueprint", "seed", "labels", "validated_at"}
    assert set(dataset_document) - set(owned) == service_owned
    assert set(owned) <= set(DATASET_FIELD_ORDER)


def test_the_instructions_are_derived_and_byte_identical_across_calls(
    published: BlueprintView,
) -> None:
    """No clock, no counter: two calls for one blueprint version agree exactly."""
    manifest = manifest_for(published)
    first = instructions_for(manifest)
    assert first == instructions_for(manifest_for(published))
    for section in manifest:
        assert section.id in first, "a section the caller must fill is not mentioned"
        for field in SECTION_FIELDS[section.id]:
            assert json.dumps(field) in first
    assert "dataset_fill_part" in first and "dataset_submit" in first
    assert "SK-002" in first, "the fill order is a rule, and the caller is told which"
    assert "label_vocabulary" in first, "labels are not repairable by re-filling (ruling R-06)"


def test_the_scaffold_keys_are_blueprint_facts_and_its_leaves_are_empty(
    published: BlueprintView,
) -> None:
    """The whole of the "how much is pre-filled" decision, as an assertion.

    Keys the blueprint knows - node ids, entity ids, pool node ids - are
    emitted; nothing under them is invented. ``request_docs`` is the loop node,
    so it appears in ``pools`` and **not** in ``nodes`` (ruling R-01).
    """
    scaffold = scaffold_for(published, LABELS, SEED, {})
    assert scaffold["blueprint"] == {"agent_id": AGENT, "version": VERSION}
    assert scaffold["seed"] == SEED and scaffold["labels"] == LABELS
    assert list(scaffold["nodes"]) == [
        "receive_request",
        "fetch_store_profile",
        "check_docs",
        "recheck_store",
        "assign_training",
        "verify_compliance",
        "complete",
        "escalate",
    ]
    assert list(scaffold["pools"]) == ["request_docs"]
    assert list(scaffold["entities"]) == ["store", "franchisee"]
    assert all(fixture == {} for fixture in scaffold["nodes"].values())
    assert scaffold["pools"]["request_docs"] == []
    assert scaffold["provenance"] == {} and scaffold["narrative"] == ""


def test_no_scaffold_placeholder_would_validate_if_left_unedited(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The rule that decides how much scaffolding is safe, tested as behaviour.

    A submit of the scaffold verbatim must be **rejected**, because a
    placeholder that validates is a placeholder that stores a nonsense dataset.
    Each of the three container sections reports a rule id rather than an
    ``AP-003``, which is what makes the rejection useful.
    """
    scaffold = scaffold_for(published, LABELS, SEED, {})
    parts = {
        section_id: {field: scaffold[field] for field in SECTION_FIELDS[section_id]}
        for section_id in SECTIONS
    }
    skeleton_id = start(context)
    fill_all(context, skeleton_id, parts)
    reported = set(rules(skeletons.submit(context, skeleton_id)))
    assert {"DS-004", "DS-019"} <= reported, reported
    assert "DS-025" in reported and "DS-021" in reported


def test_a_filled_section_shows_its_content_in_the_scaffold(
    published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """A resumed skeleton is a live view of the work so far."""
    parts = sections_of(dataset_document)
    scaffold = scaffold_for(published, LABELS, SEED, {"provenance": parts["provenance"]})
    assert scaffold["provenance"] == dataset_document["provenance"]
    assert scaffold["narrative"] == dataset_document["narrative"]
    assert scaffold["expected"] == {}, "an unfilled section still shows its placeholder"


# --------------------------------------------------------------------------- #
# dataset_skeleton: resolution, and the generation walk.
# --------------------------------------------------------------------------- #


def test_an_unpublished_blueprint_is_ap_004_not_ds_001(context: ServiceContext) -> None:
    """There is no document yet, so no dataset rule can have an opinion (R-42a)."""
    reply = skeletons.skeleton(context, AGENT, VERSION, LABELS, SEED)
    assert rules(reply) == ["AP-004"]
    assert findings(reply)[0].pointer == "/version"


def test_a_draft_blueprint_does_not_resolve(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """DS-001 wants a *published* version, and so does the skeleton it feeds."""
    blueprints.upsert(context, blueprint_document, publish=False)
    assert rules(skeletons.skeleton(context, AGENT, VERSION, LABELS, SEED)) == ["AP-004"]


def test_the_skeleton_id_is_derived_from_the_seed(
    context: ServiceContext, published: BlueprintView
) -> None:
    """Ruling R-10: no ``uuid4()``. Reproducible from the request, and v4-shaped."""
    minted = UUID(start(context))
    assert minted.version == 4
    assert minted == UUID(start(context)), "a derived id is not a random one"


def test_a_second_request_with_identical_arguments_resumes_the_same_skeleton(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The losing side of the derived id: a re-request must not wipe the work.

    Two calls with identical arguments derive one id, so the second call reaches
    an existing row. It returns that skeleton **with its parts intact** rather
    than overwriting it - an LLM that lost its ``skeleton_id`` mid-fill resumes
    instead of starting over. A ``put_skeleton`` here would silently discard
    every filled section, and nothing else in the suite would notice.
    """
    skeleton_id = start(context)
    parts = sections_of(dataset_document)
    skeletons.fill_part(context, skeleton_id, "provenance", parts["provenance"])

    again = skeletons.skeleton(context, AGENT, VERSION, LABELS, SEED)
    payload = data(again)["skeleton"]
    assert payload["skeleton_id"] == skeleton_id
    assert payload["skeleton"]["provenance"] == dataset_document["provenance"]

    stored = context.store.get_skeleton(skeleton_id)
    assert stored is not None and set(stored.parts) == {"provenance"}


def test_a_request_after_a_submit_mints_a_new_skeleton(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The other end of the same boundary: a submitted skeleton is not resumable.

    SK-005 has closed it, so the walk moves to the next generation - a different
    salt, a different id - and the earlier skeleton keeps its lineage. Without
    the walk, authoring a second dataset from one label combination would be
    impossible; without the ``submitted_as`` check, the walk would skip past a
    resumable skeleton.
    """
    first = filled(context, dataset_document)
    stored_dataset = data(skeletons.submit(context, first))["dataset"]

    second = start(context)
    assert second != first
    original = context.store.get_skeleton(first)
    assert original is not None
    assert str(original.submitted_as) == stored_dataset["id"], "lineage survived the new skeleton"
    fresh = context.store.get_skeleton(second)
    assert fresh is not None and fresh.parts == {}


def test_different_labels_or_seeds_give_different_skeletons(
    context: ServiceContext, published: BlueprintView
) -> None:
    """Every input is part of the salt, and label key order is not."""
    baseline = start(context)
    assert start(context, seed=SEED + 1) != baseline
    assert start(context, labels={**LABELS, "tier": "enterprise"}) != baseline
    reordered = dict(reversed(list(LABELS.items())))
    assert start(context, labels=reordered) == baseline, "labels are canonicalised, not ordered"


def test_running_out_of_generations_is_reported_rather_than_overwriting(
    context: ServiceContext,
    published: BlueprintView,
    dataset_document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exhaustion end of the walk, with the bound lowered so it is reachable.

    The alternative to reporting is reusing the last candidate, which would
    ``put_skeleton`` over a **submitted** skeleton's row and destroy the
    ``submitted_as`` lineage nothing else records. The pointer is at ``/seed``
    because that is the argument the caller changes to get served.
    """
    monkeypatch.setattr(skeletons, "SKELETON_GENERATIONS", 1)
    first = filled(context, dataset_document)
    skeletons.submit(context, first)

    reply = skeletons.skeleton(context, AGENT, VERSION, LABELS, SEED)
    assert rules(reply) == ["AP-001"]
    assert findings(reply)[0].pointer == "/seed"
    stored = context.store.get_skeleton(first)
    assert stored is not None and stored.submitted_as is not None


# --------------------------------------------------------------------------- #
# dataset_fill_part.
# --------------------------------------------------------------------------- #


def test_filling_entities_before_provenance_is_sk_002(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """M5's acceptance criterion 2, through the service."""
    skeleton_id = start(context)
    parts = sections_of(dataset_document)
    reply = skeletons.fill_part(context, skeleton_id, "entities", parts["entities"])
    assert rules(reply) == ["SK-002"]
    assert findings(reply)[0].section == "provenance"
    stored = context.store.get_skeleton(skeleton_id)
    assert stored is not None and stored.parts == {}, "a rejected fill writes nothing"


def test_the_progress_response_is_in_manifest_order(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """``remaining[0]`` is the caller's next action, which is what SK-002 enforces."""
    skeleton_id = start(context)
    parts = sections_of(dataset_document)
    reply = skeletons.fill_part(context, skeleton_id, "provenance", parts["provenance"])
    assert data(reply)["fill"] == {
        "filled": ["provenance"],
        "remaining": ["entities", "nodes.core", "nodes.branches", "expected"],
    }


def test_a_refill_replaces_the_section_rather_than_merging_into_it(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """PRD 6 flow B repairs one part, and the part is the whole section.

    Merging would mean a caller could not remove a field it should not have
    written, and the stored state would depend on the order of two calls rather
    than on the last one.
    """
    skeleton_id = start(context)
    parts = sections_of(dataset_document)
    skeletons.fill_part(context, skeleton_id, "provenance", parts["provenance"])
    skeletons.fill_part(context, skeleton_id, "provenance", {"narrative": "replaced"})
    stored = context.store.get_skeleton(skeleton_id)
    assert stored is not None
    assert stored.parts["provenance"] == {"narrative": "replaced"}


def test_a_section_the_manifest_does_not_have_is_sk_001(
    context: ServiceContext, published: BlueprintView
) -> None:
    skeleton_id = start(context)
    reply = skeletons.fill_part(context, skeleton_id, "nodes.compliance", {"nodes": {}})
    assert rules(reply) == ["SK-001"]


def test_a_content_key_the_section_does_not_own_is_ap_001(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """No ``SK-*`` rule covers the shape of ``content``, so it is a boundary code.

    ``narrative`` belongs to ``provenance`` (ruling R-06), so sending it with
    ``entities`` is the mistake this reports - and the message names the fields
    the section does own.
    """
    skeleton_id = start(context)
    parts = sections_of(dataset_document)
    skeletons.fill_part(context, skeleton_id, "provenance", parts["provenance"])
    reply = skeletons.fill_part(
        context, skeleton_id, "entities", {"entities": {}, "narrative": "n"}
    )
    assert rules(reply) == ["AP-001"]
    assert findings(reply)[0].pointer == "/content/narrative"
    assert findings(reply)[0].context["unknown_key"] == "narrative"


def test_an_owned_field_of_the_wrong_json_type_is_ap_001(
    context: ServiceContext, published: BlueprintView
) -> None:
    skeleton_id = start(context)
    reply = skeletons.fill_part(
        context, skeleton_id, "provenance", {"provenance": [], "narrative": 7}
    )
    assert rules(reply) == ["AP-001", "AP-001"]
    assert [finding.pointer for finding in findings(reply)] == [
        "/content/provenance",
        "/content/narrative",
    ]


def test_a_missing_owned_field_is_accepted_and_becomes_a_ds_rule_at_submit(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The line between the local check and the catalogue, in one test.

    Filling ``provenance`` without ``narrative`` is **not** a fill-time error:
    ruling R-45 assigns an absent-or-blank value to the ``DS-*`` rule that owns
    the field, so a model-level parse here would swallow a rule id the ruling
    explicitly wants. DS-021 reports it at submit, scoped to ``provenance``, and
    the caller re-fills one section.
    """
    parts = sections_of(dataset_document)
    without = {"provenance": parts["provenance"]["provenance"]}
    skeleton_id = filled(context, dataset_document, {"provenance": without})
    reply = skeletons.submit(context, skeleton_id)
    assert rules(reply) == ["DS-021"]
    assert findings(reply)[0].section == "provenance"
    assert findings(reply)[0].pointer == "/narrative"


def test_a_node_section_referencing_an_undeclared_entity_is_rejected(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """M5's brief: "rejecting a node section referencing an entity not yet declared"."""
    parts = sections_of(dataset_document)
    parts["entities"] = {"entities": {"store": dataset_document["entities"]["store"]}}
    skeleton_id = start(context)
    skeletons.fill_part(context, skeleton_id, "provenance", parts["provenance"])
    skeletons.fill_part(context, skeleton_id, "entities", parts["entities"])
    reply = skeletons.fill_part(context, skeleton_id, "nodes.core", parts["nodes.core"])
    assert set(rules(reply)) == {"SK-003"}
    assert all(finding.section == "nodes.core" for finding in findings(reply))
    assert all(finding.context["entity"] == "franchisee" for finding in findings(reply))


def test_an_unknown_skeleton_id_is_sk_005_on_both_tools(context: ServiceContext) -> None:
    """The existence half of SK-005, and the reason it is not ``AP-004``."""
    assert rules(skeletons.fill_part(context, "nope", "provenance", {})) == ["SK-005"]
    assert rules(skeletons.submit(context, "nope")) == ["SK-005"]
    assert rules(skeletons.submit(context, str(UUID(int=0)))) == ["SK-005"]


# --------------------------------------------------------------------------- #
# dataset_submit, and ruling R-45's four cases.
# --------------------------------------------------------------------------- #


def test_a_submit_stores_the_dataset_and_marks_the_skeleton(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The happy path, end to end, including what the store then holds."""
    skeleton_id = filled(context, dataset_document)
    stored = data(skeletons.submit(context, skeleton_id))["dataset"]
    assert stored["version"] == 1
    assert stored["archived"] is False
    assert stored["validated_at"] == "2026-09-08T12:00:00Z", "stamped by the frozen Clock port"

    skeleton = context.store.get_skeleton(skeleton_id)
    assert skeleton is not None and str(skeleton.submitted_as) == stored["id"]
    assert data(datasets.get(context, stored["id"], None))["dataset"] == stored


def test_the_dataset_id_is_derived_from_the_skeleton_not_the_request(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """Two skeletons from one label combination must become two datasets.

    Deriving the dataset id from ``(agent_id, version, labels, seed)`` instead
    would give both submits one id, and ``put_dataset`` would file the second as
    **version 2 of the first** - two datasets silently collapsed into one
    lineage, which no rule would report.
    """
    first = filled(context, dataset_document)
    one = data(skeletons.submit(context, first))["dataset"]
    second = filled(context, dataset_document)
    two = data(skeletons.submit(context, second))["dataset"]
    assert first != second
    assert one["id"] != two["id"]
    assert one["version"] == two["version"] == 1


def test_a_second_submit_is_sk_005_and_writes_nothing(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The losing side of "a skeleton cannot become two datasets".

    SK-005 exists precisely for this, so the proof is that the second call is
    refused *and* that the store is unchanged - a second version of the dataset
    would be a silent double write, which the version assertion is what catches.
    """
    skeleton_id = filled(context, dataset_document)
    stored = data(skeletons.submit(context, skeleton_id))["dataset"]

    again = skeletons.submit(context, skeleton_id)
    assert rules(again) == ["SK-005"]
    assert findings(again)[0].context["submitted_as"] == stored["id"]
    assert data(datasets.get(context, stored["id"], None))["dataset"]["version"] == 1


def test_a_fill_after_a_submit_is_sk_005(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """A submitted skeleton is frozen: datasets are immutable (ground rule 5)."""
    skeleton_id = filled(context, dataset_document)
    skeletons.submit(context, skeleton_id)
    reply = skeletons.fill_part(context, skeleton_id, "provenance", {"narrative": "late"})
    assert rules(reply) == ["SK-005"]


def test_a_never_filled_provenance_section_is_sk_004_not_ds_025(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-45's correction to M5's own acceptance criterion.

    A submit "missing provenance" is **SK-004**, not DS-025: there is no
    provenance document, so no ``DS-*`` provenance rule can have an opinion
    about its contents. And SK-004's precedence is total over section contents,
    which is the second assertion here - otherwise submitting an empty skeleton
    would report most of the catalogue and bury the one finding that says what
    to do.
    """
    skeleton_id = start(context)
    reply = skeletons.submit(context, skeleton_id)
    assert set(rules(reply)) == {"SK-004"}
    assert [finding.section for finding in findings(reply)] == list(SECTIONS)
    assert not any(finding.rule.startswith("DS-") for finding in findings(reply))


@pytest.mark.parametrize("title", ["   ", None], ids=["blank", "absent"])
def test_a_filled_provenance_with_a_bad_title_is_ds_025(
    context: ServiceContext,
    published: BlueprintView,
    dataset_document: dict[str, Any],
    title: str | None,
) -> None:
    """The other side of R-45's line: the section exists, so the rule owns it.

    R-45 says "*was filled* but its ``title`` is **absent or blank**", so both
    are here. The absent case is the one worth the parametrise: no ``DS-*`` rule
    owns a *missing* key in general, and ``Provenance.title`` is a required
    model field - so an implementation that parsed the section at fill time, or
    that parsed the document before validating it, would report ``AP-003``
    instead and the rule id R-45 names would be unreachable. It reports DS-025
    alone, and that is the assertion.
    """
    parts = sections_of(dataset_document)
    provenance = dict(parts["provenance"]["provenance"])
    if title is None:
        del provenance["title"]
    else:
        provenance["title"] = title
    skeleton_id = filled(
        context,
        dataset_document,
        {"provenance": {"provenance": provenance, "narrative": parts["provenance"]["narrative"]}},
    )
    reply = skeletons.submit(context, skeleton_id)
    assert rules(reply) == ["DS-025"]
    assert findings(reply)[0].section == "provenance"
    assert findings(reply)[0].pointer == "/provenance/title"


def test_a_trivial_intent_is_ds_026(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    parts = sections_of(dataset_document)
    provenance = {**parts["provenance"]["provenance"], "intent": "testing"}
    skeleton_id = filled(
        context,
        dataset_document,
        {"provenance": {"provenance": provenance, "narrative": parts["provenance"]["narrative"]}},
    )
    reply = skeletons.submit(context, skeleton_id)
    assert rules(reply) == ["DS-026"]
    assert findings(reply)[0].section == "provenance"


def test_partial_labels_are_ds_024(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The third of the gate's named rules, and the one with no section to repair.

    ``labels`` is a ``dataset_skeleton`` input rather than a fillable section
    (ruling R-06), so the finding carries ``section: null`` - the caller starts
    a new skeleton rather than re-filling. That is why ``instructions`` points
    at ``label_vocabulary`` before authoring rather than after.
    """
    partial = {"persona": LABELS["persona"], "scenario": LABELS["scenario"]}
    skeleton_id = start(context, labels=partial)
    fill_all(context, skeleton_id, sections_of(dataset_document))
    reply = skeletons.submit(context, skeleton_id)
    assert set(rules(reply)) == {"DS-024"}
    assert {finding.context["dimension"] for finding in findings(reply)} == {
        "tier",
        "outcome",
        "edge_case",
    }
    assert all(finding.section is None for finding in findings(reply))


def test_every_error_from_a_broken_section_is_scoped_to_that_section(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """M5's acceptance criterion 4, one section at a time.

    Each row breaks exactly one section and asserts that **every** finding is
    scoped to it, which is what makes the repair loop single-section: an LLM
    re-fills the section the errors name and nothing else.

    The ``entities`` row breaks a *revision* rather than removing an entity,
    because removing one is caught earlier - by SK-003, at the fill of the node
    section that references it, which is the pipeline working rather than a
    thing to work around. A dangling ``after_node`` is a submit-time finding
    scoped to ``entities``, which is what this row needs.
    """
    parts = sections_of(dataset_document)
    entities = parts["entities"]["entities"]
    store = {
        **entities["store"],
        "revisions": {"after_docs": {"after_node": "ghost", "state": {}}},
    }
    broken: dict[str, dict[str, Any]] = {
        "entities": {"entities": {**entities, "store": store}},
        "nodes.core": {"nodes": {**parts["nodes.core"]["nodes"], "ghost": {"entity_refs": []}}},
        "nodes.branches": {"pools": {"request_docs": []}},
        "expected": {"expected": {**parts["expected"]["expected"], "comparison": "vibes"}},
    }
    for offset, (section_id, content) in enumerate(broken.items()):
        skeleton_id = filled(context, dataset_document, {section_id: content}, seed=SEED + offset)
        reply = skeletons.submit(context, skeleton_id)
        reported = findings(reply)
        assert reported, f"{section_id} was expected to break"
        assert {finding.section for finding in reported} == {section_id}, [
            (finding.rule, finding.section, finding.pointer) for finding in reported
        ]


def test_an_abandoned_skeleton_does_not_become_a_dataset(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """M5's acceptance criterion 5. Abandonment is the absence of a submit.

    Three sections filled, then nothing: the skeleton row stays, the dataset
    does not exist, and ``dataset_find`` cannot see it. The skeleton is still
    resumable, which is what makes "abandoned" a state rather than a loss.
    """
    skeleton_id = start(context)
    parts = sections_of(dataset_document)
    for section_id in ("provenance", "entities", "nodes.core"):
        skeletons.fill_part(context, skeleton_id, section_id, parts[section_id])

    stored = context.store.get_skeleton(skeleton_id)
    assert stored is not None and stored.submitted_as is None
    assert data(datasets.find(context, DatasetQuery()))["datasets"] == []
    assert context.store.health().counts.datasets == 0, "a health count includes archives too"


def test_a_warning_only_dataset_stores_with_its_warnings(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-13 on the one write path that had none until M5.

    DS-032 warns when ``provenance.intent`` and ``expected.rationale`` carry the
    same text. A warning never blocks a write, so the dataset stores *and* the
    finding rides back as a ``Warning`` - which is the shape ground rule 3 asks
    for and the reason ``warnings_from`` exists.
    """
    parts = sections_of(dataset_document)
    intent = parts["provenance"]["provenance"]["intent"]
    expected = {**parts["expected"]["expected"], "rationale": intent}
    skeleton_id = filled(context, dataset_document, {"expected": {"expected": expected}})
    reply = skeletons.submit(context, skeleton_id)
    assert codes(reply) == ["DS-032"]
    assert data(reply)["dataset"]["expected"]["rationale"] == intent


def test_a_document_that_passes_every_rule_and_will_not_parse_is_ap_003(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The R-04/R-23 residue, on the write path that produces it.

    No ``DS-*`` rule owns ``entity_refs``' *presence*, so a fixture without it
    validates clean and then fails to construct. It comes back as ``AP-003``
    scoped to the section that has to be re-filled, not as a ``ValidationError``
    escaping the tool.
    """
    parts = sections_of(dataset_document)
    nodes = {**parts["nodes.core"]["nodes"]}
    nodes["complete"] = {"output": {"onboarding_status": "complete"}}
    skeleton_id = filled(context, dataset_document, {"nodes.core": {"nodes": nodes}})
    reply = skeletons.submit(context, skeleton_id)
    assert rules(reply) == ["AP-003"]
    assert findings(reply)[0].section == "nodes.core"
    assert findings(reply)[0].pointer == "/nodes/complete/entity_refs"


def test_the_assembled_document_is_in_one_canonical_key_order(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """Five calls in five orders must not produce five key orders.

    Nothing depends on it today - dict equality ignores order - but
    ``dataset_export``'s byte stability at M7 does, and one canonical order in
    the write path is cheaper than a canonicalising export.
    """
    skeleton_id = filled(context, dataset_document)
    stored = context.store.get_skeleton(skeleton_id)
    assert stored is not None
    document = assemble(stored, UUID(int=1))
    assert list(document) == [field for field in DATASET_FIELD_ORDER if field != "validated_at"]


# --------------------------------------------------------------------------- #
# The concurrency claims, each with its losing side.
# --------------------------------------------------------------------------- #


def test_a_concurrent_fill_of_another_section_is_lost(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The residue this design has, reproduced rather than reasoned about.

    ``dataset_fill_part`` is a read-modify-write of the whole ``parts`` object,
    because ``put_skeleton`` is a whole-aggregate upsert and the ``Store``
    Protocol has no compare-and-set. So two fills that both read before either
    writes end with only the second one's section: **last write wins, and the
    other section is lost.**

    This test *is* the claim. It replays the interleaving with two reads taken
    up front, which is what two concurrent calls do, and asserts the loss - so
    a future reader finds the behaviour stated as a fact instead of discovering
    it. `DECISIONS.md` carries the remedy (a CAS on the Protocol) and the
    trigger for building it; an authoring session is one caller filling one
    skeleton in sequence, which is why it is not built now.
    """
    skeleton_id = start(context)
    parts = sections_of(dataset_document)
    skeletons.fill_part(context, skeleton_id, "provenance", parts["provenance"])

    first = context.store.get_skeleton(skeleton_id)
    second = context.store.get_skeleton(skeleton_id)
    assert first is not None and second is not None

    context.store.put_skeleton(
        first.model_copy(update={"parts": {**first.parts, "entities": parts["entities"]}})
    )
    context.store.put_skeleton(
        second.model_copy(update={"parts": {**second.parts, "nodes.core": parts["nodes.core"]}})
    )

    stored = context.store.get_skeleton(skeleton_id)
    assert stored is not None
    assert set(stored.parts) == {"provenance", "nodes.core"}
    assert "entities" not in stored.parts, "if this passes, a CAS has landed - update DECISIONS.md"


def test_two_interleaved_submits_both_write_and_the_second_becomes_version_two(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """The losing side of the double-submit guard, and the honest limit of SK-005.

    Sequentially, a second submit is SK-005 and writes nothing - the test above
    proves that. **Concurrently it is not closed**: both calls read
    ``submitted_as`` as null before either writes, so both pass SK-005 and both
    reach ``put_dataset``, which allocates version 2 for the loser. The dataset
    id is derived from the skeleton, so ``mark_skeleton_submitted`` sees the same
    id twice and its replay tolerance accepts it.

    Reproduced with two service calls around one hand-held read, which is the
    interleaving two processes produce. The remedy is a compare-and-set on the
    skeleton - ``mark_skeleton_submitted`` dropping its replay tolerance, or a
    ``claim_skeleton`` method - and it is a ``Store`` Protocol change three
    adapters pay for, so it is recorded rather than smuggled into M5.
    """
    skeleton_id = filled(context, dataset_document)
    first = data(skeletons.submit(context, skeleton_id))["dataset"]

    stored = context.store.get_skeleton(skeleton_id)
    assert stored is not None
    context.store.put_skeleton(stored.model_copy(update={"submitted_as": None}))

    second = data(skeletons.submit(context, skeleton_id))["dataset"]
    assert second["id"] == first["id"]
    assert second["version"] == 2, "if this changes, a CAS has landed - update DECISIONS.md"


def test_a_skeleton_row_carries_the_two_inputs_it_needs_at_submit(
    context: ServiceContext, published: BlueprintView
) -> None:
    """Why `skeletons.labels` and `skeletons.seed` had to be added to the DDL.

    ``dataset_submit(skeleton_id)`` takes no other argument, so the two
    ``dataset_skeleton`` inputs ruling R-06 excludes from the sections have to
    survive on the row or the document cannot be assembled at all. They exist
    nowhere else to be derived from.
    """
    stored = context.store.get_skeleton(start(context))
    assert stored is not None
    assert stored.labels == LABELS
    assert stored.seed == SEED
    assert stored.created_at == FROZEN_NOW


def test_a_manifest_section_round_trips_through_the_store(
    context: ServiceContext, published: BlueprintView
) -> None:
    """The dotted section ids are legal keys, which M3's conformance suite forced."""
    stored = context.store.get_skeleton(start(context))
    assert stored is not None
    assert [section.id for section in stored.manifest] == list(SECTIONS)
    assert all(isinstance(section, Section) for section in stored.manifest)


def test_the_frozen_clock_is_the_only_timestamp_source(
    context: ServiceContext, published: BlueprintView, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-09: ``validated_at`` comes from the port, ``created_at`` from the author.

    The two timestamps differ on purpose. ``provenance.created_at`` is authored
    content and survives an export/import cycle unchanged, which is what
    ``dataset_find``'s ordering depends on; ``validated_at`` is when the service
    validated it.
    """
    skeleton_id = filled(context, dataset_document)
    stored = data(skeletons.submit(context, skeleton_id))["dataset"]
    assert stored["validated_at"] == "2026-09-08T12:00:00Z"
    assert stored["provenance"]["created_at"] == "2026-09-08T10:14:22Z"


def test_a_clock_at_the_fixtures_instant_reproduces_its_validated_at(
    tmp_path: Path, blueprint_document: dict[str, Any], dataset_document: dict[str, Any]
) -> None:
    """The one field the golden fixture pins that a frozen-at-noon clock cannot.

    Not a workaround: it is the evidence that ``validated_at`` is the clock's
    value and nothing else, which is what lets
    `test_worked_example_fill.py` compare the rest of the document byte for
    byte.
    """
    built = sqlite_context(
        tmp_path / "at-the-instant.db",
        clock=FrozenClock(datetime(2026, 9, 8, 10, 14, 22, tzinfo=UTC)),
    )
    try:
        blueprints.upsert(built, blueprint_document, publish=True)
        skeleton_id = filled(built, dataset_document)
        stored = data(skeletons.submit(built, skeleton_id))["dataset"]
        assert stored["validated_at"] == dataset_document["validated_at"]
    finally:
        if isinstance(built.store, SqlStore):
            built.store.dispose()


def test_the_stored_skeleton_is_a_model_not_a_dict(
    context: ServiceContext, published: BlueprintView
) -> None:
    """A guard on the store's row mapper, since M5 added two columns to it."""
    stored = context.store.get_skeleton(start(context))
    assert isinstance(stored, Skeleton)


def test_the_blueprint_a_skeleton_pins_is_the_one_it_was_started_against(
    context: ServiceContext, published: BlueprintView, blueprint_document: dict[str, Any]
) -> None:
    """A skeleton names ``{agent_id, bp_version}``, and the assembled document repeats it.

    So a blueprint published *after* the skeleton was started cannot change what
    the dataset claims to be authored against - which is what makes DS-001's
    check at submit meaningful rather than circular.
    """
    stored = context.store.get_skeleton(start(context))
    assert stored is not None
    assert (stored.agent_id, stored.bp_version) == (AGENT, VERSION)
    document = assemble(stored, UUID(int=2))
    assert document["blueprint"] == {"agent_id": AGENT, "version": VERSION}
    assert isinstance(Blueprint.model_validate(blueprint_document).version, str)


def test_the_golden_dataset_can_be_taken_apart_and_put_back_together(
    dataset_document: dict[str, Any],
) -> None:
    """The partition, checked directly, before any service call is involved.

    If this fails, ``sections_of`` and :data:`SECTION_FIELDS` disagree and every
    other test in this module is testing the wrong document.
    """
    parts = sections_of(dataset_document)
    rebuilt: dict[str, Any] = {}
    for section_id in SECTIONS:
        rebuilt.update(parts[section_id])
    assert set(rebuilt) == {field for fields in SECTION_FIELDS.values() for field in fields}
    assert all(rebuilt[field] == dataset_document[field] for field in rebuilt)


def test_load_document_reads_the_fixture_the_suite_thinks_it_does() -> None:
    """A guard against a renamed fixture making every assertion here vacuous."""
    assert load_document("datasets/priya-missing-docs.json")["seed"] == SEED
