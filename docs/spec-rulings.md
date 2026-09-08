# agent-props: ruling register

Corrections and binding interpretations applied to the phase-1 specification.

Revision: 1.0
Date: 2026-09-08
Status: **authoritative.** Where this file and another document disagree, this file wins.

---

## Why this file exists

A pre-flight consistency scan of `prd.md`, `contracts.md`, `build-handoff.md` and
`worked-example.md` found 35 places where the documents disagree with each other or omit a
shape needed to build: 3 blockers, 17 important, 15 minor. Full analysis, with quoted text
and mechanically verified evidence, is in the build workspace at
`.superpowers/sdd/build-handoff/preflight-scan.md`.

`build-handoff.md` section 0 says to surface genuine contradictions rather than resolve
them silently, and section 0 also says not to block: "prefer the interpretation that
upholds the ground rules in section 1, and record the choice". This file is both halves —
each ruling names the conflict, the decision, and what it costs if the decision is wrong.

**The PRD is the binding authority.** Every ruling below resolves toward it. Nothing here
redesigns the product; each one picks between readings the documents already contain, or
supplies a shape they omit.

**For the product owner.** The three blockers are spec bugs, not judgment calls — in each
case a rule-table row is the lone outlier against the PRD, both golden fixtures, and
`contracts.md`'s own worked example. R-01 through R-03 are the ones to read if you read
only three.

---

## Blockers

### R-01 — DS-002 exempts `pool: true` nodes (finding F-01)

**Conflict.** DS-002 says "Every node in the blueprint has an entry in `nodes`". The
blueprint has nine nodes; both golden datasets put eight in `nodes` and the loop node
`request_docs` in `pools`. Taken literally, DS-002 rejects both fixtures that M1 and M2
assert are clean.

**Ruling.** DS-002 is worded wrong; the rule changes, the fixtures do not. Implement as:
*every blueprint node has an entry in `nodes`, except nodes with `pool: true`, whose
fixtures appear in `pools` instead.* PRD 5.2's model comments assign `pool: true` nodes to
`pools`, DS-018 already owns the pool side, and contracts 2.2's own illustrative dataset
does the same thing. A `pool: true` node appearing in **both** `nodes` and `pools` is a
DS-003 error, not silently accepted.

**Cost if wrong.** One rule predicate and its corpus case change. Caught immediately by
M2's "valid fixtures pass clean" gate.

### R-02 — DS-010 means "`after_node` is an ancestor via at least one path" (finding F-02)

**Conflict.** DS-010 reads "A node must not reference a revision whose `after_node` is
downstream of it, **or unreachable from it**". Downstream means a path exists; unreachable
means none does. The union is every possible pair, so a literal implementation rejects
every `entity@revision` reference that can exist — and the golden fixture carries three.

**Ruling.** Error when `after_node` is a descendant of the referencing node (reachable
from it by following edges), or when the referencing node is not reachable from
`after_node` at all. Equivalently: `after_node` must be an ancestor of the referencing
node via **at least one** path. The "every path" reading would reject `assign_training`,
which is reachable from `check_docs` without passing through `request_docs` on the
`documents_complete == true` branch. PRD 5.6 point 4 supports "some path": the author
declares what a run *may* observe, not what holds on every branch.

DS-010 is **skipped** when DS-009 has already reported an unknown revision for the same
reference, so the DS-009 corpus case reports one id rather than two.

**Cost if wrong.** The "every path" reading is stricter; adopting it later invalidates one
reference in the golden fixture and needs an owner decision on the fixture.

### R-03 — repeat-and-warn always wins; RT-E05 is deleted (finding F-03)

**Conflict.** RT-E05 makes "iteration exceeds `max_iterations`" a hard error. PRD 5.2 says
the opposite, with its reasoning stated: "the last entry repeats and the run is flagged
with a `pool_exhausted` warning. **Repeating rather than erroring is deliberate**: an agent
that loops one extra time should not get a hard failure for a reason the developer never
chose." Ground rule 3 says the service never gates. M6's own gate and `worked-example.md`
section 7 step 7 both require a warning, not an error.

