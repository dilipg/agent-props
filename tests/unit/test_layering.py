"""`models/` and `validation/` are pure; `storage/` reaches only `models/`.

CLAUDE.md's layering rule says review enforces this. A test is cheaper than a
review round, and four of the properties here are rulings that later milestones
depend on staying true:

- **R-04** - models are shapes, the catalogue is policy. A ``pattern`` or a
  ``Literal`` slipped into a model turns ten corpus cases from "reports a rule
  id" into "raises ``ValidationError``", and M2's gate fails.
- **R-09 / R-10** - no clock and no random source in `models/`. Importing
  ``uuid`` for the ``UUID`` type and parser is explicitly allowed; *calling*
  ``uuid4()`` is not.
- **R-11** - `validation/` reaches the store through an injected ``Resolver``
  and never imports `storage/`. That is what lets DS-001, DS-031 and BP-016
  exist in a package specified as pure functions with no I/O. `validation/` may
  import `models/` and nothing else sideways.
- **R-09 / R-23 in `storage/`** - an adapter may import `models/` and nothing
  else sideways, and it reads no clock. Both are load-bearing: `validation/`
  is where a document is checked (R-23), so an adapter importing it would put
  the catalogue on the write path twice and on the read path at all; and every
  timestamp a row holds comes from a column default, the client, the document
  or ``Seeded`` (R-09), so a ``datetime.now()`` in an adapter is the one thing
  that would make ``dataset_find``'s ordering irreproducible after an
  export/import cycle.

M4 adds the two layers above them, and three guards that are the *only*
mechanical enforcement CLAUDE.md's layering rule has:

- **`server/` reaches `service/` and `models/`, nothing else.** No `storage/`,
  no `validation/`. That is what keeps business logic out of the tool functions:
  a tool cannot validate a document or query a store without importing the layer
  that does it. It is also why
  :func:`agentprops.service.context.context_from_url` exists - `server/__main__`
  has to open a store, and doing it through `service/` is what makes this rule
  literally true rather than true with an exemption.
- **A tool function stays under 20 lines** (CLAUDE.md, and the M4 brief).
  Measured on the parsed AST, in physical lines *and* in statements, because
  either number can be gamed alone.
- **Only `clock.py` reads a clock** (ruling R-09's "a single injected ``Clock``
  port, used only in `service/`"). Without this, "a single injected clock" is a
  sentence in a docstring.

Everything is checked against the parsed AST rather than by grepping text, so
docstrings that discuss ``min_length``, ``uuid4()`` or ``datetime.now()`` - and
this file does - cannot trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agentprops.expansion
import agentprops.models
import agentprops.server
import agentprops.service
import agentprops.storage
import agentprops.validation

MODELS_DIR = Path(agentprops.models.__file__).resolve().parent
MODEL_FILES = sorted(MODELS_DIR.glob("*.py"))

VALIDATION_DIR = Path(agentprops.validation.__file__).resolve().parent
VALIDATION_FILES = sorted(VALIDATION_DIR.glob("*.py"))

#: Recursive, so the Alembic package is covered too - `migrations/env.py` is
#: the module most likely to reach for something convenient.
STORAGE_DIR = Path(agentprops.storage.__file__).resolve().parent
STORAGE_FILES = sorted(STORAGE_DIR.rglob("*.py"))

EXPANSION_DIR = Path(agentprops.expansion.__file__).resolve().parent
EXPANSION_FILES = sorted(EXPANSION_DIR.glob("*.py"))

SERVICE_DIR = Path(agentprops.service.__file__).resolve().parent
SERVICE_FILES = sorted(SERVICE_DIR.glob("*.py"))

SERVER_DIR = Path(agentprops.server.__file__).resolve().parent
SERVER_FILES = sorted(SERVER_DIR.glob("*.py"))

#: The modules that register tools, from the package itself rather than from a
#: glob, so a new tool module is covered by the one edit that registers it.
TOOL_MODULE_FILES = sorted(
    Path(module.__file__).resolve() for module in agentprops.server.TOOL_MODULES if module.__file__
)

#: `models/` may import none of these. `export/` is in the list even though
#: CLAUDE.md's arrow diagram omits it: it is a sibling layer either way.
FORBIDDEN_LAYERS = frozenset({"validation", "storage", "service", "server", "expansion", "export"})

#: `validation/` may import `models/`, and nothing else sideways. `storage/` is
#: the one that matters: ruling R-11 exists precisely so that the three
#: existence checks do not reach for it.
FORBIDDEN_LAYERS_FOR_VALIDATION = frozenset({"storage", "service", "server", "expansion", "export"})

#: `storage/` may import `models/`, and nothing else sideways. `validation/` is
#: the one that matters here, and it is the mirror of the rule above: under
#: ruling R-23 a document is validated in `service/` *before* storage sees it,
#: so an adapter has no business importing the catalogue - and BP-016's
#: canonical comparison in `sql.py` duplicates three lines rather than crossing
#: this line.
FORBIDDEN_LAYERS_FOR_STORAGE = frozenset({"validation", "service", "server", "expansion", "export"})

#: `expansion/` may import `models/`, and nothing else sideways. It is a peer
#: of `validation/`: pure, no I/O, and - the reason it exists - **no clock and
#: no random source**, which the two guards below enforce rather than describe.
#: Ground rule 9 and contracts section 9 both ask for exactly this ("enforce
#: with a ruff custom rule or a test that greps the tree").
FORBIDDEN_LAYERS_FOR_EXPANSION = frozenset({"validation", "storage", "service", "server", "export"})

#: `service/` may reach every layer below it - that is its job - and may not
#: reach the layer above. One entry, and it is the one that matters: a service
#: function importing a tool module would invert the whole arrangement.
FORBIDDEN_LAYERS_FOR_SERVICE = frozenset({"server"})

#: `server/` may import `service/` and `models/`, and nothing else sideways.
#: `storage/` and `validation/` are the two that carry business logic behind
#: them, and excluding them is what makes "no business logic in `server/`"
#: enforceable rather than a matter of taste. `models/` is the shared
#: vocabulary layer every other layer already imports.
FORBIDDEN_LAYERS_FOR_SERVER = frozenset({"storage", "validation", "expansion", "export"})

#: The one module in `service/` and `server/` allowed to read a clock.
CLOCK_MODULE = "clock.py"

#: CLAUDE.md: "a tool function ... stays under 20 lines". Physical lines of the
#: body, docstring excluded.
MAX_TOOL_FUNCTION_LINES = 20

#: And a statement budget, because the two numbers fail differently: a
#: formatter can split one statement across ten lines, and a semicolon-free
#: author can pack ten statements into ten lines. A tool that needs more than
#: this is doing something `service/` should be doing.
MAX_TOOL_FUNCTION_STATEMENTS = 12

#: Calls that read a clock. Dotted, so ``context.clock.now()`` - the injected
#: port every stamped timestamp is supposed to come from - is not an offence
#: while ``datetime.now()`` is.
FORBIDDEN_CLOCK_CALLS = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "date.today",
        "time.time",
        "time.monotonic",
    }
)

#: Calls that draw from an **unseeded** random source, matched by dotted
#: *suffix* - so ``from uuid import uuid4; uuid4()`` is caught as well as
#: ``uuid.uuid4()``. Every name here is distinctive enough that no method on the
#: sanctioned seeded source shares it.
#:
#: ``choice`` is deliberately **not** here, and that is M7's change. Ground rule
#: 9 is "all randomness inside the service flows through ``Seeded``", and
#: contracts section 9 names ``Seeded.choice`` as one of its methods - so
#: ``source.choice(templates)`` in `service/expansion.py` is the sanctioned
#: source being used correctly, and a bare-suffix match cannot tell it from
#: ``random.choice``. The two halves that replace it are strictly stronger than
#: the suffix was: ``random.choice`` and ``secrets.choice`` are matched by their
#: **dotted** names below, and :data:`FORBIDDEN_RANDOM_MODULES` forbids the
#: *import* that a bare ``choice(...)`` would need. There is no third route.
#:
#: ``shuffle`` stays, and does not collide: the seeded method is ``shuffled``.
FORBIDDEN_RANDOM_CALLS = frozenset(
    {
        "uuid1",
        "uuid3",
        "uuid4",
        "uuid5",
        "randint",
        "randrange",
        "randbytes",
        "randbelow",
        "getrandbits",
        "shuffle",
        "urandom",
        "token_hex",
        "token_bytes",
        "token_urlsafe",
    }
)

#: Random-source calls that are offences only in their dotted form, because
#: their last segment is a legitimate name on the seeded source or elsewhere.
FORBIDDEN_DOTTED_RANDOM_CALLS = frozenset(
    {
        "random.random",
        "random.choice",
        "random.sample",
        "random.uniform",
        "secrets.choice",
        "os.urandom",
    }
)

#: Modules whose *import* is the offence, in every layer this file guards.
#:
#: This is the half that lets ``choice`` come off the suffix list without
#: weakening anything. ``random.choice`` cannot be reached without naming
#: ``random``: either as ``import random`` - and then the call is dotted and
#: caught above - or as ``from random import choice``, and then this catches the
#: import. Forbidding the import is also the earlier warning of the two: it
#: fails on the line that made the mistake possible rather than on the line that
#: made it.
FORBIDDEN_RANDOM_MODULES = frozenset({"random", "secrets"})

#: The union both call guards match against. One name, because every guard below
#: asks the same question - "is this a clock or an unseeded random source" - and
#: splitting the *check* as well as the data would be two places to update.
FORBIDDEN_CALLS = FORBIDDEN_CLOCK_CALLS | FORBIDDEN_RANDOM_CALLS | FORBIDDEN_DOTTED_RANDOM_CALLS

#: Calls that touch the filesystem or the network.
FORBIDDEN_IO_CALLS = frozenset(
    {"open", "read_text", "write_text", "read_bytes", "write_bytes", "urlopen", "connect"}
)

#: ``Field(...)`` keywords that describe a shape. Anything else is a value
#: constraint, which ruling R-04 assigns to the catalogue. ``strict`` is
#: allowed because it is a *coercion* policy, not a value constraint, and
#: ruling R-23 requires it on the numeric and boolean fields.
ALLOWED_FIELD_KEYWORDS = frozenset(
    {
        "default",
        "default_factory",
        "alias",
        "validation_alias",
        "serialization_alias",
        "title",
        "description",
        "examples",
        "exclude",
        "strict",
    }
)

#: ``ConfigDict(...)`` keywords this package uses. An allowlist rather than a
#: denylist because Pydantic's config surface includes several value
#: constraints that apply to every field at once - ``str_min_length``,
#: ``str_max_length``, ``str_strip_whitespace``, ``coerce_numbers_to_str`` -
#: and a whole-model ``strict=True`` would stop ``datetime`` and ``UUID``
#: fields accepting strings, so a JSON document would not parse at all.
ALLOWED_CONFIG_KEYWORDS = frozenset(
    {
        "extra",
        "validate_by_name",
        "validate_by_alias",
        "serialize_by_alias",
        "populate_by_name",
        "frozen",
        "title",
        "use_attribute_docstrings",
        "json_schema_extra",
    }
)

VALIDATOR_DECORATORS = frozenset(
    {"field_validator", "model_validator", "validator", "root_validator"}
)

#: Names that carry a value constraint in the *type* rather than in a
#: ``Field()`` keyword. This is the route the original guard missed:
#: ``max_iterations: PositiveInt`` is the single most likely future violation
#: of ruling R-04, and it needs no ``Field()`` call at all. Matched on the
#: bare identifier anywhere in the module, so an import of one of these names
#: is itself flagged - which is the earliest possible warning.
#:
#: ``StrictInt``/``StrictBool`` are deliberately absent: they constrain
#: coercion, not value, and ruling R-23 requires them.
CONSTRAINED_TYPE_NAMES = frozenset(
    {
        # pydantic's pre-baked constrained aliases
        "PositiveInt",
        "NegativeInt",
        "NonNegativeInt",
        "NonPositiveInt",
        "PositiveFloat",
        "NegativeFloat",
        "NonNegativeFloat",
        "NonPositiveFloat",
        "FiniteFloat",
        # pydantic's constrained-type factories
        "conint",
        "confloat",
        "condecimal",
        "constr",
        "conbytes",
        "conlist",
        "conset",
        "confrozenset",
        "condate",
        "StringConstraints",
        # annotated-types metadata, the Annotated[int, Ge(0)] route
        "Ge",
        "Gt",
        "Le",
        "Lt",
        "Interval",
        "MultipleOf",
        "MinLen",
        "MaxLen",
        "Len",
        "Predicate",
        "Timezone",
        # validator callables usable as Annotated metadata
        "AfterValidator",
        "BeforeValidator",
        "PlainValidator",
        "WrapValidator",
        "InstanceOf",
        "AllowInfNan",
    }
)


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def dotted(node: ast.expr) -> str:
    """``uuid.uuid4`` from an ``Attribute``/``Name`` chain, best effort."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def test_the_model_file_table_is_not_empty() -> None:
    assert MODEL_FILES, f"no model modules found under {MODELS_DIR}"


