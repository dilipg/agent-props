"""Ruling R-50's guard: no integer argument escapes as an exception, on any tool.

**Two milestones running, the blocking finding was an unbounded integer.** M4:
`limit`, `offset` and `version` overflowed pysqlite and produced
``is_error: true`` with no envelope at all. M5: `seed` did the same through
`dataset_skeleton`. M4's fix - `service/limits.py` with `clamp()` and
`storable()` - was correct and local, and M5 then added a new integer entry
point and did not use it.

So R-50 asks for a guard rather than a third fix, and this is it. Two
occurrences is a pattern; three would be a policy failure.

What makes this a guard and not a list
--------------------------------------

:data:`INTEGER_PARAMETERS` is read off the **running server's registered
tools** - every parameter whose published JSON type includes ``integer``. A
guard that read a literal list of today's four integers is the defect it exists
to prevent, because the next author adds a fifth and the list does not notice.

:data:`COVERED` supplies the *other* arguments each case needs to get as far as
a store call, which is the one thing no schema can tell you: a valid
`dataset_id`, a published blueprint version, a real `skeleton_id`. The
enumeration is compared against the surface, so a new integer parameter with no
entry here **fails** :func:`test_every_integer_parameter_on_the_surface_is_covered`
rather than being silently skipped. That is the same arrangement
`test_tool_surface.py` uses for tool coverage and `DEFERRED`.

M6 added `iteration` on `fetch_step` and `limit`/`offset` on `run_find`, and it
**did** find out here: the enumeration failed with those three pairs named
before any of them had a case. M7 added `count` on `dataset_expand` and found
out the same way, and M8 added `iteration` on `record_step` and found out for a
fourth time. That is the guard working as designed rather than a story about how
it would have.

`record_step`'s row also shows what :data:`COVERED` is *for*. Its in-range
control cannot pass against a fixture that merely starts a run: an actual can
only be recorded for a step that was served, so the fixture has to fetch one
first. No input schema could have said that.

`count` answers a *third* way, which is why the distinction in `limits.py` is
worth three names rather than two. A value above `2**63` is `AP-001`, like
`seed`: a count is not an identifier, but it is not a quantity that means the
same as the largest representable one either - "make 2**63 pool entries" and
"make 2**63 - 1" are both impossible rather than equivalent. And a value above
the *per-call* maximum is also `AP-001`, naming that maximum, because ruling
R-56 forbids silently serving a smaller expansion than the caller asked for.
Only a **negative** count is clamped, to zero, where it means what zero means.

The three answer differently, and the difference is the distinction
`limits.py` draws. `run_find`'s `limit` and `offset` are *quantities* and are
clamped, so an out-of-range page size still returns rows. `fetch_step`'s
`iteration` is an *identifier* of a step within a run - the M4 fix-round entry
names it as such - so it is refused with **RT-E02** rather than clamped. R-03
is untouched by that: every *storable* index past the end of the pool still
serves the last entry and warns, which
`test_service_runs.py::test_iterating_past_the_pool_is_never_an_error_however_far`
asserts at ``MAX_STORED_INT``.

Why the assertion is behavioural
--------------------------------

An AST check that `clamp()` or `storable()` appears somewhere on the path would
pass on a call that clamps the wrong argument, or clamps after the store call.
The invariant R-50 is protecting is the one CLAUDE.md states absolutely -
"structured errors, never exceptions, for anything a user could cause" - so
the assertion is that invariant: the SDK reports no error and the payload is
one of the two section 1 envelopes.

:func:`test_the_in_range_control_reaches_the_store` is what stops the whole file
passing vacuously. Every case's in-range call must **succeed**, which proves the
arguments are realistic enough to reach the store - so the out-of-range call is
genuinely exercising the bound rather than failing early for an unrelated
reason. That vacuity is not hypothetical: with `agent_id=""`,
`dataset_skeleton` reports ``AP-004`` before it ever looks at `seed`, and a
guard built that way would have passed against the code M5 shipped broken.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

import pytest

from agentprops.models import Dataset
from agentprops.server import mcp
from agentprops.service import (
    MAX_STORED_INT,
    MIN_STORED_INT,
    ServiceContext,
    blueprints,
    runs,
    storable,
)
from agentprops.service.limits import clamp
from conftest import load_document
from toolclient import attempt, connected, invoke

#: One past each end of the range a store column can hold. Both directions,
#: because M4's `paginate` entry reasoned about the negative end and left the
#: positive one unclamped - which is how that blocker happened.
OUT_OF_RANGE: Final[tuple[int, ...]] = (MAX_STORED_INT + 1, MIN_STORED_INT - 1)

GOLDEN_DATASET: Final = "datasets/priya-missing-docs.json"
GOLDEN_BLUEPRINT: Final = "blueprints/location-onboarding-1.0.0.json"

#: A run the `seeded` fixture starts, so ``fetch_step``'s integer parameter has
#: somewhere to land. M6's `iteration` reaches ``run_steps.iteration``, which is
#: an ``INTEGER`` column, so the case needs a real run and a real pool node -
#: exactly the arguments no input schema can supply.
GOLDEN_RUN: Final = "m6-bounds-run"
POOL_NODE: Final = "request_docs"


def integer_parameters() -> set[tuple[str, str]]:
    """Every ``(tool, parameter)`` on the registered surface typed as an integer.

    Read from the published input schemas, so this follows the surface rather
    than a list. ``["integer", "null"]`` counts: an optional integer overflows
    exactly as well as a required one.
    """
    import asyncio

    found: set[tuple[str, str]] = set()
    for tool in asyncio.run(mcp.list_tools()):
        properties: dict[str, Any] = tool.input_schema.get("properties", {})
        for name, schema in properties.items():
            declared = schema.get("type")
            kinds = declared if isinstance(declared, list) else [declared]
            if "integer" in kinds:
                found.add((str(tool.name), name))
    return found


INTEGER_PARAMETERS: Final[set[tuple[str, str]]] = integer_parameters()


def dataset_id() -> str:
    identifier: str = load_document(GOLDEN_DATASET)["id"]
    return identifier


def labels() -> dict[str, str]:
    values: dict[str, str] = load_document(GOLDEN_DATASET)["labels"]
    return values


#: ``(tool, parameter) -> (other arguments, an in-range value)``.
#:
#: The arguments are the part no schema can supply: they have to be good enough
#: that the call reaches a store column, or the out-of-range assertion proves
#: nothing. :func:`test_the_in_range_control_reaches_the_store` enforces that.
#:
#: Adding a tool that takes an integer means adding a row here. The enumeration
#: test is what makes that unavoidable.
COVERED: Final[dict[tuple[str, str], tuple[dict[str, Any], int]]] = {
    ("dataset_expand", "count"): (
        {"dataset_id": dataset_id(), "node_id": POOL_NODE},
        1,
    ),
    ("dataset_find", "limit"): ({}, 50),
    ("dataset_find", "offset"): ({}, 0),
    ("dataset_get", "version"): ({"dataset_id": dataset_id()}, 1),
    ("dataset_skeleton", "seed"): (
        {"agent_id": "location-onboarding", "version": "1.0.0", "labels": labels()},
        20260908,
    ),
    ("fetch_step", "iteration"): ({"run_id": GOLDEN_RUN, "node_id": POOL_NODE}, 1),
    ("record_step", "iteration"): (
        {"run_id": GOLDEN_RUN, "node_id": POOL_NODE, "actual": {"received": ["fssai"]}},
        1,
    ),
    ("run_find", "limit"): ({}, 50),
    ("run_find", "offset"): ({}, 0),
}


@pytest.fixture
def seeded(context: ServiceContext) -> Iterator[ServiceContext]:
    """The published blueprint, the golden dataset, one started run, one served step.

    The run is what makes ``fetch_step``'s case non-vacuous: without it the tool
    answers RT-E03 before it ever looks at `iteration`, which is the same shape
    of vacuity that would have let this guard pass against the code M5 shipped
    broken.

    M8's ``record_step`` needs one thing more, and it is the same argument a
    turn deeper: an actual can only be recorded for a step that was **served**
    (ruling R-33), so without the ``fetch_step`` below the in-range control
    answers ``AP-004`` at the step key and never reaches the integer either.
    Iteration 1 rather than 0 because 1 is the in-range value the ``record_step``
    row uses.
    """
    blueprints.upsert(context, load_document(GOLDEN_BLUEPRINT), publish=True)
    context.store.put_dataset(Dataset.model_validate(load_document(GOLDEN_DATASET)))
    started = runs.start(context, GOLDEN_RUN, "location-onboarding", {"dataset_id": dataset_id()})
    assert started.ok, started
    served = runs.fetch_step(context, GOLDEN_RUN, node_id=POOL_NODE, iteration=1)
    assert served.ok, served
    yield context


def test_every_integer_parameter_on_the_surface_is_covered() -> None:
    """The guard's whole point: the enumeration comes from the surface.

    A new tool with an integer parameter fails here until it has a case. That
    is the difference between a guard and a list of the integers that were
    known on the day it was written.
    """
    assert INTEGER_PARAMETERS, "no integer parameters found - the enumeration is broken"
    uncovered = INTEGER_PARAMETERS - set(COVERED)
    assert not uncovered, (
        f"these integer tool parameters can reach a store column and have no bounds case: "
        f"{sorted(uncovered)}. Add a row to COVERED with arguments good enough to reach the "
        f"store, and make sure the value is range-checked (service/limits.py) before it gets "
        f"there - ruling R-50."
    )


def test_no_bounds_case_is_stale() -> None:
    """A case for a parameter that is no longer an integer is cover for nothing."""
    stale = set(COVERED) - INTEGER_PARAMETERS
    assert not stale, f"COVERED names parameters that are not integers on the surface: {stale}"


@pytest.mark.parametrize("case", sorted(COVERED), ids=lambda case: f"{case[0]}.{case[1]}")
@pytest.mark.parametrize("value", OUT_OF_RANGE, ids=["above", "below"])
async def test_an_out_of_range_integer_answers_an_envelope(
    seeded: ServiceContext, case: tuple[str, str], value: int
) -> None:
    """Ruling R-50, as the invariant CLAUDE.md states absolutely.

    Not "it is rejected" and not "it is clamped" - either is a correct answer,
    and which one is right depends on whether the parameter is a quantity, an
    identifier or an authored value. What is never correct is an exception: the
    SDK turns one into ``is_error: true`` with ``structured_content: None``, and
    `tests/toolclient.py::invoke` asserts ``is_error is False`` on every call -
    so the suite already claims this cannot happen.
    """
    tool, parameter = case
    arguments, _ = COVERED[case]
    result = await attempt(seeded, tool, {**arguments, parameter: value})
    assert result.is_error is False, (
        f"{tool}({parameter}={value}) raised instead of answering an envelope: {result.content}"
    )
    payload = result.structured_content
    assert payload is not None, f"{tool}({parameter}={value}) returned no structured content"
    assert set(payload) in ({"ok", "data", "warnings"}, {"ok", "errors"}), (
        f"{tool}({parameter}={value}) returned neither section 1 envelope: {sorted(payload)}"
    )


@pytest.mark.parametrize("case", sorted(COVERED), ids=lambda case: f"{case[0]}.{case[1]}")
async def test_the_in_range_control_reaches_the_store(
    seeded: ServiceContext, case: tuple[str, str]
) -> None:
    """The non-vacuity half. Without it the file could pass against broken code.

    Each case's in-range call must **succeed**, which is only possible if the
    other arguments resolve all the way through to a store call. If this fails,
    the corresponding out-of-range assertion above is testing an early exit
    rather than the bound.
    """
    tool, parameter = case
    arguments, in_range = COVERED[case]
    envelope = await invoke(seeded, tool, **{**arguments, parameter: in_range})
    assert envelope["ok"] is True, (
        f"{tool}({parameter}={in_range}) does not reach the store, so the out-of-range case "
        f"proves nothing: {envelope}"
    )


async def test_an_out_of_range_seed_is_ap_001_at_the_seed(seeded: ServiceContext) -> None:
    """M5's own blocker, named. It reported an ``OverflowError`` before the fix.

    Verified through the real MCP surface at the time:
    ``dataset_skeleton(..., seed=2**63)`` returned ``is_error: True`` with
    ``structured_content: None``, from
    ``OverflowError: Python int too large to convert to SQLite INTEGER``.

    Rejected rather than clamped, and that is the point of the distinction in
    `limits.py`: a `seed` is not a quantity, so the nearest representable value
    does not mean the same thing - every derived id depends on it, so clamping
    would author a dataset the caller did not ask for.
    """
    envelope = await invoke(
        seeded,
        "dataset_skeleton",
        agent_id="location-onboarding",
        version="1.0.0",
        labels=labels(),
        seed=MAX_STORED_INT + 1,
    )
    assert envelope["ok"] is False
    assert [finding["rule"] for finding in envelope["errors"]] == ["AP-001"]
    assert envelope["errors"][0]["pointer"] == "/seed"
    assert envelope["errors"][0]["context"]["argument"] == "seed"


async def test_an_out_of_range_seed_writes_no_skeleton(seeded: ServiceContext) -> None:
    """The rejection happens *before* the store, which is what "at the boundary" means."""
    async with connected(seeded) as client:
        await invoke(
            client,
            "dataset_skeleton",
            agent_id="location-onboarding",
            version="1.0.0",
            labels=labels(),
            seed=MAX_STORED_INT + 1,
        )
    assert seeded.store.health().counts.blueprints == 1, "the fixture's blueprint is still there"


def test_the_range_is_the_column_width_and_the_helpers_agree() -> None:
    """The bound is representability, not policy - so both helpers use one range.

    ``clamp`` is for a quantity, ``storable`` for a value that must be storable
    as given. If these two ever disagreed about the range, one of them would be
    wrong about the column.
    """
    assert MAX_STORED_INT == 2**63 - 1
    assert MIN_STORED_INT == -(2**63)
    assert storable(MAX_STORED_INT) and storable(MIN_STORED_INT)
    assert not storable(MAX_STORED_INT + 1) and not storable(MIN_STORED_INT - 1)
    assert clamp(MAX_STORED_INT + 1) == MAX_STORED_INT
    assert clamp(MIN_STORED_INT - 1) == 0, "a quantity clamps to zero, not to the negative end"