**Ruling.** RT-E05 loses and is removed from the runtime code table. `fetch_step` serves
`pools[-1]` with a `pool_exhausted` warning for every iteration index >= `len(pool)`,
unconditionally, and never errors on iteration count. A negative `iteration`, or a
non-zero `iteration` against a `pool: false` node, is a caller error — report it as RT-E02
rather than reviving RT-E05.

**Also.** `worked-example.md` section 7 step 7 puts the `pool_exhausted` boundary in the
wrong place. The golden `request_docs` pool has **2** entries, so exhaustion begins at
iteration **2**, not 3. The M8 end-to-end test asserts `pool_exhausted` from iteration 2
onward.

**Cost if wrong.** If the owner wants a hard cap, it returns as a warning-plus-cap at M6
rather than an error; no storage or model change.

---

## Rulings that bind M1 (domain models)

### R-04 — models are shapes, the catalogue is policy (finding F-07)

**Conflict.** contracts 2.2 says provenance constraints are "enforced by the model **and**
the validator". If M1 encodes them as Pydantic constraints, then `DS-025`, `DS-026`,
`DS-028`, `DS-029`, `DS-030`, `BP-001`, `BP-002`, `BP-008`, `BP-017`, `DS-017` and `DS-020`
raise `pydantic.ValidationError` at parse time. The validator never sees the document, no
rule id is emitted, and M2's gate fails on ten corpus cases. CLAUDE.md's style rule —
"structured errors, never exceptions, for anything a user could cause" — forbids that.

**Ruling.** Models carry fields; the catalogue enforces constraints. Type fields as `str`,
`int`, `bool`, `dict`, `list`. **No** `min_length`, `max_length`, `pattern`, `ge`/`gt`, no
`Literal` enums on `author.agent`, `kind`, `status` or `comparison`, and no cross-field
validator tying `kind: loop` to `pool`/`max_iterations`. Read contracts 2.2 as "the model
carries the field, the validator enforces the constraint".

Structural typing that cannot swallow a rule id is still correct: a required field stays
required, `dict[str, NodeFixture]` stays that, and unknown-key policy is `extra="forbid"`
only where no rule owns it.

**Cost if wrong.** Weaker parse-time guarantees for non-catalogue callers; the JSON Schemas
M1 emits carry shape only, not policy, so the web app's live validation shows shape errors
and the tool surface supplies rule errors. This is the intended division.

### R-05 — the six unspecified Protocol types, and where their shapes come from (finding F-14)

**Conflict.** `contracts.md` section 6 names `Skeleton`, `DatasetQuery`, `RunQuery`,
`RunSummary`, `BlueprintSummary` and `StoreHealth`. None is in M1's model list and none is
defined anywhere. Only `DatasetSummary` has a shape (2.2.1).

**Ruling.** M1 defines all six, each derived from the place that already constrains it:

| Type | Derived from |
|---|---|
| `Skeleton` | the `skeletons` DDL (`id, agent_id, bp_version, manifest, parts, submitted_as, created_at`) plus `manifest: list[Section]` |
| `BlueprintSummary` | `blueprint_list`'s documented return: `{agent_id, version, status, description}` |
| `DatasetQuery` | `dataset_find`'s parameters: `agent_id?, labels?, author?, q?, blueprint_version?, limit?, offset?` |
| `RunQuery` | `run_find`'s parameters: `agent_id?, dataset_id?, run_class?, model?, limit?, offset?` |
| `RunSummary` | the `runs` DDL columns minus `outcome` |
| `StoreHealth` | `store_status`'s return: `{backend, healthy, counts: {blueprints, datasets, runs}}` |

Admin counts in `StoreHealth` **include archived datasets** — it is a store-health number,
not a discovery number, and `find_datasets` remains the thing that hides archives.

**Cost if wrong.** A field name changes in a type no external caller depends on yet.

### R-06 — the skeleton section manifest (findings F-15, F-27)

