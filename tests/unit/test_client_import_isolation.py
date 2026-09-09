"""M8's third clause: importing the compare module pulls in no network dependency.

Ground rule 2 is why this file exists. "The service never grades ... the three
comparison helpers live in the Python client as pure functions." Someone
grading a stored run - in CI, in a notebook, from a JSON file a colleague sent
- has two documents and no server, and must not need one. That is only a real
property if the *import graph* says so, and the only way to know is to measure.

Not an inspection, and not a list of today's network libraries
--------------------------------------------------------------

The obvious guard is "assert ``mcp`` and ``httpx`` and ``socket`` are not
imported". It is weaker than it looks: it holds only against the libraries whose
names someone thought of, and the next transport dependency is by definition the
one that was not on the list.

So the guard is an **allowlist over the whole transitive import set**, and the
allowlist is *measured* rather than typed:

    modules(import agentprops_client.compare) - modules(import jsonschema)
        must be the client's own modules and nothing else

``jsonschema`` is the client's one pure runtime dependency - ``compare.py`` needs
a Draft 2020-12 validator and nothing else -
and :func:`test_the_pure_dependency_is_declared` ties that name to
`client/python/pyproject.toml` so it cannot drift into a claim about a
dependency the package no longer has. Anything new that appears in ``compare``'s
graph - ``mcp``, ``httpx``, ``requests``, ``aiohttp``, ``socket``, a library
nobody has heard of yet - shows up in that difference and fails, **named**, with
no edit to this file.

Each measurement runs in a **fresh subprocess**, because this pytest process has
already imported ``mcp`` (the server's own suite uses it) and ``sys.modules`` in
here would prove nothing at all.

The positive control, which is what makes the guard honest
----------------------------------------------------------

:func:`test_the_transport_module_does_import_a_network_stack` asserts that
importing ``agentprops_client.session`` **does** bring in ``socket``, ``ssl``
and ``mcp``. Without it, every assertion above would still pass if the
measurement were broken, if the subprocess silently failed, or if ``socket``
were somehow unreachable in this environment - which is the "something can pass
while proving nothing" failure four milestones of this build have paid for.

``socket`` is the sentinel because it is the *chokepoint*: no stdlib network path
reaches the OS without it - ``http.client``, ``urllib.request``, ``asyncio``,
``smtplib``, ``socketserver`` and ``ssl`` all import it - so naming it is not
the same as naming today's libraries. ``_socket``, the C extension, is
deliberately **not** the sentinel and that is worth knowing: ``typing_extensions``
imports it to read a C-API capsule, so a guard on ``_socket`` would fail today
because of a *typing* library. Measured, not assumed - see
:func:`test_the_c_extension_is_not_the_sentinel_and_here_is_why`. The allowlist
is what bounds the code that could *use* it.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Final

import pytest

CLIENT_DIR: Final[Path] = Path(__file__).parents[2] / "client" / "python"
CLIENT_PACKAGE: Final[Path] = CLIENT_DIR / "agentprops_client"
CLIENT_PYPROJECT: Final[Path] = CLIENT_DIR / "pyproject.toml"

#: The client's one **pure** runtime dependency: `compare.py` needs a Draft
#: 2020-12 validator. Everything the grading path is allowed to import is
#: whatever importing this alone imports - measured, not enumerated.
#: :func:`test_the_pure_dependency_is_declared` checks the name against the
#: package's own metadata.
PURE_DEPENDENCY: Final = "jsonschema"

#: The client's transport dependencies. Named here only so
#: :func:`test_the_pure_dependency_is_declared` can assert the declared set is
#: exactly these plus :data:`PURE_DEPENDENCY` - a *third* transport dependency
#: appearing must be a visible decision, because it widens what the grading path
#: could accidentally reach through.
TRANSPORT_DEPENDENCIES: Final[frozenset[str]] = frozenset({"mcp", "anyio"})

#: The stdlib door to the OS network stack. See the module docstring for why
#: this is a chokepoint rather than a name off a list, and why the C extension
#: below is not it.
SOCKET_MODULE: Final = "socket"

#: The modules the grading path is allowed to add on top of the pure
#: dependency's graph: its own. Named as a prefix, so a new client module is
#: covered without an edit - and a *third-party* module is not.
CLIENT_PREFIX: Final = "agentprops_client"


def modules_after(statement: str) -> frozenset[str]:
    """Every module in ``sys.modules`` after ``statement``, in a fresh interpreter.

    A subprocess, not this process: pytest has already imported ``mcp``,
    ``pydantic`` and half the server, so an in-process measurement would report
    the suite's imports and prove nothing.

    An **audit hook** is installed before the statement runs and fails the
    import on any socket, SSL or urllib-request audit event. That covers what
    the module list cannot: a module that opens a connection *at import time*
    through the C extension directly, without ``socket`` ever appearing.
    """
    probe = (
        "import sys, json\n"
        "def _hook(event, arguments):\n"
        "    if event.startswith(('socket.', 'ssl.')) or event == 'urllib.Request':\n"
        "        raise RuntimeError('network audit event during import: ' + event)\n"
        "sys.addaudithook(_hook)\n"
        f"{statement}\n"
        "json.dump(sorted(sys.modules), sys.stdout)\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert finished.returncode == 0, (
        f"`{statement}` failed in a fresh interpreter: {finished.stderr}"
    )
    return frozenset(json.loads(finished.stdout))


@cache
def baseline() -> frozenset[str]:
    """What a bare interpreter has imported. Subtracted so the sets are readable."""
    return modules_after("pass")


@cache
def added_by(statement: str) -> frozenset[str]:
    """The modules ``statement`` adds on top of :func:`baseline`."""
    return modules_after(statement) - baseline()


def third_party(names: Iterable[str]) -> set[str]:
    """The top-level non-stdlib modules in ``names``.

    ``sys.stdlib_module_names`` is the real surface for "is this the standard
    library", so the split is derived rather than declared.
    """
    return {
        name.split(".")[0]
        for name in names
        if name.split(".")[0] not in sys.stdlib_module_names and not name.startswith("_")
    }


def test_the_measurement_works_at_all() -> None:
    """The guard's own guard: a broken probe must not read as a clean import graph.

    Three ways this file could pass while proving nothing - the subprocess
    failing, the baseline coming back empty, or the client not being importable
    at all - and all three are excluded here rather than assumed.
    """
    assert baseline(), "the baseline measurement came back empty"
    grading = added_by("import agentprops_client.compare")
    assert f"{CLIENT_PREFIX}.compare" in grading, (
        "the compare module was not imported by the probe, so every assertion below is vacuous"
    )
    assert SOCKET_MODULE in sys.stdlib_module_names, "the sentinel is not a stdlib module"


def test_importing_compare_adds_nothing_beyond_the_pure_dependencys_graph() -> None:
    """**M8's third clause.** The whole transitive import set, against an allowlist.

    Measured both ways in one interpreter each: what ``jsonschema`` alone
    imports, and what ``agentprops_client.compare`` imports. The difference must
    be the client's own modules - so any new third-party module, network or not,
    fails here by name without this file knowing what it is called.
    """
    grading = added_by("import agentprops_client.compare")
    pure = added_by(f"import {PURE_DEPENDENCY}")
    extra = {name for name in grading - pure if not name.startswith(CLIENT_PREFIX)}
    assert not extra, (
        f"importing agentprops_client.compare pulls in modules that {PURE_DEPENDENCY} does not: "
        f"{sorted(extra)}. The comparison helpers must be usable with no server and no network "
        f"(ground rule 2)."
    )


def test_importing_compare_pulls_in_no_socket() -> None:
    """The same claim named explicitly, at the chokepoint.

    Redundant against the assertion above and kept anyway, because a reader
    looking for "does this import ``socket``" should find that sentence as an
    assertion rather than have to derive it from a set difference.
    :func:`test_the_transport_module_does_import_a_network_stack` is what proves
    this one can fail.
    """
    grading = added_by("import agentprops_client.compare")
    assert SOCKET_MODULE not in grading
    assert "ssl" not in grading
    outside = third_party(grading) - third_party(added_by(f"import {PURE_DEPENDENCY}"))
    assert outside == {CLIENT_PREFIX}, (
        f"the grading path reaches third-party modules jsonschema does not: {sorted(outside)}"
    )


def test_importing_the_package_root_pulls_in_no_network_dependency() -> None:
    """``from agentprops_client import subset`` must not pay for a transport.

    This is what the lazy ``__getattr__`` in the package's ``__init__`` is for:
    ``import agentprops_client`` re-exports the comparison helpers eagerly and
    the run client not at all, so the *package* is as clean as the module. A
    plain ``from agentprops_client.run import RunClient`` at the top of that file
    would fail here and nowhere else.
    """
    root = added_by("import agentprops_client")
    assert SOCKET_MODULE not in root
    assert "mcp" not in root
    pure = added_by(f"import {PURE_DEPENDENCY}")
    extra = {name for name in root - pure if not name.startswith(CLIENT_PREFIX)}
    assert not extra, f"the package root imports {sorted(extra)}"


def test_importing_the_run_module_pulls_in_no_network_dependency() -> None:
    """The run client is transport-agnostic too, which is a stronger claim than asked for.

    `run.py` takes a callable rather than a session, so request shaping and
    response reading are testable - and usable - without a transport. Only
    `session.py` imports one. Asserting it here is what stops a future
    convenience import (``from mcp import Client`` for a type annotation, say)
    from quietly moving the boundary.
    """
    running = added_by("import agentprops_client.run")
    assert SOCKET_MODULE not in running
    assert "mcp" not in running and "anyio" not in running


def test_the_lazy_export_still_works() -> None:
    """The lazy ``__getattr__`` must actually resolve, or the ergonomics are a lie.

    In *this* process, where importing a transport is free. Two things: the name
    resolves to the same object as the submodule's, and an unknown name still
    raises ``AttributeError`` rather than importing something at random.
    """
    import agentprops_client
    from agentprops_client.run import AsyncRunClient, RunClient

    assert agentprops_client.RunClient is RunClient
    assert agentprops_client.AsyncRunClient is AsyncRunClient
    assert callable(agentprops_client.connect)
    with pytest.raises(AttributeError):
        agentprops_client.no_such_name  # noqa: B018 - the attribute access *is* the assertion


def test_every_public_name_is_reachable() -> None:
    """``__all__`` is not a wish list: every name in it must resolve.

    The lazy path is exactly where a typo survives, because a name that is only
    in ``__all__`` and in the ``TYPE_CHECKING`` block type-checks fine and
    ``AttributeError``s at runtime.
    """
    import agentprops_client

    for name in agentprops_client.__all__:
        assert getattr(agentprops_client, name, None) is not None, f"{name} does not resolve"


# -------------------------------------------------- the guard's positive control


def test_the_transport_module_does_import_a_network_stack() -> None:
    """**The positive control.** The sentinel must be able to fire.

    Every assertion above says "``socket`` is not here". This one says where it
    *is*: importing ``agentprops_client.session`` brings in ``socket``, ``ssl``
    and ``mcp``. Without this, a measurement that silently returned an empty set,
    or an environment where the network stack was unreachable, would make the
    whole file pass while proving nothing - which is the failure four rounds of
    this build have paid for.
    """
    transport = added_by("import agentprops_client.session")
    assert SOCKET_MODULE in transport, (
        "the transport module does not import socket, so the sentinel above proves nothing"
    )
    assert "ssl" in transport
    assert "mcp" in transport
    assert third_party(transport) >= TRANSPORT_DEPENDENCIES


def test_the_allowlist_would_catch_a_planted_network_import() -> None:
    """The other half of the control: the *comparison* would catch it.

    A module inside the client package that imports the transport, measured the
    same way and passed through the same allowlist. This is the assertion that
    says the difference-of-sets mechanism works, rather than that ``socket``
    happens to be absent today - and it is why a planted ``import socket`` in
    `compare.py` fails
    :func:`test_importing_compare_adds_nothing_beyond_the_pure_dependencys_graph`.
    """
    planted = added_by("import agentprops_client.session")
    pure = added_by(f"import {PURE_DEPENDENCY}")
    extra = {name for name in planted - pure if not name.startswith(CLIENT_PREFIX)}
    assert extra, "the allowlist finds nothing even for the transport module"
    assert "mcp" in third_party(extra)
    assert SOCKET_MODULE in extra


def test_the_c_extension_is_not_the_sentinel_and_here_is_why() -> None:
    """``_socket`` is present in the grading graph, and that does not weaken the claim.

    Measured rather than reasoned about: ``typing_extensions`` - a pure typing
    library, and a legitimate transitive dependency of ``jsonschema`` - imports
    ``_socket`` to read a C-API capsule. So a guard on ``_socket`` would fail
    today for a reason that has nothing to do with networking, which is exactly
    the kind of guard that gets deleted rather than fixed.

    ``socket`` is the sentinel instead, and the allowlist is what bounds the code
    that could reach the primitives ``_socket`` exposes: nothing in the grading
    graph is outside ``jsonschema``'s own closure. The audit hook in
    :func:`modules_after` closes the remaining gap by failing on socket *use* at
    import time, whichever module reaches it.
    """
    grading = added_by("import agentprops_client.compare")
    pure = added_by(f"import {PURE_DEPENDENCY}")
    assert "_socket" in pure, (
        "the premise has changed: jsonschema's graph no longer imports _socket, so the sentinel "
        "could be tightened to it"
    )
    assert "_socket" in grading, "if this is gone, so is the reason not to assert on it"
    assert "typing_extensions" in pure


# ------------------------------------------------------ the static half (AST)


def client_modules() -> dict[str, ast.Module]:
    """Every module in the client package, parsed. Keyed by module name."""
    return {
        path.stem: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(CLIENT_PACKAGE.glob("*.py"))
    }


def imported_top_levels(tree: ast.Module) -> set[str]:
    """The top-level module name of every import in ``tree``, either spelling."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def intra_package_imports(tree: ast.Module) -> set[str]:
    """The client's own submodules ``tree`` imports, as bare module names."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(CLIENT_PREFIX)
        ):
            parts = node.module.split(".")
            if len(parts) > 1:
                found.add(parts[1])
        elif isinstance(node, ast.Import):
            found.update(
                alias.name.split(".")[1]
                for alias in node.names
                if alias.name.startswith(f"{CLIENT_PREFIX}.") and "." in alias.name
            )
    return found


def static_closure(start: str) -> set[str]:
    """``start`` plus every client module reachable from it by a static import."""
    modules = client_modules()
    assert start in modules, f"{start} is not a module of the client package"
    seen: set[str] = set()
    stack = [start]
    while stack:
        name = stack.pop()
        if name in seen or name not in modules:
            continue
        seen.add(name)
        stack.extend(intra_package_imports(modules[name]))
    return seen


def test_the_client_module_table_is_not_empty() -> None:
    """`test_layering.py`'s first guard, applied here: an empty scan proves nothing."""
    modules = client_modules()
    assert {"compare", "run", "session", "envelope", "__init__"} <= set(modules), sorted(modules)