def sibling_import_offences(path: Path, forbidden: frozenset[str]) -> list[str]:
    """Every import in ``path`` that reaches a layer in ``forbidden``.

    Absolute and relative imports both. A relative import
    (``from ..validation import x``) has no ``agentprops`` segment to anchor on -
    ``node.module`` is just ``"validation"`` - so ``node.level > 0`` is matched
    against the forbidden set directly. That case is not hypothetical: it is the
    spelling an author reaching sideways is most likely to reach for.
    """
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if "agentprops" in parts:
                    tail = parts[parts.index("agentprops") + 1 :]
                    if tail and tail[0] in forbidden:
                        offences.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative: `from ..validation import x` -> module "validation";
                # `from .. import validation` -> module None, name "validation".
                heads = (
                    [node.module.split(".")[0]]
                    if node.module
                    else [alias.name.split(".")[0] for alias in node.names]
                )
                dots = "." * node.level
                for head in heads:
                    if head in forbidden:
                        offences.append(f"line {node.lineno}: from {dots}{head} (relative)")
            elif node.module:
                parts = node.module.split(".")
                if "agentprops" in parts:
                    tail = parts[parts.index("agentprops") + 1 :]
                    if tail and tail[0] in forbidden:
                        offences.append(f"line {node.lineno}: from {node.module}")
    return offences


