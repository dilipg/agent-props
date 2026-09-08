"""Emit the Blueprint and Dataset JSON Schemas from the models into `schemas/`.

Run it with::

    uv run python -m agentprops.schema_export

The web app needs these at M9 for live validation, so they are generated and
committed rather than produced at install time. `tests/unit/test_schemas.py`
asserts the committed files are byte-identical to a fresh generation, which is
what stops them drifting from the models.

This module lives outside `models/` on purpose: it writes files, and `models/`
does no I/O. It imports nothing but `models/`, so it violates no layering rule.

The emitted schemas carry **shape only, not policy** (ruling R-04). A document
that satisfies them can still be rejected by the validation catalogue - that
division is intended: the web app's live validation shows shape errors as you
type, and the tool surface supplies rule errors on submit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agentprops.models import Blueprint, Dataset

__all__ = ["SCHEMAS", "SCHEMA_DIALECT", "build_schema", "render", "schemas_dir", "write_schemas"]

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

#: Output filename to the model it is generated from. Only these two: they are
#: the documents a user authors and the web app validates. Every other model is
#: service-built and needs no wire schema.
SCHEMAS: dict[str, type[BaseModel]] = {
    "blueprint.schema.json": Blueprint,
    "dataset.schema.json": Dataset,
}


def schemas_dir() -> Path:
    """The repository's `schemas/` directory.

    Resolved from this file rather than the working directory, so the command
    works from anywhere. ``src/agentprops/schema_export.py`` is three parents
    below the repository root.
    """
    return Path(__file__).resolve().parents[2] / "schemas"


def build_schema(filename: str, model: type[BaseModel]) -> dict[str, Any]:
    """Build one schema document for ``model``.

    Pydantic already targets Draft 2020-12 and generates ``by_alias``, which is
    what makes the wire names ``from`` and ``schema`` come out right. It does
    not emit ``$schema`` or ``$id``; both are added here so a bare validator
    picks the right dialect without being told.
    """
    generated = model.model_json_schema(mode="validation")
    document: dict[str, Any] = {
        "$schema": SCHEMA_DIALECT,
        "$id": f"https://agent-props.dev/schemas/{filename}",
        "$comment": (
            f"Generated from agentprops.models.{model.__name__} by "
            "`python -m agentprops.schema_export`. Do not edit by hand. "
            "Carries shape only, not the validation catalogue."
        ),
    }
    document.update(generated)
    return document


def render(filename: str, model: type[BaseModel]) -> str:
    """Serialise one schema exactly as it is committed, trailing newline and all."""
    return json.dumps(build_schema(filename, model), indent=2, ensure_ascii=False) + "\n"


def write_schemas(target: Path | None = None) -> list[Path]:
    """Write every schema in :data:`SCHEMAS`. Returns the paths written."""
    directory = target if target is not None else schemas_dir()
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMAS.items():
        path = directory / filename
        path.write_text(render(filename, model), encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    for path in write_schemas():
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
