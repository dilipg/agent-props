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

Phase 1 is being built one milestone at a time, M0 through M10 (see
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
— alongside its two server-side writes, `record_step` and `run_finish`, and the M9 web app in
`web/`: a React 18 + Vite + TypeScript app that browses agents, blueprints and datasets, edits
both documents in `vanilla-jsoneditor` against the emitted JSON Schemas, and draws a blueprint's
graph read-only with React Flow. **Twenty-five tools, three backends, two packages, one web app.**
`export/` is an empty module waiting on M10.

Not yet built: the evidence bundle (M10), which is where phase 1 ends — ruling R-68 descopes M11,
the TypeScript client, by owner decision. `tests/unit/test_tool_surface.py` lists exactly which
documented tools are still deferred, and to which milestone.

## Use it against your own agent repo

Both repos on one machine. Every command below was run end to end; the closing
example is a real agent process talking to a real service over a real MCP session.

**What Claude does and does not do here.** `blueprint_infer` is phase 1.5 and **is not
built**, so nothing reads your repo and emits a blueprint automatically. What works today
is Claude Code *authoring* one: it reads your agent's code, writes the blueprint, and
submits it through the tool surface — where the validator rejects anything incoherent. The
rejection messages are the point. `worked-example.md` section 8 puts it plainly: "use
Claude Code itself as the ingestion LLM… that loop is the product demonstrating itself."

### 1. Give Claude Code the tools, inside your agent repo

There is no CLI — an explicit non-goal — so authoring happens over MCP. In **your agent
repo**, add `.mcp.json`:

```json
{
  "mcpServers": {
    "agent-props": {
      "command": "uv",
      "args": [
        "run", "--directory", "c:/Users/Dilip/Documents/GitHub/agent-props",
        "python", "-m", "agentprops.server",
        "--transport", "stdio",
        "--store", "c:/Users/Dilip/Documents/GitHub/agent-props/demo.db"
      ]
    }
  }
}
```

`--directory` is what lets the service run from your repo's cwd against its own
environment. Verify it answers before relying on it:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  | uv run --directory c:/Users/Dilip/Documents/GitHub/agent-props \
      python -m agentprops.server --transport stdio --store demo.db | head -c 80
```

### 2. Have Claude author the blueprint

Restart Claude Code in your agent repo so it picks up the server. This step needs the
longest prompt, because — unlike dataset filling — **nothing in the tool surface tells
Claude the blueprint shape up front.** There is no `blueprint_skeleton`. Paste this:

> Read this repository's agent and author an agent-props blueprint for it, using the
> `agent-props` MCP tools.
>
> First orient yourself on a known-good example rather than guessing the shape: call
> `agent_list`, then `blueprint_get` for `location-onboarding` at `1.0.0`. Copy its
> structure, not its content.
>
> Then model *this* repo's agent:
>
> - **One node per step the agent actually takes.** Set `tool_name` to the tool it really
>   calls, verbatim. Set `kind` to `tool_call`, `decision`, `loop` or `terminal`.
> - **`entry_node`** is the step that starts a run, and it must have no inbound edges.
> - **Edges** carry a JSONLogic `condition` for every branch out of a `decision`. Every
>   `{"var": "..."}` path must name a property the source node's `output_schema` actually
>   declares — that is checked, and it is the rule most blueprints fail first.
> - **Retry loops** get `kind: loop`, an integer `max_iterations`, and `pool: true`. Every
>   cycle in the graph must pass through a loop node.
> - **At least one `kind: terminal` node**, with no outbound edges.
> - **`entities`**: one per domain noun that persists across steps — the thing the agent
>   reads at step 2 and reads again at step 5. Give each a Draft 2020-12 JSON Schema, and
>   reference them from node schemas as `{"$ref": "entity:<id>"}`.
> - **`input_schema` and `output_schema`** per node, Draft 2020-12.
> - **`outcome_schema`**: the shape of a finished run's result.
> - **`label_schema`**: the dimensions I would filter datasets by — persona, scenario,
>   tier, outcome, plus an `edge_case` dimension whose vocabulary includes `none`. Every
>   dimension needs at least one value, and a dataset must later carry a value for *every*
>   dimension — so keep the set small and give each an explicit not-applicable value.
> - **`notes`** on each node: what the step does and what a realistic fixture looks like.
>   Absent notes are a warning, and they make generated data worse.
>
> Then loop: call `blueprint_validate` and fix **every** rule id it reports. Do not call
> `blueprint_upsert` until `blueprint_validate` returns no `error`-severity findings. Then
> `blueprint_upsert` with `publish: true`.
>
> Report the final node and edge count, and any `BP-019` warnings you chose to leave.

Insist on that validate loop. Nineteen `BP-*` rules catch an unreachable node, a cycle with
no loop node, a `var` path no output schema declares, and — the important one — **BP-014**:
two nodes sharing a `tool_name` where position cannot disambiguate them. That is the mistake
that makes an agent untestable, because `fetch_step` then cannot tell which step you mean,
and it is far cheaper to hear now than at the first run.

### 3. Have Claude fill datasets

This prompt is **short on purpose.** `dataset_skeleton` returns an `instructions` field
that already explains fill order, SK-002, re-fill-as-repair, and the exact content shape
each section takes — so the prompt only supplies *intent*, not mechanics:

> Author agent-props datasets for `<agent_id>` at `<version>`, one per scenario below.
>
> Before starting, call `label_vocabulary` to see the dimensions and values this blueprint
> declares, and `dataset_find` then `dataset_get` on an existing dataset to see what a
> filled one looks like.
>
> For each scenario: call `dataset_skeleton` with a complete label set and a seed, **read
> the `instructions` field it returns and follow it**, fill each section with
> `dataset_fill_part`, then call `dataset_submit`.
>
> Two fields people get wrong, so be deliberate. **`narrative`** is what happens in the
> world — the story the agent walks through. **`intent`** is why this dataset exists in the
> test suite: what behaviour it pins down, what bug prompted it, what would go untested if
> it were deleted. They must not be the same text, and a reviewer reads the second one.
>
> If `dataset_submit` reports rule ids, each error carries the section it belongs to —
> re-fill only that section and submit again. Do not regenerate the whole dataset.
>
> Scenarios to cover:
> 1. the happy path, no edge cases
> 2. the retry loop firing exactly once
> 3. the escalation or failure branch
> 4. …
>
> Report each dataset's title, its labels, and any warnings the submit returned.

The section scoping is what makes this cheap: a rejection names the one section to redo.
And ask for the label space to be **covered** rather than for a dataset count — the PRD's
exit criterion is twenty datasets spanning the declared labels, and `label_vocabulary`
returns per-value counts, so Claude can see which combinations are still empty.

### 4. Point your agent at the fixtures

Install the client into your agent repo. Its `pyproject.toml`:

```toml
[project]
dependencies = ["agent-props-client"]

[tool.uv.sources]
agent-props-client = { path = "c:/Users/Dilip/Documents/GitHub/agent-props/client/python" }
```

```bash
uv sync
```

The client does **not** pull in the service package — `importlib.util.find_spec("agentprops")`
is `None` in a consumer project, which is the separation ground rule 2 requires.

Start the service over HTTP (`--transport http --port 8000`), then have Claude rewire the
agent. This is ordinary code editing, so the prompt is mostly about *where the seam goes*:

> Add an agent-props fixture mode to this agent, for tests.
>
> Read `<your agent's entry point>` and find every outbound tool call. Introduce a **single
> seam** — one injected client, or one module-level indirection — so that in fixture mode
> each of those calls is replaced by `props.fetch_step(...)` and nothing else changes. Do
> not scatter conditionals through the agent's logic.
>
> Use `agentprops_client`: `connect(url, agent_id=...)`, then `run_start` once with a
> selector, then `fetch_step(node_id=...)` — or `fetch_step(tool_name=...)` where the agent
> only knows which tool it is calling. Pass `iteration=` for a loop node, counting from 0.
>
> Three things that will bite otherwise: you must `fetch_step` a node before `record_step`
> can report an actual for it, or you get `AP-004`; warnings on a reply are typed objects,
> so `w.code` and not `w["code"]`; and the run id is generated by the client, so do not
> invent one.
>
> Finish with `record_step` for the terminal step and `run_finish(outcome)`. Then grade in
> the **test**, not in the agent: `compare.grade(mode, expected, actual)`, where `mode` is
> the dataset's own `expected.comparison`.
>
> Keep the real tool path as the default and make fixture mode opt-in via one env var.

Then it looks like this — the example below ran against the worked example:

```python
from agentprops_client import compare, connect

with connect("http://127.0.0.1:8000/mcp", agent_id="location-onboarding") as props:
    start = props.run_start({"labels": {"scenario": "missing-documents"}})
    print("pinned", start.pin)

    props.fetch_step(node_id="receive_request")
    profile = props.fetch_step(tool_name="delightree.stores.get")  # resolves by position
    for i in (0, 1, 2):
        drawn = props.fetch_step(node_id="request_docs", iteration=i)
        print(i, [w.code for w in drawn.warnings])

    # R-33: an actual cannot be recorded for a step that was never served.
    props.fetch_step(node_id="complete")
    outcome = {"onboarding_status": "completed", "outstanding_tasks": 0, "extra": "ignored"}
    props.record_step(outcome, node_id="complete")
    props.run_finish(outcome)

    verdict = compare.subset({"onboarding_status": "completed", "outstanding_tasks": 0}, outcome)
    print("PASS" if verdict.ok else verdict.mismatches)
```

```text
pinned {'dataset_id': '3f8c1a20-...-0001', 'dataset_version': 1, 'blueprint_version': '1.0.0'}
0 []
1 []
2 ['pool_exhausted']
PASS
```

Three things that example demonstrates rather than asserts. `fetch_store_profile` resolved
from a **tool name** — position disambiguates two nodes that share it. `pool_exhausted`
arrived at **iteration 2**, not 3, because the pool holds two entries (ruling R-52 corrects
the published script here). And the run was graded **in the client**: the service stores the
expectation and hands back evidence, and never compares.

### Could Claude do this without the prompts?

Partly, today. Mostly, with one small addition. Measured against the running service rather
than guessed:

**Dataset filling is already nearly promptless.** `dataset_skeleton` returns an
`instructions` field, and it is a real product surface rather than a stub — it states the
fill order and its rule id, that re-filling is how you repair a rejection, the exact content
shape each of the five sections takes, and that `label_vocabulary` is the pre-flight for the
one input a re-fill cannot repair. It is derived from the manifest, so it cannot describe a
section that does not exist. That is why the step-3 prompt above is three lines of intent
and a scenario list: the mechanics arrive with the tool call.

**Blueprint authoring is not**, and that asymmetry is the gap. There is no
`blueprint_skeleton`, so nothing tells Claude the shape before it writes one — which is
exactly why the step-2 prompt is the long one. `blueprint_validate` gives a good feedback
loop, but only after a guess.

**The concrete missing piece: the server advertises `prompts` and `resources` and registers
neither.** Both come back empty:

```text
prompts  : []
resources: []
tools    : 25
```

Those are the parts of MCP designed for this. Registering them would replace the copy-paste
above with something Claude picks from a menu:

- **`prompts/list`** would expose `author-a-blueprint`, `fill-a-dataset` and
  `cover-the-label-space` as parameterised entries — in Claude Code they appear as slash
  commands. The prompt text stops living in a README that can drift from the tool surface,
  and starts living beside the tools it drives.
- **`resources/list`** would expose the golden blueprint and datasets as canonical examples,
  so "orient yourself on a known-good example" needs no hard-coded `location-onboarding`
  id — which is the one line of the step-2 prompt that is wrong for anyone who has not
  seeded the demo store.

Neither changes a tool, a rule, or the storage contract; both are additive surface on an
existing capability the SDK already advertises. It is not in the phase-1 plan — the plan
predates the observation — so it is recorded as a phase-1.5 candidate rather than smuggled
in. **The honest summary: samples alone would not remove the prompts, because a prompt also
carries intent — which scenarios matter, where the seam goes. What removes the boilerplate
is registering the prompts themselves, and the capability is already there and unused.**

### Rough edges, so you meet them here and not mid-session

- **`blueprint_infer` does not exist.** Claude authors; nothing infers.
- **`run_evidence` is M10 and unbuilt.** Grade the outcome you recorded, as above, rather
  than asking the service for a bundle.
- **Warnings never block.** A DS-027 warning — intent duplicating narrative — stores
  anyway. That is ground rule 3, not a bug.
- **Fetch before you record.** `record_step` on an unserved step is `AP-004`, by design.
- **Warnings are typed objects**, so `w.code`, not `w["code"]`.
- **One store, one process at a time** for SQLite. Point the service and Claude Code at the
  same `--store` file, not two copies.

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
splits by whether the value differs (ruling R-65): the identical `actual` again is a **silent** no-op
success, so a retry after a network blip is safe and reports nothing, while a *different* one is
`AP-007` and **nothing is written**. An actual for a step that was never fetched is `AP-004`.
`run_finish` is the same shape — the first close wins, an identical repeat is silent, a divergent
one is `AP-007`.

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
web/              the React browse-and-edit app; every write goes through the tool surface
schemas/          Blueprint and Dataset JSON Schemas, generated from the models
tests/            unit/, integration/, and fixtures/ (blueprints, datasets, broken)
docs/             the specification (PRD, build handoff, contracts, worked example)
```

`Dockerfile`, `docker-compose.yml` and `.dockerignore` sit beside them at the repository root.
`client/python/` is its **own distribution** with its own `pyproject.toml` and no dependency on the
service; it is on this project's dev dependency group so one `uv run pytest` covers both packages.
`web/` is the M9 web app, its own npm project with its own `package.json` — see
[`web/README.md`](web/README.md), and note that it is served **same-origin** with the service
because a browser cannot reach the MCP endpoint cross-origin (`DECISIONS.md` [M9], finding F-16).
There is no `client/typescript`: ruling R-68 descopes M11.
