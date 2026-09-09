"""The four registered MCP prompts. Registration only; the text is `service/`.

Ground rule 4 is not violated by this module
--------------------------------------------

"**No LLM inside the service.** No model client, no API key handling, no judge
anywhere in `src/`." An MCP *prompt* is templated text served over a protocol
method - the same category as a tool's description, which nobody reads as a
model call. Nothing here or in `service/prompts.py` constructs a client, holds a
key, scores an answer or sends a request: each function returns a ``str``, the
protocol hands it to the caller, and the caller's own model reads it. Ruling
R-76 asks for this to be said out loud, because the word invites the wrong
reading and this is the file a reader lands on first.

Why these parameters are annotated ``str`` and not ``ObjectArg``
----------------------------------------------------------------

Every *tool* parameter on this surface is annotated ``object`` so a malformed
argument becomes an ``AP-001`` envelope rather than an SDK protocol error -
`server/args.py` carries that reasoning. It does not transfer, for two reasons
that are both properties of the protocol rather than choices:

- ``prompts/get`` takes ``arguments: dict[str, str]``. A non-string cannot
  reach a prompt function; it is refused one layer above by the request params
  model. There is no wrong-type case left for a reader to catch.
- There is no envelope on this surface to put a finding in. ``prompts/get``
  answers with messages, so ruling R-43(b) applies: do not wrap this in a
  ``SuccessEnvelope``. A prompt whose subject does not exist therefore returns
  *text* saying so - see ``service/prompts.py::_no_such_blueprint`` - which is
  the only shape the protocol offers and, unlike an error, is something a model
  can act on.

``Field(description=...)`` is not decoration: it becomes ``PromptArgument.
description``, which is what a caller reads in the slash-command UI these
prompts surface as. ``test_prompt_and_resource_surface.py`` asserts every
argument has one.

Only ``agent_id`` on two prompts is required. Everywhere else the tool surface
reads an omitted ``version`` as "the latest published version", so a prompt that
*demanded* one would be the only place on this surface where it is mandatory -
and the resolved version is interpolated into the text either way, so nothing
is left ambiguous by omitting it.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from agentprops.server.app import bound, mcp
from agentprops.service import prompts as prompt_text

__all__ = ["author_a_blueprint", "cover_the_label_space", "fill_a_dataset", "wire_an_agent"]

#: The blueprint to imitate. Optional: the store picks one, and says so when it
#: has none. This argument replaces the hard-coded id ruling R-76 cites.
ExampleAgentArg = Annotated[
    str,
    Field(
        description=(
            "An agent whose published blueprint to imitate. Omit it and this store picks "
            "one; if it holds none, the prompt says so rather than naming an id that does "
            "not resolve."
        )
    ),
]

#: The agent a prompt is about. Required.
AgentArg = Annotated[
    str, Field(description="The agent this blueprint version belongs to, as in `agent_list`.")
]

#: A blueprint version. Optional, meaning the latest published one.
VersionArg = Annotated[
    str,
    Field(description="A blueprint version, e.g. `1.0.0`. Omit it for the latest published."),
]

#: The scenarios to author, one per line.
ScenariosArg = Annotated[
    str,
    Field(
        description=(
            "The scenarios to cover, one per line. Omit them for the three a suite needs "
            "first: the happy path, the retry loop firing once, and the failure branch."
        )
    ),
]


@mcp.prompt(name="author-a-blueprint", title="Author a blueprint")
def author_a_blueprint(agent_id: ExampleAgentArg = "") -> str:
    """Read this repository's agent and author an agent-props blueprint for it.

    The long one, because nothing in the tool surface tells you the blueprint
    shape before you write one - there is no `blueprint_skeleton`. Carries the
    node, edge, entity and label contract, the validate loop, and a known-good
    example from this store to imitate when it has one.
    """
    return prompt_text.author_a_blueprint(bound(), agent_id)


@mcp.prompt(name="fill-a-dataset", title="Fill datasets for a blueprint")
def fill_a_dataset(
    agent_id: AgentArg, version: VersionArg = "", scenarios: ScenariosArg = ""
) -> str:
    """Author one dataset per scenario for a blueprint version.

    Short on purpose. `dataset_skeleton` returns an `instructions` field that
    carries the fill order, how to repair a rejection and each section's content
    shape, so this prompt supplies intent only: which agent, which version,
    which scenarios, and what to report.
    """
    return prompt_text.fill_a_dataset(bound(), agent_id, version, scenarios)


#: Where the service is reachable. Optional: the prompt shows
#: `service/prompts.py`'s default endpoint when it is omitted.
UrlArg = Annotated[
    str,
    Field(
        description=(
            "The MCP endpoint the agent should connect to, e.g. "
            "`http://127.0.0.1:8000/mcp`. Omit it for that default."
        )
    ),
]


@mcp.prompt(name="cover-the-label-space", title="Cover a blueprint's label space")
def cover_the_label_space(agent_id: AgentArg, version: VersionArg = "") -> str:
    """Close the gaps in one blueprint version's declared label space.

    Reads this store's `label_vocabulary` and names the declared label values
    that currently carry **no** dataset, plus the label combinations already
    present. Only answerable against live store contents.
    """
    return prompt_text.cover_the_label_space(bound(), agent_id, version)


@mcp.prompt(name="wire-an-agent", title="Point an agent at the fixtures")
def wire_an_agent(agent_id: ExampleAgentArg = "", url: UrlArg = "") -> str:
    """Add an agent-props fixture mode to the repository's own agent, behind one seam.

    Ordinary code editing, so this prompt is mostly about *where the seam goes*:
    one injected client rather than conditionals scattered through the agent's
    logic. Carries the `agentprops_client` call sequence, the three things that
    bite otherwise, and a `run_start` selector taken from a real dataset in this
    store.
    """
    return prompt_text.wire_an_agent(bound(), agent_id, url)
