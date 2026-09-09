# PRD: agent-props

A blueprint-driven narrative fixture and golden-dataset service for agentic systems.

Revision: v0.5
Date: 2026-09-08
Owner: Dilipg
Home: Delightree, Astra AI unit (eval infrastructure)

---

## 1. The problem

Agentic systems are tested with data that does not resemble the world they run in. A developer either points the agent at production (slow, unsafe, unrepeatable), or seeds random rows from Faker (fast, safe, meaningless). Neither tells you whether the agent behaves correctly, and neither tells you whether a model upgrade changed the behaviour, because the environment moved at the same time as the model.

Existing tooling has converged on **record-and-replay**: AIMock, agent-vcr, resilireplay, agentest and AgentCheck all capture or hand-write responses and play them back. That approach has three structural limits:

1. It cannot test a path that has never happened in production.
2. It cannot test an agent before that agent has any traffic.
3. It can vary faults but never the content of the world, so it cannot answer "what happens with a different customer, a different tier, a different history."

For a franchise operating platform, limit 2 is fatal. Every new brand onboarded is a cold start with zero history, and the agent has to work on day one.

## 2. The inversion

**Declare the world before any traffic exists.**

A developer describes, once, the shape of data each step of an agent consumes and produces. That description is a **blueprint**. An LLM then fills the blueprint with a coherent story, producing a **dataset** that is internally consistent across every step and carries the expected final outcome. Runtime agents fetch from that dataset instead of from production. Every run records which dataset it used, what each step returned, and what the agent actually produced.

Because a dataset carries an expected outcome, it is simultaneously a fixture set and a golden test case. That is the product in one sentence: **the same artifact that makes the agent runnable also makes it gradeable.**

Positioning line to test: *record-and-replay can only test what already happened; declare the world instead, and test the agent you are about to ship.*

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime injection | **Pull.** The agent calls the service explicitly for step data. | Framework-agnostic, no interception layer, avoids competing with AIMock on its home ground. |
| Step identity | **Both node id and tool name**, node id wins. | Node id is unambiguous, tool name is zero-friction. Resolution rules in 5.5. |
| Run identity | **Client-generated idempotent run id**, issued when the library is initialised, passed on every fetch and record call. | Makes retries safe, makes the traversed path reconstructible from call order, needs no separate path hint. |
| Expected outcome | **Yes.** Every dataset carries one. | Cheap to add during the same LLM fill pass, and it turns a fixture set into a golden test case. |
| Grading | **The service never grades.** It stores the expectation and emits an evidence bundle. All grading happens externally, from the traces and evidence provided. | Keeps every LLM dependency out of the service and keeps it a data provider, not a judge. |
| Run retention | **Infinite.** The client-supplied run id keys the dataset used and every recorded output, forever. | A run record references the dataset rather than copying it, so runs are small. See 8.2. |
| Runtime writes | **None.** Datasets are immutable during a run. Steps fetch and execute, they never edit stored data. | Removes all mutable world state. See 5.6, which is the most consequential section in this document. |
| Dataset versioning | Datasets are **versioned and copy-on-write.** `run_start` pins a version and every step resolves against that pin. | Honours "the version id is retained across the steps" and keeps permanent runs readable forever. |
| Deletion | **Soft archive only.** Hidden from `dataset_find`, still servable by explicit id and to pinned runs. No hard delete. | Permanent runs make hard deletion incoherent. |
| Skeleton filling | **Partial fills supported.** Section by section, finalised by one submit. | A 30-node cross-domain blueprint does not fit comfortably in one pass. |
| Deployment | **Both.** A shared internal instance for reuse, and a single-container local mode for a developer building a new agent. | Authoring wants isolation, consumption wants reuse. |
| Provenance | **Mandatory on every dataset:** title, narrative, intent, complete labels, named author. No dataset enters the store without them. | A shared library of fixtures is only reusable if a stranger can tell, in ten seconds, what each one is for. See 5.7. |
| Pool draw order | **In order**, deterministically. Repeat the last entry on exhaustion and flag a warning. | Most reproducible. Matches agentest's sequence-mock precedent. |
| Blueprint version drift | `run_start` **pins** the dataset's blueprint version, warns loudly on mismatch, and still serves. | Consistent with never gating. |
| Phase 1 scope | **Blueprint + generation + storage + label retrieval.** Runtime is a read API. | Builds the unclaimed half first. |
| Storage | **Pluggable adapter.** SQLite default, Postgres and Mongo external via credentials plus Docker params. | Rebuild is Postgres-backed, legacy is Mongo, contributors want zero-setup SQLite. |
| Observability | **Publish out in phase 1**, own it later. | Braintrust and Langfuse already do experiment comparison well. |
| Blueprint topology | **Full graph:** branches, loops, conditionals. | Matches how real agents run. |
| Dataset granularity | **Chains by default, per-step pools opt-in.** | Chain preserves the narrative, pools handle independent nodes and load-test volume. |
| Real data | **Fake only in phase 1.** | No PII means no redaction, no residency question, no security review. |
| Access control | **None. Flat, single-tenant, no auth.** | Internal tool for dev, product and QA. Enforced by the fake-only rule and internal-only deployment, not by permissions. |
| CI behaviour | **Never gates. Records only.** | It provisions data and records what happened. Teams build their own gates on top. |
| Client library | **Python only in phase 1.** ~~TypeScript client is the closing milestone.~~ **The TypeScript client was descoped by the owner on 2026-09-09 — see `docs/spec-rulings.md` R-68. Phase 1 ends at M10.** | Astra's eval side is Python. TS follows for the product stack. |
| Authoring UI | **JSON editor with live schema validation**, not a graph builder. | Cheapest thing that works. Library recommendation in 10.4. |
| CLI | **Deferred out of phase 1.** | The MCP server, the Python client and the web app cover the phase 1 jobs. No daemon-mode question to answer yet. |
| Distribution | **Delightree internal first**, open source later. | Astra is the design partner and the requirement source. |
| Design partner | **A cross-domain ops workflow** (onboarding a new location: tasks, training, compliance). | The hardest coherence test, therefore the best proof. |

