"""The canonical examples, read from **this** store. What ``resources/*`` serves.

Ruling R-76: a resource is "a known-good document in *this* store", read from
the store "rather than from `tests/fixtures/`", because a fixture-backed
resource would be a lie in any store the fixtures were never imported into.
Everything in this module therefore goes through the ``Store`` Protocol and
nothing reads a file.

Why the URI strings live here and not in `server/`
--------------------------------------------------

A URI is protocol surface, so `server/resources.py` would be the obvious home.
It is not the workable one: :func:`catalogue` puts the URIs *inside* a document,
and `service/prompts.py` names them in prose. If `server/` owned the strings,
both of those would have to be handed them or would spell them again - and a
prompt that names ``agentprops://examples/blueprint`` while the decorator
registers ``agentprops://example/blueprint`` is exactly the drift R-76 exists to
close. One home, imported by the decorators.

:data:`BLUEPRINT_URI_TEMPLATE` is both an RFC 6570 template *and* a
``str.format`` template, because the two syntaxes coincide for the simple
``{name}`` form. That coincidence is load-bearing rather than cute - it is what
lets the registered template and the built URI come from one string - so
``test_prompt_and_resource_surface.py`` round-trips
``UriTemplate.parse(TEMPLATE).match(builder(...))`` rather than trusting it.

The two "which document is the example" decisions, each with one site
-------------------------------------------------------------------

**The example blueprint** is the latest *published* version of the first agent
in ``list_blueprints(published)`` order, which ruling R-35 made total. "Latest
published" is not a second choice: it is what ``get_blueprint(agent_id, None)``
already means, so this reuses the store's own resolution instead of inventing
one. :func:`example_agent_id` is the only place that picks.

**The example dataset** is the first row of ``find_datasets`` - the oldest
lineage, at its latest version, archives excluded, in the total ``(created_at,
id)`` order rulings R-38 and R-09 fix.

Those two are computed **independently**, and the alternative was rejected on
purpose. Pinning the example dataset to the example blueprint's agent would give
a caller a matching pair, which reads better; it would also report "no example
dataset" whenever *that* agent has none, while the store holds plenty for other
agents. A false "unavailable" is the failure this milestone is about, and a
matching pair is not worth buying one. Every body names its own ``agent_id``, so
nothing is ambiguous.

The empty store
---------------

``resources/list`` cannot be empty here: the *set* of resources is a property of
the server (they are registered at import time), and only their *bodies* are
store-derived. So every body carries ``available`` and a ``note`` - the same
keys whether or not there is anything to show - and the note says either what
the document is or why there is none. A resource that promised an example this
store does not hold would be the dishonesty R-76 names; an opaque protocol error
would be the same dishonesty with less information.

**These bodies are not envelopes.** Ruling R-43(b)'s one-named-key ``data``
convention governs a *tool*'s success payload. ``resources/read`` has its own
protocol shape, so there is no ``ok``, no ``warnings`` and no ``data`` key here
- R-76 says so explicitly.
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import quote

from agentprops.models import Blueprint, Dataset, DatasetQuery
from agentprops.service.blueprints import document as blueprint_document
from agentprops.service.context import ServiceContext
from agentprops.service.datasets import document as dataset_document
from agentprops.storage import STATUS_PUBLISHED

__all__ = [
    "AGENT_EXAMPLE_DATASET_URI_TEMPLATE",
    "BLUEPRINT_URI_TEMPLATE",
    "CATALOGUE_URI",
    "EXAMPLE_BLUEPRINT_URI",
    "EXAMPLE_DATASET_URI",
    "ORIENTATION_URI",
    "agent_example_dataset_uri",
    "blueprint_body",
    "blueprint_uri",
    "catalogue",
    "dataset_body",
    "example_agent_id",
    "example_blueprint",
    "example_dataset",
    "orientation",
    "published",
    "published_agent_ids",
    "published_blueprint",
]

#: The index. Every other URI this store serves is reachable from its body, so
#: a caller who has listed the resources needs no id told to them - which is the
#: hard-coded ``location-onboarding`` R-76 removes.
CATALOGUE_URI: Final = "agentprops://catalogue"

#: The order of operations. Static text plus two store-derived facts, and the
#: one surface that answers "what do I call, and when" without a prompt - a
#: harness that lists resources but not prompts still finds it.
ORIENTATION_URI: Final = "agentprops://orientation"

#: One known-good published blueprint, in full, with no id needed to ask.
EXAMPLE_BLUEPRINT_URI: Final = "agentprops://examples/blueprint"

#: One known-good dataset, in full, with no id needed to ask.
EXAMPLE_DATASET_URI: Final = "agentprops://examples/dataset"

#: Every published blueprint, addressably. Enumerated by :func:`catalogue`.
BLUEPRINT_URI_TEMPLATE: Final = "agentprops://blueprint/{agent_id}/{version}"

#: One example dataset **per agent**, which is the other half of R-76's
#: "expose the published blueprints and one example dataset per agent".
#:
#: The literal ``example`` sits at the *end* deliberately: a template of
#: ``agentprops://examples/dataset/{agent_id}`` would share a prefix with
#: :data:`EXAMPLE_DATASET_URI`, and a coverage guard that asks "does any tested
#: URI match this template" could then be satisfied by the static resource
#: instead of by a test of the template.
AGENT_EXAMPLE_DATASET_URI_TEMPLATE: Final = "agentprops://dataset/{agent_id}/example"


def blueprint_uri(agent_id: str, version: str) -> str:
    """The URI of one published blueprint. ``safe=""`` so a ``/`` cannot escape."""
    return BLUEPRINT_URI_TEMPLATE.format(
        agent_id=quote(agent_id, safe=""), version=quote(version, safe="")
    )


def agent_example_dataset_uri(agent_id: str) -> str:
    """The URI of one agent's example dataset."""
    return AGENT_EXAMPLE_DATASET_URI_TEMPLATE.format(agent_id=quote(agent_id, safe=""))