@pytest.mark.parametrize("start", ["compare", "run", "envelope"])
def test_the_pure_modules_static_closure_reaches_no_transport(start: str) -> None:
    """The AST half, in `test_layering.py`'s shape: fast, and it names the line.

    Weaker than the subprocess measurement - it cannot see a dynamic import or
    a side effect - and worth having anyway, because it fails in milliseconds
    and blames a module rather than a set difference. Both halves are here for
    the reason `test_layering.py` keeps its call guard *and* its import guard.

    Every module reachable from ``start`` through the client's own imports must
    import only the standard library, the pure dependency, and the client
    itself.
    """
    modules = client_modules()
    offences: dict[str, set[str]] = {}
    for name in static_closure(start):
        outside = {
            imported
            for imported in imported_top_levels(modules[name])
            if imported not in sys.stdlib_module_names
            and imported != PURE_DEPENDENCY
            and imported != CLIENT_PREFIX
        }
        if outside:
            offences[name] = outside
    assert not offences, (
        f"the static closure of {start}.py imports outside the standard library and "
        f"{PURE_DEPENDENCY}: {offences}"
    )


def test_the_static_guard_would_catch_a_planted_transport_import() -> None:
    """The static guard's own control, on the module that really does import one.

    ``session.py`` is the planted case that exists on purpose, so this needs no
    temporary file: its closure must report ``mcp`` and ``anyio``. A guard that
    reported nothing for `session.py` would report nothing for a `compare.py`
    that imported the same thing.
    """
    modules = client_modules()
    outside = {
        imported
        for name in static_closure("session")
        for imported in imported_top_levels(modules[name])
        if imported not in sys.stdlib_module_names
        and imported not in {PURE_DEPENDENCY, CLIENT_PREFIX}
    }
    assert outside >= TRANSPORT_DEPENDENCIES, outside