## 4. Users

| User | Job | Touchpoint |
|---|---|---|
| Agent developer (Astra + domain pods) | Author a blueprint; develop against realistic data instead of production | React app, CLI, MCP |
| Coding agent (Claude Code, Codex) | Fetch a skeleton, write the story, post the filled dataset back | MCP tools |
| Runtime agent | Fetch step data during an execution | Client library, MCP |
| QA / eval engineer | Select datasets by label; grade runs against expected outcomes; compare model versions | CLI, MCP, exported experiments |
| Platform / SRE | Load tests where each concurrent execution draws a different dataset | CLI, MCP |

## 5. Core concepts

### 5.1 Blueprint

A versioned, immutable-once-published declaration of an agent's step graph.

```
Blueprint {
  agent_id: string            // "location-onboarding"
  version: semver             // "1.2.0"
  description: string
  entities: EntitySchema[]    // shared nouns referenced across nodes
  nodes: Node[]
  edges: Edge[]
  label_schema: LabelSchema
  outcome_schema: JSONSchema  // shape of the expected final outcome
}

Node {
  id: string                  // "fetch_store_profile"
  tool_name?: string          // "delightree.stores.get" — enables tool-name addressing
  kind: "tool_call" | "llm" | "decision" | "loop" | "terminal"
  input_schema: JSONSchema
  output_schema: JSONSchema
  pool: boolean               // opt-in independent pool instead of chain position
  max_iterations?: number     // loop nodes
  notes?: string              // narrative hints for the generating LLM
}

Edge {
  from: node_id
  to: node_id
  condition?: JSONLogic       // evaluated against the source node's output
}
```

**Entities are the coherence mechanism.** Nodes do not invent nouns independently. A blueprint declares shared entities (`Store`, `Franchisee`, `Task`, `TrainingModule`) once, and node schemas reference them by `$ref`. This is what stops step 3 talking about a different store than step 1, and it makes coherence structural rather than a matter of prompt quality.

### 5.2 Dataset

One coherent fill of one blueprint version. Both a fixture set and a golden test case.

