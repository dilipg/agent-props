# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`agent-props` is a blueprint-driven narrative fixture service for agentic systems. A **blueprint** is a
graph of an agent's steps (nodes, edges, entity schemas, label vocabulary). A **dataset** is one
hand-authored, immutable, versioned world for that blueprint: a fixture per node, a coherent entity
timeline, a narrative, an expected outcome. A running agent calls `fetch_step` over MCP and gets its
step's fixture back. It inverts "mock each tool call" into "author the world once, then run the agent
through it".

Phase 1 is eleven milestones, **M0 to M10**, defined in
[docs/build-handoff.md](docs/build-handoff.md) section 4, plus **M9.5** - an owner-approved
additive milestone between M9 and M10, defined by ruling **R-76** rather than by the handoff. **M11, the TypeScript client, was
descoped by the owner — do not build it (R-68).** Build one milestone per session, in order.
Each milestone's acceptance criteria are the gate: do not start one until the previous one's
criteria pass. **Phase 1 is complete: M0 through M10, plus M9.5** - the models, the validator, all
three storage adapters, the MCP surface, the skeleton pipeline, the runtime read path, the
containers, the promotion path, the Python client, the web app, the MCP prompt and resource
surfaces, and M10's outward publishing.
Twenty-seven tools, four prompts, five resources, three backends, two packages and one web app -
the service in `src/agentprops/`, `agent-props-client` in `client/python/`, which is separately
installable and depends on nothing of the service's, and `web/`, which reaches the service through
the tool surface and nothing else.

## Read before writing code

| Document | Contains | Read when |
|---|---|---|
| [docs/spec-rulings.md](docs/spec-rulings.md) | **Authoritative.** Corrections and binding interpretations applied to the spec after a consistency scan found 35 disagreements between the four documents. Where a ruling and another document conflict, the ruling wins | Before any code, every session — read it alongside the handoff |
| [docs/build-handoff.md](docs/build-handoff.md) | Locked stack, repo layout, the eleven milestones and their acceptance criteria, testing strategy, non-goals | Before any code, every session |
| [docs/contracts.md](docs/contracts.md) | Error envelope, model shapes, the full validation rule catalogue (`BP-*`, `DS-*`, `SK-*`, `RT-*`), MCP tool signatures, step-resolution algorithm, storage `Protocol`, SQL DDL, Mongo indexes, seeded-expansion API | Before touching `models/`, `validation/`, `server/`, `storage/` or `expansion/` |
| [docs/worked-example.md](docs/worked-example.md) | The `location-onboarding` blueprint, two golden datasets, the rejection-corpus mutation manifest, the M8 end-to-end scenario | At M0 (commit these as fixtures) and whenever writing tests |
| [docs/prd.md](docs/prd.md) | Product rationale, and the reasoning behind every decision | Before proposing any behaviour change |
| [docs/competitive-analysis.md](docs/competitive-analysis.md), [docs/landscape-research.md](docs/landscape-research.md) | Market context | Rarely |

## Invariants

A change that violates one of these is a bug, however convenient.

- **The runtime is read-only.** No code path writes to a dataset from a running agent. `record_step`
  writes to a run. Only the authoring flow writes datasets.
- **The service never grades.** It stores expectations and emits evidence. Comparison lives in the
  Python client as three pure functions (`exact`, `schema`, `subset`) in
  `client/python/agentprops_client/compare.py`, with `grade` dispatching on the mode the dataset
  declared. `record_step` and `run_finish` store an `actual` and an `outcome` **verbatim** — checked
  against neither `expected.final` nor any schema. Importing the compare module pulls in no network
  dependency, and `tests/unit/test_client_import_isolation.py` measures that in a fresh interpreter
  rather than claiming it.