@pytest.mark.parametrize("path", MODEL_FILES, ids=lambda p: p.name)
def test_models_import_no_sibling_layer(path: Path) -> None:
    offences = sibling_import_offences(path, FORBIDDEN_LAYERS)
    assert not offences, f"{path.name} imports a sibling layer: {offences}"


def test_the_validation_file_table_is_not_empty() -> None:
    assert len(VALIDATION_FILES) > 1, f"no rule modules found under {VALIDATION_DIR}"


@pytest.mark.parametrize("path", VALIDATION_FILES, ids=lambda p: p.name)
def test_validation_imports_no_sibling_layer_but_models(path: Path) -> None:
    """Ruling R-11: the existence checks take a ``Resolver``, never a store.

    `storage/` is the import that would quietly undo the ruling - DS-001 is one
    ``from agentprops.storage import ...`` away from being an I/O call inside a
    package specified as pure.
    """
    offences = sibling_import_offences(path, FORBIDDEN_LAYERS_FOR_VALIDATION)
    assert not offences, f"{path.name} imports a sibling layer: {offences}"


@pytest.mark.parametrize("path", VALIDATION_FILES, ids=lambda p: p.name)
def test_validation_does_no_io(path: Path) -> None:
    """Pure functions, no I/O - including the raw-text helper.

    `rawjson.py` exists for DS-013 and takes a *string*: the caller reads the
    file or the request body. If it ever opened a path itself, the rule would
    stop being a rule and become an I/O call.
    """
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name and name.rsplit(".", 1)[-1] in FORBIDDEN_IO_CALLS:
                offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} performs I/O: {offences}"


