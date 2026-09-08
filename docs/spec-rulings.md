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

**Ruling.** `after_node` must be an ancestor of the referencing node via **at least one**
path. That is: DS-010 fires when the referencing node is **not reachable from
`after_node`**. The "every path" reading would reject `assign_training`, which is reachable
from `check_docs` without passing through `request_docs` on the `documents_complete == true`
branch. PRD 5.6 point 4 supports "some path": the author declares what a run *may* observe,
not what holds on every branch.

**Corrected during M2** (this ruling was wrong as first written). The original text also
said "error when `after_node` is a descendant of the referencing node", and called that
equivalent to the ancestor formulation. **In a cyclic graph the two are not equivalent, and
the golden blueprint is cyclic.** `request_docs → recheck_store → check_docs →
request_docs`: `recheck_store` observes `store@after_docs`, whose `after_node` is
`request_docs` — which is *also* a descendant of `recheck_store` through the loop. So the
descendant clause rejects `priya-missing-docs.json`, the very fixture this ruling exists to
keep passing. The clause is struck; the ancestor formulation above is the rule.

Semantically the ancestor reading is also the right one: `recheck_store` runs after
`request_docs` inside the loop, so the store state after documents were requested is
exactly what it should observe.

Reachability is **strict** — one or more edges. A node referencing a revision whose
`after_node` is *itself* fires DS-010 unless a cycle returns to it, because a revision is
the state produced *by* `after_node`, so that node's own fixture shows the pre-state.

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

**Second amendment, after M2's review — `output` becomes optional on `NodeFixture`.**

This ruling blessed the absent form ("`output` to be absent or an object") but M1's
`NodeFixture` requires `output`. M2 implemented the ruling faithfully, so the two layers now
disagree, and the composite was verified: a faulted fixture with no `output` returns **zero
findings and `ok: True`** from the validator, and then `Dataset.model_validate` raises
`('nodes', 'verify_compliance', 'output') missing`. That is precisely the failure mode
CLAUDE.md forbids — a user-causable condition producing an exception instead of a rule id —
and under R-23's validate-then-parse ordering it lands in `service/` at M4/M5, where an LLM
filling `nodes.core` is the likeliest author of a faulted fixture with no output.

**Ruling.** `output` becomes `dict | None` on `NodeFixture`, and **DS-004 takes over the
presence check**:

- `fault` set → skip `output_schema` validation; `output` may be absent, and must be an
  object if present (as this ruling already said).
- `fault` unset → `output` must be **present** and must validate against `output_schema`.

This is R-04's own principle applied to a case R-04's "required fields stay required"
exception was silently covering: policy belongs in the catalogue, and here a ruling
explicitly blesses an absent value, so the model can no longer be the thing that enforces
presence. Both directions need a corpus case — a clean faulted fixture with no `output`, and
a non-faulted fixture missing `output` reporting DS-004 alone.

**Extended to DS-019, ratified.** The amendment above names DS-004 only, but `NodeFixture`
is also the pool entry's model — so making `output` optional let a pool entry with **no
`output` and no `fault`** parse cleanly while DS-019 still deferred presence to the model,
reopening the same hole one field over. DS-019 therefore carries the identical presence
check, through a shared helper, with its own corpus case. This follows directly from R-18
making DS-019 the sole owner of everything about a pool entry: if it owns the schema check
there, it owns the presence check there too. The alternative — a separate `PoolFixture`
model with `output` required — was correctly judged larger than the round should take
unasked, and would split one concept across two models to avoid one shared predicate.

**Cost if wrong.** Consumers of `NodeFixture.output` must handle `None`, which is correct
anyway: a faulted step genuinely has no output.

**Amendment, after M1's review.** "No additional properties" describes the *shape DS-022
enforces*, not the model's config. `FaultSpec` uses `extra="allow"` so that an unknown key
reaches the validator intact and DS-022 can report it with a rule id — under
`extra="forbid"` the model would raise and DS-022 could never fire, which is the R-04
failure mode again, and no DS-022 corpus case would be writable. Two consequences to carry
forward: **DS-022 implements the unknown-key check itself** (M2), and the web app's live
schema validation will not flag an unknown fault key (M9), because `$defs/FaultSpec` emits
`additionalProperties: true`.

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

### R-23 — validate the raw document, then parse it

Found by M1's review, which showed `seed: int` swallowing DS-020 — DS-020's entire check is
"`seed` is an integer", so with `seed: int` on the model Pydantic owns the check and no rule
id can ever be emitted for a non-integer seed.

The underlying question the documents never answer: does the validator operate on a raw
document or on a parsed model?

**Ruling.** On the **raw document** — the `dict` that comes off `json.loads`. Model
construction happens only *after* validation passes. Three reasons, in order of force:

1. Every error carries an RFC 6901 JSON pointer "into the submitted document"
   (`contracts.md` section 1). A pointer is meaningful against the submitted document, not
   against a model instance that may have coerced, defaulted or dropped fields.
2. It is what makes R-04's ten rule ids reachable at all.
3. Ground rule 7 — "validation is strict at write time and absent at read time" — implies
   the read path parses documents already known good. Validating first and parsing second
   is exactly that ordering.

So `validate_blueprint(doc: dict, ...)` and `validate_dataset(doc: dict, resolver)` take
mappings, not models. `service/` validates, then constructs the model, then stores.

