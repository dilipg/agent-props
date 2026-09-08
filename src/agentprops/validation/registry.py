"""Rule id to callable, so the catalogue is machine-checkable rather than prose.

This module is the reason the prose catalogue and the code cannot drift apart.
Two tests in `tests/unit/test_validation_drift.py` read it:

- ``test_every_rule_has_an_implementation`` asserts that the ids documented in
  `docs/contracts.md` sections 3.1-3.3, filtered to the milestone's prefixes,
  are exactly :data:`RULE_REGISTRY`'s keys. Section 3.4 is a response-code
  table, not part of the registry (ruling R-12).
- ``test_every_rule_has_a_broken_fixture`` asserts that every id here is
  exercised by at least one corpus case.

So adding a row to the catalogue without an entry here fails CI, and vice versa.

:class:`RuleSpec` carries three things beyond the callable, all of which exist
to keep a decision out of the rule bodies:

``severity``
    From :data:`~agentprops.validation.context.WARNING_RULES`. Ruling R-13: a
    document whose only findings are warnings still stores.
``needs_blueprint``
    Whether the rule reads the dataset's blueprint. When DS-001 cannot resolve
    one, the runner skips these rather than reporting twenty consequential
    errors that bury the real one.
``summary``
    The first line of the rule's own docstring, so the registry never restates
    the catalogue in a second place that could be wrong.

The ``check`` field is typed ``Callable[[Any], ...]`` on purpose. The blueprint
rules take a ``BlueprintContext`` and the dataset rules a ``DatasetContext``;
one heterogeneous table plus a runner that knows which context to build is
simpler than a generic registry, and ``target`` is what makes the pairing
explicit.

``SK-*`` is deliberately absent: skeleton rules arrive at M5 with the skeleton
pipeline, and the drift test takes the prefix set as a parameter so their
absence is correct now rather than a failure.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from agentprops.models.errors import SEVERITY_ERROR, SEVERITY_WARNING, RuleError
from agentprops.validation import blueprint as bp
from agentprops.validation import dataset as ds
from agentprops.validation import timeline as tl
from agentprops.validation.context import WARNING_RULES

__all__ = [
    "RULE_REGISTRY",
    "TARGET_BLUEPRINT",
    "TARGET_DATASET",
    "RuleSpec",
]

TARGET_BLUEPRINT: Final = "blueprint"
TARGET_DATASET: Final = "dataset"


@dataclass(frozen=True)
class RuleSpec:
    """One catalogue rule, as the runner and the drift tests see it."""

    rule: str
    target: str
    check: Callable[[Any], list[RuleError]]
    needs_blueprint: bool = False

    @property
    def severity(self) -> str:
        return SEVERITY_WARNING if self.rule in WARNING_RULES else SEVERITY_ERROR

    @property
    def summary(self) -> str:
        """The rule's first docstring line - the catalogue text, stated once."""
        doc = (self.check.__doc__ or "").strip()
        return doc.splitlines()[0] if doc else ""


def _spec(
    rule: str,
    target: str,
    check: Callable[[Any], list[RuleError]],
    *,
    needs_blueprint: bool = False,
) -> tuple[str, RuleSpec]:
    return rule, RuleSpec(rule=rule, target=target, check=check, needs_blueprint=needs_blueprint)


