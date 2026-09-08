# agent-props: contracts

Schemas, tool signatures, validation rule catalogue, storage interface and DDL.
Companion to `docs/build-handoff.md`. Read that first.

Revision: 1.1
Date: 2026-09-08

---

## 1. Error envelope

Every tool returns this shape on a validation or resolution failure. The service never raises for anything a user could cause.

```json
{
  "ok": false,
  "errors": [
    {
      "rule": "DS-008",
      "severity": "error",
      "pointer": "/nodes/verify_compliance/entity_refs/0",
      "section": "nodes.core",
      "message": "Entity 'store' has no revisions block but differs from its state in node 'fetch_store_profile'.",
      "context": { "entity": "store", "conflicting_node": "fetch_store_profile" }
    }
  ]
}
```

- `rule` is the catalogue id. **Tests assert on this, never on `message`.**
- `severity` is `error` or `warning`. Warnings never block a write or a read.
- `pointer` is an RFC 6901 JSON pointer into the submitted document.
- `section` is the skeleton section the error belongs to, so a partial fill can be repaired without regenerating the whole dataset. Null for non-skeleton contexts. The five sections are `provenance`, `entities`, `nodes.core`, `nodes.branches`, `expected`; the example above reads `nodes.core`, corrected from `nodes.compliance` by ruling R-06.

Success shape:

```json
{ "ok": true, "data": { }, "warnings": [ ] }
```

## 2. Domain models

Pydantic v2 in `models/`. JSON Schemas are emitted from these at build time into `schemas/` for the web app.

### 2.1 Blueprint

```jsonc
{
  "agent_id": "location-onboarding",        // ^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$
  "version": "1.0.0",                       // semver
  "description": "string",
  "status": "draft" | "published",          // published is immutable
  "entry_node": "receive_request",
  "entities": [
    {
      "id": "store",                        // ^[a-z][a-z0-9_]{0,62}$
      "schema": { }                         // JSON Schema, Draft 2020-12
    }
  ],
  "nodes": [
    {
      "id": "fetch_store_profile",
      "tool_name": "delightree.stores.get", // optional, enables tool-name addressing
      "kind": "tool_call" | "llm" | "decision" | "loop" | "terminal",
      "input_schema": { },                  // JSON Schema; may $ref an entity
      "output_schema": { },
      "pool": false,                        // required true when kind == "loop"
      "max_iterations": null,               // required when kind == "loop"
      "notes": "narrative hints for the generating LLM"
    }
  ],
  "edges": [
    { "from": "receive_request", "to": "fetch_store_profile", "condition": null },
    { "from": "check_docs", "to": "request_docs",
      "condition": { "==": [ { "var": "documents_complete" }, false ] } }
  ],
  "label_schema": {
    "dimensions": {
      "persona": ["new-franchisee", "multi-unit-operator", "corporate-admin"],
      "scenario": ["happy-path", "missing-documents", "compliance-overdue"]
    }
  },
  "outcome_schema": { }                     // JSON Schema for expected.final
}
```

**Entity `$ref` convention.** A node schema references an entity as `{"$ref": "entity:store"}`. The validator resolves `entity:` refs against the blueprint's entity list before handing the schema to `jsonschema`. Do not use HTTP or file refs.

**Condition language.** JSONLogic, evaluated against the source node's `output_schema`. The validator checks that every `var` path in a condition exists as a property in that schema. It does not evaluate conditions, ever. Runtime path selection is observed from call order, not computed.

### 2.2 Dataset

