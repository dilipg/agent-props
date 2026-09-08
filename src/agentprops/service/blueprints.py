"""Blueprint orchestration: validate, then parse, then store (ruling R-23).

Five functions, one per blueprint tool. Storage-agnostic - everything here goes
through the ``Store`` Protocol on the :class:`~agentprops.service.context.ServiceContext`
and none of it names an adapter.

The write pipeline, in the order ruling R-23 fixes
--------------------------------------------------

``upsert`` is the first end-to-end implementation of that ordering, and each
step exists because skipping it loses a rule id:

1. **resolve the payload** - a document argument that is not a JSON object is a
   boundary finding, because there is nothing for a rule to point into;
2. **normalise ``status``** from the ``publish`` flag, before validation. See
   below; this is the one substantive decision in this module;
3. **validate the raw document** against the catalogue, with the real resolver,
   so BP-016 can see the stored published version;
4. **stop on an error-severity finding.** Warning-severity findings (BP-019)
   do not stop anything - ruling R-13 - and ride back on the success envelope;
5. **construct the model**, translating a ``ValidationError`` into findings
   rather than letting it escape (rulings R-04 and R-23's residue);
6. **store**, translating the adapter's two programming-error guards into
   findings for the same reason.

Why ``status`` is normalised before validation
----------------------------------------------

``blueprint_upsert`` takes ``publish`` as a separate argument, and
``Store.put_blueprint`` documents ``publish`` as "the single source of truth for
the stored status: the document's own ``status`` is normalised to match, so the
flag and the document cannot drift apart". BP-016 compares the *submitted*
document against the *stored* one canonically (ruling R-29). If the service
validated the document as submitted and then stored a normalised copy, the two
comparisons would be looking at different documents - and there is a reachable
sequence where that difference is a crash rather than a rule id:

    submit a document identical to a published version but with
    ``status: "published"``, and pass ``publish=False``

BP-016 sees two identical documents, reports nothing, and then ``put_blueprint``
normalises ``status`` to ``draft``, finds a difference against a published row,
and raises ``PublishedVersionImmutableError`` - a user-caused exception, which
CLAUDE.md forbids. Normalising first makes the two comparisons identical by
construction, so that sequence reports BP-016 as it should. (Step 6 still
catches the exception. A guard whose only proof is "the code above cannot reach
it" is the shape of claim the M3 fix rounds were spent on;
``test_service_blueprints.py`` monkeypatches the store into raising and asserts
the envelope, so the losing side has a test.)

Normalising costs nothing: no rule reads ``status``, so no finding's pointer
moves, and it makes ``status`` a *derived* field - a caller may omit it
entirely, and ``publish`` decides.
"""

from __future__ import annotations

from typing import Any

from agentprops.models import Blueprint, RuleError
from agentprops.service.context import ServiceContext
from agentprops.service.diff import diff_blueprints
from agentprops.service.documents import parse, read_document
from agentprops.service.envelope import (
    AP_STORE_REFUSED,
    Reply,
    blocking,
    boundary,
    failure,
    field_pointer,
    not_found,
    success,
    warning,
    warnings_from,
)
from agentprops.storage import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    PublishedVersionImmutableError,
    StoreError,
)
from agentprops.validation import envelope as validation_envelope
from agentprops.validation import validate_blueprint

__all__ = [
    "WARNING_BLUEPRINT_VERSION_MISSING",
    "diff",
    "get",
    "list_summaries",
    "upsert",
    "validate",
]

#: Attached by :func:`diff` when a version it was asked to compare is absent.
#: ``Warning.code`` is an open string so the vocabulary grows without a model
#: change (ruling R-22); this is the first addition beyond contracts 3.4's
#: three runtime codes.
WARNING_BLUEPRINT_VERSION_MISSING = "blueprint_version_missing"


def upsert(context: ServiceContext, payload: object, *, publish: bool) -> Reply:
    """Validate, parse and store a blueprint. See the module docstring's pipeline."""
    document = read_document("blueprint", payload)
    if not document.ok:
        return failure(list(document.findings))
    normalised = dict(document.value)
    normalised["status"] = STATUS_PUBLISHED if publish else STATUS_DRAFT

    findings = validate_blueprint(normalised, context.resolver).errors
    if blocking(findings):
        return failure(findings)

    model, shape_findings = parse(Blueprint, normalised)
    if model is None:
        return failure(findings + shape_findings)

    return _store(context, model, publish=publish, findings=findings)