```
Dataset {
  id: uuid
  version: integer                         // monotonic, bumped on any edit. Copy-on-write.
  archived: boolean                        // soft delete. Hidden from find, still servable.
  blueprint: { agent_id, version }
  seed: integer
  provenance: Provenance                   // REQUIRED. See 5.7.
  narrative: string                        // REQUIRED. The story in prose, in-world.
  labels: Labels                           // REQUIRED and COMPLETE. Every declared dimension.
  entities: { [entity_id]: Entity }        // the cast
  nodes: { [node_id]: NodeFixture }        // every node filled, so any path is playable
  pools: { [node_id]: NodeFixture[] }      // pool:true nodes, and loop iterations
  expected: ExpectedOutcome
  validated_at: timestamp
}

Provenance {
  title: string                            // REQUIRED. Short, human-scannable. Max 120 chars.
  intent: string                           // REQUIRED. Why this dataset exists. Min 30 chars.
  author: {
    name: string                           // REQUIRED. A person.
    handle: string                         // REQUIRED. Stable identifier.
    agent: "claude-code" | "codex" | "human" | "generator"   // what filled it
  }
  created_at: timestamp
  supersedes?: dataset_id                  // optional lineage
}

Entity {
  base: object                             // the state at run start
  revisions?: {                            // OPTIONAL: declared states this entity passes through
    [revision_id]: { after_node: node_id, state: object }
  }
}

NodeFixture {
  input?: object              // what the step should receive
  output: object              // what the mocked step returns, schema-valid
  entity_refs: string[]       // "store" or "store@after_onboarding" for a revised state
  latency_hint_ms?: number
  fault?: FaultSpec           // this fixture represents a failure
}

ExpectedOutcome {
  final: object                            // conforms to blueprint.outcome_schema
  expected_path?: node_id[]                // the traversal the agent should take
  node_expectations?: {                    // optional per-node assertions on agent behaviour
    [node_id]: { called: boolean, args_match?: JSONLogic }
  }
  comparison: "exact" | "schema" | "subset"   // a DECLARED INTENT, not executed by the service
  rationale: string                        // why this is the right answer, for the human reader
}
```

Note the distinction that matters: `nodes[].output` is what **the world returns to the agent**. `expected.final` is what **the agent should produce**. Mixing these two is the single easiest way to build a confusing product.

**`comparison` declares intent, it does not trigger anything.** The service never executes a comparison. It stores the expectation and, on request, emits an evidence bundle (expected versus actual, per node and terminal, plus the traversed path). Whatever grades the run, a test runner, an eval platform, or a human, reads that bundle and applies the declared mode. Carrying the mode on the dataset means every consumer grades it the same way instead of each inventing its own rule.

The three phase 1 modes are deterministic and need no model: `exact` (deep equality), `schema` (validates against `outcome_schema` only), `subset` (every field in `expected.final` is present and equal in the actual, extra fields ignored). `semantic` is deliberately absent, since a judge is an LLM and the service holds none. It belongs in whatever external grader the team already runs.

The Python client ships these three as pure functions so callers do not each reimplement deep equality. That is a convenience in the client, not a capability of the service.

**Every node in the graph is filled**, including nodes on branches not taken. That is what makes a full-graph blueprint playable: the run chooses the path, the dataset already has data waiting on all of them.

**Loop and pool draw order is in-order and deterministic.** Iteration N of a loop node takes `pools[node_id][N]`. If a loop runs more iterations than the pool has entries, the last entry repeats and the run is flagged with a `pool_exhausted` warning. Repeating rather than erroring is deliberate: an agent that loops one extra time should not get a hard failure for a reason the developer never chose, and the warning makes the condition visible without breaking the run. This matches agentest's sequence-mock precedent.

**Guarding against the tau2-bench failure.** Amazon had to publish a corrected fork of tau2-bench because task definitions drifted out of alignment with database contents. Mitigations here: `expected` is authored in the same LLM pass as the narrative and the fixtures, so it is never written against a different world; the validator checks `expected.final` against `outcome_schema` and every entity it references against the declared cast; and any edit to a dataset re-runs validation before the dataset is servable again.

### 5.3 Labels

Typed, not free-form. Free-form tags rot and fragment.

```
LabelSchema {
  dimensions: {
    persona:   ["new-franchisee", "multi-unit-operator", "corporate-admin"]
    scenario:  ["happy-path", "missing-documents", "compliance-overdue"]
    tier:      ["single-unit", "regional", "enterprise"]
    outcome:   ["success", "partial", "escalated", "failed"]
    edge_case: ["timezone-boundary", "unicode-names", "empty-history"]
  }
}
```

A dataset carries one value per dimension it declares. Retrieval is `find(persona=new-franchisee, scenario=compliance-overdue)` and it returns the same dataset every time.

### 5.4 Run

