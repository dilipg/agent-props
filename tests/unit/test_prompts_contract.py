"""``prompts/get`` and ``resources/read`` over a real in-memory MCP session.

`test_tools_contract.py` is the sibling and the pattern: an ``mcp.Client``
speaking to the server object, no subprocess, the whole round trip exercised
including the initialise handshake. Ruling R-76's surfaces are protocol surfaces
and this is the only path that measures them as such - a direct call into
`service/prompts.py` would test the text and none of the registration.

Every test here reads a **store**, so each one is written twice where the answer
differs: once against the golden fixtures and once against an empty store.
`DECISIONS.md` records why - "when a `DECISIONS.md` entry reasons about a
boundary, test both ends of it", and the empty store is the end where a prompt
is most tempted to promise something that does not exist.

Two properties this file pins that nothing else can
---------------------------------------------------

**Byte identity with the tools.** A resource serving a blueprint and
``blueprint_get`` serving the same version must hand back the same bytes. They
share ``service/blueprints.py::document``, and
:func:`test_the_blueprint_resource_is_byte_identical_to_blueprint_get` measures
it rather than trusting the shared call - the whole reason that function was
made public.

**No duplication of ``dataset_skeleton``'s ``instructions``.** R-76 names
duplication as this milestone's risk, and the sharpest instance is one layer in
from the README: ``fill-a-dataset`` must not restate the fill order, SK-002, the
re-fill-as-repair rule or the per-section content shape, because
``dataset_skeleton`` already returns all four.
:func:`test_fill_a_dataset_does_not_restate_the_skeleton_instructions` asserts
that against the **live** ``instructions`` text rather than against a list of
phrases somebody typed here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentprops.models import Dataset
from agentprops.service import ServiceContext, blueprints
from agentprops.service.skeletons import instructions_for, manifest_for
from agentprops.validation.context import BlueprintView
from conftest import BLUEPRINT_FIXTURE, load_document
from toolclient import connected, invoke

AGENT = "location-onboarding"
VERSION = "1.0.0"

CATALOGUE = "agentprops://catalogue"
ORIENTATION = "agentprops://orientation"
EXAMPLE_BLUEPRINT = "agentprops://examples/blueprint"
EXAMPLE_DATASET = "agentprops://examples/dataset"
ONE_BLUEPRINT = "agentprops://blueprint/location-onboarding/1.0.0"
ONE_AGENTS_DATASET = "agentprops://dataset/location-onboarding/example"


@pytest.fixture
def seeded(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
    other_dataset_document: dict[str, Any],
) -> ServiceContext:
    """The golden blueprint published, plus both golden datasets."""
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    context.store.put_dataset(Dataset.model_validate(other_dataset_document))
    return context


async def prompt_text(context: ServiceContext, name: str, **arguments: str) -> str:
    """One ``prompts/get``, flattened to the text of its messages."""
    async with connected(context) as client:
        result = await client.get_prompt(name, dict(arguments))
    return "\n".join(getattr(message.content, "text", "") for message in result.messages)


async def resource_body(context: ServiceContext, uri: str) -> dict[str, Any]:
    """One ``resources/read``, parsed.

    Call sites pass the URI as a **literal** rather than through the constants
    above, and that is not an oversight: ``test_prompt_and_resource_surface.py``
    proves coverage by scanning the AST for literals handed to this helper, so a
    URI reaching it through a name is a URI the guard cannot verify. The
    constants are kept for the assertions, where a name reads better than a
    string and nothing depends on it being one.
    """
    async with connected(context) as client:
        result = await client.read_resource(uri)
    payload: dict[str, Any] = json.loads(getattr(result.contents[0], "text", ""))
    return payload


# --------------------------------------------------------------- prompts/list


async def test_the_four_prompts_are_listed_with_their_arguments(
    context: ServiceContext,
) -> None:
    """R-76's parameterisation clause, over the wire.

    ``author-a-blueprint`` takes nothing required; the other two require an
    ``agent_id`` and nothing else, because everywhere else on this surface an
    omitted ``version`` means the latest published one.
    """
    async with connected(context) as client:
        listed = await client.list_prompts()
    required = {
        prompt.name: sorted(
            argument.name for argument in prompt.arguments or [] if argument.required
        )
        for prompt in listed.prompts
    }
    assert required["author-a-blueprint"] == []
    assert required["fill-a-dataset"] == ["agent_id"]
    assert required["cover-the-label-space"] == ["agent_id"]
    assert required["wire-an-agent"] == []
    optional = {
        prompt.name: sorted(
            argument.name for argument in prompt.arguments or [] if not argument.required
        )
        for prompt in listed.prompts
    }
    assert optional["author-a-blueprint"] == ["agent_id"]
    assert optional["fill-a-dataset"] == ["scenarios", "version"]
    assert optional["cover-the-label-space"] == ["version"]
    assert optional["wire-an-agent"] == ["agent_id", "url"]


# --------------------------------------------------- author-a-blueprint


async def test_author_a_blueprint_names_an_example_the_store_actually_holds(
    seeded: ServiceContext,
) -> None:
    """The concrete defect R-76 cites, fixed and measured.

    The README's step-2 prompt hard-coded ``location-onboarding``. This prompt
    resolves it: the agent and version it names are the ones this store has, and
    the resource URI it hands over is one ``resources/read`` answers.
    """
    text = await prompt_text(seeded, "author-a-blueprint")
    assert f"`{AGENT}` at `{VERSION}`" in text
    assert ONE_BLUEPRINT in text
    body = await resource_body(seeded, "agentprops://blueprint/location-onboarding/1.0.0")
    assert body["available"] is True, "the prompt named a resource that is not available"


async def test_author_a_blueprint_promises_no_example_in_an_empty_store(
    context: ServiceContext,
) -> None:
    """The other end of the boundary, and the one that matters.

    No agent id is invented, and the contract still arrives in full - so a
    caller in a fresh store gets a usable prompt rather than an instruction to
    imitate something that does not exist.
    """
    text = await prompt_text(context, "author-a-blueprint")
    assert "no published blueprint" in text
    assert AGENT not in text, "the prompt named an agent this store has never heard of"
    assert "agentprops://blueprint/" not in text
    assert "`entry_node`" in text, "the shape contract went missing with the example"


async def test_author_a_blueprint_carries_the_shape_contract_the_tools_do_not(
    seeded: ServiceContext,
) -> None:
    """Why this is the long prompt: nothing in the tool surface says any of this.

    There is no ``blueprint_skeleton``, so each of these is reachable over MCP
    only through this prompt. Asserted as a set of contract facts rather than as
    a length, because a length is not a claim about content.
    """
    text = await prompt_text(seeded, "author-a-blueprint")
    for fact in (
        "`entry_node`",
        "`tool_name`",
        "output_schema",
        "entity:<id>",
        "outcome_schema",
        "label_schema",
        "max_iterations",
        "`kind: terminal`",
        "blueprint_validate",
        "BP-014",
        "BP-019",
    ):
        assert fact in text, f"author-a-blueprint does not carry {fact}"


async def test_author_a_blueprint_takes_an_explicit_agent_to_imitate(
    seeded: ServiceContext,
) -> None:
    """The optional argument, and its honest answer for an agent with no blueprint."""
    named = await prompt_text(seeded, "author-a-blueprint", agent_id=AGENT)
    assert f"`{AGENT}` at `{VERSION}`" in named

    missing = await prompt_text(seeded, "author-a-blueprint", agent_id="no-such-agent")
    assert "no published blueprint for `no-such-agent`" in missing
    assert "`entry_node`" in missing


# ------------------------------------------------------- fill-a-dataset


async def test_fill_a_dataset_resolves_the_version_and_supplies_intent(
    seeded: ServiceContext,
) -> None:
    text = await prompt_text(seeded, "fill-a-dataset", agent_id=AGENT)
    assert f"`{AGENT}` at `{VERSION}`" in text
    assert "label_vocabulary" in text
    assert EXAMPLE_DATASET in text
    assert "the happy path" in text
    assert "the retry loop firing exactly once" in text


async def test_fill_a_dataset_takes_the_caller_s_scenarios_one_per_line(
    seeded: ServiceContext,
) -> None:
    """Newlines rather than commas: a scenario description contains commas.

    Leading list markers are stripped so a pasted numbered list is not
    double-numbered, which is what a caller actually pastes.
    """
    text = await prompt_text(
        seeded,
        "fill-a-dataset",
        agent_id=AGENT,
        scenarios="1. a store with no documents at all\n- a franchisee who withdraws",
    )
    assert "1. a store with no documents at all" in text
    assert "2. a franchisee who withdraws" in text
    assert "the happy path" not in text, "the defaults were used alongside the caller's list"


async def test_fill_a_dataset_honours_an_explicit_version(seeded: ServiceContext) -> None:
    text = await prompt_text(seeded, "fill-a-dataset", agent_id=AGENT, version=VERSION)
    assert f"`{AGENT}` at `{VERSION}`" in text

    missing = await prompt_text(seeded, "fill-a-dataset", agent_id=AGENT, version="9.9.9")
    assert "no published agent-props blueprint" in missing
    assert "at version `9.9.9`" in missing


async def test_fill_a_dataset_refuses_a_draft_version(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """The "whatever its status" bug, at its second site. Fix round 1.

    ``Store.get_blueprint`` returns an exact version whatever its status, so an
    explicit **draft** version resolved and this prompt told a caller to author
    datasets against it - which ``dataset_skeleton`` refuses and DS-001 rejects
    regardless. The same call's not-found branch already said "no **published**
    blueprint", so the two branches contradicted each other.

    Three assertions, because the third is what stops this passing for the wrong
    reason: the prompt refuses, it says "published", and ``blueprint_get`` still
    returns the draft - so the difference is a decision rather than the blueprint
    having gone missing.
    """
    draft = json.loads(json.dumps(blueprint_document))
    draft["version"] = "2.0.0"
    blueprints.upsert(context, blueprint_document, publish=True)
    blueprints.upsert(context, draft, publish=False)

    text = await prompt_text(context, "fill-a-dataset", agent_id=AGENT, version="2.0.0")
    assert "no published agent-props blueprint" in text
    assert "at version `2.0.0`" in text
    assert "dataset_skeleton" not in text, "the prompt gave fill instructions for a draft"

    served = (await invoke(context, "blueprint_get", agent_id=AGENT, version="2.0.0"))["data"]
    assert served["blueprint"]["status"] == "draft", (
        "blueprint_get no longer returns the draft, so this test proves nothing"
    )


async def test_fill_a_dataset_substitutes_no_agent_when_given_none(
    seeded: ServiceContext,
) -> None:
    """An empty ``agent_id`` is a miss, not "the store's example agent".

    ``examples.published_blueprint`` deliberately has no sentinel; only
    ``example_blueprint`` fills one in, and only because "the example" is what
    it was asked for. Routing this prompt through the sentinel would have made
    an empty argument silently answer about ``location-onboarding`` - which is a
    plausible answer to a question nobody asked, and the class of defect this
    milestone is about.
    """
    text = await prompt_text(seeded, "fill-a-dataset", agent_id="")
    assert "no published agent-props blueprint" in text
    assert AGENT not in text, "an empty agent_id was answered with the store's example agent"


async def test_fill_a_dataset_promises_nothing_in_an_empty_store(
    context: ServiceContext,
) -> None:
    text = await prompt_text(context, "fill-a-dataset", agent_id=AGENT)
    assert "no published agent-props blueprint" in text
    assert "dataset_skeleton" not in text, (
        "the prompt gave fill instructions for a blueprint that does not exist"
    )
    assert "author-a-blueprint" in text, "the miss does not say what to do next"


async def test_fill_a_dataset_does_not_restate_the_skeleton_instructions(
    seeded: ServiceContext,
) -> None:
    """R-76's duplication risk, one layer in, measured against the live text.

    ``dataset_skeleton`` returns an ``instructions`` field carrying the fill
    order, SK-002, re-fill-as-repair and each section's content shape. This
    prompt supplies *intent* only, so it must not repeat any of that - and the
    check is against ``instructions_for`` itself rather than against phrases
    typed into this test, because a hand-typed list is a claim about what
    somebody remembered the instructions saying.
    """
    document = load_document(BLUEPRINT_FIXTURE)
    view = BlueprintView(document)
    instructions = instructions_for(manifest_for(view))
    text = await prompt_text(seeded, "fill-a-dataset", agent_id=AGENT)

    for owned in ("SK-002", "dataset_fill_part", "manifest order", "A re-fill replaces"):
        assert owned in instructions, f"the premise moved: instructions no longer say {owned!r}"
        assert owned not in text, (
            f"fill-a-dataset restates {owned!r}, which dataset_skeleton's instructions already "
            f"carry - the duplication ruling R-76 warns about"
        )

    sentences = {
        line.strip() for line in instructions.replace("\n", " ").split(". ") if len(line) > 40
    }
    repeated = [sentence for sentence in sentences if sentence and sentence in text]
    assert not repeated, f"fill-a-dataset copies sentences out of instructions: {repeated}"

    assert "instructions" in text, "the prompt must at least point at the field it defers to"


# ------------------------------------------------- cover-the-label-space


async def test_cover_the_label_space_names_the_values_with_no_dataset(
    seeded: ServiceContext,
) -> None:
    """The prompt that could not have been a README paragraph.

    Cross-checked against ``label_vocabulary``'s own counts over the same
    session, so the prompt and the tool cannot disagree about what is empty.
    """
    text = await prompt_text(seeded, "cover-the-label-space", agent_id=AGENT)
    vocabulary = (await invoke(seeded, "label_vocabulary", agent_id=AGENT, version=VERSION))[
        "data"
    ]["vocabulary"]

    empty = {
        f"{dimension}={value}"
        for dimension, values in vocabulary["counts"].items()
        for value, count in values.items()
        if count == 0
    }
    covered = {
        f"{dimension}={value}"
        for dimension, values in vocabulary["counts"].items()
        for value, count in values.items()
        if count > 0
    }
    assert empty, "the fixture no longer has an uncovered label value to name"
    for pair in empty:
        assert f"`{pair}`" in text, f"{pair} has no dataset and the prompt does not name it"
    for pair in covered:
        assert f"- `{pair}`" not in text, f"{pair} has a dataset and the prompt calls it empty"


async def test_cover_the_label_space_lists_the_combinations_already_present(
    seeded: ServiceContext,
) -> None:
    """The tuples, bounded by the dataset count rather than by the vocabulary."""
    text = await prompt_text(seeded, "cover-the-label-space", agent_id=AGENT)
    assert "scenario=missing-documents" in text
    assert "scenario=compliance-overdue" in text
    assert "cross-product of 432 combination(s)" in text, (
        "the cross-product size is stated as a number rather than enumerated"
    )
    assert "This store holds 2 dataset(s)" in text


async def test_cover_the_label_space_says_so_when_nothing_is_stored(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """A published blueprint with no datasets: every value is empty, and it says so."""
    blueprints.upsert(context, blueprint_document, publish=True)
    text = await prompt_text(context, "cover-the-label-space", agent_id=AGENT)
    assert "This store holds 0 dataset(s)" in text
    assert "No label combination is present yet." in text
    assert "`scenario=missing-documents`" in text


async def test_cover_the_label_space_reports_a_draft_with_a_caveat(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """This prompt keeps the raw lookup, and says what a draft cannot do. Fix round 1.

    It mirrors ``label_vocabulary``, which reports a draft version's vocabulary
    quite correctly - a read with a real answer. What a draft cannot do is
    receive a dataset (DS-001), so the coverage advice would be unactionable
    without the caveat. Both halves: the vocabulary still arrives, and the
    caveat names the blocker and the fix.
    """
    draft = json.loads(json.dumps(blueprint_document))
    draft["version"] = "2.0.0"
    blueprints.upsert(context, draft, publish=False)

    text = await prompt_text(context, "cover-the-label-space", agent_id=AGENT, version="2.0.0")
    assert "`2.0.0` is a draft" in text
    assert "DS-001" in text
    assert "blueprint_upsert(publish: true)" in text
    assert "`scenario=missing-documents`" in text, "the coverage list went missing with it"

    vocabulary = (await invoke(context, "label_vocabulary", agent_id=AGENT, version="2.0.0"))[
        "data"
    ]["vocabulary"]
    assert vocabulary["version"] == "2.0.0", (
        "label_vocabulary no longer answers for a draft, so this prompt should stop too"
    )


async def test_the_two_prompts_word_a_miss_differently_on_purpose(
    seeded: ServiceContext,
) -> None:
    """One sentence, two truths, and the parameter that stops it saying the wrong one.

    ``fill-a-dataset`` resolves published-only, so its miss really is "no
    *published* version". ``cover-the-label-space`` mirrors ``label_vocabulary``'s
    raw lookup, so a draft is a hit there and claiming "published" would
    contradict the branch beside it. Fix round 1 found the shared wording
    asserting the second thing about the first.
    """
    filling = await prompt_text(seeded, "fill-a-dataset", agent_id="ghost")
    covering = await prompt_text(seeded, "cover-the-label-space", agent_id="ghost")
    assert "no published agent-props blueprint for `ghost`" in filling
    assert "no agent-props blueprint for `ghost`" in covering
    assert "no published" not in covering, (
        "cover-the-label-space claims published while resolving any status"
    )


async def test_cover_the_label_space_counts_one_dataset_in_the_singular_path(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
) -> None:
    """The one-dataset case: zero and two were asserted and one was not.

    It is the only input that exercises the single-tuple branch of
    ``_present_tuples`` with a real count beside it, and the count formatting is
    the kind of thing that reads fine and is wrong.
    """
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    text = await prompt_text(context, "cover-the-label-space", agent_id=AGENT)
    assert "This store holds 1 dataset(s)" in text
    tuples = [
        line for line in text.splitlines() if line.startswith("- `") and ", scenario=" in line
    ]
    assert tuples == [
        "- `persona=multi-unit-operator, scenario=missing-documents, tier=regional, "
        "outcome=success, edge_case=none` (1)"
    ], f"expected exactly one present tuple with a count of 1, got {tuples}"
    assert "`scenario=compliance-overdue`" in text, (
        "the other dataset's scenario should now read as an empty value"
    )


async def test_cover_the_label_space_promises_nothing_in_an_empty_store(
    context: ServiceContext,
) -> None:
    text = await prompt_text(context, "cover-the-label-space", agent_id=AGENT)
    assert "no agent-props blueprint" in text
    assert "no published" not in text, "the wording split is not being tested"
    assert "label space is covered" not in text


# ---------------------------------------------------------- wire-an-agent


async def test_wire_an_agent_shows_a_selector_this_store_can_serve(
    seeded: ServiceContext,
) -> None:
    """The step-4 prompt's one store-derived fact, and why it is worth having.

    `README.md` step 4 hard-coded ``{"scenario": "missing-documents"}`` in its
    snippet - the same defect ruling R-76 cites one step earlier. Here the
    labels come off a real dataset, so the selector the caller pastes pins
    something; cross-checked against ``dataset_find`` over the same session.
    """
    text = await prompt_text(seeded, "wire-an-agent")
    rows = (await invoke(seeded, "dataset_find", agent_id=AGENT))["data"]["datasets"]
    labels = rows[0]["labels"]
    for name, value in labels.items():
        assert f'"{name}": "{value}"' in text, f"the selector omits {name}={value}"
    assert rows[0]["title"] in text
    assert "http://127.0.0.1:8000/mcp" in text


async def test_wire_an_agent_carries_the_seam_and_the_three_gotchas(
    seeded: ServiceContext,
) -> None:
    """The half of step 4 that is about code shape, and the client's contract.

    None of these is in a tool description, which is why they are a prompt.
    """
    text = await prompt_text(seeded, "wire-an-agent")
    for fact in (
        "single seam",
        "environment variable",
        "AP-004",
        "w.code",
        "the run id belongs to the client",
        "compare.grade",
        "the comparison is yours",
    ):
        assert fact in text, f"wire-an-agent does not carry {fact}"


async def test_wire_an_agent_takes_the_endpoint_it_is_told(seeded: ServiceContext) -> None:
    text = await prompt_text(seeded, "wire-an-agent", url="http://props.internal:9000/mcp")
    assert "http://props.internal:9000/mcp" in text
    assert "127.0.0.1:8000" not in text


async def test_wire_an_agent_shows_no_selector_when_there_is_nothing_to_select(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """The other end: a published blueprint, no datasets, and no invented labels."""
    blueprints.upsert(context, blueprint_document, publish=True)
    text = await prompt_text(context, "wire-an-agent")
    assert "no dataset yet" in text
    assert '"labels": {"' not in text, "a selector was shown for a store with no datasets"
    assert "single seam" in text, "the seam guidance went missing with the selector"
    assert "fill-a-dataset" in text


# ------------------------------------------------------------- resources


async def test_the_catalogue_names_every_resource_a_caller_needs(
    seeded: ServiceContext,
) -> None:
    """R-76's "without being told an id", as an assertion.

    Nothing in this test knows an id in advance except the ones it reads back
    out of the catalogue, and every URI the catalogue names resolves to
    something a caller can use.

    Two properties, because the catalogue now names two kinds of thing. The
    document resources must report ``available``, which is the flag that
    separates a real blueprint from a placeholder. The orientation is not drawn
    from the store and carries no such flag, so what stands in for it is that it
    resolves to a non-empty phase list - the exact-equality assertion on
    ``start_here`` above is what stops a third kind slipping past both.
    """
    body = await resource_body(seeded, "agentprops://catalogue")
    assert [agent["agent_id"] for agent in body["agents"]] == [AGENT]
    assert body["agents"][0]["published_versions"] == [VERSION]
    assert body["agents"][0]["dataset_count"] == 2
    assert body["start_here"] == {
        "orientation": ORIENTATION,
        "blueprint": EXAMPLE_BLUEPRINT,
        "dataset": EXAMPLE_DATASET,
    }

    for uri in [
        *body["agents"][0]["blueprints"],
        body["agents"][0]["example_dataset"],
        body["start_here"]["blueprint"],
        body["start_here"]["dataset"],
    ]:
        named = await resource_body(seeded, uri)
        assert named["available"] is True, f"the catalogue names {uri}, which is not available"

    guide = await resource_body(seeded, "agentprops://orientation")
    assert guide["phases"], "the catalogue names the orientation, which resolves to no phases"


async def test_the_catalogue_of_an_empty_store_is_empty_and_says_what_to_do(
    context: ServiceContext,
) -> None:
    body = await resource_body(context, "agentprops://catalogue")
    assert body["agents"] == []
    assert "author-a-blueprint" in body["next_step"]


async def test_the_catalogue_lists_published_blueprints_only(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """A draft is not a known-good document, so it is not a resource.

    ``agent_list`` and ``blueprint_list`` are the surfaces that show drafts, and
    this asserts the split rather than leaving it to the docstring.
    """
    blueprints.upsert(context, blueprint_document, publish=False)
    body = await resource_body(context, "agentprops://catalogue")
    assert body["agents"] == [], "a draft blueprint was offered as an example"
    listed = (await invoke(context, "agent_list"))["data"]["agents"]
    assert [agent["agent_id"] for agent in listed] == [AGENT], (
        "the draft is invisible to agent_list too, so the split is not being tested"
    )


async def test_the_example_blueprint_needs_no_id_and_is_a_published_one(
    seeded: ServiceContext,
) -> None:
    body = await resource_body(seeded, "agentprops://examples/blueprint")
    assert body["available"] is True
    assert body["agent_id"] == AGENT
    assert body["version"] == VERSION
    assert body["blueprint"]["status"] == "published"
    assert body["uri"] == ONE_BLUEPRINT


async def test_the_example_dataset_needs_no_id(seeded: ServiceContext) -> None:
    body = await resource_body(seeded, "agentprops://examples/dataset")
    assert body["available"] is True
    assert body["agent_id"] == AGENT
    assert body["dataset"]["provenance"]["title"]
    assert body["dataset"]["nodes"], "an example dataset with no fixtures is not an example"


async def test_the_blueprint_resource_is_byte_identical_to_blueprint_get(
    seeded: ServiceContext,
) -> None:
    """One document function, two surfaces - measured, not assumed.

    ``service/blueprints.py::document`` was made public at M9.5 exactly so a
    resource and ``blueprint_get`` could not drift; a shared call is not proof
    that the bytes match, and this is.
    """
    body = await resource_body(seeded, "agentprops://blueprint/location-onboarding/1.0.0")
    served = (await invoke(seeded, "blueprint_get", agent_id=AGENT, version=VERSION))["data"]
    assert json.dumps(body["blueprint"]) == json.dumps(served["blueprint"])


async def test_the_dataset_resource_is_byte_identical_to_dataset_get(
    seeded: ServiceContext,
) -> None:
    body = await resource_body(seeded, "agentprops://dataset/location-onboarding/example")
    served = (
        await invoke(seeded, "dataset_get", dataset_id=body["dataset_id"], version=body["version"])
    )["data"]
    assert json.dumps(body["dataset"]) == json.dumps(served["dataset"])


async def test_a_draft_version_is_not_served_as_a_resource(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """The template's own published-only rule, at the version grain.

    ``blueprint_get`` returns a draft by exact version; the resource does not,
    because a resource is a known-good document. Both halves asserted, so the
    difference is a tested decision rather than an accident.
    """
    blueprints.upsert(context, blueprint_document, publish=False)
    body = await resource_body(context, "agentprops://blueprint/location-onboarding/1.0.0")
    assert body["available"] is False
    assert "no published blueprint" in body["note"]
    served = (await invoke(context, "blueprint_get", agent_id=AGENT, version=VERSION))["data"]
    assert served["blueprint"]["status"] == "draft", (
        "blueprint_get no longer returns the draft, so this test proves nothing"
    )


async def test_an_agent_with_no_dataset_gets_no_other_agent_s(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
) -> None:
    """The per-agent example never falls back, which is the point of it.

    A fallback would report an example for an agent that has none - a plausible
    answer to a question the caller did not ask.
    """
    other = json.loads(json.dumps(blueprint_document))
    other["agent_id"] = "second-agent"
    blueprints.upsert(context, blueprint_document, publish=True)
    blueprints.upsert(context, other, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))

    theirs = await resource_body(context, "agentprops://dataset/location-onboarding/example")
    assert theirs["available"] is True

    body = await resource_body(context, "agentprops://dataset/second-agent/example")
    assert body["available"] is False
    assert body["agent_id"] == "second-agent"
    assert "no dataset for `second-agent`" in body["note"]


async def test_an_archived_dataset_is_not_offered_as_an_example(
    seeded: ServiceContext,
) -> None:
    """``find_datasets`` excludes archives, so the example inherits that.

    Worth its own test because ``dataset_get`` deliberately *does* return an
    archived dataset (with a warning), so the two behaviours differ and the
    difference is load-bearing: an example is a discovery surface.
    """
    first = await resource_body(seeded, "agentprops://examples/dataset")
    await invoke(seeded, "dataset_archive", dataset_id=first["dataset_id"])
    second = await resource_body(seeded, "agentprops://examples/dataset")
    assert second["dataset_id"] != first["dataset_id"], "an archived dataset is still the example"
    assert second["available"] is True


async def test_an_unknown_resource_uri_is_a_protocol_miss(seeded: ServiceContext) -> None:
    """No envelope here, and that is ruling R-43(b) rather than an omission.

    ``resources/read`` has its own shape: an unknown URI is a protocol error,
    not an ``AP-004`` envelope, because there is no envelope on this surface to
    put a finding in.
    """
    async with connected(seeded) as client:
        with pytest.raises(Exception, match="Unknown resource"):
            await client.read_resource("agentprops://catalogue/no-such-thing")


async def test_repeated_reads_are_byte_identical(seeded: ServiceContext) -> None:
    """M4's determinism criterion, extended to the new surface.

    Nothing in a resource body is a clock reading and nothing is re-sorted in
    Python, so two reads of one URI serialise identically. A body that carried a
    timestamp would fail this.
    """
    for uri in (CATALOGUE, EXAMPLE_BLUEPRINT, EXAMPLE_DATASET, ONE_BLUEPRINT):
        first = await resource_body(seeded, uri)
        second = await resource_body(seeded, uri)
        assert json.dumps(first) == json.dumps(second), f"{uri} is not deterministic"


async def test_repeated_prompt_reads_are_byte_identical(seeded: ServiceContext) -> None:
    for name in (
        "author-a-blueprint",
        "fill-a-dataset",
        "cover-the-label-space",
        "wire-an-agent",
    ):
        arguments = (
            {"agent_id": AGENT} if name in {"fill-a-dataset", "cover-the-label-space"} else {}
        )
        first = await prompt_text(seeded, name, **arguments)
        second = await prompt_text(seeded, name, **arguments)
        assert first == second, f"{name} is not deterministic"


async def test_the_prompts_and_the_resources_agree_about_the_example(
    seeded: ServiceContext,
) -> None:
    """One decision site, asserted across the two surfaces that read it.

    ``service/examples.py::example_agent_id`` is the only place that picks. If
    it were duplicated, this is the test that would catch the two copies
    disagreeing - which is a state a caller would experience as a prompt
    pointing at the wrong document.
    """
    text = await prompt_text(seeded, "author-a-blueprint")
    body = await resource_body(seeded, "agentprops://examples/blueprint")
    assert body["uri"] in text
    assert f"`{body['agent_id']}` at `{body['version']}`" in text


# ----------------------------------------------------- agentprops://orientation


async def test_every_tool_the_orientation_names_is_a_registered_tool(
    seeded: ServiceContext,
) -> None:
    """The ordering document, checked against the tool registry it orders.

    This is the whole reason the resource is safe to write. A phase naming
    `dataset_publish` - a plausible tool this server does not have - would send
    a caller looking for it, and no amount of proofreading catches that
    reliably. Asking the running server instead means a tool renamed on Tuesday
    fails this on Tuesday.

    ``tools/list`` is the authority rather than a constant in this file: a list
    kept here would drift in exactly the way the resource is being guarded
    against.
    """
    body = await resource_body(seeded, "agentprops://orientation")
    async with connected(seeded) as client:
        registered = {tool.name for tool in (await client.list_tools()).tools}

    named = {tool for phase in body["phases"] for tool in phase["tools"]}
    assert named, "the orientation names no tools at all, so this guard proves nothing"
    assert named <= registered, (
        f"the orientation names tools this server does not register: {sorted(named - registered)}"
    )


async def test_the_orientation_covers_the_run_lifecycle_in_order(
    seeded: ServiceContext,
) -> None:
    """The four runtime calls, in the order a caller has to make them.

    The sequence is the payload here - a caller who calls ``record_step`` before
    ``fetch_step`` gets ``AP-004`` - so the assertion is on relative position
    rather than on presence. The read-back phase follows, because a run nobody
    reads back was not checked.
    """
    body = await resource_body(seeded, "agentprops://orientation")
    flat = " | ".join(entry for phase in body["phases"] for entry in phase.get("sequence", []))
    positions = [
        flat.index(call) for call in ("run_start", "fetch_step", "record_step", "run_finish")
    ]
    assert positions == sorted(positions), f"the runtime calls are out of order: {flat}"
    assert flat.index("run_finish") < flat.index("run_evidence"), (
        "inspection is documented before the run it inspects is closed"
    )


async def test_the_orientation_sends_an_empty_store_to_the_authoring_phase(
    context: ServiceContext,
) -> None:
    """``you_are_here`` with nothing published.

    Takes ``context`` and never ``seeded``: the ``seeded`` fixture publishes into
    that same object, so a test asking for both would be handed one full store
    and would assert emptiness against a store that has two datasets in it.
    """
    body = await resource_body(context, "agentprops://orientation")
    assert body["published_agents"] == []
    assert "Phase 2" in body["you_are_here"]


async def test_the_orientation_sends_a_stocked_store_to_the_running_phase(
    seeded: ServiceContext,
) -> None:
    """The other end of the arc, which is what makes the field worth serving.

    Paired with the test above: if both ends reported the same phase, the field
    would be a constant dressed as a measurement.
    """
    body = await resource_body(seeded, "agentprops://orientation")
    assert body["published_agents"] == ["location-onboarding"]
    assert "Phase 4" in body["you_are_here"]


async def test_the_catalogue_reaches_the_orientation(seeded: ServiceContext) -> None:
    """The index promises every other URI is reachable from it; this is one of them.

    Without this the orientation is discoverable only by ``resources/list``, and
    the catalogue's own claim to be the index would be false.
    """
    body = await resource_body(seeded, "agentprops://catalogue")
    assert body["start_here"]["orientation"] == "agentprops://orientation"
