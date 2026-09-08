"""The five ``SK-*`` rules, and the two phases they split into.

`docs/contracts.md` section 3.3. M2 deliberately left these to M5: they are the
only rules whose subject is not a *document* but the server-side fill state a
``skeleton_id`` names, so they need their own context and their own runner.

    SK-001  the named section exists in the skeleton's manifest
    SK-002  sections are filled in manifest order
    SK-003  a node section references only entities already declared
    SK-004  ``dataset_submit`` requires every required section to be filled
    SK-005  ``skeleton_id`` exists and has not already been submitted

Two phases, because a rule that cannot fire is worse than absent
----------------------------------------------------------------

SK-001, SK-002 and SK-003 are about *one fill request*: they need a section
name and its content, which a submit does not have. SK-004 is about the
skeleton as a whole and only means anything at submit. SK-005 guards both.

So there are two runners -
:func:`~agentprops.validation.validate_fill` and
:func:`~agentprops.validation.validate_submit`, in this package's ``__init__``
beside the two document runners - and :data:`FILL_RULES` / :data:`SUBMIT_RULES`
say which rules each one runs. A test asserts their union is exactly the
``SK-*`` registry, so a sixth skeleton rule cannot be added and then silently
never run. The alternative - one runner and a phase check inside each rule -
hides the same decision in five places.

Precedence, and why each skip is here rather than in the caller
---------------------------------------------------------------

Ruling R-26's principle: *when one rule's violation removes the thing a second
rule would check against, the first rule owns the finding and the second
skips.* Three applications, all of them inside the lower-priority rule so the
rules stay order-free:

- **SK-001 before SK-002.** A section name that is not in the manifest has no
  ordinal, so "is every earlier-ordinal section filled" has no answer.
- **SK-002 before SK-003.** SK-003 checks node ``entity_refs`` against the
  *filled* ``entities`` section; if that section is unfilled, SK-002 already
  fires (``entities`` is earlier-ordinal than both node sections) and SK-003
  would otherwise report one finding per reference on top of it.
- **SK-004 before every ``DS-*`` rule scoped to an unfilled section**, which is
  ruling R-45 and lives in `service/skeletons.py` because it decides whether
  the dataset catalogue runs at all.

SK-003 checks the ``entity_id`` segment only, never ``@revision`` - the same
split ruling R-18 makes between DS-006 and DS-009.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from agentprops.models.errors import SEVERITY_ERROR, RuleError
from agentprops.validation.context import as_mapping, as_sequence, as_text
from agentprops.validation.pointers import (
    SECTION_ENTITIES,
    SECTION_NODES_BRANCHES,
    SECTION_NODES_CORE,
    pointer,
    section_for_pointer,
)

__all__ = [
    "FILL_RULES",
    "NODE_SECTIONS",
    "SUBMIT_RULES",
    "SkeletonContext",
    "sk_001",
    "sk_002",
    "sk_003",
    "sk_004",
    "sk_005",
]

#: The two sections whose content is node fixtures, and therefore the two
#: SK-003 applies to. ``nodes.core`` owns ``/nodes``; ``nodes.branches`` owns
#: ``/pools``, which is where a ``pool: true`` node's fixtures live (rulings
#: R-01 and R-06).
NODE_SECTIONS: Final[frozenset[str]] = frozenset({SECTION_NODES_CORE, SECTION_NODES_BRANCHES})


class SkeletonContext:
    """What an ``SK-*`` rule is handed. A read-only view of the fill state.

    Pure, like every other context in this package: no store, no clock. The
    caller has already read the skeleton and passes its manifest and filled
    parts in.

    ``skeleton_id`` is carried for the finding's context and pointer, and
    ``exists``/``submitted_as`` are what SK-005 reads. A skeleton that does not
    exist is represented as ``exists=False`` with an empty manifest rather than
    as ``None``, so every rule can run against a total object and no rule needs
    a guard for it - SK-005 fires, and the caller stops there.
    """

    def __init__(
        self,
        skeleton_id: str,
        *,
        exists: bool = True,
        submitted_as: str | None = None,
        manifest: Sequence[Mapping[str, Any]] = (),
        parts: Mapping[str, Mapping[str, Any]] | None = None,
        section: str = "",
        content: Mapping[str, Any] | None = None,
    ) -> None:
        self.skeleton_id = skeleton_id
        self.exists = exists
        self.submitted_as = submitted_as
        self.manifest = [as_mapping(entry) for entry in manifest]
        self.parts = dict(parts or {})
        self.section = section
        self.content = dict(content or {})

    @property
    def section_ids(self) -> list[str]:
        """The manifest's section ids, in manifest order."""
        return [str(entry.get("id")) for entry in self.manifest]

    @property
    def required_section_ids(self) -> list[str]:
        """The manifest's required section ids, in manifest order."""
        return [str(entry.get("id")) for entry in self.manifest if entry.get("required") is True]

    def ordinal(self, section_id: str) -> int | None:
        """``section_id``'s position in the manifest, or ``None`` if absent."""
        ids = self.section_ids
        return ids.index(section_id) if section_id in ids else None

    def pointers_for(self, section_id: str) -> list[str]:
        """The RFC 6901 pointers a manifest section declares it owns."""
        for entry in self.manifest:
            if entry.get("id") == section_id:
                return [str(target) for target in as_sequence(entry.get("pointers"))]
        return []

    def is_filled(self, section_id: str) -> bool:
        return section_id in self.parts

    def error(self, rule: str, at: str, message: str, **context: Any) -> RuleError:
        """A finding against the skeleton, scoped to the section to repair.

        ``section`` is passed explicitly through ``context["section"]`` when the
        finding belongs to a section that :func:`section_for_pointer` cannot
        derive from the pointer - a pointer at ``/skeleton_id`` addresses a
        request argument, not a place in the dataset document, so it has no
        section at all.
        """
        scoped = context.pop("section", None)
        return RuleError(
            rule=rule,
            severity=SEVERITY_ERROR,
            pointer=at,
            message=message,
            section=scoped if scoped is not None else section_for_pointer(at),
            context=context,
        )


