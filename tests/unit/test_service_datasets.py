"""The five dataset service functions M4 owns.

M4 has no dataset write path - ``dataset_submit`` is M5's - so these tests seed
through ``Store.put_dataset`` directly. That is the honest way to do it: the
Protocol is the seam the service reaches storage through either way, and
inventing a write here to test a read would be building M5 early.

Two properties get the most attention, because both are one line from being
wrong in the opposite direction:

- an **archived** dataset is still returned by ``dataset_get``, with a warning,
  and is *not* returned by ``dataset_find``;
- nothing in the service re-sorts what the store returned, so the
  ``(created_at, id)`` order and the byte-identical output survive.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from agentprops.models import Dataset, DatasetQuery
from agentprops.service import ServiceContext, blueprints, datasets
from agentprops.service.datasets import DEFAULT_FIND_LIMIT, paginate
from agentprops.service.envelope import AP_ARGUMENT, AP_NOT_FOUND
from agentprops.storage import RecordNotFoundError
from conftest import DATASET_FIXTURE, load_document, load_text
from envelopes import codes, data, findings, rules, warnings_of


@pytest.fixture
def seeded(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
    other_dataset_document: dict[str, Any],
) -> ServiceContext:
    """A store holding the published blueprint and both golden datasets.

    `priya` is written first and `arun` second; `arun`'s
    ``provenance.created_at`` is the later of the two, which is what makes the
    ordering assertions mean something.
    """
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    context.store.put_dataset(Dataset.model_validate(other_dataset_document))
    return context


# --------------------------------------------------------------------- find


def test_find_returns_one_summary_per_lineage_ordered_by_created_at(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    rows = data(datasets.find(seeded, DatasetQuery()))["datasets"]
    assert [row["id"] for row in rows] == [
        dataset_document["id"],
        load_document("datasets/arun-escalated.json")["id"],
    ]
    assert all("narrative_excerpt" in row for row in rows)
    assert all("nodes" not in row for row in rows), "a summary never carries a fixture"


def test_find_returns_one_row_per_lineage_after_an_edit(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-38: copy-on-write adds a version, not a row."""
    edited = copy.deepcopy(dataset_document)
    edited["narrative"] = dataset_document["narrative"] + " Edited."
    seeded.store.put_dataset(Dataset.model_validate(edited))
    rows = data(datasets.find(seeded, DatasetQuery()))["datasets"]
    assert len(rows) == 2
    assert rows[0]["version"] == 2


def test_find_filters_by_labels_author_and_query(seeded: ServiceContext) -> None:
    by_label = data(datasets.find(seeded, DatasetQuery(labels={"tier": "regional"})))["datasets"]
    assert len(by_label) == 1
    by_author = data(datasets.find(seeded, DatasetQuery(author="pnair")))["datasets"]
    assert [row["author"]["handle"] for row in by_author] == ["pnair"]
    by_query = data(datasets.find(seeded, DatasetQuery(q="FSSAI")))["datasets"]
    assert by_query, "q is a case-folded substring match over title and intent"


