# agent-props: worked example and rejection corpus

The golden fixtures. Commit these at M0 under `tests/fixtures/`.
Companion to `docs/build-handoff.md` and `docs/contracts.md`.

Revision: 1.1
Date: 2026-09-08

---

## 1. Why this example

`location-onboarding` is deliberately chosen to exercise every hard part of the system in one artifact:

| Feature | How this example exercises it |
|---|---|
| Conditional branching | `check_docs` forks on `documents_complete` |
| Loops | `request_docs` is a loop node with a pool, cycling back through `check_docs` |
| Two terminal states | `complete` and `escalate` |
| Entity revisions and timeline validation | `store` changes state mid-run; `franchisee` does not |
| Constant-entity enforcement | `franchisee` has no revisions, so DS-008 requires byte-identity |
| Tool-name disambiguation by position | `fetch_store_profile` and `recheck_store` share `acme.stores.get` |
| Pool draw and exhaustion | The pool has 2 entries against `max_iterations: 3` |
| Unreached branches still filled | `escalate` has a fixture even though `expected_path` never visits it |
| Mandatory provenance | Title, intent, complete labels and a named author, with intent distinct from both narrative and rationale |
| Complete labelling | All five declared dimensions carry a value, including `edge_case: none` |

If the implementation handles this blueprint, it handles the phase 1 feature set.

## 2. The graph

```
receive_request
      |
fetch_store_profile                      (tool: acme.stores.get)
      |
  check_docs  <-------------------+
   /       \                      |
  |         | documents_complete  |
  | false   | == true             |
  v         v                     |
request_docs  assign_training     |
  |  (loop)         |             |
  v                 |             |
recheck_store ------+             |     (tool: acme.stores.get, same name)
  |                               |
  +-------------------------------+
                    |
            verify_compliance
              /            \
      compliant           not compliant
          |                    |
       complete            escalate
```

## 3. `tests/fixtures/blueprints/location-onboarding-1.0.0.json`

