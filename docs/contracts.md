# agent-props: contracts

Schemas, tool signatures, validation rule catalogue, storage interface and DDL.
Companion to `docs/build-handoff.md`. Read that first.

Revision: 1.3
Date: 2026-09-09

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

`data` always carries the payload under **one named key** — `{"blueprint": {...}}`, `{"blueprints": [...]}`, `{"diff": {...}}`. The "Returns" column in section 4 describes that payload, not where in the envelope it sits. The named key is the only convention that works for the tools whose payload is an array, and using it everywhere means a caller never has to ask which tools wrap (added at M4; see `DECISIONS.md`).

`rule` also carries the five **boundary codes** in section 3.5, for the failures a user can cause that no catalogue rule covers.

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

**`expected.comparison` is stored semantics, not a hint.** The service never executes it (PRD 5.2), but the value is persisted in every dataset and every consumer grades by it — so what each mode *means* is part of what a stored dataset means, and changing a meaning later silently re-grades every dataset that uses it. The three, precisely, as the Python client implements them in `client/python/agentprops_client/compare.py`:

| mode | meaning |
|---|---|
| `exact` | deep equality of the two documents |
| `subset` | every field in `expected.final` is present and equal in the actual; extra fields are ignored **wherever they are**, so nested objects are subsetted too |
| `schema` | the actual validates against the blueprint's `outcome_schema` (Draft 2020-12). `expected.final` plays no part |

Three points where the one-line definitions leave a choice, all fixed:

- **`subset` over an array is positional** — same length, and element *i* of the expected must subset element *i* of the actual. It is **not** set-like containment. Ruling R-66 settles this as a data-contract decision for three reasons: a positional mismatch names an index and a set-like one only says "no element matched", which a dataset author cannot act on; set-like matching would mask an ordering bug, and rulings R-35 and R-58 spent two rulings making every ordering total and collation-stable so that order *is* meaningful; and an author who genuinely does not care about order already has `schema`, whose JSON Schema can describe a set. Pinned by `tests/unit/test_client_compare.py::test_a_superset_array_is_not_a_subset`, which passes only under the positional reading.
- **`null` is not absence.** An expected `null` requires the key to be **present** and hold `null`. `{"escalated_to": null}` does not match `{}`.
- **Numbers compare across `int`/`float`, and `bool` is neither.** `1` equals `1.0` — JSON has one number type and the difference is an artefact of the decoder — while `true` never equals `1`, in either direction. Note that `schema` mode is *more* permissive about the first: JSON Schema says a number with a zero fractional part **is** an integer, so `0.0` satisfies `{"type": "integer"}`. That the modes agree about `1` and `1.0` is a coincidence rather than a shared rule.

`semantic` is deliberately absent from the vocabulary: a judge is an LLM, and ground rule 4 puts none in the service.

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

Sections 3.1 to 3.3 are the rule registry. **Sections 3.4 and 3.5 are not part of the rule registry** (ruling R-12, extended at M4): 3.4's codes describe a `fetch_step` response and 3.5's describe a request that never became a document, so neither has a document to validate, a JSON pointer into one, a registry entry or a broken fixture. The drift test parses 3.1 to 3.3 only, and a test asserts the 3.5 codes are disjoint from the registry.

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
| BP-011 | Every `input_schema`, `output_schema` and entity `schema` is valid Draft 2020-12 JSON Schema (entity schemas added by ruling R-27: nothing checked them, because BP-011 strips `entity:` refs) |
| BP-012 | `outcome_schema` is valid Draft 2020-12 JSON Schema |
| BP-013 | Every `label_schema` dimension has at least one value, and values within a dimension are unique |
| BP-014 | If two nodes share a `tool_name`, they must not both be reachable in one step from any single node. Otherwise resolution is ambiguous and cannot be repaired at runtime |
| BP-015 | Entity ids are unique |
| BP-016 | A `published` blueprint version cannot be modified. An upsert against an existing published `{agent_id, version}` is rejected unless the submitted document is identical to the stored one (ruling R-29: a byte-identical re-publish changes nothing, and M7's import must stay idempotent) |
| BP-017 | `kind: loop` implies `pool: true` |
| BP-018 | Every cycle in the graph passes through at least one `kind: loop` node |
| BP-019 | *(warning)* A node declares `notes`. Absent notes produce weaker generated data |

### 3.2 Dataset rules

| Rule | Check |
|---|---|
| DS-001 | `blueprint` references an existing published blueprint at that exact version |
| DS-002 | Every node in the blueprint has an entry in `nodes` |
| DS-003 | `nodes` contains no key that is not a blueprint node |
| DS-004 | Each fixture `output` is present and validates against its node's `output_schema`; when `fault` is set the schema check is skipped and `output` may be absent (presence moved here from the model by ruling R-07's second amendment) |
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
| DS-017 | `expected.comparison` is one of `exact`, `schema`, `subset`. What each one *means* is stored semantics rather than a helper's private choice — see section 2.2, and ruling R-66 for why `subset` over an array is positional |
| DS-018 | `pools` has an entry for every `pool: true` node and no entry for any other node |
| DS-019 | Every pool has at least one fixture, and each validates against the node's `output_schema`, and each `input`, when present, against its `input_schema` (the `input` half added by ruling R-28: nothing validated it, since DS-005 covers `nodes` only) |
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

