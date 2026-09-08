"""The entity timeline: DS-008, DS-009, DS-010, DS-011.

These four are the coherence rules - the ones that make a dataset a *world*
rather than a bag of fixtures. They are separated from the rest of the ``DS-*``
catalogue because they share one idea: an entity has a declared history, and
every fixture that mentions it must be consistent with where that fixture sits
in the graph.

Two of them needed a definition no document supplies.

**DS-008's mechanism** (ruling R-14 asks for one). "An entity with no
``revisions`` block has a byte-identical embedded state everywhere it appears" -
but nothing said where an entity's state *is* inside a fixture. It is defined
here as: the value at each site the node's schema marks with
``{"$ref": "entity:<id>"}``, for every fixture that lists the entity in
``entity_refs``, compared against ``entities.<id>.base``. The blueprint is what
knows the shape, so the blueprint is what locates the state. A fixture that
references an entity without embedding a copy - ``verify_compliance`` takes only
a ``store_id`` - contributes no site and cannot drift.

**DS-010's direction** (ruling R-02). The catalogue's "downstream of it, or
unreachable from it" spans every possible pair, so it would reject every
``entity@revision`` reference that can exist, including the three in the golden
fixture. R-02 states the intended rule and its equivalent form: ``after_node``
must be an ancestor of the referencing node via **at least one** path. The
equivalent form is what is implemented, because in a cyclic graph the two halves
of the original sentence are not complements - ``request_docs`` is both upstream
and downstream of ``recheck_store`` - and the golden fixture depends on the
loop. See DECISIONS.md.
"""

from collections.abc import Mapping
from typing import Any

from agentprops.models.errors import RuleError
from agentprops.validation.context import DatasetContext, as_mapping, as_sequence, as_text
from agentprops.validation.jsonschemas import canonical, entity_ref_sites
from agentprops.validation.pointers import pointer

__all__ = ["ds_008", "ds_009", "ds_010", "ds_011", "split_ref"]

FIXTURE_KEYS: tuple[tuple[str, str], ...] = (("input", "input_schema"), ("output", "output_schema"))


def split_ref(ref: str) -> tuple[str, str | None]:
    """``"store@after_docs"`` -> ``("store", "after_docs")``; ``"store"`` -> ``("store", None)``.

    The ``entity_refs`` grammar from `docs/contracts.md` section 2.2. Ruling
    R-18 splits ownership along this same seam: DS-006 checks the first element,
    DS-009 the second.
    """
    entity_id, separator, revision = ref.partition("@")
    return entity_id, revision if separator else None


def _refs_at(fixture: Mapping[str, Any]) -> list[tuple[int, str]]:
    """A fixture's ``entity_refs`` as ``(index, ref)``, skipping non-strings."""
    return [
        (index, ref)
        for index, raw in enumerate(as_sequence(fixture.get("entity_refs")))
        if (ref := as_text(raw)) is not None
    ]


def ds_008(ctx: DatasetContext) -> list[RuleError]:
    """An entity with no ``revisions`` block has a byte-identical embedded state everywhere.

    An author who needs an entity to change has to say so, once, by declaring a
    revision. Silence means constant, and this rule holds the world to it.

    Only entities without a ``revisions`` block are checked: an entity that
    declares revisions is *expected* to differ between nodes, and which state
    belongs where is DS-010's question, not this one.
    """
    findings: list[RuleError] = []
    for entity_id in sorted(ctx.ds.entities):
        if ctx.ds.has_revisions_block(entity_id):
            continue
        base = ctx.ds.entities[entity_id].get("base")
        expected = canonical(base)
        for node_id, prefix, fixture in ctx.ds.fixture_sites():
            if node_id not in ctx.blueprint.node_by_id:
                continue  # DS-003 and DS-018 own an unknown node key
            referenced = {split_ref(ref)[0] for _, ref in _refs_at(fixture)}
            if entity_id not in referenced:
                continue
            for fixture_key, schema_key in FIXTURE_KEYS:
                if fixture_key not in fixture:
                    continue
                schema = ctx.blueprint.schema_of(node_id, schema_key)
                for path, embedded in entity_ref_sites(schema, fixture[fixture_key], entity_id):
                    if canonical(embedded) == expected:
                        continue
                    findings.append(
                        ctx.error(
                            "DS-008",
                            prefix + pointer(fixture_key, *path),
                            f"entity {entity_id!r} declares no revisions, so its state in "
                            f"{node_id!r} must be identical to entities.{entity_id}.base.",
                            entity=entity_id,
                            node_id=node_id,
                        )
                    )
    return findings


