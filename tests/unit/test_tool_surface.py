"""The surface's drift guards: what is registered, what is documented, what is tested.

`test_validation_drift.py` is the pattern being copied, and the reasoning
transfers exactly. That file exists because a rule catalogue kept in prose and a
registry kept in code will diverge unless a test compares them; a *tool*
surface, a documented tool table and a test suite have the same problem.

Four guards:

``test_every_registered_tool_is_documented``
    The running ``MCPServer``'s tool names are a subset of the names
    `docs/contracts.md` section 4 tabulates. A tool with a typo in its name, or
    one invented without a contract, fails here.

``test_every_documented_tool_is_registered_or_deferred``
    The other direction, scoped to the milestone by :data:`DEFERRED`. Each
    entry names the milestone that owns the tool, so M5 through M10 land by
    *deleting* entries - and a deferred tool that quietly appears on the
    surface fails ``test_no_deferral_is_stale``.

``test_every_registered_tool_answers_an_envelope``
    Runtime, and fully mechanical: arguments are synthesised from each tool's
    own published input schema, so this covers a new tool with no test change
    at all. It asserts the two properties every tool shares - the SDK does not
    report an error, and the payload is one of the two section 1 envelopes.

    The envelope assertion is deliberately the *real* invariant and not the
    stricter one that happens to hold here. An earlier version asserted
    ``{ok, data, warnings}`` whenever ``ok`` was true, which passed only because
    :func:`sample` synthesises arguments that make both ``*_validate`` tools
    fail - and those two legitimately return ``{ok, errors}`` with ``ok: true``
    on a clean document (ruling R-13, and ``ErrorEnvelope``'s own docstring). A
    reader at M5 would have taken the assertion for the contract and been wrong.

``test_every_registered_tool_has_a_dedicated_test``
    Reads the AST of `tests/` and collects every tool name passed to a
    ``call_tool``/``invoke``/``attempt`` call. This is the "at least one
    exercising test per tool" gate the M4 acceptance criteria ask for,
    mechanically rather than as a list maintained by hand.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any, Final

import pytest
from mcp_types import Tool as RegisteredTool

from agentprops.server import mcp
from agentprops.service import ServiceContext
from catalogue import parse_tool_names
from toolclient import attempt

CATALOGUE_PATH: Final[Path] = Path(__file__).parents[2] / "docs" / "contracts.md"
TESTS_DIR: Final[Path] = Path(__file__).parents[1]

#: Tools `docs/contracts.md` documents that this milestone deliberately does
#: not register, each with the milestone that owns it. M5 landed the three
#: skeleton tools by deleting their entries and M6 removed the four run tools,
#: which is the mechanism working.
#: **Delete an entry when you land it** -
#: :func:`test_no_deferral_is_stale` fails if a deferred tool is registered
#: anyway, and :func:`test_every_documented_tool_is_registered_or_deferred`
#: fails if a documented tool is neither registered nor listed here.
#:
#: ``blueprint_infer`` is the one entry with no milestone: contracts section 4
#: tags it *(phase 1.5)* and says "Not in phase 1".
DEFERRED: Final[dict[str, str]] = {
    "blueprint_infer": "phase 1.5; contracts section 4 says 'Not in phase 1'",
    "dataset_expand": "M7, seeded expansion",
    "dataset_export": "M7, the portable bundle",
    "dataset_import": "M7, the portable bundle",
    "record_step": "M8, whose gate needs it (ruling R-15)",
    "run_finish": "M8, whose gate needs it (ruling R-15)",
    "run_evidence": "M10, the evidence bundle (ruling R-15)",
    "run_export": "M10, the evidence bundle (ruling R-15)",
}

#: The two tools whose documented return is ``{ok, errors}`` rather than
#: ``{ok, data, warnings}`` - contracts section 4, "`{ok, errors}`. Does not
#: store". They are the only tools that may report ``ok: true`` on an
#: ``ErrorEnvelope``, which ruling R-13 requires for a warning-only document.
#: :func:`test_the_validate_tools_are_registered` keeps the set from going
#: stale, and `test_tools_contract.py` pins the exact shape for both.
VALIDATE_TOOLS: Final[frozenset[str]] = frozenset({"blueprint_validate", "dataset_validate"})

#: The function names a test uses to call a tool. The AST guard collects string
#: literals from the positional arguments of a call to any of these, so a test
#: that reaches a tool through a new spelling needs that spelling added here -
#: which is a visible decision rather than a silent gap in the guard.
TOOL_CALL_FUNCTIONS: Final[frozenset[str]] = frozenset({"attempt", "call_tool", "invoke"})


def registered() -> list[RegisteredTool]:
    """The tools the running server reports.

    ``asyncio.run`` at collection time: ``MCPServer.list_tools`` is a coroutine
    and this has to be a module-level list so it can parametrise a test. No
    event loop is running during collection, so there is nothing to conflict
    with.
    """
    return asyncio.run(mcp.list_tools())


TOOLS: Final[list[RegisteredTool]] = registered()
TOOL_NAMES: Final[tuple[str, ...]] = tuple(sorted(tool.name for tool in TOOLS))


def test_the_server_registered_its_tools() -> None:
    """Guard against an enumeration that silently finds nothing.

    Every test below would pass vacuously against an empty surface, and would
    blame the wrong thing while doing it.
    """
    assert len(TOOL_NAMES) == 20, (
        f"expected M4's thirteen tools plus M5's three plus M6's four, got {TOOL_NAMES}"
    )


def test_every_registered_tool_is_documented() -> None:
    documented = parse_tool_names(CATALOGUE_PATH)
    undocumented = set(TOOL_NAMES) - documented
    assert not undocumented, (
        f"these tools are on the surface but not in docs/contracts.md section 4: "
        f"{sorted(undocumented)}"
    )


def test_every_documented_tool_is_registered_or_deferred() -> None:
    documented = parse_tool_names(CATALOGUE_PATH)
    missing = documented - set(TOOL_NAMES) - set(DEFERRED)
    assert not missing, (
        f"these tools are documented, not registered, and not listed in DEFERRED: {sorted(missing)}"
    )


def test_no_deferral_is_stale() -> None:
    """A tool that has landed must be removed from :data:`DEFERRED`."""
    landed = set(DEFERRED) & set(TOOL_NAMES)
    assert not landed, f"these tools are registered but still marked deferred: {sorted(landed)}"


def test_every_deferral_names_a_documented_tool() -> None:
    unknown = set(DEFERRED) - parse_tool_names(CATALOGUE_PATH)
    assert not unknown, f"DEFERRED names tools section 4 does not document: {sorted(unknown)}"


def test_the_validate_tools_are_registered() -> None:
    """:data:`VALIDATE_TOOLS` names real tools, so the exemption cannot go stale."""
    unknown = VALIDATE_TOOLS - set(TOOL_NAMES)
    assert not unknown, f"VALIDATE_TOOLS names unregistered tools: {sorted(unknown)}"


def sample(schema: dict[str, Any]) -> Any:
    """A value of the type a property advertises.

    Deliberately the *emptiest* legal value - ``""``, ``{}``, ``0``, ``False`` -
    so the call reaches the tool and exercises its argument handling rather than
    depending on anything being in the store.
    """
    declared = schema.get("type", "string")
    kinds = declared if isinstance(declared, list) else [declared]
    for kind in kinds:
        if kind == "string":
            return ""
        if kind == "object":
            return {}
        if kind == "integer":
            return 0
        if kind == "boolean":
            return False
    return None


def minimal_arguments(tool: RegisteredTool) -> dict[str, Any]:
    schema: dict[str, Any] = tool.input_schema
    properties: dict[str, Any] = schema.get("properties", {})
    return {name: sample(properties.get(name, {})) for name in schema.get("required", [])}


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: str(tool.name))
async def test_every_registered_tool_answers_an_envelope(
    context: ServiceContext, tool: RegisteredTool
) -> None:
    """Mechanical every-tool coverage: derived from the registry, not from a list.

    Arguments come from the tool's own input schema, so a tool added at M5 or M6
    is covered by this the moment it is registered. The store is empty, which is
    the point: this asserts the envelope, not the payload.
    """
    result = await attempt(context, tool.name, minimal_arguments(tool))
    assert result.is_error is False, f"{tool.name} raised: {result.content}"
    payload = result.structured_content
    assert payload is not None, f"{tool.name} returned no structured content"
    assert isinstance(payload.get("ok"), bool), f"{tool.name} did not return an envelope"

    shape = set(payload)
    assert shape in ({"ok", "data", "warnings"}, {"ok", "errors"}), (
        f"{tool.name} returned neither section 1 envelope: {sorted(shape)}"
    )
    if shape == {"ok", "data", "warnings"}:
        assert payload["ok"] is True, "a success envelope is only produced for a success"
        assert isinstance(payload["data"], dict)
        assert len(payload["data"]) == 1, "data carries the payload under one named key"
    elif payload["ok"]:
        assert tool.name in VALIDATE_TOOLS, (
            f"{tool.name} reported ok: true on an {{ok, errors}} envelope; only the validate "
            f"tools do that, and only for a warning-only document (ruling R-13)"
        )
        assert all(item["severity"] == "warning" for item in payload["errors"]), (
            "ok: true with an error-severity finding contradicts ruling R-13"
        )
    else:
        assert payload["errors"], "a failure envelope with no errors says nothing"
        assert any(item["severity"] == "error" for item in payload["errors"]), (
            "ok: false with only warnings contradicts ruling R-13"
        )


def test_every_registered_tool_declares_a_description() -> None:
    """The docstring is what an LLM caller reads to decide whether to call it."""
    undescribed = [tool.name for tool in TOOLS if not (tool.description or "").strip()]
    assert not undescribed, f"these tools have no description: {undescribed}"


def test_every_tool_argument_advertises_a_json_type() -> None:
    """The ``json_schema_extra`` half of the permissive-annotation trade.

    `server/args.py` annotates every parameter ``object`` so a malformed
    argument becomes an envelope, and puts the real type back into the published
    schema. If that were ever dropped, the runtime would keep working and the
    schema an LLM reads would silently become untyped - which is the failure
    this catches.
    """
    untyped: list[str] = []
    for tool in TOOLS:
        properties: dict[str, Any] = tool.input_schema.get("properties", {})
        untyped.extend(
            f"{tool.name}.{name}" for name, schema in properties.items() if "type" not in schema
        )
    assert not untyped, f"these tool arguments publish no JSON type: {untyped}"


def tool_names_in_test_sources() -> set[str]:
    """Every string literal handed to a ``call_tool``/``invoke`` call under `tests/`.

    Parsed rather than imported, for the reason the drift test gives: a name is
    checked against the source, so a test that exists but is skipped or renamed
    is all visible, and no import order matters.
    """
    found: set[str] = set()
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else None
            if name is None and isinstance(function, ast.Name):
                name = function.id
            if name not in TOOL_CALL_FUNCTIONS:
                continue
            found.update(
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            )
    return found


def test_the_source_scanner_finds_something() -> None:
    """Blames the scanner rather than the suite when the parse comes back empty."""
    found = tool_names_in_test_sources()
    assert len(found) >= 13, f"the AST scan of {TESTS_DIR} found only {sorted(found)}"


def test_every_registered_tool_has_a_dedicated_test() -> None:
    """The M4 acceptance criterion, mechanically enforced.

    "An in-memory ``Client`` connected straight to the server object exercises
    **every** tool" - so every registered tool name must appear in a
    ``call_tool`` call somewhere under `tests/`. Add a tool without a test and
    this fails; delete a tool's only test and this fails.
    """
    exercised = tool_names_in_test_sources()
    unexercised = set(TOOL_NAMES) - exercised
    assert not unexercised, (
        f"these tools are registered but no test calls them by name: {sorted(unexercised)}"
    )


def test_the_scanner_does_not_credit_a_name_nobody_calls() -> None:
    """The guard's own guard: it must not match an arbitrary string literal.

    Without this, a scanner that collected every string in the test tree would
    pass the coverage test forever - including for a tool named in a comment, a
    docstring or a ``DEFERRED`` entry.

    The control was ``dataset_skeleton`` at M4 and had to move when M5 landed
    it, which is the flaw in choosing a deferred tool that will one day exist.
    ``blueprint_infer`` is *(phase 1.5)* and "Not in phase 1", so it stays a
    valid control for the whole of this build - and the first assertion is what
    keeps the control honest, since a name that no longer appears as a literal
    anywhere would make this test pass vacuously.
    """
    control = "blueprint_infer"
    assert control in DEFERRED
    sources = "".join(path.read_text(encoding="utf-8") for path in TESTS_DIR.rglob("test_*.py"))
    assert f'"{control}"' in sources, "the control name is not a string literal under tests/"
    assert control not in tool_names_in_test_sources(), (
        f"{control} is named in DEFERRED and in prose but called by nothing; "
        "the scanner is matching more than tool calls"
    )