Warning codes are an open vocabulary (ruling R-22), so a tool may add one. M4 added `blueprint_version_missing`, attached by `blueprint_diff` when a version it was asked to compare does not exist — because that tool is informational and never a failure signal. M6 added two. `dataset_selection_ambiguous`, attached by `run_start` when a `{labels}` selector matched more than one dataset: selection is *assignment* rather than reservation (PRD 5.6 point 2), so several matches is not an error, and the warning names how many matched and which one was assigned (ruling R-54(a)). And `run_start_mismatch`, attached when `run_start` names a run id that already exists with arguments that do not match it — a different `agent_id`, or a selector that resolves to a different pin or to nothing at all. Ruling R-53: the run keeps its pin and is returned unchanged, because a retried request must neither create a second run nor fail, and the divergence is reported rather than ignored. Its detail carries `diverged`, `pinned` and `requested`. M7 added one more. `expansion_added_nothing`, attached by `dataset_expand` when `count` is zero (or negative, and clamped to zero): the argument is well formed, the service does not refuse well-formed requests, and writing a byte-identical new version would put a meaningless entry in a lineage a reviewer reads — so the caller gets the unchanged dataset and is told that nothing was added. Its detail carries `dataset_id`, `node_id` and `requested`.

M8 added **two**, both on the run writes ruling R-15 lands there. `run_already_finished`, attached by `fetch_step` and `record_step` when the run's lifecycle is closed, and by `run_finish` on an **identical** repeat — the condition is the same in all three cases, so the code is: this run is already closed. Ruling R-54(c) settles that a finished run still serves ("M8 may add a warning if it proves useful; it must not add a refusal") and this is the warning half. Its detail carries `run_id`, `status` and `finished_at`, and deliberately no `node_id`, so R-54(b)'s merge key records it once per run rather than once per step. And `step_actual_already_recorded`, attached by `record_step` when the step already records the **same** actual: ruling R-65's no-op-success half, "the caller's intent is already satisfied, nothing changed, and saying so is honest". Its detail carries `run_id`, `node_id` and `iteration`, so it is keyed per step.

M8 first added **two others** and ruling R-65 removed them, which is worth recording rather than quietly reverting. `step_actual_conflict` and `run_finish_mismatch` were warnings on a *successful* response for a re-write whose value **differed**, argued from R-53 and ground rule 3. They are now `AP-007` on an `ok: false` envelope (section 3.5), because for a differing value the write **did not happen**: `ok: true` misreports what the store holds, and `set_step_actual` already *raises* (R-33), so a tool reporting success would be claiming one the storage layer declined to give. So a caller distinguishes the three outcomes of a write by `ok` plus one code — "it landed" is a bare success, "it was already there" is a success with `run_already_finished` or `step_actual_already_recorded`, and "someone else's value is there" is `ok: false` with `AP-007`.

Each addition is a constant in the module that attaches it rather than in `models/errors.py`, whose `RUNTIME_WARNING_CODES` stays exactly the three codes tabulated above; `tests/unit/test_validation_drift.py` asserts both halves of that arrangement.

### 3.5 Boundary codes

