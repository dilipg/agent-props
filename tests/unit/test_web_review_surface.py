"""M9's clauses 2 and 3 on the **service** side, over a real MCP session.

The web app's own suite asserts that a reviewer's keystrokes reach
`dataset_find` as `q` and `author`, and that the row that comes back is
rendered with everything clause 3 names. It cannot assert that the *service*
answers those two filters correctly, because it talks to a stub. This file is
the other half: the same two filters, against a store holding both golden
datasets, through the same in-memory MCP client every other tool test uses.

Clause 2 is an assertion about real data, so it is checked as one
--------------------------------------------------------------------

The brief is explicit that "a reviewer can find the Priya dataset by searching
'repeat operator' and by filtering `author=pnair`" is a claim about the golden
fixture, to be reported rather than adjusted if it turns out false. It is true:
`priya-missing-docs.json` carries "repeat operator" in `provenance.intent` and
`pnair` in `provenance.author.handle`.

The half that makes it worth testing is **discrimination**.
`arun-escalated.json` carries neither - its handle is `claude-code` and its
intent never says "repeat operator" - so each filter returning exactly one row
out of two is evidence. A filter that returned everything would pass a
"contains Priya" assertion and prove nothing, which is why every assertion here
names the **exact** row set.

Clause 3, and what a test can honestly claim about it
-----------------------------------------------------

"The list shows enough to judge relevance without a detail fetch" is a design
claim. What is checkable is the two facts underneath it, and this file checks
one of them: **every field the brief names is present on the `dataset_find` row
itself**, at full length, so no second call is needed to obtain any of them.
`web/src/components/DatasetList.test.tsx` checks the other - that the app makes
exactly one call to render the list.

Neither reaches "enough for a human to judge". If a reviewer needs a fifth
field, both tests still pass. That is the limit of the pair, stated rather than
implied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest

from agentprops.service import ServiceContext
from toolclient import invoke

FIXTURES: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures"
BLUEPRINT: Final[Path] = FIXTURES / "blueprints" / "location-onboarding-1.0.0.json"
PRIYA_FILE: Final[Path] = FIXTURES / "datasets" / "priya-missing-docs.json"
ARUN_FILE: Final[Path] = FIXTURES / "datasets" / "arun-escalated.json"

#: The two strings the acceptance clause names, verbatim.
SEARCH: Final[str] = "repeat operator"
HANDLE: Final[str] = "pnair"

#: What contracts 2.2.1 promises on every `dataset_find` row, and what M9's
#: clause 3 depends on being there. Read as a set so a *removed* field fails as
#: loudly as a renamed one.
SUMMARY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "version",
        "title",
        "intent",
        "labels",
        "author",
        "blueprint",
        "narrative_excerpt",
        "archived",
        "created_at",
    }
)


def data(envelope: dict[str, Any]) -> dict[str, Any]:
    """A success envelope's payload object.

    A local helper rather than `tests/envelopes.py`'s, which narrows a Pydantic
    ``Reply``; over a real MCP session the envelope arrives as a plain dict.
    """
    assert envelope["ok"] is True, f"expected a success envelope, got {envelope!r}"
    payload: dict[str, Any] = envelope["data"]
    return payload


def load(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


PRIYA: Final[dict[str, Any]] = load(PRIYA_FILE)
ARUN: Final[dict[str, Any]] = load(ARUN_FILE)


def bundle(*datasets: dict[str, Any]) -> dict[str, Any]:
    """The bundle `web/src/mcp/tools.ts::datasetSave` builds, for these datasets.

    `blueprints` is empty for the reason that module records: import validates
    against a resolver answering from the receiving store **plus** the bundle,
    so a dataset whose blueprint is already published here satisfies DS-001
    without the bundle re-carrying an immutable version.
    """
    return {
        "format": "agentprops.bundle",
        "format_version": 1,
        "agent_id": PRIYA["blueprint"]["agent_id"],
        "blueprints": [],
        "datasets": list(datasets),
    }


@pytest.fixture
async def library(context: ServiceContext) -> ServiceContext:
    """A store holding the published blueprint and both golden datasets.

    Seeded through the **web app's own write path** - `blueprint_upsert` then
    `dataset_import` - rather than through ``Store.put_dataset``, because that
    is the path clause 5 constrains and a fixture that bypassed it would leave
    the copy-on-write assertions below testing a store this app never writes.
    """
    published = await invoke(context, "blueprint_upsert", blueprint=load(BLUEPRINT), publish=True)
    assert published["ok"] is True, published
    imported = await invoke(context, "dataset_import", bundle=bundle(PRIYA, ARUN))
    assert imported["ok"] is True, imported
    return context


async def titles(context: ServiceContext, **arguments: Any) -> list[str]:
    """`dataset_find`'s rows, as titles, so an assertion reads as a row set."""
    reply = await invoke(context, "dataset_find", **arguments)
    assert reply["ok"] is True, reply
    rows: list[dict[str, Any]] = data(reply)["datasets"]
    return [str(row["title"]) for row in rows]


