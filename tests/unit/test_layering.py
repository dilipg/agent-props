"""`models/` and `validation/` are pure: no sibling layers, no I/O, no clock.

CLAUDE.md's layering rule says review enforces this. A test is cheaper than a
review round, and three of the properties here are rulings that later milestones
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

Everything is checked against the parsed AST rather than by grepping text, so
docstrings that discuss ``min_length`` or ``uuid4()`` - and this file does -
cannot trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agentprops.models
import agentprops.validation

MODELS_DIR = Path(agentprops.models.__file__).resolve().parent
MODEL_FILES = sorted(MODELS_DIR.glob("*.py"))

VALIDATION_DIR = Path(agentprops.validation.__file__).resolve().parent
VALIDATION_FILES = sorted(VALIDATION_DIR.glob("*.py"))

#: `models/` may import none of these. `export/` is in the list even though
#: CLAUDE.md's arrow diagram omits it: it is a sibling layer either way.
FORBIDDEN_LAYERS = frozenset({"validation", "storage", "service", "server", "expansion", "export"})

#: `validation/` may import `models/`, and nothing else sideways. `storage/` is
#: the one that matters: ruling R-11 exists precisely so that the three
#: existence checks do not reach for it.
FORBIDDEN_LAYERS_FOR_VALIDATION = frozenset({"storage", "service", "server", "expansion", "export"})

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
        # annotation, in a call, or in the import that brought it in.
        identifier = (
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else None
        )
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