@pytest.mark.parametrize("path", VALIDATION_FILES, ids=lambda p: p.name)
def test_validation_reads_no_clock_and_no_random_source(path: Path) -> None:
    """Ruling R-09's grep, scoped: a rule that read a clock would not be a rule."""
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if name and (name in FORBIDDEN_CALLS or name.rsplit(".", 1)[-1] in FORBIDDEN_CALLS):
            offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} calls a clock or a random source: {offences}"


def test_the_expansion_file_table_is_not_empty() -> None:
    assert len(EXPANSION_FILES) > 1, f"no expansion modules found under {EXPANSION_DIR}"


@pytest.mark.parametrize("path", EXPANSION_FILES, ids=lambda p: p.name)
def test_expansion_imports_no_sibling_layer_but_models(path: Path) -> None:
    """`expansion/` is a peer of `validation/`, not a consumer of it."""
    offences = sibling_import_offences(path, FORBIDDEN_LAYERS_FOR_EXPANSION)
    assert not offences, f"{path.name} imports a sibling layer: {offences}"


@pytest.mark.parametrize("path", EXPANSION_FILES, ids=lambda p: p.name)
def test_expansion_reads_no_clock_and_no_random_source(path: Path) -> None:
    """Ground rule 9 at its source: this is the layer the rule is *about*.

    "All randomness inside the service flows through ``Seeded``" only means
    something if ``Seeded`` itself contains no randomness. So the same grep that
    protects `models/`, `validation/` and `storage/` runs here, and it is the
    only one of the four where the module's whole purpose would be defeated by a
    single call.

    ``uuid.UUID(bytes=...)`` is not caught and should not be: ruling R-10 bans
    *calls* to ``uuid4()`` and friends, not the type and the parser. M7 adds
    ``choice()`` and ``shuffled()`` as **methods**, whose definitions this guard
    does not see - but a call to ``random.choice`` inside one of them is
    ``choice`` by dotted suffix and would be caught, which is the case that
    matters.
    """
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if not name or name.startswith("self."):
            continue
        if name in FORBIDDEN_CALLS or name.rsplit(".", 1)[-1] in FORBIDDEN_CALLS:
            offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} calls a clock or a random source: {offences}"