**R-04 still stands** and is not made redundant by this. It is defence in depth: M1's
round-trip test parses fixtures directly, `dataset_validate(dataset: object)` may be handed
an already-parsed object, and any future path that parses before validating would otherwise
swallow rule ids silently.

**Consequently** `seed` stays typed `int` on the model, DS-020 is implemented against the
raw document, and it gets a **real corpus case** (`{"op": "replace", "path": "/seed",
"value": "not-a-number"}`) rather than the named coverage exemption the review offered as an
alternative. Same for BP-008's "is an integer" half.

**One hardening required.** Pydantic's lax coercion means `seed: "42"` parses and is
silently rewritten to `42`, and `seed: true` parses as `1` — a document that parses but
fails the R-08 round-trip criterion, which is worse than either accepting or rejecting it
cleanly. Set `strict=True` on the fields where silent coercion changes the value: `seed`,
`version`, `max_iterations`, `iteration`, `seq`, `after_ms`, `latency_hint_ms` and the
boolean fields. Coercion must never be the reason a stored document stops round-tripping.

**Cost if wrong.** If validation must instead take models, the rule signatures change and
JSON pointers have to be reconstructed from field paths — the expensive direction, which is
why this is ruled now rather than at M2.

### R-24 — the model is the timestamp canonicaliser

Also from M1's review. `Provenance.created_at` accepts four spellings of the same instant
and only the one the golden fixtures happen to use survives the R-08 round-trip:
`2026-09-08T10:14:22Z` round-trips; `…22.500Z` becomes `…22.500000Z`; `…22+00:00` becomes
`…22Z`; `…22.000Z` becomes `…22Z`. All four are valid ISO-8601 and all four parse. Nothing
fails today because both fixtures use the surviving spelling.

**Ruling.** The model **is** the canonicaliser. "Round-trips without loss" means the model's
canonical output, not the author's original bytes. Pin it with a parametrised test over
several timestamp spellings asserting that each parses and that the canonical form is
stable, so the contract is stated rather than derived from a failure later.

**Why now.** This lands at M5 and M7. At M5 an LLM fills the provenance section and will
plausibly emit fractional seconds or `+00:00`; the document validates and stores; then
`dataset_export` emits different bytes than were submitted — which is precisely the
byte-stability M7's "exported from SQLite, imported into Postgres" gate depends on.
Discovering it with three backends in play is expensive; deciding it now is nearly free.

**Cost if wrong.** If byte-preservation of the author's original spelling is ever required,
the timestamp fields become `str` and comparison moves into the catalogue.

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

### R-25 — `contracts.md` stays the catalogue of record, so M2 amends it

R-12's drift test asserts `documented == set(RULE_REGISTRY.keys())`, reading the documented
set from `docs/contracts.md` sections 3.1-3.3. Two earlier rulings change the rule set, so
as things stand that test cannot pass: R-21 **adds** DS-033, which `contracts.md` does not
list, and R-03 **deletes** RT-E05, which it still does.

**Ruling.** `docs/contracts.md` section 3 remains the single catalogue of record, and M2
amends it to match the register rather than teaching the drift test to read two files. M2
makes exactly these edits:

1. Add a **DS-033** row to section 3.2: "`expected.rationale` is present and at least 30
   characters."
2. Delete the **RT-E05** row from section 3.4, per R-03.
3. Add one line under the section 3 preamble stating that **section 3.4 is a response-code
   table, not part of the rule registry** — which R-12 already requires — so the parser's
   scope is documented where the parser's author will look.
4. Correct the `"section": "nodes.compliance"` value in section 1's example envelope to
   `nodes.core`, per R-06.

Each edit carries a parenthetical pointing at the ruling that made it, so the reasoning
stays in this register and the catalogue stays scannable. This register does not become a
second catalogue the drift test has to merge.

**Why amend rather than teach the test.** A drift test whose "documented" set is spread
across two files, one of which is a narrative document, is a test that will itself drift.
The whole point of the drift test is that the prose catalogue and the code cannot diverge —
that only works when there is exactly one prose catalogue.

**Cost if wrong.** Four small edits to a specification document, each individually
revertible, with the register preserving why they were made.

### R-26 — five more precedence overlaps, all ratified as implemented

M2 found five overlaps beyond R-18's three. Each is implemented as a skip inside the
lower-priority rule, so rules stay independent and order-free. All five are **ratified**:

| Overlap | Owner | Why |
|---|---|---|
| BP-005 vs BP-006 when `entry_node` names no node | BP-006 | reachability from a nonexistent node is empty, so BP-005 would report *every* node on top of BP-006's single finding |
| DS-004/DS-005 vs DS-003 on an unknown `nodes` key | DS-003 | there is no schema to validate against |
| DS-010 vs DS-011 when `after_node` names no node | DS-011 | the same shape as R-02's DS-009 skip |
| DS-019 vs DS-018 on a `pools` key that is not a pool node | DS-018 | same reasoning as DS-003 |
| every blueprint-dependent DS rule vs DS-001 | DS-001 | nothing to check a fixture against; the DS-001 case must report one id, not twenty |

Also ratified: **DS-019 skips the schema check on a faulted pool entry**, extending R-07's
DS-004 reasoning. Without it a faulted pool fixture is unauthorable, since R-18 gives pools
to DS-019 and DS-019's text does not mention `fault`.