def published(context: ServiceContext) -> dict[str, list[str]]:
    """Published versions per agent, in the store's own order. One store call.

    Published only. A draft is a legitimate thing to read and is not a
    *known-good* document, which is what a resource is here; ``agent_list`` and
    ``blueprint_list`` are the surfaces that show drafts.

    Not re-sorted, in either dimension: ``list_blueprints`` is ordered
    ``(agent_id, semver)`` per ruling R-35, and a Python sort would put
    ``1.10.0`` before ``1.9.0``. This is the same grouping
    ``service/admin.py::agents`` does, over the published rows rather than all
    of them.
    """
    grouped: dict[str, list[str]] = {}
    for summary in context.store.list_blueprints(STATUS_PUBLISHED):
        grouped.setdefault(summary.agent_id, []).append(summary.version)
    return grouped


def published_agent_ids(context: ServiceContext) -> list[str]:
    """Every agent with at least one published blueprint, in the store's order."""
    return list(published(context))


def example_agent_id(context: ServiceContext) -> str:
    """The agent whose blueprint is *the* example, or ``""`` if there is none.

    The one decision site. Both :func:`example_blueprint` and the prompts read
    it, so "which agent do we point a caller at" is answered once.
    """
    agents = published_agent_ids(context)
    return agents[0] if agents else ""


def published_blueprint(context: ServiceContext, agent_id: str, version: str) -> Blueprint | None:
    """The **published** blueprint at that version, or ``None``. No sentinel.

    The one place the status check lives, and it is not redundant - a test
    caught it being absent. ``Store.get_blueprint`` documents itself as "with
    ``version``, that exact version **whatever its status**", so a draft at a
    named version comes back, and two surfaces were handing it out: a resource
    served it as a canonical example, and ``fill-a-dataset`` told a caller to
    author datasets against it. Neither is answerable - ``blueprint_get`` is the
    surface that returns a draft, and DS-001 requires "an existing *published*
    blueprint at that exact version" before a dataset can name one.

    An empty ``agent_id`` is a miss rather than a substitution. Only
    :func:`example_blueprint` fills one in, and only because "the example" is
    what it was asked for; a prompt that was given no agent must not be answered
    about somebody else's.

    ``version`` empty means the latest published version, which is what
    ``get_blueprint(agent_id, None)`` already resolves.
    """
    if not agent_id:
        return None
    blueprint = context.store.get_blueprint(agent_id, version or None)
    if blueprint is None or blueprint.status != STATUS_PUBLISHED:
        return None
    return blueprint


