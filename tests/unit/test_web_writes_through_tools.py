"""M9's fifth acceptance clause, mechanically: the web app mutates only through tools.

Ruling R-17 says the web app writes "through the MCP tool surface, producing a
new dataset version by copy-on-write". The M9 brief says clause 5 is the one to
make mechanical, and the pattern to follow is
`test_runtime_is_read_only.py` / `test_bounded_integers.py`: **enumerate from a
real surface rather than from a literal list**, so a change that widens the
surface fails here the day it lands.

Three enumerations, all derived
-------------------------------

1. **Which tools exist** comes from ``mcp.list_tools()`` - the running server's
   registry, exactly as ruling R-50's bounded-integer guard reads it.
2. **Which of those tools write** comes from walking the AST call graph from
   each tool function in `server/` into `service/`, and asking whether it
   reaches a method that `test_runtime_is_read_only.py` classifies as a
   ``Store`` mutator - which is itself read off the ``Store`` Protocol by prefix
   rather than listed. So a `dataset_upsert` added at some future milestone is
   classified as a write with no edit to this file, and appearing in `web/src`
   would fail :func:`test_the_web_app_names_only_permitted_writes`.
3. **Which tools the web app calls** comes from scanning `web/src` for
   ``callTool("<name>")`` call sites and for every registered tool name
   appearing as a string literal.

What the three of them together assert
--------------------------------------

- Every tool name the web app names is a **real** tool. A typo, or an invented
  REST endpoint dressed up as a tool name, fails.
- The only **writing** tools it names are :data:`PERMITTED_WRITES`.
- Every ``callTool`` argument is a **literal**, so the set of names above is the
  complete set - a computed name would make the scan a lower bound rather than
  an enumeration.
- ``callTool`` is reached from exactly one module, and **no network primitive**
  (``fetch``, ``XMLHttpRequest``, ``WebSocket``, ``EventSource``,
  ``sendBeacon``, ``axios``) appears anywhere in `web/src` outside
  `src/mcp/transport.ts`. That is what closes the hole an allowlist of tool
  names leaves wide open: a `PUT /api/datasets` would not name a tool at all.
- ``transport.ts`` itself posts to exactly one path, and it is the MCP path.

Why the network-primitive scan is the load-bearing half
-------------------------------------------------------

"No mutation outside the tool surface" is not "only these tool names are
called". A REST call, a form POST or a `<form action>` would satisfy a tool-name
allowlist while violating the clause outright. So the guard's real claim is
narrower and stronger: **the only way out of this app is one function**, and
that function speaks JSON-RPC to `/mcp`. The tool-name checks then constrain
what goes through it.

The proof that it fails
-----------------------

:func:`test_the_network_scan_rejects_a_planted_call` and
:func:`test_the_write_scan_rejects_a_planted_write` run the same predicates over
**synthetic source** carrying a planted mutation, and assert they report it.
The M9 report also records the guard failing against the defect planted in the
real tree, which is the version that cannot be fooled by a predicate that only
works on strings this file wrote.
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final

import pytest

import agentprops.server
import agentprops.service
from agentprops.server import mcp
from agentprops.storage import Store

SERVER_DIR: Final[Path] = Path(agentprops.server.__file__).resolve().parent
SERVICE_DIR: Final[Path] = Path(agentprops.service.__file__).resolve().parent
REPO_ROOT: Final[Path] = SERVER_DIR.parent.parent.parent
WEB_SRC: Final[Path] = REPO_ROOT / "web" / "src"

#: The transport module, the one place a network call is allowed.
TRANSPORT: Final[Path] = WEB_SRC / "mcp" / "transport.ts"

#: The module that names tools. Every ``callTool`` call site lives here.
TOOLS_MODULE: Final[Path] = WEB_SRC / "mcp" / "tools.ts"

#: The writes M9 permits. Both are ratified in `web/src/mcp/tools.ts`'s module
#: docstring: ``blueprint_upsert`` is the only blueprint write, and
#: ``dataset_import`` is the only tool that turns an edited dataset document
#: into a **new version of its own lineage** - which is what ruling R-17's
#: "copy-on-write" means. ``dataset_submit`` would mint a new lineage instead,
#: so it is a different operation rather than a stricter one.
PERMITTED_WRITES: Final[frozenset[str]] = frozenset({"blueprint_upsert", "dataset_import"})

#: Mutating-method prefixes on the ``Store`` Protocol. The same list
#: `test_runtime_is_read_only.py` uses, for the same reason: a guard reading a
#: literal list of today's writes is the defect it exists to prevent.
MUTATING_VERBS: Final[tuple[str, ...]] = ("put_", "set_", "mark_", "upsert_", "delete_")

#: Everything that can start a request from a browser. Not an exhaustive list of
#: every API in every engine - it is the set reachable from application code,
#: and the ``callTool`` chokepoint below is what makes the claim closed rather
#: than dependent on this list being complete.
NETWORK_PRIMITIVES: Final[tuple[str, ...]] = (
    "fetch(",
    "XMLHttpRequest",
    "WebSocket(",
    "EventSource(",
    "sendBeacon(",
    "axios",
    "<form",
)

#: Source files the scans cover. `.css` and `.json` cannot make a request, and
#: neither can a `.md`. Every dialect that *can* is listed: the scan was `.ts`
#: and `.tsx` only, which would have skipped a plain `.js` or `.mjs` module
#: dropped into `web/src` - and a guard whose coverage depends on a file
#: extension nobody chose deliberately is a guard with a gap.
WEB_SUFFIXES: Final[tuple[str, ...]] = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
)

#: Named rather than written inline so this file's own source stays free of the
#: escape sequences its stripper is about, which makes both easier to read.
NEWLINE: Final[str] = chr(10)
BACKSLASH: Final[str] = chr(92)
QUOTES: Final[str] = "\"'`"


# ------------------------------------------------------------ the tool surface


def registered_tools() -> frozenset[str]:
    """Every tool name on the running server. Ruling R-50's enumeration."""
    return frozenset(str(tool.name) for tool in asyncio.run(mcp.list_tools()))