@pytest.mark.parametrize("path", EXPANSION_FILES, ids=lambda p: p.name)
def test_expansion_does_no_io(path: Path) -> None:
    """A deterministic generator that read a file would not be deterministic."""
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name and name.rsplit(".", 1)[-1] in FORBIDDEN_IO_CALLS:
                offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} performs I/O: {offences}"


def test_the_expansion_guard_would_catch_a_random_source(tmp_path: Path) -> None:
    """The guard's own guard, in the shape M4's five layering guards were verified.

    A guard that has never been shown to fail is a guard nobody has tested. This
    feeds it a module that reads a clock and a random source and asserts that
    both are reported - so the parametrised test above is known to be doing
    something, rather than passing because `seeded.py` happens to be clean.
    """
    offending = tmp_path / "cheating.py"
    offending.write_text(
        "import random\nimport uuid\nfrom datetime import datetime\n\n"
        "def pick() -> object:\n"
        "    return (random.choice([1, 2]), uuid.uuid4(), datetime.now())\n",
        encoding="utf-8",
    )
    offences: list[str] = []
    for node in ast.walk(parse(offending)):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if not name or name.startswith("self."):
            continue
        if name in FORBIDDEN_CALLS or name.rsplit(".", 1)[-1] in FORBIDDEN_CALLS:
            offences.append(name)
    assert offences == ["random.choice", "uuid.uuid4", "datetime.now"]


#: Every module the two random-source guards cover. All six layers, because
#: ground rule 9 is stated absolutely - no bare ``random``, ``uuid4()`` or
#: ``datetime.now()`` in any generation or expansion path - and "which modules
#: are a generation path" is not a question a guard should have to answer.
GUARDED_FILES = (
    MODEL_FILES + VALIDATION_FILES + STORAGE_FILES + EXPANSION_FILES + SERVICE_FILES + SERVER_FILES
)


def random_module_imports(tree: ast.Module) -> list[str]:
    """Every import of a module in :data:`FORBIDDEN_RANDOM_MODULES`, either spelling."""
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offences.extend(
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in FORBIDDEN_RANDOM_MODULES
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in FORBIDDEN_RANDOM_MODULES
        ):
            offences.append(f"line {node.lineno}: from {node.module}")
    return offences