#: Every implemented rule, keyed by catalogue id and ordered by it. Findings are
#: produced in this order, which makes the error list deterministic.
RULE_REGISTRY: Final[Mapping[str, RuleSpec]] = dict(
    [
        # 3.1 Blueprint rules
        _spec("BP-001", TARGET_BLUEPRINT, bp.bp_001),
        _spec("BP-002", TARGET_BLUEPRINT, bp.bp_002),
        _spec("BP-003", TARGET_BLUEPRINT, bp.bp_003),
        _spec("BP-004", TARGET_BLUEPRINT, bp.bp_004),
        _spec("BP-005", TARGET_BLUEPRINT, bp.bp_005),
        _spec("BP-006", TARGET_BLUEPRINT, bp.bp_006),
        _spec("BP-007", TARGET_BLUEPRINT, bp.bp_007),
        _spec("BP-008", TARGET_BLUEPRINT, bp.bp_008),
        _spec("BP-009", TARGET_BLUEPRINT, bp.bp_009),
        _spec("BP-010", TARGET_BLUEPRINT, bp.bp_010),
        _spec("BP-011", TARGET_BLUEPRINT, bp.bp_011),
        _spec("BP-012", TARGET_BLUEPRINT, bp.bp_012),
        _spec("BP-013", TARGET_BLUEPRINT, bp.bp_013),
        _spec("BP-014", TARGET_BLUEPRINT, bp.bp_014),
        _spec("BP-015", TARGET_BLUEPRINT, bp.bp_015),
        _spec("BP-016", TARGET_BLUEPRINT, bp.bp_016),
        _spec("BP-017", TARGET_BLUEPRINT, bp.bp_017),
        _spec("BP-018", TARGET_BLUEPRINT, bp.bp_018),
        _spec("BP-019", TARGET_BLUEPRINT, bp.bp_019),
        # 3.2 Dataset rules
        _spec("DS-001", TARGET_DATASET, ds.ds_001),
        _spec("DS-002", TARGET_DATASET, ds.ds_002, needs_blueprint=True),
        _spec("DS-003", TARGET_DATASET, ds.ds_003, needs_blueprint=True),
        _spec("DS-004", TARGET_DATASET, ds.ds_004, needs_blueprint=True),
        _spec("DS-005", TARGET_DATASET, ds.ds_005, needs_blueprint=True),
        _spec("DS-006", TARGET_DATASET, ds.ds_006),
        _spec("DS-007", TARGET_DATASET, ds.ds_007),
        _spec("DS-008", TARGET_DATASET, tl.ds_008, needs_blueprint=True),
        _spec("DS-009", TARGET_DATASET, tl.ds_009),
        _spec("DS-010", TARGET_DATASET, tl.ds_010, needs_blueprint=True),
        _spec("DS-011", TARGET_DATASET, tl.ds_011, needs_blueprint=True),
        _spec("DS-012", TARGET_DATASET, ds.ds_012, needs_blueprint=True),
        _spec("DS-013", TARGET_DATASET, ds.ds_013),
        _spec("DS-014", TARGET_DATASET, ds.ds_014, needs_blueprint=True),
        _spec("DS-015", TARGET_DATASET, ds.ds_015, needs_blueprint=True),
        _spec("DS-016", TARGET_DATASET, ds.ds_016, needs_blueprint=True),
        _spec("DS-017", TARGET_DATASET, ds.ds_017),
        _spec("DS-018", TARGET_DATASET, ds.ds_018, needs_blueprint=True),
        _spec("DS-019", TARGET_DATASET, ds.ds_019, needs_blueprint=True),
        _spec("DS-020", TARGET_DATASET, ds.ds_020),
        _spec("DS-021", TARGET_DATASET, ds.ds_021),
        _spec("DS-022", TARGET_DATASET, ds.ds_022),
        _spec("DS-023", TARGET_DATASET, ds.ds_023, needs_blueprint=True),
        _spec("DS-024", TARGET_DATASET, ds.ds_024, needs_blueprint=True),
        _spec("DS-025", TARGET_DATASET, ds.ds_025),
        _spec("DS-026", TARGET_DATASET, ds.ds_026),
        _spec("DS-027", TARGET_DATASET, ds.ds_027),
        _spec("DS-028", TARGET_DATASET, ds.ds_028),
        _spec("DS-029", TARGET_DATASET, ds.ds_029),
        _spec("DS-030", TARGET_DATASET, ds.ds_030),
        _spec("DS-031", TARGET_DATASET, ds.ds_031),
        _spec("DS-032", TARGET_DATASET, ds.ds_032),
        _spec("DS-033", TARGET_DATASET, ds.ds_033),
    ]
)
