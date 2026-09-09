"""The registered MCP resources: the canonical examples, read from this store.

Registration only. Every body is composed in `service/examples.py`, which is
where store reads live, and every URI string comes from there too so the
decorator and the prose that names it cannot drift - the module docstring there
explains why that is the workable arrangement rather than the tidy one.

Three static resources and two templates
----------------------------------------

``resources/list`` returns the three static ones, and that is what makes ruling
R-76's "find something to imitate **without being told an id**" true: two of
them *are* documents to imitate and need no argument, and the third names every
other URI this store can serve. The two templates carry the per-agent forms
R-76 asks for - every published blueprint, and one example dataset per agent -
and appear under ``resources/templates/list``.

Why the static set is fixed rather than one entry per stored document
---------------------------------------------------------------------

R-76 reads as though ``resources/list`` should enumerate one entry per published
blueprint. On `mcp` 2.2.0 that is not reachable through the public API, and the
two ways to fake it are both worse than the index:

- ``MCPServer`` serves ``resources/list`` from a registry populated at *import*
  time. There is no listing callback, and ``Extension.methods()`` refuses to
  replace an already-registered handler ("extension methods are additive and
  cannot replace another handler"), so the only override is
  ``mcp._lowlevel_server.add_request_handler`` - a private attribute, which is
  exactly the kind of dependency ruling R-16 was written after.
- ``mcp.add_resource(...)`` is public and can be called from :func:`bind`. It
  would freeze the list at bind time, so a blueprint published mid-session
  never appears; and because ``binding()`` nests and restores in tests while
  the resource registry does not, one test's store would leak resources into
  the next. A stale list that is also cross-contaminating is not an improvement
  on an index.

So the *set* of resources is a property of the server and only the *bodies* are
store-derived. Every body carries ``available`` and a ``note`` whether or not
there is anything to show, which is how the empty store is handled honestly -
`service/examples.py` has the reasoning, and the empty-store behaviour is
tested at both ends.
"""

from __future__ import annotations

from typing import Any, Final

from agentprops.server.app import bound, mcp
from agentprops.service import examples

__all__ = ["agent_example_dataset", "blueprint", "catalogue", "example_blueprint"]

#: Every resource here is a JSON document. The SDK's default is ``text/plain``,
#: so this has to be passed rather than assumed, and
#: ``test_prompt_and_resource_surface.py`` parses every body rather than
#: trusting the header.
JSON: Final = "application/json"


@mcp.resource(
    examples.CATALOGUE_URI,
    name="agentprops-catalogue",
    title="What this store holds",
    mime_type=JSON,
)
def catalogue() -> dict[str, Any]:
    """Every agent with a published blueprint, and a resource URI for each thing it has.

    The index: read this and you need no id told to you. Lists published
    versions, a per-agent dataset count, and the URI of one example dataset per
    agent. An empty store answers `{"agents": []}` with the next step to take.
    """
    return examples.catalogue(bound())


@mcp.resource(
    examples.EXAMPLE_BLUEPRINT_URI,
    name="example-blueprint",
    title="A known-good blueprint from this store",
    mime_type=JSON,
)
def example_blueprint() -> dict[str, Any]:
    """One published blueprint document in full, chosen by this store. No id needed.

    The shape to imitate when authoring one. Byte-identical to what
    `blueprint_get` serves for the same version. Reports
    `{"available": false}` with a reason when this store has published none.
    """
    return examples.blueprint_body(bound(), "", "")


@mcp.resource(
    examples.EXAMPLE_DATASET_URI,
    name="example-dataset",
    title="A known-good dataset from this store",
    mime_type=JSON,
)
def example_dataset() -> dict[str, Any]:
    """One submitted dataset document in full, chosen by this store. No id needed.

    What a filled dataset looks like: a fixture per node, an entity timeline, a
    narrative and an expected outcome. Byte-identical to what `dataset_get`
    serves. Reports `{"available": false}` with a reason when this store holds
    no dataset.
    """
    return examples.dataset_body(bound(), "")


@mcp.resource(
    examples.BLUEPRINT_URI_TEMPLATE,
    name="blueprint",
    title="One published blueprint",
    mime_type=JSON,
)
def blueprint(agent_id: str, version: str) -> dict[str, Any]:
    """One published blueprint document, by agent and version.

    The URIs `agentprops://catalogue` enumerates. A draft version is not served
    here - a resource is a known-good document - so it reports
    `{"available": false}`; `blueprint_get` returns drafts.
    """
    return examples.blueprint_body(bound(), agent_id, version)


@mcp.resource(
    examples.AGENT_EXAMPLE_DATASET_URI_TEMPLATE,
    name="agent-example-dataset",
    title="One agent's example dataset",
    mime_type=JSON,
)
def agent_example_dataset(agent_id: str) -> dict[str, Any]:
    """One submitted dataset document for this agent, in full.

    The oldest lineage at its latest version, archives excluded. Reports
    `{"available": false}` when the agent has no dataset yet, rather than
    falling back to another agent's.
    """
    return examples.dataset_body(bound(), agent_id)
