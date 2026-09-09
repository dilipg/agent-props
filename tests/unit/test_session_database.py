"""Ruling R-63: the test database name is per process, and stays that way.

The `store` fixture **drops its database before every test**, because half the
conformance suite counts rows. That is correct isolation *within* a run and no
isolation at all *between* two of them: with a shared name, two concurrent runs
delete each other's data mid-test.

**The failure that made this a ruling.** It does not look like a collision. It
looks like a test suite finding bugs: `14 failed, 118 passed`, every failure a
row-count or a latest-version assertion, and M7's implementer came within one
paste of reporting it as a real result. Ruling R-60 had just been decided for
the identical shape one layer out - a *documented* convention about which server
to talk to, violated three times - and R-63 notes this one is worse, because the
wrong-server case at least produced suspiciously *passing* numbers, which is the
easier thing to notice.

So the fix is structural rather than a line in a README, and these are the
guards on the structure. Two directions, and the second is the silent one:

* two sessions must compute **different** names - checked in real subprocesses,
  because a module constant compared with itself inside one interpreter agrees
  by construction and would prove nothing;
* no test file may spell the bare prefix as a database name, which is how the
  sharing would come back: one `MongoStore(client, "agentprops_conformance")`
  and that test is on a fixed name again while every other test is isolated,
  which is a worse state than uniform sharing because it only breaks under
  concurrency.

No server is needed for any of this, so it lives in `tests/unit/` and runs in
the containerless mode CI uses.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from conftest import (
    LEGAL_DATABASE_NAME,
    TEST_DATABASE_NAME,
    TEST_DATABASE_PREFIX,
    postgres_session_url,
    postgres_test_url,
)

TESTS_DIR: Final = Path(__file__).resolve().parents[1]

#: How many subprocesses to ask for a name. Two proves difference; the third is
#: cheap and turns "these two happened to differ" into a set assertion.
_SESSIONS: Final = 3

#: Printed by each subprocess. It imports the root conftest exactly as pytest
#: does - by putting `tests/` on the path - so what is measured is the same
#: constant the fixtures use, not a re-derivation of it.
_PRINT_THE_NAME: Final = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "import conftest; print(conftest.TEST_DATABASE_NAME)"
)


def name_from_a_fresh_session() -> str:
    """The database name a brand-new interpreter computes."""
    completed = subprocess.run(
        [sys.executable, "-c", _PRINT_THE_NAME, str(TESTS_DIR)],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def test_two_sessions_compute_different_database_names() -> None:
    """The assertion R-63 exists for, in the only place it can be observed.

    Real subprocesses, for the same reason
    `test_expansion_determinism.py` uses them: a per-process value is constant
    for the life of a process, so any single-interpreter comparison of it
    against itself passes even if the name were hard-coded.
    """
    names = [name_from_a_fresh_session() for _ in range(_SESSIONS)]
    assert len(set(names)) == _SESSIONS, f"sessions reused a database name: {names}"


def test_every_session_name_starts_with_the_prefix_the_sweep_looks_for() -> None:
    """Uniqueness is worth nothing if a stray database cannot be found again.

    Ruling R-63's stated cost is that a killed run leaves one behind, covered by
    a sweep - and a sweep is a shell pattern over this prefix.
    """
    for name in (TEST_DATABASE_NAME, name_from_a_fresh_session()):
        assert name.startswith(f"{TEST_DATABASE_PREFIX}_"), name


def test_the_session_name_is_legal_on_both_backends() -> None:
    """It is interpolated into ``CREATE DATABASE``, so its shape is an assertion.

    A database name cannot be a bound parameter in DDL, so `conftest`'s
    :data:`LEGAL_DATABASE_NAME` is the thing standing between the name and a SQL
    string. Lowercase and ``[a-z0-9_]`` also keeps it legal unquoted in Postgres
    and free of the characters MongoDB rejects in a database name.
    """
    assert LEGAL_DATABASE_NAME.fullmatch(TEST_DATABASE_NAME), TEST_DATABASE_NAME
    assert len(TEST_DATABASE_NAME) <= 63, len(TEST_DATABASE_NAME)


def test_the_session_url_keeps_the_server_and_replaces_only_the_database() -> None:
    """The credentials, host and port must survive; only the path may change."""
    server = postgres_test_url()
    session = postgres_session_url()
    assert session.startswith(server.rsplit("/", 1)[0] + "/")
    assert session.endswith(f"/{TEST_DATABASE_NAME}")
    assert session != server


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5442/agentprops", "postgresql://u:p@h:5442/{name}"),
        (
            "postgresql://u:p@h:5442/agentprops?sslmode=require",
            "postgresql://u:p@h:5442/{name}?sslmode=require",
        ),
        ("postgresql://u:p@h:5442/agentprops/", "postgresql://u:p@h:5442/{name}"),
    ],
    ids=["plain", "with-query", "trailing-slash"],
)
def test_the_session_url_handles_the_url_shapes_an_override_can_carry(
    monkeypatch: pytest.MonkeyPatch, url: str, expected: str
) -> None:
    """``AGENTPROPS_TEST_POSTGRES_URL`` is a human-typed string.

    The query-parameter case is the one that would break a naive
    ``rpartition("/")``: it would move the database name into the query and
    hand ``CREATE DATABASE`` something that is not a database name.
    """
    monkeypatch.setenv("AGENTPROPS_TEST_POSTGRES_URL", url)
    assert postgres_session_url() == expected.format(name=TEST_DATABASE_NAME)


def bare_prefix_literals(source: str) -> list[int]:
    """Line numbers where ``source`` uses the bare prefix as a **value**.

    An AST walk rather than a grep, and the difference is not pedantry: the
    first version of this guard was a regex over the file text, and it failed on
    this module's own docstring, which quotes
    ``MongoStore(client, "<the bare name>")`` as the mistake to avoid. A guard
    that cannot tell prose from code either has to be exempted from itself - so
    the one file most likely to describe the mistake is the one file not checked
    for it - or it forces the documentation to stop being concrete. Both are
    worse than parsing.

    Docstrings are excluded by position (the first statement of a module, class
    or function); comments never reach the AST at all.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and node.value == TEST_DATABASE_PREFIX
        and id(node) not in docstrings
    ]


