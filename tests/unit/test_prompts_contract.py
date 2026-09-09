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


async def test_cover_the_label_space_promises_nothing_in_an_empty_store(
    context: ServiceContext,
) -> None:
    text = await prompt_text(context, "cover-the-label-space", agent_id=AGENT)
    assert "no published agent-props blueprint" in text
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
        "run id is generated by the client",
        "compare.grade",
        "never compares",
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
    out of the catalogue, and every URI the catalogue names is one
    ``resources/read`` answers with an available document.
    """
    body = await resource_body(seeded, "agentprops://catalogue")
    assert [agent["agent_id"] for agent in body["agents"]] == [AGENT]
    assert body["agents"][0]["published_versions"] == [VERSION]
    assert body["agents"][0]["dataset_count"] == 2
    assert body["start_here"] == {"blueprint": EXAMPLE_BLUEPRINT, "dataset": EXAMPLE_DATASET}

    for uri in [
        *body["agents"][0]["blueprints"],
        body["agents"][0]["example_dataset"],
        *body["start_here"].values(),
    ]:
        named = await resource_body(seeded, uri)
        assert named["available"] is True, f"the catalogue names {uri}, which is not available"


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
