"""`agent-props-client` is **separately installable**, and this proves it.

The milestone's first word about the client is "separately installable". That is
a claim about *packaging*, so an assertion about imports in this environment
cannot settle it - this environment has the service installed, and a client that
quietly imported ``agentprops`` would work here and fail for every real user.

So the test builds a wheel from `client/python/` and installs it into a
throwaway environment with **no** `agentprops` on the path, then imports the
client and grades a document in that interpreter. Three separate claims fall out
and each is asserted:

1. the packaging **resolves** - metadata, build backend and dependencies;
2. the client **imports** with no service package available;
3. the comparison helpers **work** there, which is what ground rule 2 actually
   promises: someone with two documents, no server and no `agentprops` can grade
   a run.

Why it is skipped rather than xfailed when there is no network
--------------------------------------------------------------

``uv`` resolves the client's dependencies to install it, which needs an index or
a warm cache. On a machine with neither, the build is not a failure of this
repository, so the test **skips with the reason and the command that failed** -
the same contract `tests/integration/conftest.py` holds itself to for an
unreachable Postgres, and for the same reason: a silent absence and a stated one
are different things.

`test_client_import_isolation.py` is the complement and is *not* skippable: it
measures the import graph inside the environment that already exists, so the
no-network claim is asserted on every run regardless.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

import pytest

CLIENT_DIR: Final[Path] = Path(__file__).parents[2] / "client" / "python"
CLIENT_PYPROJECT: Final[Path] = CLIENT_DIR / "pyproject.toml"

#: The distribution name, and it is **not** the module name. ``agent-props-client``
#: installs ``agentprops_client``, the way ``agent-props`` installs
#: ``agentprops`` - so a test that checked only the module would not notice the
#: distribution being renamed.
DISTRIBUTION: Final = "agent-props-client"
MODULE: Final = "agentprops_client"

#: The service's package name, which must **not** be importable in the
#: throwaway environment. That absence is the whole point: it is what makes
#: "separately installable" a measurement rather than a hope.
SERVICE_MODULE: Final = "agentprops"

#: What the probe below runs inside the fresh environment. Deliberately more
#: than an import: it grades a document, so the assertion is that the client
#: *works* there rather than that it merely resolves.
PROBE: Final = """
import importlib.util, json, sys
import agentprops_client
from agentprops_client import grade, subset

verdict = subset({"onboarding_status": "complete"}, {"onboarding_status": "complete", "x": 1})
dispatched = grade("subset", {"a": {"b": 1}}, {"a": {"b": 1, "c": 2}})
json.dump(
    {
        "module": agentprops_client.__name__,
        "graded": bool(verdict.ok),
        "dispatched": bool(dispatched.ok),
        "service_available": importlib.util.find_spec("agentprops") is not None,
        "run_client": agentprops_client.RunClient.__name__,
        "run_module": agentprops_client.RunClient.__module__,
    },
    sys.stdout,
)
"""


def test_the_client_declares_its_own_packaging() -> None:
    """The metadata that makes it a distribution rather than a directory.

    Read from the file rather than from installed metadata, because this is the
    claim that the *source tree* is publishable: a name, a build backend, and a
    wheel target naming the package. Nothing here needs a network.
    """
    metadata = tomllib.loads(CLIENT_PYPROJECT.read_text(encoding="utf-8"))
    assert metadata["project"]["name"] == DISTRIBUTION
    assert metadata["project"]["requires-python"] == ">=3.12"
    assert metadata["build-system"]["build-backend"] == "hatchling.build"
    assert metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [MODULE]
    assert (CLIENT_DIR / MODULE / "py.typed").exists(), (
        "a typed library must ship py.typed or every caller's mypy sees Any"
    )


def test_the_client_does_not_depend_on_the_service() -> None:
    """The dependency that must not be there, checked in the file that would carry it.

    The service and its client are separate distributions: the client speaks
    MCP to a *server*, which may be a different process, a different machine or
    a different language. A path dependency on `agentprops` would make the whole
    milestone's first sentence false, and it would be invisible in this
    repository, where the service is installed anyway.
    """
    metadata = tomllib.loads(CLIENT_PYPROJECT.read_text(encoding="utf-8"))
    declared = " ".join(metadata["project"]["dependencies"])
    assert "agentprops" not in declared, declared
    assert "agent-props" not in declared, declared
    assert "tool" not in metadata or "uv" not in metadata["tool"], (
        "the client must not carry a uv source table pointing at this repository"
    )


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway environment with the client installed and the service absent.

    Module-scoped, because building a wheel and resolving its dependencies is
    the expensive part and every assertion below wants the same environment.

    Installed by **path** rather than with ``-e``: an editable install would
    put this repository's ``client/python`` directory on the path, and a wheel
    is what a real user gets. ``uv pip install --python <venv>`` targets the
    throwaway interpreter explicitly, so nothing about this repository's own
    environment is involved.
    """
    root = tmp_path_factory.mktemp("client-install")
    venv = root / "venv"
    version = f"{sys.version_info.major}.{sys.version_info.minor}"

    # Two steps, run in order rather than built in one list: the interpreter
    # path inside the venv does not exist until `uv venv` has made it, and a
    # list comprehension would resolve it first and fall back to the wrong
    # platform layout. Found by the skip message naming the command, which is
    # the whole reason the message carries it.
    _run([_uv(), "venv", str(venv), "--python", version])
    interpreter = _python(venv)
    assert interpreter.exists(), f"uv venv made no interpreter at {interpreter}"
    _run([_uv(), "pip", "install", "--python", str(interpreter), str(CLIENT_DIR)])
    return interpreter


