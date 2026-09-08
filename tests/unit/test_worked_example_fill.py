"""M5's gate: five `dataset_fill_part` calls reassemble `priya-missing-docs.json`.

`docs/worked-example.md` section 7 steps 1 to 4, driven through the MCP tool
surface with the in-memory ``Client``, so this is also the **exact call
sequence M8's end-to-end test starts with**. Its steps 5 to 9 - the client, the
run and the grading - are M6's and M8's.

Two acceptance criteria live here:

**Criterion 1.** The worked-example dataset can be filled in five separate
``dataset_fill_part`` calls in manifest order and submitted successfully.

**Criterion 6.** That fill reproduces `priya-missing-docs.json`. The fixture is
byte-exact from the spec, and `test_fixtures.py` now carries the guard that
keeps it that way (ruling R-44), so if the five sections could not reassemble
it then either ruling R-06's manifest partition or the skeleton shape would be
wrong - and the report would say which rather than the fixture being adjusted.

One field is excluded from that comparison, and it is named at the assertion:
``id``, which the fixture hand-builds and ruling R-10 mints from
``(seed, salt)``. ``validated_at`` would have been the second, so the clock
here is frozen at the fixture's own ``validated_at`` and that field is
**compared** rather than excused.

And the **repair loop**, which is the product's headline authoring flow and the
thing PRD 6 flow B is written about:
:func:`test_a_rejected_submit_is_repaired_by_refilling_one_section` drives
reject -> re-fill the named section -> submit, through the same client. Re-fill
was proven at the rule layer and at the service layer separately; the loop as a
caller experiences it was not, and that is the one assertion that says the
error envelope is actually useful to the LLM it was designed for.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from mcp import Client

from agentprops.models import Dataset
from agentprops.service import FrozenClock, ServiceContext, sqlite_context
from agentprops.storage import SqlStore
from agentprops.validation.pointers import SECTIONS
from conftest import BLUEPRINT_FIXTURE, DATASET_FIXTURE, load_document
from toolclient import connected, invoke

#: The instant `priya-missing-docs.json` carries in ``validated_at``. Freezing
#: the clock here is what lets the comparison below cover that field.
FIXTURE_INSTANT: Final = datetime(2026, 9, 8, 10, 14, 22, tzinfo=UTC)

#: The one field the service cannot reproduce: ruling R-10 derives a dataset id
#: from ``(seed, salt)`` and the fixture's is hand-built in the specification.
UNREPRODUCIBLE: Final[frozenset[str]] = frozenset({"id"})


@pytest.fixture
def spec_context(tmp_path: Path) -> Iterator[ServiceContext]:
    """A context whose clock reads the golden fixture's ``validated_at``."""
    built = sqlite_context(tmp_path / "worked-example.db", clock=FrozenClock(FIXTURE_INSTANT))
    try:
        yield built
    finally:
        if isinstance(built.store, SqlStore):
            built.store.dispose()


async def publish(client: Client) -> None:
    """Step 1: publish `location-onboarding` 1.0.0."""
    envelope = await invoke(
        client, "blueprint_upsert", blueprint=load_document(BLUEPRINT_FIXTURE), publish=True
    )
    assert envelope["ok"] is True, envelope


async def start(client: Client, golden: dict[str, Any]) -> dict[str, Any]:
    """Step 2: ``dataset_skeleton`` with the fixture's own labels and seed."""
    envelope = await invoke(
        client,
        "dataset_skeleton",
        agent_id=golden["blueprint"]["agent_id"],
        version=golden["blueprint"]["version"],
        labels=golden["labels"],
        seed=golden["seed"],
    )
    assert envelope["ok"] is True, envelope
    payload: dict[str, Any] = envelope["data"]["skeleton"]
    return payload


