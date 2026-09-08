"""Shapes the storage ``Protocol`` needs that project no single aggregate.

`docs/contracts.md` section 6 names six types the specification never defines.
Ruling R-05 supplies all six; five of them are projections of, or queries
against, one aggregate and live with it - ``BlueprintSummary`` in
`blueprint.py`, ``DatasetQuery`` and ``DatasetSummary`` in `dataset.py`,
``RunQuery`` and ``RunSummary`` in `run.py`, ``Skeleton`` in `skeleton.py`.

:class:`StoreHealth` is the one that describes the *store* rather than anything
in the domain, so it lives here.

**This module holds no Protocol.** The filename sits next to a `storage/`
package and implies otherwise, so to be unambiguous: the ``Store`` Protocol -
contracts section 6's fifteen methods, and the two programming-error guards an
adapter may raise - is :class:`agentprops.storage.base.Store`, in
`src/agentprops/storage/base.py`. This module holds only the data shape that
Protocol's ``health()`` returns. Nothing here does I/O, and
`tests/unit/test_layering.py` asserts that `models/` never imports `storage/`.
"""

from pydantic import StrictBool, StrictInt

from agentprops.models.base import StrictModel

__all__ = ["StoreCounts", "StoreHealth"]


class StoreCounts(StrictModel):
    """Row counts per aggregate.

    ``datasets`` **includes archived datasets**, per ruling R-05: this is a
    store-health number, not a discovery number, and ``find_datasets`` remains
    the thing that hides archives.
    """

    blueprints: StrictInt
    datasets: StrictInt
    runs: StrictInt


class StoreHealth(StrictModel):
    """What ``store_status`` returns, and what ``Store.health()`` produces.

    ``healthy`` is a liveness answer about the backend, not a policy verdict:
    a store with zero datasets is perfectly healthy. Nothing about this type
    gates anything.
    """

    backend: str
    """``sqlite``, ``postgres`` or ``mongo``."""

    healthy: StrictBool
    counts: StoreCounts
