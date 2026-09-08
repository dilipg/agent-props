"""What a rule is handed: a read-only view of the raw document, plus a finding factory.

Ruling R-23 settles that validation runs against the **raw document** - the
``dict`` off ``json.loads`` - and that models are constructed afterwards, in
`service/`. Two consequences shape this module:

1. **Nothing here may raise on a malformed document.** A mutation can put a
   string where the catalogue expects a list, and the rule that owns that field
   has to report a rule id rather than blow up on the way to it. So every
   accessor is total: a wrongly-typed ``nodes`` yields an empty node list, and
   the rules report what they can. Shape errors proper are Pydantic's job, one
   layer later.
2. **Pointers are built from the document's own structure.** A blueprint's nodes
   are a JSON *array*, so a blueprint finding points at ``/nodes/3/pool``; a
   dataset's nodes are an *object*, so a dataset finding points at
   ``/nodes/request_docs/output``. The two views below own that difference so no
   rule has to remember it.

Severity comes from :data:`WARNING_RULES` rather than from the rule body, so the
four warning-severity rules are visible in one place and cannot drift from
`docs/contracts.md` section 3 - or from ruling R-13, which is what makes them
``ok: true`` findings instead of rejections.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from agentprops.models.errors import SEVERITY_ERROR, SEVERITY_WARNING, RuleError
from agentprops.validation.graph import Graph
from agentprops.validation.pointers import pointer, section_for_pointer
from agentprops.validation.resolver import Resolver

__all__ = [
    "WARNING_RULES",
    "BlueprintContext",
    "BlueprintView",
    "DatasetContext",
    "DatasetView",
    "as_int",
    "as_mapping",
    "as_sequence",
    "as_text",
]

#: The four warning-severity rules (`docs/contracts.md` section 3, ruling R-13).
#: A document whose only findings are these still stores: ``ok`` stays ``True``.
WARNING_RULES: Final[frozenset[str]] = frozenset({"BP-019", "DS-007", "DS-027", "DS-032"})

#: Node kinds the catalogue names. Only ``loop`` and ``terminal`` carry rules
#: (BP-007, BP-008, BP-017, BP-018, DS-015, DS-023); the rest are informational.
KIND_LOOP: Final = "loop"
KIND_TERMINAL: Final = "terminal"


def as_mapping(value: Any) -> Mapping[str, Any]:
    """``value`` if it is a JSON object, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def as_sequence(value: Any) -> Sequence[Any]:
    """``value`` if it is a JSON array, else an empty sequence."""
    return value if isinstance(value, list) else ()


def as_text(value: Any) -> str | None:
    """``value`` if it is a string, else ``None``."""
    return value if isinstance(value, str) else None


def as_int(value: Any) -> int | None:
    """``value`` if it is a JSON integer, else ``None``.

    ``bool`` is excluded on purpose. Python makes ``isinstance(True, int)``
    true, so without this DS-020 would accept ``"seed": true`` - which is
    precisely the silent-coercion failure ruling R-23 set out to close.
    """
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


class BlueprintView:
    """Read-only derived structure over a raw blueprint mapping.

    Built once per validation pass and shared by every rule that needs it,
    including the ``DS-*`` rules, which read the dataset's blueprint through the
    same view.
    """

    def __init__(self, doc: Mapping[str, Any]) -> None:
        self.doc = doc
        self.agent_id = as_text(doc.get("agent_id"))
        self.version = as_text(doc.get("version"))
        self.status = as_text(doc.get("status"))
        self.entry_node = as_text(doc.get("entry_node"))
        self.outcome_schema = doc.get("outcome_schema")

        self.nodes: list[Mapping[str, Any]] = []
        self.node_indexes: dict[str, int] = {}
        self.node_by_id: dict[str, Mapping[str, Any]] = {}
        self.node_ids: list[str] = []
        self.duplicate_node_ids: list[tuple[int, str]] = []
        for index, raw in enumerate(as_sequence(doc.get("nodes"))):
            node = as_mapping(raw)
            self.nodes.append(node)
            node_id = as_text(node.get("id"))
            if node_id is None:
                continue
            if node_id in self.node_indexes:
                self.duplicate_node_ids.append((index, node_id))
                continue
            self.node_indexes[node_id] = index
            self.node_by_id[node_id] = node
            self.node_ids.append(node_id)

        self.edges: list[Mapping[str, Any]] = [
            as_mapping(raw) for raw in as_sequence(doc.get("edges"))
        ]

        self.entities: list[Mapping[str, Any]] = [
            as_mapping(raw) for raw in as_sequence(doc.get("entities"))
        ]
        self.entity_schemas: dict[str, Any] = {}
        self.entity_ids: list[str] = []
        self.duplicate_entity_ids: list[tuple[int, str]] = []
        for index, entity in enumerate(self.entities):
            entity_id = as_text(entity.get("id"))
            if entity_id is None:
                continue
            if entity_id in self.entity_schemas:
                self.duplicate_entity_ids.append((index, entity_id))
                continue
            self.entity_ids.append(entity_id)
            self.entity_schemas[entity_id] = entity.get("schema")

        self.pool_node_ids = frozenset(
            node_id for node_id in self.node_ids if self.node_by_id[node_id].get("pool") is True
        )
        self.loop_node_ids = frozenset(
            node_id for node_id in self.node_ids if self.kind_of(node_id) == KIND_LOOP
        )
        self.terminal_node_ids = frozenset(
            node_id for node_id in self.node_ids if self.kind_of(node_id) == KIND_TERMINAL
        )

        self.label_dimensions: dict[str, Sequence[Any]] = {
            dimension: as_sequence(values)
            for dimension, values in as_mapping(
                as_mapping(doc.get("label_schema")).get("dimensions")
            ).items()
        }

        self.graph = Graph(self.node_ids, self.edges)

    def kind_of(self, node_id: str) -> str | None:
        return as_text(self.node_by_id.get(node_id, {}).get("kind"))

    def node_pointer(self, node_id: str, *tail: str | int) -> str:
        """``/nodes/<index>`` for a blueprint node, since blueprint nodes are an array."""
        index = self.node_indexes.get(node_id)
        if index is None:
            return pointer("nodes")
        return pointer("nodes", index, *tail)

    def schema_of(self, node_id: str, key: str) -> Any:
        """A node's ``input_schema`` or ``output_schema``, or ``None``."""
        return self.node_by_id.get(node_id, {}).get(key)

    def max_iterations_of(self, node_id: str) -> int | None:
        return as_int(self.node_by_id.get(node_id, {}).get("max_iterations"))