# ------------------------------------------------------------------- the data


def test_the_golden_fixture_carries_the_strings_the_clause_names() -> None:
    """Clause 2's premise, checked before anything relies on it."""
    assert SEARCH in PRIYA["provenance"]["intent"]
    assert PRIYA["provenance"]["author"]["handle"] == HANDLE


def test_the_other_golden_dataset_carries_neither() -> None:
    """The discrimination half. Without it, every filter test below is vacuous."""
    assert SEARCH not in ARUN["provenance"]["intent"]
    assert SEARCH not in ARUN["provenance"]["title"]
    assert ARUN["provenance"]["author"]["handle"] != HANDLE


# ---------------------------------------------------------------- the filters


async def test_the_library_holds_exactly_two_datasets(library: ServiceContext) -> None:
    """The unfiltered baseline, so a filtered result is a narrowing."""
    assert sorted(await titles(library)) == sorted(
        [PRIYA["provenance"]["title"], ARUN["provenance"]["title"]]
    )


async def test_searching_repeat_operator_finds_exactly_the_priya_dataset(
    library: ServiceContext,
) -> None:
    """Clause 2, first half. Exactly one row, and it is hers."""
    assert await titles(library, q=SEARCH) == [PRIYA["provenance"]["title"]]


async def test_filtering_author_pnair_finds_exactly_the_priya_dataset(
    library: ServiceContext,
) -> None:
    """Clause 2, second half. `author` filters on `provenance.author.handle`."""
    assert await titles(library, author=HANDLE) == [PRIYA["provenance"]["title"]]


async def test_the_search_is_a_case_folded_substring_as_ruling_r36_pins_it(
    library: ServiceContext,
) -> None:
    """`q` is a substring match, case-folded, on every backend.

    Asserted here because the web app's filter box sends whatever was typed and
    a reviewer types in whatever case they like. R-36 also rules out stemming,
    so a search for the *stem* must **not** match - that is the property that
    distinguishes substring semantics from full text, and the one that would
    silently change if a backend "improved" the query.
    """
    assert await titles(library, q="REPEAT OPERATOR") == [PRIYA["provenance"]["title"]]
    assert await titles(library, q="Repeat Operator") == [PRIYA["provenance"]["title"]]
    assert await titles(library, q="repeat operators") == []


async def test_the_search_covers_title_as_well_as_intent(library: ServiceContext) -> None:
    """Both fields, as the filter's label in the app promises."""
    assert await titles(library, q="FSSAI") == [PRIYA["provenance"]["title"]]
    assert await titles(library, q="fire-safety") == [ARUN["provenance"]["title"]]


async def test_a_label_filter_narrows_to_one_lineage(library: ServiceContext) -> None:
    """The third filter M9 asks for."""
    assert await titles(library, labels={"persona": "multi-unit-operator"}) == [
        PRIYA["provenance"]["title"]
    ]
    assert await titles(library, labels={"outcome": "escalated"}) == [ARUN["provenance"]["title"]]
    assert sorted(await titles(library, labels={"edge_case": "none"})) == sorted(
        [PRIYA["provenance"]["title"], ARUN["provenance"]["title"]]
    )


