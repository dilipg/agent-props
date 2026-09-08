"""Everything the catalogue needs from `jsonschema`, and the `entity:` convention.

Node schemas are user-supplied Draft 2020-12 JSON Schema, which Pydantic cannot
express - CLAUDE.md's style rule sends them here instead. Two conventions from
`docs/contracts.md` section 2.1 live in this module:

**The ``entity:`` ref.** A node schema references a shared noun as
``{"$ref": "entity:store"}``. That is a *local* convention, not a resolvable
URI: handing it to `jsonschema` unresolved raises ``Unresolvable``, which is why
:func:`resolve_entity_refs` substitutes the entity's schema before any instance
is validated. Ruling R-18 splits the two rules that care: **BP-009** owns
whether a ref resolves, and **BP-011** checks schema *syntax* with the refs
stripped, so an unresolvable entity reports BP-009 alone rather than two ids.

**Conditions are never evaluated.** Nothing here executes JSONLogic. BP-010
walks the condition tree structurally (ruling R-19) and asks this module only
for the set of properties a schema declares.
"""

import copy
import json
from collections.abc import Iterator, Mapping
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from agentprops.validation.pointers import pointer

__all__ = [
    "ENTITY_REF_PREFIX",
    "InstanceFinding",
    "canonical",
    "declared_properties",
    "entity_ref_names",
    "entity_ref_sites",
    "instance_findings",
    "resolve_entity_refs",
    "schema_error",
    "strip_entity_refs",
]

ENTITY_REF_PREFIX: Final = "entity:"

#: How deep :func:`resolve_entity_refs` will follow entity-to-entity refs before
#: treating the chain as cyclic. An entity whose schema refs itself resolves to
#: the empty schema (accept anything) at the point of recursion rather than
#: hanging; no rule in the catalogue owns that case, so the permissive choice is
#: the one that cannot reject a document for a reason no rule states.
MAX_ENTITY_REF_DEPTH: Final = 16


def _entity_ref_target(node: Any) -> str | None:
    """The entity name in ``{"$ref": "entity:store"}``, or ``None``."""
    if not isinstance(node, Mapping):
        return None
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(ENTITY_REF_PREFIX):
        return ref[len(ENTITY_REF_PREFIX) :]
    return None


