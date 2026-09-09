"""`service/examples.py`: the two "which document is the example" decisions.

Ruling R-76 says a resource is "a known-good document in **this** store", which
makes *which* document a real decision rather than an implementation detail -
it is what a prompt tells a caller to imitate. Both decisions have one site in
`service/examples.py`, and this file tests them where a protocol round trip
cannot reach cheaply: a store with two agents, three versions, a copy-on-write
edit and an archive.

`test_prompts_contract.py` covers the same functions over
``prompts/get``/``resources/read``. This file covers the *selection*, and the
cases below are the ones `DECISIONS.md` reasons about - so each end of each
boundary has a test rather than an argument.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agentprops.models import Dataset
from agentprops.service import ServiceContext, blueprints, examples
from conftest import BLUEPRINT_FIXTURE, load_document

AGENT = "location-onboarding"


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


def publish(context: ServiceContext, agent_id: str, version: str) -> None:
    document = load_document(BLUEPRINT_FIXTURE)
    document["agent_id"] = agent_id
    document["version"] = version
    blueprints.upsert(context, document, publish=True)


# ------------------------------------------------------- which blueprint


def test_published_keeps_the_store_s_semver_order(seeded: ServiceContext) -> None:
    """Ruling R-35's order, not re-sorted here.

    A Python ``sorted()`` would put ``1.10.0`` before ``1.9.0``, so this asserts
    the store's order survives the grouping rather than that some order exists.
    """
    for version in ("1.9.0", "1.10.0"):
        publish(seeded, AGENT, version)
    assert examples.published(seeded) == {AGENT: ["1.0.0", "1.9.0", "1.10.0"]}


def test_the_example_blueprint_is_the_latest_published_version(
    seeded: ServiceContext,
) -> None:
    """ "Latest published" reuses the store's own resolution rather than picking.

    With ``1.10.0`` published, ``1.0.0`` is no longer the example even though it
    is first in the version list - which is the difference between "the first
    row" and "what ``get_blueprint(agent, None)`` means".
    """
    publish(seeded, AGENT, "1.10.0")
    chosen = examples.example_blueprint(seeded, "", "")
    assert chosen is not None
    assert (chosen.agent_id, chosen.version) == (AGENT, "1.10.0")


def test_the_example_blueprint_is_the_first_agent_in_the_store_s_order(
    seeded: ServiceContext,
) -> None:
    """One decision site: :func:`examples.example_agent_id` picks, nothing else does.

    ``aaa-agent`` sorts before ``location-onboarding``, so it becomes the
    example - which is what makes the choice a property of the store's order
    rather than of who happened to be seeded first.
    """
    publish(seeded, "aaa-agent", "1.0.0")
    assert examples.example_agent_id(seeded) == "aaa-agent"
    chosen = examples.example_blueprint(seeded, "", "")
    assert chosen is not None
    assert chosen.agent_id == "aaa-agent"


def test_a_draft_is_never_the_example(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Both ends: a draft-only store has no example, and ``blueprint_get`` still serves it.

    Without the second assertion this would pass against a store that had lost
    the blueprint altogether, and would prove nothing about the published-only
    rule.
    """
    blueprints.upsert(context, blueprint_document, publish=False)
    assert examples.published(context) == {}
    assert examples.example_agent_id(context) == ""
    assert examples.example_blueprint(context, "", "") is None
    assert examples.example_blueprint(context, AGENT, "1.0.0") is None
    assert context.store.get_blueprint(AGENT, "1.0.0") is not None


def test_a_published_agent_id_with_no_such_version_is_a_miss(
    seeded: ServiceContext,
) -> None:
    assert examples.example_blueprint(seeded, AGENT, "9.9.9") is None
    body = examples.blueprint_body(seeded, AGENT, "9.9.9")
    assert body["available"] is False
    assert body["version"] == "9.9.9", "the note must name what the caller asked for"


# --------------------------------------------------------- which dataset