@pytest.mark.parametrize("path", GUARDED_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_module_imports_an_unseeded_random_source(path: Path) -> None:
    """Ground rule 9's other half, and the reason ``choice`` left the suffix list.

    ``Seeded.choice`` is the sanctioned random source (contracts section 9), so
    a guard matching the bare suffix ``choice`` reported `service/expansion.py`
    for doing exactly what ground rule 9 requires. The replacement is this:
    ``random`` and ``secrets`` may not be *imported* in any of the six layers,
    which closes the only route a bare ``choice(...)`` could have taken, while
    the dotted forms stay in :data:`FORBIDDEN_DOTTED_RANDOM_CALLS`.

    Strictly stronger than what it replaced rather than weaker: the suffix match
    could only see a call, and this sees the import that makes any call
    possible - including one this file has not thought of.
    """
    offences = random_module_imports(parse(path))
    assert not offences, (
        f"{path.name} imports an unseeded random source; the only randomness in this "
        f"service flows through expansion/seeded.py (ground rule 9): {offences}"
    )


def test_the_import_guard_would_catch_a_random_source_imported_by_name(tmp_path: Path) -> None:
    """The guard for the guard, in the shape the expansion one already has.

    Feeds it the exact spelling the suffix match used to catch and the call
    guard now cannot - ``from secrets import choice`` - and asserts both the
    module form and the by-name form are reported. Then feeds it the sanctioned
    source and asserts **neither** guard reports that, which is the half that
    makes removing ``choice`` from the suffix list a trade rather than a silent
    widening.
    """
    offending = tmp_path / "smuggled.py"
    offending.write_text(
        "import random\n"
        "from secrets import choice as pick\n"
        "\n"
        "def draw() -> object:\n"
        "    return pick([1, 2])\n",
        encoding="utf-8",
    )
    offences = random_module_imports(parse(offending))
    assert len(offences) == 2, offences
    assert "import random" in offences[0]
    assert "from secrets" in offences[1]

    clean = tmp_path / "seeded_use.py"
    clean.write_text(
        "from agentprops.expansion.seeded import Seeded\n"
        "\n"
        "def draw() -> object:\n"
        "    return Seeded(1).choice([1, 2])\n",
        encoding="utf-8",
    )
    assert random_module_imports(parse(clean)) == [], "the guard rejects the sanctioned source"
    reported = [
        dotted(node.func)
        for node in ast.walk(parse(clean))
        if isinstance(node, ast.Call)
        and dotted(node.func)
        and (
            dotted(node.func) in FORBIDDEN_CALLS
            or dotted(node.func).rsplit(".", 1)[-1] in FORBIDDEN_CALLS
        )
    ]
    assert reported == [], "Seeded.choice is reported as an unseeded random source"


def test_the_storage_file_table_is_not_empty() -> None:
    assert len(STORAGE_FILES) > 3, f"no storage modules found under {STORAGE_DIR}"


@pytest.mark.parametrize("path", STORAGE_FILES, ids=lambda p: p.name)
def test_storage_imports_no_sibling_layer_but_models(path: Path) -> None:
    """An adapter maps a model to a row. That is the whole of its dependencies."""
    offences = sibling_import_offences(path, FORBIDDEN_LAYERS_FOR_STORAGE)
    assert not offences, f"{path.name} imports a sibling layer: {offences}"


@pytest.mark.parametrize("path", STORAGE_FILES, ids=lambda p: p.name)
def test_storage_reads_no_clock_and_no_random_source(path: Path) -> None:
    """Ruling R-09, on the layer where breaking it would be easiest.

    Every timestamp a row holds arrives from a column default, from the client,
    from authored content in the document, or from ``Seeded``. Two rows make
    that concrete: ``datasets.created_at`` is populated from
    ``provenance.created_at``, and ``run_steps.fetched_at`` falls back to
    ``DEFAULT now()`` - which is a *SQL* function, evaluated by the database,
    and therefore not a call this guard looks at.

    An adapter is also where ``uuid4()`` would be most tempting, and ruling
    R-10 is explicit: importing ``uuid`` for the type and the parser is allowed,
    calling ``uuid4()`` is not. Ids come from ``Seeded.uuid()`` at M5/M7.
    """
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if name and (name in FORBIDDEN_CALLS or name.rsplit(".", 1)[-1] in FORBIDDEN_CALLS):
            offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} calls a clock or a random source: {offences}"


def test_no_rule_raises_for_a_validation_failure() -> None:
    """CLAUDE.md's style rule, checked at the source.

    Structured errors, never exceptions, for anything a user could cause. The
    two ``raise`` statements this allows are both programming-error guards - a
    rule reading the blueprint without declaring ``needs_blueprint``, and a
    corpus anchor that no longer matches - so the check is that a *rule
    function* contains no ``raise`` at all.
    """
    offences: list[str] = []
    for path in VALIDATION_FILES:
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not (node.name.startswith(("bp_", "ds_", "sk_"))):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Raise):
                    offences.append(f"{path.name}:{inner.lineno} in {node.name}")
    assert not offences, f"a rule raises instead of reporting a finding: {offences}"


@pytest.mark.parametrize("path", MODEL_FILES, ids=lambda p: p.name)
def test_models_read_no_clock_and_no_random_source(path: Path) -> None:
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if not name:
            continue
        if name in FORBIDDEN_CALLS or name.rsplit(".", 1)[-1] in FORBIDDEN_CALLS:
            offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} calls a clock or a random source: {offences}"


@pytest.mark.parametrize("path", MODEL_FILES, ids=lambda p: p.name)
def test_models_do_no_io(path: Path) -> None:
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if name and name.rsplit(".", 1)[-1] in FORBIDDEN_IO_CALLS:
                offences.append(f"line {node.lineno}: {name}()")
    assert not offences, f"{path.name} performs I/O: {offences}"


