"""The dataset aggregate: one hand-authored, immutable, versioned world.

`docs/contracts.md` section 2.2, 2.2.1 and the fault shape from ruling R-07.

A dataset is both a fixture set and a golden test case. ``nodes[].output`` is
what the world returns *to* the agent; ``expected.final`` is what the agent
should produce. Mixing those two is the easiest way to build a confusing
product, so they live in different models here.

Nothing in this module enforces a ``DS-*`` constraint (ruling R-04). In
particular the provenance table in contracts 2.2 - title length, intent
length, the ``author.handle`` pattern, the ``author.agent`` vocabulary - is
carried as plain ``str`` fields; DS-025, DS-026, DS-028, DS-029 and DS-030
enforce it. Read "enforced by the model and the validator" as "the model
carries the field, the validator enforces the constraint".

**What the model does still enforce is presence.** Ruling R-04 keeps
required-vs-optional structural, so the "is present" half of DS-021, DS-025,
DS-026 and DS-028 is a Pydantic requirement and only the content half is a
rule. A corpus case for one of those rules must therefore mutate the value
(the shipped cases use ``"   "``, ``""``, ``"testing"``) rather than remove
the key.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, StrictBool, StrictInt

from agentprops.models.base import StrictModel
from agentprops.models.blueprint import BlueprintRef
from agentprops.models.labels import Labels

__all__ = [
    "Author",
    "Dataset",
    "DatasetQuery",
    "DatasetSummary",
    "Entity",
    "EntityRevision",
    "ExpectedOutcome",
    "FaultSpec",
    "NodeExpectation",
    "NodeFixture",
    "Provenance",
]


class Author(StrictModel):
    """Who authored a dataset. Attribution, never authentication.

    Self-declared and recorded verbatim; the service has no auth to verify it
    against. Build no ownership, permissions or audit on this field.
    """

    name: str
    """A person. DS-028: present and non-whitespace."""

    handle: str
    """A stable identifier. DS-029 owns the pattern."""

    agent: str
    """What filled it: ``claude-code``, ``codex``, ``human`` or ``generator``.
    DS-030 owns that vocabulary, so this is a ``str``, not a ``Literal`` - the
    shipped corpus case sets it to ``"gpt"`` and expects DS-030, which a
    ``Literal`` would swallow."""


class Provenance(StrictModel):
    """Why a dataset exists in the suite, and who to ask about it.

    ``intent`` and :attr:`ExpectedOutcome.rationale` are different fields.
    ``intent`` says why the dataset exists in the suite; ``rationale`` says why
    that particular expected outcome is correct given this world. DS-032 warns
    when they are byte-identical, DS-027 when ``intent`` matches ``narrative``.
    """

    title: str
    """DS-025: present, non-whitespace, at most 120 characters."""

    intent: str
    """DS-026: present, at least 30 characters."""

    author: Author

    created_at: datetime
    """Authored content, carried and never stamped here (ruling R-09). This is
    also the value the ``datasets.created_at`` column is populated from on
    insert, so ``dataset_find``'s ordering by ``(created_at, id)`` survives an
    export/import round trip."""

    supersedes: str | None = None
    """Optional lineage: the id of the dataset this one replaces.

    A ``str``, not a ``UUID``, even though the column is ``UUID``: DS-031 owns
    this field and reports a value that names no existing dataset. Typing it as
    ``UUID`` would make a malformed id raise instead of firing DS-031. By the
    time a dataset stores, DS-031 has proved the value names a real dataset, so
    the storage layer can parse it safely.
    """


class EntityRevision(StrictModel):
    """One declared state an entity passes through, and what introduced it.

    The dataset encodes a timeline baked at authoring time: the runtime does
    not compute the post-transition state, it serves it. DS-011 checks that
    ``after_node`` exists; DS-010, as read by ruling R-02, checks that a node
    referencing this revision is genuinely downstream of ``after_node`` on at
    least one path.
    """

    after_node: str
    state: dict[str, Any]


class Entity(StrictModel):
    """A member of the dataset's cast.

    An entity with no ``revisions`` block is treated as constant for the whole
    run, and DS-008 then requires it to be byte-identical everywhere it
    appears. An author who needs mutation has to say so, once.
    """

    base: dict[str, Any]
    """State at run start."""

    revisions: dict[str, EntityRevision] | None = None
    """Revision id to revision. Absent on constant entities - the golden
    ``arun-escalated`` fixture omits the key entirely on ``store``."""


class FaultSpec(StrictModel):
    """A fixture that represents a failure rather than a success.

    Shape supplied by ruling R-07, since no document defined it and neither
    golden fixture sets ``fault``. When ``fault`` is set, DS-004 skips
    ``output_schema`` validation entirely and requires ``output`` to be absent
    or an object.

    ``extra="allow"`` overrides the package default: DS-022 owns conformance to
    this shape, so an unexpected key must reach the validator as a rule id
    rather than raise at parse time, and ``allow`` (not the Pydantic default
    ``ignore``) is what keeps such a key intact for DS-022 to find - and keeps
    the round trip lossless.
    """

    model_config = ConfigDict(extra="allow")

    kind: str
    """``error``, ``timeout`` or ``malformed``. DS-022 owns the vocabulary."""

    code: str | None = None
    after_ms: StrictInt | None = None


class NodeFixture(StrictModel):
    """What one node hands back when the agent reaches it.

    Every node in the graph is filled, including nodes on branches not taken -
    that is what makes a full-graph blueprint playable. ``pool: true`` nodes
    are the exception: their fixtures live in :attr:`Dataset.pools` instead
    (ruling R-01).
    """

    input: dict[str, Any] | None = None
    """What the step should receive. DS-005 validates it against the node's
    ``input_schema`` when present; the golden datasets omit it on the two
    terminal nodes and on every pool entry."""

    output: dict[str, Any]
    """What the mocked step returns. DS-004 validates it against the node's
    ``output_schema``, unless ``fault`` is set."""

    entity_refs: list[str]
    """``entity_id`` for the base state, ``entity_id@revision_id`` for a
    revised state. DS-006 resolves them, DS-009 the revision half."""

    latency_hint_ms: StrictInt | None = None
    fault: FaultSpec | None = None


class NodeExpectation(StrictModel):
    """A per-node assertion on agent behaviour, not on the world.

    Declared inline in contracts 2.2 and PRD 5.2 rather than named as a model;
    given a name here because :attr:`ExpectedOutcome.node_expectations` needs a
    value type. DS-016 checks that every key names an existing node.
    """

    called: StrictBool
    args_match: dict[str, Any] | None = None
    """JSONLogic against the arguments the agent passed. Declared intent - the
    service never evaluates it."""


class ExpectedOutcome(StrictModel):
    """What the agent should produce, and why that is the right answer.

    ``comparison`` declares intent; it does not trigger anything. The service
    never executes a comparison - it stores the expectation and, on request,
    emits an evidence bundle. The three phase-1 modes ship as pure functions in
    the Python client.
    """

    final: dict[str, Any]
    """DS-014: validates against the blueprint's ``outcome_schema``."""

    expected_path: list[str] | None = None
    """The traversal the agent should take. DS-015, when present: consecutive
    pairs connected by an edge, starting at ``entry_node``, ending at a
    terminal node."""

    node_expectations: dict[str, NodeExpectation] | None = None

    comparison: str
    """``exact``, ``schema`` or ``subset``. DS-017 owns the vocabulary, so this
    is a ``str`` - a ``Literal`` here would swallow that rule id."""

    rationale: str
    """Why this is the right answer given this world, for the human reader."""


