"""``blueprint_diff``'s computation. Pure, total, and never a failure signal.

contracts section 4 named four categories - "nodes added, removed, changed;
edges added, removed; schema changes per node; label vocabulary changes" - and
one property: **informational, never a failure signal.** Nothing in this module
can fail. It takes two raw blueprint documents, either of which may be ``None``,
and always produces a diff.

**Ruling R-41 adds a fifth**, ``blueprint``, covering the blueprint-level fields
the four left unreportable: ``entry_node``, ``description``, ``outcome_schema``
and the **entity schemas**. M4 shipped without it and recorded the gap as an
owner question; R-41 closed the question the other way, and its reasoning is
worth keeping here: R-27 has just made entity schemas a validated part of a
blueprint, so a version could change one and this diff - the tool that exists to
answer "what changed between these two versions" - would have shown nothing. The
category is additive and inherits totality and "never a failure signal" for
free, because nothing here can raise.

The shape, which nothing specifies
----------------------------------

Defined here because the contract names the categories and not the payload::

    {
      "agent_id": "location-onboarding",
      "from": {"version": "1.0.0", "present": true,  "status": "published"},
      "to":   {"version": "1.1.0", "present": false, "status": null},
      "nodes": {
        "added":   ["escalate_manual"],
        "removed": ["old_check"],
        "changed": [
          {"node_id": "check_docs",
           "changes": [{"field": "kind", "from": "decision", "to": "llm"}]}
        ]
      },
      "edges": {
        "added":   [{"from": "check_docs", "to": "escalate_manual", "condition": {...}}],
        "removed": [{"from": "check_docs", "to": "old_check",       "condition": null}]
      },
      "schemas": [
        {"node_id": "check_docs", "field": "output_schema", "from": {...}, "to": {...}}
      ],
      "labels": {
        "dimensions_added":   ["region"],
        "dimensions_removed": [],
        "values_added":   [{"dimension": "tier", "values": ["national"]}],
        "values_removed": []
      },
      "blueprint": {
        "changes": [{"field": "entry_node", "from": "receive_request", "to": "intake"}],
        "entities": {
          "added":   ["franchisee"],
          "removed": [],
          "changed": [{"entity_id": "store", "field": "schema", "from": { }, "to": { }}]
        }
      }
    }

Six decisions in that shape, each of which could have gone the other way.

**``nodes.changed`` and ``schemas`` partition a node's fields; they never
overlap.** ``changed`` reports the five scalar fields (``tool_name``, ``kind``,
``pool``, ``max_iterations``, ``notes``); ``schemas`` reports ``input_schema``
and ``output_schema``. So a node whose only difference is a schema appears in
``schemas`` and *not* in ``nodes.changed``, and "which nodes differ at all?" is
the union of three lists. The alternative - repeating the before/after schema
blobs inside ``nodes.changed`` too - makes each category independently
consumable at the cost of duplicating the largest values in the document, twice
over, in a payload an LLM may be reading.

**Edge identity is ``(from, to, condition)``, so a changed condition shows up as
one removed edge plus one added edge.** The contract gives edges only *added*
and *removed*, with no *changed*; under identity ``(from, to)`` alone a
condition change would be neither, and therefore invisible. Splitting it keeps
the diff total within the categories the contract names.

**A missing version is not an error.** ``present: false`` and an empty
blueprint, so a diff against a version that does not exist reports every node in
the other version as added or removed. That is R-03's and ground rule 3's
reading applied to a read: the service never gates, and "never a failure signal"
in the contract row is unconditional. The *service* attaches a warning; this
module just computes.

**Everything is sorted.** Node ids, edges, schema entries, dimensions and
values, all in a total order, because M4's acceptance criterion is
byte-identical output across repeated identical calls and a set iteration order
is not that.

**No timestamp.** For the same reason.

**``blueprint.changes`` and ``blueprint.entities`` mirror the shapes already
here** rather than inventing a third convention. ``changes`` is the same
``{field, from, to}`` triple ``nodes.changed`` uses, through the same helper, so
the two cannot drift into two spellings of one thing. ``entities`` is the same
added/removed/changed split ``nodes`` uses, and its ``changed`` entry carries
``field: "schema"`` the way a ``schemas`` entry does. An entity has exactly two
fields - its id, which is the identity, and its schema - so ``schema`` is the
only field that can appear there today; naming it keeps the entry readable if
``EntitySchema`` ever gains a second.

``outcome_schema`` sits in ``blueprint.changes`` rather than in ``schemas``
because that category is documented as *per-node* and this schema belongs to no
node.

What is *still* not in the diff
-------------------------------

Nothing structural. Every top-level field of a blueprint is now covered:
``agent_id`` and ``version`` are the diff's own subject, ``nodes``, ``edges`` and
``label_schema`` have their own categories, and ``entry_node``,
``description``, ``outcome_schema`` and ``entities`` are the fifth. A field
added to ``Blueprint`` in future is the one thing that could slip through, and
``test_service_diff.py::test_every_blueprint_field_is_covered_by_some_category``
is what fails when one does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from agentprops.validation.context import as_mapping, as_sequence, as_text
from agentprops.validation.jsonschemas import canonical

__all__ = [
    "BLUEPRINT_FIELDS",
    "EDGE_KEYS",
    "ENTITY_FIELDS",
    "NODE_SCALAR_FIELDS",
    "SCHEMA_FIELDS",
    "diff_blueprints",
]

#: The node fields ``nodes.changed`` reports, in the order it reports them.
NODE_SCALAR_FIELDS: Final[tuple[str, ...]] = (
    "tool_name",
    "kind",
    "pool",
    "max_iterations",
    "notes",
)

#: The node fields the ``schemas`` category owns. Disjoint from the above.
SCHEMA_FIELDS: Final[tuple[str, ...]] = ("input_schema", "output_schema")

#: An edge's identity, and the key order of an edge entry in the diff.
EDGE_KEYS: Final[tuple[str, ...]] = ("from", "to", "condition")

#: The blueprint-level fields the ``blueprint`` category reports, in the order it
#: reports them (ruling R-41).
BLUEPRINT_FIELDS: Final[tuple[str, ...]] = ("entry_node", "description", "outcome_schema")

#: The entity field ``blueprint.entities`` compares. An entity has an id and a
#: schema; the id is the identity, so this is the rest of it.
ENTITY_FIELDS: Final[tuple[str, ...]] = ("schema",)


def diff_blueprints(
    agent_id: str,
    from_version: str,
    from_document: Mapping[str, Any] | None,
    to_version: str,
    to_document: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The structured diff. Total: no input makes this raise or report failure."""
    before = as_mapping(from_document)
    after = as_mapping(to_document)
    return {
        "agent_id": agent_id,
        "from": _side(from_version, from_document),
        "to": _side(to_version, to_document),
        "nodes": _nodes(before, after),
        "edges": _edges(before, after),
        "schemas": _schemas(before, after),
        "labels": _labels(before, after),
        "blueprint": _blueprint(before, after),
    }


