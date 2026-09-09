"""Shared pytest fixtures, the one path constant every suite needs, and ``--store``.

:data:`FIXTURES_DIR` is defined here and imported by the suites (``from
conftest import FIXTURES_DIR``) rather than recomputed per file, so a fixture
directory moves in one place. It is a module constant, not a pytest fixture,
because the round-trip and schema suites parametrise over the directory at
collection time and a fixture is not available then.

``--store`` lives here because ``pytest_addoption`` is only honoured in the
rootdir conftest. The fixture it feeds is in `tests/integration/conftest.py`,
next to the suite that uses it.

M4 adds the tool-surface fixtures: a ``ServiceContext`` over a file-backed
SQLite store with a **frozen clock**, plus the golden documents. They live here
rather than in `tests/unit/` because both the service suites and the
tool-contract suite take them, and because M5 through M8 will take them too.

The MCP client itself is **not** a fixture - it is
:func:`toolclient.connected`, for the pytest-asyncio/anyio reason that module
records.

The clock is frozen in every fixture that builds a context, which is ruling
R-09's other half: "a single injected ``Clock`` port, used only in `service/`
... and frozen in tests". M4 stamps nothing with it, so no assertion here
depends on the value yet - the point is that the seam is wired and that no later
milestone has to invent it.
"""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentprops.service import FrozenClock, ServiceContext, sqlite_context
from agentprops.storage import SqlStore

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: A frozen clock for the fields `service/`'s injected ``Clock`` stamps (ruling
#: R-09). Tests never read a real clock either, so a stored timestamp is
#: comparable to an expected one. Imported by `tests/integration/conftest.py`
#: rather than redefined there.
FROZEN_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

#: Every backend the storage conformance suite is written against.
#: `docs/contracts.md` section 6: the suite is "written once against it and
#: parameterised over all three backends".
STORE_BACKENDS = ("sqlite", "postgres", "mongo")

#: The backends that exist. **All three, from M7.** The mechanism M3 built for
#: this is the whole reason the conformance suite did not change: extend this
#: tuple and add a branch to the ``store`` fixture in
#: `tests/integration/conftest.py`, and every test in that directory runs
#: against the new backend.
IMPLEMENTED_STORE_BACKENDS = STORE_BACKENDS

#: What ``--store`` defaults to. Plain ``uv run pytest`` therefore runs the
#: whole conformance suite rather than skipping it - and it runs it against
#: SQLite only, because that is the containerless mode CLAUDE.md describes and
#: the one CI uses. Postgres and Mongo are opt-in with ``--store``, and each
#: **skips with a reason** when its server is unreachable rather than failing
#: a run that never asked for a container.
DEFAULT_STORE_BACKENDS = ("sqlite",)

#: Where ``--store postgres`` and ``--store mongo`` look for their servers.
#:
#: The ports match `docker-compose.yml`'s published ones, so
#: ``docker compose --profile shared up -d postgres`` followed by
#: ``uv run pytest -m integration --store postgres`` needs no environment at
#: all - and `tests/unit/test_containers.py` asserts the two sides agree,
#: because a default that dials a port nothing publishes is a default that
#: skips every test.
#:
#: **27117 and 5442 rather than 27017 and 5432, and that is ruling R-60 rather
#: than a preference.** A default that can reach a *foreign* server is the
#: problem: it happened three times on the machine M7 was built on, and the
#: third time it silently added 14 passing tests to a reviewer's gate run
#: against an unrelated MongoDB. The cost was never the stray database - it was
#: a reported test count that depended on what happened to be listening.
#: Overridable ports plus a skip message naming the URL was the first mitigation
#: and it was not enough, because neither changes what the *default* does. Now a
#: foreign server on 27017 or 5432 is unreachable unless someone overrides on
#: purpose.
POSTGRES_URL_ENV_VAR = "AGENTPROPS_TEST_POSTGRES_URL"
MONGO_URL_ENV_VAR = "AGENTPROPS_TEST_MONGO_URL"
DEFAULT_TEST_POSTGRES_URL = "postgresql://agentprops:agentprops@localhost:5442/agentprops"
DEFAULT_TEST_MONGO_URL = "mongodb://localhost:27117"


def postgres_test_url() -> str:
    """The Postgres the integration suite should use."""
    return os.environ.get(POSTGRES_URL_ENV_VAR) or DEFAULT_TEST_POSTGRES_URL


def mongo_test_url() -> str:
    """The Mongo the integration suite should use."""
    return os.environ.get(MONGO_URL_ENV_VAR) or DEFAULT_TEST_MONGO_URL


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--store",
        action="append",
        default=[],
        choices=list(STORE_BACKENDS),
        help=(
            "storage backend to run the integration suite against; repeatable. "
            f"Defaults to {' '.join(DEFAULT_STORE_BACKENDS)}. "
            f"Implemented today: {' '.join(IMPLEMENTED_STORE_BACKENDS)}."
        ),
    )


def selected_store_backends(config: pytest.Config) -> tuple[str, ...]:
    """The backends this run was asked for, de-duplicated, order preserved."""
    chosen: list[str] = list(config.getoption("--store"))
    if not chosen:
        return DEFAULT_STORE_BACKENDS
    return tuple(dict.fromkeys(chosen))


def load_document(relative: str) -> dict[str, Any]:
    """One golden fixture, parsed. ``relative`` is under `tests/fixtures/`."""
    document: dict[str, Any] = json.loads((FIXTURES_DIR / relative).read_text(encoding="utf-8"))
    return document


def load_text(relative: str) -> str:
    """One golden fixture as **raw text**, for ruling R-20's duplicate-key path."""
    return (FIXTURES_DIR / relative).read_text(encoding="utf-8")


BLUEPRINT_FIXTURE = "blueprints/location-onboarding-1.0.0.json"
DATASET_FIXTURE = "datasets/priya-missing-docs.json"
OTHER_DATASET_FIXTURE = "datasets/arun-escalated.json"


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """The injected clock, frozen at :data:`FROZEN_NOW` (ruling R-09)."""
    return FrozenClock(FROZEN_NOW)


@pytest.fixture
def context(tmp_path: Path, frozen_clock: FrozenClock) -> Iterator[ServiceContext]:
    """A ``ServiceContext`` over an empty file-backed SQLite store, clock frozen.

    A file rather than ``:memory:`` for the reason
    :func:`agentprops.service.sqlite_context` records: the SDK runs a
    synchronous tool on a worker thread, and an in-memory SQLite database
    belongs to its connection, so the schema would be invisible to the thread
    the tool runs on.
    """
    built = sqlite_context(tmp_path / "agentprops.db", clock=frozen_clock)
    try:
        yield built
    finally:
        if isinstance(built.store, SqlStore):
            built.store.dispose()


@pytest.fixture
def blueprint_document() -> dict[str, Any]:
    """The golden `location-onboarding` blueprint, 1.0.0, as a raw document."""
    return load_document(BLUEPRINT_FIXTURE)


@pytest.fixture
def dataset_document() -> dict[str, Any]:
    """`priya-missing-docs`, the loop-path golden dataset, as a raw document."""
    return load_document(DATASET_FIXTURE)


@pytest.fixture
def other_dataset_document() -> dict[str, Any]:
    """`arun-escalated`, whose ``provenance.created_at`` is later than `priya`'s."""
    return load_document(OTHER_DATASET_FIXTURE)