```json
{
  "agent_id": "location-onboarding",
  "version": "1.0.0",
  "status": "published",
  "description": "Onboards a new franchise location: intake, document collection, training assignment, compliance verification.",
  "entry_node": "receive_request",

  "entities": [
    {
      "id": "store",
      "schema": {
        "type": "object",
        "required": ["id", "name", "city", "status"],
        "properties": {
          "id":     { "type": "string", "pattern": "^ST-[0-9]{4}$" },
          "name":   { "type": "string" },
          "city":   { "type": "string" },
          "status": { "type": "string",
                      "enum": ["pending_documents", "documents_complete", "active", "blocked"] }
        },
        "additionalProperties": false
      }
    },
    {
      "id": "franchisee",
      "schema": {
        "type": "object",
        "required": ["id", "name", "existing_locations"],
        "properties": {
          "id":                 { "type": "string", "pattern": "^FR-[0-9]{4}$" },
          "name":               { "type": "string" },
          "existing_locations": { "type": "integer", "minimum": 0 }
        },
        "additionalProperties": false
      }
    }
  ],

  "nodes": [
    {
      "id": "receive_request",
      "tool_name": "acme.onboarding.intake",
      "kind": "tool_call",
      "pool": false,
      "notes": "The opening beat. Who is opening what, and where.",
      "input_schema": {
        "type": "object",
        "required": ["request_id"],
        "properties": { "request_id": { "type": "string" } },
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "required": ["store_id", "franchisee"],
        "properties": {
          "store_id":   { "type": "string" },
          "franchisee": { "$ref": "entity:franchisee" }
        },
        "additionalProperties": false
      }
    },
    {
      "id": "fetch_store_profile",
      "tool_name": "acme.stores.get",
      "kind": "tool_call",
      "pool": false,
      "notes": "First read of the store. Status here sets up whether the loop runs.",
      "input_schema": {
        "type": "object",
        "required": ["store_id"],
        "properties": { "store_id": { "type": "string" } },
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "required": ["store"],
        "properties": { "store": { "$ref": "entity:store" } },
        "additionalProperties": false
      }
    },
    {
      "id": "check_docs",
      "kind": "decision",
      "pool": false,
      "notes": "Pure decision. Reads store status, decides whether documents are outstanding.",
      "input_schema": {
        "type": "object",
        "required": ["store"],
        "properties": { "store": { "$ref": "entity:store" } },
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "required": ["documents_complete", "outstanding"],
        "properties": {
          "documents_complete": { "type": "boolean" },
          "outstanding":        { "type": "array", "items": { "type": "string" } }
        },
        "additionalProperties": false
      }
    },
    {
      "id": "request_docs",
      "tool_name": "acme.docs.request",
      "kind": "loop",
      "pool": true,
      "max_iterations": 3,
      "notes": "Each iteration chases one outstanding document. Later iterations should show progress, not repetition.",
      "input_schema": {
        "type": "object",
        "required": ["doc_type"],
        "properties": { "doc_type": { "type": "string" } },
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "required": ["requested", "received"],
        "properties": {
          "requested": { "type": "array", "items": { "type": "string" } },
          "received":  { "type": "array", "items": { "type": "string" } }
        },
        "additionalProperties": false
      }
    },
    {
      "id": "recheck_store",
      "tool_name": "acme.stores.get",
      "kind": "tool_call",
      "pool": false,
      "notes": "Second read of the store, after documents arrive. Same tool as the first read.",
      "input_schema": {
        "type": "object",
        "required": ["store_id"],
        "properties": { "store_id": { "type": "string" } },
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "required": ["store"],
        "properties": { "store": { "$ref": "entity:store" } },
        "additionalProperties": false
      }
    },
    {
      "id": "assign_training",
      "tool_name": "acme.training.assign",
      "kind": "tool_call",
      "pool": false,
      "notes": "Assigns onboarding modules. Count should reflect whether this is a first or repeat location.",
      "input_schema": {
        "type": "object",
        "required": ["store_id", "franchisee_id"],
        "properties": {
          "store_id":      { "type": "string" },
          "franchisee_id": { "type": "string" }
        },
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "required": ["assigned_modules"],
        "properties": {
          "assigned_modules": { "type": "array", "items": { "type": "string" } }
        },
        "additionalProperties": false
      }
    },
    {
      "id": "verify_compliance",
      "tool_name": "acme.compliance.check",
      "kind": "tool_call",
      "pool": false,
      "notes": "The gate before completion. Non-compliance routes to escalation.",
      "input_schema": {
        "type": "object",
        "required": ["store_id"],
        "properties": { "store_id": { "type": "string" } },
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "required": ["compliant", "findings"],
        "properties": {
          "compliant": { "type": "boolean" },
          "findings":  { "type": "array", "items": { "type": "string" } }
        },
        "additionalProperties": false
      }
    },
    {
      "id": "complete",
      "kind": "terminal",
      "pool": false,
      "notes": "Happy ending.",
      "input_schema": { "type": "object", "additionalProperties": true },
      "output_schema": {
        "type": "object",
        "required": ["onboarding_status"],
        "properties": {
          "onboarding_status": { "type": "string", "const": "complete" }
        },
        "additionalProperties": false
      }
    },
    {
      "id": "escalate",
      "kind": "terminal",
      "pool": false,
      "notes": "Compliance failed. A human takes over.",
      "input_schema": { "type": "object", "additionalProperties": true },
      "output_schema": {
        "type": "object",
        "required": ["onboarding_status", "escalated_to"],
        "properties": {
          "onboarding_status": { "type": "string", "const": "escalated" },
          "escalated_to":      { "type": "string" }
        },
        "additionalProperties": false
      }
    }
  ],

  "edges": [
    { "from": "receive_request",    "to": "fetch_store_profile", "condition": null },
    { "from": "fetch_store_profile","to": "check_docs",          "condition": null },
    { "from": "check_docs",         "to": "request_docs",
      "condition": { "==": [ { "var": "documents_complete" }, false ] } },
    { "from": "check_docs",         "to": "assign_training",
      "condition": { "==": [ { "var": "documents_complete" }, true ] } },
    { "from": "request_docs",       "to": "recheck_store",       "condition": null },
    { "from": "recheck_store",      "to": "check_docs",          "condition": null },
    { "from": "assign_training",    "to": "verify_compliance",   "condition": null },
    { "from": "verify_compliance",  "to": "complete",
      "condition": { "==": [ { "var": "compliant" }, true ] } },
    { "from": "verify_compliance",  "to": "escalate",
      "condition": { "==": [ { "var": "compliant" }, false ] } }
  ],

  "label_schema": {
    "dimensions": {
      "persona":   ["new-franchisee", "multi-unit-operator", "corporate-admin"],
      "scenario":  ["happy-path", "missing-documents", "compliance-overdue"],
      "tier":      ["single-unit", "regional", "enterprise"],
      "outcome":   ["success", "partial", "escalated", "failed"],
      "edge_case": ["none", "timezone-boundary", "unicode-names", "empty-history"]
    }
  },

  "outcome_schema": {
    "type": "object",
    "required": ["onboarding_status", "outstanding_tasks"],
    "properties": {
      "onboarding_status": { "type": "string", "enum": ["complete", "escalated"] },
      "outstanding_tasks": { "type": "integer", "minimum": 0 },
      "assigned_modules":  { "type": "array", "items": { "type": "string" } }
    },
    "additionalProperties": false
  }
}
```

