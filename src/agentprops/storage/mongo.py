"""The Mongo adapter: the same ``Store`` Protocol, a document store underneath.

`docs/contracts.md` section 8 is the collection list, and it is short because
the logical model does not change: ``blueprints`` keyed by
``{agent_id, version}``, ``datasets`` keyed by ``{id, version}`` with a compound
index on ``{agent_id, bp_version, archived}`` and an index over ``labels``,
``skeletons`` keyed by ``id``, ``runs`` keyed by ``id``, and ``run_steps`` with
unique compound indexes on ``{run_id, node_id, iteration}`` (the idempotency
key) and on ``{run_id, seq}`` (ruling R-37).

This module shares **nothing** with `sql.py` except `common.py`, which is
deliberate on both counts. Nothing, because a document store is not a dialect
of SQL and pretending otherwise produces the worst of both. `common.py`,
because the ``q`` case fold, the semver ordering and every row-to-model mapper
are decisions the conformance suite requires two adapters to make *identically*
- see that module's docstring for why a second copy of the fold would be the
divergence ruling R-36 exists to prevent.

The four things Mongo makes genuinely different
-----------------------------------------------

**1. Object keys can contain a dot, and Mongo cannot address them.** Two of
ruling R-06's five section ids are ``nodes.core`` and ``nodes.branches``, and
``Skeleton.parts`` is keyed by section id - so a filled skeleton has object keys
with dots in them. That is legal JSON and a legal SQL JSON document, but
``parts.nodes.core`` reads as two levels of nesting in Mongo, and older servers
rejected such a key outright. The same hazard applies to every *authored*
document this service stores: a fixture's ``output``, an entity's ``state`` and
a step's ``served`` are arbitrary caller JSON, and a key there may hold a dot,
a ``$`` or both.

So every opaque JSON value is stored through :func:`encode_keys`, which
percent-escapes ``%``, ``.`` and ``$`` in object keys and is provably
injective (see its docstring for the argument and
`tests/unit/test_mongo_key_codec.py` for the property test). The promoted,
*queryable* fields - ``agent_id``, ``title``, ``created_at``, ``labels`` and the
rest - are stored natively, exactly as the SQL adapter promotes them into
columns, and ``labels`` is queried through the same escape so a label dimension
containing a dot is filterable rather than unreachable. M3 put a dotted key in
the conformance suite precisely so this landed here rather than surprising M8.

**2. There are no column defaults, so the clock is read from the server.**
Ruling R-09 allows a timestamp from "a DB column default, the client, authored
content in the document, or ``Seeded.timestamp()``", and R-39(d) reads the first
as "a database clock, whether a column default or an explicit ``func.now()``".
Mongo has neither, so :meth:`MongoStore._server_now` asks the server for its
own clock with the ``hello`` command, whose reply carries ``localTime``.
``hello`` is the connection handshake: it needs no privileges and no minimum
server version worth stating. There is still no clock read in Python anywhere in
this package, which is what `tests/unit/test_layering.py` asserts.

**Not** an aggregation-pipeline update with ``$$NOW``, which would have saved a
round trip and was rejected: in a pipeline update every literal is an
*expression*, so a stored value that happens to be the string ``"$total"``
would be silently resolved as a field path. A codec that escapes keys and a
pipeline that reinterprets values would have left exactly one hole, in the one
place - authored fixture content - where the values are least predictable.

**3. There are no foreign keys.** Contracts section 8 says so and exempts
referential behaviour from the conformance suite for that reason: "the
conformance suite tests referential behaviour through the service layer, not the
storage layer, so this asymmetry does not change the tests". The suite writes a
blueprint before a dataset because that is the honest order, not because a
constraint forces it.

**4. A duplicate key is a different exception.** :class:`DuplicateKeyError`
where SQL raises ``IntegrityError``. Every retry in this module is the same
bounded-retry-on-duplicate-key shape ``put_dataset`` and ``upsert_step`` use in
`sql.py`, with the same budgets from `common.py`, because the guard is a unique
index on all three backends - which is what makes the pattern portable and what
rulings R-37 and R-39 both turn on.

What this module does not do
----------------------------

It does not validate (ruling R-23), does not read a *Python* clock (ruling
R-09), does not mint an id (ruling R-10) and issues no delete of any kind
(ground rule 6 - and `tests/unit/test_storage_no_delete.py` reads the AST of
this file to say so, with ``delete_one``, ``delete_many``, ``drop`` and
``find_one_and_delete`` in its list because of this adapter).

One property holds across every method, as it does in `sql.py`: **a write
returns exactly what the matching read returns.** Every writer builds its return
value from the stored record rather than from its argument.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

from agentprops.models import (
    Blueprint,
    BlueprintSummary,
    Dataset,
    DatasetQuery,
    DatasetSummary,
    Run,
    RunQuery,
    RunSummary,
    Skeleton,
    StepRecord,
    StoreCounts,
    StoreHealth,
    Warning,
)
from agentprops.storage.base import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    PublishedVersionImmutableError,
    RecordNotFoundError,
    Store,
    StoreError,
)
from agentprops.storage.common import (
    FIRST_WRITE_ATTEMPTS,
    SEQ_ALLOCATION_ATTEMPTS,
    VERSION_ALLOCATION_ATTEMPTS,
    blueprint_summary,
    canonical,
    dataset_summary,
    parse_uuid,
    q_matches,
    require_uuid,
    run_from,
    run_summary,
    semver_key,
    skeleton_from,
    slice_page,
    step_from,
    stored_document,
)

__all__ = [
    "DEFAULT_DATABASE",
    "MongoStore",
    "decode_keys",
    "encode_keys",
]

#: The database a URL without a path lands in.
DEFAULT_DATABASE: Final = "agentprops"

#: How long :meth:`MongoStore.health` waits for a server before answering
#: ``healthy: false``. Short, because ``store_status`` exists to answer "is the
#: backend there" and a caller waiting thirty seconds for "no" has been told
#: nothing useful. The driver default is 30 s.
DEFAULT_SERVER_SELECTION_TIMEOUT_MS: Final = 5000

#: The collection names, so a typo is a name error rather than a silently empty
#: collection. Mongo creates a collection on first write, which means a
#: misspelled name in a *read* returns no documents and a misspelled name in a
#: *write* succeeds - the one class of bug a document store makes easier than a
#: SQL one.
BLUEPRINTS: Final = "blueprints"
DATASETS: Final = "datasets"
SKELETONS: Final = "skeletons"
RUNS: Final = "runs"
RUN_STEPS: Final = "run_steps"

#: Which fields of each record hold **opaque JSON** and therefore pass through
#: the key codec. Everything else is a promoted, queryable field stored
#: natively - the document-store equivalent of contracts section 7's "provenance
#: promoted out of the document so find can filter and sort without parsing
#: JSON".
#:
#: ``labels`` is in the list *and* queryable, which is the one case that needs
#: both: the codec is applied to its keys, and :meth:`MongoStore.find_datasets`
#: escapes the queried dimension the same way, so a label dimension containing a
#: dot is filterable rather than unreachable.
_ENCODED_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    BLUEPRINTS: ("document",),
    DATASETS: ("labels", "document"),
    SKELETONS: ("labels", "manifest", "parts"),
    RUNS: ("model", "outcome", "warnings", "external_refs"),
    RUN_STEPS: ("served", "actual"),
}

#: The three characters an object key cannot carry into Mongo unescaped, and
#: their escapes. ``%`` is first on the way in and last on the way out, which is
#: what makes the encoding reversible - see :func:`encode_keys`.
_ESCAPES: Final[tuple[tuple[str, str], ...]] = (("%", "%25"), (".", "%2E"), ("$", "%24"))


# --------------------------------------------------------------------------
# the key codec
# --------------------------------------------------------------------------


def _escape_key(key: str) -> str:
    for character, escape in _ESCAPES:
        key = key.replace(character, escape)
    return key


def _unescape_key(key: str) -> str:
    for character, escape in reversed(_ESCAPES):
        key = key.replace(escape, character)
    return key


def encode_keys(value: Any) -> Any:
    """``value`` with every object key escaped for Mongo. Values are untouched.

    Three characters are escaped - ``%`` to ``%25``, ``.`` to ``%2E`` and ``$``
    to ``%24`` - and the order is what makes it reversible. ``%`` goes first, so
    every literal ``%`` in the input becomes ``%25`` before the other two
    introduce any ``%`` of their own; decoding therefore undoes ``%2E`` and
    ``%24`` first and ``%25`` last, and a literal ``%2E`` in a key survives as
    ``%252E``, which contains no ``%2E`` substring to be mistaken for one.
    `tests/unit/test_mongo_key_codec.py` carries that argument as a property
    test over generated keys, because "provably injective" written in a
    docstring is worth nothing next to a test of the losing side.

    Why escape at all: two of ruling R-06's five section ids contain a dot and
    ``Skeleton.parts`` is keyed by section id, so ``parts`` has dotted keys by
    construction; and every authored document this service stores - a fixture's
    ``output``, an entity's ``state``, a step's ``served`` - is arbitrary caller
    JSON whose keys this adapter does not get to constrain. ``$`` is escaped for
    the same reason: a leading ``$`` makes a key look like an operator.

    Only *keys* are transformed. A value that looks like an escape is left
    exactly as it was, which is why this codec is safe to apply to authored
    content: nothing a caller wrote comes back different.
    """
    if isinstance(value, Mapping):
        return {_escape_key(str(key)): encode_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode_keys(item) for item in value]
    return value


def decode_keys(value: Any) -> Any:
    """The inverse of :func:`encode_keys`."""
    if isinstance(value, Mapping):
        return {_unescape_key(str(key)): decode_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_keys(item) for item in value]
    return value


def _encoded(collection: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """``record`` with its opaque JSON fields key-encoded, ready to store."""
    fields = _ENCODED_FIELDS[collection]
    return {key: encode_keys(value) if key in fields else value for key, value in record.items()}


def _decoded(collection: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """``record`` as read back, with its opaque JSON fields key-decoded.

    ``_id`` is dropped: it is a projection of the natural key, which is carried
    in promoted fields as well, so nothing above this layer ever needs it and
    every mapper in `common.py` would have to learn to ignore it.
    """
    fields = _ENCODED_FIELDS[collection]
    return {
        key: decode_keys(value) if key in fields else value
        for key, value in record.items()
        if key != "_id"
    }


# --------------------------------------------------------------------------
# the natural keys, each built in exactly one place
# --------------------------------------------------------------------------
#
# ``_id`` is set explicitly rather than left to the driver, for two reasons.
# A driver-generated ``ObjectId`` embeds a client clock and a per-process random
# value, which ground rule 9 forbids in this package; and the natural key is
# what contracts section 8 says each collection is "keyed by", so making it the
# ``_id`` gets the uniqueness constraint from the server for free rather than
# from a second index that could be missing.
#
# A subdocument ``_id`` matches by exact field order, so each one is built by a
# single function and never spelled inline.


def _blueprint_key(agent_id: str, version: str) -> dict[str, Any]:
    return {"agent_id": agent_id, "version": version}


def _dataset_key(identifier: uuid.UUID, version: int) -> dict[str, Any]:
    return {"id": identifier, "version": version}


def _step_key(run_id: str, node_id: str, iteration: int) -> dict[str, Any]:
    return {"run_id": run_id, "node_id": node_id, "iteration": iteration}


class MongoStore:
    """The ``Store`` for MongoDB.

    Constructed over a :class:`~pymongo.MongoClient`, so the caller owns the
    connection string, the pool and the lifecycle - the same division
    :class:`~agentprops.storage.sql.SqlStore` makes over an ``Engine``.

    Two client options are **not** optional and :meth:`from_url` sets both.
    ``tz_aware=True``, because pymongo otherwise returns naive datetimes and
    every timestamp would compare unequal to the aware value the model carries -
    the same trap ``UtcDateTime`` exists for on SQLite.
    ``uuidRepresentation="standard"``, because pymongo 4's default is
    ``unspecified``, which *raises* on encoding a ``uuid.UUID``; standard stores
    it as BSON binary subtype 4, whose sort order is the byte order both SQL
    dialects use for a ``UUID`` column, so ``find_datasets``'s
    ``(created_at, id)`` tie-break is the same order on all three backends.
    """

    def __init__(
        self, client: MongoClient[dict[str, Any]], database: str = DEFAULT_DATABASE
    ) -> None:
        self._client = client
        self._db: Database[dict[str, Any]] = client[database]

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        database: str | None = None,
        server_selection_timeout_ms: int = DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
    ) -> MongoStore:
        """A store over a fresh client for ``url``. Indexes not created.

        ``database`` defaults to the URL's path when it names one and to
        :data:`DEFAULT_DATABASE` otherwise, which is what makes
        ``mongodb://localhost:27117/`` a complete argument.
        """
        client: MongoClient[dict[str, Any]] = MongoClient(
            url,
            tz_aware=True,
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=server_selection_timeout_ms,
        )
        chosen = database or _database_in(url) or DEFAULT_DATABASE
        return cls(client, chosen)

    @property
    def backend(self) -> str:
        return "mongo"

    @property
    def database(self) -> Database[dict[str, Any]]:
        return self._db

    def close(self) -> None:
        """Release the connection pool. Not on the Protocol - a lifecycle
        concern, and the SQL equivalent is ``SqlStore.dispose()``."""
        self._client.close()

    def create_schema(self) -> None:
        """Create every index contracts section 8 declares. Idempotent.

        A *method* rather than the module-level ``create_schema(engine)`` the
        SQL side has, because there is no schema to create without a
        connection: Mongo makes a collection on first write, so the only
        structure worth declaring is the indexes, and they are declared against
        this store's database.

        Two of them are the constraints rulings R-37 and R-39 turn on, and they
        are unique for that reason: ``run_steps._id`` is the
        ``{run_id, node_id, iteration}`` idempotency key, and
        ``{run_id, seq}`` is what makes ``seq`` allocation sound rather than
        hopeful. ``datasets._id`` is ``{id, version}``, the same guard
        ``put_dataset``'s version allocation relies on in SQL.

        Two deviations from contracts section 8, both recorded in
        `DECISIONS.md`:

        - the index over ``labels`` is a **wildcard** index on ``labels.$**``,
          not a multikey one. "Multikey" describes an index over an array-valued
          field; ``labels`` is an object, and a plain index on it would serve
          only whole-object equality. A wildcard index is what actually
          accelerates ``labels.tier == "regional"``, which is the query
          ``find_datasets`` issues.
        - **no text index** on ``{title, intent}``. Ruling R-36 makes ``q``
          substring matching by contract, a text index does stemming, and
          R-39(a) puts the fold in Python on every backend - so the index would
          be dead in exactly the way the ``datasets_search`` GIN index was.
        """
        self._datasets.create_index(
            [("agent_id", ASCENDING), ("bp_version", ASCENDING), ("archived", ASCENDING)],
            name="datasets_lookup",
        )
        self._datasets.create_index([("author_handle", ASCENDING)], name="datasets_author")
        self._datasets.create_index([("labels.$**", ASCENDING)], name="datasets_labels_wildcard")
        self._datasets.create_index(
            [("id", ASCENDING), ("version", DESCENDING)], name="datasets_lineage"
        )
        self._runs.create_index(
            [("agent_id", ASCENDING), ("run_class", ASCENDING), ("started_at", DESCENDING)],
            name="runs_lookup",
        )
        self._steps.create_index(
            [("run_id", ASCENDING), ("seq", ASCENDING)], name="run_steps_seq_key", unique=True
        )
        self._steps.create_index([("run_id", ASCENDING)], name="run_steps_run")

    # ---------------------------------------------------------------- blueprints

    def put_blueprint(self, bp: Blueprint, publish: bool) -> Blueprint:
        """See :meth:`agentprops.storage.base.Store.put_blueprint`.

        The same select-then-insert-or-update as `sql.py`, with
        :class:`DuplicateKeyError` where that adapter catches ``IntegrityError``.
        The insert branch retries once so two concurrent *first* writes of one
        ``{agent_id, version}`` converge on the update branch, where R-29's
        identical re-publish is a no-op success and a differing one is BP-016.

        That retry is load-bearing here and not only for tidiness: without it,
        the second of two concurrent publishes of a *differing* document would
        take the insert branch, and an upsert would have overwritten a published
        version that BP-016 makes immutable. The read said "absent" for both
        writers; the unique ``_id`` is what makes exactly one of them right.
        """
        status = STATUS_PUBLISHED if publish else STATUS_DRAFT
        document = stored_document(bp, status=status)
        key = _blueprint_key(bp.agent_id, bp.version)
        for attempt in range(FIRST_WRITE_ATTEMPTS):
            existing = self._blueprint_doc(bp.agent_id, bp.version)
            if existing is None:
                try:
                    self._blueprints.insert_one(
                        _encoded(
                            BLUEPRINTS,
                            {
                                "_id": key,
                                "agent_id": bp.agent_id,
                                "version": bp.version,
                                "status": status,
                                "document": document,
                                "created_at": self._server_now(),
                            },
                        )
                    )
                except DuplicateKeyError:
                    if attempt == FIRST_WRITE_ATTEMPTS - 1:
                        raise
                    continue
                return Blueprint.model_validate(document)

            stored: dict[str, Any] = decode_keys(existing["document"])
            if existing["status"] == STATUS_PUBLISHED:
                if canonical(stored) == canonical(document):
                    # Ruling R-29: a byte-identical re-publish is a no-op
                    # success. Nothing changes, because the documents are
                    # identical, so immutability is fully preserved - and
                    # `dataset_import` and a CI pipeline that publishes on every
                    # run both become idempotent.
                    return Blueprint.model_validate(stored)
                raise PublishedVersionImmutableError(
                    f"blueprint {bp.agent_id}@{bp.version} is published and differs (BP-016)"
                )
            self._blueprints.update_one(
                {"_id": key},
                {"$set": _encoded(BLUEPRINTS, {"status": status, "document": document})},
            )
            return Blueprint.model_validate(document)
        raise StoreError(  # pragma: no cover - the loop returns or re-raises
            f"could not write blueprint {bp.agent_id}@{bp.version}"
        )

    def get_blueprint(self, agent_id: str, version: str | None) -> Blueprint | None:
        """See :meth:`agentprops.storage.base.Store.get_blueprint`."""
        if version is not None:
            found = self._blueprints.find_one({"_id": _blueprint_key(agent_id, version)})
            if found is None:
                return None
            exact: dict[str, Any] = decode_keys(found["document"])
            return Blueprint.model_validate(exact)

        published = list(self._blueprints.find({"agent_id": agent_id, "status": STATUS_PUBLISHED}))
        if not published:
            return None
        newest = max(published, key=lambda candidate: semver_key(str(candidate["version"])))
        latest: dict[str, Any] = decode_keys(newest["document"])
        return Blueprint.model_validate(latest)

    def list_blueprints(self, status: str | None) -> list[BlueprintSummary]:
        """See :meth:`agentprops.storage.base.Store.list_blueprints`.

        Ordered by ``(agent_id, semver)`` in Python, through the same
        :func:`~agentprops.storage.common.semver_key` the SQL adapter uses -
        which is the point of that function existing: a sort here would put
        ``1.10.0`` before ``1.9.0`` and the conformance suite would find the
        divergence rather than the bug.
        """
        query = {} if status is None else {"status": status}
        rows = [_decoded(BLUEPRINTS, row) for row in self._blueprints.find(query)]
        ordered = sorted(
            rows, key=lambda row: (str(row["agent_id"]), semver_key(str(row["version"])))
        )
        return [blueprint_summary(row) for row in ordered]

    # ------------------------------------------------------------------ datasets

    def put_dataset(self, ds: Dataset) -> Dataset:
        """See :meth:`agentprops.storage.base.Store.put_dataset`.

        Version allocation is ``max(version) + 1`` read outside the insert, with
        the unique ``_id`` of ``{id, version}`` as the guard rather than a lock -
        the same shape `sql.py` uses, and the reason M3 chose that shape: two
        writers that read the same maximum both attempt the same version, one of
        them loses on the unique index, and the loser re-reads and takes the
        next one. Monotonic under concurrency using only a mechanism all three
        backends have.

        A :class:`DuplicateKeyError` that is *not* that race is re-raised
        immediately rather than retried, so a real cause is not buried under
        eight attempts. Mongo has no foreign keys, so the specific case
        `sql.py` names - a missing blueprint tripping ``datasets_blueprint_fkey``
        - cannot arise here; the branch is kept because the *class* of error can
        (a duplicate on some other unique index), and collapsing it into the
        retry is how a real fault becomes an eight-attempt timeout.

        ``archived`` is **inherited from the lineage**, not taken from
        ``ds.archived`` (ruling R-34).
        """
        base = stored_document(ds)
        inherited = self._lineage_archived(ds.id)
        archived = ds.archived if inherited is None else inherited
        for _ in range(VERSION_ALLOCATION_ATTEMPTS):
            version = self._next_dataset_version(ds.id)
            document = {**base, "version": version, "archived": archived}
            record = self._dataset_record(ds, document, version, archived)
            try:
                self._datasets.insert_one(_encoded(DATASETS, record))
            except DuplicateKeyError:
                if self._dataset_version_exists(ds.id, version):
                    continue
                raise
            return Dataset.model_validate(document)
        raise StoreError(f"could not allocate a version for dataset {ds.id} after repeated races")

    def get_dataset(self, dataset_id: str, version: int | None) -> Dataset | None:
        """See :meth:`agentprops.storage.base.Store.get_dataset`."""
        identifier = parse_uuid(dataset_id)
        if identifier is None:
            return None
        if version is not None:
            found = self._datasets.find_one({"_id": _dataset_key(identifier, version)})
        else:
            found = self._datasets.find_one({"id": identifier}, sort=[("version", DESCENDING)])
        if found is None:
            return None
        document: dict[str, Any] = decode_keys(found["document"])
        return Dataset.model_validate(document)

    def find_datasets(self, q: DatasetQuery) -> list[DatasetSummary]:
        """See :meth:`agentprops.storage.base.Store.find_datasets`.

        **One row per dataset lineage**, at its latest version, excluding
        archived (rulings R-38 and R-34), ordered by ``(created_at, id)``. The
        grain is produced by an aggregation - sort by ``(id, version desc)``,
        group taking the first of each - where SQL uses a correlated
        ``max(version)`` subquery. Different plan, identical rows, which is
        exactly the licence contracts section 7 grants and the conformance suite
        checks.

        **The order of the stages is the whole correctness of this method, and
        the first version got it wrong.** Every filter runs *after*
        ``$replaceRoot``, so what is filtered is the lineage's latest version.
        The filters were in a ``$match`` before the ``$group`` at first, which
        reads perfectly and answers a different question: the group then sees
        only *matching* versions, and ``$first`` returns the latest version
        **that matched** rather than the latest version of the lineage. For a
        lineage whose promoted fields differ between versions those are
        different rows, and Mongo returned a non-latest one - contradicting
        R-38 literally while agreeing with SQL on every fixture that writes the
        same document twice.

        Why it matters rather than merely differs: ``find_datasets`` is the
        review surface M9 builds on (PRD 5.7, "a stranger has to judge
        relevance without opening anything"), so a search for
        ``tier=regional`` that surfaces a dataset which is now ``national`` is
        the surface lying rather than a near miss. And it is reachable through
        a shipped feature - ``dataset_import`` writes a bundle's document as a
        new version of an existing lineage id, so a bundle whose labels,
        blueprint version or author differ from the store's copy produces
        exactly such a lineage.
        ``test_find_filters_the_latest_version_and_not_whichever_version_matched``
        is the conformance test, and it failed on this backend and passed on
        both SQL dialects when it was written.

        ``archived`` moved with the rest, even though ruling R-34 makes it
        lineage-level and pre-filtering it would therefore be equivalent
        *today*. An optimisation whose correctness depends on an invariant
        enforced two modules away is the shape of thing that survives the
        invariant, and SQL does not pre-filter it either.

        The cost, stated: the group now runs over the whole collection rather
        than over the filtered subset. ``datasets_lineage`` (``{id, version
        desc}``) serves the sort that feeds it, and the volumes are a local
        authoring instance's - the same trade ruling R-39(a) accepted for the
        ``q`` filter and M6 accepted for ``_by_labels``. Identical results
        first; contracts section 7 licenses the different plan.

        The ``q`` filter is applied in Python, after the query filters and the
        ordering, through the same
        :func:`~agentprops.storage.common.q_matches` both adapters use.
        ``$regex`` with ``i`` is a *third* answer to case folding - not SQLite's
        and not Postgres's - so using it here would be the divergence ruling
        R-36 forbids, and it would show up on exactly one fixture. Pagination
        follows the filter, for the same reason the archive exclusion precedes
        it: a page sliced before the filter returns short.
        """
        pipeline: list[dict[str, Any]] = [
            {"$sort": {"id": ASCENDING, "version": DESCENDING}},
            {"$group": {"_id": "$id", "latest": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$latest"}},
            {"$match": self._dataset_match(q)},
            {"$sort": {"created_at": ASCENDING, "id": ASCENDING}},
        ]
        if q.q is None:
            if q.offset:
                pipeline.append({"$skip": q.offset})
            if q.limit is not None:
                pipeline.append({"$limit": q.limit})
        rows = [_decoded(DATASETS, row) for row in self._datasets.aggregate(pipeline)]
        if q.q is None:
            return [dataset_summary(row) for row in rows]
        matched = [row for row in rows if q_matches(q.q, row["title"], row["intent"])]
        return [dataset_summary(row) for row in slice_page(matched, q.limit, q.offset)]

    def set_archived(self, dataset_id: str, archived: bool) -> DatasetSummary:
        """See :meth:`agentprops.storage.base.Store.set_archived`.

        Every version of the lineage is flipped, in the promoted field **and**
        inside the stored document, in one ``update_many``. Two consequences,
        both wanted: an exported dataset carries its true archive state rather
        than a stale ``false``, and nothing anywhere has to know which of the two
        copies wins.

        The document path is built through the key escape like every other one,
        even though ``archived`` needs no escaping: a path assembled by hand is
        a path that stops being right the day the field it addresses is renamed
        to something with a dot in it.
        """
        identifier = parse_uuid(dataset_id)
        if identifier is None:
            raise RecordNotFoundError(f"no dataset {dataset_id!r}")
        result = self._datasets.update_many(
            {"id": identifier},
            {"$set": {"archived": archived, f"document.{_escape_key('archived')}": archived}},
        )
        if result.matched_count == 0:
            raise RecordNotFoundError(f"no dataset {dataset_id!r}")
        latest = self._datasets.find_one({"id": identifier}, sort=[("version", DESCENDING)])
        if latest is None:  # pragma: no cover - the rows were just updated
            raise RecordNotFoundError(f"no dataset {dataset_id!r}")
        return dataset_summary(_decoded(DATASETS, latest))

    # ----------------------------------------------------------------- skeletons

    def put_skeleton(self, sk: Skeleton) -> Skeleton:
        """See :meth:`agentprops.storage.base.Store.put_skeleton`.

        ``parts`` is where the key codec earns itself: it is keyed by section id
        and two of ruling R-06's five section ids contain a dot, so
        ``nodes.core`` is stored as ``nodes%2Ecore`` and read back as
        ``nodes.core``. Nothing above this layer knows, and nothing here reaches
        *into* ``parts`` - a re-fill replaces the whole object, which is what
        ruling R-48 accepts and what makes the opaque treatment sufficient
        rather than merely convenient.
        """
        record = {
            "agent_id": sk.agent_id,
            "bp_version": sk.bp_version,
            "labels": dict(sk.labels),
            "seed": sk.seed,
            "manifest": [section.model_dump(mode="json") for section in sk.manifest],
            "parts": sk.parts,
            "submitted_as": sk.submitted_as,
            "created_at": sk.created_at,
        }
        for attempt in range(FIRST_WRITE_ATTEMPTS):
            if not self._skeleton_exists(sk.id):
                try:
                    self._skeletons.insert_one(
                        _encoded(SKELETONS, {"_id": sk.id, "id": sk.id, **record})
                    )
                except DuplicateKeyError:
                    if attempt == FIRST_WRITE_ATTEMPTS - 1:
                        raise
                    continue
            else:
                self._skeletons.update_one({"_id": sk.id}, {"$set": _encoded(SKELETONS, record)})
            stored = self._skeletons.find_one({"_id": sk.id})
            if stored is None:  # pragma: no cover - the record was just written
                raise StoreError(f"skeleton {sk.id} vanished during write")
            return skeleton_from(_decoded(SKELETONS, stored))
        raise StoreError(  # pragma: no cover - the loop returns or re-raises
            f"could not write skeleton {sk.id}"
        )

    def get_skeleton(self, skeleton_id: str) -> Skeleton | None:
        """See :meth:`agentprops.storage.base.Store.get_skeleton`."""
        identifier = parse_uuid(skeleton_id)
        if identifier is None:
            return None
        found = self._skeletons.find_one({"_id": identifier})
        return None if found is None else skeleton_from(_decoded(SKELETONS, found))

    def mark_skeleton_submitted(self, skeleton_id: str, dataset_id: str) -> bool:
        """See :meth:`agentprops.storage.base.Store.mark_skeleton_submitted`.

        Ruling R-47's compare-and-set, and the **conditional update is what
        makes it one**: ``submitted_as: None`` is part of the filter, so the
        server decides, once, which of two concurrent statements matches a
        record. ``matched_count`` is the answer rather than a follow-up read,
        which would reintroduce the race one statement later.

        The existence check stays separate and still raises: "no such skeleton"
        is not a false answer to "did this call claim it".
        """
        identifier = parse_uuid(skeleton_id)
        if identifier is None:
            raise RecordNotFoundError(f"no skeleton {skeleton_id!r}")
        dataset = require_uuid(dataset_id, "dataset_id")
        if not self._skeleton_exists(identifier):
            raise RecordNotFoundError(f"no skeleton {skeleton_id!r}")
        claimed = self._skeletons.update_one(
            {"_id": identifier, "submitted_as": None}, {"$set": {"submitted_as": dataset}}
        )
        return bool(claimed.matched_count)

    # ---------------------------------------------------------------------- runs

    def put_run(self, run: Run) -> Run:
        """See :meth:`agentprops.storage.base.Store.put_run`.

        The insert branch retries once, as :meth:`put_blueprint` does, so two
        concurrent first writes of one run id converge on the update branch.
        Any ``steps`` on the model are written through :meth:`upsert_step`, so
        they inherit its idempotency and its ``seq`` allocation.
        """
        record = {
            "agent_id": run.agent_id,
            "dataset_id": run.pin.dataset_id,
            "dataset_ver": run.pin.dataset_version,
            "bp_version": run.pin.blueprint_version,
            "declared_bp_version": run.declared_blueprint_version,
            "run_class": run.run_class,
            "model": None if run.model is None else run.model.model_dump(mode="json"),
            "status": run.status,
            "outcome": run.outcome,
            "warnings": [warning.model_dump(mode="json") for warning in run.warnings],
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "external_refs": run.external_refs,
        }
        for attempt in range(FIRST_WRITE_ATTEMPTS):
            if not self._run_exists(run.id):
                try:
                    self._runs.insert_one(_encoded(RUNS, {"_id": run.id, "id": run.id, **record}))
                except DuplicateKeyError:
                    if attempt == FIRST_WRITE_ATTEMPTS - 1:
                        raise
                    continue
            else:
                self._runs.update_one({"_id": run.id}, {"$set": _encoded(RUNS, record)})
            break
        for step in run.steps:
            self.upsert_step(run.id, step)
        stored = self.get_run(run.id)
        if stored is None:  # pragma: no cover - the record was just written
            raise StoreError(f"run {run.id} vanished during write")
        return stored

    def get_run(self, run_id: str) -> Run | None:
        """See :meth:`agentprops.storage.base.Store.get_run`.

        ``path`` is rebuilt from ``run_steps`` by
        :func:`~agentprops.storage.common.run_from`, in the **total**
        ``(seq, node_id, iteration)`` order ruling R-37 requires. ``seq`` alone
        is unique, so the two extra keys never decide anything today; they are
        there because a partial order that happens to be stable on one backend
        is how a divergence gets discovered at the worst possible layer.
        """
        found = self._runs.find_one({"_id": run_id})
        if found is None:
            return None
        steps = [
            step_from(_decoded(RUN_STEPS, row))
            for row in self._steps.find({"run_id": run_id}).sort(
                [("seq", ASCENDING), ("node_id", ASCENDING), ("iteration", ASCENDING)]
            )
        ]
        return run_from(_decoded(RUNS, found), steps)

    def find_runs(self, q: RunQuery) -> list[RunSummary]:
        """See :meth:`agentprops.storage.base.Store.find_runs`.

        Newest first, tie-broken by id (ruling R-35). An unparseable
        ``dataset_id`` filter matches nothing rather than raising.
        ``outcome`` is projected away, which is ruling R-05's whole reason for
        ``RunSummary`` existing: it is "the ``runs`` columns minus ``outcome``",
        the one field large enough to be worth withholding from a list.
        """
        match: dict[str, Any] = {}
        if q.agent_id is not None:
            match["agent_id"] = q.agent_id
        if q.dataset_id is not None:
            identifier = parse_uuid(q.dataset_id)
            if identifier is None:
                return []
            match["dataset_id"] = identifier
        if q.run_class is not None:
            match["run_class"] = q.run_class
        if q.model is not None:
            match[f"model.{_escape_key('name')}"] = q.model
        cursor = self._runs.find(match, {"outcome": 0}).sort(
            [("started_at", DESCENDING), ("id", ASCENDING)]
        )
        if q.offset:
            cursor = cursor.skip(q.offset)
        if q.limit is not None:
            cursor = cursor.limit(q.limit)
        return [run_summary(_decoded(RUNS, row)) for row in cursor]

    def upsert_step(self, run_id: str, step: StepRecord) -> StepRecord:
        """See :meth:`agentprops.storage.base.Store.upsert_step`.

        Idempotent on ``(run_id, node_id, iteration)``, which is this
        collection's ``_id`` - so the guarantee is the server's rather than this
        method's, exactly as it is the primary key's in SQL.

        ``seq`` is allocated as ``max(seq) + 1`` within the run and any incoming
        ``StepRecord.seq`` is ignored, because ``seq`` is the store's record of
        the order steps were actually served in. **The unique ``{run_id, seq}``
        index is what makes that sound, not a transaction** (ruling R-37): a
        read outside the write takes no lock on any of the three backends, so
        the index rejects the duplicate and this retries.

        Three outcomes are distinguished after a :class:`DuplicateKeyError`,
        because collapsing them would bury a real error under eight retries:

        - the step key now exists - another writer served this same step first,
          so the next pass returns *its* record and this call is the no-op;
        - the ``(run_id, seq)`` pair is taken - the race, lost, so re-read and
          take the next number;
        - neither - re-raised as itself.

        ``fetched_at`` falls back to the **server's** clock when the model does
        not carry one, which is ruling R-09's first sanctioned source read the
        way a store with no column defaults has to read it. See
        :meth:`_server_now`.
        """
        for _ in range(SEQ_ALLOCATION_ATTEMPTS):
            existing = self._step_doc(run_id, step.node_id, step.iteration)
            if existing is not None:
                return step_from(_decoded(RUN_STEPS, existing))
            seq = self._next_step_seq(run_id)
            key = _step_key(run_id, step.node_id, step.iteration)
            record = {
                "_id": key,
                **key,
                "seq": seq,
                "served": step.served,
                "actual": step.actual,
                "fetched_at": step.fetched_at or self._server_now(),
                "recorded_at": step.recorded_at,
            }
            try:
                self._steps.insert_one(_encoded(RUN_STEPS, record))
            except DuplicateKeyError:
                if self._step_doc(run_id, step.node_id, step.iteration) is not None:
                    continue
                if self._step_seq_taken(run_id, seq):
                    continue
                raise
            written = self._step_doc(run_id, step.node_id, step.iteration)
            if written is None:  # pragma: no cover - the record was just written
                raise StoreError(f"step {run_id}/{step.node_id}/{step.iteration} vanished")
            return step_from(_decoded(RUN_STEPS, written))
        raise StoreError(f"could not allocate a seq for run {run_id} after repeated races")

    def set_run_warnings(self, run_id: str, warnings: Sequence[Warning]) -> list[Warning]:
        """See :meth:`agentprops.storage.base.Store.set_run_warnings`.

        **One statement, one field.** That is the whole method and it is the
        whole point: a ``$set`` of ``warnings`` cannot revert ``status``,
        ``outcome`` or ``finished_at``, which is what :meth:`put_run` with an
        edited copy of a stale run would do. ``matched_count`` answers "was
        there such a run" without a follow-up read.
        """
        payload = [warning.model_dump(mode="json") for warning in warnings]
        updated = self._runs.update_one(
            {"_id": run_id}, {"$set": _encoded(RUNS, {"warnings": payload})}
        )
        if updated.matched_count == 0:
            raise RecordNotFoundError(f"no run {run_id}")
        stored = self._runs.find_one({"_id": run_id}, {"warnings": 1})
        if stored is None:  # pragma: no cover - the record was just updated
            raise RecordNotFoundError(f"no run {run_id}")
        return [Warning.model_validate(item) for item in decode_keys(stored["warnings"])]

    def set_step_actual(
        self, run_id: str, node_id: str, iteration: int, actual: Mapping[str, Any]
    ) -> StepRecord:
        """See :meth:`agentprops.storage.base.Store.set_step_actual`.

        Ruling R-33's method. ``recorded_at`` comes from the server's clock, the
        way ``fetched_at``'s fallback does.

        **There is exactly one place this method decides anything**, and that is
        deliberate. ``actual: None`` in the filter makes the update conditional,
        so a writer that lost the race changes nothing - and it then *loops
        back* rather than reading the record and returning it, because the
        record it would read holds the winner's value. The top of the loop is
        where an already-recorded actual is compared with this caller's, and
        where a difference becomes a :class:`StoreError`. M3's fix round got
        exactly this wrong on the SQL side by checking that *some* actual was
        now stored rather than that it was *this* one; the shape is copied here
        along with the reason.
        """
        for _ in range(FIRST_WRITE_ATTEMPTS):
            found = self._step_doc(run_id, node_id, iteration)
            if found is None:
                raise RecordNotFoundError(
                    f"no step {run_id}/{node_id}/{iteration}: "
                    "an actual cannot be recorded for a step that was never served"
                )
            stored = found["actual"]
            if stored is not None:
                if canonical(decode_keys(stored)) == canonical(dict(actual)):
                    return step_from(_decoded(RUN_STEPS, found))
                raise StoreError(
                    f"step {run_id}/{node_id}/{iteration} already recorded a different actual"
                )
            self._steps.update_one(
                {"_id": _step_key(run_id, node_id, iteration), "actual": None},
                {
                    "$set": _encoded(
                        RUN_STEPS, {"actual": dict(actual), "recorded_at": self._server_now()}
                    )
                },
            )
            # Back to the top, whether that update applied or not. The next pass
            # re-reads and takes the identical-or-refuse decision above, which
            # returns this call's own value when it won and raises when a
            # concurrent writer's value got there first.
        raise StoreError(f"could not record an actual for {run_id}/{node_id}/{iteration}")

    # -------------------------------------------------------------------- health

    def health(self) -> StoreHealth:
        """See :meth:`agentprops.storage.base.Store.health`.

        Never raises: a store that cannot be reached reports ``healthy: false``
        with zero counts, because ``store_status`` exists to answer "is the
        backend there" and an exception is a worse answer than "no". The
        driver's short server-selection timeout is what keeps that answer
        prompt - see :data:`DEFAULT_SERVER_SELECTION_TIMEOUT_MS`.
        """
        try:
            counts = StoreCounts(
                blueprints=self._blueprints.count_documents({}),
                datasets=self._datasets.count_documents({}),
                runs=self._runs.count_documents({}),
            )
        except PyMongoError:
            return StoreHealth(
                backend=self.backend,
                healthy=False,
                counts=StoreCounts(blueprints=0, datasets=0, runs=0),
            )
        return StoreHealth(backend=self.backend, healthy=True, counts=counts)

    # ------------------------------------------------------------------ internals

    @property
    def _blueprints(self) -> Collection[dict[str, Any]]:
        return self._db[BLUEPRINTS]

    @property
    def _datasets(self) -> Collection[dict[str, Any]]:
        return self._db[DATASETS]

    @property
    def _skeletons(self) -> Collection[dict[str, Any]]:
        return self._db[SKELETONS]

    @property
    def _runs(self) -> Collection[dict[str, Any]]:
        return self._db[RUNS]

    @property
    def _steps(self) -> Collection[dict[str, Any]]:
        return self._db[RUN_STEPS]

    def _blueprint_doc(self, agent_id: str, version: str) -> dict[str, Any] | None:
        """The stored record for one blueprint version, or ``None``.

        A method rather than an inline ``find_one`` for the reason
        ``sql.py``'s ``_blueprint_row`` is one: it is the read
        :meth:`put_blueprint` decides on, so **the losing side of a concurrent
        first write is reachable in a test by making this return ``None``
        once** - which is how `test_mongo_allocation_races.py` reaches R-29's
        no-op convergence without threads. A read spelled inline is a branch
        with no way in.
        """
        return self._blueprints.find_one({"_id": _blueprint_key(agent_id, version)})

    def _skeleton_exists(self, skeleton_id: uuid.UUID) -> bool:
        """Whether a skeleton record exists. See :meth:`_blueprint_doc` for why
        this is a method.

        Two callers ask it for different reasons and that is deliberate:
        :meth:`put_skeleton` decides insert-or-update, and
        :meth:`mark_skeleton_submitted` distinguishes "no such skeleton" from
        "already claimed", because the second is a ``bool`` answer and the first
        is not.
        """
        return self._skeletons.find_one({"_id": skeleton_id}, {"_id": 1}) is not None

    def _run_exists(self, run_id: str) -> bool:
        """Whether a run record exists. See :meth:`_blueprint_doc`."""
        return self._runs.find_one({"_id": run_id}, {"_id": 1}) is not None

    def _server_now(self) -> datetime:
        """The **server's** clock, from the ``hello`` command's ``localTime``.

        Ruling R-09 allows a timestamp from a database clock, and R-39(d) reads
        that as "whether a column default or an explicit ``func.now()``". Mongo
        has no column defaults, so this is where a store with none gets the
        value - and it is genuinely the database's clock, not this process's, so
        there is still no clock read in Python anywhere in `storage/` and
        `tests/unit/test_layering.py` still passes.

        ``hello`` is the connection handshake command: it needs no privileges,
        which the alternatives (``serverStatus``, ``hostInfo``, ``$collStats``)
        all do, and it is available on every server version this project could
        meet. The reply is decoded with the client's codec options, so
        ``tz_aware=True`` makes the value UTC-aware like every other timestamp
        this adapter stores.

        Reached from three writes - ``put_blueprint``'s insert,
        ``upsert_step``'s ``fetched_at`` fallback and ``set_step_actual``'s
        ``recorded_at`` - which is one extra round trip on each. The rejected
        alternative was an aggregation-pipeline update using ``$$NOW``, which
        costs nothing extra and treats every literal in the update as an
        *expression*: a stored value that happened to be the string ``"$total"``
        would resolve as a field path. That is a hole in exactly the place -
        authored fixture content - where the values are least predictable, and
        no round trip is worth it.
        """
        return datetime_of(self._db.command("hello")["localTime"])

    def _next_dataset_version(self, dataset_id: uuid.UUID) -> int:
        newest = self._datasets.find_one(
            {"id": dataset_id}, {"version": 1}, sort=[("version", DESCENDING)]
        )
        return 1 if newest is None else int(newest["version"]) + 1

    def _dataset_version_exists(self, dataset_id: uuid.UUID, version: int) -> bool:
        return (
            self._datasets.find_one({"_id": _dataset_key(dataset_id, version)}, {"_id": 1})
            is not None
        )

    def _lineage_archived(self, dataset_id: uuid.UUID) -> bool | None:
        """The lineage's current archive state, or ``None`` if it has no versions.

        Ruling R-34: ``archived`` is a property of the *lineage*, not of a
        version. Read from the newest existing version, which under
        :meth:`set_archived`'s lineage-wide update is the same as every other
        version's.
        """
        newest = self._datasets.find_one(
            {"id": dataset_id}, {"archived": 1}, sort=[("version", DESCENDING)]
        )
        return None if newest is None else bool(newest["archived"])

    def _dataset_record(
        self, ds: Dataset, document: dict[str, Any], version: int, archived: bool
    ) -> dict[str, Any]:
        """The promoted fields for one dataset version.

        ``created_at`` comes from ``provenance.created_at`` - ruling R-09, and
        the reason no backend defaults this one. ``archived`` is passed in
        rather than read off ``ds`` because it belongs to the lineage, not to
        the model the caller handed over (ruling R-34).
        """
        return {
            "_id": _dataset_key(ds.id, version),
            "id": ds.id,
            "version": version,
            "agent_id": ds.blueprint.agent_id,
            "bp_version": ds.blueprint.version,
            "archived": archived,
            "labels": dict(ds.labels),
            "seed": ds.seed,
            "title": ds.provenance.title,
            "intent": ds.provenance.intent,
            "author_name": ds.provenance.author.name,
            "author_handle": ds.provenance.author.handle,
            "author_agent": ds.provenance.author.agent,
            "supersedes": (
                None
                if ds.provenance.supersedes is None
                else require_uuid(ds.provenance.supersedes, "provenance.supersedes")
            ),
            "document": document,
            "created_at": ds.provenance.created_at,
        }

    def _dataset_match(self, q: DatasetQuery) -> dict[str, Any]:
        """``dataset_find``'s four query filters. The fifth, ``q``, is not one.

        **labels** is the filter contracts section 7 says each backend handles
        differently, and here it is one equality per dimension against a
        dot-path into the stored object - the direct analogue of
        ``labels ->> 'tier'``. The dimension is escaped through the same codec
        the stored keys went through, which is what makes a dimension containing
        a dot filterable rather than a silent no-match. Every dimension given
        must match, which is ``DatasetQuery.labels``'s documented meaning.

        **``q`` is deliberately absent.** See :meth:`find_datasets`.

        Every clause this returns is applied **after** the lineage has been
        reduced to its latest version, which is not a detail of the caller's
        choosing - see :meth:`find_datasets` for what happened when they ran
        first.
        """
        match: dict[str, Any] = {"archived": False}
        if q.agent_id is not None:
            match["agent_id"] = q.agent_id
        if q.blueprint_version is not None:
            match["bp_version"] = q.blueprint_version
        if q.author is not None:
            match["author_handle"] = q.author
        for dimension, value in (q.labels or {}).items():
            match[f"labels.{_escape_key(dimension)}"] = value
        return match

    def _step_doc(self, run_id: str, node_id: str, iteration: int) -> dict[str, Any] | None:
        return self._steps.find_one({"_id": _step_key(run_id, node_id, iteration)})

    def _next_step_seq(self, run_id: str) -> int:
        newest = self._steps.find_one({"run_id": run_id}, {"seq": 1}, sort=[("seq", DESCENDING)])
        return 1 if newest is None else int(newest["seq"]) + 1

    def _step_seq_taken(self, run_id: str, seq: int) -> bool:
        return self._steps.find_one({"run_id": run_id, "seq": seq}, {"_id": 1}) is not None


def datetime_of(value: Any) -> datetime:
    """``value`` as a ``datetime``, or a :class:`StoreError` naming what it was.

    The one place this adapter reads a value it did not write: the ``hello``
    reply. ``localTime`` is a BSON date on every server, so this is a guard
    rather than a conversion - and a guard that says what it got beats an
    ``AttributeError`` three frames away.
    """
    if isinstance(value, datetime):
        return value
    raise StoreError(f"the server did not report a usable clock: {value!r}")


def _database_in(url: str) -> str | None:
    """The database named in a Mongo URL's path, if it names one.

    ``mongodb://host:27117/agentprops`` gives ``agentprops``;
    ``mongodb://host:27117/`` and ``mongodb://host:27117`` give ``None``. Query
    parameters are stripped, because ``?replicaSet=rs0`` is not part of a
    database name.
    """
    _, _, tail = url.partition("://")
    _, slash, path = tail.partition("/")
    if not slash:
        return None
    name = path.split("?", 1)[0]
    return name or None


def _protocol_conformance(store: MongoStore) -> Store:
    """``mypy --strict`` checks that :class:`MongoStore` satisfies ``Store``.

    The same three-line function `sql.py` carries, and it is the half
    ``runtime_checkable`` cannot see: ``isinstance`` compares method *names*,
    this compares argument names and return types against
    `docs/contracts.md` section 6.
    """
    return store