The general principle behind all of these, worth stating for the rules still to come: when
one rule's violation removes the thing a second rule would check against, the first rule
owns the finding and the second skips.

**Cost if wrong.** Each is a skip condition in one predicate, and the exact-set gate makes
a wrong choice fail loudly rather than silently.

### R-27 — BP-011 also covers entity schemas

M2 found a real hole: BP-011 covers `input_schema` and `output_schema`, BP-012 covers
`outcome_schema`, and R-18 has BP-011 check syntax with `entity:` refs **stripped** — so
`entities[*].schema` is never checked for being valid Draft 2020-12. A blueprint with a
malformed entity schema publishes, and the first symptom is a confusing DS-004 finding at
dataset time, one milestone away from the cause.

**Ruling.** Extend BP-011 rather than adding a rule id: its row becomes "Every
`input_schema`, `output_schema` and entity `schema` is valid Draft 2020-12 JSON Schema."
Extending the existing owner keeps the registry and the corpus stable, and needs no new
coverage case. Amend the `contracts.md` row (this authorises the catalogue edit) and widen
the implementation.

**Cost if wrong.** A blueprint that previously published now fails validation — which is
the point, and it fails at the milestone that can explain why.

### R-28 — DS-019 also covers a pool entry's `input`

Same class of hole. R-18 gives every pool fixture to DS-019, whose text checks only `output`
against `output_schema`; DS-005 covers `nodes` only. So a pool entry's `input` is validated
by nothing. Neither golden pool entry has an `input`, so nothing is broken today — it is
latent, and it will bite the first author who writes one.

**Ruling.** Extend DS-019's row to "Every pool has at least one fixture, and each validates
against the node's `output_schema`, and each `input`, when present, against its
`input_schema`." This preserves R-18's one-owner principle rather than splitting pool
entries between two rules.

**Cost if wrong.** One predicate widens; no id changes.

### R-29 — BP-016 is idempotent for a byte-identical re-publish

`contracts.md` 3.1 says BP-016 rejects "any upsert against an existing published
`{agent_id, version}`", which rejects even a re-publish of the identical document. M2
implemented the literal text and flagged it.

**Ruling.** BP-016 fires only when the submitted document **differs** from the stored
published version. A byte-identical upsert is a no-op success.

Two reasons. M7's `dataset_import` takes a bundle carrying "blueprint version plus
datasets", so re-importing into a store that already holds that version identically would
fail under the literal reading — import would not be idempotent, which defeats the
promotion path the milestone exists to build. And a CI pipeline that publishes on every run
is the normal case, not an abuse.

Immutability is fully preserved: nothing changes, because the documents are identical.
Comparison uses the same canonical form DS-008 uses (`json.dumps(sort_keys=True)`), so key
re-ordering is not a difference.

**Cost if wrong.** Revert to strict rejection — one comparison removed. The `Resolver`
already returns the stored document, so the machinery is in place either way.

### R-30 — `entity_refs` is partly declarative, and the golden fixtures require that

M2 defined DS-008's mechanism as: for every fixture listing entity `E` in `entity_refs`,
walk the node's schemas to each `{"$ref": "entity:E"}`, read the value at the matching
location in `input`/`output`, and compare against `entities.E.base`. Consequence: a fixture
that references an entity **without embedding a copy of its state** contributes no
comparison site and cannot drift.

**Ruling.** Correct as implemented, and **required** by the fixtures. Both golden datasets
depend on it: `verify_compliance` declares the `store` ref but takes a `store_id` and
returns `{compliant, findings}`, embedding no store state. A rule saying "a fixture that
declares an entity ref must embed that entity's state" would reject both golden datasets, so
the looser reading is what the spec's own examples intend.

The residue is real and worth naming: `entity_refs` is partly an authorial assertion — "this
step is about the store" — that no rule can contradict. That is acceptable for a field whose
purpose is documentation and timeline anchoring rather than enforcement.

Ratified alongside it: "byte-identical" in DS-008 means **canonically** identical
(`json.dumps(sort_keys=True)`), so re-ordered keys are not drift, while `1` vs `1.0` and
`true` vs `1` are. Literal bytes would make key order part of the rule; Python's `==` would
let a fixture change JSON type and still pass a rule whose whole subject is identity.

**Cost if wrong.** Adding the stricter rule later invalidates both golden fixtures and needs
an owner decision on them.

### R-31 — BP-006's inbound-edge half gets a named exemption

Every node in the golden blueprint is reachable from `receive_request`, so any mutation
adding an edge *into* the entry node also creates a cycle without a loop node and BP-018
fires alongside BP-006 — breaking the exact-set assertion. The corpus case therefore
exercises BP-006's existence half, and the inbound half is covered by a unit test.

**Ruling.** Accept, with a **named** exemption in the coverage gate, exactly as R-20 handles
DS-013. The principle: a rule half that no mutation of the single base fixture can isolate
gets a unit test plus a named exemption — never a silent gap.

**Corrected after M2's review** (this ruling was wrong in two ways).

First, it claimed "the inbound half is covered by a unit test". **No such test exists.**
`bp_006`'s inbound branch is the only predicate branch in M2's diff with no test of it
*firing*, so a version that never reported the inbound case would pass all 423 tests. A
ruling must not assert coverage without it being verified — the test is now required work,
not a recorded fact.

