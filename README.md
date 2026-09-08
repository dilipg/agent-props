# agent-props

A blueprint-driven narrative fixture service for agentic systems.

A **blueprint** describes an agent's steps as a graph (nodes, edges, entity schemas, label
vocabulary). A **dataset** is one hand-authored, immutable, versioned world for that blueprint: a
fixture per node, a coherent entity timeline, a narrative, and an expected outcome. A running agent
calls `fetch_step` over MCP and gets its step's fixture back. Instead of mocking each tool call by
hand, you author the world once and run the agent through it.

See [`CLAUDE.md`](CLAUDE.md) for the invariants and layering rules that govern this codebase, and
[`docs/`](docs/) for the full specification: [`docs/prd.md`](docs/prd.md) (product rationale),
[`docs/build-handoff.md`](docs/build-handoff.md) (stack, repo layout, milestone plan),
[`docs/contracts.md`](docs/contracts.md) (schemas, rule catalogue, tool signatures), and
[`docs/worked-example.md`](docs/worked-example.md) (the fixtures under `tests/fixtures/`).

## Status

Phase 1 is being built one milestone at a time, M0 through M11 (see
[`docs/build-handoff.md`](docs/build-handoff.md) section 4). This repository currently has the M0
scaffold (package layout, tooling, CI, golden fixtures), the M1 domain models in
`src/agentprops/models/`, the M2 validator in `src/agentprops/validation/` — every `BP-*` and
`DS-*` rule in [`docs/contracts.md`](docs/contracts.md) section 3, with a rule-id-to-callable
registry and a declarative rejection corpus — the M3 `Store` Protocol and SQLite adapter in
`src/agentprops/storage/`, and the M4 MCP surface: `src/agentprops/service/` and
`src/agentprops/server/`, thirteen tools over stdio and streamable HTTP. `expansion/` and `export/`
are empty modules waiting on M7.

Not yet built: the skeleton pipeline (`dataset_skeleton`, `dataset_fill_part`, `dataset_submit`, M5),
the runtime (`run_start`, `fetch_step`, M6), expansion and export/import (M7), and the web app (M9).
`tests/unit/test_tool_surface.py` lists exactly which documented tools are still deferred, and to
which milestone.

## Validate a document

```python
import json
from agentprops.validation import validate_blueprint, validate_dataset

envelope = validate_blueprint(json.loads(blueprint_text))
envelope.ok  # False if any error-severity rule fired
envelope.errors  # RuleError(rule, severity, pointer, message, section, context)
```

Both entry points take the raw `dict` off `json.loads`, never a parsed model, because every finding
carries an RFC 6901 pointer into the *submitted* document. Neither ever raises for something a
document did; warnings (`BP-019`, `DS-007`, `DS-027`, `DS-032`) report `ok: true` and ride along in
`errors` with `severity: "warning"`. `validate_dataset` takes a `Resolver` — two read-only lookups
for the three rules that are existence checks — so `validation/` stays pure and imports no storage.

## Store a document

```python
from agentprops.storage import SqlStore, create_schema, sqlite_url

store = SqlStore.from_url(sqlite_url("agentprops.db"))  # or: alembic upgrade head
store.put_blueprint(blueprint, publish=True)
version_1 = store.put_dataset(dataset)  # the store allocates the version
version_2 = store.put_dataset(edited)  # copy-on-write; version 1 stays readable
store.set_archived(str(dataset.id), True)  # a flag, never a delete
```

Everything above `storage/` goes through the `Store` Protocol in
[`src/agentprops/storage/base.py`](src/agentprops/storage/base.py), never through an adapter
directly. It has no `delete_*` method and never will: archive is a flag, a dataset edit is
copy-on-write, and two tests hold the line — one on the Protocol's shape, one on the AST of every
module in the package.

## Serve the tools

```bash
uv run python -m agentprops.server --store agentprops.db                     # stdio
uv run python -m agentprops.server --store agentprops.db --transport http    # streamable HTTP
```

Thirteen tools: `blueprint_upsert`, `blueprint_get`, `blueprint_list`, `blueprint_validate`,
`blueprint_diff`, `dataset_find`, `dataset_get`, `dataset_archive`, `dataset_restore`,
`dataset_validate`, `store_status`, `label_vocabulary`, `agent_list`.

Every tool answers one of the two envelopes in [`docs/contracts.md`](docs/contracts.md) section 1,
and never raises for anything that reaches it — a malformed argument, an unknown id, a rule
violation, an out-of-range integer and a store refusal are all `{"ok": false, "errors": [...]}` with
a rule id and an RFC 6901 pointer. (A request malformed at the transport or MCP-SDK layer never
reaches a tool function at all, so it comes back as a protocol error instead; ruling R-40 settles
that the envelope contract binds the code in this repository.) A policy problem is a **warning** on
a successful response: `blueprint_diff` never fails, and `dataset_get` returns an archived dataset
with a `dataset_archived` warning.

In-process, for a test or a script:

```python
from mcp import Client
from agentprops.server import binding, mcp
from agentprops.service import sqlite_context

with binding(sqlite_context("agentprops.db")):
    async with Client(mcp) as client:  # no subprocess, no transport
        result = await client.call_tool("store_status", {})
```

## Migrate a database

```bash
uv run alembic upgrade head                     # apply, using AGENTPROPS_DB_URL or alembic.ini
uv run alembic check                            # does the live schema match storage/sql.py?
AGENTPROPS_DB_URL=sqlite+pysqlite:///dev.db uv run alembic upgrade head
```

The schema is declared once, as SQLAlchemy Core tables in
[`src/agentprops/storage/sql.py`](src/agentprops/storage/sql.py), and `migrations/env.py` hands that
same metadata to Alembic — so `alembic check` is a real drift gate rather than a comparison between
two copies of the truth.

## Install

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

This creates `.venv/` and installs both the service dependencies and the dev tools (`pytest`,
`ruff`, `mypy`).

## Run the checks

```bash
uv run ruff check          # lint
uv run ruff format --check # formatting
uv run mypy src            # types, strict
uv run pytest              # tests
uv run pytest -m integration --store sqlite   # integration tests, once a backend exists
```

The first four must pass before any commit. The integration run is opt-in: it needs a storage
backend, which arrives at M3.

## Regenerate the JSON Schemas

```bash
uv run python -m agentprops.schema_export
```

`schemas/blueprint.schema.json` and `schemas/dataset.schema.json` are generated from the Pydantic
models and committed, because the web app validates against them in the browser. Run this after any
change to `models/blueprint.py` or `models/dataset.py`; `tests/unit/test_schemas.py` fails if the
committed files are stale. The schemas carry **shape only, not policy** — a document can satisfy
them and still be rejected by the validation catalogue.

## Repository layout

```
src/agentprops/   the service: models, validation, storage, service, server, expansion, export
schemas/          Blueprint and Dataset JSON Schemas, generated from the models
tests/            unit/, integration/, and fixtures/ (blueprints, datasets, broken)
docs/             the specification (PRD, build handoff, contracts, worked example)
```

`client/python`, `client/typescript`, `web/`, and the container files arrive with the milestones
that own them (M8, M11, M9, M7 respectively) — they are not part of the M0 scaffold.
