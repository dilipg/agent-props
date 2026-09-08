"""M1's gate: every golden fixture round-trips through the models without loss.

The criterion is ruling R-08's, verbatim::

    Model.model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw

``exclude_unset`` is the load-bearing part. The golden fixtures omit many
optional fields - ``tool_name`` on three nodes, ``max_iterations`` on eight,
``fault`` and ``latency_hint_ms`` on most fixtures, ``input`` on ``complete``
and ``escalate`` and on every pool entry, ``revisions`` on two entities - and a
plain ``model_dump()`` would reintroduce every one of them as ``null``.
``exclude_none`` would not work either: ``condition: null`` is *present* on the
golden blueprint's five unconditional edges, and ``supersedes: null`` on both
datasets.
``exclude_unset`` keeps an explicitly-set null and drops an absent field, which
is exactly the distinction the fixtures draw.

Both tests are table-driven over the fixture directory, so a fixture added
later is covered without touching this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from agentprops.models import Blueprint, Dataset
from conftest import FIXTURES_DIR

BLUEPRINT_FIXTURES = sorted((FIXTURES_DIR / "blueprints").glob("*.json"))
DATASET_FIXTURES = sorted((FIXTURES_DIR / "datasets").glob("*.json"))


def load(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def flatten(document: Any, pointer: str = "") -> dict[str, Any]:
    """Every leaf of ``document``, keyed by RFC 6901 pointer.

    Only used to describe a failure. A pointer-level diff says "``/nodes/
    complete/input`` appeared, was absent in the fixture", which is the shape
    of every plausible round-trip bug here; a whole-document dict diff does
    not.
    """
    if isinstance(document, dict):
        flat: dict[str, Any] = {}
        for key, value in document.items():
            flat.update(flatten(value, f"{pointer}/{key}"))
        return flat
    if isinstance(document, list):
        flat = {}
        for index, value in enumerate(document):
            flat.update(flatten(value, f"{pointer}/{index}"))
        return flat
    return {pointer: document}


def describe_loss(raw: dict[str, Any], dumped: dict[str, Any]) -> str:
    before, after = flatten(raw), flatten(dumped)
    added = sorted(set(after) - set(before))
    dropped = sorted(set(before) - set(after))
    changed = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    return "\n".join(
        [
            f"added by the dump ({len(added)}): {added}",
            f"dropped by the dump ({len(dropped)}): {dropped}",
            f"changed ({len(changed)}): " + str([(p, before[p], after[p]) for p in changed]),
        ]
    )


def assert_round_trips(model: type[BaseModel], path: Path) -> None:
    raw = load(path)
    dumped = model.model_validate(raw).model_dump(mode="json", exclude_unset=True)
    assert dumped == raw, f"{path.name} did not round-trip:\n{describe_loss(raw, dumped)}"


def test_fixture_tables_are_not_empty() -> None:
    """Guard against a moved directory making the parametrised tests vacuous."""
    assert BLUEPRINT_FIXTURES, f"no blueprint fixtures under {FIXTURES_DIR / 'blueprints'}"
    assert DATASET_FIXTURES, f"no dataset fixtures under {FIXTURES_DIR / 'datasets'}"


@pytest.mark.parametrize("path", BLUEPRINT_FIXTURES, ids=lambda p: p.name)
def test_blueprint_round_trips(path: Path) -> None:
    assert_round_trips(Blueprint, path)


@pytest.mark.parametrize("path", DATASET_FIXTURES, ids=lambda p: p.name)
def test_dataset_round_trips(path: Path) -> None:
    assert_round_trips(Dataset, path)


@pytest.mark.parametrize("path", DATASET_FIXTURES, ids=lambda p: p.name)
def test_dataset_parses_into_the_expected_shape(path: Path) -> None:
    """A round trip alone cannot prove the document was *understood*.

    ``extra="forbid"`` means an unrecognised key raises rather than surviving
    the trip untouched, so a passing round trip already implies every key was
    claimed by a field. These assertions pin the few places where the shape
    the specification describes is not obvious from the JSON: the nested
    ``blueprint`` ref, the pool-versus-node split (ruling R-01), and the
    ``entity@revision`` grammar.
    """
    dataset = Dataset.model_validate(load(path))

    assert dataset.blueprint.agent_id == "location-onboarding"
    assert dataset.blueprint.version == "1.0.0"

    # Ruling R-01: the loop node's fixtures live in `pools`, not in `nodes`.
    assert "request_docs" in dataset.pools
    assert "request_docs" not in dataset.nodes
    assert len(dataset.nodes) == 8

    # Every fixture carries entity_refs, and the revision grammar is preserved.
    for fixture in dataset.nodes.values():
        assert isinstance(fixture.entity_refs, list)
    revision_refs = [
        ref for fixture in dataset.nodes.values() for ref in fixture.entity_refs if "@" in ref
    ]
    for ref in revision_refs:
        entity_id, revision_id = ref.split("@", 1)
        entity = dataset.entities[entity_id]
        assert entity.revisions is not None
        assert revision_id in entity.revisions