class DatasetView:
    """Read-only derived structure over a raw dataset mapping."""

    def __init__(self, doc: Mapping[str, Any]) -> None:
        self.doc = doc
        self.provenance = as_mapping(doc.get("provenance"))
        self.author = as_mapping(self.provenance.get("author"))
        self.labels = as_mapping(doc.get("labels"))
        self.entities = {
            entity_id: as_mapping(entity)
            for entity_id, entity in as_mapping(doc.get("entities")).items()
        }
        self.nodes = {
            node_id: as_mapping(fixture)
            for node_id, fixture in as_mapping(doc.get("nodes")).items()
        }
        self.pools = {
            node_id: [as_mapping(entry) for entry in as_sequence(entries)]
            for node_id, entries in as_mapping(doc.get("pools")).items()
        }
        self.expected = as_mapping(doc.get("expected"))
        self.blueprint_ref = as_mapping(doc.get("blueprint"))

    def revisions_of(self, entity_id: str) -> Mapping[str, Any]:
        """An entity's declared revisions, or an empty mapping if it has none."""
        return as_mapping(self.entities.get(entity_id, {}).get("revisions"))

    def has_revisions_block(self, entity_id: str) -> bool:
        """Whether an entity declares ``revisions`` at all - DS-008's precondition."""
        return isinstance(self.entities.get(entity_id, {}).get("revisions"), Mapping)

    def fixture_sites(self) -> list[tuple[str, str, Mapping[str, Any]]]:
        """Every fixture in the document as ``(node_id, pointer_prefix, fixture)``.

        The entity-ref rules (DS-006, DS-008, DS-009, DS-010) apply to pool
        fixtures exactly as they apply to node fixtures, so they iterate this
        rather than ``nodes`` alone. Ordering is deterministic - ``nodes`` then
        ``pools``, each by key - because findings are compared as ordered lists
        in the tests.
        """
        sites: list[tuple[str, str, Mapping[str, Any]]] = []
        for node_id in sorted(self.nodes):
            sites.append((node_id, pointer("nodes", node_id), self.nodes[node_id]))
        for node_id in sorted(self.pools):
            for index, entry in enumerate(self.pools[node_id]):
                sites.append((node_id, pointer("pools", node_id, index), entry))
        return sites


class BlueprintContext:
    """The argument every ``BP-*`` rule takes."""

    def __init__(self, doc: Mapping[str, Any], resolver: Resolver) -> None:
        self.doc = doc
        self.bp = BlueprintView(doc)
        self.resolver = resolver

    def error(self, rule: str, at: str, message: str, **context: Any) -> RuleError:
        """A finding against a blueprint. ``section`` is always ``None``.

        A blueprint is authored as one document - there is no skeleton and no
        section to repair - and contracts section 1 allows a null ``section``
        for exactly this case.
        """
        return RuleError(
            rule=rule,
            severity=SEVERITY_WARNING if rule in WARNING_RULES else SEVERITY_ERROR,
            pointer=at,
            message=message,
            section=None,
            context=context,
        )


class DatasetContext:
    """The argument every ``DS-*`` rule takes.

    ``blueprint`` is ``None`` when DS-001 could not resolve one. The registry
    marks which rules need it, and the runner skips those - so a rule that
    declares ``needs_blueprint`` may read :attr:`blueprint` without a guard.
    """

    def __init__(
        self,
        doc: Mapping[str, Any],
        resolver: Resolver,
        *,
        blueprint: Mapping[str, Any] | None = None,
        duplicate_keys: Sequence[str] = (),
    ) -> None:
        self.doc = doc
        self.ds = DatasetView(doc)
        self.resolver = resolver
        self.duplicate_keys = tuple(duplicate_keys)
        self._blueprint = BlueprintView(blueprint) if blueprint is not None else None

    @property
    def has_blueprint(self) -> bool:
        return self._blueprint is not None

    @property
    def blueprint(self) -> BlueprintView:
        if self._blueprint is None:  # pragma: no cover - the registry prevents this
            raise RuntimeError(
                "a rule that reads the blueprint must declare needs_blueprint in the registry"
            )
        return self._blueprint

    @property
    def pool_nodes(self) -> frozenset[str]:
        """Blueprint node ids with ``pool: true``, for section attribution."""
        return self._blueprint.pool_node_ids if self._blueprint is not None else frozenset()

    def error(self, rule: str, at: str, message: str, **context: Any) -> RuleError:
        """A finding against a dataset, with its skeleton section filled in.

        The section comes from the pointer (ruling R-06's five-section
        manifest), so a rejection can be repaired one section at a time rather
        than by regenerating the whole dataset.
        """
        return RuleError(
            rule=rule,
            severity=SEVERITY_WARNING if rule in WARNING_RULES else SEVERITY_ERROR,
            pointer=at,
            message=message,
            section=section_for_pointer(at, pool_nodes=self.pool_nodes),
            context=context,
        )