def ds_009(ctx: DatasetContext) -> list[RuleError]:
    """Every ``entity@revision`` reference names a revision declared on that entity.

    The ``@revision`` segment only (ruling R-18); an unknown ``entity_id`` is
    DS-006's. When this fires for a reference, DS-010 skips that reference
    (ruling R-02), so the corpus case reports one id rather than two.
    """
    findings: list[RuleError] = []
    for node_id, prefix, fixture in ctx.ds.fixture_sites():
        for index, ref in _refs_at(fixture):
            entity_id, revision = split_ref(ref)
            if revision is None or entity_id not in ctx.ds.entities:
                continue
            if revision in ctx.ds.revisions_of(entity_id):
                continue
            findings.append(
                ctx.error(
                    "DS-009",
                    prefix + pointer("entity_refs", index),
                    f"{node_id!r} references revision {revision!r} of {entity_id!r}, which is "
                    "not declared.",
                    node_id=node_id,
                    entity=entity_id,
                    revision=revision,
                )
            )
    return findings


def ds_010(ctx: DatasetContext) -> list[RuleError]:
    """A revision may only be observed by a node the revision's ``after_node`` reaches.

    Ruling R-02: ``after_node`` must be an ancestor of the referencing node via
    at least one path. The state comes into being *after* ``after_node`` runs, so
    a fixture that could execute before it cannot show it.

    Skipped for a reference DS-009 already reported (unknown revision), for an
    entity DS-006 already reported, and for a revision whose ``after_node`` does
    not exist, which is DS-011's.
    """
    findings: list[RuleError] = []
    for node_id, prefix, fixture in ctx.ds.fixture_sites():
        if node_id not in ctx.blueprint.node_by_id:
            continue
        for index, ref in _refs_at(fixture):
            entity_id, revision = split_ref(ref)
            if revision is None or entity_id not in ctx.ds.entities:
                continue
            declared = as_mapping(ctx.ds.revisions_of(entity_id).get(revision))
            if not declared:
                continue  # DS-009 owns an unknown revision
            after_node = as_text(declared.get("after_node"))
            if after_node is None or after_node not in ctx.blueprint.node_by_id:
                continue  # DS-011 owns an unknown after_node
            downstream = ctx.blueprint.graph.reachable_from(after_node, include_start=False)
            if node_id in downstream:
                continue
            findings.append(
                ctx.error(
                    "DS-010",
                    prefix + pointer("entity_refs", index),
                    f"{node_id!r} observes {entity_id}@{revision}, but no path leads from "
                    f"{after_node!r} - which produces that state - to {node_id!r}.",
                    node_id=node_id,
                    entity=entity_id,
                    revision=revision,
                    after_node=after_node,
                )
            )
    return findings


def ds_011(ctx: DatasetContext) -> list[RuleError]:
    """Every revision's ``after_node`` references an existing blueprint node."""
    findings: list[RuleError] = []
    for entity_id in sorted(ctx.ds.entities):
        revisions = ctx.ds.revisions_of(entity_id)
        for revision_id in sorted(revisions):
            declared = as_mapping(revisions[revision_id])
            after_node = as_text(declared.get("after_node"))
            if after_node is not None and after_node in ctx.blueprint.node_by_id:
                continue
            findings.append(
                ctx.error(
                    "DS-011",
                    pointer("entities", entity_id, "revisions", revision_id, "after_node"),
                    f"revision {revision_id!r} of {entity_id!r} names after_node "
                    f"{declared.get('after_node')!r}, which is not a blueprint node.",
                    entity=entity_id,
                    revision=revision_id,
                    after_node=declared.get("after_node"),
                )
            )
    return findings
