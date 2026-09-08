"""Pure validation functions. No I/O, no clock, no random source, no store.

The public surface is two functions:

    validate_blueprint(document, resolver=NullResolver()) -> ErrorEnvelope
    validate_dataset(document, resolver, duplicate_keys=()) -> ErrorEnvelope

Four properties of that surface are rulings later milestones depend on:

**The raw document, not a model** (ruling R-23). Both take the ``dict`` off
``json.loads``. Every finding carries an RFC 6901 pointer "into the submitted
document" (`docs/contracts.md` section 1), which is only meaningful against the
document that was submitted - not against a model instance that may have
coerced, defaulted or dropped a field. `service/` validates first and
constructs the model second.

**Never raises for a validation failure** (CLAUDE.md's style rule). A malformed
document produces findings. The only exceptions that escape are programming
errors.

**An injected resolver, never a store** (ruling R-11). DS-001, DS-031 and BP-016
are existence checks; the two lookups they need arrive as a
:class:`~agentprops.validation.resolver.Resolver`, so this package imports
nothing from `storage/`.

**Warnings do not reject** (ruling R-13). ``ok`` is ``False`` only when an
``error``-severity finding is present. BP-019, DS-007, DS-027 and DS-032 are
warnings: a document that trips only those stores, and the warnings ride along
in ``errors`` with ``severity: "warning"``.

Rules run in catalogue order, from `registry.py`, so the error list is
deterministic. Every rule runs on every document - the validator reports every
problem in one pass rather than stopping at the first, which is what the
corpus's ``MULTI-*`` cases exist to prove.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from agentprops.models.errors import SEVERITY_ERROR, ErrorEnvelope, RuleError
from agentprops.validation.context import (
    WARNING_RULES,
    BlueprintContext,
    DatasetContext,
)
from agentprops.validation.rawjson import parse_with_duplicate_keys
from agentprops.validation.registry import (
    RULE_REGISTRY,
    TARGET_BLUEPRINT,
    TARGET_DATASET,
    RuleSpec,
)
from agentprops.validation.resolver import NullResolver, Resolver

__all__ = [
    "RULE_REGISTRY",
    "WARNING_RULES",
    "BlueprintContext",
    "DatasetContext",
    "NullResolver",
    "Resolver",
    "RuleSpec",
    "envelope",
    "parse_with_duplicate_keys",
    "validate_blueprint",
    "validate_dataset",
]


def envelope(findings: Sequence[RuleError]) -> ErrorEnvelope:
    """Wrap findings in the contracts section 1 envelope.

    ``ok`` is ``False`` only if an ``error``-severity finding is present, so a
    warning-only document reports ``ok: true`` *with* its warnings (ruling
    R-13). Warnings never block a write or a read.
    """
    return ErrorEnvelope(
        ok=not any(finding.severity == SEVERITY_ERROR for finding in findings),
        errors=list(findings),
    )


def _run(specs: Sequence[RuleSpec], ctx: object) -> list[RuleError]:
    findings: list[RuleError] = []
    for spec in specs:
        findings.extend(spec.check(ctx))
    return findings


def validate_blueprint(
    document: Mapping[str, Any], resolver: Resolver | None = None
) -> ErrorEnvelope:
    """Run every ``BP-*`` rule against a raw blueprint document.

    ``resolver`` is optional and defaults to one that knows nothing, which is
    the right answer for ``blueprint_validate``: the document is checked on its
    own terms and BP-016 cannot fire. The publish path passes a real resolver,
    and BP-016 then rejects an upsert against an existing published
    ``{agent_id, version}``.
    """
    ctx = BlueprintContext(document, resolver if resolver is not None else NullResolver())
    specs = [spec for spec in RULE_REGISTRY.values() if spec.target == TARGET_BLUEPRINT]
    return envelope(_run(specs, ctx))


def validate_dataset(
    document: Mapping[str, Any],
    resolver: Resolver,
    *,
    duplicate_keys: Sequence[str] = (),
) -> ErrorEnvelope:
    """Run every ``DS-*`` rule against a raw dataset document.

    ``duplicate_keys`` carries RFC 6901 pointers to keys the *raw text* held
    twice, from :func:`~agentprops.validation.rawjson.parse_with_duplicate_keys`
    at the tool boundary. DS-013 is the only rule that reads them, because a
    parsed document cannot show a duplicate key (ruling R-20).

    When DS-001 cannot resolve the blueprint, the rules that need one are
    skipped: a fixture cannot be checked against a schema that is not there, and
    twenty consequential findings would bury the one that matters. The
    document-local rules - provenance, narrative, seed, faults - still run.
    """
    blueprint = _resolve_blueprint(document, resolver)
    ctx = DatasetContext(
        document, resolver, blueprint=blueprint, duplicate_keys=tuple(duplicate_keys)
    )
    specs = [
        spec
        for spec in RULE_REGISTRY.values()
        if spec.target == TARGET_DATASET and (ctx.has_blueprint or not spec.needs_blueprint)
    ]
    return envelope(_run(specs, ctx))


def _resolve_blueprint(document: Mapping[str, Any], resolver: Resolver) -> Mapping[str, Any] | None:
    ref = document.get("blueprint")
    if not isinstance(ref, Mapping):
        return None
    agent_id = ref.get("agent_id")
    version = ref.get("version")
    if not isinstance(agent_id, str) or not isinstance(version, str):
        return None
    return resolver.get_published_blueprint(agent_id, version)