```
Run {
  id: string                  // CLIENT-GENERATED at library init. Idempotency key.
  agent_id, blueprint_version, dataset_id
  model: { provider, name, version }
  path: PathStep[]            // reconstructed from call order, not declared
  steps: StepRecord[]         // fixture served + actual agent output, per node per iteration
  outcome: object             // what the agent actually produced. NOT graded here.
  run_class: "dev" | "eval" | "load"
  warnings: Warning[]         // blueprint_version_mismatch, pool_exhausted, unresolved_step
  status, started_at, finished_at
  external_refs: { langfuse_run_id?, braintrust_experiment_id?, otel_trace_id? }
}
```

**The run id is generated by the client library when it is initialised**, before the first call, and is passed on every `fetch_step` and `record_step` for the whole end-to-end execution. Four properties follow:

1. **Retries are safe.** `(run_id, step_key, iteration)` is an idempotency key. Calling `fetch_step` twice for the same key returns the identical fixture and does not advance any cursor. A network retry or an agent-framework retry cannot corrupt the run.
2. **The path is reconstructed, not declared.** The ordered sequence of calls carrying a run id *is* the traversal. Branch selection is learned implicitly, so no `path_hint` parameter is needed and the agent cannot lie about where it went.
3. **Concurrency is free.** N load-test executions are N run ids against N datasets with no coordination.
4. **Idempotency never expires.** Because the run id is the primary key of a permanently stored record, a retry an hour or a week later still resolves to the same fixture. There is no window to tune and no expiry edge case to handle. See 8.2.

**Version pinning.** `run_start` records the blueprint version the dataset was built against and pins the run to it. If the agent declares a different version, the service emits a `blueprint_version_mismatch` warning on the response and on the stored run, and then serves the dataset anyway. Consistent with never gating: the developer is told loudly and decides for themselves.

### 5.5 Step identity resolution

`fetch_step` and `record_step` accept either `node_id` or `tool_name`. Resolution order:

1. If `node_id` is supplied, use it. Unambiguous, and this is what the CLI and generated code should emit.
2. If only `tool_name` is supplied and exactly one node in the blueprint declares it, resolve directly.
3. If several nodes declare the same `tool_name`, disambiguate against the run's reconstructed position: among nodes reachable from the current path head, pick the one declaring that tool. If exactly one, resolve.
4. If still ambiguous, return a structured error naming the candidate node ids. Never guess.

Rule 3 is why the run id matters even for tool-name addressing: without a known position, a repeated tool is unresolvable.

### 5.6 The runtime is read-only

**A step fetches stored data and executes. It never edits the dataset.** There is no write path from a running agent to a dataset, in any phase. `record_step` writes to the run, never to the world.

`run_start` pins `{dataset_id, dataset_version, blueprint_version}` and every subsequent `fetch_step` in that run resolves against that pin. Dataset edits are copy-on-write, producing a new version, so a run in flight keeps reading exactly what it started with, and a run finished two years ago is still readable against the version it used. This is also what makes soft archive coherent: an archived dataset disappears from `dataset_find` but stays servable to any run holding a pin to it.

Four consequences, and they are the reason this is worth a section of its own:

1. **No mutable world state anywhere in the system.** The hardest class of bug in fixture services, a run that corrupts the fixtures another run is reading, cannot occur.
2. **Concurrency needs no coordination.** N runs can share one dataset safely because nobody writes to it. Load testing therefore needs *assignment*, choosing which dataset each execution gets, not *reservation*, locking one so nobody else takes it. The phase 2 work is a selection strategy, not a locking scheme.
3. **The read path is trivially cacheable and horizontally scalable.** Immutable, versioned, content-addressable fixtures.
4. **The dataset encodes a timeline, and that timeline is baked at authoring time.** If step 2 is meant to update a `Store` that step 5 then reads, the author writes both states into the dataset. The runtime does not compute the second state, it serves it.

**Validating a timeline.** Because point 4 makes an entity legitimately different between step 2 and step 5, the validator cannot simply require an entity to be byte-identical everywhere. The `revisions` block resolves this. An entity may declare the states it passes through and which node introduces each transition. Node fixtures then reference `store@after_onboarding` rather than bare `store`, and the validator checks that every referenced revision exists, and that a node does not reference a revision introduced by a node downstream of it.

Entities with no `revisions` block are treated as constant for the whole run, and the validator then does require them to be byte-identical everywhere they appear. That default is the right one: most entities do not change, and silently permitting drift in them is exactly the incoherence this product exists to eliminate. An author who genuinely needs mutation has to say so, in one place, once.

### 5.7 Provenance is mandatory

