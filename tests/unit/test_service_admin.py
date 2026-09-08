"""The three admin reads, and the two counting numbers that differ on purpose.

``store_status`` counts including archived datasets (ruling R-05: a store-health
number); ``label_vocabulary`` and ``agent_list`` count excluding them (discovery
numbers, built from ``find_datasets``).
:func:`test_the_two_counting_numbers_disagree_about_an_archived_dataset` pins
that difference, because a future reader will otherwise read it as a bug.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from agentprops.models import Dataset
from agentprops.service import ServiceContext, admin, blueprints, datasets
from agentprops.service.envelope import AP_NOT_FOUND
from conftest import BLUEPRINT_FIXTURE, load_document
from envelopes import data, findings


@pytest.fixture
def seeded(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
    other_dataset_document: dict[str, Any],
) -> ServiceContext:
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    context.store.put_dataset(Dataset.model_validate(other_dataset_document))
    return context


def test_store_status_reports_the_backend_and_the_counts(seeded: ServiceContext) -> None:
    status = data(admin.store_status(seeded))["status"]
    assert status["backend"] == "sqlite"
    assert status["healthy"] is True
    assert status["counts"] == {"blueprints": 1, "datasets": 2, "runs": 0}


def test_store_status_on_an_empty_store_is_still_healthy(context: ServiceContext) -> None:
    """``healthy`` is liveness, not a verdict about the data."""
    status = data(admin.store_status(context))["status"]
    assert status["healthy"] is True
    assert status["counts"]["datasets"] == 0


def test_label_vocabulary_reports_the_schema_and_a_count_per_value(
    seeded: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    vocabulary = data(admin.label_vocabulary(seeded, "location-onboarding", None))["vocabulary"]
    assert vocabulary["version"] == "1.0.0"
    assert vocabulary["label_schema"] == blueprint_document["label_schema"]
    assert vocabulary["dataset_count"] == 2
    assert vocabulary["counts"]["persona"]["multi-unit-operator"] == 1
    assert vocabulary["counts"]["persona"]["corporate-admin"] == 0
    assert sum(vocabulary["counts"]["scenario"].values()) == 2


def test_label_vocabulary_keeps_the_blueprints_declaration_order(
    seeded: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Deterministic *and* the order a human wrote, rather than a sort."""
    declared = blueprint_document["label_schema"]["dimensions"]
    vocabulary = data(admin.label_vocabulary(seeded, "location-onboarding", None))["vocabulary"]
    assert list(vocabulary["counts"]) == list(declared)
    for dimension, values in declared.items():
        assert list(vocabulary["counts"][dimension]) == values


def test_repeated_identical_label_queries_are_byte_identical(seeded: ServiceContext) -> None:
    """The acceptance criterion, on the tool that names labels in its own title."""
    first = json.dumps(admin.label_vocabulary(seeded, "location-onboarding", None).model_dump())
    second = json.dumps(admin.label_vocabulary(seeded, "location-onboarding", None).model_dump())
    assert first == second


def test_label_vocabulary_takes_an_explicit_version(
    seeded: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    newer = load_document(BLUEPRINT_FIXTURE)
    newer["version"] = "2.0.0"
    newer["label_schema"]["dimensions"]["region"] = ["south"]
    blueprints.upsert(seeded, newer, publish=True)
    old = data(admin.label_vocabulary(seeded, "location-onboarding", "1.0.0"))["vocabulary"]
    new = data(admin.label_vocabulary(seeded, "location-onboarding", "2.0.0"))["vocabulary"]
    assert "region" not in old["counts"]
    assert new["counts"]["region"] == {"south": 0}
    assert new["dataset_count"] == 0, "counts are scoped to the blueprint version"


def test_label_vocabulary_reports_ap_004_for_an_unknown_agent(context: ServiceContext) -> None:
    envelope = admin.label_vocabulary(context, "no-such-agent", None)
    assert envelope.ok is False
    assert [finding.rule for finding in findings(envelope)] == [AP_NOT_FOUND]


def test_label_vocabulary_counts_every_version_of_a_lineage_once(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """One row per lineage (ruling R-38), so an edit does not inflate a count."""
    edited = copy.deepcopy(dataset_document)
    edited["seed"] = 1234
    seeded.store.put_dataset(Dataset.model_validate(edited))
    vocabulary = data(admin.label_vocabulary(seeded, "location-onboarding", None))["vocabulary"]
    assert vocabulary["dataset_count"] == 2


def test_agent_list_reports_versions_in_semver_order(
    seeded: ServiceContext,
) -> None:
    for version in ("1.9.0", "1.10.0"):
        document = load_document(BLUEPRINT_FIXTURE)
        document["version"] = version
        blueprints.upsert(seeded, document, publish=True)
    agents = data(admin.agents(seeded))["agents"]
    assert len(agents) == 1
    assert agents[0]["versions"] == ["1.0.0", "1.9.0", "1.10.0"]
    assert agents[0]["dataset_count"] == 2


def test_agent_list_includes_a_draft_only_agent(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    blueprint_document["agent_id"] = "draft-only"
    blueprints.upsert(context, blueprint_document, publish=False)
    agents = data(admin.agents(context))["agents"]
    assert [row["agent_id"] for row in agents] == ["draft-only"]
    assert agents[0]["dataset_count"] == 0


def test_agent_list_is_ordered_by_agent_id(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    for agent_id in ("zeta-agent", "alpha-agent"):
        document = load_document(BLUEPRINT_FIXTURE)
        document["agent_id"] = agent_id
        blueprints.upsert(context, document, publish=True)
    agents = data(admin.agents(context))["agents"]
    assert [row["agent_id"] for row in agents] == ["alpha-agent", "zeta-agent"]


def test_agent_list_on_an_empty_store_is_an_empty_list(context: ServiceContext) -> None:
    assert data(admin.agents(context))["agents"] == []


def test_the_two_counting_numbers_disagree_about_an_archived_dataset(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-05, pinned: health counts archives, discovery hides them."""
    datasets.archive(seeded, dataset_document["id"])
    assert data(admin.store_status(seeded))["status"]["counts"]["datasets"] == 2
    assert data(admin.agents(seeded))["agents"][0]["dataset_count"] == 1
    vocabulary = data(admin.label_vocabulary(seeded, "location-onboarding", None))["vocabulary"]
    assert vocabulary["dataset_count"] == 1