def content_for(section: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """One section's ``content``, sliced out of a dataset document by its pointers.

    Driven off the **manifest the server returned** rather than a table in this
    file, because that is the thing being tested: a caller with a dataset in
    hand and the manifest in front of it has to be able to work out what to send
    without knowing anything else. Each pointer is a whole top-level field, so
    the slice is one ``lstrip`` away.
    """
    return {target.lstrip("/"): document[target.lstrip("/")] for target in section["pointers"]}


async def fill_every_section(
    client: Client, skeleton_id: str, manifest: Sequence[dict[str, Any]], golden: dict[str, Any]
) -> int:
    """Step 3: one ``dataset_fill_part`` per section, in manifest order."""
    calls = 0
    for section in manifest:
        envelope = await invoke(
            client,
            "dataset_fill_part",
            skeleton_id=skeleton_id,
            section=section["id"],
            content=content_for(section, golden),
        )
        assert envelope["ok"] is True, envelope
        assert envelope["data"]["fill"]["filled"][-1] == section["id"]
        calls += 1
    return calls


async def test_the_worked_example_fills_in_five_calls_and_reproduces_the_fixture(
    spec_context: ServiceContext,
) -> None:
    """M5 acceptance criteria 1 and 6, and M8's opening call sequence.

    The sequence, which is the thing to copy:

    1. ``blueprint_upsert(blueprint, publish=True)``
    2. ``dataset_skeleton(agent_id, version, labels, seed)`` -> ``skeleton_id``
    3. ``dataset_fill_part(skeleton_id, section, content)`` x5, in manifest order
    4. ``dataset_submit(skeleton_id)`` -> the stored dataset

    ``content`` is ``{field: value}`` for the fields that section's ``pointers``
    name, which is where the keyed content form earns itself: a section's
    content is a fragment of the dataset document, so a caller slices its own
    document by the manifest rather than learning a second shape.
    """
    golden = load_document(DATASET_FIXTURE)

    async with connected(spec_context) as client:
        await publish(client)
        payload = await start(client, golden)
        assert [section["id"] for section in payload["manifest"]] == list(SECTIONS)
        assert payload["manifest"][0]["id"] == "provenance", "provenance first (M5's gate)"
        assert payload["instructions"], "an LLM caller is told how to fill it"

        calls = await fill_every_section(
            client, payload["skeleton_id"], payload["manifest"], golden
        )
        assert calls == 5, "five separate dataset_fill_part calls (M5's gate)"

        submitted = await invoke(client, "dataset_submit", skeleton_id=payload["skeleton_id"])

    assert submitted["ok"] is True, submitted
    assert submitted["warnings"] == [], "the golden dataset trips no warning either"
    stored = submitted["data"]["dataset"]
    assert set(stored) == set(golden), "the assembled document carries the fixture's fields"
    differing = {field for field in golden if stored[field] != golden[field]}
    assert differing == UNREPRODUCIBLE, (
        "the five sections must reassemble the fixture except for the id, which ruling R-10 "
        f"mints from (seed, salt) where the fixture hand-builds it. Differing: {sorted(differing)}"
    )
    assert stored["validated_at"] == golden["validated_at"], (
        "the clock is frozen at the fixture's instant, so validated_at is compared rather "
        "than excused"
    )


async def test_a_rejected_submit_is_repaired_by_refilling_one_section(
    spec_context: ServiceContext,
) -> None:
    """PRD 6 flow B, end to end: reject, repair one part, submit.

    "A rejection returns structured errors, scoped to the section that caused
    them, so the LLM repairs one part rather than regenerating everything."
    That sentence is the reason SK-002 permits a re-fill (ruling R-06), the
    reason every finding carries a ``section``, and the reason
    ``dataset_fill_part`` replaces a section rather than merging into it. It had
    no end-to-end test, so the loop it describes was three separately-proven
    pieces rather than a working flow.

    The break is ``expected.comparison: "vibes"`` - DS-017's vocabulary - chosen
    because it is one word in one section, which is exactly the shape of mistake
    an LLM makes and a repair should cost one call to fix.

    Four things are asserted, in the order a caller depends on them:

    1. the submit is **rejected**, with the rule id and nothing else;
    2. every finding names ``expected`` in ``section``, so the caller knows
       which of the five parts to regenerate without guessing;
    3. one ``dataset_fill_part`` on that section is accepted, and ``remaining``
       is empty - so the caller can tell it is ready to submit again;
    4. the submit then **succeeds**, and produces the golden dataset. The
       repaired document is byte-identical to the one the clean fill produced,
       which is what makes a re-fill a repair rather than a second draft.
    """
    golden = load_document(DATASET_FIXTURE)
    broken = {**golden, "expected": {**golden["expected"], "comparison": "vibes"}}

    async with connected(spec_context) as client:
        await publish(client)
        payload = await start(client, golden)
        skeleton_id = payload["skeleton_id"]
        await fill_every_section(client, skeleton_id, payload["manifest"], broken)

        rejected = await invoke(client, "dataset_submit", skeleton_id=skeleton_id)
        assert rejected["ok"] is False, rejected
        assert [error["rule"] for error in rejected["errors"]] == ["DS-017"]
        named = {error["section"] for error in rejected["errors"]}
        assert named == {"expected"}, "the caller is told which part to repair"
        assert rejected["errors"][0]["pointer"] == "/expected/comparison"

        wanted = next(iter(named))
        section = next(entry for entry in payload["manifest"] if entry["id"] == wanted)
        repaired = await invoke(
            client,
            "dataset_fill_part",
            skeleton_id=skeleton_id,
            section=section["id"],
            content=content_for(section, golden),
        )
        assert repaired["ok"] is True, repaired
        assert repaired["data"]["fill"]["remaining"] == [], "ready to submit again"

        submitted = await invoke(client, "dataset_submit", skeleton_id=skeleton_id)

    assert submitted["ok"] is True, submitted
    stored = submitted["data"]["dataset"]
    differing = {field for field in golden if stored[field] != golden[field]}
    assert differing == UNREPRODUCIBLE, (
        f"a repaired dataset is the golden dataset, not a second draft: {sorted(differing)}"
    )
    assert stored["version"] == 1, "the repair happened before the write, so there is one version"


async def test_the_reassembled_dataset_round_trips_like_the_fixture(
    spec_context: ServiceContext,
) -> None:
    """Ruling R-08's criterion, applied to the document the pipeline produced.

    ``model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw``.
    It matters here because it is what makes the comparison above meaningful: a
    stored document carrying ``"fault": null`` on eight fixtures would compare
    unequal to the fixture for a reason that has nothing to do with the
    manifest, and ``exclude_unset`` is what keeps those keys out.
    """
    golden = load_document(DATASET_FIXTURE)
    async with connected(spec_context) as client:
        await publish(client)
        payload = await start(client, golden)
        await fill_every_section(client, payload["skeleton_id"], payload["manifest"], golden)
        submitted = await invoke(client, "dataset_submit", skeleton_id=payload["skeleton_id"])

    stored = submitted["data"]["dataset"]
    assert Dataset.model_validate(stored).model_dump(mode="json", exclude_unset=True) == stored
    assert "fault" not in stored["nodes"]["complete"], "exclude_unset keeps optionals out"


async def test_filling_entities_before_provenance_is_rejected_with_sk_002(
    spec_context: ServiceContext,
) -> None:
    """M5 acceptance criterion 2, and `worked-example.md` section 7 step 3's clause.

    Through the tool surface, because that is where M8 will hit it. The error is
    scoped to ``provenance`` - the section to go and fill - and the skeleton is
    left untouched, so the caller retries in the right order rather than
    starting over.
    """
    golden = load_document(DATASET_FIXTURE)
    async with connected(spec_context) as client:
        await publish(client)
        payload = await start(client, golden)
        rejected = await invoke(
            client,
            "dataset_fill_part",
            skeleton_id=payload["skeleton_id"],
            section="entities",
            content={"entities": golden["entities"]},
        )
        assert rejected["ok"] is False
        assert [error["rule"] for error in rejected["errors"]] == ["SK-002"]
        assert rejected["errors"][0]["section"] == "provenance"

        again = await start(client, golden)
        assert again["skeleton_id"] == payload["skeleton_id"], "the same request resumes"
        assert again["skeleton"]["entities"] == {"store": {}, "franchisee": {}}, (
            "a rejected fill wrote nothing, so the scaffold still shows its placeholders"
        )
