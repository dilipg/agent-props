"""The real :class:`~agentprops.validation.resolver.Resolver`, backed by a store.

Ruling R-11's other half. `validation/` is specified as pure functions with no
I/O, but DS-001 (the blueprint exists and is published), DS-031
(``provenance.supersedes`` names a dataset that exists) and BP-016 (a published
version is immutable) are existence checks against the store. R-11 resolves that
by injecting a two-method ``Resolver`` rather than a ``Store``, so `validation/`
imports nothing from `storage/` and a test asserts it.

This module is the seam. It is the *only* place in the codebase where those two
lookups meet a real store, and it is deliberately three lines of logic:
everything interesting about DS-001, DS-031 and BP-016 is in the rules, where a
reader looking for a rule will look.

Two decisions worth reading before changing anything here.

**It returns a document, not a model.** Ruling R-23 has the validator working on
raw mappings throughout, so the blueprint a dataset is checked against arrives
in the same shape as the dataset itself. ``exclude_unset=True`` per ruling R-08,
which is what makes BP-016's canonical comparison compare like with like: the
stored document was written with ``exclude_unset`` too (see
`storage/sql.py::_document`), so a re-publish of an unmodified document is
canonically identical and R-29's no-op success is reachable. A full dump would
reintroduce every omitted optional field as ``null`` and BP-016 would fire on
every re-publish.

**``published`` is filtered here, not in the store.**
``Store.get_blueprint(agent_id, version)`` returns that exact version *whatever
its status* - which is what ``blueprint_get`` needs, since a draft is a
legitimate thing to fetch. DS-001 and BP-016 both ask specifically about a
*published* version, so the status check belongs to this adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentprops.storage import STATUS_PUBLISHED, Store

__all__ = ["StoreResolver"]


class StoreResolver:
    """A ``Resolver`` over a ``Store``. Read-only, and only these two lookups."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def get_published_blueprint(self, agent_id: str, version: str) -> Mapping[str, Any] | None:
        """The stored published blueprint document at that exact version, or ``None``."""
        blueprint = self._store.get_blueprint(agent_id, version)
        if blueprint is None or blueprint.status != STATUS_PUBLISHED:
            return None
        document: dict[str, Any] = blueprint.model_dump(mode="json", exclude_unset=True)
        return document

    def dataset_exists(self, dataset_id: str) -> bool:
        """Whether any version of ``dataset_id`` exists, archived or not.

        ``get_dataset`` returns archived rows, which is what DS-031 wants:
        superseding an archived dataset is exactly what lineage is for. A
        malformed id is a miss rather than an exception - the store's read path
        guarantees that.
        """
        return self._store.get_dataset(dataset_id, None) is not None
