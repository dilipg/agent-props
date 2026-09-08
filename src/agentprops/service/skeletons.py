"""The skeleton pipeline: hand out a partial dataset, take it back a section at a time.

`docs/contracts.md` section 4's three skeleton tools, and the authoring half of
PRD 6 flow B. This is the only module in the codebase that **writes a dataset**
- ground rule 1's "nothing writes to a dataset except the authoring flow" is
this file.

    dataset_skeleton(agent_id, version, labels, seed)
        -> {skeleton_id, manifest, skeleton, instructions}
    dataset_fill_part(skeleton_id, section, content)
        -> {filled, remaining}, or section-scoped errors
    dataset_submit(skeleton_id)
        -> the stored dataset, or full cross-cutting validation errors

The section partition, and why it reproduces the golden fixture
---------------------------------------------------------------

Ruling R-06 fixes five sections in order - ``provenance``, ``entities``,
``nodes.core``, ``nodes.branches``, ``expected`` - and assigns ``narrative`` to
``provenance`` and ``pools`` to ``nodes.branches``. :data:`SECTION_FIELDS` is
that ruling as data: each section owns whole **top-level fields of the dataset
document**, and the five sections' fields partition every authored field.

``nodes.core`` owns ``/nodes`` and ``nodes.branches`` owns ``/pools``. That is
not a new decision: `validation/pointers.py::section_for_pointer` already maps
pointers that way, so a ``DS-*`` finding and the manifest agree by construction
rather than by coincidence, and
:func:`test_every_manifest_pointer_maps_back_to_its_section` asserts it.
"Alongside the loop node's own fixture" in R-06 is exactly this: a ``pool:
true`` node's fixtures live in ``pools`` (ruling R-01), so the loop node's
fixture *is* the ``nodes.branches`` content.

Two consequences worth naming:

**A section's content is a fragment of the dataset document, keyed at the top
level.** ``dataset_fill_part("nodes.core", {"nodes": {...}})``, not
``dataset_fill_part("nodes.core", {...})``. So an RFC 6901 pointer into the
content is already a pointer into the assembled dataset, and SK-003's findings
need no translation - which is why the keyed form is the contract.

**Filling the five sections reassembles `priya-missing-docs.json` exactly**,
modulo the two fields the service owns rather than the author: ``id``, which
ruling R-10 mints from ``(seed, salt)`` where the fixture's is hand-built, and
``validated_at``, which the ``Clock`` port stamps. That is M5's acceptance
criterion 6 and `test_worked_example_fill.py` is where it is checked.

Rule precedence, in one place
-----------------------------

Ruling R-45: **SK-004 owns an unfilled section; the ``DS-*`` provenance rules
own a filled-but-bad one.** SK-004's precedence is total over section contents,
so :func:`submit` runs the ``SK-*`` submit rules first and returns their
findings *without* running the dataset catalogue. Otherwise submitting an empty
skeleton would report most of the catalogue, and the one finding that says what
to do ("you never filled provenance") would be buried in it.

The order inside :func:`submit` is therefore: SK-005, SK-004, then the whole
``DS-*`` catalogue against the assembled document (ruling R-23's validate, then
parse, then store).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final
from uuid import UUID

from agentprops.expansion.seeded import Seeded
from agentprops.models import Dataset, RuleError, Section, Skeleton
from agentprops.service.context import ServiceContext
from agentprops.service.documents import parse
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_STORE_REFUSED,
    Reply,
    blocking,
    boundary,
    failure,
    field_pointer,
    not_found,
    success,
    warnings_from,
)
from agentprops.storage import RecordNotFoundError, StoreError
from agentprops.validation import SkeletonContext, validate_dataset, validate_fill, validate_submit
from agentprops.validation.context import BlueprintView
from agentprops.validation.pointers import (
    SECTION_ENTITIES,
    SECTION_EXPECTED,
    SECTION_NODES_BRANCHES,
    SECTION_NODES_CORE,
    SECTION_PROVENANCE,
    SECTIONS,
    pointer,
)

__all__ = [
    "DATASET_FIELD_ORDER",
    "FIELD_TYPES",
    "SECTION_FIELDS",
    "SKELETON_GENERATIONS",
    "assemble",
    "fill_part",
    "instructions_for",
    "manifest_for",
    "scaffold_for",
    "skeleton",
    "submit",
]

#: Which top-level dataset fields each section owns (ruling R-06). The union is
#: every authored field of a dataset; the three the service owns instead -
#: ``blueprint``, ``seed`` and ``labels`` - appear in no section, because R-06
#: settles that ``labels`` and ``seed`` are ``dataset_skeleton`` *inputs*.
SECTION_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    SECTION_PROVENANCE: ("provenance", "narrative"),
    SECTION_ENTITIES: ("entities",),
    SECTION_NODES_CORE: ("nodes",),
    SECTION_NODES_BRANCHES: ("pools",),
    SECTION_EXPECTED: ("expected",),
}

#: The JSON type each owned field takes, for the local schema-level check
#: :func:`fill_part` runs. Deliberately **one level deep**: a required leaf
#: (``provenance.title``) is a ``DS-*`` rule's business, not a fill-time
#: rejection - ruling R-45 requires DS-025 to be reachable for a filled
#: provenance whose title is absent, which a model-level parse at fill time
#: would swallow as an ``AP-003``.
FIELD_TYPES: Final[dict[str, type | tuple[type, ...]]] = {
    "provenance": Mapping,
    "narrative": str,
    "entities": Mapping,
    "nodes": Mapping,
    "pools": Mapping,
    "expected": Mapping,
}

#: The key order of an assembled dataset document, which is the golden
#: fixture's own order. Dict equality ignores order, so this is for the reader
#: and for M7: ``dataset_export``'s byte stability is easier to reason about
#: when the write path emits one canonical order rather than whichever order
#: five LLM calls happened to arrive in.
DATASET_FIELD_ORDER: Final[tuple[str, ...]] = (
    "id",
    "version",
    "archived",
    "blueprint",
    "seed",
    "provenance",
    "narrative",
    "labels",
    "entities",
    "nodes",
    "pools",
    "expected",
    "validated_at",
)

#: What the ``nodes.branches`` description says when a blueprint has no
#: ``pool: true`` node. The section is still required - see :func:`manifest_for`.
NO_POOLS: Final = 'this blueprint declares none, so fill {"pools": {}}'

#: How many skeleton ids one ``(agent_id, version, labels, seed)`` can mint.
#: See :func:`_claim` for what the walk is for and why it is bounded.
SKELETON_GENERATIONS: Final = 64

#: The version an assembled dataset carries. ``put_dataset`` allocates the real
#: one (``max(version) + 1``), so this is the value a first write gets and the
#: model needs a value for.
FIRST_VERSION: Final = 1


def manifest_for(view: BlueprintView) -> list[Section]:
    """The five-section manifest for one blueprint, in ruling R-06's order.

    ``pointers`` come from :data:`SECTION_FIELDS`, so they cannot drift from
    what :func:`assemble` reads or from what ``section_for_pointer`` maps.

    ``required`` is ``True`` for all five. Every one of their fields is required
    by the ``Dataset`` model, including ``pools`` - a blueprint with no ``pool:
    true`` node still needs ``{"pools": {}}``, and one explicit call is better
    than a service-invented default, which would be the service authoring part
    of a dataset. The field is not decoration: SK-004 filters on it, and
    `test_validation_rules.py` exercises a manifest with an optional section.
    """
    return [
        Section(
            id=section_id,
            required=True,
            pointers=[pointer(field) for field in SECTION_FIELDS[section_id]],
            description=_description(section_id, view),
        )
        for section_id in SECTIONS
    ]


def _description(section_id: str, view: BlueprintView) -> str:
    """What the filling LLM is being asked for, in one paragraph per section.

    Interpolated from the blueprint, not a constant: the node ids, the entity
    ids and the pool cap are the facts an author needs and the only place they
    are otherwise visible is ``blueprint_get``. Every value here is derived, so
    two calls for one blueprint version produce byte-identical text.
    """
    caps = ", ".join(
        f"{node_id} (max_iterations {view.max_iterations_of(node_id)})"
        for node_id in _pool_nodes(view)
    )
    texts = {
        SECTION_PROVENANCE: (
            "Why this dataset exists in the suite, who wrote it, and the in-world story. "
            "provenance takes title (non-whitespace, at most 120 characters), intent (at least "
            "30 characters, naming the regression this dataset guards), author {name, handle, "
            "agent: claude-code|codex|human|generator}, created_at (ISO-8601) and supersedes "
            "(null, or the id of a dataset this replaces). narrative is at least 30 characters "
            "of in-world prose and must not repeat intent: intent says why the dataset exists, "
            "narrative says what happens in the world."
        ),
        SECTION_ENTITIES: (
            "The cast, keyed by entity id. The blueprint declares "
            f"{', '.join(view.entity_ids) or 'no entities'}. Each entity takes base (its state "
            "at run start) and, when its state changes during the run, revisions: "
            "{revision_id: {after_node, state}}. An entity with no revisions block must have a "
            "byte-identical embedded state everywhere it appears."
        ),
        SECTION_NODES_CORE: (
            f"One fixture per non-pool blueprint node, keyed by node id: "
            f"{', '.join(_core_nodes(view))}. "
            "Each fixture takes output (validated against that node's output_schema) and "
            "entity_refs (entity_id, or entity_id@revision_id for a revised state); input, "
            "latency_hint_ms and fault are optional. A faulted fixture may omit output."
        ),
        SECTION_NODES_BRANCHES: (
            f"Ordered fixtures for every pool: true node, keyed by node id: {caps or NO_POOLS}. "
            "Iteration N draws entry N; past the end the last entry repeats with a "
            "pool_exhausted warning. Each entry has the same shape as a node fixture."
        ),
        SECTION_EXPECTED: (
            "What the agent should produce, and why that is right in this world. final "
            "(validated against the blueprint's outcome_schema), comparison "
            "(exact|schema|subset), rationale (at least 30 characters, and not the same text as "
            "provenance.intent), and optionally expected_path (a real traversal from the entry "
            "node to a terminal node) and node_expectations (keyed by node id)."
        ),
    }
    return texts[section_id]


def instructions_for(manifest: Sequence[Section]) -> str:
    """The prose an LLM caller reads before filling. Derived from ``manifest``.

    Composed from the manifest and :data:`SECTION_FIELDS` rather than written
    out, so it cannot drift from the sections that actually exist: rename a
    section and this text renames with it.

    What it has to say is fixed by what a caller gets wrong otherwise, and each
    line is here because a rule exists for it: the fill **order** (SK-002), that
    a re-fill is allowed and is how a rejection is repaired (ruling R-06, PRD 6
    flow B), the **keyed** content form (so a pointer in an error addresses the
    caller's own content), that a re-fill **replaces** a section, that
    ``labels`` and ``seed`` are not fillable (R-06) and where to check them
    before starting, and that submit is the cross-cutting pass.

    No timestamp, no counter and no clock reading, so two calls against one
    blueprint version return byte-identical text - which is what makes the
    whole response comparable across calls.
    """
    order = ", ".join(section.id for section in manifest)
    shapes = "; ".join(f"{section.id} takes {_shape(section.id)}" for section in manifest)
    return (
        f"Fill this skeleton one section at a time with dataset_fill_part(skeleton_id, section, "
        f"content), then call dataset_submit(skeleton_id).\n\n"
        f"Sections are filled in manifest order: {order}. A section may not be filled before "
        f"every earlier one is filled (SK-002). Re-filling an already-filled section is allowed "
        f"and is how you repair a rejection: an error carries the section to re-fill, so fix that "
        f"part rather than regenerating everything. A re-fill replaces the whole section.\n\n"
        f"content is a fragment of the dataset document - an object whose keys are the section's "
        f"own top-level fields. {shapes}. Each section's description says what its fields must "
        f"carry, and pointers in an error address your content directly.\n\n"
        f"blueprint, seed and labels are fixed by this skeleton and are not fillable sections. "
        f"Call label_vocabulary(agent_id, version) before dataset_skeleton if you are unsure "
        f"which label values a blueprint declares; a label outside its vocabulary is not "
        f"repairable by re-filling and needs a new skeleton.\n\n"
        f"dataset_submit runs the full DS-* catalogue over the assembled dataset and stores it. "
        f"Every error carries a rule id, an RFC 6901 pointer into the document, and the section "
        f"to re-fill. A skeleton becomes exactly one dataset (SK-005)."
    )


def _shape(section_id: str) -> str:
    """One section's ``content`` shape, as an object literal: ``{"nodes": ...}``."""
    fields = ", ".join(f"{json.dumps(field)}: ..." for field in SECTION_FIELDS[section_id])
    return "{" + fields + "}"


def scaffold_for(
    view: BlueprintView,
    labels: Mapping[str, str],
    seed: int,
    parts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """The partially-filled dataset document: what the caller completes.

    Two rules decide how much is pre-filled, and between them they are the whole
    of the "how much scaffolding" decision:

    **Keys are blueprint facts; leaf values are the author's job.** The node
    ids, the entity ids and the pool node ids are all knowable here and are
    exactly what an author needs to enumerate - DS-002 requires a fixture per
    non-pool node and DS-018 an entry per pool node - so they are emitted as
    keys. Nothing under them is invented.

    **No placeholder may validate if it is left unedited.** An empty fixture
    ``{}`` reports DS-004, an empty pool reports DS-019, and an empty entity
    fails to construct - each of them loudly. A more "helpful" placeholder such
    as ``{"base": {}}`` or ``{"entity_refs": []}`` would pass every rule and
    store a nonsense dataset, which is strictly worse than an error.

    A section that has been filled shows its stored content instead of its
    placeholder, so a resumed skeleton is a live view of the work so far. The
    authoritative fill state is ``dataset_fill_part``'s ``{filled, remaining}``
    - a section deliberately filled with an empty object is indistinguishable
    here from an unfilled one, and that response is where the difference is
    visible.
    """
    document: dict[str, Any] = {
        "blueprint": {"agent_id": view.agent_id, "version": view.version},
        "seed": seed,
        "labels": dict(labels),
    }
    for section_id in SECTIONS:
        filled = parts.get(section_id)
        if filled is not None:
            document.update(filled)
            continue
        document.update(_placeholder(section_id, view))
    return _ordered(document)


def _placeholder(section_id: str, view: BlueprintView) -> dict[str, Any]:
    """The empty shape of one unfilled section. See :func:`scaffold_for`."""
    if section_id == SECTION_PROVENANCE:
        return {"provenance": {}, "narrative": ""}
    if section_id == SECTION_ENTITIES:
        return {"entities": {entity_id: {} for entity_id in view.entity_ids}}
    if section_id == SECTION_NODES_CORE:
        return {"nodes": {node_id: {} for node_id in _core_nodes(view)}}
    if section_id == SECTION_NODES_BRANCHES:
        return {"pools": {node_id: [] for node_id in _pool_nodes(view)}}
    return {"expected": {}}


def _core_nodes(view: BlueprintView) -> list[str]:
    """The non-pool node ids, in blueprint declaration order.

    Declaration order rather than sorted: it is the order the graph reads in,
    and nothing here re-sorts what a blueprint declared (the same rule M4's
    `admin.py` follows for label dimensions).
    """
    return [node_id for node_id in view.node_ids if node_id not in view.pool_node_ids]


def _pool_nodes(view: BlueprintView) -> list[str]:
    """The ``pool: true`` node ids, in blueprint declaration order."""
    return [node_id for node_id in view.node_ids if node_id in view.pool_node_ids]


def _ordered(document: Mapping[str, Any]) -> dict[str, Any]:
    """``document`` in :data:`DATASET_FIELD_ORDER`, then anything unexpected.

    The tail is not dead code: :func:`assemble` runs over caller-supplied parts,
    and a part carrying an unowned field is an ``AP-001`` at fill time - but
    ordering must not be the thing that silently drops a value, because a value
    dropped here would be a value no rule could report.
    """
    known = {field: document[field] for field in DATASET_FIELD_ORDER if field in document}
    known.update({field: value for field, value in document.items() if field not in known})
    return known


def skeleton(
    context: ServiceContext, agent_id: str, version: str, labels: Mapping[str, str], seed: int
) -> Reply:
    """Start - or resume - authoring one dataset against a published blueprint.

    An unknown ``{agent_id, version}`` is ``AP-004``, not DS-001: DS-001 is a
    rule about a *dataset document*, and there is no document yet. Ruling R-42(a)
    settles that ``ok: false`` for an unresolvable id is inside the envelope
    contract.

    Nothing about ``labels`` is checked here, and that is deliberate. DS-012 and
    DS-024 own them, at submit, against the assembled document - which is what
    M5's acceptance criterion asks for ("a submit carrying partial labels is
    rejected with DS-024"), and pre-empting them here would make that criterion
    unreachable through the tool surface. ``label_vocabulary`` is the pre-flight
    surface, and :func:`instructions_for` points a caller at it.
    """
    blueprint = context.resolver.get_published_blueprint(agent_id, version)
    if blueprint is None:
        return not_found("published blueprint", field="version", agent_id=agent_id, version=version)
    view = BlueprintView(blueprint)
    resumed, skeleton_id = _claim(context, agent_id, version, labels, seed)
    if skeleton_id is None:
        return failure([_generations_exhausted(agent_id, version, seed)])
    stored = resumed if resumed is not None else _write(context, skeleton_id, view, labels, seed)
    return success(
        "skeleton",
        {
            "skeleton_id": str(stored.id),
            "manifest": [section.model_dump(mode="json") for section in stored.manifest],
            "skeleton": scaffold_for(view, stored.labels, stored.seed, stored.parts),
            "instructions": instructions_for(stored.manifest),
        },
    )


def _write(
    context: ServiceContext,
    skeleton_id: UUID,
    view: BlueprintView,
    labels: Mapping[str, str],
    seed: int,
) -> Skeleton:
    """Persist a fresh skeleton. ``created_at`` comes from the ``Clock`` port."""
    return context.store.put_skeleton(
        Skeleton(
            id=skeleton_id,
            agent_id=str(view.agent_id),
            bp_version=str(view.version),
            labels=dict(labels),
            seed=seed,
            manifest=manifest_for(view),
            created_at=context.clock.now(),
        )
    )


def _claim(
    context: ServiceContext,
    agent_id: str,
    version: str,
    labels: Mapping[str, str],
    seed: int,
) -> tuple[Skeleton | None, UUID | None]:
    """The skeleton id for this request: ``(resumed skeleton or None, id or None)``.

    Ruling R-10 mints skeleton ids from ``Seeded.uuid()``, so an id is a pure
    function of the request - which means two ``dataset_skeleton`` calls with
    identical arguments derive the **same** id. That is a hazard and a feature,
    and this function is where the difference is decided:

    - the id names **no** skeleton: it is free, use it;
    - the id names an **unsubmitted** skeleton: the caller is asking for the same
      skeleton again, so return it *with its parts intact* rather than
      overwriting them. An LLM that lost its ``skeleton_id`` mid-fill resumes
      instead of starting over;
    - the id names a **submitted** skeleton: that dataset is authored and SK-005
      has closed it, so move to the next generation, which is a different salt
      and therefore a different id.

    One loop with one exit predicate - *free or unsubmitted* - because two
    conditions that must agree are how the ``seq`` allocation bug at M3 happened.

    The walk is bounded by :data:`SKELETON_GENERATIONS`, so the tuple
    ``(agent_id, version, labels, seed)`` can author that many datasets before
    the caller has to vary an argument. The store offers no way to enumerate
    skeletons - the ``Store`` Protocol has ``get_skeleton`` and nothing else -
    so probing by derived id is the only mechanism available, and an unbounded
    probe would be a worse answer than a stated limit.
    """
    generator = Seeded(seed)
    for generation in range(SKELETON_GENERATIONS):
        candidate = generator.uuid(_skeleton_salt(agent_id, version, labels, generation))
        existing = context.store.get_skeleton(str(candidate))
        if existing is None:
            return None, candidate
        if existing.submitted_as is None:
            return existing, candidate
    return None, None


def _skeleton_salt(agent_id: str, version: str, labels: Mapping[str, str], generation: int) -> str:
    """The salt a skeleton id is derived from. Canonical, so it is reproducible.

    ``labels`` is serialised with ``sort_keys`` so that two callers passing the
    same labels in a different key order derive the same id, matching the fact
    that they are asking for the same thing. ``Seeded`` length-prefixes its
    components, so nothing inside this string can forge a different salt.
    """
    canonical = json.dumps(dict(labels), sort_keys=True, separators=(",", ":"))
    return f"skeleton:{agent_id}:{version}:{canonical}:{generation}"


def _dataset_salt(skeleton_id: UUID | str) -> str:
    """The salt a dataset id is derived from: the skeleton it came from.

    Not the request arguments. Two skeletons authored from identical
    ``(agent_id, version, labels, seed)`` are different skeletons and must
    become different datasets - deriving from the arguments would give them one
    id, and ``put_dataset`` would then file the second as **version 2 of the
    first**, silently turning two datasets into one lineage.
    """
    return f"dataset:{skeleton_id}"


def _generations_exhausted(agent_id: str, version: str, seed: int) -> RuleError:
    """No free skeleton id left for this request. ``AP-001``, pointed at ``seed``.

    The honest code for "your arguments cannot be served": every id this tuple
    can derive names a skeleton that has already become a dataset, and the fix
    is to vary an argument - the ``seed`` being the one that exists for exactly
    this purpose. A sixth ``AP-*`` code would be a better fit and would mean
    amending contracts section 3.5, which M5 was not asked to do; recorded in
    `DECISIONS.md` rather than done quietly.
    """
    return boundary(
        AP_ARGUMENT,
        field_pointer("seed"),
        f"all {SKELETON_GENERATIONS} skeleton ids derivable from this "
        f"(agent_id, version, labels, seed) name skeletons that were already submitted; "
        f"vary the seed to author another dataset from these labels.",
        argument="seed",
        agent_id=agent_id,
        version=version,
        seed=seed,
        generations=SKELETON_GENERATIONS,
    )


def fill_part(
    context: ServiceContext, skeleton_id: str, section: str, content: Mapping[str, Any]
) -> Reply:
    """Fill one section. Returns ``{filled, remaining}`` in manifest order.

    Three checks, in this order, and each one removes the ground the next stands
    on: the ``SK-*`` fill rules (SK-005, SK-001, SK-002, SK-003, stopping at the
    first that reports), then the local schema-level check on the content, then
    the write.

    **A re-fill replaces the section.** PRD 6 flow B repairs "one part", and the
    part is the section; merging a partial re-fill into the stored content would
    mean a caller could not remove a field it should not have written, and would
    make the stored state depend on the order of two calls rather than on the
    last one.

    **The write is a read-modify-write of the whole ``parts`` object**, because
    ``put_skeleton`` is a whole-aggregate upsert - the ``Store`` Protocol has no
    compare-and-set. Two *concurrent* fills of different sections can therefore
    lose one: last write wins. That is tested rather than claimed
    (``test_a_concurrent_fill_of_another_section_is_lost``) and recorded in
    `DECISIONS.md` with the remedy, because an authoring session is one caller
    filling one skeleton in sequence and a CAS on the Protocol is a change three
    adapters pay for.
    """
    stored, findings = _load(context, skeleton_id, section=section, content=content)
    if stored is None:
        return failure(findings)
    shape = _content_findings(section, content)
    if shape:
        return failure(shape)
    parts = {**stored.parts, section: dict(content)}
    written, refused = _put(context, stored.model_copy(update={"parts": parts}))
    if written is None:
        return failure(refused)
    return success("fill", _progress(written))


def _progress(stored: Skeleton) -> dict[str, Any]:
    """``{filled, remaining}``, both in manifest order.

    Manifest order rather than call order or sorted order: the caller's next
    action is ``remaining[0]``, and SK-002 is what makes that true.
    """
    ids = [section.id for section in stored.manifest]
    return {
        "filled": [section_id for section_id in ids if section_id in stored.parts],
        "remaining": [section_id for section_id in ids if section_id not in stored.parts],
    }


def _content_findings(section: str, content: Mapping[str, Any]) -> list[RuleError]:
    """The local schema-level check: which fields, and of what JSON type.

    "Validating the section locally at schema level" is deliberately shallow -
    the owned fields and their JSON types, one level deep. It stops exactly
    where the catalogue begins: a *missing* ``provenance.title`` must reach
    submit and be reported as DS-025 (ruling R-45), so a model-level parse here
    would swallow a rule id that a ruling explicitly assigns to a rule.

    An unowned key is ``AP-001`` rather than a rule: no ``SK-*`` rule covers
    the shape of ``content``, and section 3.5's family is for exactly the
    failures a user can cause that no catalogue rule describes.
    """
    owned = SECTION_FIELDS[section]
    findings = [
        boundary(
            AP_ARGUMENT,
            field_pointer("content", key),
            f"the {section!r} section does not own {key!r}; it owns {list(owned)}.",
            argument="content",
            section=section,
            unknown_key=key,
        )
        for key in content
        if key not in owned
    ]
    findings.extend(
        boundary(
            AP_ARGUMENT,
            field_pointer("content", field),
            f"content.{field} must be {_type_name(field)}.",
            argument="content",
            section=section,
            field=field,
            given_type=type(content[field]).__name__,
        )
        for field in owned
        if field in content and not isinstance(content[field], FIELD_TYPES[field])
    )
    return findings


def _type_name(field: str) -> str:
    """The JSON type name :data:`FIELD_TYPES` declares for ``field``."""
    return "an object" if FIELD_TYPES[field] is Mapping else "a string"


def submit(context: ServiceContext, skeleton_id: str) -> Reply:
    """Assemble the filled sections into a dataset, validate it all, and store it.

    The one dataset write path in the service. Six steps, and the order is
    ruling R-23's with ruling R-45's precedence in front of it:

    1. **SK-005 and SK-004** (:func:`validate_submit`). An unknown or
       already-submitted skeleton, or any unfilled required section, is reported
       here and the catalogue never runs - R-45's "SK-004's precedence is total
       over section contents".
    2. **Assemble** the raw document from the parts plus the three fields the
       service owns.
    3. **Validate** the raw document against every ``DS-*`` rule (R-23: the raw
       document, not a model). Findings already carry their ``section``, stamped
       by `validation/context.py` from the pointer, so a rejection is repairable
       one section at a time without this module deciding anything.
    4. **Parse** it into a ``Dataset`` - the R-04/R-23 residue becomes
       ``AP-003`` findings rather than an exception.
    5. **Stamp** ``validated_at`` from the ``Clock`` port (ruling R-09). After
       parsing, so the raw document stays raw JSON and the timestamp is visibly
       the service's rather than authored content.
    6. **Store**, then mark the skeleton submitted.

    Step 6 is in that order on purpose. A crash between the two leaves a stored
    dataset and an unmarked skeleton, which a caller can see and recover from;
    the reverse leaves a skeleton SK-005 has closed and no dataset, and the
    filled work is unreachable.

    Warning-severity findings (DS-007, DS-027, DS-032) do not block: they ride
    back as ``warnings`` on the success envelope (ruling R-13).
    """
    stored, gate = _load(context, skeleton_id, submitting=True)
    if stored is None:
        return failure(gate)

    dataset_id = Seeded(stored.seed).uuid(_dataset_salt(stored.id))
    document = assemble(stored, dataset_id)

    findings = validate_dataset(document, context.resolver).errors
    if blocking(findings):
        return failure(findings)
    model, shape_findings = parse(Dataset, document, pool_nodes=_pool_node_ids(context, stored))
    if model is None:
        return failure(findings + shape_findings)

    stamped = model.model_copy(update={"validated_at": context.clock.now()})
    return _store(context, stored, stamped, findings)


def _pool_node_ids(context: ServiceContext, stored: Skeleton) -> frozenset[str]:
    """The blueprint's ``pool: true`` node ids, for an ``AP-003`` finding's ``section``.

    Only reached once DS-001 has passed, so the blueprint resolves; the empty
    fallback is defence in depth rather than a case, because a document whose
    blueprint does not resolve never gets as far as being parsed.
    """
    blueprint = context.resolver.get_published_blueprint(stored.agent_id, stored.bp_version)
    return frozenset() if blueprint is None else BlueprintView(blueprint).pool_node_ids


def assemble(stored: Skeleton, dataset_id: UUID) -> dict[str, Any]:
    """The raw dataset document a skeleton's filled parts describe.

    The three fields no section owns are supplied here - ``blueprint`` from the
    skeleton's own ``{agent_id, bp_version}``, and ``seed`` and ``labels`` from
    the inputs the skeleton row carries - plus ``id``, ``version`` and
    ``archived``, which are the store's and the service's.

    ``validated_at`` is **not** here: it is stamped after the document has been
    through the validator, which is the only order in which the name is true.
    """
    document: dict[str, Any] = {
        "id": str(dataset_id),
        "version": FIRST_VERSION,
        "archived": False,
        "blueprint": {"agent_id": stored.agent_id, "version": stored.bp_version},
        "seed": stored.seed,
        "labels": dict(stored.labels),
    }
    for section in stored.manifest:
        document.update(stored.parts.get(section.id, {}))
    return _ordered(document)


def _store(
    context: ServiceContext, stored: Skeleton, model: Dataset, findings: list[RuleError]
) -> Reply:
    """Steps 6 and 7: write the dataset, then record what the skeleton became.

    Both store guards become findings. ``mark_skeleton_submitted`` refuses a
    *different* dataset id over an existing one, which cannot happen through
    this path - the id is derived from the skeleton, so a replay derives the
    same one - so reaching that branch means a defect, which is what
    ``AP-005`` says.
    """
    try:
        written = context.store.put_dataset(model)
        context.store.mark_skeleton_submitted(str(stored.id), str(written.id))
    except (RecordNotFoundError, StoreError) as exc:
        return failure([boundary(AP_STORE_REFUSED, field_pointer("skeleton_id"), str(exc))])
    document: dict[str, Any] = written.model_dump(mode="json", exclude_unset=True)
    return success("dataset", document, warnings_from(findings))


def _put(context: ServiceContext, model: Skeleton) -> tuple[Skeleton | None, list[RuleError]]:
    """``put_skeleton``, with its one guard translated into findings.

    The same shape as :func:`~agentprops.service.documents.parse`: there is no
    skeleton to use until the findings are known to be empty, so a caller cannot
    forget to check.
    """
    try:
        return context.store.put_skeleton(model), []
    except StoreError as exc:  # pragma: no cover - defence in depth, no known trigger
        return None, [boundary(AP_STORE_REFUSED, field_pointer("skeleton_id"), str(exc))]


def _load(
    context: ServiceContext,
    skeleton_id: str,
    *,
    section: str = "",
    content: Mapping[str, Any] | None = None,
    submitting: bool = False,
) -> tuple[Skeleton | None, list[RuleError]]:
    """The skeleton this request may act on, or the ``SK-*`` findings that say why not.

    One decision site for "does this skeleton exist, and may it be written to".
    :data:`~agentprops.validation.skeleton.FILL_RULES` and
    :data:`~agentprops.validation.skeleton.SUBMIT_RULES` both begin with SK-005,
    so a ``None`` skeleton always comes back with a finding and the caller's
    ``if stored is None`` needs no second check for an empty-findings case that
    cannot happen.

    This is the shape :func:`~agentprops.service.documents.parse` established at
    M4, and the reason there is no ``assert`` here: an ``assert`` narrowing the
    type would be stripped under ``-O`` and become an ``AttributeError`` in the
    one situation it claimed to rule out.

    ``submitting`` selects the phase. A flag rather than two functions because
    everything either phase does with the answer is identical, and the two rule
    lists are already named in one place.
    """
    stored = context.store.get_skeleton(skeleton_id)
    ctx = _context_for(skeleton_id, stored, section=section, content=content)
    findings = (validate_submit(ctx) if submitting else validate_fill(ctx)).errors
    if stored is None or findings:
        return None, findings
    return stored, []


def _context_for(
    skeleton_id: str,
    stored: Skeleton | None,
    *,
    section: str = "",
    content: Mapping[str, Any] | None = None,
) -> SkeletonContext:
    """The pure context the ``SK-*`` rules read. The only place the store is read for them.

    A missing skeleton becomes ``exists=False`` rather than ``None``, so every
    rule runs against a total object and SK-005 is the only one that has to know
    about absence.
    """
    if stored is None:
        return SkeletonContext(skeleton_id, exists=False, section=section, content=content)
    return SkeletonContext(
        skeleton_id,
        submitted_as=None if stored.submitted_as is None else str(stored.submitted_as),
        manifest=[item.model_dump(mode="json") for item in stored.manifest],
        parts=stored.parts,
        section=section,
        content=content,
    )
