"""RFC 6901 pointer construction, and the pointer-to-section mapping.

Every finding carries a pointer "into the submitted document"
(`docs/contracts.md` section 1) and, for a dataset, the skeleton ``section``
that pointer belongs to, so a rejection can be repaired one section at a time
(PRD 6, flow B).

The five sections and their ordering are ruling R-06's:
``provenance``, ``entities``, ``nodes.core``, ``nodes.branches``, ``expected``.
Two of the mappings here come straight from that ruling and are not obvious
from the field names alone: ``narrative`` belongs to **provenance**, and
``pools`` belongs to **nodes.branches** alongside the loop node's own fixture.

Three top-level fields have no section, and get ``None``, which contracts
section 1 explicitly allows ("Null for non-skeleton contexts"):
``blueprint``, ``seed`` and ``labels``. R-06 settles that ``labels`` and
``seed`` are ``dataset_skeleton`` *inputs* rather than fillable sections, so an
error against one cannot be repaired by re-filling a section - the caller has
to start a new skeleton. Blueprint findings have no section at all: a blueprint
is authored as a whole document, never filled section by section.
"""

from typing import Final

__all__ = [
    "SECTIONS",
    "SECTION_ENTITIES",
    "SECTION_EXPECTED",
    "SECTION_NODES_BRANCHES",
    "SECTION_NODES_CORE",
    "SECTION_PROVENANCE",
    "escape_token",
    "pointer",
    "section_for_pointer",
]

SECTION_PROVENANCE: Final = "provenance"
SECTION_ENTITIES: Final = "entities"
SECTION_NODES_CORE: Final = "nodes.core"
SECTION_NODES_BRANCHES: Final = "nodes.branches"
SECTION_EXPECTED: Final = "expected"

#: The manifest order (ruling R-06). M5 builds the skeleton from this list.
SECTIONS: Final[tuple[str, ...]] = (
    SECTION_PROVENANCE,
    SECTION_ENTITIES,
    SECTION_NODES_CORE,
    SECTION_NODES_BRANCHES,
    SECTION_EXPECTED,
)


def escape_token(token: str) -> str:
    """Escape one reference token: ``~`` becomes ``~0``, ``/`` becomes ``~1``.

    Order matters - ``~`` first, or ``~1`` produced by the second substitution
    would be re-escaped into ``~01``.
    """
    return token.replace("~", "~0").replace("/", "~1")


def pointer(*tokens: str | int) -> str:
    """Build an RFC 6901 pointer. ``pointer("nodes", "a/b", 0)`` -> ``/nodes/a~1b/0``."""
    return "".join(f"/{escape_token(str(token))}" for token in tokens)


def section_for_pointer(
    target_pointer: str, *, pool_nodes: frozenset[str] = frozenset()
) -> str | None:
    """The skeleton section a dataset pointer belongs to, or ``None``.

    ``pool_nodes`` is the set of blueprint node ids with ``pool: true``. It only
    matters for the ``/nodes/<id>`` case: a pool node's fixture belongs in
    ``pools`` (ruling R-01), so if one turns up under ``/nodes`` anyway - which
    is a DS-003 error - the finding is still filed against the section the
    author would have to repair, ``nodes.branches``.
    """
    tokens = [
        token.replace("~1", "/").replace("~0", "~") for token in target_pointer.split("/")[1:]
    ]
    if not tokens:
        return None
    head = tokens[0]
    if head in {"provenance", "narrative"}:
        return SECTION_PROVENANCE
    if head == "entities":
        return SECTION_ENTITIES
    if head == "pools":
        return SECTION_NODES_BRANCHES
    if head == "nodes":
        node_id = tokens[1] if len(tokens) > 1 else ""
        return SECTION_NODES_BRANCHES if node_id in pool_nodes else SECTION_NODES_CORE
    if head == "expected":
        return SECTION_EXPECTED
    return None
