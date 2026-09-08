"""The error envelope, the warning shape, and the runtime code constants.

`docs/contracts.md` section 1. The service never raises for anything a user
could cause: every tool answers with one of these envelopes instead.

Severity is part of the *item*, not the envelope: a document that violates only
warning-severity rules still stores, so ``ok`` can be ``True`` while ``errors``
is non-empty (ruling R-13). Callers decide by looking at ``severity``.
"""

from typing import Any, Final

from pydantic import Field, StrictBool

from agentprops.models.base import StrictModel

__all__ = [
    "ERROR_ENVELOPE_SEVERITIES",
    "RT_E01",
    "RT_E02",
    "RT_E03",
    "RT_E04",
    "RUNTIME_ERROR_CODES",
    "RUNTIME_WARNING_CODES",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "WARNING_BLUEPRINT_VERSION_MISMATCH",
    "WARNING_DATASET_ARCHIVED",
    "WARNING_POOL_EXHAUSTED",
    "ErrorEnvelope",
    "RuleError",
    "SuccessEnvelope",
    "Warning",
]

SEVERITY_ERROR: Final = "error"
SEVERITY_WARNING: Final = "warning"

#: The two legal values of :attr:`RuleError.severity`. Not a ``Literal`` on the
#: field, per ruling R-04; the catalogue is what assigns a severity, so nothing
#: user-supplied ever reaches this field.
ERROR_ENVELOPE_SEVERITIES: Final[frozenset[str]] = frozenset({SEVERITY_ERROR, SEVERITY_WARNING})

# Runtime error codes, contracts section 3.4. RT-E05 ("iteration exceeds
# max_iterations") is deliberately absent: ruling R-03 deletes it, because
# PRD 5.2 requires a repeat-and-warn (`pool_exhausted`) instead and ground
# rule 3 says the service never gates. A negative iteration, or a non-zero
# iteration against a `pool: false` node, is reported as RT-E02.
RT_E01: Final = "RT-E01"  # step ambiguous; the response lists candidate node ids
RT_E02: Final = "RT-E02"  # unknown node or tool name for this blueprint
RT_E03: Final = "RT-E03"  # unknown run id
RT_E04: Final = "RT-E04"  # dataset not found, or archived and not pinned by this run

RUNTIME_ERROR_CODES: Final[frozenset[str]] = frozenset({RT_E01, RT_E02, RT_E03, RT_E04})

WARNING_BLUEPRINT_VERSION_MISMATCH: Final = "blueprint_version_mismatch"
WARNING_POOL_EXHAUSTED: Final = "pool_exhausted"
WARNING_DATASET_ARCHIVED: Final = "dataset_archived"

RUNTIME_WARNING_CODES: Final[frozenset[str]] = frozenset(
    {
        WARNING_BLUEPRINT_VERSION_MISMATCH,
        WARNING_POOL_EXHAUSTED,
        WARNING_DATASET_ARCHIVED,
    }
)


class RuleError(StrictModel):
    """One catalogue finding against a submitted document.

    Tests assert on :attr:`rule`, never on :attr:`message`; messages get
    reworded.
    """

    rule: str
    """The catalogue id: ``BP-001``, ``DS-014``, ``SK-002``."""

    severity: str
    """``"error"`` or ``"warning"``. Warnings never block a write or a read."""

    pointer: str
    """RFC 6901 JSON pointer into the submitted document."""

    message: str
    """Human-readable. Not a test surface."""

    section: str | None = None
    """The skeleton section this finding belongs to, so a partial fill can be
    repaired without regenerating the whole dataset. ``None`` for
    non-skeleton contexts."""

    context: dict[str, Any] = Field(default_factory=dict)
    """Machine-readable detail: the offending ids, the conflicting node."""


class Warning(StrictModel):
    """A non-blocking condition, attached to a response and to the stored run.

    Codes are in :data:`RUNTIME_WARNING_CODES`. Shadows the builtin
    ``Warning``; the name comes from ``docs/contracts.md`` section 2.3 and the
    M1 model list, and nothing in this package needs the builtin.
    """

    code: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(StrictModel):
    """The failure envelope, and what ``*_validate`` returns either way.

    ``dataset_validate`` and ``blueprint_validate`` return this with
    ``ok=True, errors=[]`` for a clean document, and with ``ok=True`` plus
    warning-severity items for a document that only trips warnings (ruling
    R-13).
    """

    ok: StrictBool
    errors: list[RuleError] = Field(default_factory=list)


class SuccessEnvelope(StrictModel):
    """The success envelope: ``{"ok": true, "data": {...}, "warnings": [...]}``."""

    ok: StrictBool
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Warning] = Field(default_factory=list)