- **The service never gates.** Policy problems come back as warnings attached to the response and to
  the stored run. No tool refuses to serve, raises on a policy violation, or returns a failure exit
  code. That extends to the two writes: a `record_step` whose actual differs from the recorded one
  and a `run_finish` on an already-closed run both keep the stored value, return it and warn, and a
  **finished run still serves** `fetch_step` (ruling R-54(c)) with a `run_already_finished` warning.
- **No LLM inside the service.** No model client, no API key handling, no judge anywhere in `src/`.
- **Datasets are immutable and versioned.** Edits are copy-on-write. `run_start` pins
  `{dataset_id, dataset_version, blueprint_version}` and the run reads that pin for its whole life.
  Published blueprint versions are immutable (BP-016).
- **Archive, never delete.** The storage `Protocol` has no `delete_*` method, and a test asserts that.
- **Validation is strict at write time and absent at read time.** A dataset that reached the store is
  trusted by the read path.
- **No dataset without provenance:** title, intent, complete labels and a named author, all at submit.
  No defaults, no "fill it in later". `narrative` (what happens in the world) and `intent` (why this
  dataset exists in the suite) are different fields and must not carry the same text. `author` is
  attribution, never authentication — build no ownership or permissions on it.
- **Nothing outward grades, either.** M10's `export/` turns a recorded run into an OTLP trace and
  adds the `langfuse.*` attributes that make it arrive as a dataset run, and **no span carries an
  error status and no attribute names a verdict** - both paths ride the trace side by side, as they
  do in the bundle. `run_evidence` hands out everything the client's three comparison helpers take
  as input, including the pinned blueprint's `outcome_schema` **itself** rather than a reference to
  it (ruling R-82), and `tests/unit/test_evidence_grading.py` grades a committed bundle in an
  interpreter where importing `agentprops` **raises**. `export/` imports no sibling layer at all -
  not even `models/` - because it takes the bundle, so the trace and the evidence cannot drift.
- **Determinism.** All randomness inside the service flows through `Seeded` in `expansion/seeded.py`,
  which takes `(seed, salt)` where salt is the node id or field path. No bare `random`, `uuid4()` or
  `datetime.now()` in any generation or expansion path — `tests/unit/test_layering.py` forbids the
  *import* of `random` and `secrets` in every layer, which is what makes `Seeded.choice` the only
  reachable one. Run ids are generated by the *client*, so `uuid4()` is correct there and only
  there - `client/python/agentprops_client/run.py::new_run_id`, at construction, before the first
  call.

## Do not redesign

The PRD went through four rounds of decisions with the product owner. If a spec looks wrong, build what
is specified and record the concern in `DECISIONS.md` under "Questions for the owner". Pick the
reasonable option and move on rather than blocking. The one exception is a genuine contradiction between
documents — surface that instead of resolving it silently.

Append one `DECISIONS.md` entry per non-obvious choice, in the shape given in
[docs/build-handoff.md](docs/build-handoff.md) section 6: heading `## [M3] <the choice>`, then what was
chosen, the reason, and the alternative rejected. The file is append-only.

## Layering

`server/` -> `service/` -> `storage/`, `validation/`, `models/`. `models/` and `validation/` import
nothing from the others and do no I/O. Business logic lives in `service/`; a tool function in `server/`
parses, delegates, shapes the response, and stays under 20 lines. Storage is reached only through the
`Store` Protocol in `storage/base.py`, never through an adapter directly.

The **prompt and resource** surfaces obey the same rule, and ruling R-76 states it for them:
registration in `server/prompts.py` and `server/resources.py`, thin; anything whose content depends
on store contents composes in `service/prompts.py` and `service/examples.py`. `tests/unit/
test_layering.py` holds a `@mcp.prompt()` or `@mcp.resource()` function to the same thinness budget
a `@mcp.tool()` function has. Neither surface is wrapped in a `SuccessEnvelope`: `prompts/get` and
`resources/read` have their own protocol shapes and R-43(b)'s one-named-key `data` convention
governs tool payloads only. **Registering a prompt does not put an LLM in the service** (ground
rule 4) - a prompt is templated text served over a protocol method, the same category as a tool
description, and nothing calls a model.

