"""The blueprint aggregate: the graph of an agent's steps.

`docs/contracts.md` section 2.1 is the authority for these shapes, per ruling
R-08: it carries ``status`` and ``entry_node``, which `docs/prd.md` section 5.1
omits, and the PRD's own prose (section 6, flow A) demands both.

Every constraint the ``BP-*`` catalogue owns is absent here by design. There is
no ``pattern`` on :attr:`Blueprint.agent_id` (BP-001), no semver validation on
:attr:`Blueprint.version` (BP-002), no ``Literal`` on :attr:`Node.kind` or
:attr:`Blueprint.status`, no ``gt=0`` on :attr:`Node.max_iterations` (BP-008),
and no validator tying ``kind: "loop"`` to ``pool``/``max_iterations``
(BP-008, BP-017). Ruling R-04: encoding those here would make Pydantic raise
where the validator must return a rule id.
"""

from typing import Any

from pydantic import Field

from agentprops.models.base import StrictModel
from agentprops.models.labels import LabelSchema

__all__ = ["Blueprint", "BlueprintRef", "BlueprintSummary", "Edge", "EntitySchema", "Node"]


class EntitySchema(StrictModel):
    """A shared noun, declared once and referenced by node schemas.

    Entities are the coherence mechanism: node schemas reference an entity as
    ``{"$ref": "entity:store"}`` rather than re-describing the noun, which is
    what stops step 3 talking about a different store than step 1. BP-009
    resolves those refs, BP-015 checks id uniqueness.
    """

    id: str
    """``^[a-z][a-z0-9_]{0,62}$`` by convention; unenforced here."""

    json_schema: dict[str, Any] = Field(alias="schema")
    """Draft 2020-12 JSON Schema for the entity's state.

    Carried under the wire name ``schema``; the Python attribute is
    ``json_schema`` because ``schema`` is taken on ``pydantic.BaseModel`` (a
    field of that name fails ``mypy --strict`` and warns at import). The alias
    means both the JSON round trip and the emitted JSON Schema still say
    ``schema``.
    """


class Node(StrictModel):
    """One step in the graph.

    ``tool_name``, ``max_iterations`` and ``notes`` are optional. ``notes``
    being optional is load-bearing: BP-019 is a *warning* fired when a node is
    missing ``notes`` (ruling R-13 reads its catalogue wording as the satisfied
    condition), so the model must accept a node without them.
    """

    id: str
    tool_name: str | None = None
    """Enables tool-name addressing at ``fetch_step``. BP-014 rejects a
    ``tool_name`` shared by two nodes reachable in one step from the same
    node, because that resolution cannot be repaired at runtime."""

    kind: str
    """``tool_call``, ``llm``, ``decision``, ``loop`` or ``terminal``. A plain
    ``str``: the vocabulary is the catalogue's business, not the model's."""

    input_schema: dict[str, Any]
    """Draft 2020-12 JSON Schema; may ``$ref`` an entity. BP-011."""

    output_schema: dict[str, Any]
    """Draft 2020-12 JSON Schema; may ``$ref`` an entity. BP-011. Edge
    conditions are checked against this schema by BP-010."""

    pool: bool
    """Opt in to an independent fixture pool instead of chain position.
    BP-017 requires ``True`` when ``kind == "loop"``."""

    max_iterations: int | None = None
    """Required by BP-008 when ``kind == "loop"``, and it caps pool length
    (DS-023). It is *not* a runtime gate: ruling R-03 deletes RT-E05, so an
    agent looping past the pool gets a ``pool_exhausted`` warning."""

    notes: str | None = None
    """Narrative hints for the generating LLM. BP-019 warns when absent."""


class Edge(StrictModel):
    """A directed transition, optionally conditional.

    The condition language is JSONLogic evaluated against the source node's
    ``output_schema``. The service never evaluates a condition - runtime path
    selection is observed from call order. BP-010 only checks that every
    ``var`` path exists as a property of that schema.
    """

    from_node: str = Field(alias="from")
    """Source node id. Carried under the wire name ``from``, which is a Python
    keyword and cannot be a field name."""

    to: str
    """Target node id."""

    condition: dict[str, Any] | None = None
    """JSONLogic, or ``None`` for an unconditional edge. The golden blueprint
    sets this key explicitly to ``null`` on six edges, which is why the round
    trip uses ``exclude_unset`` rather than ``exclude_none``."""


class Blueprint(StrictModel):
    """A versioned declaration of an agent's step graph.

    ``published`` versions are immutable (BP-016). There is no ``created_at``
    here: the ``blueprints`` table stamps one as a column default, and the
    document itself carries no clock reading (ruling R-09).
    """

    agent_id: str
    """``^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`` per BP-001; unenforced here."""

    version: str
    """Semver per BP-002; unenforced here."""

    description: str
    status: str
    """``draft`` or ``published``. ``published`` is immutable."""

    entry_node: str
    """BP-006: must exist and have no inbound edges. BP-005 measures
    reachability from it."""

    entities: list[EntitySchema]
    nodes: list[Node]
    edges: list[Edge]
    label_schema: LabelSchema
    outcome_schema: dict[str, Any]
    """Draft 2020-12 JSON Schema for ``Dataset.expected.final``. BP-012."""


class BlueprintRef(StrictModel):
    """A dataset's pointer at the blueprint version it was authored against.

    DS-001 requires it to name an existing *published* blueprint at that exact
    version.
    """

    agent_id: str
    version: str


class BlueprintSummary(StrictModel):
    """One row of ``blueprint_list``.

    Shape taken from that tool's documented return, per ruling R-05:
    ``{agent_id, version, status, description}``.
    """

    agent_id: str
    version: str
    status: str
    description: str