def _walk_entity_refs(
    node: Any, path: tuple[str | int, ...]
) -> Iterator[tuple[tuple[str | int, ...], str]]:
    name = _entity_ref_target(node)
    if name is not None:
        yield path, name
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            if isinstance(key, str):
                yield from _walk_entity_refs(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_entity_refs(value, (*path, index))


def entity_ref_names(schema: Any) -> list[tuple[tuple[str | int, ...], str]]:
    """Every ``entity:`` ref in ``schema``, as ``(path, entity name)`` pairs.

    BP-009's input. The path is relative to the schema, so the rule can point at
    the ref itself rather than at the whole node.
    """
    return list(_walk_entity_refs(schema, ()))


def resolve_entity_refs(schema: Any, entity_schemas: Mapping[str, Any], *, _depth: int = 0) -> Any:
    """A copy of ``schema`` with every ``entity:`` ref replaced by its schema.

    A ref to an entity that does not exist resolves to the **empty schema**, not
    to a dangling ``$ref``: BP-009 already reports the unknown entity, and
    leaving the ref in place would make `jsonschema` raise ``Unresolvable``
    inside DS-004 and turn one blueprint error into a second, misleading dataset
    error.

    Sibling keywords beside a ``$ref`` are preserved and win, which is Draft
    2020-12's own reading of ``$ref`` alongside other keywords.
    """
    name = _entity_ref_target(schema)
    if name is not None and isinstance(schema, Mapping):
        siblings = {
            key: resolve_entity_refs(value, entity_schemas, _depth=_depth + 1)
            for key, value in schema.items()
            if key != "$ref"
        }
        target = entity_schemas.get(name)
        if target is None or _depth >= MAX_ENTITY_REF_DEPTH:
            resolved: dict[str, Any] = {}
        else:
            expanded = resolve_entity_refs(target, entity_schemas, _depth=_depth + 1)
            resolved = dict(expanded) if isinstance(expanded, Mapping) else {}
        return {**resolved, **siblings}
    if isinstance(schema, Mapping):
        return {
            key: resolve_entity_refs(value, entity_schemas, _depth=_depth + 1)
            for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [resolve_entity_refs(value, entity_schemas, _depth=_depth + 1) for value in schema]
    return copy.deepcopy(schema)


def strip_entity_refs(schema: Any) -> Any:
    """A copy of ``schema`` with every ``entity:`` ref replaced by ``{}``.

    BP-011 and BP-012 check syntax on this, per ruling R-18: an unresolvable
    entity is BP-009's finding alone.
    """
    return resolve_entity_refs(schema, {})


def schema_error(schema: Any) -> str | None:
    """``None`` if ``schema`` is valid Draft 2020-12, else the reason.

    Pass a schema with its entity refs *stripped* - BP-011's territory is
    syntax.
    """
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return str(exc.message)
    return None


class InstanceFinding:
    """One `jsonschema` validation failure: where in the instance, and why."""

    __slots__ = ("message", "path")

    def __init__(self, path: tuple[str | int, ...], message: str) -> None:
        self.path = path
        self.message = message

    def pointer_under(self, prefix: str) -> str:
        return prefix + pointer(*self.path)


def instance_findings(instance: Any, schema: Any) -> list[InstanceFinding]:
    """Validate ``instance`` against an already-resolved ``schema``.

    An unusable schema is reported as a single finding at the instance root
    rather than raised. That should be unreachable in production - BP-011 and
    BP-012 run before a blueprint is published - but neither rule covers an
    *entity* schema (see DECISIONS.md), so DS-004 has to survive one. "Cannot be
    validated" is reported as "does not validate", because the alternative is to
    pass a fixture nobody checked.
    """
    try:
        errors = list(Draft202012Validator(schema).iter_errors(instance))
    except Exception as exc:  # a bad schema must never raise out of a rule
        return [InstanceFinding((), f"schema could not be applied: {exc}")]
    findings = [
        InstanceFinding(
            tuple(part for part in error.path if isinstance(part, str | int)), error.message
        )
        for error in errors
    ]
    return sorted(findings, key=lambda finding: [str(part) for part in finding.path])


def _collect_properties(schema: Any, into: dict[str, Any], depth: int) -> None:
    if not isinstance(schema, Mapping) or depth > MAX_ENTITY_REF_DEPTH:
        return
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for key, value in properties.items():
            if isinstance(key, str):
                into.setdefault(key, value)
    for combinator in ("allOf", "anyOf", "oneOf", "then", "else"):
        branch = schema.get(combinator)
        if isinstance(branch, list):
            for member in branch:
                _collect_properties(member, into, depth + 1)
        elif isinstance(branch, Mapping):
            _collect_properties(branch, into, depth + 1)


def declared_properties(schema: Any, entity_schemas: Mapping[str, Any]) -> dict[str, Any]:
    """The properties a schema declares, following ``entity:`` refs and branches.

    BP-010's walk (ruling R-19) needs "is this segment a declared property of
    that schema", and a schema may declare it under ``properties`` or inside an
    ``allOf``/``anyOf``/``oneOf`` branch. One branch declaring it is enough:
    BP-010 checks that the author is not naming a field the schema never
    mentions, not that the field is always present.
    """
    collected: dict[str, Any] = {}
    _collect_properties(resolve_entity_refs(schema, entity_schemas), collected, 0)
    return collected


def _walk_sites(
    schema: Any,
    instance: Any,
    entity: str,
    path: tuple[str | int, ...],
    sites: list[tuple[tuple[str | int, ...], Any]],
    depth: int,
) -> None:
    if not isinstance(schema, Mapping) or depth > MAX_ENTITY_REF_DEPTH:
        return
    if _entity_ref_target(schema) == entity:
        # Stops here rather than descending: the whole value at this location
        # *is* the entity's state, so there is nothing finer to point at. The
        # consequence, unreachable in the golden fixtures and worth knowing
        # anyway: an entity embedded inside *another* entity's schema
        # contributes no site of its own, because the walk never enters the
        # outer entity's substituted body. DS-008 compares the outer state,
        # which contains the inner one, so drift is still caught - just
        # reported one level up.
        sites.append((path, instance))
        return
    properties = schema.get("properties")
    if isinstance(properties, Mapping) and isinstance(instance, Mapping):
        for key, subschema in properties.items():
            if isinstance(key, str) and key in instance:
                _walk_sites(subschema, instance[key], entity, (*path, key), sites, depth + 1)
    additional = schema.get("additionalProperties")
    if isinstance(additional, Mapping) and isinstance(instance, Mapping):
        declared = properties if isinstance(properties, Mapping) else {}
        for key, value in instance.items():
            if isinstance(key, str) and key not in declared:
                _walk_sites(additional, value, entity, (*path, key), sites, depth + 1)
    items = schema.get("items")
    if isinstance(items, Mapping) and isinstance(instance, list):
        for index, value in enumerate(instance):
            _walk_sites(items, value, entity, (*path, index), sites, depth + 1)
    prefix_items = schema.get("prefixItems")
    if isinstance(prefix_items, list) and isinstance(instance, list):
        for index, subschema in enumerate(prefix_items):
            if index < len(instance):
                _walk_sites(subschema, instance[index], entity, (*path, index), sites, depth + 1)
    for combinator in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(combinator)
        if isinstance(branches, list):
            for branch in branches:
                _walk_sites(branch, instance, entity, path, sites, depth + 1)


def entity_ref_sites(
    schema: Any, instance: Any, entity: str
) -> list[tuple[tuple[str | int, ...], Any]]:
    """Where an entity's state is embedded in a fixture, and what is there.

    This is the mechanism DS-008 needs and no document defines - ruling R-14
    asks for it to be defined and recorded. The *blueprint* says where an entity
    lives in a fixture, so walk the node's schema to each
    ``{"$ref": "entity:<entity>"}`` and read the matching location out of the
    instance. A node that declares an entity in ``entity_refs`` but embeds no
    copy of its state - ``verify_compliance`` takes only a ``store_id`` - yields
    no site and contributes nothing to DS-008.

    Only locations with a deterministic instance counterpart are followed:
    ``properties``, ``additionalProperties``, ``items`` and ``prefixItems``.
    """
    sites: list[tuple[tuple[str | int, ...], Any]] = []
    _walk_sites(schema, instance, entity, (), sites, 0)
    return sites


def canonical(value: Any) -> str:
    """A stable text form of a JSON value, for identity comparison.

    Used by DS-008 (an entity's state is the same everywhere) and BP-016 (a
    re-publish is identical, ruling R-29).

    "Byte-identical" (DS-008's wording) is read as *canonically* identical: keys
    sorted, so a re-ordered object is not drift, but ``1`` and ``1.0`` - and
    ``true`` and ``1`` - are drift, because they are different JSON values that
    Python's ``==`` treats as equal. See DECISIONS.md and ruling R-30.

    A value ``json.dumps`` cannot serialise falls back to ``repr``. That is
    unreachable from ``json.loads`` output, but ruling R-23 notes that
    ``dataset_validate(dataset: object)`` may be handed an already-parsed
    object - a ``datetime``, a ``UUID``, a ``Decimal`` - and a rule must report
    rather than raise. ``repr`` is stable within a process, which is all an
    identity comparison needs.
    """
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return repr(value)