def _store(
    context: ServiceContext, model: Blueprint, *, publish: bool, findings: list[RuleError]
) -> Reply:
    """Step 6. The adapter's two guards become findings, never exceptions.

    ``PublishedVersionImmutableError`` maps to **BP-016**, because that is the
    rule the condition belongs to and a caller must not have to learn a second
    vocabulary for the same problem. Any other ``StoreError`` maps to
    :data:`~agentprops.service.envelope.AP_STORE_REFUSED`, which means a rule
    that should have caught the condition did not - a defect, reported as an
    envelope rather than as a stack trace.
    """
    try:
        stored = context.store.put_blueprint(model, publish)
    except PublishedVersionImmutableError as exc:
        return failure(
            [
                boundary(
                    "BP-016",
                    field_pointer("version"),
                    str(exc),
                    agent_id=model.agent_id,
                    version=model.version,
                )
            ]
        )
    except StoreError as exc:
        return failure([boundary(AP_STORE_REFUSED, field_pointer("blueprint"), str(exc))])
    return success("blueprint", _document(stored), warnings_from(findings))


def get(context: ServiceContext, agent_id: str, version: str | None) -> Reply:
    """One blueprint. The latest *published* version when ``version`` is omitted."""
    blueprint = context.store.get_blueprint(agent_id, version)
    if blueprint is None:
        return not_found("blueprint", field="agent_id", agent_id=agent_id, version=version)
    return success("blueprint", _document(blueprint))


def list_summaries(context: ServiceContext, status: str | None) -> Reply:
    """Blueprint summaries, in the store's ``(agent_id, semver)`` order (ruling R-35).

    A ``status`` that is not ``draft`` or ``published`` returns an empty list
    rather than an error: the service never gates, and the store's filter has
    no rows to match. The order is **not** re-sorted here - ruling R-35 makes it
    total at the storage layer, and re-sorting in Python would put ``1.10.0``
    before ``1.9.0`` again.
    """
    rows = context.store.list_blueprints(status)
    return success("blueprints", [row.model_dump(mode="json") for row in rows])


def validate(context: ServiceContext, payload: object) -> Reply:
    """``{ok, errors}`` for a blueprint document. Stores nothing.

    Returns the validator's own envelope unchanged, so a document whose only
    findings are warnings reports ``ok: true`` *with* its warnings in
    ``errors`` (ruling R-13). ``status`` is **not** normalised here: there is no
    ``publish`` flag to normalise from, and validating a document on its own
    terms is what this tool is for.

    The resolver is the real one, so BP-016 fires if the caller is validating a
    document against an already-published version - which is the useful answer,
    and is why this does not use ``validate_blueprint``'s ``NullResolver``
    default.
    """
    document = read_document("blueprint", payload)
    if not document.ok:
        return validation_envelope(list(document.findings))
    return validate_blueprint(document.value, context.resolver)


def diff(context: ServiceContext, agent_id: str, from_version: str, to_version: str) -> Reply:
    """A structured diff between two versions. **Never a failure signal.**

    contracts section 4 makes that unconditional, so an absent version is
    ``ok: true`` with ``present: false`` on that side and a
    :data:`WARNING_BLUEPRINT_VERSION_MISSING` warning - not an ``AP-004``. This
    is the one read in the service that must not report ``not_found``, and the
    reason is in the contract row rather than in a preference.
    """
    before = context.store.get_blueprint(agent_id, from_version)
    after = context.store.get_blueprint(agent_id, to_version)
    warnings = [
        warning(WARNING_BLUEPRINT_VERSION_MISSING, agent_id=agent_id, version=version, side=side)
        for side, version, found in (
            ("from", from_version, before),
            ("to", to_version, after),
        )
        if found is None
    ]
    computed = diff_blueprints(
        agent_id,
        from_version,
        _document(before) if before is not None else None,
        to_version,
        _document(after) if after is not None else None,
    )
    return success("diff", computed, warnings)


def _document(blueprint: Blueprint) -> dict[str, Any]:
    """The stored document, as submitted.

    ``exclude_unset=True`` per ruling R-08's round-trip criterion: the golden
    blueprint omits ``tool_name`` on three nodes and ``max_iterations`` on
    eight, and a full dump would hand them back as ``null``. That matters
    beyond tidiness - the bytes a caller reads here are the bytes BP-016
    compares a re-publish against.
    """
    document: dict[str, Any] = blueprint.model_dump(mode="json", exclude_unset=True)
    return document