def test_the_example_dataset_is_the_oldest_lineage_at_its_latest_version(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-38's lineage grain and ruling R-09's ``created_at`` order, together.

    ``priya-missing-docs`` has the earlier ``provenance.created_at`` of the two
    golden datasets, and a copy-on-write edit gives it a version 2. The example
    is therefore that lineage, at version 2 - not the edit as a separate row and
    not the newer lineage.
    """
    edited = copy.deepcopy(dataset_document)
    edited["seed"] = 4321
    seeded.store.put_dataset(Dataset.model_validate(edited))

    chosen = examples.example_dataset(seeded, "")
    assert chosen is not None
    assert str(chosen.id) == dataset_document["id"]
    assert chosen.version == 2
    assert chosen.seed == 4321


def test_the_example_dataset_never_falls_back_to_another_agent(
    seeded: ServiceContext,
) -> None:
    """The rejected alternative, asserted.

    Pinning the per-agent example to some other agent's dataset would answer a
    question the caller did not ask. Both halves: the seeded agent has one, the
    second agent does not, and asking for the second gets ``None``.
    """
    publish(seeded, "second-agent", "1.0.0")
    assert examples.example_dataset(seeded, AGENT) is not None
    assert examples.example_dataset(seeded, "second-agent") is None


def test_the_store_wide_example_dataset_is_not_pinned_to_the_example_blueprint(
    seeded: ServiceContext,
) -> None:
    """The independence decision, and why it was made that way.

    ``aaa-agent`` becomes the example *blueprint* because it sorts first, and it
    has no datasets. If the store-wide example dataset were pinned to the
    example blueprint's agent it would now report "unavailable" while the store
    holds two - a false unavailable, which is the failure this milestone is
    about. So it stays the oldest lineage in the store.
    """
    publish(seeded, "aaa-agent", "1.0.0")
    assert examples.example_agent_id(seeded) == "aaa-agent"
    assert examples.example_dataset(seeded, "aaa-agent") is None

    body = examples.dataset_body(seeded, "")
    assert body["available"] is True
    assert body["agent_id"] == AGENT, "the body must name the agent it actually belongs to"


def test_an_archived_lineage_is_not_the_example(seeded: ServiceContext) -> None:
    """``find_datasets`` excludes archives, and ``get_dataset`` does not.

    The asymmetry is deliberate (`service/datasets.py`), so the example has to
    inherit the discovery side. Both ends: archiving moves the example, and the
    archived document is still readable by id.
    """
    first = examples.example_dataset(seeded, "")
    assert first is not None
    seeded.store.set_archived(str(first.id), True)

    second = examples.example_dataset(seeded, "")
    assert second is not None
    assert second.id != first.id
    assert seeded.store.get_dataset(str(first.id), None) is not None


def test_the_catalogue_dataset_count_excludes_archives(seeded: ServiceContext) -> None:
    """A discovery number, like ``label_vocabulary``'s and unlike ``store_status``'s."""
    assert examples.catalogue(seeded)["agents"][0]["dataset_count"] == 2
    chosen = examples.example_dataset(seeded, "")
    assert chosen is not None
    seeded.store.set_archived(str(chosen.id), True)
    assert examples.catalogue(seeded)["agents"][0]["dataset_count"] == 1


# ------------------------------------------------------------------ URIs


def test_a_uri_escapes_a_separator_in_an_id() -> None:
    """``safe=""`` so an id containing ``/`` cannot invent a path segment.

    Not hypothetical protection: an agent id is a free string, and a template
    variable that swallowed a ``/`` would match a different template than the
    one that built it.
    """
    assert examples.blueprint_uri("a/b", "1.0.0") == "agentprops://blueprint/a%2Fb/1.0.0"
    assert examples.agent_example_dataset_uri("a b") == "agentprops://dataset/a%20b/example"


def test_every_uri_the_catalogue_names_is_built_by_this_module(
    seeded: ServiceContext,
) -> None:
    """No URI is spelled twice: the catalogue's strings are the builders' output."""
    agent = examples.catalogue(seeded)["agents"][0]
    assert agent["blueprints"] == [examples.blueprint_uri(AGENT, "1.0.0")]
    assert agent["example_dataset"] == examples.agent_example_dataset_uri(AGENT)
