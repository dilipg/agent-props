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
    "agent_example_dataset_uri",
    "blueprint_body",
    "blueprint_uri",
    "catalogue",
    "dataset_body",
    "example_agent_id",
    "example_blueprint",
    "example_dataset",
    "published",
    "published_agent_ids",
    "published_blueprint",
]

#: The index. Every other URI this store serves is reachable from its body, so
#: a caller who has listed the resources needs no id told to them - which is the
#: hard-coded ``location-onboarding`` R-76 removes.
CATALOGUE_URI: Final = "agentprops://catalogue"

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