**Conflict.** `Section` is never defined. Section names disagree: contracts 1's error
example says `nodes.compliance`, `worked-example.md` section 7 says `nodes.core` and
`nodes.branches`. Two required fields have no section: `narrative` and `pools`. And it is
unstated whether re-filling a section for repair violates SK-002.

**Ruling.**

- The manifest is exactly five sections, in this order:
  `provenance`, `entities`, `nodes.core`, `nodes.branches`, `expected`. This is
  `worked-example.md`'s list, it is what the M8 end-to-end test asserts, and it keeps M5's
  "five separate `dataset_fill_part` calls" gate literally true.
- `narrative` belongs to the **`provenance`** section. PRD 5.7 groups title, narrative,
  intent and labels as the four required provenance pieces.
- `pools` belongs to **`nodes.branches`**, alongside the loop node's own fixture.
- `Section = {id: str, required: bool, pointers: list[str], description: str}`.
- SK-002 means *a section may not be filled before every earlier-ordinal section is
  filled.* Re-filling an already-filled section is explicitly allowed — PRD 6 Flow B
  depends on it: "A rejection returns structured errors, scoped to the section that caused
  them, so the LLM repairs one part rather than regenerating everything."
- `contracts.md` section 1's `"section": "nodes.compliance"` example is a slip; the value
  in that example should be `nodes.core`.
- `labels` and `seed` are `dataset_skeleton` **inputs**, not fillable sections.

**Cost if wrong.** Section names are strings in a manifest and in error envelopes; renaming
them later is mechanical but touches M2, M5 and M8 tests at once.

### R-07 — `FaultSpec`, and what DS-004 does when `fault` is set (finding F-11)

**Conflict.** DS-022 requires conformance to "the FaultSpec shape", which is given only as
an example. DS-004 hands a faulted `output` to "the fault shape", which no document
defines. Neither golden fixture sets `fault`, so there is no example to infer from. This is
an outright omission rather than a disagreement.

**Ruling.** `FaultSpec = {kind: "error" | "timeout" | "malformed" (required), code: str |
None, after_ms: int | None}`, no additional properties. Per R-04 the `kind` enum is
enforced by DS-022 in the catalogue, not by a `Literal` on the model.

When `fault` is set, DS-004 **skips `output_schema` validation entirely** and requires
`output` to be absent or an object — reading "the fault shape" as "not the node's success
shape". M2 adds one valid fault fixture to the corpus base and one DS-022 case.

**Owner question.** DS-004's "validated against the fault shape" sentence is the ambiguous
one. If a faulted fixture is meant to carry a *structured error payload* with its own
schema, say so and this ruling changes.

**Cost if wrong.** A stricter fault-output schema can be added later without moving any
field; existing fault fixtures would need an `output` reshape.

### R-08 — contracts' model shapes win, and "round-trips without loss" is defined (finding F-33)

**Conflict.** PRD 5.1's `Blueprint` block omits `status` and `entry_node`; PRD 5.4's `Run`
is flat where contracts 2.3 nests a `pin`. M1 is told "exact shapes in `contracts.md`"
while the PRD is the binding authority. Separately, "round-trips without loss" is undefined
against optional fields, and the golden fixtures omit many — `tool_name` on 3 nodes,
`max_iterations` on 8, `fault` and `latency_hint_ms` on most fixtures, `input` on
`complete`, `escalate` and every pool entry. A plain `model_dump()` reintroduces them as
`null` and byte-comparison fails on both golden fixtures.

**Ruling.** Build contracts' shapes. This is additive refinement, not disagreement: the
PRD's own prose demands both additions — section 6 Flow A requires reachability from an
entry node and immutable published versions, and 5.6 says `run_start` pins
`{dataset_id, dataset_version, blueprint_version}`, which is exactly contracts' `pin`.

The M1 round-trip criterion is:
`Model.model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw`.

**Cost if wrong.** `exclude_unset` also decides whether `dataset_export` emits byte-stable
bundles for M7's cross-backend import test, so a change here reaches M7.