class Dataset(StrictModel):
    """One coherent fill of one blueprint version.

    Immutable and versioned: edits are copy-on-write, bumping ``version``, so a
    run that pinned version 1 keeps reading version 1 for its whole life.
    Archive is soft - hidden from ``dataset_find``, still servable to a run
    holding a pin.
    """

    id: UUID
    """Derived deterministically from ``(seed, salt)`` by ``Seeded.uuid()``,
    never from ``uuid4()`` (ruling R-10)."""

    version: StrictInt
    """Monotonic, bumped on every edit. Copy-on-write."""

    archived: StrictBool
    blueprint: BlueprintRef

    seed: StrictInt
    """An integer, with no narrower constraint - ruling R-04 asks for the base
    JSON type and nothing more.

    ``StrictInt`` rather than ``int`` per ruling R-23: lax ``int`` rewrites
    ``"42"`` to ``42`` and ``true`` to ``1``, producing a document that parses
    and then fails the round-trip criterion. DS-020 still owns the check, and
    still fires, because R-23 runs the validator against the raw ``dict``
    before any model is constructed."""

    provenance: Provenance

    narrative: str
    """The story in prose, in-world. What the agent sees. DS-021: present and
    at least 30 characters. Must not carry the same text as
    ``provenance.intent``, which DS-027 warns about."""

    labels: Labels
    """One value per dimension the blueprint declares. DS-012 checks the
    vocabulary, DS-024 the completeness."""

    entities: dict[str, Entity]
    """The cast, keyed by entity id. DS-007 warns about an unreferenced one."""

    nodes: dict[str, NodeFixture]
    """One fixture per blueprint node, keyed by node id - except ``pool: true``
    nodes, whose fixtures live in :attr:`pools` (DS-002 as corrected by ruling
    R-01). DS-003 rejects a key that is not a blueprint node."""

    pools: dict[str, list[NodeFixture]]
    """Ordered fixtures for ``pool: true`` nodes. Iteration N draws
    ``pools[node_id][N]``; past the end the last entry repeats with a
    ``pool_exhausted`` warning (ruling R-03). DS-018 owns which nodes may
    appear, DS-019 that each pool is non-empty, DS-023 the length cap."""

    expected: ExpectedOutcome

    validated_at: datetime | None = None
    """Stamped by the service through its injected ``Clock`` port, never by a
    clock call inside this package (ruling R-09). Absent on a document that has
    not been through the validator yet."""


class DatasetSummary(StrictModel):
    """What ``dataset_find`` returns per row. Contracts section 2.2.1.

    It exists so a reviewer can judge a dataset without fetching it: provenance
    is carried in full, and fixtures are not carried at all.
    """

    id: UUID
    version: StrictInt
    title: str
    intent: str
    labels: Labels
    author: Author
    blueprint: BlueprintRef

    narrative_excerpt: str
    """The first 200 characters of ``narrative``. Never the whole narrative,
    and never any node fixture."""

    archived: StrictBool

    created_at: datetime
    """The stored ``datasets.created_at`` column, populated from
    ``provenance.created_at`` on insert - which is the value ``dataset_find``
    orders by, per ruling R-09."""


class DatasetQuery(StrictModel):
    """The filter ``Store.find_datasets`` takes. Excludes archived rows.

    Shape derived from ``dataset_find``'s parameters, per ruling R-05. Every
    field is optional and unconstrained: ``limit`` and ``offset`` carry no
    ``ge``/``le`` (ruling R-04), and the service decides the defaults so the
    three storage adapters do not each invent their own.
    """

    agent_id: str | None = None

    labels: Labels | None = None
    """Every dimension given must match exactly."""

    author: str | None = None
    """Matches ``provenance.author.handle``."""

    q: str | None = None
    """Substring match over ``title`` and ``intent``."""

    blueprint_version: str | None = None
    limit: StrictInt | None = None
    offset: StrictInt | None = None