Second, it said "a second base blueprint would reach it". **No blueprint can.** The reviewer
proved it structurally: any blueprint with an edge into `entry_node` must also have either a
cycle (BP-018) or a predecessor unreachable from the entry (BP-005), so an inbound-edge
mutation always fires a second id and breaks the exact-set assertion. A verified probe
returns `['BP-006', 'BP-007', 'BP-018']`.

So the only available coverage is a **membership-style unit test**
(`assert "BP-006" in rules_for(doc)`) rather than an exact-set corpus case. And the
exemption needs a shape the gate understands: the existing `MUTATION_UNREACHABLE` map is
keyed by whole rule id, so registering `BP-006` there would trip
`test_no_exemption_is_stale` (the rule *does* have a corpus case, for its other half). The
gate needs a **rule-half** exemption entry that names the unit test covering the half, so
deleting that test fails the gate.

**Cost if wrong.** None; this only adds coverage. The prose exemption in `manifest.json`
and `DECISIONS.md` currently protects nothing, which is the defect being fixed.

## Rulings that bind M3 (storage) and the adapters after it

### R-32 — `runs.declared_bp_version` is a real column; the DDL omitted it

M3 found that `contracts.md` section 7's `runs` table has no column for
`declared_blueprint_version`, which section 2.3 gives to `Run`. Dropping it on write loses
the input to the `blueprint_version_mismatch` warning.

**Ruling.** Keep the column, nullable. Ground rule 3 requires mismatches to produce
"warnings attached to the response **and to the stored run**" — and a stored warning whose
cause cannot be reconstructed is not evidence. Deriving it later is impossible: the declared
version is caller input that exists nowhere else. This is the same class of omission as the
six missing Protocol types in R-05 — the DDL is a refinement that lost a field the model
already carried, not a deliberate exclusion. Amend section 7's `runs` table.

**Cost if wrong.** One migration drops a nullable column.

### R-33 — `set_step_actual` joins the Protocol now, not at M8

M3 found that `upsert_step`'s documented strict idempotency — "calling it twice with the
same key is a no-op that returns the existing record" — means it can never write `actual`.
M8's `record_step` needs to attach `actual` to a step that `fetch_step` already recorded.

**Ruling.** Add `set_step_actual(run_id: str, node_id: str, iteration: int, actual: Mapping)
-> StepRecord` to the `Store` Protocol. The two operations are genuinely different and both
contracts are worth keeping intact:

- `upsert_step` records **what was served**. Its idempotency is what makes M6's gate
  possible — "fetching the same step key twice returns byte-identical fixtures and advances
  nothing" — so it must stay a literal no-op on a repeat.
- `set_step_actual` records **what the agent did**. Write-once per key, and it fails if no
  step record exists, because you cannot report an actual for a step that was never served.

Rejected alternatives: making `upsert_step` merge non-null fields (breaks the no-op contract
M6 depends on), and a step-rewriting `put_run` (a whole-aggregate write to change one field,
and it would let a caller rewrite served fixtures, which ground rule 1 forbids in spirit).

**Do it at M3, not M8.** The method belongs to the Protocol, and M7 implements the Protocol
against Postgres and Mongo. Adding it now costs one method and one conformance test; adding
it at M8 costs touching three adapters that were already signed off. Ground rule 1 is
untouched — this writes to a run, never to a dataset.

**Cost if wrong.** One unused Protocol method until M8.

### R-34 — `archived` is a property of the dataset lineage, not of a version

M3 left open whether a dataset version written *after* an archive inherits the flag.

**Ruling.** Archive is a **lineage-level** flag. The Protocol says so already:
`set_archived(dataset_id, archived)` takes an id and no version. So `set_archived` applies to
every row for that id, and `put_dataset` inherits the current state of the lineage rather
than taking the caller's word for it.

This follows from what archive is *for* (PRD 5.6): an archived dataset "disappears from
`dataset_find` but stays servable to any run holding a pin to it". Per-version archiving
would mean a lineage half-hidden from discovery, and `find_datasets` — which returns one row
per lineage — would have no coherent answer.

**Cost if wrong.** If per-version archiving is ever wanted, the column already exists per
row; only the write path changes.

### R-35 — the two unspecified orderings, ratified

Nothing in the spec set gives an ordering for `list_blueprints` or `find_runs`, though
`dataset_find`'s `(created_at, id)` is specified and M4's gate requires "label queries
return deterministic ordering for identical inputs".

**Ruling.** Ratified as M3 chose them: `list_blueprints` orders by `(agent_id, semver)` —
semver-aware, not lexicographic, so `1.10.0` sorts after `1.9.0`; `find_runs` orders by
`(started_at DESC, id)`, newest first, with the id as the tiebreak that makes it total.

Every list-returning Protocol method must have a **total** order, so that identical inputs
give byte-identical output on every backend. A partial order that happens to be stable on
SQLite will diverge on Postgres or Mongo and M7's conformance suite will fail on it. Document
all three orderings in `contracts.md` section 4.

**Cost if wrong.** An ordering change is one `ORDER BY`, but it is a visible API change once
the web app pages through results.

### R-36 — `q` is substring matching, and Postgres must not "improve" it

M3 flagged that swapping the `q` filter to Postgres `to_tsvector` at M7 would change
semantics — stemming is not substring matching — and the conformance suite's `q` assertions
would fail.