### R-09 — the four legitimate clock sources, and which `created_at` orders `dataset_find` (finding F-18)

**Conflict.** Ground rule 9 bans `datetime.now()` in generation and expansion paths, but
`validated_at` must be stamped by the service, and two different `created_at` values exist
— `provenance.created_at` (authored content) and the `datasets.created_at` column
(`DEFAULT now()`). `DatasetSummary` has one field, and `dataset_find`'s "deterministic
ordering by `(created_at, id)`" does not say which.

**Ruling.** A timestamp may come from exactly four places: a DB column default, the client,
authored content in the document, or `Seeded.timestamp()`. Plus one addition: a single
injected `Clock` port, used **only** in `service/`, for `validated_at` and run timestamps,
and frozen in tests. Ground rule 9's target is generation and expansion — the handoff says
so itself: "It is generation and expansion inside the service that must be seeded." The
grep/ruff enforcement is therefore scoped to `models/`, `validation/` and `expansion/`, not
all of `src/`.

`dataset_find` orders by the **stored column**, and the column is populated from
`provenance.created_at` on insert. One value, reproducible across export/import, and
`DatasetSummary.created_at` gets an unambiguous meaning.

**Cost if wrong.** If the column instead used `now()`, re-import would re-stamp it and
M4's deterministic-ordering gate would be irreproducible — which is the failure this ruling
avoids.

### R-10 — deterministic ids: `Seeded.uuid()` (finding F-19)

**Conflict.** The DDL types dataset and skeleton ids as `UUID`. The only sanctioned
generator, `Seeded.id(prefix)`, returns a prefixed string. And contracts 9's "nothing else
in `src/` may import `random`, `uuid`" literally forbids the `uuid.UUID` type and parser
that the models and SQL adapter need.

**Ruling.** The ban is on **calls** to `uuid.uuid4()`, `random.*` and `datetime.now()` in
generation and expansion paths — handoff section 1 states it that way — not on importing
the modules for typing and parsing. `expansion/seeded.py` gains
`uuid(salt: str) -> uuid.UUID`, deriving an RFC 4122-shaped value from `(seed, salt)`.
Dataset and skeleton ids come from it. This matches the golden fixture's hand-built
deterministic id (`3f8c1a20-0000-4000-8000-000000000001`) and design principle 3, and keeps
export/import idempotent. Run ids remain `uuid4()` in the **client**, which ground rule 9
explicitly permits.

**Cost if wrong.** Ids become non-reproducible across a re-expansion; no schema change.

---

## Rulings that bind M2 (the validator)

### R-11 — `validation/` stays pure via an injected `Resolver` (finding F-06)

**Conflict.** DS-001 (blueprint exists), DS-031 (superseded dataset exists) and BP-016
(published version immutable) are existence checks against the store, but `validation/` is
specified "Pure functions. No I/O." Omitting them from the registry fails the drift test;
implementing DS-001 with no blueprint present fires it on all 20 dataset corpus cases and
breaks every exact-set assertion at once.

**Ruling.** Inject a resolver, not a store. A `Resolver` Protocol with
`get_published_blueprint(agent_id, version)` and `dataset_exists(id)` is passed into
`validate_dataset(...)`. `validation/` imports nothing from `storage/`, so the layering rule
holds literally. M2's corpus stub knows the golden blueprint and knows no dataset ids —
which makes DS-001 pass and DS-031 fire, exactly what the corpus expects.

**Cost if wrong.** Two Protocol methods change signature; the rules stay where they are.

### R-12 — the drift test is scoped to sections 3.1-3.3 and to the milestone's registry (finding F-08)

**Conflict.** `contracts.md` documents 61 rule ids: BP-001..019, DS-001..032, SK-001..005,
RT-E01..E05. RT-E0* are `fetch_step` response codes — no document to validate, no JSON
pointer, no possible corpus fixture. SK-* are not implemented until M5. An equality
assertion at M2 fails by 40 ids.