def _run(command: list[str]) -> None:
    """Run ``command``, or **skip with the command and its error**.

    The contract `tests/integration/conftest.py` holds itself to for an
    unreachable Postgres: a skip that says which command failed and why is a
    different thing from a silent absence, and it is what turns "this needs a
    package index" into something a reader can act on.
    """
    finished = subprocess.run(command, capture_output=True, text=True, check=False)
    if finished.returncode != 0:
        pytest.skip(
            f"could not build the client into a throwaway environment; "
            f"`{' '.join(command)}` failed with: {finished.stderr.strip()[-400:]}"
        )


def _uv() -> str:
    """The ``uv`` on the path. The tool this project is already run with."""
    return "uv"


def _python(venv: Path) -> Path:
    """The interpreter inside ``venv``, on either platform layout."""
    for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return venv / "bin" / "python"


def test_the_installed_client_imports_and_grades_without_the_service(installed: Path) -> None:
    """**The evidence for "separately installable".** No `agentprops` anywhere.

    Four assertions, and the third is the one that makes the other three mean
    something: ``service_available`` must be ``False``, or this proves only that
    the client works in an environment that also has the service - which is
    what running the probe in *this* interpreter would have proved.
    """
    finished = subprocess.run(
        [str(installed), "-c", PROBE], capture_output=True, text=True, check=False
    )
    assert finished.returncode == 0, finished.stderr
    report = json.loads(finished.stdout)
    assert report["module"] == MODULE
    assert report["service_available"] is False, (
        "the throwaway environment can import the service package, so this test proves nothing "
        "about the client being separately installable"
    )
    assert report["graded"] is True, "the comparison helpers do not work in a fresh install"
    assert report["dispatched"] is True, "grade() does not dispatch in a fresh install"
    assert report["run_client"] == "RunClient", (
        "the lazy __getattr__ did not resolve the run client in a fresh install"
    )
    assert report["run_module"] == f"{MODULE}.run"


def test_the_installed_distribution_is_named_and_versioned(installed: Path) -> None:
    """The distribution metadata as *installed*, not as declared.

    A wheel that built from the wrong directory, or a ``packages`` target with a
    typo, would still import in an editable checkout and would produce the wrong
    thing here.
    """
    finished = subprocess.run(
        [
            str(installed),
            "-c",
            "import importlib.metadata as m, json; "
            f"json.dump({{'version': m.version('{DISTRIBUTION}'), "
            f"'requires': m.requires('{DISTRIBUTION}')}}, __import__('sys').stdout)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    report = json.loads(finished.stdout)
    assert report["version"] == "0.1.0"
    assert not any("agentprops" in requirement for requirement in report["requires"]), (
        f"the installed client requires the service: {report['requires']}"
    )


def test_the_transport_works_in_the_fresh_install_too(installed: Path) -> None:
    """The other half: the client is not *only* a grading library.

    ``connect`` has to be reachable and its transport importable in a plain
    install, or "separately installable" would be true of half the package. This
    imports the transport module rather than connecting to anything - there is
    no server in that environment, and the point is the packaging.
    """
    finished = subprocess.run(
        [
            str(installed),
            "-c",
            "import agentprops_client.session as s, json, sys; "
            "json.dump({'connect': callable(s.connect), "
            "'connect_async': callable(s.connect_async)}, sys.stdout)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout) == {"connect": True, "connect_async": True}