```jsonc
{
  "id": "uuid",
  "version": 1,                             // monotonic, bumped on edit, copy-on-write
  "archived": false,
  "blueprint": { "agent_id": "location-onboarding", "version": "1.0.0" },
  "seed": 20260908,

  "provenance": {
    "title": "Multi-unit operator missing one FSSAI licence",
    "intent": "Covers the single-iteration loop path for an operator with prior locations, where training assignment should be reduced rather than full. Added after the agent assigned the full module set to a repeat operator.",
    "author": { "name": "Priya Nair", "handle": "pnair", "agent": "claude-code" },
    "created_at": "2026-09-08T10:14:22Z",
    "supersedes": null
  },

  "narrative": "Priya Raman is opening her second location...",
  "labels": {
    "persona": "multi-unit-operator", "scenario": "missing-documents",
    "tier": "regional", "outcome": "success", "edge_case": "none"
  },
  "entities": {
    "store": {
      "base": { "id": "ST-4471", "name": "Koramangala 2", "status": "pending" },
      "revisions": {
        "after_docs": { "after_node": "request_docs",
                        "state": { "id": "ST-4471", "name": "Koramangala 2", "status": "docs_received" } }
      }
    }
  },
  "nodes": {
    "fetch_store_profile": {
      "input": { "store_id": "ST-4471" },
      "output": { "store": { "id": "ST-4471", "name": "Koramangala 2", "status": "pending" } },
      "entity_refs": ["store"],
      "latency_hint_ms": 120,
      "fault": null
    },
    "verify_compliance": {
      "output": { "store": { "id": "ST-4471", "status": "docs_received" }, "compliant": true },
      "entity_refs": ["store@after_docs"]
    }
  },
  "pools": {
    "request_docs": [
      { "output": { "requested": ["fssai"], "received": [] }, "entity_refs": [] },
      { "output": { "requested": ["fssai"], "received": ["fssai"] }, "entity_refs": [] }
    ]
  },
  "expected": {
    "final": { "onboarding_status": "complete", "outstanding_tasks": 0 },
    "expected_path": ["receive_request", "fetch_store_profile", "check_docs",
                      "request_docs", "verify_compliance", "complete"],
    "node_expectations": {
      "request_docs": { "called": true, "args_match": { "==": [ { "var": "doc_type" }, "fssai" ] } }
    },
    "comparison": "subset",
    "rationale": "The franchisee is missing one document, so the agent must loop once."
  },
  "validated_at": "2026-09-08T10:14:22Z"
}
```

**`provenance.intent` and `expected.rationale` are different fields.** `intent` says why the dataset exists in the suite. `rationale` says why that particular expected outcome is the correct one given this world. Both are required, neither substitutes for the other.

**`provenance` field constraints, enforced by the model and the validator:**

| Field | Constraint |
|---|---|
| `title` | 1 to 120 characters, non-whitespace |
| `intent` | at least 30 characters |
| `author.name` | 1 to 120 characters, non-whitespace |
| `author.handle` | `^[a-z0-9][a-z0-9._-]{0,62}$` |
| `author.agent` | one of `claude-code`, `codex`, `human`, `generator` |
| `supersedes` | null, or the id of a dataset that exists |

`author` is self-declared and recorded verbatim. There is no auth to verify it against. Do not build ownership, permissions or audit on this field.

### 2.2.1 DatasetSummary

What `dataset_find` returns per row. It exists so a reviewer can judge a dataset without fetching it, so provenance is carried in full and fixtures are not carried at all.

```jsonc
{
  "id": "uuid",
  "version": 1,
  "title": "Multi-unit operator missing one FSSAI licence",
  "intent": "Covers the single-iteration loop path for an operator with prior locations...",
  "labels": { "persona": "multi-unit-operator", "scenario": "missing-documents",
              "tier": "regional", "outcome": "success", "edge_case": "none" },
  "author": { "name": "Priya Nair", "handle": "pnair", "agent": "claude-code" },
  "blueprint": { "agent_id": "location-onboarding", "version": "1.0.0" },
  "narrative_excerpt": "Priya Raman already runs one outlet in Indiranagar and is...",
  "archived": false,
  "created_at": "2026-09-08T10:14:22Z"
}
```

`narrative_excerpt` is the first 200 characters. Never the whole narrative, and never any node fixture.

**`entity_refs` grammar:** `entity_id` for the base state, `entity_id@revision_id` for a revised state.

**Fault fixtures.** When `fault` is non-null the fixture represents a failure. `output` is then validated against the fault shape rather than the node's `output_schema`.