A shared dataset library fails the moment a stranger cannot tell what a dataset is for. Twenty datasets identified only by UUID and a label tuple are twenty things nobody will reuse, and the first person who needs a missing-documents case writes a twenty-first rather than find the one that exists. So every dataset carries provenance, and the validator rejects a submission without it.

Four required pieces, and they are four different things:

| Field | Answers | Example |
|---|---|---|
| `title` | What is this, in one line? | "Multi-unit operator missing one FSSAI licence" |
| `narrative` | What happens in the world? | "Priya Raman already runs one outlet and is opening her second. Her intake is clean except a missing FSSAI licence, so the agent must chase exactly one document..." |
| `intent` | Why does this dataset exist? | "Covers the single-iteration loop path for an operator with prior locations, where training assignment should be reduced rather than full. Added after the agent assigned the full module set to a repeat operator." |
| `labels` | How do I find it? | Every dimension the blueprint declares, no gaps |

**`narrative` and `intent` are not the same field and must not be filled with the same text.** The narrative describes the world. The intent describes the purpose in the test suite: what behaviour it pins down, what bug prompted it, what would be untested if it were deleted. The first is what the agent sees, the second is what a reviewer needs. The validator rejects an empty or trivially short intent, and warns when intent is byte-identical to narrative, because that is what a hurried author does.

**Labels must be complete, not partial.** Every dimension the blueprint declares gets a value. A dimension that does not apply gets an explicit value saying so, which is why the worked example's `edge_case` vocabulary includes `none`. Partial labelling makes `dataset_find` unreliable: a query for `edge_case=none` should return every ordinary dataset, and it cannot if half of them left the field blank.

**Author is attribution, not authentication.** The service has no auth, so `author` is self-declared and recorded verbatim. It is required because a reviewer needs someone to ask about a dataset, and it is unverified because there is nothing to verify against. Nobody should build access control, ownership, or audit on top of this field. When auth arrives, author becomes derived rather than declared, and that is a deliberate future change rather than a gap.

## 6. The three flows

### Flow A: authoring
1. Developer opens the React app or CLI, names an agent, lays out the step graph.
2. Declares shared entities, then per-node input and output schemas referencing them.
3. Declares the label schema and the outcome schema.
4. `blueprint_validate` checks: every node reachable, every `$ref` resolves, every conditional edge references a field that exists in the source node's output schema, loop nodes bounded, tool names either unique or disambiguable by position.
5. Publish. The version is immutable. Blueprints are JSON and live in git.

**Blueprint inference is the adoption gate.** Nobody hand-writes per-step schemas for a cross-domain workflow. `blueprint_infer` bootstraps from an OpenAPI spec, an MCP server's `tools/list`, a LangGraph graph definition, or one recorded trace, then the developer edits. Phase 1.5, not "later".

### Flow B: ingestion (the handoff)
1. `dataset_skeleton(agent_id, version, labels, seed)` returns a skeleton plus a `skeleton_id` and a **section manifest**: provenance first, then the entity cast, then node groups, then the expected outcome. The manifest declares a fill order.

Provenance is filled first deliberately. Stating the intent before generating the world is what keeps the world on purpose, and an abandoned skeleton still shows what someone was trying to build.
2. Claude Code or Codex fills section by section, calling `dataset_fill_part(skeleton_id, section, content)` for each. Partial state is held server-side against the `skeleton_id`, so no single pass has to hold a 30-node blueprint at once.
3. `dataset_submit(skeleton_id)` finalises: full validation, then store.

**Why the manifest declares an order.** Node fixtures reference entities, so the cast must be filled before the nodes that reference it. `dataset_fill_part` validates locally, at schema level, and rejects a node section that references an entity not yet declared. Cross-cutting checks (entity coherence, timeline ordering, expected outcome, path reachability) run once at submit, because they cannot be evaluated on a fragment.

Submit rejects on: missing or trivial provenance, incomplete labels, a schema violation in any node output, an entity referenced but not defined, an entity without a `revisions` block differing between nodes, a revision referenced by a node upstream of the node that introduces it, a label value outside the declared vocabulary, a missing node, or an `expected.final` that violates `outcome_schema`. A rejection returns structured errors, scoped to the section that caused them, so the LLM repairs one part rather than regenerating everything.

No LLM call happens inside the service in phase 1. It hands out work and validates results.

