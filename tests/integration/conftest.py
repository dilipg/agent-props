"""The ``store`` fixture, parameterised over backends, and the golden aggregates.

`docs/contracts.md` section 6: the conformance suite is "written once against
[the Protocol] and parameterised over all three backends". This is the half that
makes that literally true - every test in this directory takes ``store``, typed
as :class:`~agentprops.storage.Store`, and never names an adapter.

M7's work here is to extend ``IMPLEMENTED_STORE_BACKENDS`` in
`tests/conftest.py` and add two branches to :func:`store`. Not a line of the
suite changes, and a backend that is selected but not built yet skips with a
reason rather than erroring - which is what makes
``uv run pytest -m integration --store postgres`` a sensible command to type
today.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from agentprops.models import SECTION_IDS, Blueprint, Dataset, Run, RunPin, Section, Skeleton
from agentprops.storage import SqlStore, Store, create_schema, sqlite_url
from conftest import (
    FIXTURES_DIR,
    FROZEN_NOW,
    IMPLEMENTED_STORE_BACKENDS,
    selected_store_backends,
)

__all__ = ["FROZEN_NOW"]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parameterise every ``store``-taking test over the selected backends."""
    if "store" in metafunc.fixturenames:
        metafunc.parametrize(
            "store",
            selected_store_backends(metafunc.config),
            indirect=True,
            ids=lambda backend: str(backend),
        )


@pytest.fixture
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Store]:
    """One empty store per test, on the backend named by ``--store``.

    A file-backed SQLite database rather than ``:memory:``, so the store is
    exercised through a real connection pool and a real file - the mode
    CLAUDE.md calls containerless and CI uses. The schema is created from
    ``METADATA``; `test_migrations.py` is what proves that schema and the
    migration's agree, and one test there runs a write journey against a
    migrated database.
    """
    backend = str(request.param)
    if backend not in IMPLEMENTED_STORE_BACKENDS:
        pytest.skip(
            f"the {backend} storage adapter lands at M7; "
            f"implemented today: {', '.join(IMPLEMENTED_STORE_BACKENDS)}"
        )
    adapter = SqlStore.from_url(sqlite_url(tmp_path / "agentprops.db"))
    create_schema(adapter.engine)
    try:
        yield adapter
    finally:
        adapter.dispose()


def _load(relative: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((FIXTURES_DIR / relative).read_text(encoding="utf-8"))
    return document


@pytest.fixture
def blueprint() -> Blueprint:
    """The golden `location-onboarding` blueprint, version 1.0.0."""
    return Blueprint.model_validate(_load("blueprints/location-onboarding-1.0.0.json"))


@pytest.fixture
def dataset() -> Dataset:
    """`priya-missing-docs`: the loop-path golden dataset."""
    return Dataset.model_validate(_load("datasets/priya-missing-docs.json"))


@pytest.fixture
def dataset_document() -> dict[str, Any]:
    """The same dataset as raw JSON, for the byte-for-byte round-trip assertion."""
    return _load("datasets/priya-missing-docs.json")


@pytest.fixture
def other_dataset() -> Dataset:
    """`arun-escalated`: the compliant-branch golden dataset.

    Its ``provenance.created_at`` is 11:02:47, after `priya`'s 10:14:22, which
    is what makes ``find_datasets``'s ordering assertion mean something.
    """
    return Dataset.model_validate(_load("datasets/arun-escalated.json"))


@pytest.fixture
def published(store: Store, blueprint: Blueprint) -> Blueprint:
    """The golden blueprint, published, so datasets have a parent to reference.

    The DDL's ``FOREIGN KEY (agent_id, bp_version)`` is real on both SQL
    dialects, and writing the blueprint first is the honest order regardless -
    DS-001 requires a dataset to name an existing published blueprint.
    """
    return store.put_blueprint(blueprint, publish=True)


@pytest.fixture
def skeleton(blueprint: Blueprint, dataset: Dataset) -> Skeleton:
    """A skeleton with the five-section manifest ruling R-06 fixes.

    ``labels`` and ``seed`` are the golden dataset's, because they are
    ``dataset_skeleton``'s two inputs and M5 added them to the row: a skeleton
    that does not carry them cannot be assembled into a dataset.
    """
    return Skeleton(
        id=UUID("3f8c1a20-0000-4000-8000-00000000aaaa"),
        agent_id=blueprint.agent_id,
        bp_version=blueprint.version,
        labels=dict(dataset.labels),
        seed=dataset.seed,
        manifest=[
            Section(
                id=section_id,
                required=True,
                pointers=[f"/{section_id.replace('.', '/')}"],
                description=f"fill {section_id}",
            )
            for section_id in SECTION_IDS
        ],
        created_at=FROZEN_NOW,
    )


def make_run(run_id: str, dataset: Dataset, version: int = 1, **overrides: Any) -> Run:
    """A run pinned to ``(dataset.id, version)``.

    The run id is a client-generated opaque string - ground rule 9's one
    sanctioned ``uuid4()`` is in the *client*, and these are literals so the
    tests stay deterministic.
    """
    values: dict[str, Any] = {
        "id": run_id,
        "agent_id": dataset.blueprint.agent_id,
        "pin": RunPin(
            dataset_id=dataset.id,
            dataset_version=version,
            blueprint_version=dataset.blueprint.version,
        ),
        "run_class": "dev",
        "status": "running",
        "started_at": FROZEN_NOW,
    }
    values.update(overrides)
    return Run(**values)
