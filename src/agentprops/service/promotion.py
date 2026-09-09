"""``dataset_export`` and ``dataset_import``: local to shared, as one portable file.

PRD section 4 calls the pair "local to shared promotion" and that is exactly the
journey: a developer authors against `--store ./agentprops.db` or the ``local``
compose profile, exports a bundle, and imports it into the team's Postgres.
M7's acceptance criterion is that specific crossing - "a dataset exported from a
SQLite store imports into a Postgres store and validates".

The bundle
----------

One JSON object, self-describing, with the blueprint versions its datasets need
carried alongside them::

    {
      "format": "agentprops.bundle",
      "format_version": 1,
      "agent_id": "location-onboarding",
      "blueprints": [ <blueprint document>, ... ],
      "datasets":   [ <dataset document>, ... ]
    }

Three decisions in that shape, each of which had a cheaper alternative.

**The blueprints travel with the datasets.** contracts section 4 says the bundle
is a "blueprint version plus datasets", and it has to be: DS-001 requires a
dataset to name an **existing published blueprint**, so a bundle carrying only
datasets would fail its own validation on arrival at any store that had not
already seen the blueprint. Only the versions the exported datasets actually
reference are included - an export is a bundle of *those datasets*, not a backup
of the agent.

**The documents are the stored documents, verbatim.** Written with
``exclude_unset=True``, the same way the adapters write them, so a field the
author omitted stays omitted rather than coming back as ``null``. That is what
makes a bundle byte-stable across an export/import/export cycle, and it is why
ruling R-08's round-trip criterion was worth having.

**``format`` and ``format_version`` are checked on import, not guessed.** A
bundle is a file a human moves between machines, so the one thing worth being
strict about is whether this *is* one: an unknown ``format`` is ``AP-001``
naming what was expected, rather than a confusing cascade of ``DS-*`` findings
against something that was never a bundle.

Import re-runs **full** validation, and writes nothing until all of it passes
------------------------------------------------------------------------------

Every blueprint through ``validate_blueprint`` and every dataset through
``validate_dataset``, against **this** store's resolver - which is R-23's
ordering and ground rule 7's write-time strictness, and it is the whole point of
the milestone gate. A document that was valid in the store it left is *not*
necessarily valid in the store it arrives at: DS-001 and DS-031 are existence
checks against the receiving store, so a dataset whose ``supersedes`` names a
dataset that stayed behind is caught here and nowhere else.

The ordering that makes "nothing is written until all of it passes" true is
worth stating, because the obvious implementation does not have it. DS-001 asks
whether the dataset's blueprint is **published in this store**, and for a new
agent it is not until the bundle's blueprints are written - so the obvious order
is *publish the blueprints, then validate the datasets*, and a bundle whose
datasets fail then leaves a published blueprint version behind that BP-016 has
made immutable forever. :class:`_BundleResolver` removes that: it answers the
two existence lookups from the receiving store **plus the bundle's own
contents**, which is the question that actually matters - "will this document be
valid once this import finishes?" - so every document is validated before any
of them is written.

What that still does not buy is atomicity against a crash mid-write, and that
is stated rather than papered over: there is no transaction across the ``Store``
Protocol. The residue is a partial import, visible in ``dataset_find``, and
re-importing the same bundle is safe - a byte-identical re-publish is a no-op
(ruling R-29) and a re-imported dataset becomes a new version rather than
corrupting the old one.

What a version number means after an import
-------------------------------------------

``put_dataset`` allocates the version, ignoring the document's, so a dataset
exported at version 3 arrives at version 1 in an empty store. That is deliberate
and it is why ruling R-09 exists: **``created_at`` survives** - it comes from
``provenance.created_at``, authored content - so ``dataset_find``'s
``(created_at, id)`` ordering is identical on both sides of the crossing, which
is the property the gate actually depends on. Version *numbers* are a store's
own bookkeeping about its own edit history and cannot travel, because the
receiving store may already hold versions of the same lineage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from agentprops.models import Blueprint, Dataset, DatasetQuery, RuleError
from agentprops.service.context import ServiceContext
from agentprops.service.documents import parse, read_document
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_NOT_FOUND,
    AP_STORE_REFUSED,
    Reply,
    blocking,
    boundary,
    failure,
    field_pointer,
    success,
    warnings_from,
)
from agentprops.storage import (
    STATUS_PUBLISHED,
    PublishedVersionImmutableError,
    RecordNotFoundError,
    Store,
    StoreError,
)
from agentprops.storage.common import semver_key
from agentprops.validation import Resolver, validate_blueprint, validate_dataset

__all__ = [
    "BUNDLE_FORMAT",
    "BUNDLE_FORMAT_VERSION",
    "export_bundle",
    "import_bundle",
]

#: The ``format`` every bundle carries, and the value ``dataset_import``
#: requires. A name rather than a bare version marker, so a file found on a disk
#: in two years says what produced it.
BUNDLE_FORMAT: Final = "agentprops.bundle"

#: The bundle's own version, independent of any blueprint or dataset version.
#: One, and it stays one until the *shape above* changes - not when a dataset
#: field is added, because a bundle carries documents rather than describing
#: them.
BUNDLE_FORMAT_VERSION: Final = 1

#: The bundle's keys, in the order they are emitted. Explicit because the order
#: is part of the byte stability an export promises: two exports of the same
#: datasets have to produce the same bytes, and a dict assembled in two places
#: is a dict that eventually differs.
_BUNDLE_KEYS: Final = ("format", "format_version", "agent_id", "blueprints", "datasets")


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def export_bundle(
    context: ServiceContext, agent_id: str, dataset_ids: Sequence[str] | None
) -> Reply:
    """A portable bundle for ``agent_id``. See the module docstring for its shape.

    ``dataset_ids`` omitted exports every **discoverable** dataset for the
    agent, at its latest version: ``find_datasets``'s grain and ordering
    exactly, so archived lineages are left behind and the bundle's dataset order
    is the total ``(created_at, id)`` one - which is what makes two exports of
    one store byte-identical.

    ``dataset_ids`` given exports **those** lineages at their latest version,
    archived or not, in the order they were asked for. The asymmetry is
    ``dataset_get``'s and the reasoning is the same: an explicit id is a caller
    saying which dataset it means, and refusing to export an archived one it
    named would be a policy judgement this layer does not make.

    An id that names nothing, or names a dataset belonging to another agent, is
    ``AP-004``. A bundle silently missing a dataset the caller listed is the one
    outcome worth refusing, because the omission only becomes visible on the
    other machine.
    """
    datasets, missing = (
        (_discoverable(context, agent_id), [])
        if dataset_ids is None
        else _named(context, agent_id, dataset_ids)
    )
    if missing:
        return failure(missing)
    blueprints, absent = _referenced_blueprints(context, datasets)
    if absent:
        return failure(absent)
    return success("bundle", _bundle(agent_id, blueprints, datasets))


def _bundle(
    agent_id: str, blueprints: Sequence[Blueprint], datasets: Sequence[Dataset]
) -> dict[str, Any]:
    """The bundle object, with its keys in :data:`_BUNDLE_KEYS` order.

    Assembled in exactly one place so that two exports of the same content are
    the same bytes, which an export/import/export cycle is what proves.
    """
    values: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "agent_id": agent_id,
        "blueprints": [item.model_dump(mode="json", exclude_unset=True) for item in blueprints],
        "datasets": [item.model_dump(mode="json", exclude_unset=True) for item in datasets],
    }
    return {key: values[key] for key in _BUNDLE_KEYS}


def _discoverable(context: ServiceContext, agent_id: str) -> list[Dataset]:
    """Every discoverable dataset for ``agent_id``, at its latest version.

    ``limit=None`` on purpose, and it is one of the two places in this service
    that asks the store for an unbounded page. An export is defined as "these
    datasets", so a default page size would silently truncate a bundle - the
    failure that only shows up on the other machine, which is the failure this
    whole module exists to avoid. The read is bounded by the agent's authored
    volume, which is the bound ``label_vocabulary`` and ``runs._by_labels``
    already accepted for the same reason.
    """
    rows = context.store.find_datasets(DatasetQuery(agent_id=agent_id, limit=None, offset=None))
    found = [context.store.get_dataset(str(row.id), row.version) for row in rows]
    return [dataset for dataset in found if dataset is not None]


def _named(
    context: ServiceContext, agent_id: str, dataset_ids: Sequence[str]
) -> tuple[list[Dataset], list[RuleError]]:
    """The named lineages at their latest version, plus an ``AP-004`` per miss.

    A dataset belonging to a *different* agent is a miss rather than a silent
    inclusion: the bundle names one ``agent_id`` and carries the blueprints for
    it, so a foreign dataset would arrive without the blueprint DS-001 needs.
    """
    found: list[Dataset] = []
    missing: list[RuleError] = []
    for wanted in dataset_ids:
        dataset = context.store.get_dataset(wanted, None)
        if dataset is None or dataset.blueprint.agent_id != agent_id:
            missing.append(
                boundary(
                    AP_NOT_FOUND,
                    field_pointer("dataset_ids"),
                    f"no dataset matches dataset_id={wanted!r}, agent_id={agent_id!r}.",
                    dataset_id=wanted,
                    agent_id=agent_id,
                )
            )
            continue
        found.append(dataset)
    return found, missing


def _referenced_blueprints(
    context: ServiceContext, datasets: Sequence[Dataset]
) -> tuple[list[Blueprint], list[RuleError]]:
    """The blueprint versions ``datasets`` reference, newest last, plus any miss.

    Ordered by semver through the same
    :func:`~agentprops.storage.common.semver_key` ``list_blueprints`` uses, so a
    bundle carrying ``1.9.0`` and ``1.10.0`` lists them in that order on every
    backend. Deduplicated, because several datasets normally share one version.

    A referenced version that is absent is ``AP-005``: DS-001 proved it existed
    when the dataset was written, so its absence means the store lost it, and
    that is what ``AP-005`` says - a guard fired where a rule should already
    have been satisfied.
    """
    wanted = sorted(
        {(item.blueprint.agent_id, item.blueprint.version) for item in datasets},
        key=lambda ref: (ref[0], semver_key(ref[1])),
    )
    found: list[Blueprint] = []
    absent: list[RuleError] = []
    for agent_id, version in wanted:
        blueprint = context.store.get_blueprint(agent_id, version)
        if blueprint is None:
            absent.append(
                boundary(
                    AP_STORE_REFUSED,
                    field_pointer("agent_id"),
                    f"blueprint {agent_id}@{version} is referenced by an exported dataset "
                    "and is not in this store.",
                    agent_id=agent_id,
                    version=version,
                )
            )
            continue
        found.append(blueprint)
    return found, absent


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


class _BundleResolver:
    """This store's resolver, plus the bundle's own blueprints and dataset ids.

    A ``Resolver`` (ruling R-11's two-method Protocol) rather than anything
    cleverer, and it exists to make one sentence in the module docstring true:
    **nothing is written until all of it passes.**

    Without it the import has to publish the bundle's blueprints before it can
    validate the bundle's datasets, because DS-001 asks whether the dataset's
    blueprint is published *in this store*. A bundle whose datasets then fail
    leaves a published blueprint version behind that BP-016 has made immutable
    forever - and a failed import that permanently changed the store is a worse
    failure than the one it was reporting.

    It is not a *different* answer to the two lookups; it is the answer they
    will give once the import finishes, which is the question worth asking
    before writing. The store still wins on a *conflict*, and there is no case
    where it can: blueprint validation runs against the plain store resolver
    first, so a bundle version that differs from a published one fires BP-016
    and the import stops before this resolver is ever consulted.

    ``dataset_exists`` stages the bundle's dataset ids for DS-031, so a bundle
    that exports a superseding pair imports into a fresh store. Without it the
    later dataset would fail on a lineage the same bundle supplies, which is a
    rejection nobody could act on.
    """

    def __init__(
        self,
        base: Resolver,
        blueprints: Sequence[Mapping[str, Any]],
        dataset_ids: frozenset[str],
    ) -> None:
        self._base = base
        self._staged = {
            (str(document.get("agent_id")), str(document.get("version"))): document
            for document in blueprints
        }
        self._dataset_ids = dataset_ids

    def get_published_blueprint(self, agent_id: str, version: str) -> Mapping[str, Any] | None:
        stored = self._base.get_published_blueprint(agent_id, version)
        return stored if stored is not None else self._staged.get((agent_id, version))

    def dataset_exists(self, dataset_id: str) -> bool:
        return dataset_id in self._dataset_ids or self._base.dataset_exists(dataset_id)


def import_bundle(context: ServiceContext, payload: object) -> Reply:
    """Validate a whole bundle against this store, then store it. Six steps.

    1. **Read the bundle** and check that it *is* one - ``format`` and
       ``format_version`` - which is ``AP-001`` rather than a cascade of
       ``DS-*`` findings against a file that was never a bundle.
    2. **Validate every blueprint** through the full ``BP-*`` catalogue, against
       the plain store resolver. This is where BP-016 fires if the receiving
       store already publishes that version with different content, which is
       the normal shape of "two people edited 1.0.0" and the right answer to it.
    3. **Validate every dataset** through the full ``DS-*`` catalogue, against
       :class:`_BundleResolver` - the store *plus* this bundle - so DS-001 and
       DS-031 answer the question that matters: will this be valid once the
       import finishes?
    4. **Refuse the whole bundle** on any error-severity finding, having written
       nothing.
    5. **Publish the blueprints.** A byte-identical re-publish is a no-op
       (ruling R-29), so re-importing a bundle into a store that already has its
       blueprint is not a BP-016 failure.
    6. **Store the datasets**, each as a new version of its lineage.

    Warning-severity findings (DS-007, DS-027, DS-032, BP-019) do not block:
    they ride back as ``warnings`` on the success envelope (ruling R-13), with
    their pointers prefixed by which document in the bundle they came from.
    """
    document = read_document("bundle", payload)
    if not document.ok:
        return failure(list(document.findings))
    shape = _bundle_shape(document.value)
    if shape:
        return failure(shape)

    blueprints, findings = _validated_blueprints(context.resolver, document.value["blueprints"])
    datasets, dataset_findings = _validated_datasets(
        _BundleResolver(
            context.resolver,
            document.value["blueprints"],
            frozenset(str(item.get("id")) for item in document.value["datasets"]),
        ),
        document.value["datasets"],
    )
    findings = findings + dataset_findings
    if blocking(findings):
        return failure(findings)

    stored, write_findings = _write_bundle(context.store, blueprints, datasets)
    if write_findings:
        return failure(findings + write_findings)
    return success("imported", stored, warnings_from(findings))


def _bundle_shape(bundle: Mapping[str, Any]) -> list[RuleError]:
    """``AP-001`` findings for anything that is not a bundle of this format.

    Checked before any catalogue rule runs, because a caller who passed the
    wrong object should be told *that*, once, rather than handed the
    catalogue's opinion of a document that was never a dataset. Every finding
    points at the key that is wrong.
    """
    findings: list[RuleError] = [
        boundary(
            AP_ARGUMENT,
            field_pointer("bundle", key),
            f"bundle {key} must be {expected!r}.",
            expected=expected,
            given=bundle.get(key),
        )
        for key, expected in (("format", BUNDLE_FORMAT), ("format_version", BUNDLE_FORMAT_VERSION))
        if bundle.get(key) != expected
    ]
    findings.extend(
        boundary(
            AP_ARGUMENT,
            field_pointer("bundle", key),
            f"bundle {key} must be an array of objects.",
            given_type=type(bundle.get(key)).__name__,
        )
        for key in ("blueprints", "datasets")
        if not _is_object_array(bundle.get(key))
    )
    return findings


def _is_object_array(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, Mapping) for item in value)


def _validated_blueprints(
    resolver: Resolver, documents: Sequence[Mapping[str, Any]]
) -> tuple[list[Blueprint], list[RuleError]]:
    """Every blueprint in the bundle, validated then parsed (ruling R-23).

    ``status`` is normalised to ``published`` before validating, exactly as
    ``blueprint_upsert`` does with ``publish=True``: a bundle carries a
    blueprint so datasets can reference it, and DS-001 requires the referenced
    version to be *published*. A draft in a bundle would import and then fail
    every dataset in the same bundle.
    """
    findings: list[RuleError] = []
    parsed: list[Blueprint] = []
    for index, document in enumerate(documents):
        normalised = {**document, "status": STATUS_PUBLISHED}
        findings.extend(_at(validate_blueprint(normalised, resolver).errors, "blueprints", index))
        model, shape = parse(Blueprint, normalised)
        findings.extend(_at(shape, "blueprints", index))
        if model is not None:
            parsed.append(model)
    return parsed, findings


def _validated_datasets(
    resolver: Resolver, documents: Sequence[Mapping[str, Any]]
) -> tuple[list[Dataset], list[RuleError]]:
    """Every dataset in the bundle, validated then parsed, against ``resolver``.

    Which is the point of the milestone gate. DS-001 and DS-031 are existence
    checks through the resolver, so a dataset is being asked a question its
    source store already answered - and can answer differently. A dataset whose
    ``supersedes`` names a dataset that is neither in this bundle nor in this
    store fails DS-031 here, and nowhere else.

    ``validated_at`` is **not** re-stamped: the document carries the timestamp
    the source store's validator wrote, and that is a fact about the document
    rather than about this import. Every other field is likewise the exported
    one, including ``archived`` - which ``put_dataset`` honours on a first
    version (ruling R-34), so an archived dataset arrives archived.
    """
    findings: list[RuleError] = []
    parsed: list[Dataset] = []
    for index, document in enumerate(documents):
        findings.extend(_at(validate_dataset(document, resolver).errors, "datasets", index))
        model, shape = parse(Dataset, document)
        findings.extend(_at(shape, "datasets", index))
        if model is not None:
            parsed.append(model)
    return parsed, findings


def _at(findings: Sequence[RuleError], key: str, index: int) -> list[RuleError]:
    """``findings`` re-pointed into the bundle, so a pointer names which document.

    Without this, every finding in a five-dataset bundle points at
    ``/provenance/title`` and the caller has to guess which dataset. The rule
    id, the severity, the section and the context are untouched - only the
    pointer gains the ``/datasets/2`` prefix, which is what RFC 6901 is for.
    """
    prefix = f"/{key}/{index}"
    return [
        finding.model_copy(update={"pointer": f"{prefix}{finding.pointer}"}) for finding in findings
    ]


def _write_bundle(
    store: Store, blueprints: Sequence[Blueprint], datasets: Sequence[Dataset]
) -> tuple[dict[str, Any], list[RuleError]]:
    """Steps 5 and 6: publish the blueprints, then write the datasets.

    Blueprints first, because a store enforcing contracts section 7's foreign
    key - both SQL dialects do - rejects a dataset whose blueprint is not there
    yet. Mongo has no foreign keys, so on that backend the order is merely
    correct rather than required, and doing it the same way on all three is what
    keeps this one function rather than three.

    Both adapter guards are translated. ``PublishedVersionImmutableError``
    becomes **BP-016**, for the reason ``blueprint_upsert`` maps it that way: it
    is the rule the condition belongs to, and a caller must not have to learn a
    second vocabulary for the same problem. It is *unreachable* from here in
    practice, because step 2 already fired BP-016 through the resolver - kept
    because "unreachable because another step checked first" is a claim about
    two things staying in agreement, and this is the branch that stops a
    disagreement from destroying a published version.

    This is the **one dataset write** on the import path, in its own function,
    because `tests/unit/test_runtime_is_read_only.py` enumerates every
    ``put_dataset`` call site in `src/` by name.
    """
    findings: list[RuleError] = []
    published: list[Blueprint] = []
    for index, blueprint in enumerate(blueprints):
        try:
            published.append(store.put_blueprint(blueprint, publish=True))
        except PublishedVersionImmutableError as exc:
            findings.extend(_at([_immutable(blueprint, exc)], "blueprints", index))
        except StoreError as exc:
            findings.extend(_at([_refused("blueprint", exc)], "blueprints", index))
    written: list[Dataset] = []
    for index, dataset in enumerate(datasets):
        try:
            written.append(store.put_dataset(dataset))
        except (RecordNotFoundError, StoreError) as exc:
            findings.extend(_at([_refused("bundle", exc)], "datasets", index))
    imported = {
        "blueprints": [{"agent_id": item.agent_id, "version": item.version} for item in published],
        "datasets": [{"id": str(item.id), "version": item.version} for item in written],
    }
    return imported, findings


def _immutable(blueprint: Blueprint, exc: Exception) -> RuleError:
    """BP-016 for a published version the bundle would have changed."""
    return boundary(
        "BP-016",
        field_pointer("version"),
        str(exc),
        agent_id=blueprint.agent_id,
        version=blueprint.version,
    )


def _refused(field: str, exc: Exception) -> RuleError:
    """``AP-005``: a store guard fired where a rule should already have been satisfied."""
    return boundary(AP_STORE_REFUSED, field_pointer(field), str(exc))
