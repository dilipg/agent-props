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
`src/agentprops/storage/`, the M4 MCP surface — `src/agentprops/service/` and
`src/agentprops/server/`, over stdio and streamable HTTP — and the M5 skeleton pipeline:
`dataset_skeleton`, `dataset_fill_part` and `dataset_submit` in
`src/agentprops/service/skeletons.py`, the five `SK-*` rules, and `Seeded.uuid()` in
`src/agentprops/expansion/seeded.py`, and the M6 runtime read path — `run_start`, `fetch_step`,
`run_get` and `run_find` in `src/agentprops/service/runs.py`, with step identity resolution in
`src/agentprops/service/resolution.py`. Twenty tools. `export/` is an empty module waiting on M7,
and `expansion/` holds `uuid()` only.

Not yet built: expansion beyond ids and export/import (M7), the Python client and `record_step`
(M8), and the web app (M9). `tests/unit/test_tool_surface.py` lists exactly which documented tools
are still deferred, and to which milestone.

## Author a dataset

```python
# manifest order: provenance, entities, nodes.core, nodes.branches, expected
started = call(
    "dataset_skeleton",
    agent_id="location-onboarding",
    version="1.0.0",
    labels={"persona": "multi-unit-operator", ...},
    seed=20260908,
)
skeleton = started["data"]["skeleton"]
for section in skeleton["manifest"]:
    call(
        "dataset_fill_part",
        skeleton_id=skeleton["skeleton_id"],
        section=section["id"],
        content={target.lstrip("/"): ... for target in section["pointers"]},
    )
call("dataset_submit", skeleton_id=skeleton["skeleton_id"])
```

Sections are filled in manifest order (SK-002) and a section's `content` is a fragment of the
dataset document, keyed by the top-level fields its `pointers` name. Re-filling a filled section is
allowed and replaces it: a rejection carries the `section` to repair, so an LLM fixes one part
rather than regenerating the whole dataset. `dataset_submit` runs every `DS-*` rule over the
assembled document and stores it; a skeleton becomes exactly one dataset (SK-005).

## Run an agent through a dataset

```python
started = call(
    "run_start",
    run_id=str(uuid4()),  # the client generates it, before the first call
    agent_id="location-onboarding",
    selector={"labels": {"scenario": "missing-documents"}},  # or {"dataset_id": "..."}
    declared_blueprint_version="1.0.0",
)
pin = started["data"]["start"]["pin"]  # {dataset_id, dataset_version, blueprint_version}

call("fetch_step", run_id=run_id, node_id="receive_request")
call("fetch_step", run_id=run_id, tool_name="delightree.stores.get")  # resolved by position
call("fetch_step", run_id=run_id, node_id="request_docs", iteration=1)  # a pool draw
call("run_get", run_id=run_id)  # the run, its steps, and its reconstructed path
```

`run_start` pins one dataset version and one blueprint version, and the run reads that pin for its
whole life. A `declared_blueprint_version` other than the pinned one warns and serves anyway;
nothing on this path refuses to serve. Calling it again with the same `run_id` returns the same run
and never re-pins — a retry after a network blip must not create a second run — and warns with
`run_start_mismatch` if the arguments diverge from the run that exists.

Address a step by `node_id`, or by `tool_name` — a tool name several nodes declare is resolved
against where the run currently is, and an ambiguity that position cannot settle comes back as
`RT-E01` naming every candidate node id rather than a guess. `iteration` draws from a `pool: true`
node's fixtures in order; past the end the last entry repeats with a `pool_exhausted` warning,
which is deliberate — an agent that loops one extra time should not get a hard failure.

`fetch_step` is idempotent on `(run_id, node_id, iteration)`: the same key returns the identical
fixture and records nothing new, so a retry an hour later resolves to the same answer. It writes
the served step **to the run** and never to a dataset;
`tests/unit/test_runtime_is_read_only.py` enforces that on the call graph and behaviourally.

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

Twenty tools. Blueprint: `blueprint_upsert`, `blueprint_get`, `blueprint_list`,
`blueprint_validate`, `blueprint_diff`. Dataset: `dataset_skeleton`, `dataset_fill_part`,
`dataset_submit`, `dataset_validate`, `dataset_find`, `dataset_get`, `dataset_archive`,
`dataset_restore`. Run: `run_start`, `fetch_step`, `run_get`, `run_find`. Admin: `store_status`,
`label_vocabulary`, `agent_list`.

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
