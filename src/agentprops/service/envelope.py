"""Envelope construction, and the eight boundary codes that are not rules.

`docs/contracts.md` section 1 defines two shapes and CLAUDE.md's style rule says
every tool answers with one of them: **structured errors, never exceptions, for
anything a user could cause.** This module is where they are built, once, so no
tool assembles a dict by hand and no two tools disagree about the shape.

The success envelope's ``data`` convention
------------------------------------------

``data`` is always an object with **one named key** holding the payload -
``{"blueprint": {...}}``, ``{"blueprints": [...]}``, ``{"diff": {...}}``.
contracts section 4's "Returns" column describes the payload, not where in the
envelope it sits, and the named key is the only convention that works for the
tools whose payload is an *array*: ``data`` is typed ``dict[str, Any]``, so
``blueprint_list`` could not put its rows there directly. Using the same shape
for the object-valued tools costs one level of nesting and buys a caller that
never has to ask which tools wrap and which do not. It also means a field added
to ``data`` later can never collide with a payload key.

The boundary codes
------------------

Three failures a user can cause have **no catalogue rule**, because the
catalogue validates documents and these happen before or after a document
exists:

- a request argument of the wrong JSON type,
- raw JSON text that does not parse,
- a document that passes every rule and still will not construct as a model.

The last one is the residue of rulings R-04 and R-23 and is not hypothetical.
R-04 keeps value constraints out of the models so a bad value reaches the
validator as a rule id, and R-23 orders validation *before* parsing - but the
models keep ``extra="forbid"`` and their structural types, and no ``BP-*`` or
``DS-*`` rule owns an unknown top-level key or a list where an object belongs.
R-07's second amendment is the recorded case of that gap producing a raw
``ValidationError`` where a rule id belonged. So the gap is closed here, by
translating ``ValidationError`` into findings, rather than left for a caller to
discover as a stack trace.

Two more codes cover resolution rather than validation: an id that names
nothing, and a store that refused a write. contracts section 1 covers the first
explicitly - the envelope is what a tool returns "on a validation **or
resolution** failure".

A sixth, added at M5 by ruling R-49(b), covers a deterministic **id space** with
no free value left. It is the counterpart to :data:`AP_ARGUMENT` rather than a
variant of it: ``AP-001`` means an argument is malformed, and this means every
argument is well formed and the request still cannot be served. Collapsing the
two would tell a caller to fix a value that is already correct.

A seventh, added at M8's fix round by ruling R-65, covers a **write-once** value
that is already recorded and **differs** from the one supplied: the write did not
happen, and ``ok: true`` would claim a success the store declined to give. An
*identical* re-write is not this - it is a no-op success, because the caller's
intent is already satisfied.

An eighth, added at M10 for ``run_export``, covers an **outward** target that
refused the trace or was not listening. It is the same rule as the seventh
pointed outward: nothing was published, so ``ok: true`` would claim a success
the transport declined to give. It is the first code in this family about
something outside this process, which is why its context carries the endpoint.

``AP-*`` ids are **not catalogue rules** and must never be registered as ones.
``tests/unit/test_service_envelope.py::test_boundary_codes_are_not_catalogue_rules``
asserts they are disjoint from ``RULE_REGISTRY`` and from the documented
catalogue, so the drift test in `test_validation_drift.py` stays green and
nobody mistakes one for a rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from pydantic import ValidationError

from agentprops.models import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    ErrorEnvelope,
    RuleError,
    SuccessEnvelope,
    Warning,
)
from agentprops.validation.pointers import pointer, section_for_pointer

__all__ = [
    "AP_ARGUMENT",
    "AP_DOCUMENT_SHAPE",
    "AP_EXPORT_REFUSED",
    "AP_ID_SPACE_EXHAUSTED",
    "AP_MALFORMED_JSON",
    "AP_NOT_FOUND",
    "AP_STORE_REFUSED",
    "AP_WRITE_ONCE_CONFLICT",
    "BOUNDARY_CODES",
    "Reply",
    "blocking",
    "boundary",
    "failure",
    "field_pointer",
    "findings_from_validation_error",
    "not_found",
    "runtime",
    "success",
    "warning",
    "warnings_from",
]

#: A request argument is missing, of the wrong JSON type, or mutually exclusive
#: with another argument that was also given.
AP_ARGUMENT: Final = "AP-001"

#: Raw JSON text did not parse. Not a catalogue violation - a request that
#: never became a document.
AP_MALFORMED_JSON: Final = "AP-002"

#: The document satisfied every catalogue rule and still would not construct as
#: a model. See the module docstring: this is the R-04/R-23 residue.
AP_DOCUMENT_SHAPE: Final = "AP-003"

#: No record with that id, or no such version.
AP_NOT_FOUND: Final = "AP-004"

#: The store refused a write through one of its programming-error guards.
#: Reaching this means a rule that should have caught the condition did not.
AP_STORE_REFUSED: Final = "AP-005"

#: A deterministic id space is exhausted: every id derivable from the given
#: arguments already names a record (ruling R-49(b)). Distinct from
#: :data:`AP_ARGUMENT` on purpose - the arguments are well formed, and telling a
#: caller to fix a value that is already correct is worse than saying nothing.
AP_ID_SPACE_EXHAUSTED: Final = "AP-006"

#: A **write-once** value is already recorded and differs from the one supplied,
#: so the write did not happen (ruling R-65). Added at M8's fix round for
#: ``record_step`` and ``run_finish``.
#:
#: The distinction R-65 draws is between the two halves of a repeated write, not
#: between serving and refusing: an **identical** re-write is a no-op success,
#: because the caller's intent is already satisfied and a retry after a network
#: blip must be safe, while a **differing** one is refused, because returning
#: ``ok: true`` would report a success the storage layer explicitly declined to
#: give - which is M3's silent-success defect said louder. Ground rule 3's
#: "never gates" governs refusing to *serve*; R-56 already settled that a
#: refused **write** is ``ok: false`` and is not gating.
#:
#: Distinct from :data:`AP_STORE_REFUSED`, and the distinction is the whole
#: reason both exist: this means "a value is there and it is not yours", and
#: ``AP-005`` means the store refused with **nothing** recorded. Telling a
#: caller its evidence lost to a value that does not exist is the error M8's
#: classification was written to avoid.
AP_WRITE_ONCE_CONFLICT: Final = "AP-007"

#: An eighth, added at M10 for ``run_export``: an **outward export target**
#: refused the trace, or is not there. Nothing was published; nothing about the
#: run changed.
#:
#: It is not any of the seven above, and the near misses are worth naming.
#: ``AP-001`` means an argument is malformed, and here every argument is well
#: formed - an unknown ``target`` *is* ``AP-001``, and this is the case where
#: the target was one of the two and the collector at the other end was not
#: listening. ``AP-005`` is a *store* refusing a write through a
#: programming-error guard, which is a defect in this service; a collector
#: refusing a batch is a fact about someone else's deployment, and the only
#: actionable thing in it is the endpoint, which travels in the finding's
#: context (ruling R-60: put the URL beside the count).
#:
#: **Why this is a failure rather than a warning.** ``ok: true`` would claim a
#: publication that did not happen, which is ruling R-65's rule one layer out -
#: "a success the transport declined to give". And it is not gating: ground
#: rule 3 forbids refusing to *serve*, and R-56 settled that a refused write is
#: ``ok: false``. An export is a write, outward.
AP_EXPORT_REFUSED: Final = "AP-008"

BOUNDARY_CODES: Final[frozenset[str]] = frozenset(
    {
        AP_ARGUMENT,
        AP_MALFORMED_JSON,
        AP_DOCUMENT_SHAPE,
        AP_NOT_FOUND,
        AP_STORE_REFUSED,
        AP_ID_SPACE_EXHAUSTED,
        AP_WRITE_ONCE_CONFLICT,
        AP_EXPORT_REFUSED,
    }
)

#: What every service function returns. `server/` dumps it and returns the dict.
type Reply = SuccessEnvelope | ErrorEnvelope


def success(key: str, payload: Any, warnings: Sequence[Warning] = ()) -> SuccessEnvelope:
    """``{"ok": true, "data": {key: payload}, "warnings": [...]}``."""
    return SuccessEnvelope(ok=True, data={key: payload}, warnings=list(warnings))


def failure(errors: Sequence[RuleError]) -> ErrorEnvelope:
    """``{"ok": false, "errors": [...]}``.

    ``ok`` is hard-coded ``False`` rather than derived from the severities,
    because a caller reaching for this has already decided the request failed.
    A *validation* envelope - where a warning-only document must report
    ``ok: true`` (ruling R-13) - comes from
    :func:`agentprops.validation.envelope` instead and is returned unchanged.
    """
    return ErrorEnvelope(ok=False, errors=list(errors))


def boundary(code: str, target: str, message: str, **context: Any) -> RuleError:
    """One boundary finding. ``target`` is an RFC 6901 pointer, already built."""
    return RuleError(
        rule=code,
        severity=SEVERITY_ERROR,
        pointer=target,
        message=message,
        section=None,
        context=context,
    )


def runtime(code: str, target: str, message: str, **context: Any) -> RuleError:
    """One **runtime** finding: contracts 3.4's ``RT-*`` codes.

    Same ``RuleError`` shape as :func:`boundary` and deliberately a separate
    name, because the two families answer different questions and a reader
    should be able to tell which one a call site is emitting. ``AP-*`` means
    "the request was malformed or named nothing"; ``RT-*`` means "the request
    was well formed and the runtime cannot resolve it against this run's pin" -
    an unknown run, an unknown node, an ambiguous step. Neither family is a
    catalogue rule and neither is ever registered as one.

    ``target`` points at the offending *argument*, since a runtime failure has
    no submitted document to point into.
    """
    return RuleError(
        rule=code,
        severity=SEVERITY_ERROR,
        pointer=target,
        message=message,
        section=None,
        context=context,
    )


def warning(code: str, **detail: Any) -> Warning:
    """One non-blocking condition, for the ``warnings`` list."""
    return Warning(code=code, detail=detail)


def warnings_from(findings: Sequence[RuleError]) -> list[Warning]:
    """The warning-severity findings, as ``Warning`` entries for a success envelope.

    This is the shape ground rule 3 asks for on a *write* path: "mismatches
    produce warnings attached to the response". Ruling R-13 makes BP-019,
    DS-007, DS-027 and DS-032 warnings, so a document that trips only those
    stores - and the findings have to ride back to the caller somewhere other
    than ``errors``, which the success envelope does not have.

    The rule id becomes ``Warning.code`` and everything else becomes
    ``detail``, so nothing the validator reported is dropped. ``Warning.code``
    is an open string precisely so the vocabulary can grow this way (ruling
    R-22).

    Order is the validator's order, which is catalogue order, which is
    deterministic.
    """
    return [
        Warning(
            code=finding.rule,
            detail={
                "pointer": finding.pointer,
                "message": finding.message,
                "section": finding.section,
                "context": finding.context,
            },
        )
        for finding in findings
        if finding.severity == SEVERITY_WARNING
    ]


def findings_from_validation_error(
    error: ValidationError, *, pool_nodes: frozenset[str] = frozenset()
) -> list[RuleError]:
    """Translate a Pydantic ``ValidationError`` into :data:`AP_DOCUMENT_SHAPE` findings.

    Called only *after* the catalogue passed, so every finding here is a shape
    problem no rule owns. Each Pydantic error becomes one finding with an RFC
    6901 pointer built from its ``loc``, which is exactly what contracts section
    1 asks for - "a pointer into the submitted document".

    ``pool_nodes`` is threaded through to :func:`section_for_pointer` so a
    dataset shape error is filed against the skeleton section that would have to
    be re-filled to repair it, the same as a rule finding. Empty for a
    blueprint, which has no sections at all.

    One Pydantic detail is worth naming: a ``loc`` entry for a union member is a
    string like ``"dict[str,any]"`` and one for a list index is an ``int``.
    Both are stringified into pointer tokens, and :func:`escape_token` handles
    the ``/`` such a synthetic token can contain.
    """
    findings: list[RuleError] = []
    for detail in error.errors():
        target = pointer(*detail["loc"])
        findings.append(
            RuleError(
                rule=AP_DOCUMENT_SHAPE,
                severity=SEVERITY_ERROR,
                pointer=target,
                message=str(detail["msg"]),
                section=section_for_pointer(target, pool_nodes=pool_nodes),
                context={"type": str(detail["type"]), "loc": [str(part) for part in detail["loc"]]},
            )
        )
    return findings


def blocking(findings: Sequence[RuleError]) -> bool:
    """Whether any finding is ``error`` severity, and therefore blocks a write.

    The one place the ruling R-13 question - "does this document store?" - is
    asked, so a write path never re-derives it.
    """
    return any(finding.severity == SEVERITY_ERROR for finding in findings)


def field_pointer(*tokens: str | int) -> str:
    """An RFC 6901 pointer at a *request argument* rather than into a document.

    A boundary finding often has no document to point into - the argument was
    the wrong type, so there is nothing to address inside it. Pointing at the
    argument name keeps :attr:`RuleError.pointer` meaningful and non-empty for
    every finding the surface can emit.

    Varargs because one case does have somewhere more precise to point:
    ``field_pointer("labels", "tier")`` gives ``/labels/tier``, so a caller
    whose ``labels`` object carries one non-string value is told which
    dimension rather than being handed the whole argument back. Tokens are
    escaped, so a dimension containing ``/`` cannot forge a pointer segment.
    """
    return pointer(*tokens)


def not_found(what: str, *, field: str, **context: Any) -> ErrorEnvelope:
    """The envelope for an id that names nothing. A *resolution* failure.

    contracts section 1 covers it explicitly ("on a validation **or resolution**
    failure"), and ruling R-42(a) ratifies ``ok: false`` for it: "the service
    never gates" is about *policy* - it must not refuse to serve because
    something looked wrong - not about pretending a nonexistent id resolved.

    ``field`` is the argument the pointer addresses, and it is a **named
    parameter** rather than the first key of ``context``. It was the first key
    once, which happened to be right for both callers and would silently point
    at ``/version`` the first time someone wrote
    ``not_found("dataset", version=..., dataset_id=...)``. A pointer that
    depends on kwarg insertion order is a pointer that is correct by luck.
    """
    return failure(
        [
            boundary(
                AP_NOT_FOUND,
                field_pointer(field),
                f"no {what} matches {_describe(context)}.",
                **context,
            )
        ]
    )


def _describe(context: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in context.items()) or "the given filter"