**Ruling.** `parse_catalogue_ids` reads sections 3.1-3.3 only, matching
`^(BP|DS|SK)-\d{3}$`. The equality test takes the expected prefix set as a parameter, so M2
asserts `{BP-*, DS-*}` and M5 widens it to include `SK-*`. Section 3.4 is a response-code
table and is not part of the rule registry. It reads the catalogue from **`docs/contracts.md`**
(finding F-21: the snippet in `worked-example.md` says `"contracts.md"`, which does not
resolve from the repo root).

**Cost if wrong.** The test's scope is one parameter; widening it later is a one-line change.

### R-13 — warning-severity rules assert `ok is True` plus the warning (finding F-09)

**Conflict.** M2's gate says every corpus case "**is rejected** with exactly the rule ids
its manifest declares". BP-019, DS-007, DS-027 and DS-032 are warnings, and contracts 1 says
"warnings never block a write or a read". `worked-example.md` section 6 says the opposite of
M2: warning cases "must store successfully *and* return the warning".

**Ruling.** `worked-example.md` wins — it is the more specific statement and the only one
compatible with contracts 1. M2's criterion is restated as: *every case in the manifest
produces exactly its declared rule id set, with `ok is False` when any declared rule is
`error`-severity and `ok is True` when all declared rules are warnings.*

BP-019's catalogue wording states the satisfied condition ("A node declares `notes`") rather
than the violation; it means *a node is missing `notes`*.

**Cost if wrong.** The assertion helper changes shape; no rule logic moves.

### R-14 — the corpus is extended to full coverage, and the known-broken cases are fixed (findings F-04, F-05, F-09, F-10)

The corpus that `worked-example.md` ships covers 26 of 61 documented ids and section 6 says
so: "The corpus above is a starting set, not the complete one." M2 extends it to cover every
`BP-*` and `DS-*` rule. Cases known to be wrong as shipped, to fix while extending:

- **`BP-003-duplicate-node`** fires BP-003 plus BP-004, and possibly BP-005 and BP-010 —
  renaming a node orphans every edge that referenced it. Replace with a mutation that
  duplicates an id without breaking edges.
- **`DS-008-constant-entity-drift`** contains a no-op mutation and may fire nothing. DS-008's
  mechanism for locating embedded entity state is undefined; define it as *the value at each
  `entity_refs` site plus `entities.<id>.base`*, and record that.
- **DS-013** ("no label dimension appears twice") is unreachable through any JSON document —
  a JSON object cannot hold a duplicate key. Implement the rule against the parsed
  representation for non-JSON callers and mark it structurally unreachable from the corpus,
  exempting it from the coverage gate with a named exemption rather than a silent gap.
- **Missing warning cases** to add: BP-019 (`remove /nodes/0/notes`), DS-007 (add an
  unreferenced entity), DS-032 (`copy /expected/rationale` to `/provenance/intent`).
- **Rule precedence** must be explicit wherever two rules can fire on one mutation, because
  the gate asserts exact sets: DS-010 is skipped after DS-009 (R-02), and DS-004 is skipped
  when `fault` is set (R-07).

**Cost if wrong.** Corpus cases are declarative and cheap to correct; a wrong exemption
hides a rule with no implementation, which the registry half of the drift test still catches.

---

### R-18 — rule precedence wherever two rules can fire on one document (finding F-30)

M2's gate asserts **exact** rule-id sets, so every overlap must resolve to exactly one id.
Three overlaps exist in the catalogue as written. Rulings, all three verified against real
corpus cases:

| Overlap | Ruling |
|---|---|
| DS-004/DS-005 (fixture `output`/`input` validates) vs DS-019 (each pool fixture validates) | Pool fixtures are **DS-019's** territory. DS-004 and DS-005 cover `nodes` only. |
| DS-006 (`entity_refs` id resolves) vs DS-009 (`@revision` is declared) vs DS-010 | DS-006 checks the **`entity_id` segment only**; DS-009 checks the **`@revision` segment**; DS-010 is skipped when DS-009 fired (already R-02). |
| BP-009 (`entity:` ref resolves) vs BP-011 (schema is valid Draft 2020-12) | BP-011 checks schema syntax **with `entity:` refs stripped**, so an unresolvable entity is **BP-009 alone**. |