**Not rules, and never registered as ones.** Three failures a user can cause happen before or after a document exists, so no `BP-*`/`DS-*` rule can own them; two more are resolution rather than validation, which section 1's envelope covers explicitly ("on a validation **or resolution** failure"); the sixth is a deterministic id space that has no free value left, and the seventh is a write-once value that is already recorded and differs from the one supplied. They appear in `rule`, carry a pointer at the offending *argument*, and are `error` severity. Added at M4, extended at M5 by ruling R-49(b); `service/envelope.py` is the implementation and `tests/unit/test_service_envelope.py` asserts they are disjoint from the registry and from this catalogue.

| Code | Meaning |
|---|---|
| AP-001 | A request argument is missing, of the wrong JSON type, or mutually exclusive with another that was also given |
| AP-002 | Raw JSON text did not parse |
| AP-003 | The document satisfied every catalogue rule and still would not construct as a model — the residue of rulings R-04 and R-23, since no rule owns an unknown key or a list where an object belongs |
| AP-004 | No record with that id, or no such version |
| AP-005 | The store refused a write through one of its programming-error guards. Reaching this means a rule that should have caught the condition did not |
| AP-006 | A deterministic id space is exhausted: every id derivable from the given arguments already names a record. The arguments are well formed and simply cannot be served, so one of them has to change (ruling R-49(b): reporting this as `AP-001` said "bad argument" when the truth is "this seed's id space is full") |
| AP-007 | A **write-once** value is already recorded and **differs** from the one supplied, so the write did not happen. Added at M8 by ruling R-65 for `record_step` (a step already records a different `actual`) and `run_finish` (the run is already closed with a different `status` or `outcome`). An *identical* re-write is **not** this — it is a no-op success, because the caller's intent is already satisfied and a retry after a network blip must be safe. Distinct from `AP-005`, and the distinction is the reason both exist: this means "a value is there and it is not yours", `AP-005` means the store refused with **nothing** recorded, and telling a caller its evidence lost to a value that does not exist is the error the classification in `service/runs.py::_conflicted` avoids. Reporting a differing re-write as `ok: true` would claim a success the storage layer explicitly declined to give (`set_step_actual` raises, per ruling R-33) — the same class of defect as M3's silent success carrying the winner's value. **This is not gating:** ground rule 3 forbids refusing to *serve*, and ruling R-56 already settled that a refused **write** is `ok: false` |

## 4. MCP tool contracts

All tools return the envelope from section 1. Inputs listed as `name: type` with `?` marking optional.