def example_blueprint(context: ServiceContext, agent_id: str, version: str) -> Blueprint | None:
    """One published blueprint to imitate, or ``None``.

    :func:`published_blueprint` plus the one sentinel this surface needs: an
    empty ``agent_id`` means :func:`example_agent_id`, because a caller asking
    for "the example" has asked to be told which one.
    """
    return published_blueprint(context, agent_id or example_agent_id(context), version)


def example_dataset(context: ServiceContext, agent_id: str) -> Dataset | None:
    """One dataset to imitate, or ``None``. ``agent_id`` empty means any agent.

    ``find_datasets`` already excludes archived lineages and already orders
    totally, so ``limit=1`` names one row without a tie-break of our own.
    """
    rows = context.store.find_datasets(DatasetQuery(agent_id=agent_id or None, limit=1))
    if not rows:
        return None
    return context.store.get_dataset(str(rows[0].id), None)


def blueprint_body(context: ServiceContext, agent_id: str, version: str) -> dict[str, Any]:
    """The ``resources/read`` body for a blueprint example.

    The document itself comes from :func:`agentprops.service.blueprints.document`
    - the same function ``blueprint_get`` serves - so a resource and the tool
    hand back byte-identical bytes rather than two dumps that agree today.
    """
    blueprint = example_blueprint(context, agent_id, version)
    if blueprint is None:
        return {
            "available": False,
            "agent_id": agent_id or None,
            "version": version or None,
            "uri": None,
            "blueprint": None,
            "note": _absent_blueprint_note(agent_id, version),
        }
    return {
        "available": True,
        "agent_id": blueprint.agent_id,
        "version": blueprint.version,
        "uri": blueprint_uri(blueprint.agent_id, blueprint.version),
        "blueprint": blueprint_document(blueprint),
        "note": (
            "A published blueprint from this store. Copy its structure, not its content. "
            "The `author-a-blueprint` prompt carries the node, edge, entity and label contract."
        ),
    }


def dataset_body(context: ServiceContext, agent_id: str) -> dict[str, Any]:
    """The ``resources/read`` body for a dataset example.

    Same fixed key set whether or not there is one, for the reason the module
    docstring gives.
    """
    dataset = example_dataset(context, agent_id)
    if dataset is None:
        return {
            "available": False,
            "agent_id": agent_id or None,
            "dataset_id": None,
            "version": None,
            "uri": None,
            "dataset": None,
            "note": _absent_dataset_note(agent_id),
        }
    return {
        "available": True,
        "agent_id": dataset.blueprint.agent_id,
        "dataset_id": str(dataset.id),
        "version": dataset.version,
        "uri": agent_example_dataset_uri(dataset.blueprint.agent_id),
        "dataset": dataset_document(dataset),
        "note": (
            "A submitted dataset from this store, at its latest version. "
            "`dataset_skeleton` returns the fill mechanics; the `fill-a-dataset` prompt "
            "carries the intent."
        ),
    }


def catalogue(context: ServiceContext) -> dict[str, Any]:
    """What this store holds, with a URI for each thing in it.

    Deliberately does **not** repeat ``store_status``: a backend name and three
    row counts are that tool's answer, and a second copy here would be a second
    place to drift, one layer in. This says only what is addressable.

    **Known cost, accepted.** ``dataset_count`` reads full ``DatasetSummary``
    rows per agent only to ``len()`` them. The ``Store`` Protocol offers no
    per-agent count, and widening it is a storage-contract change that ruling
    R-76 puts out of scope ("no change to a rule, an envelope, or the storage
    contract"). ``service/admin.py::agents`` pays exactly the same cost for
    exactly the same reason, so this is the surface's existing shape rather than
    a new one; the remedy, if it ever matters, is a counting method on the
    Protocol rather than a page size here.
    """
    agents = [
        {
            "agent_id": agent_id,
            "published_versions": versions,
            "blueprints": [blueprint_uri(agent_id, version) for version in versions],
            "dataset_count": len(context.store.find_datasets(DatasetQuery(agent_id=agent_id))),
            "example_dataset": agent_example_dataset_uri(agent_id),
        }
        for agent_id, versions in published(context).items()
    ]
    return {
        "agents": agents,
        "start_here": {
            "orientation": ORIENTATION_URI,
            "blueprint": EXAMPLE_BLUEPRINT_URI,
            "dataset": EXAMPLE_DATASET_URI,
        },
        "next_step": (
            "Read agentprops://examples/blueprint for the shape to imitate, then "
            "agentprops://examples/dataset for a filled world. The `author-a-blueprint` "
            "and `fill-a-dataset` prompts carry the rest."
            if agents
            else "This store holds no published blueprint, so there is nothing to imitate "
            "yet. Run the `author-a-blueprint` prompt: whatever you publish becomes the "
            "example the next caller reads."
        ),
    }


