"""Shapes the storage ``Protocol`` needs that project no single aggregate.

`docs/contracts.md` section 6 names six types the specification never defines.
Ruling R-05 supplies all six; five of them are projections of, or queries
against, one aggregate and live with it - ``BlueprintSummary`` in
`blueprint.py`, ``DatasetQuery`` and ``DatasetSummary`` in `dataset.py`,
``RunQuery`` and ``RunSummary`` in `run.py`, ``Skeleton`` in `skeleton.py`.

:class:`StoreHealth` is the one that describes the *store* rather than anything
in the domain, so it lives here. The ``Store`` Protocol itself is M3's, in
`storage/base.py`; only its data shapes are M1's.
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