## 4. `tests/fixtures/datasets/priya-missing-docs.json`

One loop iteration, ending in success. This is the canonical valid dataset.

```json
{
  "id": "3f8c1a20-0000-4000-8000-000000000001",
  "version": 1,
  "archived": false,
  "blueprint": { "agent_id": "location-onboarding", "version": "1.0.0" },
  "seed": 20260908,

  "provenance": {
    "title": "Multi-unit operator missing one FSSAI licence",
    "intent": "Pins the single-iteration loop path for a franchisee who already operates a location. Training assignment must be the reduced two-module set, not the full onboarding set. Added after the agent assigned all six modules to a repeat operator, which is the behaviour this dataset exists to catch if it regresses.",
    "author": { "name": "Priya Nair", "handle": "pnair", "agent": "human" },
    "created_at": "2026-09-08T10:14:22Z",
    "supersedes": null
  },

  "narrative": "Priya Raman already runs one outlet in Indiranagar and is opening her second in Koramangala. Her intake is clean except for a missing FSSAI licence, so the agent has to chase exactly one document before onboarding can proceed. Once it arrives the store flips to documents_complete, training is assigned at the reduced count appropriate for an operator who has done this before, compliance passes, and onboarding completes with nothing outstanding.",

  "labels": {
    "persona":   "multi-unit-operator",
    "scenario":  "missing-documents",
    "tier":      "regional",
    "outcome":   "success",
    "edge_case": "none"
  },

  "entities": {
    "franchisee": {
      "base": { "id": "FR-2210", "name": "Priya Raman", "existing_locations": 1 }
    },
    "store": {
      "base": {
        "id": "ST-4471", "name": "Koramangala 2", "city": "Bengaluru",
        "status": "pending_documents"
      },
      "revisions": {
        "after_docs": {
          "after_node": "request_docs",
          "state": {
            "id": "ST-4471", "name": "Koramangala 2", "city": "Bengaluru",
            "status": "documents_complete"
          }
        }
      }
    }
  },

  "nodes": {
    "receive_request": {
      "input":  { "request_id": "REQ-88120" },
      "output": {
        "store_id": "ST-4471",
        "franchisee": { "id": "FR-2210", "name": "Priya Raman", "existing_locations": 1 }
      },
      "entity_refs": ["franchisee"],
      "latency_hint_ms": 90
    },
    "fetch_store_profile": {
      "input":  { "store_id": "ST-4471" },
      "output": {
        "store": { "id": "ST-4471", "name": "Koramangala 2", "city": "Bengaluru",
                   "status": "pending_documents" }
      },
      "entity_refs": ["store"],
      "latency_hint_ms": 120
    },
    "check_docs": {
      "input":  { "store": { "id": "ST-4471", "name": "Koramangala 2", "city": "Bengaluru",
                             "status": "pending_documents" } },
      "output": { "documents_complete": false, "outstanding": ["fssai"] },
      "entity_refs": ["store"]
    },
    "recheck_store": {
      "input":  { "store_id": "ST-4471" },
      "output": {
        "store": { "id": "ST-4471", "name": "Koramangala 2", "city": "Bengaluru",
                   "status": "documents_complete" }
      },
      "entity_refs": ["store@after_docs"],
      "latency_hint_ms": 115
    },
    "assign_training": {
      "input":  { "store_id": "ST-4471", "franchisee_id": "FR-2210" },
      "output": { "assigned_modules": ["pos-refresher", "local-compliance-bengaluru"] },
      "entity_refs": ["store@after_docs", "franchisee"],
      "latency_hint_ms": 200
    },
    "verify_compliance": {
      "input":  { "store_id": "ST-4471" },
      "output": { "compliant": true, "findings": [] },
      "entity_refs": ["store@after_docs"],
      "latency_hint_ms": 340
    },
    "complete": {
      "output": { "onboarding_status": "complete" },
      "entity_refs": []
    },
    "escalate": {
      "output": { "onboarding_status": "escalated", "escalated_to": "regional-ops" },
      "entity_refs": []
    }
  },

  "pools": {
    "request_docs": [
      { "output": { "requested": ["fssai"], "received": [] },        "entity_refs": [] },
      { "output": { "requested": ["fssai"], "received": ["fssai"] }, "entity_refs": [] }
    ]
  },

  "expected": {
    "final": {
      "onboarding_status": "complete",
      "outstanding_tasks": 0,
      "assigned_modules": ["pos-refresher", "local-compliance-bengaluru"]
    },
    "expected_path": [
      "receive_request", "fetch_store_profile", "check_docs",
      "request_docs", "recheck_store", "check_docs",
      "assign_training", "verify_compliance", "complete"
    ],
    "node_expectations": {
      "request_docs":  { "called": true,
                         "args_match": { "==": [ { "var": "doc_type" }, "fssai" ] } },
      "escalate":      { "called": false }
    },
    "comparison": "subset",
    "rationale": "One document is outstanding, so the agent must loop through request_docs exactly once, re-read the store, then proceed. Compliance passes, so escalate must never be called."
  },

  "validated_at": "2026-09-08T10:14:22Z"
}
```