```jsonc
"fault": { "kind": "error" | "timeout" | "malformed", "code": "RATE_LIMITED", "after_ms": 3000 }
```

### 2.3 Run

```jsonc
{
  "id": "client-generated string",
  "agent_id": "location-onboarding",
  "pin": { "dataset_id": "uuid", "dataset_version": 1, "blueprint_version": "1.0.0" },
  "declared_blueprint_version": "1.1.0",
  "model": { "provider": "anthropic", "name": "claude-opus-5", "version": "20260401" },
  "run_class": "dev" | "eval" | "load",
  "path": [ { "node_id": "fetch_store_profile", "iteration": 0, "at": "2026-09-08T10:15:01Z" } ],
  "steps": [ { "node_id": "fetch_store_profile", "iteration": 0,
               "served": { }, "actual": { }, "recorded_at": "..." } ],
  "outcome": { },
  "warnings": [ { "code": "blueprint_version_mismatch", "detail": { } } ],
  "status": "running" | "finished" | "abandoned",
  "started_at": "...", "finished_at": null,
  "external_refs": { "otel_trace_id": null, "langfuse_run_id": null }
}
```

The run id is a client-supplied opaque string, 8 to 128 characters, `^[A-Za-z0-9_.:-]+$`. The service does not generate it and does not parse meaning from it.

## 3. Validation rule catalogue

Every rule needs an entry in `validation/registry.py` and at least one fixture in `tests/fixtures/broken/`. A test enforces both. Severity is `error` unless stated.

Sections 3.1 to 3.3 are the rule registry. **Section 3.4 is a response-code table, not part of the rule registry** (ruling R-12): those codes describe a `fetch_step` response, have no document to validate and no JSON pointer, so they carry no registry entry and no broken fixture. The drift test parses 3.1 to 3.3 only.

### 3.1 Blueprint rules

| Rule | Check |
|---|---|
| BP-001 | `agent_id` matches `^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$` |
| BP-002 | `version` is valid semver |
| BP-003 | Node ids are unique |
| BP-004 | Every edge `from` and `to` references an existing node |
| BP-005 | Every node is reachable from `entry_node` |
| BP-006 | `entry_node` exists and has no inbound edges |
| BP-007 | At least one node has `kind: terminal`, and terminal nodes have no outbound edges |
| BP-008 | `kind: loop` nodes declare `max_iterations` as an integer greater than 0 |
| BP-009 | Every `entity:` ref in any node schema resolves to a declared entity |
| BP-010 | Every `var` path in an edge condition exists as a property in the source node's `output_schema` |
| BP-011 | Every `input_schema` and `output_schema` is valid Draft 2020-12 JSON Schema |
| BP-012 | `outcome_schema` is valid Draft 2020-12 JSON Schema |
| BP-013 | Every `label_schema` dimension has at least one value, and values within a dimension are unique |
| BP-014 | If two nodes share a `tool_name`, they must not both be reachable in one step from any single node. Otherwise resolution is ambiguous and cannot be repaired at runtime |
| BP-015 | Entity ids are unique |
| BP-016 | A `published` blueprint version cannot be modified. Any upsert against an existing published `{agent_id, version}` is rejected |
| BP-017 | `kind: loop` implies `pool: true` |
| BP-018 | Every cycle in the graph passes through at least one `kind: loop` node |
| BP-019 | *(warning)* A node declares `notes`. Absent notes produce weaker generated data |

### 3.2 Dataset rules

