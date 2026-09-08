"""The skeleton aggregate: partial fill state for an in-progress dataset.

Neither ``Skeleton`` nor ``Section`` is defined anywhere in the specification;
both shapes come from ruling R-05 and R-06. ``Skeleton`` is derived from the
``skeletons`` DDL in contracts section 7 - ``id, agent_id, bp_version,
manifest, parts, submitted_as, created_at`` - with ``manifest`` typed as a list
of sections rather than opaque JSON.

The fourth aggregate, alongside blueprint, dataset and run: its own DDL table,
its own ``SK-*`` rules, its own service module. Hence its own module here.
"""

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from pydantic import Field, StrictBool

from agentprops.models.base import StrictModel

__all__ = ["SECTION_IDS", "Section", "Skeleton"]

#: The manifest, in order, fixed by ruling R-06. Exactly five sections, which
#: is what keeps M5's "five separate ``dataset_fill_part`` calls" gate
#: literally true.
#:
#: Two fields the PRD requires have no section of their own and are assigned
#: here: ``narrative`` belongs to ``provenance`` (PRD 5.7 groups title,
#: narrative, intent and labels as the four required provenance pieces), and
#: ``pools`` belongs to ``nodes.branches``, alongside the loop node's own
#: fixture. ``labels`` and ``seed`` are ``dataset_skeleton`` *inputs*, not
#: fillable sections. Contracts section 1's ``"section": "nodes.compliance"``
#: is a slip for ``nodes.core``.
SECTION_IDS: Final[tuple[str, ...]] = (
    "provenance",
    "entities",
    "nodes.core",
    "nodes.branches",
    "expected",
)


class Section(StrictModel):
    """One fillable part of a skeleton. Shape fixed by ruling R-06.

    SK-001 checks that a named section exists in the manifest. SK-002 means *a
    section may not be filled before every earlier-ordinal section is filled* -
    re-filling an already-filled section is explicitly allowed, because PRD 6
    flow B depends on it: a rejection returns errors scoped to the section that
    caused them so the LLM repairs one part rather than regenerating
    everything.
    """

    id: str
    """One of :data:`SECTION_IDS`."""

    required: StrictBool
    """SK-004: ``dataset_submit`` needs every required section filled."""

    pointers: list[str]
    """RFC 6901 pointers into the dataset document this section owns. What lets
    a ``RuleError.pointer`` be mapped back to a ``RuleError.section``."""

    description: str
    """What the filling LLM is being asked for."""


class Skeleton(StrictModel):
    """An in-progress dataset: the manifest, plus whatever has been filled.

    Column names are the DDL's, verbatim (``bp_version``, not
    ``blueprint_version``), so an adapter maps row to model without a
    translation table.
    """

    id: UUID
    """Derived deterministically by ``Seeded.uuid()``, per ruling R-10."""

    agent_id: str
    bp_version: str

    manifest: list[Section]
    """The five sections of :data:`SECTION_IDS`, in order."""

    parts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Section id to the content filled for it. Empty until the first
    ``dataset_fill_part``; the DDL declares the same default."""

    submitted_as: UUID | None = None
    """The dataset this skeleton became. SK-005 rejects a second submit."""

    created_at: datetime
    """Carried, never stamped here (ruling R-09). The column has a ``now()``
    default; the service supplies the value through its ``Clock`` port."""