def test_no_test_file_uses_the_bare_prefix_as_a_database_name() -> None:
    """The silent direction: one hard-coded name in a suite of unique ones.

    Worse than uniform sharing, because everything passes until two runs overlap
    and then only *those* tests corrupt each other - the
    plausible-failure-count problem R-63 was written about, reintroduced in one
    file and harder to find than before.

    `conftest.py` is where the name is *built*, so the prefix is a value there
    by definition; everywhere else it may only appear inside a longer name.
    """
    offenders = {
        str(path.relative_to(TESTS_DIR)): lines
        for path in sorted(TESTS_DIR.rglob("*.py"))
        if path.name != "conftest.py"
        and (lines := bare_prefix_literals(path.read_text(encoding="utf-8")))
    }
    assert not offenders, (
        f"these files use the bare database name as a value instead of "
        f"conftest.TEST_DATABASE_NAME, so they would collide between concurrent "
        f"runs: {offenders}"
    )


def test_the_bare_prefix_guard_sees_a_value_and_not_a_docstring() -> None:
    """The guard's own losing side, which is why it is an AST walk.

    Verified to fail for the right reason: the middle case is exactly the shape
    that broke the regex version of this guard.
    """
    name = TEST_DATABASE_PREFIX
    assert bare_prefix_literals(f'x = "{name}"') == [1]
    assert bare_prefix_literals(f'"""prose naming {name} as a mistake."""') == []
    assert bare_prefix_literals(f"# a comment naming {name}") == []

    # A docstring and a value in one function, so position is what separates
    # them rather than the file happening to contain only one of the two.
    nested = f'''
def f():
    """doc {name}."""
    return "{name}"
'''
    assert bare_prefix_literals(nested) == [4]
