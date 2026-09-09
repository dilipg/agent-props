"""The three admin reads: ``store_status``, ``label_vocabulary``, ``agent_list``.

None of them writes anything and none of them gates. ``store_status`` reports
``healthy`` as a *liveness* answer, not a policy verdict - a store with zero
datasets is perfectly healthy.

The two counting numbers, and why they differ
---------------------------------------------

``store_status`` counts **including archived datasets** (ruling R-05: "it is a
store-health number, not a discovery number"). ``label_vocabulary`` and
``agent_list`` count **excluding** them, because both are discovery surfaces and
both are built from ``find_datasets``, which is the thing that hides archives.
The two numbers can therefore disagree, and they are meant to.

Determinism, which is M4's acceptance criterion
-----------------------------------------------

"Label queries return deterministic ordering for identical inputs", asserted as
byte-identical output. Both counting tools build dicts in Python, so the order
is chosen here rather than inherited from the store, and it is chosen twice:

- dimensions and their values follow the **blueprint's declaration order**, not
  a sort. That is deterministic (a JSON object preserves order through
  ``json.loads`` and through the ``LabelSchema`` model) and it is also the order
  a human wrote them in, which is what a reviewer wants to read.
- agents follow ``list_blueprints``'s ``(agent_id, semver)`` order, which ruling
  R-35 already made total, and the versions inside each agent inherit it. **Not
  re-sorted here**: a Python sort would put ``1.10.0`` before ``1.9.0``.

``label_vocabulary`` deliberately does not page. It calls ``find_datasets`` with
no ``limit``, because a count over the first 50 rows is not a count. That is a
full scan bounded by the number of datasets for one blueprint version, which is
authoring volume; if it ever matters, the remedy is a counting method on the
Protocol, not a page size here.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentprops.models import DatasetQuery, DatasetSummary
from agentprops.service.context import ServiceContext
from agentprops.service.envelope import Reply, not_found, success

__all__ = ["agents", "label_vocabulary", "store_status", "value_counts"]


def store_status(context: ServiceContext) -> Reply:
    """``{backend, healthy, counts: {blueprints, datasets, runs}}``."""
    return success("status", context.store.health().model_dump(mode="json"))


def label_vocabulary(context: ServiceContext, agent_id: str, version: str | None) -> Reply:
    """A blueprint's ``label_schema`` plus per-value dataset counts.

    ``version`` omitted means the latest published version, the same as
    ``blueprint_get``. An agent with no blueprint is ``AP-004``: there is no
    vocabulary to report, which is a resolution failure rather than an empty
    answer.
    """
    blueprint = context.store.get_blueprint(agent_id, version)
    if blueprint is None:
        return not_found("blueprint", field="agent_id", agent_id=agent_id, version=version)
    dimensions = blueprint.label_schema.dimensions
    rows = context.store.find_datasets(
        DatasetQuery(agent_id=agent_id, blueprint_version=blueprint.version)
    )
    return success(
        "vocabulary",
        {
            "agent_id": blueprint.agent_id,
            "version": blueprint.version,
            "label_schema": {
                "dimensions": {name: list(values) for name, values in dimensions.items()}
            },
            "counts": value_counts(dimensions, rows),
            "dataset_count": len(rows),
        },
    )


def value_counts(
    dimensions: Mapping[str, list[str]], rows: list[DatasetSummary]
) -> dict[str, dict[str, int]]:
    """Per-value dataset counts, in the blueprint's declaration order.

    Public because ``service/prompts.py::cover_the_label_space`` needs exactly
    this number to name the values with **zero** datasets, and a second
    implementation of "how many datasets carry this value" is a second thing to
    keep in step with DS-012.

    A value carried by a dataset but absent from the vocabulary is **not**
    reported, because DS-012 makes it unstorable: every label dimension and
    value has to exist in the blueprint's ``label_schema`` before a dataset can
    be written. Reporting a phantom column would be inventing a state the
    validator forbids.
    """
    return {
        name: {value: sum(1 for row in rows if row.labels.get(name) == value) for value in values}
        for name, values in dimensions.items()
    }


def agents(context: ServiceContext) -> Reply:
    """``{agent_id, versions[], dataset_count}`` per agent.

    Built from ``list_blueprints(None)`` - every status, so a draft-only agent
    is still listed - and one ``find_datasets`` call per agent. The version list
    keeps the store's semver-aware order.
    """
    ordered: dict[str, list[str]] = {}
    for summary in context.store.list_blueprints(None):
        ordered.setdefault(summary.agent_id, []).append(summary.version)
    return success(
        "agents",
        [
            {
                "agent_id": agent_id,
                "versions": versions,
                "dataset_count": len(context.store.find_datasets(DatasetQuery(agent_id=agent_id))),
            }
            for agent_id, versions in ordered.items()
        ],
    )