| Rule | Check |
|---|---|
| DS-001 | `blueprint` references an existing published blueprint at that exact version |
| DS-002 | Every node in the blueprint has an entry in `nodes` |
| DS-003 | `nodes` contains no key that is not a blueprint node |
| DS-004 | Each fixture `output` validates against its node's `output_schema`, unless `fault` is set |
| DS-005 | Each fixture `input`, when present, validates against its node's `input_schema` |
| DS-006 | Every id in `entity_refs` resolves to a declared entity |
| DS-007 | *(warning)* Every declared entity is referenced by at least one fixture |
| DS-008 | An entity with no `revisions` block has a byte-identical embedded state everywhere it appears |
| DS-009 | Every `entity@revision` reference names a revision declared on that entity |
| DS-010 | A node must not reference a revision whose `after_node` is downstream of it, or unreachable from it |
| DS-011 | Every revision's `after_node` references an existing blueprint node |
| DS-012 | Every label dimension and value exists in the blueprint's `label_schema` |
| DS-013 | No label dimension appears twice |
| DS-014 | `expected.final` validates against the blueprint's `outcome_schema` |
| DS-015 | `expected.expected_path`, when present, is a valid traversal: consecutive pairs are connected by an edge, it starts at `entry_node`, and it ends at a terminal node |
| DS-016 | Every key in `expected.node_expectations` is an existing node |
| DS-017 | `expected.comparison` is one of `exact`, `schema`, `subset` |
| DS-018 | `pools` has an entry for every `pool: true` node and no entry for any other node |
| DS-019 | Every pool has at least one fixture, and each validates against the node's `output_schema` |
| DS-020 | `seed` is an integer |
| DS-021 | `narrative` is present and at least 30 characters |
| DS-022 | When `fault` is set, it conforms to the FaultSpec shape |
| DS-023 | A loop node's pool has at most `max_iterations` entries |
| DS-024 | `labels` carries a value for **every** dimension declared in the blueprint's `label_schema`. Partial labelling is rejected |
| DS-025 | `provenance.title` is present, non-whitespace, and at most 120 characters |
| DS-026 | `provenance.intent` is present and at least 30 characters |
| DS-027 | *(warning)* `provenance.intent` is byte-identical to `narrative`. They answer different questions |
| DS-028 | `provenance.author.name` is present and non-whitespace |
| DS-029 | `provenance.author.handle` matches `^[a-z0-9][a-z0-9._-]{0,62}$` |
| DS-030 | `provenance.author.agent` is one of `claude-code`, `codex`, `human`, `generator` |
| DS-031 | `provenance.supersedes`, when non-null, references a dataset that exists |
| DS-032 | *(warning)* `provenance.intent` is byte-identical to `expected.rationale` |
| DS-033 | `expected.rationale` is present and at least 30 characters (added by ruling R-21: contracts 2.2 requires it in prose, but no rule enforced it) |

### 3.3 Skeleton rules

| Rule | Check |
|---|---|
| SK-001 | The named section exists in the skeleton's manifest |
| SK-002 | Sections are filled in manifest order. Entities before any node section |
| SK-003 | A node section references only entities already declared in a filled section |
| SK-004 | `dataset_submit` requires every required section to be filled |
| SK-005 | `skeleton_id` exists and has not already been submitted |

### 3.4 Runtime codes

Errors:

| Code | Meaning |
|---|---|
| RT-E01 | Step ambiguous. Response lists candidate node ids |
| RT-E02 | Unknown node or tool name for this blueprint |
| RT-E03 | Unknown run id |
| RT-E04 | Dataset not found, or archived and not pinned by this run |

There is no RT-E05. Ruling R-03 deleted "iteration exceeds `max_iterations`": PRD 5.2's deliberate repeat-and-warn wins, so a loop drawing past its pool repeats the last entry with a `pool_exhausted` warning and the service never gates on iteration count.

Warnings, attached to both the response and the stored run, never blocking:

| Code | Meaning |
|---|---|
| `blueprint_version_mismatch` | The agent declared a version other than the pinned one. Served anyway |
| `pool_exhausted` | A loop drew past pool length. Last entry repeated |
| `dataset_archived` | The pinned dataset has since been archived. Served anyway |

## 4. MCP tool contracts

All tools return the envelope from section 1. Inputs listed as `name: type` with `?` marking optional.

### Blueprint

