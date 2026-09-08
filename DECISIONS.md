# DECISIONS.md

Append-only build log. One entry per non-obvious choice, in the shape:

```
## [M3] SQLite JSON column type
Chose sqlalchemy.JSON over TEXT with manual serialisation.
Reason: query support on labels without a second table.
Alternative rejected: normalised label table, deferred until label queries are slow.
```

---

## [M0] Build backend: hatchling
Chose `hatchling` as the PEP 517 build backend, with `tool.hatch.build.targets.wheel.packages =
["src/agentprops"]` to pick up the src layout.
Reason: `uv`'s own documentation and scaffolding default to hatchling for src-layout projects with
no compiled extensions; it needs no extra configuration beyond pointing at the package directory.
Alternative rejected: setuptools, which needs more src-layout boilerplate for no benefit here.

## [M0] Dev tools split via PEP 735 dependency-groups, not optional-dependencies
Put `pytest`, `pytest-asyncio`, `ruff`, `mypy` in `[dependency-groups] dev = [...]` rather than
`[project.optional-dependencies]`.
Reason: dependency-groups are not part of the installable package's metadata (they cannot be
`pip install`ed by a consumer, unlike an extra), which matches the fact that dev tooling is a
build-time concern of this repo, not something `agent-props-client` or any downstream consumer of
the service package should ever pull in. `uv sync` includes the `dev` group by default, so
`uv run pytest`/`ruff`/`mypy` work with no extra flags.
Alternative rejected: `[project.optional-dependencies] dev = [...]`, which would advertise "dev" as
an installable extra of the published package.

## [M0] `mypy --strict` scoped to `src`
`[tool.mypy] files = ["src"]`, matching the accepted command `uv run mypy src`.
Reason: `tests/` has no domain code yet (one fixture-parsing test); once tests exercise real models
from M1 onward, they should be typed too, but that is a call for whoever writes those tests, not
one to make now against code that does not exist.
Alternative rejected: including `tests` in `files` now — deferred, revisit at M1.

## [M0] No `[[tool.mypy.overrides]]` added yet
The brief anticipated needing a targeted override for a third-party package missing stubs (e.g.
`pymongo`). None was needed: `mypy --strict src` passes clean because `src/agentprops/` currently
contains only module docstrings — no code imports any dependency yet. Adding a speculative override
for a problem that has not occurred would be exactly the kind of premature configuration the brief
warns against.
Reason: verify before configuring.
Alternative rejected: pre-emptively silencing `pymongo`/`opentelemetry`/etc. import warnings now.
Revisit when M3/M7 actually import these and mypy actually complains.

## [M0] `ruff` scoped away from `docs/`
Added `[tool.ruff] extend-exclude = ["docs", ".superpowers"]`.
Reason: ruff 0.16 formats fenced code blocks inside Markdown by default, and without this exclusion
both `ruff check` and `ruff format --check` walk into `docs/*.md` and want to reformat Python code
fences inside `docs/contracts.md` and `docs/worked-example.md`. Those files are the frozen
specification; `worked-example.md` in particular is the byte-exact source for `tests/fixtures/`, and
must never be rewritten by a tooling pass. `.superpowers/` is build-handoff scratch, already
gitignored, and not part of the Python project either.
Alternative rejected: leaving the default scan scope and hand-fixing the two flagged files, which
would mean a lint/format tool silently editing the spec it is supposed to be validated against.

## [M0] `pytest-asyncio` mode: `auto`
Set `asyncio_mode = "auto"` rather than `"strict"`.
Reason: M4's in-memory MCP client tests and later async storage/tool-contract tests will have many
`async def test_...` functions; `auto` mode treats every async test function as an asyncio test
without a `@pytest.mark.asyncio` decorator on each one, which keeps that suite's boilerplate down.
There is no mixed sync/async-framework risk here (no other event-loop-based test plugin is in use),
so the usual reason to prefer `strict` (avoiding accidental double-marking) does not apply.
Alternative rejected: `strict` mode, requiring an explicit decorator per async test.