def _argument(name: str) -> str:
    """A pointer at a request argument rather than into a document."""
    return pointer(name)


def sk_001(ctx: SkeletonContext) -> list[RuleError]:
    """The named section exists in the skeleton's manifest.

    ``section`` on the finding is ``None`` rather than the name that was asked
    for: the five section ids in contracts section 1 are the legal values, and a
    name outside them is not a section a caller could re-fill. The rejected name
    is in ``context`` instead, with the manifest that would have accepted it.
    """
    if ctx.ordinal(ctx.section) is not None:
        return []
    return [
        ctx.error(
            "SK-001",
            _argument("section"),
            f"{ctx.section!r} is not a section of this skeleton's manifest; the sections are "
            f"{ctx.section_ids}.",
            section=None,
            requested=ctx.section,
            manifest=ctx.section_ids,
        )
    ]


def sk_002(ctx: SkeletonContext) -> list[RuleError]:
    """Sections are filled in manifest order. Entities before any node section.

    Ruling R-06 states this as *a section may not be filled before every
    earlier-ordinal section is filled*, and **re-filling an already-filled
    section is explicitly allowed** - PRD 6 flow B depends on it, since a
    rejection returns errors scoped to one section so the LLM repairs that part
    rather than regenerating everything. A repair loop that cannot re-fill is
    not a repair loop.

    One finding, scoped to the **earliest** unfilled predecessor, because that
    is the section the caller has to go and fill; the full blocking list is in
    ``context``. Reporting one finding per predecessor would scope four findings
    to four different sections for a single mistake.

    Skipped when SK-001 fired: an unknown section has no ordinal.
    """
    ordinal = ctx.ordinal(ctx.section)
    if ordinal is None:
        return []
    blocking = [
        section_id for section_id in ctx.section_ids[:ordinal] if not ctx.is_filled(section_id)
    ]
    if not blocking:
        return []
    first = blocking[0]
    return [
        ctx.error(
            "SK-002",
            _argument("section"),
            f"{first!r} must be filled before {ctx.section!r}: sections are filled in manifest "
            f"order.",
            section=first,
            requested=ctx.section,
            blocked_by=blocking,
        )
    ]


def sk_003(ctx: SkeletonContext) -> list[RuleError]:
    """A node section references only entities already declared in a filled section.

    The ``entity_id`` segment only - ``@revision`` is DS-009's, the same split
    ruling R-18 makes for DS-006. And only the *declaration*: whether the entity
    is well formed is DS-006's and the model's business at submit.

    Skipped unless the section being filled is a node section and
    :data:`~agentprops.validation.pointers.SECTION_ENTITIES` is filled. When it
    is unfilled, SK-002 owns the finding (ruling R-26): ``entities`` is
    earlier-ordinal than both node sections, so the two can never both fire.
    """
    if ctx.section not in NODE_SECTIONS or not ctx.is_filled(SECTION_ENTITIES):
        return []
    declared = set(as_mapping(ctx.parts[SECTION_ENTITIES].get("entities")))
    findings: list[RuleError] = []
    for at, node_id, index, ref in _entity_ref_sites(ctx.content):
        entity_id = ref.split("@", 1)[0]
        if entity_id in declared:
            continue
        findings.append(
            ctx.error(
                "SK-003",
                at,
                f"{node_id!r} references entity {entity_id!r}, which the filled entities section "
                f"does not declare; it declares {sorted(declared)}.",
                node_id=node_id,
                entity=entity_id,
                index=index,
            )
        )
    return findings


