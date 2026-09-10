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
graph read-only with React Flow, and the M9.5 prompt and resource surfaces — four registered
MCP prompts in `src/agentprops/server/prompts.py`, composed against the store in
`src/agentprops/service/prompts.py`, plus the canonical examples read *from the store* in
`src/agentprops/service/examples.py`, and M10's outward publishing — `run_evidence`, `run_export`
and `src/agentprops/export/`. **Twenty-seven tools, four prompts, five resources, three
backends, two packages, one web app.**

M9.5 is an additive milestone between M9 and M10, approved by the owner and recorded as ruling
R-76: the server advertised the `prompts` and `resources` capabilities from M4 and registered
neither, so the workflow instructions lived only in this README.

**Phase 1 is complete at M10** — ruling R-68 descopes M11, the TypeScript client, by owner
decision. The one documented tool still unbuilt is `blueprint_infer`, which `contracts.md` tags
phase 1.5; `tests/unit/test_tool_surface.py` is where that deferral is recorded, and
`tests/unit/test_prompt_and_resource_surface.py` does the equivalent for the other two surfaces.

## Use it against your own agent repo

**Handing this to a coding agent in another repository?**
[`docs/connect-your-agent-repo.md`](docs/connect-your-agent-repo.md) is that brief — the MCP config
for a harness that is not this one, the client install, and which prompt drives which step. Point
the other harness at that path; both repos are on one machine, so it reads the file rather than a
copy that can drift. The rest of this section is the same four steps written for a reader of *this*
repo.

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

Restart Claude Code in your agent repo so it picks up the server, then run the registered
prompt **`author-a-blueprint`**. It appears in `prompts/list`, which Claude Code surfaces as a
slash command under the server's name; it takes an optional `agent_id` — a published blueprint
to imitate — and picks one out of your store when you omit it.

**That prompt is the source of truth for the blueprint shape, and this README deliberately
does not restate it.** It carries the whole node / edge / entity / label contract, because —
unlike dataset filling — **nothing in the tool surface tells Claude the blueprint shape up
front.** There is no `blueprint_skeleton`, so if the contract is not in the prompt it is
nowhere a caller can reach over MCP. The text lives in
[`src/agentprops/service/prompts.py`](src/agentprops/service/prompts.py) and is composed
against your store, so the example it tells Claude to imitate is one you actually have — and
when you have none it says so instead of naming an id that does not resolve.

Insist on the validate loop the prompt asks for, and on reading every rule id it reports
rather than the first. The `BP-*` catalogue is what turns "this blueprint looks right" into
"this blueprint is coherent", and the prompt singles out the one rule worth knowing before
you start — the step ambiguity that makes an agent untestable at all — because hearing it
from `blueprint_validate` costs minutes and hearing it from the first run costs a rewrite.

### 3. Have Claude fill datasets

Two registered prompts here. **`fill-a-dataset`** takes `agent_id`, an optional `version` and
your scenarios one per line, and it is **short on purpose**: `dataset_skeleton` returns an
`instructions` field that already explains fill order, SK-002, re-fill-as-repair and the exact
content shape each section takes, so the prompt supplies *intent* and nothing else. That is not
a claim about restraint —
[`tests/unit/test_prompts_contract.py`](tests/unit/test_prompts_contract.py) diffs the prompt
against the live `instructions` text and fails if it starts repeating it.

The section scoping is what makes filling cheap: a rejection names the one section to redo.

**`cover-the-label-space`** is the prompt that could not have been a README paragraph. Given
`agent_id` and an optional `version` it reads `label_vocabulary` against *your* store and names
every declared label value that carries **no** dataset, plus the label combinations already
present and how many datasets carry each. Ask for the label space to be **covered** rather
than for a dataset count: the prompt quotes the bar PRD 6 sets and reports the cross-product's
size, rather than handing you a list nobody could finish.

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

Start the service over HTTP (`--transport http --port 8000`), then run the registered prompt
**`wire-an-agent`**. It takes an optional `agent_id` and an optional `url`, and it is mostly
about *where the seam goes* — one injected client or one module-level indirection, rather than
conditionals scattered through the agent's logic. It also carries the `agentprops_client` call
order, the three mistakes that cost the most time on the way in, and where grading belongs.
The `run_start` selector it shows is a real dataset's own label set read out of your store, so
the snippet you paste pins something that exists — which the hard-coded selector this section
used to print did not, in any store without the demo fixtures.

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

That question is what produced the prompts, and the answer changed when they were registered
(ruling **R-76**). Measured against the running service *before*:

```text
prompts  : []
resources: []
tools    : 25
```