**Note three deliberate details.** `escalate` carries a fixture even though `expected_path` never reaches it, because a full-graph blueprint must be playable down any branch. `check_docs` appears twice in `expected_path`, because the loop returns to it. And `provenance.intent` names the regression it guards against, while `expected.rationale` explains why the expected outcome follows from this world: two different questions, two different fields, and DS-027 and DS-032 warn when an author collapses them into one. All three will break a naive implementation, which is exactly why they are in the golden fixture.

## 5. Second valid dataset: `tests/fixtures/datasets/arun-escalated.json`

Build this as a variant with `scenario: compliance-overdue`, `outcome: escalated`, a `franchisee` with `existing_locations: 0`, documents already complete (so the loop never runs), and `verify_compliance` returning `compliant: false` with findings. `expected_path` runs `receive_request, fetch_store_profile, check_docs, assign_training, verify_compliance, escalate`, and `expected.final.onboarding_status` is `escalated`. Give it a different author handle from the Priya dataset, so `dataset_find(author=...)` has something to discriminate.

Its job in the suite is to prove that a dataset which never enters the loop still validates, still fills the loop's pool, and grades correctly against a different terminal node.

## 6. The rejection corpus

**Do not hand-write 45 broken JSON files.** They rot the moment a schema changes. Instead commit a declarative mutation manifest and a small builder that applies each mutation to the golden fixture at test collection time.

`tests/fixtures/broken/manifest.json`:

