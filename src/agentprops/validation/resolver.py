"""The one dependency `validation/` is allowed to have on the outside world.

Three rules in the catalogue are existence checks against the store:

- **DS-001** the dataset's ``blueprint`` names an existing *published* blueprint
  at that exact version,
- **DS-031** ``provenance.supersedes`` names a dataset that exists,
- **BP-016** a published ``{agent_id, version}`` may not be modified.

`validation/` is specified as pure functions with no I/O, and CLAUDE.md's
layering rule forbids it from importing `storage/`. Ruling R-11 resolves that
by injecting a *resolver*, not a store: two read-only lookups, defined here as
a ``Protocol`` so the caller supplies the implementation. `service/` passes an
adapter over the ``Store``; the test corpus passes a stub. Nothing in this
package ever performs the lookup itself.

Deliberately not on this Protocol: anything that writes, anything that lists,
and anything a rule does not need. It is two methods so that widening it is a
visible decision rather than a convenience.
"""

from collections.abc import Mapping
from typing import Any, Protocol

__all__ = ["NullResolver", "Resolver"]


class Resolver(Protocol):
    """Read-only existence lookups the catalogue needs (ruling R-11)."""

    def get_published_blueprint(self, agent_id: str, version: str) -> Mapping[str, Any] | None:
        """The raw published blueprint document, or ``None`` if there is none.

        Returns the *document* rather than a model: ruling R-23 has the
        validator working on raw mappings throughout, and the blueprint a
        dataset is checked against is read the same way as the dataset itself.
        """
        ...

    def dataset_exists(self, dataset_id: str) -> bool:
        """Whether a dataset with this id exists, archived or not.

        Archived counts as existing: DS-031 records lineage, and superseding an
        archived dataset is exactly what lineage is for.
        """
        ...


class NullResolver:
    """A resolver that knows nothing. The default for ``validate_blueprint``.

    Blueprint validation needs a resolver only for BP-016, which asks "does a
    published version already exist at this ``{agent_id, version}``?". For a
    first publish the honest answer is no, and that is what this returns - so
    ``validate_blueprint(doc)`` with no resolver validates a document on its own
    terms and never fires BP-016.
    """

    def get_published_blueprint(self, agent_id: str, version: str) -> Mapping[str, Any] | None:
        return None

    def dataset_exists(self, dataset_id: str) -> bool:
        return False