**Dataset filling was already nearly promptless.** `dataset_skeleton` returns an `instructions`
field, and it is a real product surface rather than a stub — it states the fill order and its
rule id, that re-filling is how you repair a rejection, the exact content shape each of the
five sections takes, and that `label_vocabulary` is the pre-flight for the one input a re-fill
cannot repair. It is derived from the manifest, so it cannot describe a section that does not
exist. That is why `fill-a-dataset` is intent and a scenario list.

**Blueprint authoring was not**, and that asymmetry is why `author-a-blueprint` is the long one.
There is no `blueprint_skeleton`, so nothing tells Claude the shape before it writes one.
`blueprint_validate` gives a good feedback loop, but only after a guess.

**The concrete problem was that the server advertised `prompts` and `resources` and registered
neither.** Both capabilities were in its `initialize` reply from M4 onward, so every workflow
instruction lived in this README — a second place to drift from the tools it describes, and one
that already had: the old step-2 prompt hard-coded `location-onboarding` as the example to
imitate, which is wrong for any store that has not seeded the demo fixtures.

Both are registered now, and the four steps above point at them instead of restating them:

```text
prompts  : ['author-a-blueprint', 'fill-a-dataset', 'cover-the-label-space', 'wire-an-agent']
resources: ['agentprops://catalogue', 'agentprops://examples/blueprint',
            'agentprops://examples/dataset']
templates: ['agentprops://blueprint/{agent_id}/{version}',
            'agentprops://dataset/{agent_id}/example']
tools    : 25
```

- **`prompts/list`** exposes the four as parameterised entries — in Claude Code they appear as
  slash commands, which is the ergonomic payoff. The text lives beside the tools it drives, and
  the `BP-*` count `author-a-blueprint` quotes is read off the live rule registry rather than
  typed, so it cannot go stale the way a README sentence can.
- **`resources/list`** exposes the canonical examples **read from your store**, never from
  `tests/fixtures/`: `agentprops://catalogue` names every URI the store can serve, so "orient
  yourself on a known-good example" needs no hard-coded id at all. On an empty store the bodies
  say `"available": false` and what to do next, because a resource that promised an example
  this store does not hold would be worse than no resource.

Neither changed a tool, a rule, or the storage contract; both are additive surface on a
capability the SDK already advertised. What removes the boilerplate is registering the prompts
themselves — a sample alone would not, because a prompt also carries intent: which scenarios
matter, where the seam goes.

### Rough edges, so you meet them here and not mid-session

- **`blueprint_infer` does not exist.** Claude authors; nothing infers.
- **`run_evidence` gives you everything and grades nothing.** The bundle carries the expectation,
  your recorded outcome, per-node expected against actual, both paths side by side, the declared
  comparison mode and the pinned blueprint's `outcome_schema` itself — so
  `grade(b["comparison"], b["expected"]["final"], b["actual"], outcome_schema=b["outcome_schema"])`
  is the whole call and needs no second request. It computes no verdict, including about the paths:
  compare them yourself with `exact(b["path"]["expected"], b["path"]["actual"])`, and read the note
  in `DECISIONS.md` first — an `expected_path` that revisits a node can never equal a reconstructed
  one, because `fetch_step` is idempotent per step key.
- **Warnings never block.** A DS-027 warning — intent duplicating narrative — stores
  anyway. That is ground rule 3, not a bug.
- **Fetch before you record.** `record_step` on an unserved step is `AP-004`, by design.
- **Warnings are typed objects**, so `w.code`, not `w["code"]`.
- **`resources/list` is a fixed set of three, not one entry per stored document.**
  `agentprops://catalogue` is the index that names the rest; the per-agent URIs are resource
  *templates* and appear under `resources/templates/list`. The SDK serves `resources/list` from
  a registry built at import time; a live per-document list *is* reachable, by subclassing
  `MCPServer` and overriding its public `list_resources()`, and was not built because that
  couples this surface to an SDK method's contract to save one `resources/read` —
  `src/agentprops/server/resources.py` records all three routes.
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

Twenty-seven tools. Blueprint: `blueprint_upsert`, `blueprint_get`, `blueprint_list`,
`blueprint_validate`, `blueprint_diff`. Dataset: `dataset_skeleton`, `dataset_fill_part`,
`dataset_submit`, `dataset_validate`, `dataset_find`, `dataset_get`, `dataset_archive`,
`dataset_restore`, `dataset_expand`, `dataset_export`, `dataset_import`. Run: `run_start`,
`fetch_step`, `record_step`, `run_finish`, `run_get`, `run_find`, `run_evidence`, `run_export`.
Admin: `store_status`, `label_vocabulary`, `agent_list`.

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
                  server/ registers the tools, the four prompts and the five resources
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