| Tool | Input | Returns |
|---|---|---|
| `blueprint_upsert` | `blueprint: object`, `publish?: bool` | The stored blueprint, or errors. Rejects on BP-016 |
| `blueprint_get` | `agent_id: str`, `version?: str` | Blueprint. Latest published when version omitted |
| `blueprint_list` | `status?: str` | Array of `{agent_id, version, status, description}` |
| `blueprint_validate` | `blueprint: object` | `{ok, errors}`. Does not store |
| `blueprint_diff` | `agent_id: str`, `from_version: str`, `to_version: str` | Structured diff: nodes added, removed, changed; edges added, removed; schema changes per node; label vocabulary changes. **Informational. Never a failure signal** |
| `blueprint_infer` | *(phase 1.5)* | Not in phase 1 |

### Dataset

| Tool | Input | Returns |
|---|---|---|
| `dataset_skeleton` | `agent_id: str`, `version: str`, `labels: object`, `seed: int` | `{skeleton_id, manifest: Section[], skeleton: object, instructions: str}` |
| `dataset_fill_part` | `skeleton_id: str`, `section: str`, `content: object` | `{ok, filled: str[], remaining: str[]}` or section-scoped errors |
| `dataset_submit` | `skeleton_id: str` | The stored dataset, or full validation errors |
| `dataset_validate` | `dataset: object` | `{ok, errors}`. Does not store |
| `dataset_find` | `agent_id?: str`, `labels?: object`, `author?: str`, `q?: str`, `blueprint_version?: str`, `limit?: int`, `offset?: int` | Array of dataset summaries. Excludes archived. **Deterministic ordering** by `(created_at, id)`. `author` filters on `provenance.author.handle`; `q` is a substring match over `title` and `intent` |
| `dataset_get` | `dataset_id: str`, `version?: int` | Full dataset. Latest version when omitted. Returns archived datasets by explicit id |
| `dataset_archive` | `dataset_id: str` | Updated summary |
| `dataset_restore` | `dataset_id: str` | Updated summary |
| `dataset_expand` | `dataset_id: str`, `node_id: str`, `count: int` | Seeded deterministic pool expansion |
| `dataset_export` | `agent_id: str`, `dataset_ids?: str[]` | Portable bundle: blueprint version plus datasets |
| `dataset_import` | `bundle: object` | Re-validates fully on arrival, then stores |

### Run

| Tool | Input | Returns |
|---|---|---|
| `run_start` | `run_id: str`, `agent_id: str`, `declared_blueprint_version?: str`, `selector: object`, `model?: object`, `run_class?: str` | `{run, pin, warnings}`. `selector` is `{dataset_id}` or `{labels}` |
| `fetch_step` | `run_id: str`, `node_id?: str`, `tool_name?: str`, `iteration?: int` | `{fixture, resolved_node_id, warnings}`. Idempotent on `(run_id, resolved_node_id, iteration)` |
| `record_step` | `run_id: str`, `node_id?: str`, `tool_name?: str`, `iteration?: int`, `actual: object` | `{ok}` *(phase 2)* |
| `run_finish` | `run_id: str`, `outcome: object`, `status: str` | The stored run *(phase 2)* |
| `run_get` | `run_id: str` | Full run |
| `run_evidence` | `run_id: str` | Evidence bundle *(phase 2, M10)* |
| `run_find` | `agent_id?`, `dataset_id?`, `run_class?`, `model?`, `limit?`, `offset?` | Run summaries |
| `run_export` | `run_id: str`, `target: str` | `{ok, external_ref}` *(M10)* |

Phase 1 ships `run_start` and `fetch_step` as reads. `record_step`, `run_finish`, `run_evidence` and `run_export` are phase 2, modelled now so the storage shape does not change later.

### Admin

| Tool | Input | Returns |
|---|---|---|
| `store_status` | none | `{backend, healthy, counts: {blueprints, datasets, runs}}` |
| `label_vocabulary` | `agent_id: str`, `version?: str` | The blueprint's `label_schema`, plus per-value dataset counts |
| `agent_list` | none | `{agent_id, versions[], dataset_count}` |

## 5. Step identity resolution

