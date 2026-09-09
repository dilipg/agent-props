"""`agent-props-client`: run ids, step fetching, and the three comparison helpers.

Two halves, and the division is ground rule 2 - "the service never grades ...
the three comparison helpers live in the Python client as pure functions":

**Grading** - :func:`~agentprops_client.compare.exact`,
:func:`~agentprops_client.compare.schema`,
:func:`~agentprops_client.compare.subset` and
:func:`~agentprops_client.compare.grade`. Pure functions over two documents.
No server, no network, no run.

**Running** - :class:`~agentprops_client.run.RunClient` and
:class:`~agentprops_client.run.AsyncRunClient`, over a session from
:mod:`agentprops_client.session`.

Why these names are imported lazily
-----------------------------------

``from agentprops_client import subset`` must not pull in an MCP transport, and
this file is what decides that. Importing :mod:`agentprops_client.session` here
would put ``mcp``'s whole graph - ``anyio``, ``httpx``, ``starlette``,
``socket``, ``ssl`` - behind every ``import agentprops_client.compare`` in the
world, including in a process that has no server to talk to. So
:func:`connect` and :func:`connect_async` arrive through :pep:`562`'s module
``__getattr__`` instead: they work, and they pay for the transport only when
they are asked for.

**Precisely: the lazy import is load-bearing for `session.py` and a
consistency choice for `run.py`.** `run.py` imports no transport of its own -
it takes a callable - so an eager import of it here would cost nothing and
break nothing. That was checked rather than assumed: with
``from agentprops_client.run import RunClient`` planted at the top of this
file, all seventeen assertions in
`tests/unit/test_client_import_isolation.py` still pass, and with
``from agentprops_client.session import connect`` planted there, four of them
fail. The run client is lazy anyway so that one table covers both and a future
transport import inside `run.py` cannot quietly re-enter through this file.

``TYPE_CHECKING`` imports keep the lazy names visible to ``mypy --strict``,
which would otherwise type them ``Any`` and stop checking every caller.

That test file measures all of this in a fresh interpreter rather than trusting
this paragraph, and its positive control asserts the transport module *does*
import ``socket`` - so the guard is known to be able to fail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from agentprops_client.compare import (
    COMPARISON_MODES,
    MODE_EXACT,
    MODE_SCHEMA,
    MODE_SUBSET,
    Comparison,
    Difference,
    exact,
    grade,
    schema,
    subset,
)
from agentprops_client.envelope import Envelope, Finding, ToolError, Warning

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from agentprops_client.run import (
        AsyncRunClient,
        Recorded,
        RunClient,
        RunStart,
        Step,
        new_run_id,
    )
    from agentprops_client.session import connect, connect_async

__all__ = [
    "COMPARISON_MODES",
    "MODE_EXACT",
    "MODE_SCHEMA",
    "MODE_SUBSET",
    "AsyncRunClient",
    "Comparison",
    "Difference",
    "Envelope",
    "Finding",
    "Recorded",
    "RunClient",
    "RunStart",
    "Step",
    "ToolError",
    "Warning",
    "connect",
    "connect_async",
    "exact",
    "grade",
    "new_run_id",
    "schema",
    "subset",
]

#: Which module each lazily-exported name comes from. A table rather than a
#: chain of ``if`` statements so that the set of lazy names is one readable
#: thing, and so :data:`__all__` can be checked against it by a test.
_LAZY: Final[dict[str, str]] = {
    "AsyncRunClient": "run",
    "Recorded": "run",
    "RunClient": "run",
    "RunStart": "run",
    "Step": "run",
    "new_run_id": "run",
    "connect": "session",
    "connect_async": "session",
}


def __getattr__(name: str) -> Any:
    """:pep:`562` lazy export. See the module docstring for why.

    Only the names in :data:`_LAZY` are reachable this way; anything else raises
    ``AttributeError`` as it normally would, so a typo does not import a random
    submodule.
    """
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f"{__name__}.{module}"), name)


def __dir__() -> list[str]:
    """``dir()`` reports the lazy names too, so tab completion is not misleading."""
    return sorted(__all__)
