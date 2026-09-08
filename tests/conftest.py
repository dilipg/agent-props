"""Shared pytest fixtures, the one path constant every suite needs, and ``--store``.

:data:`FIXTURES_DIR` is defined here and imported by the suites (``from
conftest import FIXTURES_DIR``) rather than recomputed per file, so a fixture
directory moves in one place. It is a module constant, not a pytest fixture,
because the round-trip and schema suites parametrise over the directory at
collection time and a fixture is not available then.

``--store`` lives here because ``pytest_addoption`` is only honoured in the
rootdir conftest. The fixture it feeds is in `tests/integration/conftest.py`,
next to the suite that uses it.

Pytest fixtures proper land as each milestone needs them: in-memory MCP client
wiring at M4.
"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: Every backend the storage conformance suite is written against.
#: `docs/contracts.md` section 6: the suite is "written once against it and
#: parameterised over all three backends".
STORE_BACKENDS = ("sqlite", "postgres", "mongo")

#: The backends that exist. M7 adds the other two, by extending this tuple and
#: the fixture in `tests/integration/conftest.py` - not by writing a second
#: suite. A selected-but-unimplemented backend skips with a reason rather than
#: erroring, so ``--store postgres`` is a meaningful command today and a
#: passing one at M7.
IMPLEMENTED_STORE_BACKENDS = ("sqlite",)

#: What ``--store`` defaults to. Plain ``uv run pytest`` therefore runs the
#: whole conformance suite rather than skipping it.
DEFAULT_STORE_BACKENDS = ("sqlite",)


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