```jsonc
{
  "base_blueprint": "blueprints/location-onboarding-1.0.0.json",
  "base_dataset":   "datasets/priya-missing-docs.json",
  "cases": [
    { "id": "BP-001-bad-agent-id", "target": "blueprint", "expect": ["BP-001"],
      "mutate": [ { "op": "replace", "path": "/agent_id", "value": "Location_Onboarding" } ] },

    { "id": "BP-003-duplicate-node", "target": "blueprint", "expect": ["BP-003"],
      "mutate": [ { "op": "replace", "path": "/nodes/2/id", "value": "fetch_store_profile" } ] },

    { "id": "BP-005-unreachable", "target": "blueprint", "expect": ["BP-005"],
      "mutate": [ { "op": "remove", "path": "/edges/6" } ] },

    { "id": "BP-009-unknown-entity-ref", "target": "blueprint", "expect": ["BP-009"],
      "mutate": [ { "op": "replace", "path": "/nodes/1/output_schema/properties/store/$ref",
                    "value": "entity:outlet" } ] },

    { "id": "BP-010-condition-unknown-field", "target": "blueprint", "expect": ["BP-010"],
      "mutate": [ { "op": "replace", "path": "/edges/2/condition",
                    "value": { "==": [ { "var": "docs_done" }, false ] } } ] },

    { "id": "BP-014-ambiguous-tool", "target": "blueprint", "expect": ["BP-014"],
      "mutate": [ { "op": "replace", "path": "/nodes/5/tool_name",
                    "value": "acme.docs.request" } ] },

    { "id": "BP-017-loop-without-pool", "target": "blueprint", "expect": ["BP-017"],
      "mutate": [ { "op": "replace", "path": "/nodes/3/pool", "value": false } ] },

    { "id": "BP-018-cycle-without-loop", "target": "blueprint", "expect": ["BP-018"],
      "mutate": [ { "op": "replace", "path": "/nodes/3/kind", "value": "tool_call" },
                  { "op": "replace", "path": "/nodes/3/pool", "value": false } ] },

    { "id": "DS-002-missing-node-fixture", "target": "dataset", "expect": ["DS-002"],
      "mutate": [ { "op": "remove", "path": "/nodes/escalate" } ] },

    { "id": "DS-004-output-violates-schema", "target": "dataset", "expect": ["DS-004"],
      "mutate": [ { "op": "replace", "path": "/nodes/fetch_store_profile/output/store/status",
                    "value": "unknown_state" } ] },

    { "id": "DS-008-constant-entity-drift", "target": "dataset", "expect": ["DS-008"],
      "mutate": [ { "op": "replace",
                    "path": "/nodes/assign_training/output/assigned_modules/0",
                    "value": "pos-refresher" },
                  { "op": "replace", "path": "/nodes/receive_request/output/franchisee/existing_locations",
                    "value": 4 } ] },

    { "id": "DS-009-unknown-revision", "target": "dataset", "expect": ["DS-009"],
      "mutate": [ { "op": "replace", "path": "/nodes/verify_compliance/entity_refs/0",
                    "value": "store@after_training" } ] },

    { "id": "DS-010-revision-from-downstream", "target": "dataset", "expect": ["DS-010"],
      "mutate": [ { "op": "replace", "path": "/nodes/fetch_store_profile/entity_refs/0",
                    "value": "store@after_docs" } ] },

    { "id": "DS-012-label-outside-vocabulary", "target": "dataset", "expect": ["DS-012"],
      "mutate": [ { "op": "replace", "path": "/labels/persona", "value": "master-franchisee" } ] },

    { "id": "DS-014-outcome-violates-schema", "target": "dataset", "expect": ["DS-014"],
      "mutate": [ { "op": "replace", "path": "/expected/final/onboarding_status",
                    "value": "half-done" } ] },

    { "id": "DS-015-invalid-path", "target": "dataset", "expect": ["DS-015"],
      "mutate": [ { "op": "replace", "path": "/expected/expected_path",
                    "value": ["receive_request", "verify_compliance", "complete"] } ] },

    { "id": "DS-018-pool-on-non-pool-node", "target": "dataset", "expect": ["DS-018"],
      "mutate": [ { "op": "add", "path": "/pools/assign_training",
                    "value": [ { "output": { "assigned_modules": [] }, "entity_refs": [] } ] } ] },

    { "id": "DS-023-pool-exceeds-max", "target": "dataset", "expect": ["DS-023"],
      "mutate": [ { "op": "add", "path": "/pools/request_docs/-",
                    "value": { "output": { "requested": [], "received": [] }, "entity_refs": [] } },
                  { "op": "add", "path": "/pools/request_docs/-",
                    "value": { "output": { "requested": [], "received": [] }, "entity_refs": [] } } ] },

    { "id": "DS-024-partial-labels", "target": "dataset", "expect": ["DS-024"],
      "mutate": [ { "op": "remove", "path": "/labels/edge_case" } ] },

    { "id": "DS-025-missing-title", "target": "dataset", "expect": ["DS-025"],
      "mutate": [ { "op": "replace", "path": "/provenance/title", "value": "   " } ] },

    { "id": "DS-026-trivial-intent", "target": "dataset", "expect": ["DS-026"],
      "mutate": [ { "op": "replace", "path": "/provenance/intent", "value": "testing" } ] },

    { "id": "DS-027-intent-equals-narrative", "target": "dataset", "expect": ["DS-027"],
      "note": "warning only, the dataset still stores",
      "mutate": [ { "op": "copy", "from": "/narrative", "path": "/provenance/intent" } ] },

    { "id": "DS-028-no-author-name", "target": "dataset", "expect": ["DS-028"],
      "mutate": [ { "op": "replace", "path": "/provenance/author/name", "value": "" } ] },

    { "id": "DS-029-bad-handle", "target": "dataset", "expect": ["DS-029"],
      "mutate": [ { "op": "replace", "path": "/provenance/author/handle", "value": "Priya Nair" } ] },

    { "id": "DS-030-unknown-agent", "target": "dataset", "expect": ["DS-030"],
      "mutate": [ { "op": "replace", "path": "/provenance/author/agent", "value": "gpt" } ] },

    { "id": "DS-031-dangling-supersedes", "target": "dataset", "expect": ["DS-031"],
      "mutate": [ { "op": "replace", "path": "/provenance/supersedes",
                    "value": "3f8c1a20-0000-4000-8000-00000000dead" } ] },

    { "id": "MULTI-two-errors", "target": "dataset", "expect": ["DS-012", "DS-014"],
      "mutate": [ { "op": "replace", "path": "/labels/scenario", "value": "not-a-scenario" },
                  { "op": "replace", "path": "/expected/final/outstanding_tasks", "value": -3 } ] },

    { "id": "MULTI-provenance-and-world", "target": "dataset", "expect": ["DS-024", "DS-026", "DS-010"],
      "note": "proves errors from three different validators surface in one pass",
      "mutate": [ { "op": "remove", "path": "/labels/tier" },
                  { "op": "replace", "path": "/provenance/intent", "value": "wip" },
                  { "op": "replace", "path": "/nodes/fetch_store_profile/entity_refs/0",
                    "value": "store@after_docs" } ] }
  ]
}
```