TOOLS: Final[frozenset[str]] = registered_tools()


def store_mutators() -> frozenset[str]:
    """Every mutating method on the ``Store`` Protocol, read off the Protocol."""
    return frozenset(
        name for name in dir(Store) if not name.startswith("_") and name.startswith(MUTATING_VERBS)
    )


STORE_MUTATORS: Final[frozenset[str]] = store_mutators()


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _functions(directory: Path) -> dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    found: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path in sorted(directory.glob("*.py")):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[path.name, node.name] = node
    return found


SERVICE_FUNCTIONS: Final[dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]] = (
    _functions(SERVICE_DIR)
)
SERVER_FUNCTIONS: Final[dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]] = _functions(
    SERVER_DIR
)


def _called(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name:
                names.add(name.rsplit(".", 1)[-1])
    return names


def _reachable(entry: tuple[str, str]) -> set[str]:
    """Every call name reachable from a `server/` tool function through `service/`.

    Ambiguous names pull in every `service/` function with that name, which is
    conservative in the safe direction: the walk can over-report a write and
    cannot under-report one.
    """
    stack: list[tuple[str, str]] = [entry]
    seen: set[tuple[str, str]] = set()
    calls: set[str] = set()
    index = {**SERVICE_FUNCTIONS, **SERVER_FUNCTIONS}
    while stack:
        key = stack.pop()
        if key in seen or key not in index:
            continue
        seen.add(key)
        for name in _called(index[key]):
            calls.add(name)
            if (key[0], name) in index:
                stack.append((key[0], name))
            else:
                stack.extend(other for other in index if other[1] == name and other not in seen)
    return calls


def tool_entry_points() -> dict[str, tuple[str, str]]:
    """Tool name to the ``(module, function)`` that implements it.

    Read off the ``@mcp.tool()`` decorator in `server/tools_*.py`, then checked
    against the registry: :func:`test_every_registered_tool_has_an_ast_entry_point`
    fails if the two ever disagree, so the classification below cannot silently
    cover fewer tools than the server serves.
    """
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(SERVER_DIR.glob("tools_*.py")):
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if _dotted(target).endswith("tool"):
                    found[node.name] = (path.name, node.name)
    return found


TOOL_ENTRY_POINTS: Final[dict[str, tuple[str, str]]] = tool_entry_points()


def writing_tools() -> frozenset[str]:
    """Every registered tool that can reach a ``Store`` mutator."""
    return frozenset(
        name for name, entry in TOOL_ENTRY_POINTS.items() if _reachable(entry) & STORE_MUTATORS
    )


WRITING_TOOLS: Final[frozenset[str]] = writing_tools()


# --------------------------------------------------------------- the web scans


def web_files() -> list[Path]:
    """Every TypeScript source under `web/src`, app code and tests alike."""
    return sorted(
        path for path in WEB_SRC.rglob("*") if path.is_file() and path.suffix in WEB_SUFFIXES
    )


def is_test(path: Path) -> bool:
    """True for a test or a test helper.

    Excluded from every scan below, and the exclusion is not a loophole: a test
    file is not shipped, it stubs ``fetch`` on purpose (that is how the request
    count in `DatasetList.test.tsx` is measured), and it names a deliberately
    unregistered tool to assert the app's error path. Including them would make
    the guard fail for the reasons it exists to permit.
    :func:`test_the_app_and_test_partition_is_real` asserts both halves are
    non-empty, so this cannot quietly become "scan nothing".
    """
    return ".test." in path.name or "test" in path.relative_to(WEB_SRC).parts[:-1]


def app_files() -> list[Path]:
    """The shipped app: `web/src` minus tests and test helpers."""
    return [path for path in web_files() if not is_test(path)]


def strip_comments(source: str) -> str:
    """``source`` with comment bodies blanked out, string literals intact.

    Needed because a *comment* naming a tool must not trip the scans, and
    several comments in this app name tools deliberately - `tools.ts` explains
    at length why ``dataset_submit`` is the wrong tool for an edit, and a guard
    that punished that explanation would push the reasoning out of the code.

    A character-by-character pass rather than a regex, because a regex that
    strips ``//`` to end-of-line also eats the rest of the line containing
    ``"https://example"``. String and template literals are preserved verbatim
    so :func:`_quoted` still finds real tool names.

    **The known limit:** a comment inside a template literal's ``${...}``
    substitution is not stripped. Nothing in this app has one, and the
    alternative is a TypeScript parser.
    """
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if char == "/" and nxt == "/":
            while index < length and source[index] != NEWLINE:
                out.append(" ")
                index += 1
            continue
        if char == "/" and nxt == "*":
            while index < length and not (
                source[index] == "*" and source[index + 1 : index + 2] == "/"
            ):
                out.append(NEWLINE if source[index] == NEWLINE else " ")
                index += 1
            out.append("  ")
            index += 2
            continue
        if char in QUOTES:
            quote = char
            out.append(char)
            index += 1
            while index < length:
                if source[index] == BACKSLASH:
                    out.append(source[index : index + 2])
                    index += 2
                    continue
                out.append(source[index])
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _sources(paths: Iterable[Path]) -> Iterator[tuple[Path, str]]:
    """``(path, source)`` with comments blanked out."""
    for path in paths:
        yield path, strip_comments(path.read_text(encoding="utf-8"))


#: A quoted tool name. Restricted to a string or template literal rather than
#: any substring, and applied to comment-stripped source, so prose naming a tool
#: does not trip the scan. The complement is
#: :func:`test_every_call_tool_argument_is_a_literal`, which is what makes "the
#: literals are the whole set" true.
def _quoted(name: str) -> re.Pattern[str]:
    return re.compile(rf"""["'`]{re.escape(name)}["'`]""")


CALL_TOOL_LITERAL: Final[re.Pattern[str]] = re.compile(r"""callTool\(\s*["'`]([a-z_]+)["'`]""")
CALL_TOOL_ANY: Final[re.Pattern[str]] = re.compile(r"""callTool\(\s*([^)\s,]+)""")


def tool_names_in(sources: Iterable[tuple[Path, str]]) -> dict[str, set[Path]]:
    """Every registered tool name appearing as a string literal, and where."""
    found: dict[str, set[Path]] = {}
    materialised = list(sources)
    for name in TOOLS:
        pattern = _quoted(name)
        for path, text in materialised:
            if pattern.search(text):
                found.setdefault(name, set()).add(path)
    return found


def network_offences(sources: Iterable[tuple[Path, str]], allowed: Path) -> dict[Path, list[str]]:
    """Files outside ``allowed`` that contain a network primitive."""
    offences: dict[Path, list[str]] = {}
    for path, text in sources:
        if path == allowed:
            continue
        hits = [primitive for primitive in NETWORK_PRIMITIVES if primitive in text]
        if hits:
            offences[path] = hits
    return offences


# ------------------------------------------------------------- the enumerations


def test_the_web_app_exists_and_is_scanned() -> None:
    """Every assertion below is vacuous without this."""
    assert WEB_SRC.is_dir(), f"the web app is missing from {WEB_SRC}"
    assert TRANSPORT.is_file(), f"the transport module is missing from {TRANSPORT}"
    assert TOOLS_MODULE.is_file(), f"the tools module is missing from {TOOLS_MODULE}"
    files = web_files()
    assert len(files) >= 10, f"only {len(files)} web source files found; the scan is broken"


def test_the_app_and_test_partition_is_real() -> None:
    """Both halves of :func:`is_test` are non-empty.

    The exclusion of test files is the one place this guard could quietly become
    vacuous: if `is_test` ever matched everything, every scan below would run
    over an empty list and pass. So both counts are asserted, and the app half
    has to be the larger.
    """
    app = app_files()
    tests = [path for path in web_files() if is_test(path)]
    assert len(app) >= 10, f"only {len(app)} app files; is_test is excluding too much"
    assert len(tests) >= 4, f"only {len(tests)} test files found; the partition is broken"
    assert set(app).isdisjoint(tests)
    assert sorted(app + tests) == web_files()


def test_the_comment_stripper_keeps_code_and_drops_prose() -> None:
    """The stripper's own test. Three properties, each one a way it could be wrong.

    It must not eat a URL's ``//`` (which a regex would), it must blank a
    comment that names a tool (the reason it exists), and it must leave a real
    string literal naming a tool intact (or the scans would find nothing).
    """
    kept = strip_comments('const u = "https://example.test/x"; // dataset_archive here')
    assert '"https://example.test/x"' in kept
    assert "dataset_archive" not in kept

    block = strip_comments("/* explains dataset_submit at length */ callTool('dataset_find', {})")
    assert "dataset_submit" not in block
    assert "callTool('dataset_find', {})" in block

    # Line numbering survives, so a future failure message can cite a line.
    source = "// a" + NEWLINE + "const x = 1;" + NEWLINE
    assert strip_comments(source).count(NEWLINE) == source.count(NEWLINE)


def test_the_stripper_is_what_lets_tools_ts_explain_itself() -> None:
    """The concrete case that motivated it, asserted against the real file.

    `tools.ts` names ``dataset_submit``, ``dataset_expand``, ``dataset_archive``
    and ``dataset_restore`` in its module comment, to say why each is *not* the
    tool an edit uses. That explanation is the most valuable thing in the file
    and the guard must not force its removal - so: present in the raw source,
    absent after stripping.
    """
    raw = TOOLS_MODULE.read_text(encoding="utf-8")
    stripped = strip_comments(raw)
    for tool in ("dataset_submit", "dataset_expand", "dataset_archive", "dataset_restore"):
        assert tool in raw, f"{tool} is no longer discussed in tools.ts; update this test"
        assert tool not in stripped, (
            f"{tool} survived comment stripping in tools.ts. Either the stripper is broken, or - "
            f"the likelier reading - tools.ts now *calls* {tool}, which "
            f"test_the_web_app_names_only_permitted_writes will also be failing about."
        )
    # And the two real calls survive.
    assert '"blueprint_upsert"' in stripped
    assert '"dataset_import"' in stripped


def test_the_tool_enumeration_is_not_empty() -> None:
    """The registry read, and the two known writes really in it."""
    assert len(TOOLS) >= 25, f"expected the full tool surface, found {sorted(TOOLS)}"
    assert PERMITTED_WRITES <= TOOLS, (
        f"these permitted writes are not tools any more: {sorted(PERMITTED_WRITES - TOOLS)}"
    )


def test_every_registered_tool_has_an_ast_entry_point() -> None:
    """The AST classification covers exactly the tools the server serves.

    Without this, a tool defined in a module the glob misses would be classified
    as neither a read nor a write, and naming it in the web app would pass every
    assertion below by being invisible.
    """
    assert set(TOOL_ENTRY_POINTS) == TOOLS, (
        f"the decorator scan and the registry disagree. "
        f"only in the AST: {sorted(set(TOOL_ENTRY_POINTS) - TOOLS)}; "
        f"only in the registry: {sorted(TOOLS - set(TOOL_ENTRY_POINTS))}"
    )


def test_the_write_classification_finds_the_writes_it_should() -> None:
    """The call-graph classification's own guard.

    Names what must be true of it: the known writes are classified as writes,
    and the known pure reads are not. A classification that returned everything,
    or nothing, would make :func:`test_the_web_app_names_only_permitted_writes`
    either always fail or always pass.
    """
    assert STORE_MUTATORS, f"no mutators found on the Store Protocol: {dir(Store)}"
    for tool in ("blueprint_upsert", "dataset_submit", "dataset_import", "dataset_archive"):
        assert tool in WRITING_TOOLS, f"{tool} writes and was classified as a read"
    for tool in ("blueprint_get", "blueprint_list", "dataset_find", "dataset_get", "agent_list"):
        assert tool not in WRITING_TOOLS, f"{tool} is a read and was classified as a write"


def test_the_validate_tools_are_reads_which_is_what_lets_them_run_before_save() -> None:
    """`blueprint_validate` and `dataset_validate` store nothing.

    The whole pre-save half of clause 1 rests on this: the editor round-trips a
    broken document to the catalogue *before* any write. If either tool ever
    reached a store mutator, the editor would be writing while validating and
    the phrase "before save" would stop being true.
    """
    for tool in ("blueprint_validate", "dataset_validate"):
        assert tool not in WRITING_TOOLS, f"{tool} reached a Store mutator; it must not store"


# ------------------------------------------------------------------ the clause


def test_the_web_app_names_only_real_tools() -> None:
    """No invented endpoint, no typo, nothing that is not on the surface.

    The scan is over registered names, so this catches the reverse direction
    too: :func:`test_no_call_tool_argument_is_unregistered` is where an
    unregistered name fails.
    """
    named = tool_names_in(_sources(app_files()))
    assert named, "the web app names no tools at all; the scan or the app is broken"
    assert set(named) <= TOOLS


def test_no_call_tool_argument_is_unregistered() -> None:
    """Every ``callTool`` literal is a tool the server actually serves."""
    called: dict[str, Path] = {}
    for path, text in _sources(app_files()):
        for name in CALL_TOOL_LITERAL.findall(text):
            called[name] = path
    assert called, "no callTool call site found; the regex or the app has changed"
    unknown = {name: path for name, path in called.items() if name not in TOOLS}
    assert not unknown, (
        f"these callTool names are not registered tools: "
        f"{ {name: str(path) for name, path in unknown.items()} }. "
        f"The web app must speak only to the tool surface (ruling R-17)."
    )


def test_the_web_app_names_only_permitted_writes() -> None:
    """Clause 5's allowlist half: no writing tool beyond the two M9 needs."""
    named = set(tool_names_in(_sources(app_files())))
    offences = (named & WRITING_TOOLS) - PERMITTED_WRITES
    assert not offences, (
        f"the web app names these writing tools, which M9 does not permit: {sorted(offences)}. "
        f"Permitted: {sorted(PERMITTED_WRITES)}. If a new write is genuinely wanted, add it to "
        f"PERMITTED_WRITES with a reason - do not widen the scan."
    )


def test_the_web_app_does_name_both_permitted_writes() -> None:
    """The non-vacuity of the assertion above.

    An app that called no write at all would pass it, and would also fail ruling
    R-17 - which says the web app *does* write. So both permitted writes must be
    reached.
    """
    called = {name for _, text in _sources(app_files()) for name in CALL_TOOL_LITERAL.findall(text)}
    missing = PERMITTED_WRITES - called
    assert not missing, (
        f"the web app never calls {sorted(missing)}, so it cannot write. Ruling R-17: the web app "
        f"writes through the tool surface."
    )


#: Literals in `tools.ts` that ruling R-70 rests on, each with the reason it is
#: load-bearing. The clause-5 guard checks tool *names*; these are tool
#: *arguments*, and R-70 accepted `dataset_import` as the edit path partly
#: because of what they are.
REQUIRED_LITERALS: Final[dict[str, str]] = {
    "blueprints: []": (
        "ruling R-70 accepted dataset_import as the dataset-edit path on the fact that with an "
        "empty blueprint list it CANNOT publish a blueprint as a side effect of saving a "
        "dataset. A non-empty list would re-publish an immutable version and make R-70's first "
        "supporting fact false."
    ),
    "publish: false": (
        "a published blueprint version is immutable (BP-016), so an editor that published on "
        "every save would make the second save an error. Publishing is a deliberate act, not "
        "what pressing save in a JSON editor means."
    ),
}


def test_the_dataset_edit_bundle_carries_no_blueprint() -> None:
    """Minor 6: the fact ruling R-70 rests on, guarded rather than assumed.

    Nothing mechanically held it. The clause-5 scans check which tools are
    *named*; the bundle's contents are an *argument*, and the Python-side
    review-surface test re-implements the bundle rather than deriving it from
    the app - so both sides could have agreed while the app sent something
    else.

    A literal assertion over `tools.ts`, beside the existing `"dataset_import"`
    check, because the value is a constant in one construction site with one
    caller. If it ever becomes computed, this fails and the guard has to be
    rewritten to follow it - which is the right moment to notice.
    """
    source = strip_comments(TOOLS_MODULE.read_text(encoding="utf-8"))
    missing = {
        literal: reason for literal, reason in REQUIRED_LITERALS.items() if literal not in source
    }
    assert not missing, f"these literals are gone from {TOOLS_MODULE.name}: " + "; ".join(
        f"{literal!r} - {reason}" for literal, reason in missing.items()
    )


def test_the_bundle_literals_are_in_code_and_not_only_in_prose() -> None:
    """The non-vacuity of the test above.

    :func:`strip_comments` blanks comment bodies, so a `blueprints: []` that
    survives stripping is in code. Asserted separately because the docstring in
    `tools.ts` *discusses* `blueprints: []` at length, and a scan over the raw
    source would pass on the prose alone.
    """
    raw = TOOLS_MODULE.read_text(encoding="utf-8")
    stripped = strip_comments(raw)
    for literal in REQUIRED_LITERALS:
        assert literal in raw
        assert literal in stripped, (
            f"{literal!r} appears in {TOOLS_MODULE.name} only inside a comment; the code no "
            f"longer does it"
        )


def test_every_call_tool_argument_is_a_literal() -> None:
    """A computed tool name would make the scans a lower bound, not an enumeration.

    `transport.ts` is excluded because it *defines* ``callTool`` and never calls
    it, so its own signature - ``callTool(name: string, ...)`` - is not a call
    site. Every real call site is in `tools.ts`, which
    :func:`test_call_tool_is_reached_from_one_module_only` pins.
    """
    computed: dict[str, str] = {}
    for path, text in _sources(path for path in app_files() if path != TRANSPORT):
        for argument in CALL_TOOL_ANY.findall(text):
            if not re.fullmatch(r"""["'`][a-z_]+["'`]""", argument):
                computed[str(path)] = argument
    assert not computed, (
        f"these callTool arguments are not string literals: {computed}. A computed tool name "
        f"defeats the enumeration this guard is built on."
    )


def test_call_tool_is_reached_from_one_module_only() -> None:
    """One place names tools, so the allowlist above is a complete list."""
    sites = {
        path
        for path, text in _sources(app_files())
        if CALL_TOOL_ANY.search(text) and path != TRANSPORT
    }
    unexpected = sites - {TOOLS_MODULE}
    assert not unexpected, (
        f"callTool is called from {sorted(str(path) for path in unexpected)} as well as "
        f"{TOOLS_MODULE.name}. Keep every tool call in one module so the write surface is "
        f"readable in one file."
    )


def test_no_network_primitive_outside_the_transport() -> None:
    """The load-bearing half: the only way out of the app is ``callTool``.

    A tool-name allowlist says nothing about a ``fetch('/api/datasets', {method:
    'PUT'})``, and that is precisely the mutation clause 5 forbids. So no
    application module may contain a network primitive at all.
    """
    offences = network_offences(_sources(app_files()), TRANSPORT)
    assert not offences, (
        f"these modules can reach the network without going through the tool surface: "
        f"{ {str(path): hits for path, hits in offences.items()} }. Every request must go "
        f"through callTool() in src/mcp/transport.ts - ruling R-17 and M9 clause 5."
    )


def test_the_transport_posts_to_the_mcp_path_and_nowhere_else() -> None:
    """One URL, and it is the MCP endpoint. No second base, no absolute host."""
    text = TRANSPORT.read_text(encoding="utf-8")
    assert 'MCP_PATH = "/mcp"' in text, "the transport's path constant has moved or changed"
    fetches = re.findall(r"""fetch\(\s*([^,)\s]+)""", text)
    assert fetches, "the transport makes no fetch call; the guard is reading the wrong file"
    assert set(fetches) == {"MCP_PATH"}, (
        f"the transport fetches {sorted(set(fetches))}; it must reach only MCP_PATH so the app "
        f"has exactly one interface to the service."
    )
    # An absolute URL would mean the app can be pointed at a second service, and
    # would reintroduce the cross-origin problem the same-origin design removes.
    absolute = re.findall(r"""["'`]https?://[^"'`]+["'`]""", text)
    assert not absolute, f"the transport names absolute URLs: {absolute}"


def test_the_app_declares_no_html_form() -> None:
    """A ``<form>`` submits without JavaScript, which is a mutation path.

    Covered by :data:`NETWORK_PRIMITIVES` already; asserted separately because
    that scan's failure message would blame "the network" for what is really an
    HTML default, and the next author needs to know which.
    """
    offences = [str(path) for path, text in _sources(app_files()) if "<form" in text]
    assert not offences, (
        f"these modules declare an HTML form, which submits outside the tool surface: {offences}"
    )


# --------------------------------------------------- the guard's own falsifiers


PLANTED_REST_CALL: Final[str] = """
import { thing } from "@/x";
export async function save(id: string, body: unknown): Promise<void> {
  await fetch(`/api/datasets/${id}`, { method: "PUT", body: JSON.stringify(body) });
}
"""

PLANTED_WRITE_TOOL: Final[str] = """
import { callTool } from "./transport";
export async function archive(id: string): Promise<unknown> {
  return callTool("dataset_archive", { dataset_id: id });
}
"""

PLANTED_COMPUTED_NAME: Final[str] = """
import { callTool } from "./transport";
export async function anything(verb: string): Promise<unknown> {
  return callTool(`dataset_${verb}`, {});
}
"""


def test_the_network_scan_rejects_a_planted_call(tmp_path: Path) -> None:
    """The predicate, run over a planted REST call, reports it."""
    planted = tmp_path / "Sneaky.tsx"
    planted.write_text(PLANTED_REST_CALL, encoding="utf-8")
    offences = network_offences([(planted, PLANTED_REST_CALL)], TRANSPORT)
    assert planted in offences
    assert "fetch(" in offences[planted]


def test_the_write_scan_rejects_a_planted_write(tmp_path: Path) -> None:
    """The predicate, run over a planted non-permitted write, reports it."""
    planted = tmp_path / "Archive.ts"
    named = set(tool_names_in([(planted, PLANTED_WRITE_TOOL)]))
    assert "dataset_archive" in named
    assert "dataset_archive" in WRITING_TOOLS
    assert (named & WRITING_TOOLS) - PERMITTED_WRITES == {"dataset_archive"}


def test_the_literal_scan_rejects_a_planted_computed_name(tmp_path: Path) -> None:
    """A computed tool name is reported rather than silently uncounted."""
    planted = tmp_path / "Dynamic.ts"
    computed = [
        argument
        for argument in CALL_TOOL_ANY.findall(PLANTED_COMPUTED_NAME)
        if not re.fullmatch(r"""["'`][a-z_]+["'`]""", argument)
    ]
    assert computed, f"the literal check missed a computed name in {planted.name}"


@pytest.mark.parametrize(
    "primitive",
    ["fetch(", "XMLHttpRequest", "WebSocket(", "EventSource(", "sendBeacon(", "axios", "<form"],
)
def test_every_network_primitive_is_detected(primitive: str, tmp_path: Path) -> None:
    """Each entry in the list actually trips the scan.

    A typo in :data:`NETWORK_PRIMITIVES` would silently stop guarding one of
    them, and nothing else in this file would notice.
    """
    planted = tmp_path / "Planted.ts"
    source = f"const x = {primitive}'/api';"
    assert network_offences([(planted, source)], TRANSPORT) == {planted: [primitive]}