Implemented in `service/resolution.py`. This is the algorithm from PRD 5.5, stated precisely.

```
resolve(run, node_id?, tool_name?, iteration) -> node_id | RT-E01 | RT-E02

1. if node_id is given:
     if node_id in blueprint.nodes: return node_id
     else: return RT-E02
2. if tool_name is given:
     candidates = [n for n in blueprint.nodes if n.tool_name == tool_name]
     if len(candidates) == 0: return RT-E02
     if len(candidates) == 1: return candidates[0]
     # disambiguate by position
     head = run.path[-1].node_id if run.path else blueprint.entry_node
     reachable = successors(head)          # one hop, following edges
     narrowed = [c for c in candidates if c in reachable]
     if len(narrowed) == 1: return narrowed[0]
     return RT-E01 with candidates=[c.id for c in candidates]
3. return RT-E02
```

Never guess. `RT-E01` must list every candidate so the caller can retry with an explicit `node_id`.

## 6. Storage adapter interface

`storage/base.py`. Every adapter implements this Protocol. The conformance suite in `tests/integration/` is written once against it and parameterised over all three backends.

```python
class Store(Protocol):
    # blueprints
    def put_blueprint(self, bp: Blueprint, publish: bool) -> Blueprint: ...
    def get_blueprint(self, agent_id: str, version: str | None) -> Blueprint | None: ...
    def list_blueprints(self, status: str | None) -> list[BlueprintSummary]: ...

    # datasets: copy-on-write, never mutate in place
    def put_dataset(self, ds: Dataset) -> Dataset: ...          # bumps version
    def get_dataset(self, dataset_id: str, version: int | None) -> Dataset | None: ...
    def find_datasets(self, q: DatasetQuery) -> list[DatasetSummary]: ...  # excludes archived
    def set_archived(self, dataset_id: str, archived: bool) -> DatasetSummary: ...

    # skeletons: partial fill state
    def put_skeleton(self, sk: Skeleton) -> Skeleton: ...
    def get_skeleton(self, skeleton_id: str) -> Skeleton | None: ...
    def mark_skeleton_submitted(self, skeleton_id: str, dataset_id: str) -> None: ...

    # runs
    def put_run(self, run: Run) -> Run: ...
    def get_run(self, run_id: str) -> Run | None: ...
    def find_runs(self, q: RunQuery) -> list[RunSummary]: ...
    def upsert_step(self, run_id: str, step: StepRecord) -> StepRecord: ...  # idempotent

    def health(self) -> StoreHealth: ...
```

**There is no `delete_*` method on the Protocol.** That is deliberate and enforced by a test that asserts the Protocol has no method whose name starts with `delete`.

`upsert_step` is idempotent on `(run_id, node_id, iteration)`. Calling it twice with the same key is a no-op that returns the existing record.

## 7. SQL DDL

Postgres shown. SQLite is the same with `JSONB` as `JSON` and `TIMESTAMPTZ` as `TEXT`. Two Postgres-only indexes have no SQLite equivalent: `datasets_labels_gin` and `datasets_search`. SQLite falls back to a scan with `LIKE` for the `q` filter and JSON extraction for labels, which is fine at the volumes a local authoring instance sees. Mongo uses a text index on `{title, intent}` and a multikey index on `labels`. The conformance suite asserts identical results across all three, never identical query plans.