Plus the two already ruled elsewhere: DS-004 is skipped entirely when `fault` is set
(R-07), and DS-010 is skipped after DS-009 (R-02).

**Cost if wrong.** A corpus case reports two ids where the manifest declares one; the exact-set
gate catches it immediately.

### R-19 — BP-010 walks the condition tree; conditions are never evaluated (finding F-29)

The condition language is JSONLogic and contracts 2.1 states the check, but the accepted
operator subset, dotted `var` paths, array indices and the `{"var": [path, default]}` form
are all unspecified. Every golden condition uses a flat top-level `var`, so nothing in this
build breaks — the first author who writes `{"var": "store.status"}` hits it.

**Ruling.** Implement BP-010 as: recursively collect every `var` operand anywhere in the
condition tree; accept both the string and `[path, default]` forms; split the path on `.`;
walk `properties` with `entity:` refs resolved; report BP-010 on the first segment that is
not a declared property. Conditions are **structurally walked, never evaluated**, so
JSONLogic is not a dependency and no operator subset needs defining.

`node_expectations[*].args_match` is **deliberately unvalidated** — the service never
evaluates it, and DS-016 checks only the keys of `node_expectations`. Record that rather
than adding a rule.

**Cost if wrong.** A blueprint with a dotted `var` path validates when it should not; no
stored data is affected.

### R-20 — DS-013 keeps its rule and gains a raw-text fixture (finding F-10)

DS-013 ("no label dimension appears twice") cannot be violated by any committed JSON file
or JSON Patch: duplicate object keys collapse at parse time and a Python dict cannot hold
one twice. But the coverage gate demands a case for every registered rule.

**Ruling.** Keep the rule. Give it a raw-text fixture outside the mutation manifest — a
file with a literally duplicated `"persona"` key — and detect duplicates at the **tool
boundary**, where the raw text still exists, via
`json.loads(..., object_pairs_hook=...)`. The MCP boundary genuinely does receive raw text,
so the rule protects something real. The coverage gate gets a named exemption for
manifest-unreachable rules, never a silent gap.

**Cost if wrong.** If the boundary hook proves awkward, retire DS-013 instead — parsing
already provides the protection — and remove it from the registry so the drift test stays
green.

### R-21 — `expected.rationale` gets DS-033 (finding F-28)

contracts 2.2 says of `provenance.intent` and `expected.rationale`: "**Both are required,
neither substitutes for the other.**" But DS-026 covers only `intent`, and no rule requires
`rationale` at all — so `rationale: ""` validates clean, contradicting the prose.

**Ruling.** Add **DS-033**: `expected.rationale` is present and at least 30 characters,
mirroring DS-026. Cheap, symmetric with the existing provenance rules, and consistent with
PRD 5.7's argument for mandatory human-readable justification. Add its corpus case. Adding
the id means the drift test is what catches a missing implementation.

This is the one place the register **adds** a rule rather than reinterpreting one. It is
not a redesign: it makes an existing prose requirement enforceable.

**Cost if wrong.** One rule and one corpus case to remove; both golden fixtures already
carry a substantial `rationale`.

### R-22 — three catalogue and bookkeeping corrections (findings F-21, F-22, F-23)

- The drift test resolves the catalogue path from the test module
  (`Path(__file__).parents[2] / "docs" / "contracts.md"`), not the CWD. As written,
  `parse_catalogue_ids("contracts.md")` resolves to `./contracts.md`, which does not exist,
  so the one test that prevents catalogue drift would die of `FileNotFoundError` instead of
  comparing.
- `build-handoff.md` section 8 says "all eleven milestones"; there are **twelve**, M0..M11.
  Its section 3 layout omits `schemas/` (M1's output, M9's input), `docs/` and `.github/`.