async def test_the_three_filters_compose(library: ServiceContext) -> None:
    """All three at once, as the app sends them when all three are set."""
    composed = await titles(
        library, q=SEARCH, author=HANDLE, labels={"persona": "multi-unit-operator"}
    )
    assert composed == [PRIYA["provenance"]["title"]]
    # And a contradictory combination is empty rather than falling back to one
    # filter - which is what would happen if they were applied as an "or".
    assert await titles(library, q=SEARCH, author="claude-code") == []


async def test_a_filter_matching_nothing_is_an_empty_success_not_an_error(
    library: ServiceContext,
) -> None:
    """The service never gates. An empty list is the honest answer to a miss."""
    reply = await invoke(library, "dataset_find", author="nobody")
    assert reply["ok"] is True
    assert data(reply)["datasets"] == []


# ------------------------------------------------------------- the review row


async def test_every_row_carries_the_whole_review_surface(library: ServiceContext) -> None:
    """Clause 3's checkable half: no field needs a second call to obtain.

    The field *set* is asserted exactly, so a removed field fails here rather
    than showing up as a blank in the app.
    """
    reply = await invoke(library, "dataset_find")
    rows: list[dict[str, Any]] = data(reply)["datasets"]
    assert rows
    for row in rows:
        assert set(row) == SUMMARY_FIELDS, f"row field set drifted: {sorted(row)}"


async def test_the_row_carries_the_intent_in_full_not_an_excerpt(library: ServiceContext) -> None:
    """The one field it would be tempting to truncate, and must not be.

    `narrative_excerpt` is deliberately the first 200 characters
    (contracts 2.2.1). `intent` has no such limit and must not acquire one:
    PRD 5.7 makes it the field a reviewer reads to decide relevance, and Priya's
    is longer than 200 characters - so a summary that clipped both would look
    consistent and be wrong.
    """
    reply = await invoke(library, "dataset_find", author=HANDLE)
    row: dict[str, Any] = data(reply)["datasets"][0]
    assert row["intent"] == PRIYA["provenance"]["intent"]
    assert len(row["intent"]) > 200
    assert row["narrative_excerpt"] == PRIYA["narrative"][:200]


async def test_the_row_carries_every_declared_label_dimension(library: ServiceContext) -> None:
    """Complete labels, not partial. PRD 5.7's requirement, on the row."""
    blueprint = load(BLUEPRINT)
    declared = set(blueprint["label_schema"]["dimensions"])
    reply = await invoke(library, "dataset_find")
    for row in data(reply)["datasets"]:
        assert set(row["labels"]) == declared


async def test_the_row_names_a_person_to_ask(library: ServiceContext) -> None:
    """`author` is attribution: a name, a handle, and how it was written."""
    reply = await invoke(library, "dataset_find", author=HANDLE)
    author: dict[str, Any] = data(reply)["datasets"][0]["author"]
    assert set(author) == {"name", "handle", "agent"}
    assert author["handle"] == HANDLE
    assert author["name"] == PRIYA["provenance"]["author"]["name"]


async def test_no_row_carries_a_node_fixture(library: ServiceContext) -> None:
    """The other side of 2.2.1: a summary is a summary.

    A list that shipped the fixtures would make clause 3 trivially true and the
    review surface unusable. `nodes`, `pools` and `entities` must be absent.
    """
    reply = await invoke(library, "dataset_find")
    for row in data(reply)["datasets"]:
        for heavy in ("nodes", "pools", "entities", "narrative", "expected"):
            assert heavy not in row


# ------------------------------------------------- the edit is copy-on-write