def _side(version: str, document: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "version": version,
        "present": document is not None,
        "status": as_text(as_mapping(document).get("status")),
    }


def _nodes_by_id(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Nodes keyed by id, skipping anything without a string id.

    A stored blueprint has unique string node ids - BP-003 and the model both
    say so - but this module must not raise on a document it can be handed, and
    a draft read straight out of the store is a document.
    """
    nodes: dict[str, Mapping[str, Any]] = {}
    for entry in as_sequence(document.get("nodes")):
        node = as_mapping(entry)
        node_id = as_text(node.get("id"))
        if node_id is not None:
            nodes[node_id] = node
    return nodes


def _nodes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    old = _nodes_by_id(before)
    new = _nodes_by_id(after)
    changed = [
        {"node_id": node_id, "changes": changes}
        for node_id in sorted(old.keys() & new.keys())
        if (changes := _scalar_changes(old[node_id], new[node_id]))
    ]
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": changed,
    }


def _scalar_changes(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _field_changes(old, new, NODE_SCALAR_FIELDS)


def _field_changes(
    old: Mapping[str, Any], new: Mapping[str, Any], fields: Sequence[str]
) -> list[dict[str, Any]]:
    """``{field, from, to}`` for each of ``fields`` that differs canonically.

    Shared by ``nodes.changed`` and ``blueprint.changes``, so the two categories
    cannot drift into two spellings of one triple.
    """
    return [
        {"field": name, "from": old.get(name), "to": new.get(name)}
        for name in fields
        if canonical(old.get(name)) != canonical(new.get(name))
    ]


def _schemas(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    old = _nodes_by_id(before)
    new = _nodes_by_id(after)
    return [
        {
            "node_id": node_id,
            "field": name,
            "from": old[node_id].get(name),
            "to": new[node_id].get(name),
        }
        for node_id in sorted(old.keys() & new.keys())
        for name in SCHEMA_FIELDS
        if canonical(old[node_id].get(name)) != canonical(new[node_id].get(name))
    ]


def _edge_set(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Edges keyed by their canonical identity, so ordering is total.

    The key is the canonical form of ``(from, to, condition)``, which is what
    makes a changed condition read as one removal plus one addition rather than
    disappearing.
    """
    edges: dict[str, dict[str, Any]] = {}
    for entry in as_sequence(document.get("edges")):
        edge = as_mapping(entry)
        record = {key: edge.get(key) for key in EDGE_KEYS}
        edges[canonical([record[key] for key in EDGE_KEYS])] = record
    return edges


def _edges(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    old = _edge_set(before)
    new = _edge_set(after)
    return {
        "added": [new[key] for key in sorted(new.keys() - old.keys())],
        "removed": [old[key] for key in sorted(old.keys() - new.keys())],
    }


def _entities_by_id(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Entities keyed by id, skipping anything without a string id.

    The mirror of :func:`_nodes_by_id`, and total for the same reason: BP-015
    makes entity ids unique in a stored blueprint, but this module must not raise
    on a document it can be handed.
    """
    entities: dict[str, Mapping[str, Any]] = {}
    for entry in as_sequence(document.get("entities")):
        entity = as_mapping(entry)
        entity_id = as_text(entity.get("id"))
        if entity_id is not None:
            entities[entity_id] = entity
    return entities


def _blueprint(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Ruling R-41's fifth category: blueprint-level fields and entity schemas."""
    old = _entities_by_id(before)
    new = _entities_by_id(after)
    return {
        "changes": _field_changes(before, after, BLUEPRINT_FIELDS),
        "entities": {
            "added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "changed": [
                {
                    "entity_id": entity_id,
                    "field": name,
                    "from": old[entity_id].get(name),
                    "to": new[entity_id].get(name),
                }
                for entity_id in sorted(old.keys() & new.keys())
                for name in ENTITY_FIELDS
                if canonical(old[entity_id].get(name)) != canonical(new[entity_id].get(name))
            ],
        },
    }


def _dimensions(document: Mapping[str, Any]) -> dict[str, list[str]]:
    schema = as_mapping(as_mapping(document.get("label_schema")).get("dimensions"))
    return {
        str(dimension): [str(value) for value in as_sequence(values)]
        for dimension, values in schema.items()
    }


def _labels(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    old = _dimensions(before)
    new = _dimensions(after)
    shared = sorted(old.keys() & new.keys())
    return {
        "dimensions_added": sorted(new.keys() - old.keys()),
        "dimensions_removed": sorted(old.keys() - new.keys()),
        "values_added": _value_delta(shared, old, new),
        "values_removed": _value_delta(shared, new, old),
    }


def _value_delta(
    dimensions: Sequence[str], subtrahend: Mapping[str, list[str]], minuend: Mapping[str, list[str]]
) -> list[dict[str, Any]]:
    """``minuend`` minus ``subtrahend``, per shared dimension, sorted."""
    deltas: list[dict[str, Any]] = []
    for dimension in dimensions:
        values = sorted(set(minuend[dimension]) - set(subtrahend[dimension]))
        if values:
            deltas.append({"dimension": dimension, "values": values})
    return deltas