- The warning vocabulary is contracts 3.4's three codes — `blueprint_version_mismatch`,
  `pool_exhausted`, `dataset_archived`. PRD 5.4's `unresolved_step` was **superseded** by
  RT-E01/RT-E02, not dropped by accident; M6's gate is written against contracts.
  `Warning.code` stays an open string so the vocabulary grows without a model change.

**Cost if wrong.** All three are bookkeeping; none affects stored data.

## Rulings that bind later milestones

### R-15 — phase-2 tools required by a phase-1 gate get built (findings F-12, F-13)

`contracts.md` section 4 tags `record_step`, `run_finish`, `run_evidence` and `run_export`
phase 2, but M8's gate needs `record_step`, M10's needs `run_evidence` and `run_export`, and
M8 grades a recorded outcome. PRD 5.6 already blesses run-scoped writes: "`record_step`
writes to the run, never to the world."

**Ruling.** Build them at the milestone whose gate requires them — `record_step` and
`run_finish` at M8, `run_evidence` and `run_export` at M10. The phase-2 tag means "not
required for a working phase-1 runtime", not "must not exist". `run_get` and `run_find` land
at M6 with the run storage they read; `dataset_validate` lands at M4 beside
`blueprint_validate`, which M9 needs for cross-cutting validation.

**Cost if wrong.** Four tools exist earlier than the roadmap implies. Ground rule 1 is
unaffected: none of them writes to a dataset.

### R-16 — the MCP SDK import path (finding F-20)

`build-handoff.md` section 2 says `from mcp import MCPServer`. Verified against the installed
`mcp` 2.2.0: that import **fails**; `MCPServer` is not a top-level export.

**Ruling.** Use `from mcp.server import MCPServer`. Everything else the handoff claims is
verified correct: the `@mcp.tool()` decorator exists, both `run_stdio_async()` and
`run_streamable_http_async()` exist, and `mcp.Client` accepts an `MCPServer` instance
directly, so in-memory tool tests need no subprocess. The handoff is right in substance —
v2 did rename `FastMCP` to `MCPServer` — and wrong only in the path.

**Cost if wrong.** None; the wrong import raises `ImportError` immediately.

### R-17 — the web app writes through the tool surface (finding F-34)

PRD 14.4 still lists "does the web app write, or only read?" as open, while PRD 10.4 and M9
both answer "edit".

**Ruling.** It writes, through the MCP tool surface, producing a new dataset version by
copy-on-write. Both halves of the PRD's own question are satisfied at once, so no design
choice is being made — 14.4 is closed by 10.4.

**Cost if wrong.** None; this is bookkeeping.

---

## Deferred to the milestone that needs them

These findings are real but bind a milestone not yet reached. Each gets a ruling in its
gating dispatch, recorded here at that point: F-16 (the web app's transport to the service,
M9), F-17 (`outcome_schema` missing from the evidence bundle, M10), F-24 (contracts 2.2's
illustrative dataset is not valid against the golden blueprint — documentation only),
F-25 (`dataset_expand` can produce a DS-023 violation, M7), F-26 (M8 step 8's re-fetch must
use `node_id`, M8), F-27 (M5's DS-025 wording and SK-004 precedence, M5), F-31 (`pool: true`
on a non-loop node, M6), F-32 (Langfuse absent from the stack, M10).

## Questions for the owner

None of these block the build; each has a working ruling above.

1. **R-07** — is a faulted fixture's `output` meant to carry a structured error payload with
   its own schema? DS-004's "validated against the fault shape" implies one, and no document
   defines it.
2. **R-03** — is a hard cap on loop iterations wanted at all? RT-E05 was deleted as
   irreconcilable with PRD 5.2's deliberate repeat-and-warn, so nothing currently stops an
   agent looping indefinitely against a pool.
3. **R-02** — should `entity@revision` references hold on *every* path from `entry_node`, or
   on *at least one*? The looser reading was taken so the golden fixture passes.
4. **R-14** — DS-013 is unreachable from a JSON corpus. Keep it as a rule for non-JSON
   callers, or retire it?