#: The five phases, each naming the tools that carry it. Ordered, because the
#: order is the part a tool description cannot express: every tool documents
#: itself and none of them says which one comes next.
#:
#: Phrasing is deliberately not lifted from any tracked document - ruling R-78's
#: guard holds served text and markdown apart, and this is served text.
_PHASES: Final[tuple[dict[str, Any], ...]] = (
    {
        "phase": "1. orient",
        "goal": "Learn what this store already holds before adding to it.",
        "tools": ["store_status", "agent_list", "blueprint_list", "dataset_find"],
        "resources": [CATALOGUE_URI, EXAMPLE_BLUEPRINT_URI, EXAMPLE_DATASET_URI],
        "notes": (
            "Read the two example resources first. They are real documents out of this "
            "store rather than illustrations, so their structure is safe to copy."
        ),
    },
    {
        "phase": "2. author a blueprint",
        "goal": "Describe the agent's steps as a graph, once.",
        "prompt": "author-a-blueprint",
        "tools": ["blueprint_validate", "blueprint_upsert", "blueprint_get", "blueprint_diff"],
        "notes": (
            "Loop on blueprint_validate until it returns no error-severity finding, then "
            "call blueprint_upsert with publish true. Model the agent from its source, "
            "because each node's tool_name has to equal the string the agent really calls. "
            "A published version can never be edited; publish a new version instead."
        ),
    },
    {
        "phase": "3. author datasets",
        "goal": "One coherent world per scenario worth testing.",
        "prompt": "fill-a-dataset, then cover-the-label-space",
        "tools": [
            "dataset_skeleton",
            "dataset_fill_part",
            "dataset_validate",
            "dataset_submit",
            "dataset_import",
            "dataset_expand",
            "label_vocabulary",
        ],
        "notes": (
            "dataset_skeleton returns an instructions field that carries the fill order "
            "and the repair loop; follow it. Aim at label coverage rather than a count - "
            "label_vocabulary reports which values still have nothing behind them."
        ),
    },
    {
        "phase": "4. run the agent against fixtures",
        "goal": "Serve one pinned world to a running agent.",
        "prompt": "wire-an-agent",
        "tools": ["run_start", "fetch_step", "record_step", "run_finish"],
        "sequence": [
            "run_start(selector) -> pins {dataset_id, dataset_version, blueprint_version} "
            "for the whole run",
            "fetch_step(node_id= or tool_name=, iteration=) -> that step's fixture",
            "record_step(actual, node_id=) -> stores what the agent produced, verbatim",
            "run_finish(outcome) -> closes the run and stores the final result, verbatim",
        ],
        "notes": (
            "Repeat the middle pair once per step the agent takes. Both writes are "
            "idempotent per key: recording the same value twice is a silent success, and "
            "a different value returns AP-007 while the first value stays put."
        ),
    },
    {
        "phase": "5. inspect what happened",
        "goal": "Read a finished run back and grade it yourself.",
        "tools": ["run_find", "run_get", "run_evidence", "run_export"],
        "sequence": [
            "run_find(agent_id=, dataset_id=, status=) -> run summaries, newest first",
            "run_get(run_id) -> the whole run: pin, path, every step, outcome, warnings",
            "run_evidence(run_id) -> expected and actual side by side, plus the comparison "
            "mode and the pinned outcome_schema",
            "run_export(run_id) -> the same run as an OpenTelemetry trace",
        ],
        "notes": (
            "run_evidence is the one to reach for: it returns everything the client's "
            "three comparison helpers take as arguments, and computes no verdict itself. "
            "There is no run view in the web app - inspection is these four tools."
        ),
    },
)