```sql
CREATE TABLE blueprints (
  agent_id      TEXT        NOT NULL,
  version       TEXT        NOT NULL,
  status        TEXT        NOT NULL CHECK (status IN ('draft','published')),
  document      JSONB       NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, version)
);

CREATE TABLE datasets (
  id            UUID        NOT NULL,
  version       INTEGER     NOT NULL,
  agent_id      TEXT        NOT NULL,
  bp_version    TEXT        NOT NULL,
  archived      BOOLEAN     NOT NULL DEFAULT false,
  labels        JSONB       NOT NULL,
  seed          BIGINT      NOT NULL,
  -- provenance promoted out of the document so find can filter and sort without parsing JSON
  title         TEXT        NOT NULL,
  intent        TEXT        NOT NULL,
  author_name   TEXT        NOT NULL,
  author_handle TEXT        NOT NULL,
  author_agent  TEXT        NOT NULL,
  supersedes    UUID        NULL,
  document      JSONB       NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id, version),
  FOREIGN KEY (agent_id, bp_version) REFERENCES blueprints (agent_id, version)
);
CREATE INDEX datasets_labels_gin ON datasets USING gin (labels);
CREATE INDEX datasets_lookup ON datasets (agent_id, bp_version, archived);
CREATE INDEX datasets_author ON datasets (author_handle);
CREATE INDEX datasets_search ON datasets USING gin (to_tsvector('english', title || ' ' || intent));

CREATE TABLE skeletons (
  id            UUID        PRIMARY KEY,
  agent_id      TEXT        NOT NULL,
  bp_version    TEXT        NOT NULL,
  manifest      JSONB       NOT NULL,
  parts         JSONB       NOT NULL DEFAULT '{}'::jsonb,
  submitted_as  UUID        NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runs (
  id            TEXT        PRIMARY KEY,
  agent_id      TEXT        NOT NULL,
  dataset_id    UUID        NOT NULL,
  dataset_ver   INTEGER     NOT NULL,
  bp_version    TEXT        NOT NULL,
  run_class     TEXT        NOT NULL DEFAULT 'dev',
  model         JSONB       NULL,
  status        TEXT        NOT NULL,
  outcome       JSONB       NULL,
  warnings      JSONB       NOT NULL DEFAULT '[]'::jsonb,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ NULL,
  external_refs JSONB       NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (dataset_id, dataset_ver) REFERENCES datasets (id, version)
);
CREATE INDEX runs_lookup ON runs (agent_id, run_class, started_at DESC);

CREATE TABLE run_steps (
  run_id        TEXT        NOT NULL REFERENCES runs (id),
  node_id       TEXT        NOT NULL,
  iteration     INTEGER     NOT NULL,
  seq           INTEGER     NOT NULL,          -- traversal order, reconstructs the path
  served        JSONB       NOT NULL,
  actual        JSONB       NULL,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  recorded_at   TIMESTAMPTZ NULL,
  PRIMARY KEY (run_id, node_id, iteration)     -- the idempotency key
);
```

Note what the run tables do **not** contain: any copy of the fixtures. `served` holds the fixture actually handed over, which is needed for evidence, but the dataset is referenced, not duplicated. The `PRIMARY KEY (run_id, node_id, iteration)` is what makes `fetch_step` idempotent at the storage layer rather than in application code.

## 8. Mongo collections

Same logical model. `blueprints` keyed by `{agent_id, version}`, `datasets` keyed by `{id, version}` with a compound index on `{agent_id, bp_version, archived}` and a multikey index on `labels`, `skeletons` keyed by `id`, `runs` keyed by `id`, and `run_steps` with a unique compound index on `{run_id, node_id, iteration}`.

Mongo has no foreign keys. The conformance suite tests referential behaviour through the service layer, not the storage layer, so this asymmetry does not change the tests.

## 9. Seeded expansion

`expansion/seeded.py`. One entry point.

```python
class Seeded:
    def __init__(self, seed: int, salt: str) -> None: ...
    def int(self, lo: int, hi: int) -> int: ...
    def choice(self, xs: Sequence[T]) -> T: ...
    def shuffled(self, xs: Sequence[T]) -> list[T]: ...
    def id(self, prefix: str) -> str: ...        # deterministic, replaces uuid4
    def timestamp(self, base: datetime, drift_s: int) -> datetime: ...
```

`salt` is the node id or field path, so two fields expanded from one seed do not correlate. A `hypothesis` property test asserts that identical `(seed, salt)` always yields identical output across processes, and that differing seeds usually diverge.

Nothing else in `src/` may import `random`, `uuid`, or call `datetime.now()` inside a generation or expansion path. Enforce with a ruff custom rule or a test that greps the tree.