def test_the_static_closure_is_transitive() -> None:
    """It follows imports rather than checking one file.

    ``compare`` imports nothing of the client's own, ``run`` imports
    ``envelope``, and ``session`` imports ``run`` - so a transport import added
    to `envelope.py` would be caught by ``run``'s parametrised case, not only by
    ``envelope``'s.
    """
    assert static_closure("compare") == {"compare"}
    assert static_closure("run") == {"run", "envelope"}
    assert {"run", "envelope"} <= static_closure("session")


# ------------------------------------------------------------- the packaging


def declared_dependencies() -> set[str]:
    """The client's declared runtime dependencies, by distribution name.

    Read from its own `pyproject.toml`, which is the real surface: this file's
    allowlist is a claim *about* that list, and a claim about a stale list is
    worth nothing.
    """
    metadata = tomllib.loads(CLIENT_PYPROJECT.read_text(encoding="utf-8"))
    requirements: list[str] = metadata["project"]["dependencies"]
    named = (re.match(r"[A-Za-z0-9._-]+", requirement.strip()) for requirement in requirements)
    return {match.group(0) for match in named if match is not None}


def test_the_pure_dependency_is_declared() -> None:
    """:data:`PURE_DEPENDENCY` names a real dependency of the client, and the set is closed.

    The allowlist above is "whatever ``jsonschema`` imports", which is only a
    meaningful bound while ``jsonschema`` is what the grading path actually
    depends on. And the declared set must be exactly the pure one plus the two
    transport ones: a third dependency arriving is a decision about what the
    grading path could reach, so it belongs here rather than passing silently.
    """
    declared = declared_dependencies()
    assert PURE_DEPENDENCY in declared, f"{PURE_DEPENDENCY} is not declared: {sorted(declared)}"
    assert declared == {PURE_DEPENDENCY} | TRANSPORT_DEPENDENCIES, (
        f"the client's dependencies changed to {sorted(declared)}; the allowlist in this file is "
        f"a claim about that list"
    )