#: What a caller gets wrong when nothing tells them. Each entry is a property of
#: this surface rather than advice about testing in general.
_PRACTICES: Final[tuple[str, ...]] = (
    "Fetch a step before you record one. record_step for a node that was never served "
    "returns AP-004, because an actual with no serve behind it reports on nothing.",
    "This service stores expectations and never judges them. record_step and run_finish "
    "keep what you send unchecked; comparison belongs in your test, through "
    "agentprops_client.compare.grade with the mode the dataset declared.",
    "Nothing here refuses to serve. A policy problem arrives as a warning riding a "
    "successful reply, and even a finished run keeps answering fetch_step. Read the "
    "warnings; do not treat their absence as the only success signal.",
    "Branch on rule and pointer, never on message. Every finding carries a stable rule id "
    "and an RFC 6901 pointer at the offending field; the prose gets reworded.",
    "Validation replies have a third envelope shape: ok true, an errors list holding only "
    "warning-severity findings, and no data key at all. A client written for two shapes "
    "breaks on the first clean-with-warnings validate.",
    "Datasets are immutable and versioned. An edit writes a new version rather than "
    "changing the old one, and a run reads the version it pinned at run_start for its "
    "whole life. Nothing is ever deleted; dataset_archive hides a lineage and "
    "dataset_restore brings it back.",
    "Let the client mint the run id. It generates one when you construct it, before the "
    "first call, so an id you invent yourself will not match what the run is filed under.",
    "Point one store at everything. The MCP server your harness spawns and the HTTP "
    "service your agent dials must name the same --store, or you will author into one "
    "database and run against another.",
)


def orientation(context: ServiceContext) -> dict[str, Any]:
    """The order of operations: which endpoint to call at each phase, and why.

    The gap this fills is not a missing tool description - all twenty-seven carry
    one - but the missing *sequence* between them. A harness that lists the tools
    learns what each does and nothing about which comes first, and the four
    prompts each cover one phase without ever naming the arc they sit in.

    Store-aware in the one respect that changes the answer: ``you_are_here``
    reports whether this store has a published blueprint and a dataset yet, so
    the caller is pointed at the phase they are actually in rather than at
    phase 1 forever.
    """
    agents = published(context)
    has_dataset = any(
        context.store.find_datasets(DatasetQuery(agent_id=agent_id)) for agent_id in agents
    )
    if not agents:
        here = "Phase 2. This store has no published blueprint, so there is nothing to run yet."
    elif not has_dataset:
        here = (
            "Phase 3. Blueprints are published here but no dataset has been submitted, so "
            "run_start has nothing to pin."
        )
    else:
        here = (
            "Phase 4. This store holds published blueprints and at least one dataset, so an "
            "agent can be pointed at it now."
        )
    return {
        "what_this_is": (
            "A fixture service. Describe an agent's steps once as a blueprint, author "
            "immutable worlds against that graph as datasets, and let the agent read each "
            "step from here at test time in place of whatever it would really have called."
        ),
        "you_are_here": here,
        "published_agents": sorted(agents),
        "phases": list(_PHASES),
        "practices": list(_PRACTICES),
        "prompts": (
            "Every phase above names its prompt. Call prompts/get for the detail - the "
            "prompts hold the blueprint and dataset contracts, and they read this store, so "
            "what they tell you is true of the data in front of you."
        ),
    }


def _absent_blueprint_note(agent_id: str, version: str) -> str:
    """Why there is no blueprint to show, in the caller's own terms."""
    if agent_id:
        return (
            f"This store holds no published blueprint for `{agent_id}`"
            + (f" at version `{version}`" if version else "")
            + ". Call `agent_list` to see what it does hold, or `blueprint_upsert` with "
            "publish: true to add this one."
        )
    return (
        "This store holds no published blueprint at all. Run the `author-a-blueprint` "
        "prompt: whatever you publish becomes the example the next caller reads."
    )


def _absent_dataset_note(agent_id: str) -> str:
    """Why there is no dataset to show."""
    scope = f" for `{agent_id}`" if agent_id else ""
    return (
        f"This store holds no dataset{scope}. Run the `fill-a-dataset` prompt; "
        "`dataset_skeleton` returns the fill mechanics with the skeleton."
    )