**Ruling.** The contract is explicit: "`q` is a substring match over `title` and `intent`"
(`contracts.md` section 4). **Substring semantics are the contract**, so M7's Postgres
adapter uses `ILIKE`, not full-text search, and the conformance suite's assertions stand as
written. M3's `LIKE` implementation is the correct one.

Consequence for the DDL: the `datasets_search` index in section 7 cannot be a plain
`to_tsvector` GIN index and still serve this contract. At M7 it becomes a **`pg_trgm`** GIN
index, which accelerates substring matching, or it is dropped where the extension is
unavailable — the conformance suite asserts identical *results*, never identical query plans,
so dropping it costs speed and nothing else.

**Why not switch to full-text.** M9's gate is that a reviewer can find the Priya dataset by
searching "repeat operator". Stemming would also match "repeated operators", which sounds
like an improvement until two backends disagree about what a query returns — and PRD design
principle 2 ("hold the environment byte-identical") is the whole basis of the product's
drift claim.

**Cost if wrong.** If full-text is genuinely wanted, it is a new named parameter alongside
`q`, not a redefinition of it.

### R-37 — `run_steps` gains `UNIQUE (run_id, seq)`, and the step read order is total

M3's review found a race and **reproduced it** rather than reasoning about it: replaying the
statement sequence `upsert_step` issues, on two interleaved connections against one
file-backed SQLite database, both connections committed `seq = 1`. pysqlite defers `BEGIN`
until the first DML, so the existence check and the `max(seq)` read take no lock; Postgres
under READ COMMITTED behaves the same way. Wrapping the read and the insert in
`engine.begin()` is therefore not the safety the code and `DECISIONS.md` describe, and
unlike the dataset-version race this branch had no test.

**Ruling.** Two changes, both required.

1. Add `UNIQUE (run_id, seq)` to `run_steps` — an addition to `contracts.md` section 7, which
   this ruling authorises. Then allocate `seq` with the same bounded-retry-on-`IntegrityError`
   pattern `put_dataset` already uses for dataset versions, which the review verified is
   correct and which even tests the losing side of the race. The constraint is what makes the
   retry sound; a transaction alone is not.
2. Make the step read order **total**: `order_by(seq, node_id, iteration)`. This is free and
   unconditional, and it holds even if a duplicate `seq` ever reaches the table.

**Why it matters at M6, not now.** `get_run` orders steps by `seq` alone, so duplicate values
make `Run.steps` and the reconstructed `Run.path` non-deterministic. `Run.path` is what step
resolution disambiguates against (`contracts.md` section 5 uses `run.path[-1]` as the head),
so a duplicate `seq` would surface at M6 as intermittently wrong tool-name resolution with no
obvious cause. That is the most expensive shape of bug this build can ship.

**This extends R-35.** The step order is a **fourth** ordering, absent from R-35's list, and
it was the only one not already total. R-35's requirement — every ordering total, so
identical inputs give byte-identical output on every backend — applies to it.

**Cost if wrong.** A unique constraint and a wider `ORDER BY`; both are additive and neither
changes an existing result.

### R-38 — `find_datasets` returns one row per lineage, at its latest version

M3 implemented `find_datasets` as one row per dataset lineage at its latest version, and the
review flagged that this is an implementer choice on a Protocol method M4 and M9 consume.

**Ruling.** Ratified, and it is the only coherent option. `dataset_find`'s specified ordering
is `(created_at, id)`, and under R-09 `created_at` comes from `provenance.created_at` —
authored content, identical across every version of one dataset. So per-version rows would
share both sort keys and the specified ordering could not be total, breaking M4's gate that
"label queries return deterministic ordering for identical inputs".

It is also what the method is *for*. PRD 5.7 and M9 make the dataset list a review surface —
"a stranger has to judge relevance without opening anything" — and a list showing the same
dataset five times, once per edit, is a worse review surface, not a more complete one.
`dataset_get(dataset_id, version)` is how a specific version is reached.

**Cost if wrong.** If version history ever needs to be browsable, it is a new parameter or a
new method, not a redefinition of this one.

### R-39 — five decisions from M3's fix round, ratified

**(a) The `q` filter matches in Python, and that is the correct trade.** Making the case fold
consistent turned out to be larger than it looked: no SQL expression folds case identically
across all three backends — SQLite's `lower()` is ASCII-only, verified — so both the fold and
the substring match moved into Python, and `limit`/`offset` moved with them for `q` queries.
A `q` query therefore materialises every row matching the other filters before slicing.

Ratified. R-36 already decided that identical results across backends beat speed, and this is
that ruling's consequence rather than a new choice. The cost is bounded by the other filters
and by authoring volumes; pagination stays correct because the slice happens after the full
match set exists.

**The named remedy, if it ever matters:** store a pre-folded `title || intent` search column,
computed in Python at write time, and `LIKE` against that in SQL — identical results across
backends *and* `limit`/`offset` back in the query. Datasets are copy-on-write, so it is
computed once per version. **Do not build it now.** The trigger is a measured problem, and
phase 1's non-goals warn specifically against this kind of creep.

**(b) `none_as_null=True` on `JsonDocument`.** Ratified as a genuine bug fix. SQLAlchemy
stores a Python `None` in a JSON column as JSON `null`, so `runs.model`, `runs.outcome` and
`run_steps.actual` held a JSON value where the DDL says `NULL` — found because
`set_step_actual`'s `WHERE actual IS NULL` guard matched nothing. Worth recording as a
general hazard: a JSON column has two distinct empties, and only one of them is `NULL`.

