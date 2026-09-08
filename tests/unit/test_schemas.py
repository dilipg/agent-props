"""The emitted JSON Schemas: valid, committed, and accepted by the fixtures.

Three things are asserted, and they fail for three different reasons:

1. Each emitted document is itself a legal Draft 2020-12 schema.
2. The committed file under `schemas/` is byte-identical to a fresh emission,
   so the web app can never be validating against a stale shape.
3. Every golden fixture validates against the schema for its kind, under an
   explicit ``Draft202012Validator``.

Point 3 is the second half of M1's acceptance criterion, and it is table-driven
over the same fixture directories the round-trip test walks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from agentprops.schema_export import SCHEMA_DIALECT, SCHEMAS, build_schema, render, schemas_dir
from conftest import FIXTURES_DIR

BLUEPRINT_FIXTURES = sorted((FIXTURES_DIR / "blueprints").glob("*.json"))
DATASET_FIXTURES = sorted((FIXTURES_DIR / "datasets").glob("*.json"))

SCHEMA_CASES = sorted(SCHEMAS.items())


def case_id(value: object) -> str:
    return str(getattr(value, "__name__", value))


def load(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def committed(filename: str) -> dict[str, Any]:
    return load(schemas_dir() / filename)


def report(validator: Draft202012Validator, document: dict[str, Any]) -> str:
    return "\n".join(
        f"  {error.json_path}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    )


@pytest.mark.parametrize(("filename", "model"), SCHEMA_CASES, ids=case_id)
def test_emitted_schema_is_valid_draft_2020_12(filename: str, model: type[BaseModel]) -> None:
    schema = build_schema(filename, model)
    assert schema["$schema"] == SCHEMA_DIALECT
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(("filename", "model"), SCHEMA_CASES, ids=case_id)
def test_committed_schema_is_up_to_date(filename: str, model: type[BaseModel]) -> None:
    path = schemas_dir() / filename
    assert path.is_file(), f"missing generated schema: {path}"
    assert path.read_text(encoding="utf-8") == render(filename, model), (
        f"{filename} is stale. Regenerate with: uv run python -m agentprops.schema_export"
    )


@pytest.mark.parametrize("path", BLUEPRINT_FIXTURES, ids=lambda p: p.name)
def test_blueprint_schema_validates_fixture(path: Path) -> None:
    validator = Draft202012Validator(committed("blueprint.schema.json"))
    document = load(path)
    assert validator.is_valid(document), f"{path.name}:\n{report(validator, document)}"


@pytest.mark.parametrize("path", DATASET_FIXTURES, ids=lambda p: p.name)
def test_dataset_schema_validates_fixture(path: Path) -> None:
    validator = Draft202012Validator(committed("dataset.schema.json"))
    document = load(path)
    assert validator.is_valid(document), f"{path.name}:\n{report(validator, document)}"


def test_schema_carries_shape_not_policy() -> None:
    """Ruling R-04, asserted on the artefact the web app actually consumes.

    If a future change adds a catalogue constraint to a model, it shows up here
    as a keyword in the emitted schema and this test names it. ``pattern`` and
    the numeric bounds are the ones that would silently turn a rule id into a
    shape error.
    """
    forbidden = {
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "exclusiveMinimum",
        "enum",
        "const",
    }
    found: list[str] = []

    def walk(node: Any, pointer: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in forbidden:
                    found.append(f"{pointer}/{key}")
                walk(value, f"{pointer}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{pointer}/{index}")

    for filename in SCHEMAS:
        walk(committed(filename), f"/{filename}")

    assert not found, f"catalogue constraints leaked into the emitted schemas: {found}"
