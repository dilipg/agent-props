"""The guard against the failure mode M7 could have shipped: zero tests, quietly.

``uv run pytest -m integration --store postgres`` that collects nothing, or
collects everything and skips it, reports **green**. So does a run where the
``store`` fixture silently fell back to SQLite. Both of those are the milestone
gate - "the full integration suite passes identically against SQLite, Postgres
and Mongo" - being claimed without being met, and neither leaves a mark in the
summary line anyone reads.

M3 built the mechanism this file guards: ``--store`` names the backends,
``IMPLEMENTED_STORE_BACKENDS`` says which exist, and an unavailable one skips
with a reason. Four properties keep that honest, and each one is a way the
arrangement could rot:

``test_every_declared_backend_is_implemented``
    ``STORE_BACKENDS`` and ``IMPLEMENTED_STORE_BACKENDS`` are the same three at
    M7. Until M7 they were not, deliberately - so this test is the inversion of
    the state M3 left, in the same way M5 inverted the ``SK-*`` drift test.

``test_the_conformance_suite_is_parameterised_over_the_store_fixture``
    Every test in `test_store_conformance.py` takes ``store`` **directly**,
    which is what ``pytest_generate_tests`` keys off. A conformance test that
    reached a store any other way would run once instead of once per backend,
    and nothing else would notice.

``test_the_store_fixture_reaches_a_live_store``
    The one runtime check, parameterised over the selected backends like the
    suite itself: the store answers ``healthy`` and **names the backend that
    was asked for**. A fixture that fell through to SQLite passes every
    conformance test and fails this.

``test_a_selected_backend_that_is_unavailable_skips_rather_than_erroring``
    The skip has to carry the URL, because "60 skipped" with no reason is how a
    developer concludes the suite ran.
"""

from __future__ import annotations

import inspect

import pytest

from agentprops.storage import Store
from conftest import (
    DEFAULT_STORE_BACKENDS,
    IMPLEMENTED_STORE_BACKENDS,
    STORE_BACKENDS,
    mongo_test_url,
    postgres_test_url,
    selected_store_backends,
)
from integration import test_store_conformance as suite

pytestmark = pytest.mark.integration


def test_every_declared_backend_is_implemented() -> None:
    """All three exist from M7. The inversion of the state M3 deliberately left.

    ``IMPLEMENTED_STORE_BACKENDS`` was ``("sqlite",)`` for four milestones, and
    the skip it produced was the honest answer. Asserting equality now is what
    stops a future edit narrowing it back without anyone noticing - the same
    reason M5 inverted the test that pinned the absence of the ``SK-*`` rules
    rather than deleting it.
    """
    assert set(IMPLEMENTED_STORE_BACKENDS) == set(STORE_BACKENDS) == {"sqlite", "postgres", "mongo"}


def test_the_default_is_the_containerless_backend() -> None:
    """CI runs ``uv run pytest`` with no flags and must not need a container.

    `docs/build-handoff.md` section 2: "a ``--store sqlite <path>`` mode runs
    with no container at all and is what CI uses". So the default is SQLite
    alone - not all three, which would turn every CI run red on a machine with
    no Docker, and not empty, which would collect nothing.
    """
    assert DEFAULT_STORE_BACKENDS == ("sqlite",)


def conformance_tests() -> dict[str, inspect.Signature]:
    """Every ``test_*`` function in the conformance suite, with its signature."""
    return {
        name: inspect.signature(value)
        for name, value in vars(suite).items()
        if name.startswith("test_") and inspect.isfunction(value)
    }


def test_the_conformance_suite_is_parameterised_over_the_store_fixture() -> None:
    """The mechanism, checked rather than assumed.

    ``pytest_generate_tests`` parameterises a test over the selected backends
    **if and only if** ``store`` is in its fixture names. A conformance test
    that took only ``published`` or ``pinned`` - both of which request ``store``
    themselves - would still get a store, would still pass, and would run
    **once** rather than once per backend. That is a silently
    single-backend test in a suite whose whole purpose is the opposite.

    The non-emptiness assertion is the other half: an import that produced no
    tests would satisfy the loop below trivially.
    """
    tests = conformance_tests()
    assert len(tests) > 50, f"the conformance suite has only {len(tests)} tests"
    unparameterised = [name for name, sig in tests.items() if "store" not in sig.parameters]
    assert not unparameterised, (
        f"these conformance tests do not take the `store` fixture directly, so they run once "
        f"instead of once per backend: {sorted(unparameterised)}"
    )


def test_the_store_fixture_reaches_a_live_store(
    store: Store, request: pytest.FixtureRequest
) -> None:
    """The runtime half: the store is up, and it is the backend that was asked for.

    ``health()`` never raises by contract, so an unreachable store reports
    ``healthy: false`` rather than failing the fixture - which means a suite
    could run entirely against a dead store and only the assertions that
    happened to touch data would notice.

    The ``backend`` assertion is the one that catches the worse mistake. A
    ``store`` fixture that fell through to SQLite for an unimplemented branch
    would pass every conformance test - correctly, because SQLite passes them -
    and this is the only test in the tree that would fail.
    """
    health = store.health()
    assert health.healthy is True, f"the {request.node.callspec.id} store is not reachable"
    assert health.backend == request.node.callspec.id, (
        f"--store asked for {request.node.callspec.id} and got a {health.backend} store"
    )


def test_a_selected_backend_that_is_unavailable_skips_with_the_url(
    pytestconfig: pytest.Config,
) -> None:
    """A skip has to say what to start, and where it looked.

    Not testable by making a server disappear, so what is asserted is the
    *input* to the message: both URL helpers answer, and both answer something
    that looks like a URL for the right driver. The skip reason interpolates
    them, so a helper returning an empty string would produce
    "no Postgres at :" and a developer would have no idea what to fix.
    """
    assert postgres_test_url().startswith("postgres")
    assert mongo_test_url().startswith("mongodb")
    assert set(selected_store_backends(pytestconfig)) <= set(IMPLEMENTED_STORE_BACKENDS)