**(c) Amending revision `0001` rather than adding `0002`.** Ratified. This is the initial
schema of an unreleased milestone on an unmerged branch, never applied outside temporary test
databases, and a `0002` would make every future deployment replay a SQLite table rebuild for
a table that never shipped. **Migrations become append-only the moment this branch merges** —
from then on, a schema change is a new revision, never an edit.

**(d) `set_step_actual`'s `recorded_at` from `func.now()`.** Allowed. R-09's first sanctioned
source is a database clock, and the difference between `DEFAULT now()` and an explicit
`func.now()` in a write is mechanical rather than semantic — so R-09 should be read as "a
database clock, whether a column default or an explicit `func.now()`". This does not weaken
R-09's actual target: `datasets.created_at` must come from `provenance.created_at` because it
has to survive an export/import cycle unchanged. A step's `recorded_at` is a runtime
observation, not authored content, so the database clock is the right source. R-33's signature
carries no timestamp, so the column would otherwise be permanently unreachable.

**(e) Two further `contracts.md` edits.** Both blessed. `set_step_actual` belongs in section 6
because section 6 *is* the Protocol of record and R-33 said to add it to the Protocol. The
`{run_id, seq}` unique index belongs in section 8's Mongo list because R-37 must hold on every
backend, not just the SQL ones.

## Rulings that bind M4 (the MCP surface)

### R-40 — R-20's premise was false, and DS-013 survives on an explicit raw parameter

R-20 chose to keep DS-013 over retiring it, and gave one reason: "(a) is preferable because
the MCP boundary genuinely does receive raw text." **M4 proved that premise false for this
SDK.** Raw text is destroyed in three places before any `agentprops` code runs: both
transports parse JSON on receipt, and `MCPServer.pre_parse_json` calls a bare `json.loads`
on any string argument whose annotation is not literally `str`. There is no point in the
request path where a duplicate key survives to be seen.

So the reason R-20 gave for its own decision is gone, and the choice has to be re-made on
what is actually true.

**Ruling.** DS-013 stays, on the mechanism M4 built: an **additive, optional
`dataset_json: str`** parameter on `dataset_validate`, annotated literally `str` so
`pre_parse_json` leaves it intact. DS-013 fires end-to-end over a real MCP round trip. Three
reasons this beats retiring the rule:

1. It is built, tested, and works — retiring would waste it and reopen the registry, corpus
   and exemption bookkeeping R-20 already settled.
2. `dataset_validate` is the tool that explicitly **does not store**. It is a diagnostic
   surface, so an extra diagnostic input on it is coherent rather than a second write path.
3. A rule in the registry that can never fire anywhere is fiction. Either it has an
   enforcement point or it should not be a rule; M4 gave it one.

**The guard that makes this maintainable** is the part worth keeping: a test calls
`pre_parse_json` directly, so if the SDK ever stops mangling string arguments the
workaround's justification fails loudly instead of the parameter quietly becoming cargo.
That is the right shape for any workaround built around third-party behaviour.

**On `dataset_json` being outside the envelope contract.** M4 flagged that a wrong-*typed*
value there produces an SDK error rather than an `AP-001` envelope. Accepted, and it
generalises: the envelope contract covers what our code can see. A request malformed at the
transport or SDK layer never reaches a tool function, so it cannot produce an envelope —
that is true of every tool and every parameter, not a defect of this one. "Structured errors,
never exceptions, for anything a user could cause" binds the code we write.

**Cost if wrong.** If the owner prefers retiring DS-013, removing it is one registry line,
one manifest entry, one exemption entry, `rawjson.py`, and this parameter.

### R-41 — `blueprint_diff` gets a fifth category

The contract names four diff categories — nodes added/removed/changed, edges added/removed,
per-node schema changes, label vocabulary changes. M4 found that leaves
`entities[*].schema`, `entry_node`, `outcome_schema` and `description` unreportable: a
blueprint version could change any of them and the diff would show nothing.

**Ruling.** Add a fifth category covering blueprint-level changes: `entry_node`,
`outcome_schema`, `description`, and entity schemas added, removed or changed.

`blueprint_diff` exists to answer "what changed between these two versions", and R-27 has
just made entity schemas a validated part of a blueprint — so a silent blind spot over them
is worse than the small scope increase. The tool remains informational and never a failure
signal.

**Cost if wrong.** One category is additive; no caller breaks by receiving more detail.

### R-42 — three envelope and paging decisions, ratified

**(a) `AP-004` returns `ok: false` for an unknown id.** Ratified. `contracts.md` section 1
says the envelope covers "a validation **or resolution** failure", so an unknown id is
squarely inside it. "The service never gates" is about **policy** — it must not refuse to
serve because something looked wrong — not about pretending a nonexistent id resolved. A
caller asking for a dataset that does not exist has made an error, and `ok: false` with a
rule id is the honest answer.

**(b) `dataset_find`'s default `limit` of 50.** Ratified. Nothing specifies it, and 50 suits
the review surface M9 builds on it.

**(c) `label_vocabulary` does not page.** Ratified, and it should not. It returns a
blueprint's `label_schema` plus per-value counts, so its size is bounded by the **blueprint**
— the number of dimensions an author declared — not by how much data the store holds. A
paging parameter on a bounded response is complexity with no payoff.

