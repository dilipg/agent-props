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

The fixture tests are table-driven over the fixture directory, so a fixture
added later is covered without touching this file. Assertions that are true of
*these* fixtures rather than of any dataset live in the one test named after
them.

The last section pins ruling R-24: the model is the timestamp canonicaliser, so
"round-trips without loss" means the model's canonical output, not the author's
bytes. Both golden fixtures happen to use the one spelling that survives
unchanged, so without that section the property would look like a model
guarantee when it is really a fixture coincidence.
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
    claimed by a field. What is left to pin is the shape the specification
    describes but the JSON does not make obvious.

    Everything asserted here is true of *any* dataset for *any* blueprint, so
    that a fixture added later for a different blueprint is covered rather than
    failing spuriously. Assertions about the two golden fixtures specifically
    are in :func:`test_the_golden_location_onboarding_datasets` below.
    """
    dataset = Dataset.model_validate(load(path))

    # Ruling R-01: a pool node's fixtures live in `pools`, never in `nodes`,
    # so the two key sets must be disjoint for any dataset.
    assert set(dataset.pools) & set(dataset.nodes) == set()

    # Every fixture carries entity_refs, and the `entity@revision` grammar
    # resolves against the declared cast.
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


def test_the_golden_location_onboarding_datasets() -> None:
    """Counts and ids specific to the two fixtures `worked-example.md` ships.

    Not parametrised over the directory, because a fixture for a different
    blueprint would have a different node census and a different agent_id. The
    two filenames are named explicitly so this test is about *them*.
    """
    for name in ("priya-missing-docs.json", "arun-escalated.json"):
        dataset = Dataset.model_validate(load(FIXTURES_DIR / "datasets" / name))

        assert dataset.blueprint.agent_id == "location-onboarding", name
        assert dataset.blueprint.version == "1.0.0", name

        # The blueprint has nine nodes; `request_docs` is the one `pool: true`
        # node, so eight fixtures in `nodes` and one pool (ruling R-01).
        assert "request_docs" in dataset.pools, name
        assert "request_docs" not in dataset.nodes, name
        assert len(dataset.nodes) == 8, name
        assert len(dataset.pools) == 1, name


# --------------------------------------------------------------------------- #
# Ruling R-24: the model is the timestamp canonicaliser.
# --------------------------------------------------------------------------- #

#: Spelling handed to the model, and the canonical form it comes back as.
#: Every entry is a valid ISO 8601 instant, and every entry parses. Six of the
#: eight differ from their input, which is the whole point: R-08's criterion is
#: satisfied against the model's canonical output, not against the author's
#: bytes. Both golden fixtures happen to use the first spelling.
TIMESTAMP_CANONICAL_FORMS = [
    ("2026-09-08T10:14:22Z", "2026-09-08T10:14:22Z"),
    ("2026-09-08T10:14:22+00:00", "2026-09-08T10:14:22Z"),
    ("2026-09-08T10:14:22.000Z", "2026-09-08T10:14:22Z"),
    ("2026-09-08T10:14:22.500Z", "2026-09-08T10:14:22.500000Z"),
    ("2026-09-08T10:14:22.5Z", "2026-09-08T10:14:22.500000Z"),
    ("2026-09-08T10:14:22.123456Z", "2026-09-08T10:14:22.123456Z"),
    ("2026-09-08 10:14:22Z", "2026-09-08T10:14:22Z"),
    # A non-UTC offset is preserved rather than normalised to UTC.
    ("2026-09-08T15:44:22+05:30", "2026-09-08T15:44:22+05:30"),
]


def dataset_with_created_at(spelling: str) -> dict[str, Any]:
    document = load(FIXTURES_DIR / "datasets" / "priya-missing-docs.json")
    document["provenance"]["created_at"] = spelling
    document["validated_at"] = spelling
    return document


@pytest.mark.parametrize(("spelling", "canonical"), TIMESTAMP_CANONICAL_FORMS, ids=lambda v: v)
def test_timestamp_spelling_parses_and_canonicalises(spelling: str, canonical: str) -> None:
    """Ruling R-24: every valid spelling parses, and its canonical form is pinned.

    Stated as a contract rather than derived from a failure at M5, where an LLM
    fills the provenance section and will plausibly emit fractional seconds or
    ``+00:00``, or at M7, where ``dataset_export`` byte-stability across three
    backends depends on it.
    """
    dumped = Dataset.model_validate(dataset_with_created_at(spelling)).model_dump(
        mode="json", exclude_unset=True
    )
    assert dumped["provenance"]["created_at"] == canonical
    assert dumped["validated_at"] == canonical


@pytest.mark.parametrize(("spelling", "canonical"), TIMESTAMP_CANONICAL_FORMS, ids=lambda v: v)
def test_timestamp_canonical_form_is_a_fixed_point(spelling: str, canonical: str) -> None:
    """Canonicalising twice equals canonicalising once.

    This is what makes the contract usable: once a document has been through
    the model, it round-trips byte-for-byte forever after. Export and re-import
    are therefore stable even though the *first* pass may rewrite the author's
    spelling.
    """
    once = Dataset.model_validate(dataset_with_created_at(spelling)).model_dump(
        mode="json", exclude_unset=True
    )
    twice = Dataset.model_validate(once).model_dump(mode="json", exclude_unset=True)
    assert once == twice
    assert twice["provenance"]["created_at"] == canonical


def test_all_timestamp_spellings_denote_the_same_instant() -> None:
    """Canonicalisation must not change *when* something happened.

    Guards the mapping table above against an entry that is merely a different
    time rather than a different spelling. The non-UTC entry is excluded from
    the instant comparison only in the sense that it is the same instant
    expressed in another offset - which is exactly what is asserted.
    """
    instants = {
        Dataset.model_validate(dataset_with_created_at(spelling)).provenance.created_at
        for spelling, _ in TIMESTAMP_CANONICAL_FORMS
        if not spelling.endswith(("22.500Z", "22.5Z", "22.123456Z"))
    }
    assert len(instants) == 1, f"these spellings denote different instants: {instants}"