@pytest.mark.parametrize("path", MODEL_FILES, ids=lambda p: p.name)
def test_models_declare_no_catalogue_constraints(path: Path) -> None:
    """Ruling R-04, checked at the source: no value constraints, no validators.

    Five routes a constraint could take in, and all five are covered:

    1. a ``Field()`` keyword - ``min_length``, ``pattern``, ``ge``
    2. a ``Literal[...]`` annotation
    3. a ``@field_validator``/``@model_validator`` method
    4. a **constrained type** - ``PositiveInt``, ``conint(gt=0)``,
       ``Annotated[int, Ge(0)]``, ``StringConstraints(...)``,
       ``AfterValidator(...)`` - which needs no ``Field()`` call at all and is
       the likeliest future violation
    5. a **model-wide** ``ConfigDict`` value constraint such as
       ``str_min_length``, which would apply to every ``str`` field at once
    """
    tree = parse(path)
    offences: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = dotted(node.func).rsplit(".", 1)[-1]
            if called == "Field":
                for keyword in node.keywords:
                    if keyword.arg is not None and keyword.arg not in ALLOWED_FIELD_KEYWORDS:
                        offences.append(f"line {node.lineno}: Field({keyword.arg}=...)")
            elif called == "ConfigDict":
                for keyword in node.keywords:
                    if keyword.arg is not None and keyword.arg not in ALLOWED_CONFIG_KEYWORDS:
                        offences.append(f"line {node.lineno}: ConfigDict({keyword.arg}=...)")
        if isinstance(node, ast.Subscript) and dotted(node.value).rsplit(".", 1)[-1] == "Literal":
            offences.append(f"line {node.lineno}: Literal[...]")
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if dotted(target).rsplit(".", 1)[-1] in VALIDATOR_DECORATORS:
                    offences.append(f"line {node.lineno}: @{dotted(target)} on {node.name}")
        # Route 4: match the bare identifier wherever it appears - in an
        # annotation, in a call, or in the import that brought it in. Narrowed
        # to the two node types that carry an identifier before reading
        # ``lineno``, which the generic ``ast.AST`` does not declare.
        if isinstance(node, ast.Name | ast.Attribute):
            identifier = node.id if isinstance(node, ast.Name) else node.attr
            if identifier in CONSTRAINED_TYPE_NAMES:
                offences.append(f"line {node.lineno}: constrained type {identifier}")
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if alias.name in CONSTRAINED_TYPE_NAMES:
                    offences.append(f"line {node.lineno}: imports {alias.name}")

    assert not offences, (
        f"{path.name} encodes catalogue policy in the model (ruling R-04): {sorted(set(offences))}"
    )


def test_every_protocol_type_from_ruling_r_05_exists() -> None:
    """The six types contracts section 6 names but never defines, plus R-06/R-07."""
    for name in (
        "Skeleton",
        "Section",
        "BlueprintSummary",
        "DatasetQuery",
        "DatasetSummary",
        "RunQuery",
        "RunSummary",
        "StoreHealth",
        "FaultSpec",
    ):
        assert hasattr(agentprops.models, name), f"models.{name} is missing"
        assert name in agentprops.models.__all__, f"models.{name} is not exported"


# --------------------------------------------------------------- M4: the two
# layers above, and the three properties CLAUDE.md's layering rule asserts.


def test_the_service_file_table_is_not_empty() -> None:
    assert len(SERVICE_FILES) > 3, f"no service modules found under {SERVICE_DIR}"


def test_the_server_file_table_is_not_empty() -> None:
    assert len(SERVER_FILES) > 3, f"no server modules found under {SERVER_DIR}"


def test_the_tool_module_table_is_not_empty() -> None:
    """``TOOL_MODULES`` is what makes the guards below cover a *new* tool module."""
    assert len(TOOL_MODULE_FILES) == 4, f"expected four tool modules, got {TOOL_MODULE_FILES}"


@pytest.mark.parametrize("path", SERVICE_FILES, ids=lambda p: p.name)
def test_service_does_not_import_the_server(path: Path) -> None:
    offences = sibling_import_offences(path, FORBIDDEN_LAYERS_FOR_SERVICE)
    assert not offences, f"{path.name} imports the layer above it: {offences}"


@pytest.mark.parametrize("path", SERVER_FILES, ids=lambda p: p.name)
def test_server_imports_only_service_and_models(path: Path) -> None:
    """The guard that makes "no business logic in `server/`" mechanical.

    A tool function cannot validate a document or query a store without
    importing `validation/` or `storage/`, so forbidding those two forbids the
    thing rather than the symptom. ``ServiceContext`` and the ``Reply`` type
    both reach `server/` through `service/`, which is why `service/__init__.py`
    re-exports them.
    """
    offences = sibling_import_offences(path, FORBIDDEN_LAYERS_FOR_SERVER)
    assert not offences, f"{path.name} reaches past service/: {offences}"


