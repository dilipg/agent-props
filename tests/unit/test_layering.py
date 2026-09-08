"""`models/` is pure: no sibling layers, no I/O, no clock, no policy.

CLAUDE.md's layering rule says review enforces this. A test is cheaper than a
review round, and two of the four properties here are rulings that later
milestones depend on staying true:

- **R-04** - models are shapes, the catalogue is policy. A ``pattern`` or a
  ``Literal`` slipped into a model turns ten corpus cases from "reports a rule
  id" into "raises ``ValidationError``", and M2's gate fails.
- **R-09 / R-10** - no clock and no random source in `models/`. Importing
  ``uuid`` for the ``UUID`` type and parser is explicitly allowed; *calling*
  ``uuid4()`` is not.

Everything is checked against the parsed AST rather than by grepping text, so
docstrings that discuss ``min_length`` or ``uuid4()`` - and this file does -
cannot trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agentprops.models

MODELS_DIR = Path(agentprops.models.__file__).resolve().parent
MODEL_FILES = sorted(MODELS_DIR.glob("*.py"))

#: `models/` may import none of these. `export/` is in the list even though
#: CLAUDE.md's arrow diagram omits it: it is a sibling layer either way.
FORBIDDEN_LAYERS = frozenset({"validation", "storage", "service", "server", "expansion", "export"})

#: Calls that read a clock or a random source, by dotted suffix.
FORBIDDEN_CALLS = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "date.today",
        "time.time",
        "time.monotonic",
        "uuid1",
        "uuid3",
        "uuid4",
        "uuid5",
        "random",
        "randint",
        "choice",
        "shuffle",
        "getrandbits",
        "token_hex",
        "token_bytes",
    }
)

#: Calls that touch the filesystem or the network.
FORBIDDEN_IO_CALLS = frozenset(
    {"open", "read_text", "write_text", "read_bytes", "write_bytes", "urlopen", "connect"}
)

#: ``Field(...)`` keywords that describe a shape. Anything else is a value
#: constraint, which ruling R-04 assigns to the catalogue.
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
    }
)

VALIDATOR_DECORATORS = frozenset(
    {"field_validator", "model_validator", "validator", "root_validator", "AfterValidator"}
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


@pytest.mark.parametrize("path", MODEL_FILES, ids=lambda p: p.name)
def test_models_import_no_sibling_layer(path: Path) -> None:
    offences: list[str] = []
    for node in ast.walk(parse(path)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            parts = module.split(".")
            if "agentprops" in parts:
                tail = parts[parts.index("agentprops") + 1 :]
                if tail and tail[0] in FORBIDDEN_LAYERS:
                    offences.append(f"line {node.lineno}: {module}")
    assert not offences, f"{path.name} imports a sibling layer: {offences}"


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

    Covers three ways a constraint could get in: a ``Field()`` keyword such as
    ``min_length`` or ``ge``, a ``Literal[...]`` annotation, and a
    ``@field_validator``/``@model_validator`` method.
    """
    tree = parse(path)
    offences: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and dotted(node.func).rsplit(".", 1)[-1] == "Field":
            for keyword in node.keywords:
                if keyword.arg is not None and keyword.arg not in ALLOWED_FIELD_KEYWORDS:
                    offences.append(f"line {node.lineno}: Field({keyword.arg}=...)")
        if isinstance(node, ast.Subscript) and dotted(node.value).rsplit(".", 1)[-1] == "Literal":
            offences.append(f"line {node.lineno}: Literal[...]")
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if dotted(target).rsplit(".", 1)[-1] in VALIDATOR_DECORATORS:
                    offences.append(f"line {node.lineno}: @{dotted(target)} on {node.name}")

    assert not offences, (
        f"{path.name} encodes catalogue policy in the model (ruling R-04): {offences}"
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