**Two-phase generation, for cost and determinism.** The LLM writes the narrative, the cast and the expected outcome once (expensive, cacheable). Deterministic expansion from `seed` fills volume, repeated rows and pool entries (cheap, reproducible). A 10,000-row load-test dataset must not cost 10,000 LLM calls.

### Flow C: runtime
1. `run_start(agent_id, version, selector, run_id)` where `run_id` is client-generated and selector is a label query or an explicit dataset id.
2. At each step the agent calls `fetch_step(run_id, node_id | tool_name, iteration)`.
3. The agent calls `record_step(run_id, node_id | tool_name, iteration, actual_output)`.
4. `run_finish(run_id, outcome, status)`. The service stores what the agent produced. It does not compare it to anything.
5. `run_evidence(run_id)` returns the bundle an external grader needs: the expectation, the actual outcome, per-node expected versus actual, the traversed path against `expected_path`, the declared comparison mode, and any warnings.
6. `run_export(run_id, target)` pushes to Langfuse, Braintrust or an OTel collector.

Phase 1 ships steps 1 and 2 as plain reads with no reservation or recording. Phase 2 adds the rest.

**The service never fails a build and never grades.** It provisions data, records what happened, and hands out evidence. Deciding whether a run passed, and turning that into a red pipeline, both happen outside, in whatever test runner or eval platform the team already uses. `blueprint_diff` is informational and returns a structured diff, never an exit code.

## 7. MCP tool surface

**Blueprint:** `blueprint_upsert`, `blueprint_get`, `blueprint_list`, `blueprint_validate`, `blueprint_diff` (informational), `blueprint_infer` (1.5)

**Dataset:** `dataset_skeleton`, `dataset_fill_part`, `dataset_submit`, `dataset_validate`, `dataset_find` (label query), `dataset_get` (by id, optionally at a version), `dataset_archive`, `dataset_restore`, `dataset_expand`, `dataset_export` / `dataset_import` (local to shared promotion)

There is no `dataset_delete`. Archive is the only removal.

**Run (phase 2):** `run_start`, `fetch_step`, `record_step`, `run_finish`, `run_get`, `run_evidence`, `run_export`, `run_find` (by dataset, label, model version or run class)

There is no `run_grade`. Grading is external by design.

**Admin:** `store_status`, `label_vocabulary`, `agent_list`

## 8. Storage

### 8.1 Adapters

One adapter interface, three implementations.

| Target | Use | Config |
|---|---|---|
| SQLite | Local dev, CI, zero setup | file path |
| Postgres | Delightree default, matches the rebuild | DSN or discrete credentials |
| Mongo | Legacy Delightree, document-native fit for fixtures | connection URI |

Config accepts a connection string or discrete credentials, plus Docker parameters so the service can stand up its own store for local and CI use. Blueprints, datasets and runs are separable: a team may keep blueprints in git and only datasets in the store.

### 8.2 Retention: infinite, and why that is affordable

Runs are kept forever, keyed by the client-supplied run id. Datasets are permanent unless explicitly deleted, and a dataset referenced by any run cannot be hard-deleted.

This is affordable because **a run record does not copy the fixtures it used.** It stores the dataset id, the traversed path, the agent's actual per-step outputs, the final outcome and the warnings. The fixtures themselves are stored once, in the dataset, and every run that used them points at that one copy. A run is therefore small and roughly constant in size regardless of how large the dataset is.

The consequence worth stating plainly: the storage cost of load testing scales with the agent's own output volume, not with fixture volume. Ten thousand load runs against twenty datasets store twenty datasets and ten thousand thin records.

`run_class` (`dev`, `eval`, `load`) exists so load-test noise can be filtered out of queries, and so a team that later decides its load runs are not worth keeping can prune by class without touching eval history. Pruning is opt-in and manual. Nothing expires on its own.

Datasets are copy-on-write. An edit writes a new version and leaves the previous one intact, because a permanent run may be pinned to it. Storage growth from editing is bounded in practice, since datasets are authored a handful of times and then read many times. Archiving hides a dataset from `dataset_find` without removing anything.

### 8.3 Deployment modes

Both, and the promotion path between them matters as much as either.

| Mode | Shape | For |
|---|---|---|
| **Shared instance** | One service, one Postgres or Mongo, reachable by the team | Consuming datasets. Reuse is the point: a dataset authored once is used by every developer, every eval run and every load test. |
| **Local single-container** | One Docker container running the service with Mongo alongside, per developer | Authoring a new agent. Isolation while the blueprint and the first datasets churn, with no risk of polluting shared history. |