Every list-returning tool has a **total** order, stated in its row: a partial order that happens to be stable on one backend diverges on another, and identical inputs must give byte-identical output on all three (ruling R-35, extended to `run_get`'s steps by ruling R-37).

### Blueprint

| Tool | Input | Returns |
|---|---|---|
| `blueprint_upsert` | `blueprint: object`, `publish?: bool` | The stored blueprint, or errors. Rejects on BP-016 |
| `blueprint_get` | `agent_id: str`, `version?: str` | Blueprint. Latest published when version omitted |
| `blueprint_list` | `status?: str` | Array of `{agent_id, version, status, description}`. **Deterministic ordering** by `(agent_id, version)`, version compared as semver so `1.10.0` sorts after `1.9.0` (ruling R-35) |
| `blueprint_validate` | `blueprint: object` | `{ok, errors}`. Does not store |
| `blueprint_diff` | `agent_id: str`, `from_version: str`, `to_version: str` | Structured diff in **five** categories: nodes added, removed, changed; edges added, removed; schema changes per node; label vocabulary changes; and blueprint-level changes — `entry_node`, `description`, `outcome_schema` and entity schemas added, removed or changed (the fifth added by ruling R-41: the first four left those four fields unreportable, and R-27 had just made entity schemas validated). **Informational. Never a failure signal** — an absent version is `present: false` plus a `blueprint_version_missing` warning on a successful response. The payload shape these categories take is defined in `service/diff.py` and recorded in `DECISIONS.md` (M4) |
| `blueprint_infer` | *(phase 1.5)* | Not in phase 1 |

### Dataset

| Tool | Input | Returns |
|---|---|---|
| `dataset_skeleton` | `agent_id: str`, `version: str`, `labels: object`, `seed: int` | `{skeleton_id, manifest: Section[], skeleton: object, instructions: str}` |
| `dataset_fill_part` | `skeleton_id: str`, `section: str`, `content: object` | `{ok, filled: str[], remaining: str[]}` or section-scoped errors |
| `dataset_submit` | `skeleton_id: str` | The stored dataset, or full validation errors |
| `dataset_validate` | `dataset?: object`, `dataset_json?: str` | `{ok, errors}`. Does not store. Exactly one of the two supplies the document; `dataset_json` is raw JSON **text** and is the only form that can report DS-013, because duplicate keys stop existing the moment JSON is parsed. Added at M4 to make ruling R-20's boundary detection reachable: the MCP SDK parses the request and pre-parses a JSON-string argument before any service code runs, so a parameter annotated `object` never sees raw text |
| `dataset_find` | `agent_id?: str`, `labels?: object`, `author?: str`, `q?: str`, `blueprint_version?: str`, `limit?: int`, `offset?: int` | Array of dataset summaries, **one row per dataset lineage at its latest version** (ruling R-38). Excludes archived. **Deterministic ordering** by `(created_at, id)`. `author` filters on `provenance.author.handle`; `q` is a case-folded substring match over `title` and `intent`, and substring semantics are the contract on every backend (ruling R-36) |
| `dataset_get` | `dataset_id: str`, `version?: int` | Full dataset. Latest version when omitted. Returns archived datasets by explicit id |
| `dataset_archive` | `dataset_id: str` | Updated summary |
| `dataset_restore` | `dataset_id: str` | Updated summary |
| `dataset_expand` | `dataset_id: str`, `node_id: str`, `count: int` | Seeded deterministic pool expansion, stored as a **new version** of the same lineage. Each new entry is a deterministically chosen copy of one of the node's authored fixtures with its `latency_hint_ms` varied, addressed by its position in the pool — expansion fills volume (PRD 4) and does not invent fixture content, because a conforming instance of an arbitrary `output_schema` would need a model and ground rule 4 forbids one. Seeded from the dataset's own `seed` and `expand:<node_id>:<index>`. An expansion past a loop node's `max_iterations` is **DS-023** and stores nothing, with both numbers in the finding's `context` (ruling R-56); a `count` above 10000 in one call is `AP-001` naming that maximum, because R-56 forbids silently serving a smaller expansion than was asked for; a `count` of zero returns the dataset unchanged with an `expansion_added_nothing` warning. A node with no authored pool is answered by the catalogue — DS-018 or DS-019 — rather than by a boundary code |
| `dataset_export` | `agent_id: str`, `dataset_ids?: str[]` | Portable bundle: blueprint version plus datasets. `{format: "agentprops.bundle", format_version: 1, agent_id, blueprints[], datasets[]}`, with the keys in that order and the documents written `exclude_unset` — so two exports of one store are byte-identical and an export/import/export cycle returns the same bytes. Only the blueprint versions the exported datasets reference are carried, and they have to be: DS-001 requires a dataset to name an existing *published* blueprint. `dataset_ids` omitted exports every **discoverable** dataset at its latest version in `dataset_find` order; given, it exports those lineages at their latest version, archived or not, in the order asked for, and an id naming nothing or belonging to another agent is `AP-004` rather than a silent omission |
| `dataset_import` | `bundle: object` | Re-validates fully on arrival, then stores. Every blueprint through the whole `BP-*` catalogue and every dataset through the whole `DS-*` catalogue, against **this** store — DS-001 and DS-031 are existence checks the receiving store can answer differently, which is the whole point. **Nothing is written until all of it passes**: the dataset pass runs against a resolver that answers from the store *plus this bundle*, so a rejected import cannot leave behind a published blueprint version that BP-016 has made immutable. `format` and `format_version` are checked first and are `AP-001`, so a caller who passed the wrong object is told that rather than handed the catalogue's opinion of it. Findings are re-pointed into the bundle (`/datasets/2/provenance/title`). Versions are allocated by the receiving store, so an import into an empty store starts at 1; `created_at` is authored content and survives, which is what keeps `dataset_find`'s ordering identical on both sides (ruling R-09) |

### Run

| Tool | Input | Returns |
|---|---|---|
| `run_start` | `run_id: str`, `agent_id: str`, `declared_blueprint_version?: str`, `selector: object`, `model?: object`, `run_class?: str` | `{run, pin, warnings}`. `selector` is `{dataset_id}` or `{labels}` |
| `fetch_step` | `run_id: str`, `node_id?: str`, `tool_name?: str`, `iteration?: int` | `{fixture, resolved_node_id, warnings}`. Idempotent on `(run_id, resolved_node_id, iteration)` |
| `record_step` | `run_id: str`, `node_id?: str`, `tool_name?: str`, `iteration?: int`, `actual: object` | `{ok}`, plus the stored step and the `resolved_node_id` under the `record` key. Addressed exactly as `fetch_step` is, and keyed on the **resolved** step (ruling R-64), so a step fetched by tool name can be recorded by node id. **Write-once per key** (ruling R-33), split by ruling R-65: an identical re-record is a no-op success carrying `step_actual_already_recorded`, a **differing** one is `AP-007` and nothing is written, and an actual for a step that was never served is `AP-004`. **Never grades** — `actual` is stored verbatim and is checked against neither `expected.final` nor the node's `output_schema`, because ground rule 2 puts comparison in the client. Built at M8 by ruling R-15 |
| `run_finish` | `run_id: str`, `outcome: object`, `status: str` | The stored run. `status` is `finished` or `abandoned`; anything else, `running` included, is `AP-001` naming the vocabulary. A compare-and-set, so the **first** close wins (ruling R-65): an identical repeat is a no-op success carrying `run_already_finished`, and a divergent one is `AP-007` with nothing written — the recorded outcome stays what it was. `finished_at` comes from the injected clock (ruling R-09). **Never grades** — `outcome` is stored even when the blueprint's `outcome_schema` would reject it. A closed run still serves `fetch_step` (ruling R-54(c)), with a `run_already_finished` warning. Built at M8 by ruling R-15 |
| `run_get` | `run_id: str` | Full run. `steps` and the reconstructed `path` are **deterministically ordered** by `(seq, node_id, iteration)` (ruling R-37) |
| `run_evidence` | `run_id: str` | Evidence bundle *(phase 2, M10)* |
| `run_find` | `agent_id?`, `dataset_id?`, `run_class?`, `model?`, `limit?`, `offset?` | Run summaries. **Deterministic ordering** by `(started_at DESC, id)`, newest first (ruling R-35) |
| `run_export` | `run_id: str`, `target: str` | `{ok, external_ref}` *(M10)* |

Phase 1 ships `run_start` and `fetch_step` as reads, and `record_step` and `run_finish` as the only two writes a running agent can make — both to the **run**, never to a dataset, which is the division PRD 5.6 draws and `tests/unit/test_runtime_is_read_only.py` enforces over the call graph. `run_evidence` and `run_export` stay phase 2 until M10, modelled now so the storage shape does not change later.

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
    def mark_skeleton_submitted(self, skeleton_id: str, dataset_id: str) -> bool: ...  # CAS

    # runs
    def put_run(self, run: Run) -> Run: ...
    def get_run(self, run_id: str) -> Run | None: ...
    def find_runs(self, q: RunQuery) -> list[RunSummary]: ...
    def upsert_step(self, run_id: str, step: StepRecord) -> StepRecord: ...  # idempotent
    def set_step_actual(self, run_id: str, node_id: str,
                        iteration: int, actual: Mapping) -> StepRecord: ...  # write-once
    def set_run_warnings(self, run_id: str,
                         warnings: Sequence[Warning]) -> list[Warning]: ...  # one column
    def mark_run_finished(self, run_id: str, status: str, outcome: Mapping,
                          finished_at: datetime) -> bool: ...  # CAS, three columns

    def health(self) -> StoreHealth: ...
```

**There is no `delete_*` method on the Protocol.** That is deliberate and enforced by a test that asserts the Protocol has no method whose name starts with `delete`.

`upsert_step` is idempotent on `(run_id, node_id, iteration)`. Calling it twice with the same key is a no-op that returns the existing record.

`mark_skeleton_submitted` is a **compare-and-set** (ruling R-47): it claims the skeleton only if `submitted_as` is currently null and returns whether this call is the one that claimed it. `dataset_submit` orders itself validate -> CAS -> `SK-005` on loss -> `put_dataset` on win, which is what makes "a skeleton becomes exactly one dataset" an invariant the store holds rather than one the catalogue only asserts. It replaced a no-op replay tolerance, under which two concurrent submits both passed SK-005 and the loser silently received a success envelope for a second dataset version. Ruling R-48 deliberately leaves `put_skeleton` last-write-wins: a lost fill is visible in the next response's `remaining` and self-correcting; a lost submit is silent.

`set_run_warnings` replaces a run's warning list and **touches no other column**. Added at M6, authorised by ruling R-53 (a diverging `run_start` warns "attached to the response *and* to the stored run") and R-54(b) (the merge is read-modify-write, and dedupes by `(code, node_id, iteration)`). It exists because the obvious implementation of both — `put_run` with an edited copy of the run — is wrong in a way no test today would catch: `put_run` writes every column from the model it is handed, so a `fetch_step` adding a `pool_exhausted` warning from a snapshot read before a concurrent `run_finish` committed would revert `status` to `running` and null `outcome` and `finished_at` — which M8 has now made a live race rather than a hypothetical one, since `run_finish` exists. The caller computes the merged list, because deduplication is policy and this Protocol decides none; the column bound is what makes the residue R-54(b) accepts — a lost *advisory* warning — actually the only residue. A transaction would not be that bound: pysqlite defers `BEGIN` until the first DML and Postgres under READ COMMITTED behaves the same way, so wrapping a read and a write does not lock the value read (ruling R-37). Raises `RecordNotFoundError` for an unknown run, for the reason `set_archived` does.

`mark_run_finished` is M8's, added where ruling R-15 lands `run_finish`. It writes `status`, `outcome` and `finished_at` and **touches no other column**, which is `set_run_warnings`' guarantee mirrored: the two narrow writes are each other's counterpart, one owning `warnings` and one owning the lifecycle, so neither can revert the other's column. Until M8 the un-finishing bug `set_run_warnings` exists to prevent was unreachable only because the other writer did not exist. It is a **compare-and-set** on `finished_at IS NULL` rather than an unconditional update, for ruling R-47's reason: an unconditional write makes the loser of two concurrent finishes disappear silently and hands *both* callers a success envelope naming their own outcome while the row holds one of them. The boolean says which call closed the run, and the service reports a divergence as a `run_finish_mismatch` warning rather than an error, because the service never gates. `finished_at IS NULL` is the predicate rather than `status = 'running'` because both terminal statuses set it, so one clause covers `finished` and `abandoned` without the Protocol holding an opinion about a vocabulary it does not own. Raises `RecordNotFoundError` for an unknown run, for the reason `set_archived` does.

`set_step_actual` is the other half, added by ruling R-33: `upsert_step` records **what was served** and its repeat must stay a literal no-op, so it can never also merge in an `actual`. `set_step_actual` records **what the agent did**, write-once per key, and fails if no step record exists — an actual cannot be reported for a step that was never served. Re-recording an identical actual is a no-op success; a differing one is refused. It writes to a run, never to a dataset, so ground rule 1 is untouched.

## 7. SQL DDL

Postgres shown. SQLite is the same with `JSONB` as `JSON` and `TIMESTAMPTZ` as `TEXT`. **One** Postgres-only index has no SQLite equivalent: `datasets_labels_gin`. SQLite falls back to JSON extraction for labels, which is fine at the volumes a local authoring instance sees. Mongo uses a **wildcard** index on `labels.$**`. The conformance suite asserts identical results across all three, never identical query plans.

`datasets_search` was the second Postgres-only index and **M7 dropped it**, which this revision records. It was declared as a `to_tsvector` GIN index; ruling R-36 superseded that by making `q` substring matching *by contract*, and ruling R-39(a) then moved both the case fold and the match into Python because no query expression folds case identically on all three backends. So `find_datasets` emits no text match in SQL at all, and the index served nothing while costing every write. R-36 offers a `pg_trgm` GIN index instead and it is dead for the same reason — there is no `LIKE` for it to accelerate. R-36 also names `ILIKE`, and an `ILIKE` prefilter was **measured and rejected**: a prefilter is only safe if it matches a superset of the Python fold, and `str.casefold` folds `ß` to `ss` while Postgres does not, so against Postgres 17 (UTF8, `en_US.utf8`) `'Straße operator' ILIKE '%strasse%'` is false where Python matches — in both directions. A prefilter that drops rows the contract promises is worse than a scan. Mongo likewise uses no text index, for the same reason: `$regex` with `i` is a third answer to case folding.

One further Postgres-only detail, added at M7 and not visible in the DDL below: a `TEXT` **tie-break** in an `ORDER BY` is emitted as `COLLATE "C"` on Postgres. Ruling R-35 requires every list ordering to be total so that identical inputs give byte-identical output on every backend, and a locale collation is not that — a Postgres created with `en_US.utf8` orders `'runa'` before `'run-b'`, while SQLite (`BINARY`) and Mongo (byte-wise) both order `'run-b'` first. Measured, and covered by `test_find_runs_breaks_a_tie_by_byte_order` in the conformance suite. It applies to `runs.id` in `find_runs` and `run_steps.node_id` in `get_run`; `datasets.id` needs nothing, because a `UUID` column is not collated.

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
  -- no DEFAULT now(): populated from provenance.created_at on insert, so
  -- dataset_find's ordering by (created_at, id) survives an export/import
  -- cycle. A defaulted column would be re-stamped on re-import (ruling R-09)
  created_at    TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (id, version),
  FOREIGN KEY (agent_id, bp_version) REFERENCES blueprints (agent_id, version)
);
CREATE INDEX datasets_labels_gin ON datasets USING gin (labels);
CREATE INDEX datasets_lookup ON datasets (agent_id, bp_version, archived);
CREATE INDEX datasets_author ON datasets (author_handle);
-- There is no datasets_search. It was a to_tsvector GIN index here until M7,
-- superseded by ruling R-36 (`q` is substring matching by contract) and then
-- made dead by R-39(a), which moved the fold and the match into Python on every
-- backend. R-36's pg_trgm alternative is dead for the same reason. See the
-- prose above for the measurement that closes the ILIKE option.

CREATE TABLE skeletons (
  id            UUID        PRIMARY KEY,
  agent_id      TEXT        NOT NULL,
  bp_version    TEXT        NOT NULL,
  labels        JSONB       NOT NULL,   -- dataset_skeleton's labels input (added at M5)
  seed          BIGINT      NOT NULL,   -- dataset_skeleton's seed input (added at M5)
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
  -- what the agent said it was running, as against bp_version, which is what
  -- it is being served: the input to the blueprint_version_mismatch warning,
  -- which ground rule 3 requires be stored on the run (ruling R-32)
  declared_bp_version TEXT  NULL,
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
  PRIMARY KEY (run_id, node_id, iteration),    -- the idempotency key
  UNIQUE (run_id, seq)                         -- what makes seq allocation sound (R-37)
);
```

`skeletons.labels` and `skeletons.seed` were added at M5. `dataset_skeleton` takes both as inputs, ruling R-06 settles that neither is a *fillable section*, and `dataset_submit(skeleton_id)` takes no other argument - so a skeleton that does not carry them cannot be assembled into a dataset, and neither value exists anywhere else to be derived from. Same class of omission as the missing `runs.declared_bp_version` column (ruling R-32). DS-012, DS-024 and DS-020 still own the values, at submit, against the assembled document.

Note what the run tables do **not** contain: any copy of the fixtures. `served` holds the fixture actually handed over, which is needed for evidence, but the dataset is referenced, not duplicated. The `PRIMARY KEY (run_id, node_id, iteration)` is what makes `fetch_step` idempotent at the storage layer rather than in application code.

## 8. Mongo collections

Same logical model. `blueprints` keyed by `{agent_id, version}`, `datasets` keyed by `{id, version}` with a compound index on `{agent_id, bp_version, archived}` and an index over `labels`, `skeletons` keyed by `id`, `runs` keyed by `id`, and `run_steps` with unique compound indexes on `{run_id, node_id, iteration}` (the idempotency key) and on `{run_id, seq}` (ruling R-37, what makes `seq` allocation sound).

Each natural key **is** the `_id`, so uniqueness is the server's rather than a second index that could be missing — and an `ObjectId` the driver generated would embed a client clock and a per-process random value, which ground rule 9 forbids in `storage/`.

Mongo has no foreign keys. The conformance suite tests referential behaviour through the service layer, not the storage layer, so this asymmetry does not change the tests.

Four things M7 settled while building the adapter, recorded here because they are contract-level rather than implementation detail.

**Object keys are escaped.** Two of ruling R-06's five section ids are `nodes.core` and `nodes.branches`, `Skeleton.parts` is keyed by section id, and Mongo reads `parts.nodes.core` as two levels of nesting. Every authored document this service stores — a fixture's `output`, an entity's `state`, a step's `served` — is arbitrary caller JSON with the same hazard, plus a leading `$` that looks like an operator. So every opaque JSON value is stored with its object keys percent-escaped (`%` → `%25`, `.` → `%2E`, `$` → `%24`, in that order, which is what makes it reversible) and decoded on read. Only keys are transformed; a *value* that looks like an escape comes back exactly as written. The promoted, queryable fields are stored natively, and `labels` is queried through the same escape so a label dimension containing a dot is filterable rather than unreachable.

**The `labels` index is a wildcard index, not a multikey one.** "Multikey" describes an index over an array-valued field; `labels` is an object, and a plain index on it serves only whole-object equality. `labels.$**` is what accelerates `labels.tier == "regional"`, which is the query `find_datasets` issues.

**There is no text index on `{title, intent}`.** Ruling R-36 makes `q` substring matching by contract and R-39(a) puts the fold in Python; a text index does stemming, so it would serve a query nobody issues. See section 7.

**The clock is the server's.** Mongo has no column defaults, so where the SQL adapter relies on `DEFAULT now()` or `func.now()` — `blueprints.created_at`, `run_steps.fetched_at`, `set_step_actual`'s `recorded_at` — the Mongo adapter reads `localTime` from the `hello` command. That is a database clock, which ruling R-09 sanctions and R-39(d) reads as "whether a column default or an explicit `func.now()`", and it keeps `storage/` free of any Python clock read. An aggregation-pipeline update using `$$NOW` would have saved the round trip and was rejected: in a pipeline update every literal is an *expression*, so a stored value that happened to be the string `"$total"` would resolve as a field path — a hole in exactly the place, authored fixture content, where values are least predictable.

## 9. Seeded expansion

`expansion/seeded.py`. One entry point.

```python
class Seeded:
    def __init__(self, seed: int, salt: str = "") -> None: ...
    def uuid(self, salt: str) -> UUID: ...       # deterministic, replaces uuid4
    def int(self, lo: int, hi: int) -> int: ...  # inclusive at both ends
    def choice(self, xs: Sequence[T]) -> T: ...
    def shuffled(self, xs: Sequence[T]) -> list[T]: ...
    def timestamp(self, base: datetime, drift_s: int) -> datetime: ...
```

`uuid()` replaces the `id(prefix) -> str` this section listed until M7: ruling R-10 requires an RFC 4122-shaped `UUID` matching the golden fixture's hand-built deterministic id, and the column type is `UUID`, so the return type and the argument are the ruling's rather than this section's. Ruling R-46 shipped it at M5 with the other four deferred to M7, which is where they are.

`salt` is the node id or field path, so two fields expanded from one seed do not correlate.

**The two modes are different on purpose.** `uuid()` is *addressed*: a pure function of `(seed, self.salt, salt)` with no hidden state, because R-10 makes an id re-derivable from its seed and R-46 pins the derivation with a literal test — an id that depended on how many other values had been drawn first would not be re-derivable at all. The four value generators are a *stream*: this section gives them no salt parameter and expansion needs *N* distinct draws, so each draw advances a per-instance counter that is mixed into the digest. Two fresh `Seeded(seed, salt)` instances therefore produce identical sequences while one instance does not repeat itself.

`int()` is the only method that hashes for a value; `choice()`, `shuffled()` and `timestamp()` are written in terms of it, so there is one derivation to pin and one place a bias could hide. It is inclusive at both ends, and it draws by rejection sampling against the largest multiple of the span that fits in 64 bits — `drawn % span` over-represents the first `2**64 % span` values, and a generator whose whole job is reproducible fixtures should not also be quietly skewed. `timestamp()` drifts **forward** by 0 to `drift_s` seconds: a dataset encodes a timeline ordered by `after_node` and read by DS-010, so a drift that could move a derived timestamp *before* its base is the one direction that can make an expanded timeline incoherent; a caller wanting a symmetric jitter of `d` writes `timestamp(base - timedelta(seconds=d), 2 * d)`.

A `hypothesis` property test asserts that identical `(seed, salt)` always yields identical output, and that differing seeds usually diverge — *usually*, because two seeds drawing from a narrow range collide by construction and a test asserting inequality on one draw would be flaky by design. Stability **across processes** is a separate test, with real subprocesses under differing `PYTHONHASHSEED` values, because that is the only way to see it: `hash()` is randomised per process, so a `hash()`-derived generator agrees with itself perfectly for the life of one interpreter.

Nothing else in `src/` may import `random`, `uuid`, or call `datetime.now()` inside a generation or expansion path. Enforce with a ruff custom rule or a test that greps the tree.