@pytest.mark.parametrize("path", SERVICE_FILES + SERVER_FILES, ids=lambda p: p.name)
def test_only_the_clock_module_reads_a_clock(path: Path) -> None:
    """Ruling R-09: **a single** injected ``Clock``, used only in `service/`.

    `service/clock.py` is exempt because it *is* the port - ``SystemClock.now``
    is the one sanctioned ``datetime.now()`` call in `src/`. Everywhere else, a
    clock reading would put an unfrozen timestamp into a response and break M4's
    byte-identical-output criterion, or into a stored row and break
    ``dataset_find``'s reproducible ordering.
    """
    if path.name == CLOCK_MODULE and path.parent == SERVICE_DIR:
        pytest.skip("service/clock.py is the port; its datetime.now() is the sanctioned one")
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if name and (name in FORBIDDEN_CALLS or name.rsplit(".", 1)[-1] in FORBIDDEN_CALLS):
            offences.append(f"line {node.lineno}: {name}()")
    assert not offences, (
        f"{path.name} calls a clock or a random source; the only clock is "
        f"service/{CLOCK_MODULE}'s injected port: {offences}"
    )


def tool_functions(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every module-level function in ``path`` decorated with ``@mcp.tool()``.

    Matched on the decorator rather than on the name, so a helper in a
    `tools_*.py` module is not measured as a tool and a tool cannot escape the
    budget by being named something else.
    """
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in parse(path).body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if dotted(target).endswith("mcp.tool"):
                found.append(node)
                break
    return found


def body_without_docstring(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.stmt]:
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        return body[1:]
    return body


def test_every_tool_module_registers_at_least_one_tool() -> None:
    """Otherwise the size guard below would pass on an empty list."""
    for path in TOOL_MODULE_FILES:
        assert tool_functions(path), f"{path.name} registers no @mcp.tool() function"


@pytest.mark.parametrize("path", TOOL_MODULE_FILES, ids=lambda p: p.name)
def test_a_tool_function_stays_under_twenty_lines(path: Path) -> None:
    """CLAUDE.md: "a tool function ... parses, delegates, shapes the response,
    and stays under 20 lines"."""
    offences: list[str] = []
    for function in tool_functions(path):
        body = body_without_docstring(function)
        if not body:
            offences.append(f"{function.name}: empty body")
            continue
        lines = (body[-1].end_lineno or body[-1].lineno) - body[0].lineno + 1
        if lines >= MAX_TOOL_FUNCTION_LINES:
            offences.append(f"{function.name}: {lines} lines")
        if len(body) > MAX_TOOL_FUNCTION_STATEMENTS:
            offences.append(f"{function.name}: {len(body)} statements")
    assert not offences, (
        f"{path.name} has tool functions over budget ({MAX_TOOL_FUNCTION_LINES} lines, "
        f"{MAX_TOOL_FUNCTION_STATEMENTS} statements): {offences}"
    )


@pytest.mark.parametrize("path", TOOL_MODULE_FILES, ids=lambda p: p.name)
def test_a_tool_function_contains_no_control_flow_but_the_argument_guard(path: Path) -> None:
    """ "No business logic in `server/`", as a shape rather than as a judgement.

    The only branch a tool function is allowed is the one that returns early
    when an argument reader complained. A loop, a ``try``, a comprehension over
    store rows or a second ``if`` means a decision is being made here that
    belongs in `service/`, where it can be tested without an MCP client.
    """
    offences: list[str] = []
    for function in tool_functions(path):
        for node in ast.walk(function):
            if isinstance(node, ast.For | ast.While | ast.Try | ast.ListComp | ast.DictComp):
                offences.append(f"{function.name}: {type(node).__name__} at line {node.lineno}")
            if isinstance(node, ast.If) and "args.errors" not in ast.unparse(node.test):
                offences.append(f"{function.name}: if {ast.unparse(node.test)}")
    assert not offences, f"{path.name} carries logic that belongs in service/: {offences}"


@pytest.mark.parametrize("path", TOOL_MODULE_FILES, ids=lambda p: p.name)
def test_a_tool_function_never_raises(path: Path) -> None:
    """CLAUDE.md: structured errors, never exceptions, for anything a user causes.

    The same check `validation/` already has on its rule functions. An
    exception escaping a tool function reaches the caller as an MCP protocol
    error with no rule id and no pointer, which is the one shape the error
    envelope exists to prevent.
    """
    offences = [
        f"{function.name}:{node.lineno}"
        for function in tool_functions(path)
        for node in ast.walk(function)
        if isinstance(node, ast.Raise)
    ]
    assert not offences, f"a tool function raises instead of returning an envelope: {offences}"