def _entity_ref_sites(content: Mapping[str, Any]) -> list[tuple[str, str, int, str]]:
    """Every ``entity_refs`` entry in a node section's content.

    Yields ``(pointer, node_id, index, ref)``. The pointer is into the
    *dataset document*, not into ``content``, and needs no translation: a
    section's content is a fragment of the dataset keyed at the top level, so
    ``/nodes/check_docs/entity_refs/0`` addresses the same place in both. That
    property is the reason ``content`` takes the keyed form rather than the bare
    value of the field.

    Deterministic order - ``nodes`` then ``pools``, each by key - because the
    findings are compared as ordered lists in the tests.
    """
    sites: list[tuple[str, str, int, str]] = []
    nodes = as_mapping(content.get("nodes"))
    for node_id in sorted(nodes):
        prefix = pointer("nodes", node_id)
        sites.extend(_refs_at(prefix, node_id, as_mapping(nodes[node_id])))
    pools = as_mapping(content.get("pools"))
    for node_id in sorted(pools):
        for index, entry in enumerate(as_sequence(pools[node_id])):
            prefix = pointer("pools", node_id, index)
            sites.extend(_refs_at(prefix, node_id, as_mapping(entry)))
    return sites


def _refs_at(
    prefix: str, node_id: str, fixture: Mapping[str, Any]
) -> list[tuple[str, str, int, str]]:
    """One fixture's ``entity_refs`` sites. A non-string entry is DS-006's."""
    sites: list[tuple[str, str, int, str]] = []
    for index, raw in enumerate(as_sequence(fixture.get("entity_refs"))):
        ref = as_text(raw)
        if ref is not None:
            sites.append((prefix + pointer("entity_refs", index), node_id, index, ref))
    return sites


def sk_004(ctx: SkeletonContext) -> list[RuleError]:
    """``dataset_submit`` requires every required section to be filled.

    One finding per unfilled required section, in manifest order, each pointed
    at the first pointer that section declares it owns and scoped to it - so a
    caller reads the response as a list of sections to fill.

    Ruling R-45 gives this rule **total precedence over section contents**: a
    never-filled provenance section is SK-004, not DS-025, because there is no
    provenance document for a ``DS-*`` rule to have an opinion about. The
    enforcement of that precedence is in `service/skeletons.py`, which is where
    the decision to run the dataset catalogue is taken.
    """
    findings: list[RuleError] = []
    for section_id in ctx.required_section_ids:
        if ctx.is_filled(section_id):
            continue
        targets = ctx.pointers_for(section_id)
        findings.append(
            ctx.error(
                "SK-004",
                targets[0] if targets else _argument("skeleton_id"),
                f"section {section_id!r} is required and has not been filled.",
                section=section_id,
                skeleton_id=ctx.skeleton_id,
            )
        )
    return findings


def sk_005(ctx: SkeletonContext) -> list[RuleError]:
    """``skeleton_id`` exists and has not already been submitted.

    Both halves are this rule's, including the existence half, which would
    otherwise be an ``AP-004``: the catalogue names the check, and a caller must
    not have to learn two vocabularies for one condition. It guards **both**
    tools - a submitted skeleton is frozen, so filling another part of one is
    refused for the same reason submitting it twice is.

    This is the rule that makes "a skeleton cannot become two datasets" true, so
    the tests exercise the losing side: a second submit, and a fill after a
    submit.
    """
    if not ctx.exists:
        return [
            ctx.error(
                "SK-005",
                _argument("skeleton_id"),
                f"no skeleton {ctx.skeleton_id!r}.",
                section=None,
                skeleton_id=ctx.skeleton_id,
            )
        ]
    if ctx.submitted_as is None:
        return []
    return [
        ctx.error(
            "SK-005",
            _argument("skeleton_id"),
            f"skeleton {ctx.skeleton_id!r} was already submitted as dataset "
            f"{ctx.submitted_as!r}; a skeleton becomes one dataset.",
            section=None,
            skeleton_id=ctx.skeleton_id,
            submitted_as=ctx.submitted_as,
        )
    ]


#: The rules a ``dataset_fill_part`` runs, in precedence order.
#: :func:`agentprops.validation.validate_fill` is the runner; the list lives
#: here, beside the rules, and `registry.py` is what makes it checkable.
FILL_RULES: Final[tuple[str, ...]] = ("SK-005", "SK-001", "SK-002", "SK-003")

#: The rules a ``dataset_submit`` runs, in precedence order. SK-001 to SK-003
#: need a section name and its content, which a submit does not have.
SUBMIT_RULES: Final[tuple[str, ...]] = ("SK-005", "SK-004")