Two web-app invariants beyond the layering rule, both from ruling R-72 and R-73's fix round:
**the envelope has three shapes, not two** - success-with-`data`, validate-with-`errors` (no
`data`, and `ok: true` when only warnings fired), and failure-with-`errors` - and
`tests/unit/test_web_envelope_shapes.py` measures that against the running surface while asserting
the web test harness can construct every shape it observes. A harness that cannot build a reply the
service sends manufactures agreement rather than testing anything. And **no tracked file under
`src/`, `web/src/`, `client/` or `tests/` may contain a NUL byte** (R-73), because git then calls
it binary and `git grep`, ripgrep and every diff view skip it -
`tests/unit/test_tracked_source_is_text.py` enforces it, asking git directly as well as scanning
bytes.

The **web app** has one too, and it is M9's fifth acceptance clause made mechanical: `web/src` may
reach the network from `src/mcp/transport.ts` and from nowhere else, may call `callTool` from
`src/mcp/tools.ts` and from nowhere else, and may name only the two writing tools M9 permits
(`blueprint_upsert`, `dataset_import`).
`tests/unit/test_web_writes_through_tools.py` derives all three from real surfaces - the running tool
registry, an AST walk to the `Store` Protocol's mutators, and a scan of `web/src` - so a new write on
the service is classified the day it lands. The load-bearing half is not the allowlist: it is that
**no network primitive appears outside the transport**, because a `fetch('/api/...', {method:'PUT'})`
names no tool at all.

The client has a layering rule of its own, and it is ground rule 2 made mechanical:
`compare.py` -> nothing but `jsonschema`; `run.py` -> `envelope.py`; `session.py` -> `run.py` plus
`mcp`. **`session.py` is the only module that may import a transport**, the package's `__init__`
reaches it lazily, and `tests/unit/test_client_import_isolation.py` measures the whole transitive
import set rather than trusting the rule.

## Commands

Available from M0 onward, once `pyproject.toml` exists.

- `uv run pytest` — all tests
- `uv run pytest tests/unit/test_validation.py::test_bp_005 -x` — one test; `-k <expr>` to filter
- `uv run pytest -m integration --store postgres` — integration against one backend (`sqlite`,
  `postgres`, `mongo`). All three are implemented from M7; `postgres` and `mongo` **skip with a
  reason naming the URL** when their server is unreachable, and their default URLs match
  `docker-compose.yml`'s published ports
- `uv run ruff check` and `uv run mypy` — both must pass before any commit (`mypy` covers `tests/`
  and `client/python/agentprops_client/` as well as `src/`). `ruff format` takes **no path
  argument**: `docs/` holds the frozen specification and `pyproject.toml` excludes it
- `cd web && npm run dev` — the M9 web app, at `http://localhost:5173`, proxying `/mcp` to the
  service on 8000. It is served **same-origin** with the service because a browser cannot reach the
  MCP endpoint cross-origin - `OPTIONS /mcp` is 405 and no response carries a CORS header, measured
  (`DECISIONS.md` [M9], finding F-16). `npm test`, `npm run lint` and `npm run typecheck` are its
  own gates and are separate from `uv run pytest`; `web/README.md` has the rest
- `docker compose --profile otel up -d otel-collector` — M10's local OpenTelemetry collector, which
  `tests/integration/test_otel_collector.py` measures the first acceptance clause against. A
  **third** profile, orthogonal to the two below rather than part of either. It publishes OTLP/HTTP
  on **4418** and its health endpoint on **13233**, not the defaults 4318 and 13133 — ruling R-60
  again, because any other collector on the machine answers 4318 and a clause that passed against
  someone else's collector would prove nothing. Without it those tests skip with both URLs in the
  reason. `run_export` reads the OpenTelemetry SDK's own `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` /
  `OTEL_EXPORTER_OTLP_ENDPOINT`, so agent-props adds no configuration of its own and never touches a
  credential — an API key belongs in `OTEL_EXPORTER_OTLP_HEADERS`, which the exporter reads and no
  module in `src/` names