**Warning-severity cases need a different assertion.** `DS-027` and `DS-032` are warnings, so the fixture must store successfully *and* return the warning. Assert both: `result.ok is True` and the warning rule id is present. A test that only checks the rule id would pass even if the validator wrongly rejected the dataset.

`mutate` uses RFC 6902 JSON Patch. The builder applies the patch list to a deep copy of the base fixture and hands the result to the validator.

**The corpus above is a starting set, not the complete one.** M2 is not accepted until every rule id in the catalogue in `contracts.md` has at least one case here. The test that enforces this is:

```python
def test_every_rule_has_a_broken_fixture() -> None:
    catalogue = set(RULE_REGISTRY.keys())
    covered = {rule for case in MANIFEST["cases"] for rule in case["expect"]}
    assert catalogue - covered == set(), f"rules with no broken fixture: {catalogue - covered}"

def test_every_rule_has_an_implementation() -> None:
    documented = set(parse_catalogue_ids("contracts.md"))
    assert documented == set(RULE_REGISTRY.keys())
```

The second test is what keeps the prose catalogue and the code from drifting apart. If someone adds a rule to the document without implementing it, or implements one without documenting it, CI fails.

**Assert on exact sets, not membership.** `MULTI-two-errors` exists to prove that the validator reports every problem in one pass rather than stopping at the first. A validator that returns only the first error would pass a membership check and fail this one.

## 7. End-to-end scenario for M8

The integration test the Python client must satisfy:

```
1. Publish location-onboarding 1.0.0.
2. dataset_skeleton(labels={all five dimensions}, seed=20260908)
3. Fill in manifest order: provenance, then entities, then nodes.core, then nodes.branches,
   then expected. Filling entities before provenance must be rejected with SK-002.
4. dataset_submit -> the priya-missing-docs dataset.
5. Client constructs with a generated run_id.
6. run_start(selector={labels: {scenario: missing-documents}}) -> pins the dataset.
7. Walk expected_path, calling fetch_step at each node:
   - Call receive_request by node_id.
   - Call fetch_store_profile by TOOL NAME. Must resolve to fetch_store_profile, not recheck_store.
   - Loop: fetch request_docs iteration 0, then iteration 1.
   - Call recheck_store by TOOL NAME. Must now resolve to recheck_store.
   - Fetch request_docs iteration 2, then 3. Iteration 3 must return the last pool entry
     with a pool_exhausted warning, not an error.
   - Continue to complete.
8. Re-fetch step 2 with the same key. Must return byte-identical output and add no path entry.
9. Grade the recorded outcome against expected.final with the subset helper. Must pass.
```

Step 7's two tool-name calls are the whole point: the same tool name resolving to different nodes depending on where the run is. Step 8 is the idempotency proof.

## 8. What to generate after M8

The PRD's exit criterion is 20 datasets covering the declared label space. Do not hand-write them. Once the skeleton pipeline works, use Claude Code itself as the ingestion LLM: pull a skeleton per label combination, fill it, submit it, and let the validator reject what does not hold together. That loop is the product demonstrating itself, and the rejection messages are the best possible test of whether the error envelope is actually useful to an LLM.