**Cost if wrong.** All three are single-value or single-parameter changes.

### R-43 — the `AP-*` boundary-code family and the one-named-key `data` convention

M4 made two additions to the record that no ruling authorised. Both are well-reasoned and
both are already drift-guarded, but both bind M8's client and M9's web app, so they are
ratified explicitly here rather than left standing as an implementer edit to the spec.

**(a) `contracts.md` section 3.5, the `AP-*` boundary codes.** Ratified, and it fills a gap I
left. R-12 scoped the catalogue-drift test to `{BP-*, DS-*, SK-*}`, which means the boundary
genuinely produces errors that **no registry rule can own** — a malformed argument, an
unknown id, an unparseable document. Section 1 already promises the envelope covers "a
validation **or resolution** failure", so those errors were always in scope; they simply had
no documented home and would otherwise have become ad-hoc strings invented per tool.

A separate table, with a test asserting the codes are **disjoint** from the rule catalogue,
is strictly better than either alternative: minting `DS-*` ids for conditions no dataset rule
describes, or leaving each tool to improvise. R-42(a) ratified `AP-004`'s *behaviour*; this
ratifies the family it belongs to.

**(b) Section 1's one-named-key `data` convention.** Ratified. `SuccessEnvelope.data` is
typed `dict[str, Any]`, so an array payload cannot sit there directly and every success
payload goes under one named key. That is worth being an explicit convention rather than an
accident of the type: M8's client and M9's web app both unwrap `data`, and a surface where
some tools return a bare list and others a keyed object is one both of them would have to
special-case per tool.

**Cost if wrong.** Both are documentation of what the code already does, each with a guard
test. Reversing either is a record edit plus one guard.

### R-44 — the golden fixtures need a drift guard against `worked-example.md`

M4's fix round found that `uv run ruff format tests/ docs` reformatted the fenced Python in
**both** `contracts.md` and `worked-example.md`, because an explicitly named path overrides
`extend-exclude`. It was caught in `git diff` before committing and reverted.

That near-miss exposed a real hole. I verified it: **nothing in the suite pins the committed
fixtures to the document that declares itself their source.** `tests/unit/test_fixtures.py`
asserts only that the four files exist and parse as JSON. The byte-diff against
`worked-example.md` was performed once, by M0's reviewer, as a manual review check — it was
never committed. So the fenced blocks and the fixtures can silently diverge, and the whole
reason those fixtures are trusted is that they are byte-exact copies of the spec's own
worked example.

**Ruling.** Add a drift guard that extracts the fenced blocks and byte-compares them with the
committed fixtures. It covers exactly two files, and the precision matters:

| Fixture | Guarded? |
|---|---|
| `blueprints/location-onboarding-1.0.0.json` | **Yes** — byte-exact against section 3 |
| `datasets/priya-missing-docs.json` | **Yes** — byte-exact against section 4 |
| `broken/manifest.json` | **No** — deliberately extended past section 6's 28 cases by R-14 |
| `datasets/arun-escalated.json` | **No** — authored from section 5's prose; there is no JSON to compare |

The guard must state, at the assertion, why the other two are excluded — otherwise the next
author will either extend it wrongly or delete it as broken.

This is the same reasoning as the catalogue-drift test M2 built: a document that declares
itself the source of truth for committed artifacts needs a test making that true, or it
becomes a document that merely used to be the source of truth. Landed at M5, whose gate
depends on the worked-example dataset being what the spec says it is.

**Also record the tooling rule:** run `ruff format` with **no path argument**, because naming
a path overrides `extend-exclude` and reaches `docs/`.

**Cost if wrong.** A guard that fails when someone deliberately edits the worked example —
which is the intended behaviour, and the fix is to update both sides together.

## Rulings that bind M5 (the skeleton pipeline)

### R-45 — SK-004 owns an unfilled section; the DS-* provenance rules own a filled-but-bad one

This settles finding F-27, deferred from the pre-flight scan: M5's acceptance criteria say a
submit "missing provenance" is rejected with **DS-025**, but SK-004 covers
"`dataset_submit` requires every required section to be filled", and nothing states which
fires.

**Ruling.** They describe two different failures and the distinction is what makes the error
useful:

- The provenance section was **never filled** → **SK-004**. There is no provenance document
  to check, so no `DS-*` provenance rule can have an opinion about its contents. This follows
  R-26's principle directly: when one violation removes the thing a second rule would check
  against, the first owns the finding.
- The provenance section **was filled** but its `title` is absent or blank → **DS-025**.
  Likewise DS-026 for a trivial `intent` and DS-024 for partial labels.

So M5's gate reads: a submit with **no** provenance section fires SK-004; a submit whose
filled provenance carries a trivial intent fires DS-026; one carrying partial labels fires
DS-024. The milestone's three named rule ids are all reachable — they just require the
section to exist first, which the worked example's fill order guarantees.

**SK-004's precedence is total over section contents.** If two required sections are unfilled,
SK-004 reports once per unfilled section and no `DS-*` rule scoped to those sections fires at
all. Otherwise a submit of an empty skeleton would report most of the catalogue.