- `docker compose --profile local up` — service plus Mongo, for authoring; `--profile shared` for
  Postgres. Alternatives rather than layers; both publish the service on 8000. The databases
  publish **Mongo on 27117 and Postgres on 5442**, not their default ports, and the test URLs
  dial exactly those — ruling R-60, so a foreign server on 27017 or 5432 cannot be reached by
  accident. `AGENTPROPS_MONGO_PORT` / `AGENTPROPS_POSTGRES_PORT` override, deliberately
- `--store <path>` — a SQLite file, the containerless mode and what CI uses. `--store` also takes a
  `sqlite://` / `postgresql://` / `mongodb://` URL, and every argument reads an `AGENTPROPS_*`
  environment variable
- **Concurrent runs are safe.** Each session creates its own `agentprops_conformance_<pid>_<random>`
  database and drops it on exit — ruling R-63, because the per-test reset is destructive and a
  shared name made two overlapping runs delete each other's rows mid-test, which reads as a
  plausible *failure* count rather than an error. A run that is *killed* leaves one behind; the
  sweep is in `tests/integration/conftest.py`'s module docstring

## Testing

Four layers, in the order they matter here: validation rule tests, storage conformance tests, tool
contract tests, end-to-end.

- **The validator is the product.** Every rule id in [docs/contracts.md](docs/contracts.md) section 3
  needs an entry in `validation/registry.py` and at least one case in the broken corpus. A test enforces
  both directions, and that test is what stops the catalogue drifting from the code.
- Broken fixtures come from a declarative mutation manifest (`tests/fixtures/broken/manifest.json`)
  applied to the golden fixtures at collection time. Do not hand-write broken JSON files — they rot the
  moment a schema changes.
- A broken case must be rejected with exactly the rule ids its manifest entry declares: no more, no fewer.
- **Assert on rule ids and envelope shapes, never on human-readable messages.** Messages get reworded.
- Integration tests are written once against the `Store` Protocol and parameterised over sqlite, postgres
  and mongo. A storage feature is done when it passes on all three. The suite asserts identical results
  across backends, never identical query plans.
- Use the in-memory MCP `Client` against the server object for tool tests. No subprocesses in unit
  tests - except the client's import-isolation guard, which *must* measure a fresh interpreter
  because this process has already imported the transport.
- The end-to-end script is `tests/integration/test_worked_example_end_to_end.py`:
  `docs/worked-example.md` section 7, read with rulings **R-52** (the `pool_exhausted` boundary is
  iteration 2, not 3) and **R-64** (step 8's re-fetch uses `node_id`) applied. It takes the `store`
  fixture, so it runs on every backend.
- `hypothesis` is worth it in exactly one place: seeded expansion — identical `(seed, salt)` yields
  identical output across processes, differing seeds usually diverge.

## Style

- Python 3.12+, Pydantic v2, `mypy --strict` from M0. Use `jsonschema` (Draft 2020-12) for user-supplied
  node schemas — not Pydantic, which cannot express them.
- Structured errors, never exceptions, for anything a user could cause. Every user-facing error carries a
  rule id and an RFC 6901 JSON pointer, plus the skeleton `section` when there is one.
- Never guess at step resolution. Ambiguity returns `RT-E01` listing every candidate node id.

## Out of scope for phase 1

No auth, no multi-tenancy, no CLI, no grading, no CI gating, no LLM, no dashboards, no tool-call
interception, no hard delete, no writes from a running agent, no real or anonymised production data.
If a milestone seems to need one of these, it does not — note it in `DECISIONS.md` and move on.