## [M0] Only the `integration` marker registered
Registered exactly one pytest marker: `integration`.
Reason: the brief names `uv run pytest -m integration` as a command that must exist, so that marker
must be registered now to avoid `--strict-markers`-style warnings later. The testing strategy
(section 7) describes four layers, but only the storage-conformance and end-to-end layers plainly
need a backend selector; a second marker (e.g. `e2e`) would be speculative today with zero tests to
attach it to, and the M8 milestone that first needs a live end-to-end run is better placed to decide
whether it wants its own marker or reuses `integration`.
Alternative rejected: pre-registering `e2e` and `unit` markers now with no tests using them.

## [M0] CI runs `pytest`, not `pytest -m integration`
`.github/workflows/ci.yml` runs `ruff check`, `ruff format --check`, `mypy src`, `uv run pytest` —
the three commands named in the M0 acceptance criterion, plus the formatter check. It does not also
run `-m integration`.
Reason: M0's acceptance criterion lists exactly those three commands passing "on an empty test
suite". `-m integration` currently selects zero tests, which is pytest exit code 5 (no tests
collected), a false failure signal for a milestone that is supposed to have an empty suite by
design. Wiring `-m integration` into required CI is a later milestone's concern, once there is a
`--store` fixture backing it.
Alternative rejected: running `-m integration` in CI now and accepting exit code 5 as passing (fragile:
indistinguishable from a real regression that deselects every test).

## [M0] `worked-example.md`'s `jsonc` manifest fence needed no stripping
The brief anticipated the `broken/manifest.json` source block might carry comments or trailing
commas (it is fenced ```jsonc``` in `worked-example.md`) and instructed stripping them if present,
noting that fact here. Checked: `sed -n '473,585p' docs/worked-example.md` parses cleanly with
`python3 -m json.tool` with zero edits, and a grep for `//` inside the extracted block found none.
Reason: the `jsonc` fence label describes what the format *could* contain, not what this particular
block *does* contain; it is already strict JSON.
Alternative rejected: none needed — the file was copied byte-for-byte as extracted, no stripping
step was run.