def test_find_excludes_archived_datasets(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    datasets.archive(seeded, dataset_document["id"])
    rows = data(datasets.find(seeded, DatasetQuery()))["datasets"]
    assert [row["id"] for row in rows] == [load_document("datasets/arun-escalated.json")["id"]]


def test_repeated_identical_find_calls_are_byte_identical(seeded: ServiceContext) -> None:
    """The determinism criterion. Bytes, not row counts."""
    query = DatasetQuery(labels={"scenario": "missing-documents"})
    first = json.dumps(datasets.find(seeded, query).model_dump(mode="json"))
    second = json.dumps(datasets.find(seeded, query).model_dump(mode="json"))
    assert first == second


def test_paginate_supplies_the_default_limit(seeded: ServiceContext) -> None:
    assert paginate(DatasetQuery()).limit == DEFAULT_FIND_LIMIT
    assert paginate(DatasetQuery()).offset == 0


def test_paginate_honours_an_explicit_limit_larger_than_the_default() -> None:
    assert paginate(DatasetQuery(limit=5000)).limit == 5000


def test_paginate_clamps_a_negative_limit_and_offset_rather_than_refusing() -> None:
    """The service never gates, and ``LIMIT -1`` means different things per backend."""
    clamped = paginate(DatasetQuery(limit=-3, offset=-9))
    assert clamped.limit == 0
    assert clamped.offset == 0


def test_a_zero_limit_returns_no_rows(seeded: ServiceContext) -> None:
    assert data(datasets.find(seeded, DatasetQuery(limit=0)))["datasets"] == []


def test_paginate_leaves_every_other_filter_alone() -> None:
    query = DatasetQuery(agent_id="a", labels={"t": "v"}, author="h", q="x", blueprint_version="1")
    assert paginate(query).model_dump(exclude={"limit", "offset"}) == query.model_dump(
        exclude={"limit", "offset"}
    )


# ---------------------------------------------------------------------- get


def test_get_returns_the_full_dataset_as_submitted(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    envelope = datasets.get(seeded, dataset_document["id"], None)
    assert envelope.ok is True
    assert data(envelope)["dataset"] == dataset_document
    assert warnings_of(envelope) == []


def test_get_reaches_an_earlier_version_by_number(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    edited = copy.deepcopy(dataset_document)
    edited["narrative"] = dataset_document["narrative"] + " Edited."
    seeded.store.put_dataset(Dataset.model_validate(edited))
    first = data(datasets.get(seeded, dataset_document["id"], 1))["dataset"]
    latest = data(datasets.get(seeded, dataset_document["id"], None))["dataset"]
    assert first["narrative"] == dataset_document["narrative"]
    assert latest["version"] == 2


def test_get_returns_an_archived_dataset_with_a_warning(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ground rule 3, in the one place it is most tempting to gate."""
    datasets.archive(seeded, dataset_document["id"])
    envelope = datasets.get(seeded, dataset_document["id"], None)
    assert envelope.ok is True
    assert data(envelope)["dataset"]["archived"] is True
    assert codes(envelope) == ["dataset_archived"]


def test_get_reports_ap_004_for_an_unknown_id(seeded: ServiceContext) -> None:
    envelope = datasets.get(seeded, "3f8c1a20-0000-4000-8000-0000000000ff", None)
    assert envelope.ok is False
    assert rules(envelope) == [AP_NOT_FOUND]


def test_get_reports_ap_004_for_a_malformed_id(seeded: ServiceContext) -> None:
    """A malformed id is a miss, never an exception - the store's read contract."""
    assert rules(datasets.get(seeded, "not-a-uuid", None)) == [AP_NOT_FOUND]


def test_get_reports_ap_004_for_a_version_that_does_not_exist(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    envelope = datasets.get(seeded, dataset_document["id"], 99)
    assert rules(envelope) == [AP_NOT_FOUND]
    assert findings(envelope)[0].context["version"] == 99


# --------------------------------------------------------- archive, restore


def test_archive_and_restore_flip_the_lineage_and_return_the_summary(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    archived = datasets.archive(seeded, dataset_document["id"])
    assert archived.ok is True
    assert data(archived)["summary"]["archived"] is True
    restored = datasets.restore(seeded, dataset_document["id"])
    assert data(restored)["summary"]["archived"] is False
    assert len(data(datasets.find(seeded, DatasetQuery()))["datasets"]) == 2


def test_archive_applies_to_every_version_of_the_lineage(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-34: the flag belongs to the lineage, not to a version."""
    edited = copy.deepcopy(dataset_document)
    edited["seed"] = 999
    seeded.store.put_dataset(Dataset.model_validate(edited))
    datasets.archive(seeded, dataset_document["id"])
    for version in (1, 2):
        assert data(datasets.get(seeded, dataset_document["id"], version))["dataset"]["archived"]


def test_archiving_an_unknown_id_is_an_envelope_not_an_exception(
    seeded: ServiceContext,
) -> None:
    """The raising side of ``set_archived``'s ``RecordNotFoundError`` guard."""
    envelope = datasets.archive(seeded, "3f8c1a20-0000-4000-8000-0000000000ff")
    assert envelope.ok is False
    assert rules(envelope) == [AP_NOT_FOUND]


def test_a_store_that_raises_record_not_found_is_translated(
    seeded: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard, executed against a store forced into raising."""

    def refuse(dataset_id: str, archived: bool) -> None:
        raise RecordNotFoundError("no such dataset")

    monkeypatch.setattr(seeded.store, "set_archived", refuse)
    assert rules(datasets.restore(seeded, "anything")) == [AP_NOT_FOUND]


# ----------------------------------------------------------------- validate


def test_validate_passes_the_golden_dataset_and_stores_nothing(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    envelope = datasets.validate(seeded, dataset_document)
    assert envelope.ok is True
    assert findings(envelope) == []


def test_validate_reports_ds_001_when_no_blueprint_is_published(
    context: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-11's resolver, wired to a real store: one id, not twenty (R-26)."""
    envelope = datasets.validate(context, dataset_document)
    assert envelope.ok is False
    assert rules(envelope) == ["DS-001"]


def test_validate_reports_ds_031_for_a_supersedes_that_names_nothing(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """The resolver's second lookup, against a store that really has the datasets."""
    dataset_document["provenance"]["supersedes"] = "3f8c1a20-0000-4000-8000-0000000000ff"
    assert rules(datasets.validate(seeded, dataset_document)) == ["DS-031"]


def test_validate_accepts_a_supersedes_that_does_exist(
    seeded: ServiceContext, dataset_document: dict[str, Any], other_dataset_document: dict[str, Any]
) -> None:
    other_dataset_document["provenance"]["supersedes"] = dataset_document["id"]
    assert datasets.validate(seeded, other_dataset_document).ok is True


def test_validate_reports_ds_013_only_through_the_raw_text_path(
    seeded: ServiceContext,
) -> None:
    """Ruling R-20 end to end, through the service function the tool calls."""
    text = load_text(DATASET_FIXTURE).replace(
        '"labels": {', '"labels": {"persona": "corporate-admin",', 1
    )
    from_text = datasets.validate(seeded, None, text)
    assert from_text.ok is False
    assert rules(from_text) == ["DS-013"]
    assert findings(from_text)[0].pointer == "/labels/persona"

    assert datasets.validate(seeded, json.loads(text)).ok is True


def test_validate_of_a_non_object_is_an_error_envelope(seeded: ServiceContext) -> None:
    assert rules(datasets.validate(seeded, 42)) == [AP_ARGUMENT]


def test_validate_reports_a_warning_with_ok_true(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ruling R-13: DS-032 is a warning, so the document is servable."""
    dataset_document["provenance"]["intent"] = dataset_document["expected"]["rationale"]
    envelope = datasets.validate(seeded, dataset_document)
    assert envelope.ok is True
    assert "DS-032" in rules(envelope)