async def test_saving_an_edit_makes_a_new_version_and_leaves_the_old_one(
    library: ServiceContext,
) -> None:
    """Ruling R-17's copy-on-write, through the tool the web app uses.

    This is the write path `web/src/mcp/tools.ts::datasetSave` takes, asserted
    end to end: the same `id`, a new version, the previous version still
    readable, and the list showing one row rather than two.
    """
    edited = json.loads(json.dumps(PRIYA))
    edited["provenance"]["title"] = "Edited by the web app"
    reply = await invoke(library, "dataset_import", bundle=bundle(edited))
    assert reply["ok"] is True, reply
    written = data(reply)["imported"]["datasets"][0]
    assert written["id"] == PRIYA["id"]
    assert written["version"] == 2

    latest = await invoke(library, "dataset_get", dataset_id=PRIYA["id"])
    assert data(latest)["dataset"]["provenance"]["title"] == "Edited by the web app"

    original = await invoke(library, "dataset_get", dataset_id=PRIYA["id"], version=1)
    assert data(original)["dataset"]["provenance"]["title"] == PRIYA["provenance"]["title"]

    # One row per lineage at its latest version - ruling R-38. An edit must not
    # make the review surface show the same dataset twice.
    assert await titles(library) == ["Edited by the web app", ARUN["provenance"]["title"]]


async def test_a_broken_edit_is_refused_with_rule_ids_and_writes_nothing(
    library: ServiceContext,
) -> None:
    """Ground rule 7, on the app's save path.

    The editor disables save while findings stand, and that is a courtesy. This
    is the mechanism: a document that got past the editor is still rejected at
    write time, with catalogue rule ids the app renders in the same inline list,
    and the stored version is untouched.
    """
    broken = json.loads(json.dumps(PRIYA))
    broken["provenance"]["intent"] = "x"
    reply = await invoke(library, "dataset_import", bundle=bundle(broken))
    assert reply["ok"] is False
    assert {finding["rule"] for finding in reply["errors"]} == {"DS-026"}
    # Nothing was written: the lineage is still at version 1.
    latest = await invoke(library, "dataset_get", dataset_id=PRIYA["id"])
    assert data(latest)["dataset"]["version"] == 1


async def test_the_validate_tools_report_the_same_rule_without_storing(
    library: ServiceContext,
) -> None:
    """What the editor calls *before* save, and why "before" is true.

    `dataset_validate` reports DS-026 for the same document and stores nothing -
    the lineage stays at version 1 and the row set is unchanged. That is the
    whole basis of clause 1's remote half.
    """
    broken = json.loads(json.dumps(PRIYA))
    broken["provenance"]["intent"] = "x"
    reply = await invoke(library, "dataset_validate", dataset=broken)
    assert reply["ok"] is False
    assert {finding["rule"] for finding in reply["errors"]} == {"DS-026"}

    latest = await invoke(library, "dataset_get", dataset_id=PRIYA["id"])
    assert data(latest)["dataset"]["version"] == 1
    assert sorted(await titles(library)) == sorted(
        [PRIYA["provenance"]["title"], ARUN["provenance"]["title"]]
    )


async def test_blueprint_validate_reports_bp_005_without_storing(library: ServiceContext) -> None:
    """The blueprint half of the same, and the rule Ajv provably cannot see.

    Dropping the edge into `assign_training` leaves a document whose *shape* is
    perfect - so the editor's Ajv half is silent - and whose graph is broken.
    That gap is ruling R-04's division, and this is it as an assertion.
    """
    blueprint = load(BLUEPRINT)
    blueprint["edges"] = [edge for edge in blueprint["edges"] if edge["to"] != "assign_training"]
    reply = await invoke(library, "blueprint_validate", blueprint=blueprint)
    assert reply["ok"] is False
    assert "BP-005" in {finding["rule"] for finding in reply["errors"]}
    # Every finding carries a pointer the editor can render inline.
    assert all(finding["pointer"].startswith("/") for finding in reply["errors"])
    # And the published version is untouched.
    stored = await invoke(library, "blueprint_get", agent_id=blueprint["agent_id"])
    assert len(data(stored)["blueprint"]["edges"]) == len(load(BLUEPRINT)["edges"])