**Cost if wrong.** If the owner wants DS-025 for a missing section too, it is one predicate —
but the error then says "title is missing" when the truth is "you never filled provenance",
which is worse guidance for the LLM that R-14's repair loop depends on.

### R-46 — `expansion/seeded.py` starts at M5, with `uuid()` only

R-10 requires dataset and skeleton ids to come from `Seeded.uuid()`, but the repo layout
assigns `expansion/` to "M7+" and M7 is where deterministic `dataset_expand` lands. M5 mints
both kinds of id, so it needs the generator before its stated milestone.

**Ruling.** M5 creates `expansion/seeded.py` with the `Seeded` class and **`uuid(salt)`
only**. M7 fills in `int()`, `choice()`, `shuffled()` and `timestamp()` when expansion needs
them. Do not build the rest at M5 — an unused generator method is untested surface, and M7's
`hypothesis` property test is what will establish the determinism guarantees for the others.

The `uuid()` implementation must satisfy R-10: derive an RFC 4122-shaped value from
`(seed, salt)`, matching the shape of the golden fixture's hand-built deterministic id
(`3f8c1a20-0000-4000-8000-000000000001`). Ground rule 9 applies from the first line — no
`uuid4()`, no `random`, no clock inside it.

**Cost if wrong.** If M7 wants a different derivation, ids minted before the change stop
being reproducible from their seed. So pin the derivation with a test asserting a specific
`(seed, salt)` yields a specific UUID, which is the guarantee M7's property test then
generalises.

### R-47 — `mark_skeleton_submitted` becomes a compare-and-set

M5 tested the losing side and reported honestly that **concurrent `dataset_submit` on one
skeleton is not closed**: both callers pass SK-005, both write, and the loser becomes dataset
version 2. Sequential double submit *is* correctly refused and writes nothing.

**Ruling.** Close it now, by making the existing method a CAS rather than adding a new one.
`mark_skeleton_submitted(skeleton_id, dataset_id)` succeeds **only if `submitted_as` is
currently null**, and signals the loser otherwise. `dataset_submit` then orders itself:
validate → CAS → on loss return SK-005 → on win `put_dataset`. The dataset id is available
before the write because R-10 derives it deterministically from `(seed, salt)`.

**Why now and not "when a second writer exists".** This is R-33's argument exactly. M7
implements this Protocol against Postgres and Mongo. A CAS added after M7 costs touching
three adapters that were already signed off; added now it costs one conformance test in a
suite that already runs against every backend. The trigger the implementer named — M9's web
app — arrives *after* M7, which is the worst possible ordering.

**Why it matters more than a duplicate row.** SK-005 exists so a skeleton cannot become two
datasets. A spurious second version is not corruption, but nothing signals it: the losing
caller receives a success envelope for a dataset it did not intend to create, and no rule
fires. An invariant the catalogue asserts and the store cannot hold is worse than no
invariant.

**Cost if wrong.** One method's contract tightens; the sequential path is unchanged.

### R-48 — the `dataset_fill_part` race is accepted for phase 1

M5 also found that concurrent `dataset_fill_part` calls for **different** sections lose one,
because the whole `parts` object is written last-write-wins.

**Ruling.** Accept, and do not add optimistic concurrency for it. The asymmetry with R-47 is
deliberate:

- A lost **fill** is immediately observable and self-correcting. Every `dataset_fill_part`
  response carries `filled` and `remaining`, so the caller is told exactly what is still
  outstanding and re-fills it. R-06 explicitly permits re-filling a section.
- A lost **submit** is silent. The caller gets a success envelope for a dataset it did not
  mean to create, and nothing in the response says so.

The authoring flow this serves is PRD Flow B: one LLM filling sections in manifest order,
one at a time. Concurrent fills of one skeleton are not a use case phase 1 has.

**Cost if wrong.** A `revision` field on the skeleton row plus a retry, later — and unlike
R-47 it does not change a Protocol method's contract, so it is not made expensive by M7.

### R-49 — three smaller M5 decisions

**(a) `skeletons` gains `labels` and `seed` columns.** Ratified, and it was forced rather
than chosen: `dataset_submit(skeleton_id)` takes no other argument, and R-06 makes `labels`
and `seed` non-section **inputs**, so a skeleton that does not carry them cannot be
assembled into a dataset at all. Same class as R-32 — the DDL lost a field the flow requires.
Amend `contracts.md` section 7.

**(b) Id-space exhaustion gets its own `AP-*` code.** The generation walk gives up after 64
salts for one `(agent_id, version, labels, seed)` and currently reports `AP-001` at `/seed`,
which says "bad argument" when the truth is "this seed's id space is full". Add a dedicated
code to section 3.5 — the R-43 family exists precisely for boundary conditions no catalogue
rule owns, and this is one.

**(c) All five sections are `required: true`.** Ratified. A pool-less blueprint therefore
fills `nodes.branches` with `{"pools": {}}` rather than the service inventing a default. An
explicit empty is honest and keeps the five-call gate literally true for every blueprint;
a conditionally-required section would make the manifest depend on the blueprint's shape,
which R-06 deliberately did not do.

**(d) R-44's guard compares through universal newlines.** Ratified — and it must. The
worktree has `worked-example.md` as CRLF while the fixtures are LF, though the index stores
all three as LF, so a raw-byte comparison would fail on every Windows checkout while proving
nothing about drift. The implementer verified the guard still fails on real content drift,
which is the property that matters.

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