A third option exists for the impatient: `--store sqlite` with a file path and no container at all. Useful for a quick look, and it is what CI should use.

**The promotion path.** A developer who authors a blueprint and datasets locally must be able to publish them to the shared instance, otherwise local mode strands the work it was created to protect. `dataset_export` produces a portable bundle (blueprint version plus datasets), `dataset_import` ingests it into the shared instance and re-runs full validation on arrival. Blueprints, being JSON in git, promote by merge. This is small, and it is the difference between local mode being useful and local mode being a dead end.

## 9. Non-goals for phase 1

Stated explicitly so they do not creep back in:

- **No authentication, authorisation or multi-tenancy.** Flat internal service. The safety property is that the store holds no real data and the service is not publicly reachable. If either changes, this becomes a blocker and ReBAC per the rebuild's model becomes required.
- **No CI gating.** No exit codes, no `--fail-on-breaking`, no build-breaking behaviour of any kind.
- **No grading inside the service.** It stores expectations and emits evidence. Comparison, scoring and pass/fail all happen externally.
- **No LLM inside the service.** It hands out skeletons and validates submissions. This is also why `semantic` comparison is out of scope.
- **No owned dashboards or comparison UI.** Publish outward.
- **No interception of tool calls.** Pull only.
- **No graph authoring UI.** JSON editor with live validation, plus a read-only graph view.
- **No TypeScript client.** Python only. ~~TS is the closing milestone.~~ **Descoped entirely by the owner (R-68); it is not a later milestone, it is out of phase 1.**
- **No CLI.** Deferred. The MCP server, Python client and web app cover phase 1.
- **No writes from a running agent to a dataset.** Ever, in any phase, not just phase 1.
- **No hard delete.** Archive only.
- **No real or anonymised production data.**

## 10. Phasing

**Phase 1: the fixture factory** (internal, Astra)
Blueprint CRUD, validation and versioning. Full-graph topology. Entity-based coherence with optional revisions. Typed labels. Outcome schema and expected outcomes. Skeleton handoff with partial fills and validated submit. Label query retrieval. Dataset versioning, copy-on-write and archive. Three storage adapters. Shared and local-container deployment with an export and import promotion path. **Python client** issuing idempotent run ids and shipping the three comparison helpers as pure functions. React browsing app with a JSON editor and live validation. Read-only step fetch. Run linkage and fixture chain published outward to Langfuse or OTel.

Exit criteria: the location-onboarding workflow has a published blueprint; at least 20 datasets covering the declared label space, each with an expected outcome; a domain pod develops against fixtures instead of production for a full sprint without falling back.

**Phase 1.5: adoption unblockers**
`blueprint_infer` from OpenAPI, MCP `tools/list`, LangGraph graph, or a recorded trace. Deterministic `dataset_expand`. Export as agent-vcr `.vcr` cassettes and AIMock fixture configs so teams already on those tools adopt without a rip-and-replace.

**Milestone: TypeScript client — DESCOPED (R-68), retained for the record.** Parity with the Python client (run id generation, fetch, record, the three comparison helpers). Closes out the phase 1 line and unblocks the product stack. Deliberately last, so the client contract is settled by real Python usage before a second implementation has to track it.

**Phase 2: run lifecycle**
Per-step recording, full chain persistence, the evidence bundle, load-test dataset **assignment** strategies (round-robin, random-from-seed, label query over a pool), replay of a stored run. Note there is no reservation or locking: 5.6 makes datasets immutable, so concurrent runs sharing a dataset is safe by construction.

**Phase 3: owned drift and comparison**
Same-dataset diff across model versions, drift scoring, semantic comparison (LLM judge), comparison UI, and the clean / faulted / mitigated three-run pattern for attributing a behaviour change to the model rather than the environment.

### 10.4 The React app in phase 1

Scope is browse and edit, not visual graph authoring. Blueprints are JSON that lives in git; the app is a comfortable way to read and edit that JSON with the schema enforced live, plus a way to browse datasets by label and read the narrative.

Recommended libraries, all permissive and currently maintained:

