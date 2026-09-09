"""Seed a demo store with the worked-example agent, for reviewing the web app.

Writes through the **web app's own write path** — `blueprint_upsert` then
`dataset_import` — for the reason `tests/unit/test_web_review_surface.py`
records: that is the path clause 5 constrains, so a demo seeded any other way
would show a store this app never writes.

Usage:  uv run python scripts/seed_demo.py demo.db
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agentprops.service import sqlite_context
from agentprops.service.blueprints import upsert as blueprint_upsert
from agentprops.service.promotion import import_bundle

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
BLUEPRINT = FIXTURES / "blueprints" / "location-onboarding-1.0.0.json"
DATASETS = [
    FIXTURES / "datasets" / "priya-missing-docs.json",
    FIXTURES / "datasets" / "arun-escalated.json",
]


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document: dict[str, Any] = json.load(handle)
    return document


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo.db")
    if target.exists():
        target.unlink()

    context = sqlite_context(target)
    blueprint = load(BLUEPRINT)

    published = blueprint_upsert(context, blueprint, publish=True)
    if not published.ok:
        print("blueprint_upsert failed:", published.model_dump_json(indent=2))
        return 1
    print(f"published {blueprint['agent_id']} {blueprint['version']}")

    datasets = [load(path) for path in DATASETS]
    bundle = {
        "format": "agentprops.bundle",
        "format_version": 1,
        "agent_id": blueprint["agent_id"],
        # Empty for the reason web/src/mcp/tools.ts records: import validates
        # against a resolver answering from the store *plus* the bundle, so a
        # dataset whose blueprint is already published needs no re-carry.
        "blueprints": [],
        "datasets": datasets,
    }
    imported = import_bundle(context, bundle)
    if not imported.ok:
        print("dataset_import failed:", imported.model_dump_json(indent=2))
        return 1

    for dataset in datasets:
        provenance = dataset["provenance"]
        print(f"  imported {provenance['title']!r} by {provenance['author']['handle']}")

    print(f"\nstore ready at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