## [M0] `tests/fixtures/datasets/arun-escalated.json` authored as a Priya variant
`docs/worked-example.md` section 5 describes this dataset in prose only (no JSON given). Authored it
by hand as a structural variant of `priya-missing-docs.json`, matching every field Priya's dataset
carries: full provenance (title/intent/author/created_at/supersedes), all five label dimensions
(`persona: new-franchisee`, `scenario: compliance-overdue`, `tier: single-unit`,
`outcome: escalated`, `edge_case: none`), a fixture for every non-pool blueprint node (including
`complete` and `recheck_store`, neither of which this dataset's `expected_path` reaches — mirroring
how Priya's fixture fills `escalate` despite never reaching it), and a `pools.request_docs` entry
even though the loop never runs. `franchisee.existing_locations` is `0`; `store` carries no
`revisions` block (constant throughout, since documents are already complete when the run starts,
so the loop that would otherwise produce a revision never fires). `provenance.author` is
`{name: "Claude Code", handle: "claude-code", agent: "claude-code"}` — a different handle from
Priya's `pnair`, and `agent: "claude-code"` per `AGENTS.md`'s instruction to declare the harness
that actually authored the dataset, since this one was synthesised from the prose spec rather than
transcribed from a given JSON document (unlike the other three fixtures, which are byte-exact
extractions).
Reason: the brief requires this file to exist and to carry every field the M2 validator will check,
since M2 is not built yet and cannot confirm it. Following Priya's structure field-for-field is the
best available guarantee of schema-shape correctness in the meantime.
Alternative rejected: a minimal/sparse variant carrying only the fields section 5's prose explicitly
names — rejected because the brief is explicit that it must carry every field the validator will
require, not just the ones prose called out.

## [M0] `client/python/`, `client/typescript/`, `web/`, `Dockerfile`, `docker-compose.yml` and `alembic.ini` not created at M0
The repo layout diagram in `docs/build-handoff.md` section 3 shows the full phase-1 end state,
including these paths. The M0 task brief's own "Scope: what M0 includes" section restricts the
package-skeleton work to `src/agentprops/` and its subpackages, and explicitly says not to write a
Dockerfile or docker-compose.yml (M7). By the same reasoning, `client/python` and `client/typescript`
are M8 and M11's deliverables respectively, `web/` is M9's, and `alembic.ini`/`storage/migrations/`
are M3's (alembic's own `init` scaffolds that directory; creating an empty shell for it now would
just be a stale placeholder alembic itself would need to overwrite).
Reason: creating these now would either be empty scaffolding nobody asked for at this milestone, or
would force interface decisions (a Dockerfile, a package.json, an alembic.ini pointing at a store
that doesn't exist yet) that belong to the milestone that actually implements them.
Alternative rejected: creating placeholder directories/files for all of them now, matching the full
diagram literally.

## [M0] No `license` field in `pyproject.toml`
Omitted `[project] license`.
Reason: no license is specified anywhere in `docs/` (PRD, build-handoff, contracts, worked-example,
CLAUDE.md, AGENTS.md) or in the repo. Inventing one (e.g. defaulting to MIT because two of the web
dependencies are MIT/ISC-licensed) would be a real decision made on the owner's behalf with no
textual basis.
Alternative rejected: guessing MIT by analogy to the `vanilla-jsoneditor`/`@xyflow/react` licenses
noted in the stack table — those describe *those libraries'* licenses, not this repository's.

## [M1] Protocol types split by aggregate, not gathered into one `storage.py`
`docs/contracts.md` section 6 names six types it never defines. Ruling R-05 supplies the shapes; the
M1 dispatch left the file split to me. Five of the six are a projection of, or a query against,
exactly one aggregate, so they live with that aggregate: `BlueprintSummary` in `blueprint.py`,
`DatasetQuery` and `DatasetSummary` in `dataset.py`, `RunQuery` and `RunSummary` in `run.py`. The
sixth, `StoreHealth`, is the only one that describes the *store* rather than the domain, and it is
the only thing in `models/storage.py`. `Skeleton` and `Section` got their own `models/skeleton.py`.
Reason: a change to `Dataset` and a change to `DatasetSummary` are almost always the same change,
and keeping them in one file means the reviewer sees both. `Skeleton` is a fourth aggregate on the
same footing as blueprint, dataset and run — its own DDL table, its own `SK-*` rules, its own
`service/` module — so following the layout's one-aggregate-per-file convention is more faithful to
its intent than putting a fourth aggregate inside a file named after the storage layer.
Alternative rejected: one `models/storage.py` holding all six. Rejected because it would put
`DatasetSummary`, whose shape contracts 2.2.1 specifies immediately after `Dataset`, in a different
file from `Dataset`, and would name a domain aggregate after a layer it does not belong to.

## [M1] The round-trip criterion is met with `exclude_unset`, plus nullable optionals
Ruling R-08 fixes the criterion as
`Model.model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw`. Two model-side
choices are what make it hold. First, `models/base.py` sets `serialize_by_alias=True` so the
criterion works verbatim with no `by_alias=True` a caller could forget — needed because two wire
names cannot be Python field names (`Edge.from` is a keyword; `EntitySchema.schema` collides with
`BaseModel.schema`, which warns at import and fails `mypy --strict`), so both are carried as
aliases. Second, every optional field is typed `X | None = None` rather than
`X = Field(default_factory=...)`, so an explicitly written `null` parses and survives the trip.
Reason: the fixtures need both halves. Measured on the three golden fixtures, a plain
`model_dump()` reintroduces 11, 21 and 23 nulls that were absent; `exclude_none` instead drops the
five `condition: null` edges and the `supersedes: null` on each dataset. Only `exclude_unset` draws
the distinction the fixtures draw. And the specification's own examples write explicit nulls for
optional scalars (`condition`, `fault`, `max_iterations`, `supersedes`, `finished_at`), so a model
that rejected `null` would reject documents the spec illustrates — which matters most at M5, where
an LLM fills sections and reliably emits explicit nulls.
Alternative rejected: `exclude_defaults`, and non-nullable optionals with container defaults.
`exclude_defaults` would also drop a field explicitly set to its default value, which is a different
and wrong distinction; container defaults would make `"revisions": null` a parse error.

## [M1] `provenance.supersedes` is `str`, while `Dataset.id` and `Skeleton.id` are `UUID`
Ruling R-10 sanctions the `uuid.UUID` type in `models/`, and the DDL types four columns as `UUID`.
Three of them are service-generated (`datasets.id`, `skeletons.id`, `skeletons.submitted_as`) and
are typed `UUID` here. `provenance.supersedes` is the one user-authored id, and it is typed `str`.
Reason: R-04's rule is that a field a catalogue rule owns must not carry a type stricter than the
rule. DS-031 owns `supersedes` and reports a value naming no existing dataset; typing it `UUID`
would make a malformed id raise `ValidationError` instead of firing DS-031, which is precisely the
failure R-04 exists to prevent. No rule owns the well-formedness of `Dataset.id`, so nothing can be
swallowed there. And because DS-031 proves the value names a real dataset before the document
stores, the SQL adapter can parse the `str` into its `UUID` column safely.
Alternative rejected: `UUID` everywhere for symmetry, which trades a rule id for tidiness. Also
rejected: `str` everywhere, which ignores R-10's explicit permission and leaves M3 hand-parsing four
columns.

## [M1] `FaultSpec` is the one model with `extra="allow"`
Every other model sets `extra="forbid"`, which R-04 permits "only where no rule owns it".
Reason: DS-022 owns conformance to the FaultSpec shape, so an unexpected key inside `fault` has to
reach the validator as a rule id rather than raise at parse time. `allow` rather than Pydantic's
default `ignore`, because `ignore` would silently drop the key — hiding it from DS-022 and breaking
the round trip in the same move.
Alternative rejected: `forbid` on `FaultSpec` too, which would have constrained M2's DS-022 corpus
case to an enum violation and never an unknown key.

## [M1] The schema emitter lives at `src/agentprops/schema_export.py`, outside `models/`
`uv run python -m agentprops.schema_export` writes `schemas/blueprint.schema.json` and
`schemas/dataset.schema.json`, and both files are committed. `tests/unit/test_schemas.py` fails if a
committed file differs by one byte from a fresh emission.
Reason: `models/` does no I/O and the emitter writes files, so it cannot live there. Inside the
package rather than in a `scripts/` directory because `pyproject.toml` sets mypy's
`files = ["src"]` — a script outside `src/` would be the one piece of build tooling `mypy --strict`
never checks. It imports only `models/`, so it violates no layering rule.
Alternative rejected: emitting at build time from a hatch hook. Rejected because the web app at M9
needs the files present in a checkout, not only inside a built wheel, and because a generated file
that is not committed cannot be diffed in review.

## [M1] Four shapes the specification describes inline but never names
`NodeExpectation` (`{called, args_match?}`), `BlueprintRef` (`{agent_id, version}`), `StoreCounts`
(`{blueprints, datasets, runs}`) and `PathStep` (`{node_id, iteration, at}`) appear as anonymous
objects in `contracts.md` and are given names here, because `ExpectedOutcome.node_expectations`,
`Dataset.blueprint`, `StoreHealth.counts` and `Run.path` each need a value type. Separately,
`StepRecord` carries `seq` and `fetched_at`, two `run_steps` columns that contracts 2.3's inline
`steps` example elides; both are optional.
Reason: typing them `dict[str, Any]` would give M2 and M3 no shape to code against, which is the one
thing M1 exists to provide. `StepRecord`'s two extra fields are additive refinement of the kind
ruling R-08 explicitly blesses, and adding them now saves M3 reshaping a model it has already
stored.
Alternative rejected: leaving `node_expectations` as `dict[str, dict[str, Any]]` for M2 to type.
Rejected because DS-016 and the client's `subset` comparison both read `called`, so two milestones
would each invent a shape for the same object.

## [M1] `models/base.py`: one `StrictModel` base rather than a repeated `model_config`
Reason: three of the four config settings are load-bearing, for the acceptance criterion
(`serialize_by_alias`) or for R-04 (`extra`), and a model that silently omitted one would still pass
its own tests while breaking the round trip or swallowing a rule id. One base class states the
policy once, with the reasoning next to it, and a new model inherits it by default rather than by
the author remembering.
Alternative rejected: repeating `model_config = ConfigDict(...)` in each of the thirty-odd classes.

## [M1] A layering test, and a test that the whole broken corpus still parses
`tests/unit/test_layering.py` asserts, against the parsed AST of every module in `models/`, that it
imports no sibling layer, calls no clock or random source, does no I/O, and declares no `Field`
constraint, `Literal` annotation or `@field_validator`.
`tests/unit/test_models_accept_broken_documents.py` applies all 28 mutations from
`tests/fixtures/broken/manifest.json` and asserts every mutated document still parses and still
round-trips.
Reason: R-04's cost is paid at M2, not at M1 — a `Literal` added here fails a test two milestones
later, in a suite whose author has no reason to look at `models/`. Both tests move that failure to
the commit that causes it. The corpus test is the strongest available evidence that R-04 was
honoured, because it exercises the exact documents M2's gate depends on. Both were verified by
mutation: injecting a sibling import, a `datetime.now()`, a `uuid.uuid4()`, a `Field(min_length=1)`,
a `Literal[...]` and a `@field_validator` into `models/labels.py` made exactly the three expected
tests fail, and the injection was then reverted.
Alternative rejected: relying on review, which CLAUDE.md names as the enforcement mechanism. Review
is kept as well; the test is cheaper than a review round and does not get tired.

## [M1] AST-based enforcement rather than grep
Ruling R-09 scopes the clock/random ban to `models/`, `validation/` and `expansion/`, and contracts
section 9 suggests a grep. The layering test parses each module with `ast` instead.
Reason: `models/` docstrings discuss `uuid4()`, `datetime.now()` and `min_length` at length,
precisely because those are the things ruled out — a grep-based check would flag its own
documentation, and the natural fix would be to stop documenting the rulings. Ruff has no rule for
"no `Field(min_length=...)` in this package", so a lint-based version would cover only part of it.
Alternative rejected: a ruff banned-API config plus a CI grep. Deferred to whichever milestone wants
this extended to `validation/` and `expansion/`; the AST test generalises by adding a directory to
its table.

## [M1, fix round 1] Every `int` and `bool` field is `StrictInt`/`StrictBool` (ruling R-23)
M1's review showed that `seed: int` swallows DS-020, whose entire check is "`seed` is an integer".
Ruling R-23 settles the underlying question — the validator operates on the **raw document**, the
`dict` off `json.loads`, and a model is constructed only after validation passes — so `seed` stays
typed `int` and DS-020 is implemented against the raw dict at M2 with a real corpus case. What
changed here is the hardening R-23 requires: every `int` and `bool` field in `models/` is now
`StrictInt` or `StrictBool`.
Reason: lax Pydantic *silently rewrites* values. Measured: `seed: "42"` parsed as `42` and
`seed: true` parsed as `1`, both of which then fail R-08's round-trip criterion — a document that
parses and is stored in a form it cannot be re-emitted as, which is worse than either accepting or
rejecting it. Under strict typing `"42"`, `true`, `3.7` and `3.0` all raise `int_type`, and `"no"`
and `0` raise `bool_type`. Coercion can no longer be the reason a stored document stops
round-tripping. The emitted JSON Schemas are byte-identical either way (JSON Schema has no notion of
coercion), so `schemas/` did not change.
Nothing is taken from the catalogue by this. Two consequences for M2, and they are constraints on
the corpus, not on the rules: **BP-008's corpus case must use `0` or `-1`**, never a non-integer
(the "is an integer" half is now enforced at parse time *and* by the catalogue against the raw
document; the "greater than 0" half is the catalogue's alone). And **any BP-017 case must use
`{"op": "replace"}` on `/nodes/*/pool`**, never `{"op": "remove"}` — `pool` is a required field — and
never `0`, which is no longer a boolean. Both are asserted in
`tests/unit/test_models_accept_broken_documents.py` so the constraint is discoverable from code.
Alternative rejected: widening `seed` to `int | str | float | None` so DS-020 could own it alone.
R-23 rejects this explicitly, and it would have spread through every numeric field, given M3 a union
to store and M9 a schema that permits nonsense. Also rejected: `ConfigDict(strict=True)` on
`StrictModel`, which applies to every field — in strict mode a `datetime` field accepts only
`datetime` objects and a `UUID` field only `UUID` objects, so no JSON document would parse at all.

## [M1, fix round 1] The model is the timestamp canonicaliser (ruling R-24)
`Provenance.created_at` accepts several valid ISO 8601 spellings of one instant and canonicalises
them: `…22+00:00` and `…22.000Z` both become `…22Z`, `…22.500Z` and `…22.5Z` both become
`…22.500000Z`, `2026-09-08 10:14:22Z` gains its `T`, and a non-UTC offset such as `+05:30` is
preserved rather than normalised to UTC. Both golden fixtures happen to use the one spelling that
survives unchanged, so nothing failed — which is what made it a latent trap rather than a bug.
R-24 rules that this is correct: "round-trips without loss" means the model's canonical output, not
the author's original bytes.
Reason: recording it now costs a test; discovering it later costs a debugging session with three
backends in play. It lands at M5, where an LLM fills the provenance section and will plausibly emit
fractional seconds or `+00:00` — the document validates and stores, and then `dataset_export` emits
different bytes than were submitted, which is exactly the byte-stability M7's "exported from SQLite,
imported into Postgres" gate depends on. `tests/unit/test_models_roundtrip.py` now pins eight
spellings against their canonical forms, asserts canonicalisation is a **fixed point** (twice equals
once, so a document that has been through the model round-trips byte-for-byte forever after), and
asserts the rewrite never changes *which instant* is denoted.
Alternative rejected: typing the timestamps as `str` to preserve the author's bytes, which R-24
names as the fallback if byte-preservation is ever required. Rejected now because it moves timestamp
comparison into the catalogue and gives M3 a `str` to put in a `TIMESTAMPTZ` column.

## [M1, fix round 1] R-04's coverage is a table keyed by rule id, not an inherited corpus
`tests/unit/test_models_accept_broken_documents.py` previously parametrised solely over
`manifest.json`'s 28 cases. Those reach 7 of the 11 rule ids R-04 names and miss DS-017, DS-020,
BP-002 and BP-008 — which is exactly how the `seed: int` problem stayed hidden. Added
`EXTRA_PARSE_CASES` (hand-written, keyed by rule id, must parse), `STRICT_BY_DESIGN_CASES` (must
raise, per R-23), and `test_ruling_r04_ids_are_all_covered`, which asserts the union of all three
tables covers the eleven.
Reason: the corpus is the wrong place to look for R-04 coverage, and the spec says so — R-14 quotes
`worked-example.md` calling it "a starting set, not the complete one". Deriving a ruling's coverage
from someone else's incomplete input is what failed. A named set of the eleven ids, with an assertion
that every one is exercised, means the next ruling is added to a table rather than discovered
missing two milestones later. `test_the_corpus_alone_does_not_cover_ruling_r04` pins *why* the
hand-written tables exist, and is designed to fail — and be deleted — if R-14's corpus extension
ever does reach all eleven.
Alternative rejected: a named coverage exemption for DS-020, which the review offered and R-23
explicitly declines.

## [M1, fix round 1] `FaultSpec` keeps `extra="allow"`; R-07 was amended to say so
No code change. The review adjudicated in favour of the M1 reasoning and R-07 now carries an
amendment: "no additional properties" describes the shape **DS-022 enforces**, not the model's
config, because under `extra="forbid"` the model would raise before DS-022 could report the unknown
key and no DS-022 corpus case would be writable.
Reason: recorded here because the amendment carries two consequences forward that are easy to lose.
**DS-022 implements the unknown-key check itself** (M2) — it is not inherited from Pydantic. And
**the web app's live schema validation will not flag an unknown fault key** (M9), because
`$defs/FaultSpec` emits `additionalProperties: true` while every other definition emits `false`.

## [M1, fix round 1] The R-04 AST guard also covers constrained types and model-wide config
The guard caught `Field(min_length=...)`, `Literal[...]` and `@field_validator`, but not the routes
that need no `Field()` call: `PositiveInt`, `conint(gt=0)`, `Annotated[int, Ge(0)]`,
`StringConstraints(...)`, `AfterValidator(...)`, or a model-wide `ConfigDict(str_min_length=...)`.
`max_iterations: PositiveInt` is the single most likely future violation of R-04 and would have
passed. Added a `CONSTRAINED_TYPE_NAMES` denylist matched on the bare identifier anywhere in the
module — so the *import* of one of those names is flagged too, which is the earliest possible
warning — plus an allowlist for `ConfigDict` keywords. `StrictInt`/`StrictBool` are deliberately
absent from the denylist: they constrain coercion, not value, and R-23 requires them. Verified by
mutation: all five routes injected into `models/labels.py` were reported by name, then reverted.
The layering guard also missed relative imports: `from ..validation import x` has no `agentprops`
segment to anchor on, so `node.level > 0` is now matched against the forbidden layers directly. Both
relative spellings (`from ..validation import x` and `from .. import validation`) were
mutation-verified.
Reason: a guard that covers one route to a violation and not the four others gives false confidence,
which is worse than no guard, because it is the thing everyone points at when asking whether R-04
still holds.
`tests/unit/test_schemas.py`'s forbidden-keyword set gained `maximum`, `exclusiveMaximum`,
`multipleOf`, `minItems`, `maxItems` and `uniqueItems` for the same reason, on the artefact the web
app actually consumes.

## [M1, fix round 1] Fixture-specific assertions moved out of the parametrised test
`test_dataset_parses_into_the_expected_shape` ran over the `datasets/*.json` glob but asserted
`agent_id == "location-onboarding"`, `len(nodes) == 8` and `"request_docs" in pools`, so a future
fixture for a different blueprint would have failed spuriously — contradicting the suite's own stated
design. The parametrised test now asserts only what is true of any dataset for any blueprint
(`set(pools) & set(nodes) == set()`, and that every `entity@revision` reference resolves against the
declared cast); the counts and ids moved to `test_the_golden_location_onboarding_datasets`, which
names its two fixtures explicitly.
Reason: a table-driven suite whose assertions are not table-driven is a trap for whoever adds the
next fixture, and the failure would look like their fixture being wrong.

---

## Questions for the owner

- **`priya-missing-docs.json`'s `provenance.author.agent` is `"human"` in the committed fixture
  (`docs/worked-example.md` lines 325-455), but the illustrative dataset example in
  `docs/contracts.md` (around line 105) shows the same author (`Priya Nair` / `pnair`) with
  `agent: "claude-code"`.** Not a blocker — `worked-example.md` is the authoritative source for the
  fixture we extract byte-for-byte, and it was extracted verbatim, unedited. Flagging in case
  `contracts.md`'s example was meant to be read literally rather than illustratively; if so the two
  documents disagree about who "authored" the canonical Priya fixture.
- **`arun-escalated.json`'s concrete values are invented, not specified.** Section 5 of
  `worked-example.md` pins `scenario`, `outcome`, `existing_locations`, the compliance-failure
  outcome, `expected_path`, and `expected.final.onboarding_status`. Everything else in the file
  (store/franchisee ids, the training module list, the two compliance findings, latency hints,
  `outstanding_tasks: 2`, the pool's placeholder document type) is my best-effort construction
  matching the blueprint's schemas, not something the brief specifies. Its validity against the real
  rule catalogue is unverifiable until M2's validator exists — recommend running it through the
  validator as one of the first things M2 does, alongside the broken corpus.
- **M1: `arun-escalated.json` round-trips and validates cleanly.** Flagged here because the M0 entry
  above records that it had never been machine-checked. It now parses through `Dataset`, round-trips
  byte-for-byte under R-08's criterion, and validates against the emitted Draft 2020-12 dataset
  schema. That establishes shape only. Its *semantic* validity — whether `recheck_store` belongs in
  a run whose `expected_path` skips it, whether `entities.store` should carry a `revisions` block —
  is still M2's to confirm, and the M0 recommendation stands.
- **M1, ruling R-04 versus the "is present" rule wording.** Four rules state presence as part of the
  check: DS-021 (`narrative` "is present and at least 30 characters"), DS-025, DS-026 and DS-028.
  R-04 says "a required field stays required", so presence is enforced structurally by Pydantic and
  only the content half is a rule. The consequence for M2: a corpus case for one of those four rules
  must mutate the *value* — the shipped cases use `"   "`, `""` and `"testing"` — and must never
  remove the key, because removing it raises `ValidationError` and no rule id is emitted. Not a
  blocker; the shipped corpus already does the right thing, but R-14 extends the corpus and this is
  the trap waiting there.
- **M1, `run_find`'s `model?` parameter.** `contracts.md` section 4 lists it with no type, and the
  `runs.model` column is JSONB holding `{provider, name, version}`. `RunQuery.model` is typed
  `str | None` and documented as matching `model.name`, since the name is the only part worth
  filtering a run list on. If the intent was to filter on provider, or on the whole triple, the type
  changes.
- **M1, `pool` is required on `Node`.** `contracts.md` 2.1 and PRD 5.1 both show it without a `?`,
  so it is required, and a blueprint that omits it gets a shape error rather than BP-017. Defaulting
  it to `false` would turn that into a rule id, which is friendlier; not done because the two
  specifications agree it is a required field and R-08 says build contracts' shapes.
- **M1 fix round 1: rulings R-23 and R-24 close two of the four questions above, and narrow one.**
  Recorded here because the file is append-only and the entries above now read as more open than
  they are. **R-23 closes** the "is present" question for the *numeric* rules: DS-020 and BP-008's
  "is an integer" half are implemented against the raw document, so nothing is swallowed. It also
  answers a question nobody had asked in writing — whether the validator takes a raw `dict` or a
  parsed model — with "the raw `dict`, and the model is built afterwards". **R-24 closes** the
  timestamp half of the round-trip contract: the model canonicalises and that is correct.
  What still stands, unchanged, is the *presence* half for the four string rules — DS-021, DS-025,
  DS-026, DS-028 — where a corpus case must mutate the value rather than remove the key. R-23's
  reasoning does not extend to it, because a *missing* key is not something a raw-document check and
  a model constraint can both hold: whichever runs first owns it, and R-04 assigns presence to the
  model. Two further constraints on M2's corpus follow from the R-23 hardening and are asserted in
  `tests/unit/test_models_accept_broken_documents.py`: BP-008's case must use `0`/`-1`, and BP-017's
  must `replace` `/nodes/*/pool` with `false` rather than `remove` it or set it to `0`.