| Need | Library | Notes |
|---|---|---|
| JSON editing with live schema validation | **`vanilla-jsoneditor`** ([josdejong/svelte-jsoneditor](https://github.com/josdejong/svelte-jsoneditor)) | ISC. Ajv-powered JSON Schema validation with live error display, tree / text / table modes, and repair. The `vanilla-jsoneditor` build is framework-agnostic and used from React directly. v3.12.0, March 2026, active. Best single fit. |
| Alternative editor | Monaco with JSON Schema | If the team already ships Monaco. Native schema validation, autocomplete and squiggles, but a heavier dependency for one screen. |
| Form-based authoring, if wanted later | [`react-jsonschema-form`](https://github.com/rjsf-team/react-jsonschema-form) or JSON Forms | Renders a form from the schema instead of showing JSON. Nicer for non-engineers filling datasets, unnecessary for engineers editing blueprints. |
| Read-only step graph view | [`@xyflow/react`](https://reactflow.dev/) (React Flow) | MIT. Renders the blueprint's nodes and edges so a developer can see the graph they wrote. Read-only in phase 1: it visualises, it does not author. |

`vanilla-jsoneditor` plus a read-only React Flow view is the whole phase 1 UI. Authoring stays in JSON.

## 11. Design principles

1. **The blueprint is the single source of truth.** Generation, validation and the evidence handed to external graders all derive from it.
2. **Hold the environment byte-identical.** A difference between two runs must be attributable to the model, not the world. This is the basis of the entire drift claim.
3. **Determinism is a property of the store, not the caller.** Same seed plus same blueprint version yields the same dataset.
4. **Reject invalid data loudly at write time.** A fixture that violates its schema produces a failure the developer will blame on the agent.
5. **Record, never gate.** The service reports. The caller decides.
6. **The runtime is read-only.** Agents fetch and execute. Nothing a running agent does can change what another run will see.
7. **Interoperate rather than replace.** Emit formats other tools consume.
8. **Cheap capability expansion is a first-class concern.** If a feature is inexpensive to implement and widens what the product can do, take it, as with expected outcomes.

## 12. Competitive position

Nobody occupies the blueprint plus narrative-fill space. The runtime mocking lane is well served: AIMock (open source, mocks LLMs, MCP, A2A and vector DBs from one config), agent-vcr (MIT `.vcr` cassettes with record, replay and diff), agentest (Vitest-style runner with per-scenario tool mocks and LLM-as-judge). All of them define mocks by hand or by recording, none has a fixture store, narrative coherence, labels or persistence. The comparison lane belongs to Braintrust and Langfuse.

Full detail in `docs/competitive-analysis.md`.

## 13. Naming

Requirement: the name contains "agent".

**Recommendation: `agent-props`.** In a production, props are fabricated objects that look real, made for a specific narrative, and assigned per scene. That is exactly what this makes. It also puns on component props, data passed into a step, which is literally what `fetch_step` does. One syllable, easy to type, and free on npm with no collision in the agent-testing space.

Alternatives, all free on npm:

| Name | Metaphor | Trade-off |
|---|---|---|
| `agent-backlot` | The studio's standing fake town where many productions film | Captures the reusable-world idea better than props, but longer and more obscure |
| `agent-canon` | The canonical facts of a fictional universe; a dataset is canon for a run | Clean, but collides mentally with "canonical" in data engineering |
| `agent-storyboard` | Blueprint is a storyboard, each node a panel | Most literally accurate, but search collides badly with AI video and storyboard generation tools |
| `agent-loom` | Weaving a coherent thread through steps | Pleasant, says nothing about fake data or narrative |

## 14. Open questions

Every question raised in v0.1 through v0.3 is now resolved and folded into sections 3, 5, 6, 8 and 10. The PRD is decision-complete for phase 1. What follows is build-time detail, not product direction, and none of it blocks starting.

1. **Skeleton partial-fill expiry.** Server-side partial state keyed by `skeleton_id` has to live somewhere. Discard after N days of no activity, or keep it like everything else? Leaning discard, since an abandoned half-filled skeleton is not an artifact anyone wants back.
2. **Revision reference syntax.** `store@after_onboarding` reads well but overloads `@`. Confirm during implementation that it does not collide with anything in the JSON Schema `$ref` handling.
3. **Export bundle format.** A single JSON file, or a directory the way blueprints already live in git? The directory form diffs better and merges better, which matters if the promotion path runs through a pull request.
4. **Does the web app write, or only read?** It has a JSON editor, so it can write. Confirm whether a developer edits a dataset in the browser (creating a new version) or whether all writes go through the MCP.
5. **Read-through caching on the shared instance.** Immutable versioned data makes this easy and it will matter under load-test volume. Probably phase 2, but the storage layer should not preclude it.
