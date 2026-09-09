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
`src/agentprops/service/resolution.py`, and the M7 backends and promotion path: the Postgres and
Mongo adapters in `src/agentprops/storage/`, `Dockerfile` and `docker-compose.yml`, and
`dataset_expand`, `dataset_export` and `dataset_import`, and the M8 Python client in
`client/python/` — a separately installable `agent-props-client` with the three comparison helpers
— alongside its two server-side writes, `record_step` and `run_finish`. **Twenty-five tools, three
backends, two packages.** `export/` is an empty module waiting on M10.

Not yet built: the web app (M9), the evidence bundle (M10), the TypeScript client (M11).
`tests/unit/test_tool_surface.py` lists exactly which documented tools are still deferred, and to
which milestone.

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

Then report what the agent did and close the run:

```python
call("record_step", run_id=run_id, node_id="receive_request", actual={"store_id": "ST-4471"})
call("run_finish", run_id=run_id, outcome={"onboarding_status": "complete"}, status="finished")
```

`record_step` addresses a step exactly as `fetch_step` does and keys on the resolved node, so a
step fetched by tool name can be recorded by node id. It is **write-once** per step, and a repeat
splits by whether the value differs (ruling R-65): the identical `actual` again is a no-op success
carrying `step_actual_already_recorded`, so a retry after a network blip is safe and visible, while
a *different* one is `AP-007` and **nothing is written**. An actual for a step that was never
fetched is `AP-004`. `run_finish` is the same shape — the first close wins, an identical repeat
warns with `run_already_finished`, a divergent one is `AP-007`.

A refused **write** is `ok: false`; refusing to **serve** is what ground rule 3 forbids, and a
closed run still serves `fetch_step` (with `run_already_finished`) because the fixtures are pinned
and immutable.

Neither of them grades. `actual` and `outcome` are stored verbatim, checked against neither
`expected.final` nor any schema, because comparison lives in the client.

## Grade a run

```python
from agentprops_client import connect, grade

with connect("http://localhost:8000/mcp", agent_id="location-onboarding") as client:
    client.run_start({"labels": {"scenario": "missing-documents"}})
    step = client.fetch_step(tool_name="delightree.stores.get")
    step.resolved_node_id  # 'fetch_store_profile' — resolved by position
    client.record_step(step.output, node_id=step.resolved_node_id)
    run = client.run_finish({"onboarding_status": "complete", "outstanding_tasks": 0})

grade(dataset["expected"]["comparison"], dataset["expected"]["final"], run["outcome"])
```

`agent-props-client` is a **separate distribution** (`client/python/`): the run id is generated when
the client is constructed, before the first call, and carried on every call. `connect_async` is the
same surface awaited. Both accept anything `mcp.Client` accepts — a URL, a `StdioServerParameters`,
or a server object for an in-process session.

The three comparison helpers — `exact`, `schema`, `subset`, and `grade` to dispatch on the mode the
dataset declared — are **pure functions with no network dependency at all**, so a run can be graded
from stored documents with no server: `tests/unit/test_client_import_isolation.py` measures that in
a fresh interpreter rather than claiming it. `subset` recurses into nested objects and is positional
over arrays; an expected `null` requires the key to be present; `1` equals `1.0` and `true` never
equals `1`.

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

Three backends, one Protocol, one conformance suite.

```python
from agentprops.storage import MongoStore, SqlStore, create_schema, sqlite_url

store = SqlStore.from_url(sqlite_url("agentprops.db"))  # containerless, what CI uses
store = SqlStore.from_url("postgresql://user:pw@host/agentprops")  # shared; migrate it first
store = MongoStore.from_url("mongodb://localhost:27117/agentprops")  # local authoring

store.put_blueprint(blueprint, publish=True)
version_1 = store.put_dataset(dataset)  # the store allocates the version
version_2 = store.put_dataset(edited)  # copy-on-write; version 1 stays readable
store.set_archived(str(dataset.id), True)  # a flag, never a delete
```

Everything above `storage/` goes through the `Store` Protocol in
[`src/agentprops/storage/base.py`](src/agentprops/storage/base.py), never through an adapter
directly. That is what let the conformance suite be written once at M3 and gain two backends at M7
**without a line of it changing** — `tests/integration/test_store_conformance.py` names no adapter
and knows no dialect. It has no `delete_*` method and never will: archive is a flag, a dataset edit
is copy-on-write, and two tests hold the line — one on the Protocol's shape, one on the AST of every
module in the package.

`storage/common.py` holds what the adapters must answer *identically* — the `q` case fold, the
semver ordering, every record-to-model mapper — in one place, because a second copy of the fold is
the divergence the substring contract exists to prevent. A Postgres database is **migrated**
(`alembic upgrade head`) and never created by the adapter; a Mongo deployment declares its indexes
on startup, because `create_index` is idempotent and there is no Alembic for a document store.

## Serve the tools

```bash
uv run python -m agentprops.server --store agentprops.db                     # stdio, SQLite file
uv run python -m agentprops.server --store agentprops.db --transport http    # streamable HTTP
uv run python -m agentprops.server --store mongodb://localhost:27117/agentprops
uv run python -m agentprops.server --store postgresql://user:pw@host:5442/agentprops
```

`--store` takes a SQLite file path or a `sqlite://` / `postgresql://` / `mongodb://` URL, and every
argument also reads an `AGENTPROPS_*` environment variable, which is how the containers are
configured. A SQLite file is created if absent and Mongo's indexes are declared on startup; a
Postgres database is neither, because it is migrated.

Twenty-five tools. Blueprint: `blueprint_upsert`, `blueprint_get`, `blueprint_list`,
`blueprint_validate`, `blueprint_diff`. Dataset: `dataset_skeleton`, `dataset_fill_part`,
`dataset_submit`, `dataset_validate`, `dataset_find`, `dataset_get`, `dataset_archive`,
`dataset_restore`, `dataset_expand`, `dataset_export`, `dataset_import`. Run: `run_start`,
`fetch_step`, `record_step`, `run_finish`, `run_get`, `run_find`. Admin: `store_status`,
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

## Run in a container

```bash
docker compose --profile local up      # service + Mongo, for a developer authoring
docker compose --profile shared up     # service + Postgres, for a team
```

Two profiles, and they are alternatives rather than layers — both publish the service on 8000. The
`local` profile is one command because Mongo needs no migration; the `shared` profile runs
`alembic upgrade head` before it serves, because a SQL database is migrated and by nothing else.
A third mode has no compose file at all: `--store ./agentprops.db` runs against a SQLite file with
no container, and that is what CI uses.

The two databases publish **Mongo on 27117 and Postgres on 5442** — not their default ports — and
the test URLs dial exactly those, so the conformance suite runs from the host against a container
started here with no environment set. Container-internal the ports are still 27017 and 5432.

The odd numbers are ruling R-60 and they are not fussiness. A host Postgres on `0.0.0.0:5432` wins
the race for IPv4 over Docker's port proxy on Windows, so every connection lands on the wrong
server and is told the password is wrong; and a host Mongo on 27017 answers *successfully*, so the
suite **passes** against a server nobody meant to test. That happened three times while M7 was
built, and the damage was not a stray database — it was a reported test count that depended on what
happened to be listening. `AGENTPROPS_MONGO_PORT` / `AGENTPROPS_POSTGRES_PORT` still override, which
is now a deliberate act rather than a default.

## Promote a dataset

```python
bundle = call("dataset_export", agent_id="location-onboarding")["bundle"]
# ... move the file to the shared instance ...
call("dataset_import", bundle=bundle)
```

The bundle is `{format, format_version, agent_id, blueprints, datasets}` and carries the blueprint
versions its datasets reference, because DS-001 requires an existing published blueprint. Import
re-runs the **whole** catalogue against the receiving store — DS-001 and DS-031 are existence
checks only it can answer — and writes nothing until all of it passes. Version numbers are
allocated by the receiving store; `created_at` is authored content and survives, which is what
keeps `dataset_find`'s ordering identical on both sides.

## Run the checks

```bash
uv run ruff check          # lint
uv run ruff format --check # formatting
uv run mypy                # types, strict, over src/ and tests/
uv run pytest              # tests
uv run pytest -m integration                    # integration, SQLite
uv run pytest -m integration --store postgres   # ...and against a real Postgres
uv run pytest -m integration --store mongo      # ...and against a real MongoDB
```

The first four must pass before any commit. `--store postgres` and `--store mongo` need a server;
each **skips with a reason naming the URL** when there is none, so a run that could not reach one
says so rather than reporting green on nothing.

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
client/python/    agent-props-client: the run client and the three comparison helpers
schemas/          Blueprint and Dataset JSON Schemas, generated from the models
tests/            unit/, integration/, and fixtures/ (blueprints, datasets, broken)
docs/             the specification (PRD, build handoff, contracts, worked example)
```

`Dockerfile`, `docker-compose.yml` and `.dockerignore` sit beside them at the repository root.
`client/python/` is its **own distribution** with its own `pyproject.toml` and no dependency on the
service; it is on this project's dev dependency group so one `uv run pytest` covers both packages.
`client/typescript` and `web/` arrive with the milestones that own them (M11, M9).
