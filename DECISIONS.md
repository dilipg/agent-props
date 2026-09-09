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

## [M2] `validation/` is eleven modules, not the five the brief names
Built `blueprint.py`, `dataset.py`, `timeline.py`, `registry.py` and `__init__.py` as specified, plus
six helper modules the rules would otherwise duplicate: `resolver.py` (ruling R-11's `Resolver`
Protocol and a `NullResolver`), `context.py` (the read-only document views and the finding factory),
`graph.py` (reachability and cycles, needed by BP-005, BP-018, DS-010 and DS-015), `jsonschemas.py`
(the `entity:` ref convention and every `jsonschema` call), `pointers.py` (RFC 6901 construction and
the pointer-to-section mapping) and `rawjson.py` (ruling R-20's duplicate-key detector).
Reason: four rules need reachability and six need schema resolution. Inlining either into
`blueprint.py` would make `dataset.py` import from `blueprint.py`, which is a dependency between
rule families that has no reason to exist.
Alternative rejected: one large `helpers.py`. The four concerns have nothing to do with each other,
and `graph.py` is the one a future rule is most likely to want.

## [M2] DS-008 locates embedded entity state through the blueprint's `entity:` refs
Ruling R-14 requires DS-008's mechanism to be defined and recorded, since no document says where an
entity's state *is* inside a fixture. Defined as: for every fixture that lists entity `E` in
`entity_refs`, walk the node's `input_schema` and `output_schema` to each
`{"$ref": "entity:E"}` and read the value at the matching location in `input`/`output`; compare each
against `entities.E.base`. `properties`, `additionalProperties`, `items` and `prefixItems` are
followed, because those are the constructs with a deterministic instance counterpart.
Reason: the blueprint is the thing that knows the shape of a fixture, so it is the thing that can
say where a noun lives in one. R-14's phrasing - "the value at each `entity_refs` site plus
`entities.<id>.base`" - needs a definition of "site", and the schema is the only non-guessing one.
Consequence, verified against `arun-escalated.json`: a fixture that references an entity without
embedding a copy of it contributes no site and cannot drift. `verify_compliance` takes only a
`store_id` and returns `{compliant, findings}`, so it names `store` in `entity_refs` and has nowhere
to disagree with it. Both golden datasets depend on that: five of `arun`'s fixtures reference
`store`, and only three embed it.
Alternative rejected: recursively searching each fixture for any object that looks like the entity.
It would fire on coincidence - a `{"id": ...}` that is not a store - and DS-008 is an error, not a
warning.

## [M2] DS-008's "byte-identical" means canonically identical
Compared as `json.dumps(value, sort_keys=True)`, so a re-ordered object is not drift, but `1` versus
`1.0`, and `true` versus `1`, are.
Reason: literal byte identity would make key order part of the rule, which no author could hold to
across a round trip through a `dict`. Python's `==` goes too far the other way: `1 == 1.0 == True`,
so a fixture could switch JSON type and pass a rule whose whole subject is identity.
Alternative rejected: `==` on the parsed values. Cheaper, and silently accepts a type change.

## [M2] DS-010 implements ruling R-02's "equivalently" clause, not its first sentence
R-02 gives two formulations and calls them equivalent: (a) error when `after_node` is a *descendant*
of the referencing node, **or** the referencing node is unreachable from `after_node`; (b)
`after_node` must be an ancestor of the referencing node via at least one path. In a **cyclic** graph
they are not equivalent, and the golden blueprint is cyclic: `request_docs -> recheck_store ->
check_docs -> request_docs`. `recheck_store` observes `store@after_docs`, whose `after_node` is
`request_docs` - which is *also* a descendant of `recheck_store` through the loop. Formulation (a)
therefore rejects the golden fixture; formulation (b) accepts it.
Implemented (b): DS-010 fires when the referencing node is not reachable from `after_node`.
Reason: R-02's ruling text is explicit that the golden fixture must pass, and (b) is the formulation
it states as the rule ("`after_node` must be an ancestor via at least one path"). (a)'s first clause
exists to catch a reference that is *only* downstream, which (b) also catches.
Pinned by `test_ds_010_accepts_a_reference_from_inside_the_loop`, which states the reasoning at the
assertion so nobody reinstates the literal wording.

## [M2] DS-010's reachability excludes the referencing node itself
`Graph.reachable_from(after_node, include_start=False)`: one or more edges, so a node in an acyclic
region does not reach itself, and a node in a cycle does.
Reason: a revision is the state produced *by* `after_node`, so the node whose execution produced it
shows the pre-state in its own fixture. A node referencing its own `after_node` is either an author
error or a re-entry through a loop, and reachability distinguishes those two exactly.
Not covered by the ruling register. No golden fixture or corpus case exercises it; a unit test does.

## [M2] Five precedence decisions the ruling register does not cover
The gate asserts exact rule-id sets, so every overlap must resolve to one owner. R-18 rules three,
R-02 and R-07 one each. Five more surfaced while building the corpus, and each is implemented as a
skip inside the lower-priority rule, so rules stay independent and order-free:

1. **BP-005 is skipped when `entry_node` names no node.** Reachability from a nonexistent node is
   empty, so a literal BP-005 reports *every* node as unreachable on top of BP-006's one finding.
   BP-006 owns the existence half.
2. **DS-004 and DS-005 skip a `nodes` key that is not a blueprint node.** There is no schema to
   validate against; DS-003 owns the unknown key.
3. **DS-010 is skipped when the revision's `after_node` names no node.** DS-011 owns that, exactly as
   R-02 has DS-009 own an unknown revision.
4. **DS-019 skips a `pools` key that is not a `pool: true` node** (DS-018 owns it) **and skips the
   schema check on a faulted pool entry**, extending R-07's DS-004 reasoning. Without the second
   half, a faulted pool fixture would be unauthorable, since R-18 gives pools to DS-019 and DS-019's
   text does not mention `fault`.
5. **Every dataset rule that needs the blueprint is skipped when DS-001 cannot resolve one.** The
   registry marks them `needs_blueprint`; the document-local rules still run. The DS-001 corpus case
   reports one id rather than twenty consequential ones.

Alternative rejected: threading earlier findings into later rules. It makes rule order part of the
contract and turns 52 independent functions into a pipeline.

## [M2] BP-016 answers from the resolver, and `validate_blueprint`'s resolver defaults to `NullResolver`
BP-016 ("a published version cannot be modified") is a property of the store, not of the document, so
it fires when the injected resolver reports an existing published `{agent_id, version}` - byte
identical or not, since contracts 3.1 says "any upsert". `validate_blueprint(doc)` with no resolver
uses `NullResolver`, which knows nothing published, so a first publish and `blueprint_validate` never
fire it. The corpus's BP-016 case is the only one that swaps the resolver.
Reason: with a resolver that knows the golden blueprint, *every* blueprint case would fire BP-016 as
well as its own rule and the exact-set gate would fail 19 times. The golden blueprint itself would
fail "valid fixtures pass clean".
Alternative rejected: BP-016 fires only when the submitted document *differs* from the stored one.
That makes the golden fixture pass with a knowing resolver, but every mutated case still fires it,
and it needs a document comparison nobody specified.

## [M2] The corpus manifest grew four optional per-case keys
`resolver` (which stub to use, for BP-016), `parse_raises` (the mutation is designed not to parse -
DS-020's case, per ruling R-23), `expect: []` (the mutation must validate *clean* - ruling R-07's
fault fixture), and a top-level `raw_text_cases` list (text substitutions rather than JSON Patch, for
DS-013 under ruling R-20).
Reason: three rules cannot be expressed as "patch the golden fixture and expect an error" - one is
about the store, one about the raw bytes, and one is a deliberate skip - and the manifest is the
right place for that to be visible, rather than a special case buried in a test.
Alternative rejected: separate manifests, or hand-written fixture files. Both split the case list,
which is the thing the coverage gate reads.

## [M2] The two corpus cases ruling R-14 named as broken, and what replaced them
- **`BP-003-duplicate-node`** renamed `/nodes/2/id`, which orphaned every edge referencing
  `check_docs` and fired BP-004 and BP-005 too. Replaced with an `add` of a second node claiming the
  id `complete`: the id is duplicated and no edge changes. Verified to report `{BP-003}` alone.
- **`DS-008-constant-entity-drift`** had two operations, the first of which replaced
  `assigned_modules[0]` with the value it already held. Dropped that operation; the case is now the
  single `existing_locations: 1 -> 4` mutation, which is real drift for `franchisee` (an entity with
  no `revisions` block) under the mechanism defined above. Verified to report `{DS-008}` alone.
Also added, per R-14: warning cases for BP-019, DS-007 and DS-032, plus cases for the 22 other rules
the shipped corpus did not reach. 56 cases, 52 rules, one clean case.

## [M2] BP-006's inbound-edge half is unreachable from a mutation of the golden blueprint
Every node in the golden blueprint is reachable from `receive_request`, so *any* added edge into the
entry node also creates a cycle that passes through no loop node, and BP-018 fires alongside BP-006.
The corpus case therefore uses the existence half (`entry_node: "kickoff"`), and the inbound-edge
half is covered by the rule's own code path plus the golden fixture's silence.
Reason: recorded rather than papered over. A second base blueprint would reach it, but a corpus with
two base blueprints costs more than the coverage is worth, and the exact-set gate is what would have
to be weakened otherwise.

## [M2] DS-013 keeps both a named exemption and a real case
The coverage gate carries `MUTATION_UNREACHABLE = {"DS-013": <reason>}`, as ruling R-20 requires, and
a second test asserts that every exempted rule is exercised by a `raw_text_cases` entry - so the
exemption is from the *mutation manifest*, never from testing. A third test fails if an exempted rule
turns out to be reachable by mutation after all.
Reason: R-20 asks for a named exemption rather than a silent gap; a named exemption that also has a
real case is strictly better, and the third test keeps the exemption from outliving its reason.
The detector, `validation/rawjson.py`, is a pure function over a string: the caller reads the text,
so `validation/` still does no I/O. M4 wires it at the tool boundary.

## [M2] `RuleSpec.check` is typed `Callable[[Any], list[RuleError]]`
The blueprint rules take a `BlueprintContext` and the dataset rules a `DatasetContext`; one registry
holds both, with `target` recording the pairing, and the runner builds the right context.
Reason: `mypy --strict` permits explicit `Any`, and the alternatives are worse - a generic registry
parameterised by context type, or two registries that the drift test would have to merge, in which
case `RULE_REGISTRY.keys()` stops being one set and the drift test gets a seam.
Alternative rejected: a `Protocol` with an overloaded `__call__`. Same runtime behaviour, more
machinery, and the rules would still need casting at the call site.

## [M2] `labels`, `seed` and `blueprint` findings carry `section: null`
Ruling R-06 settles that `labels` and `seed` are `dataset_skeleton` *inputs*, not fillable sections,
so an error against one cannot be repaired by re-filling a section. `contracts.md` section 1 allows a
null `section` ("Null for non-skeleton contexts"), and this is that context. Blueprint findings carry
`section: null` always - a blueprint is authored as one document, with no skeleton.
Everything else maps from the pointer: `/provenance` and `/narrative` to `provenance` (R-06 puts
`narrative` in the provenance section), `/entities` to `entities`, `/pools` and a pool node's fixture
to `nodes.branches` (R-06), any other `/nodes` fixture to `nodes.core`, `/expected` to `expected`.

## [M2] `types-jsonschema` added to the dev group rather than a mypy override
`mypy --strict` rejects `import jsonschema` as untyped.
Reason: the M0 decision "No `[[tool.mypy.overrides]]` added yet" says to add one only when a
dependency forces it. Real stubs are better than `ignore_missing_imports` for the one third-party
library the heart of the product calls, and they type-check the `Draft202012Validator` and
`SchemaError` uses rather than erasing them to `Any`.
Alternative rejected: `[[tool.mypy.overrides]] module = "jsonschema.*"`. One line, and it would have
hidden a wrong argument to `iter_errors` for the rest of the build.

## [M2] Two M1 tests changed, one of them deleted by its own instruction
- `test_the_corpus_alone_does_not_cover_ruling_r04` asserted that the corpus missed exactly
  `{BP-002, BP-008, DS-017, DS-020}`. Its docstring said: "If a future corpus extension (R-14) does
  reach all eleven ids, this test fails and should be deleted." R-14's extension does reach them, so
  it is replaced by `test_the_corpus_now_covers_every_ruling_r04_id`, which asserts the inverse. The
  hand-written tables stay: they carry second spellings (a title over 120 characters, `-1` as well as
  `0`) and the must-raise cases the corpus has no reason to duplicate.
- `test_broken_case_still_parses` and `..._round_trips` now skip the `parse_raises` cases, and a new
  `test_parse_raises_case_really_does_raise` asserts the marker is true - so the marker cannot become
  a way to excuse a case from the parsing tests.
- Both M1 tests and the M2 corpus now share one patch applier, in `tests/corpus.py`. M1 duplicated it
  deliberately because no corpus loader existed yet; now that the manifest has per-case keys, two
  appliers would be two readings of one file.

## [M2] `docs/contracts.md` amended, per ruling R-25
Four edits, each with a parenthetical naming its ruling: a DS-033 row in section 3.2 (R-21); the
RT-E05 row deleted from 3.4 (R-03), with a sentence in its place saying why there is no RT-E05; a
line under the section 3 preamble stating that 3.4 is a response-code table and not part of the rule
registry (R-12); and `"section": "nodes.compliance"` corrected to `nodes.core` in section 1's example
(R-06), with the five section names added to the bullet that explains the field.
Reason: R-25 requires the catalogue to stay the single source the drift test reads. The parenthetical
on the deletion is prose rather than a table row, because a deleted row has nothing to annotate.
`docs/worked-example.md` was not touched: it is the byte-exact source for the committed fixtures.

## [M2] Findings are emitted in catalogue order
`RULE_REGISTRY` is a dict in rule-id order, the runner iterates it, and every rule sorts its own
iteration (node ids, entity ids, pool indexes).
Reason: an error list a caller can diff, and a test that can assert on the whole list rather than a
set. A test asserts the registry is sorted, so an appended rule cannot make the order arbitrary.

---

## [M2, fix round 1] `NodeFixture.output` is optional, and DS-004 owns presence (ruling R-07)
R-07 blessed an absent `output` on a faulted fixture while M1's model required the field, so the two
layers disagreed and the composite was worse than either: a faulted fixture with no `output` returned
zero findings and `ok: True`, and then `Dataset.model_validate` raised
`('nodes','verify_compliance','output') missing`. Under R-23's validate-then-parse ordering that
lands in `service/` as an exception where a rule id belongs, which CLAUDE.md forbids.
Chose R-07's second amendment as ruled: `output: dict | None` on `NodeFixture`, and DS-004 checks
presence — absent is legal with `fault` set, required without it.
Reason: where a ruling explicitly blesses an absent value, the model cannot be the thing that
requires it. This is the one place R-04's "a required field stays required" exception had to be
withdrawn, and the reason is exactly R-04's own: policy belongs in the catalogue.
Alternative rejected: keeping the field required and having R-07 forbid the absent form. It would
also have closed the gap, but it contradicts a ruling the owner had already made, and a faulted step
genuinely has no output.
Consequence: `schemas/dataset.schema.json` regenerated — `output` leaves `required` and gains a null
branch, which is what the web app will validate against at M9.

## [M2, fix round 1] DS-019 owns pool-fixture presence too, which R-07's amendment did not name
R-07's second amendment names DS-004. But `NodeFixture` is the model for pool entries as well, so
making `output` optional made a pool entry with no `output` and no `fault` parse cleanly — and
DS-019's presence skip still said "the model owns presence", which had just stopped being true.
Left alone, such a pool entry would validate, store, and be served by `fetch_step` with nothing in
it.
Chose to extend the same rule to pools, through a shared `_output_findings` helper so the two rules
cannot drift, and added a corpus case (`DS-019-pool-entry-without-output`).
Reason: R-18 makes DS-019 the single owner of every pool fixture, and R-26's stated principle is that
the rule which owns the thing owns the finding. Fixing DS-004 alone would have moved the defect
rather than closed it.
Alternative rejected: waiting for a ruling. The hole was created by this round's own model change, so
leaving it would have been shipping a regression and reporting it.

## [M2, fix round 1] BP-011 extended to `entities[*].schema` (ruling R-27)
Verified before the fix: an entity schema of `{"type": "not-a-json-schema-type"}` produced **zero**
blueprint findings, and a dataset validated against that blueprint produced zero as well. After:
`['BP-011']` at `/entities/0/schema`, and the dataset reports `['DS-004', 'DS-005']` if such a
blueprint ever reaches one.
Reason: R-18 has BP-011 strip the very refs that reach an entity, so nobody looked at the entity's
own schema. Extending the existing owner keeps the registry, the drift test and the corpus stable,
which is what R-27 asks for.
The defensive path in `instance_findings` — an unusable schema reported as the owning rule's finding
rather than raised — stays, and now has a test. It is still needed for a blueprint stored before
this round.

## [M2, fix round 1] DS-019 extended to a pool entry's `input` (ruling R-28)
Verified before the fix: `pools.request_docs[0].input = {"missing_docs": 12345}` produced **zero**
findings. R-18 gives every pool fixture to DS-019, whose text named only `output_schema`, and DS-005
covers `nodes` only, so the check fell between two rules.
Chose to widen DS-019 rather than extend DS-005 into `pools`, as R-28 rules.
Reason: one owner per pool fixture is R-18's principle, and splitting a pool entry between two rules
is what would make the exact-set gate ambiguous again.

## [M2, fix round 1] BP-016 is idempotent for a byte-identical re-publish (ruling R-29)
Compares `canonical(stored) == canonical(submitted)` before reporting, using the same canonical form
DS-008 uses, so a re-ordered key is not a modification.
Reason: M7's `dataset_import` carries a blueprint version alongside its datasets, so under the
literal "any upsert" reading re-importing into a store that already holds that version identically
would fail — defeating the promotion path M7 exists to build. Immutability is untouched: nothing
changes, because the documents are identical.
The rule's docstring previously argued the opposite policy at length. It is rewritten, not amended:
leaving the old argument in place next to the new behaviour is how a future reader concludes the code
is wrong.

## [M2, fix round 1] A rule-*half* exemption shape, and the BP-006 test that now exists (ruling R-31)
Two defects, both mine to fix and one of them mine to have caused by reporting it as covered.
`bp_006`'s inbound-edge branch had **no test of it firing** — the only such branch in M2 — so a
version that never reported the inbound case would have passed all 423 tests. And the "exemption"
for it was prose in `manifest.json` and `DECISIONS.md`, which protects nothing.
Chose: a membership-style unit test (`test_bp_006_reports_an_inbound_edge_into_the_entry_node`), plus
`RULE_HALF_EXEMPTIONS: dict[tuple[rule, half], test_name]` in the drift suite, with three gate tests —
the named test must exist (AST-parsed from `tests/unit/`, so deleting it fails the gate), the rule
must be registered, and the rule must still have a corpus case for its other half.
Reason: the exemption has to be keyed by *half*, not by rule. Registering `BP-006` in
`MUTATION_UNREACHABLE`'s whole-rule map would trip `test_no_exemption_is_stale`, because the rule does
have a corpus case — for its existence half. Two maps with different shapes is the honest encoding of
two different situations.
Membership rather than an exact set is forced, not lazy: the reviewer proved structurally that **no**
blueprint can isolate this half, since an edge into `entry_node` implies either a cycle (BP-018) or a
predecessor unreachable from the entry (BP-005). This supersedes the earlier M2 entry, which said "a
second base blueprint would reach it".

## [M2, fix round 1] "Whatever the validator accepts must parse" is now a general gate
`test_a_case_the_validator_accepts_also_parses` runs over every corpus case: if the envelope is
`ok`, the document must construct its model.
Reason: R-07's fault seam was one instance of a class — a clean envelope followed by a
`ValidationError` — and the class is what R-23's validate-then-parse ordering creates. Fixing the
instance without gating the class invites the next one, through some other field, discovered by
whoever builds `service/`.
Alternative rejected: asserting it only for the two `VALID-*` cases. Cheaper, and blind to the
warning-only cases, which also store.

## [M2, fix round 1] Four small hardenings from the review
- `apply_operation` now asserts that a `replace` or `remove` target exists, as RFC 6902 requires.
  Without it, a `replace` whose path had drifted became an `add`: the exact-set gate catches that in
  almost every case, but not one whose violation *is* an extra key, which would pass for the wrong
  reason.
- `run_raw_text_case` asserts `target == "dataset"`, since it always calls `validate_dataset`; a
  blueprint target would have been validated as a dataset in silence.
- The hand-written R-04 tables gained a non-emptiness assertion. `test_ruling_r04_ids_are_all_covered`
  stays green if both are emptied — the corpus now reaches all eleven ids — and their parametrised
  tests would then collect zero cases and pass. What the tables uniquely carry is the second
  spellings: a 400-character title, `max_iterations: -1`, `seed: true`, `pool: 0`.
- `canonical()` falls back to `repr` instead of raising `TypeError`. Unreachable from `json.loads`
  output, but R-23 notes `dataset_validate(dataset: object)` may be handed an already-parsed object,
  and a rule must report rather than raise. `repr` is stable within a process, which is all an
  identity comparison needs.
Also removed as dead surface: `BLUEPRINT_RULE_IDS`, `DATASET_RULE_IDS`, `VALID_CASES` and
`@runtime_checkable` on `Resolver`. All four were exported and consumed nowhere; an unused export is
a claim about a contract nobody is keeping.

## [M2, fix round 1] `entity_ref_sites` stops at the first matching ref, deliberately
Documented rather than changed. The whole value at a matching location *is* the entity's state, so
there is nothing finer to point at — but the consequence is that an entity embedded inside *another*
entity's schema contributes no site of its own. Drift is still caught, because the outer state
contains the inner one; it is reported one level up. Unreachable in the golden fixtures, and now
stated at the code so the next author does not assume the walk descends.

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
- **M2: both golden datasets validate clean, `arun-escalated.json` included.** The M0 and M1 entries
  above record that it had never been machine-checked against any rule. It now passes all 33 `DS-*`
  rules against the golden blueprint with zero findings, and so does `priya-missing-docs.json`. The
  M0 recommendation is discharged: nothing in `arun` needed changing, and no rule was weakened to
  make it pass. Two things worth knowing about *why* it passes: `entities.store` has no `revisions`
  block and is genuinely constant in all three fixtures that embed it, so DS-008 is satisfied rather
  than skipped; and `recheck_store` carries a fixture even though `expected_path` skips it, which is
  correct - DS-002 requires a fixture for every non-pool node, and playing a branch not taken is the
  point.
- **M2: no rule validates an *entity* schema.** BP-011 covers `input_schema` and `output_schema`,
  BP-012 covers `outcome_schema`, and ruling R-18 has BP-011 check syntax with `entity:` refs
  *stripped* - so `entities[*].schema` is never checked for being valid Draft 2020-12. A blueprint
  with a malformed entity schema publishes, and the first thing to notice is DS-004 at dataset time.
  Handled defensively (`instance_findings` catches an unusable schema and reports it as the owning
  rule's finding rather than raising), but the gap is real. The fix is one word in BP-011's row -
  "every `input_schema`, `output_schema` and entity `schema`" - or a new BP rule; both need a
  catalogue edit, so neither was done.
- **M2: pool-entry `input` is unvalidated.** Ruling R-18 gives every pool fixture to DS-019, and
  DS-019's text checks only `output` against `output_schema`. DS-005 covers `nodes` only. So a pool
  entry's `input`, if an author writes one, is checked by nothing. Neither golden pool entry has an
  `input`, so nothing is broken today. Widening DS-019 to cover `input` is a one-line change to the
  rule and a catalogue edit.
- **M2: DS-008 cannot see a fixture that references an entity without embedding it.** With the
  mechanism defined above, `verify_compliance` naming `store` in `entity_refs` while returning only
  `{compliant, findings}` contributes nothing to the identity check - correctly, because there is no
  state there to compare. But it means `entity_refs` is partly *declarative*: the author asserts "this
  step is about the store" and no rule can contradict them. If `entity_refs` was meant to be
  verifiable in both directions, the missing rule is "a fixture that declares an entity ref must
  embed that entity's state", which nothing in the catalogue says.
- **M2: BP-016 rejects a byte-identical re-publish.** contracts 3.1 says "any upsert against an
  existing published `{agent_id, version}` is rejected", and that is what is implemented. An
  idempotent re-publish of identical bytes is therefore an error, which matters for a CI pipeline
  that publishes on every run. If idempotence is wanted, BP-016 needs to compare the submitted
  document with the stored one, and the `Resolver` already returns it.
- **M2: DS-013 remains open as ruling R-14's question 4 put it.** The rule is implemented and has a
  raw-text case, and the detector is a pure function `M4` will call at the tool boundary. If the
  answer is "retire it", the removal is one registry line, one manifest entry, one exemption entry
  and `validation/rawjson.py`.
- **M2: `node_expectations[*].args_match` is unvalidated, per ruling R-19.** Recorded rather than
  fixed: DS-016 checks only the keys. Since `args_match` is JSONLogic against the *arguments the
  agent passed*, and the service never evaluates it, there is no schema to check it against - the
  blueprint has no argument schema separate from `input_schema`. If it should be checked against the
  node's `input_schema`, that is a new rule.
- **M2 fix round 1: five of the seven questions above are closed by rulings R-26 to R-31.** Recorded
  here because the file is append-only and the entries above now read as more open than they are.
  **R-27 closes** the entity-schema gap: BP-011 covers `entities[*].schema`. **R-28 closes** the
  pool-`input` gap: DS-019 covers it. **R-29 closes** the BP-016 idempotence question: a
  byte-identical re-publish is a no-op success. **R-30 closes** the `entity_refs` question by
  ratifying the looser reading — `entity_refs` is partly an authorial assertion, and both golden
  fixtures require that. **R-31 closes** BP-006's coverage question, and corrects itself: the unit
  test it claimed existed did not, no blueprint can isolate the half, and the exemption now names a
  test that a gate checks. **R-26 ratifies** all five precedence decisions plus the DS-019 fault
  skip. What remains open is **DS-013** (keep for non-JSON callers, or retire) and
  **`args_match`**, which R-19 leaves deliberately unvalidated.
- **M2 fix round 1: one new question.** `FaultSpec.after_ms` and `latency_hint_ms` are carried and
  validated but nothing consumes them yet. At M6 `fetch_step` has to decide whether a fault's
  `after_ms` is *simulated* — an actual delay before the error — or purely declarative, like
  `latency_hint_ms`. Ground rule 9's determinism and the ban on wall-clock reads in generation paths
  both point at declarative, but a load-class run (`run_class: "load"`) is the case where a real
  delay would be the point. Not a blocker for M2; the shape is validated either way.

---

## [M3] Dataset versions are allocated by the store, guarded by the primary key
`put_dataset` ignores `ds.version`, reads `max(version)` for that id, and inserts at `max + 1`. The
read happens *outside* the insert's transaction and `PRIMARY KEY (id, version)` is what makes it
safe: two writers that read the same maximum both attempt the same version, one loses on the
constraint, and the loser re-reads and takes the next one. Bounded at eight attempts, and an
`IntegrityError` that is not that race — a missing blueprint tripping `datasets_blueprint_fkey` —
is re-raised immediately rather than retried, so the real cause is not buried.
Reason: monotonic under concurrency with no mechanism that only one backend has. `SELECT ... FOR
UPDATE` does not exist in SQLite, advisory locks do not exist in Mongo, and a serialisable
transaction held across a read and a write is the shape that deadlocks under load. The constraint is
already in the DDL and is already the thing that must hold; leaning on it means there is one
guarantee rather than two that have to agree.
Alternative rejected: a `SELECT max(version) ... FOR UPDATE` inside the insert transaction — correct
on Postgres, unavailable on SQLite, meaningless on Mongo. Also rejected: a separate counter table,
which adds a row that can disagree with the data it counts.
Both branches are tested. `tests/integration/test_sql_version_allocation.py` forces a lost race by
making the first version read return a stale answer, and asserts the retry produces version 2 rather
than a duplicate; a second test asserts a foreign-key violation surfaces as itself.

## [M3] `seq` is allocated by the store; a caller-supplied `seq` is ignored
`run_steps.seq` is `NOT NULL` in the DDL while `StepRecord.seq` is optional — the seam M1 flagged.
`upsert_step` closes it by computing `max(seq) + 1` within the run, starting at 1, and ignoring any
`seq` on the incoming model. The returned `StepRecord` carries the allocated value.
Reason: `seq` is defined as "traversal order, reconstructs the path". It is the store's record of the
order steps were actually served in, not caller data — and the store is the only party that knows
the run's current step count. Honouring a caller-supplied value would give traversal order two
sources of truth and would let a client forge the order it visited nodes in, which is exactly what
"the path is reconstructed, not declared" exists to prevent.
Alternative rejected: honour `step.seq` when present and allocate otherwise. It reads as
accommodating and is the same class of hole as a declared `path`.
Consequence, recorded rather than hidden: a run re-imported step by step is renumbered from 1. Order
is preserved, the original numbers are not. Nothing in phase 1 imports runs (`run_export` at M10 is
export-only), and if run import ever needs the original numbers it needs a bulk path that bypasses
the idempotency check anyway.

## [M3] The label filter and the `q` filter are one portable expression each
Both are SQLAlchemy Core expressions with no dialect branch, and both are the fallback
`contracts.md` section 7 prescribes for SQLite:
- **labels**: one equality per dimension, `datasets.c.labels[dimension].as_string() == value`. That
  compiles to `JSON_EXTRACT(labels, '$."tier"')` on SQLite and `labels ->> 'tier'` on Postgres from
  the same Python, because the column type is `JSON().with_variant(postgresql.JSONB(), "postgresql")`
  and the variant carries the operator through.
- **`q`**: `LOWER(title) LIKE '%term%' OR LOWER(intent) LIKE '%term%'`, with `autoescape` so a `%`
  or `_` in the search term is a literal.
Reason: it survives Postgres because it is already correct on Postgres. `datasets_labels_gin` and
`datasets_search` are *optimisations* of these two expressions, not replacements for them — the GIN
index on `labels` accelerates `->>` without the query changing, and the `q` filter on Postgres is a
correct sequential scan until someone decides the volume justifies swapping in `to_tsvector`. That
swap is one expression at one call site (`_apply_dataset_filters`), and the conformance suite holds
both implementations to the same rows, because it asserts results and never plans.
Alternative rejected: a normalised `dataset_labels` table, which would make the label filter a join
on every backend and would need its own copy-on-write story per dataset version. Deferred until
label queries are slow, exactly as the M3 brief's example entry suggests. Also rejected: a
`_backend == "postgres"` branch at M3 for indexes that do not exist yet — speculative code in the
one place the milestone is trying to keep honest.

## [M3] `mypy` now checks `tests/` as well as `src/`
Evaluated as asked, and done. `uv run mypy src tests` reported **three** errors across 48 files, all
of them one-line fixes and all of them latent bugs rather than annotation noise:
1. `tests/unit/test_layering.py` read `node.lineno` off an `ast.AST` from `ast.walk` after narrowing
   with a conditional expression mypy cannot follow. Fixed by narrowing with `isinstance` first,
   which also removed a three-branch conditional.
2. `tests/integration/conftest.py` returned `Any` from `json.loads` through a `dict[str, Any]`
   signature.
3. The same file passed a `str` where `Skeleton.id` is a `UUID` — Pydantic coerces it, so it worked,
   and the type said otherwise.
Reason: the guards are the argument. `test_layering.py`, `test_models_accept_broken_documents.py`
and the new `test_storage_no_delete.py` are AST walkers whose entire job is to be precise about node
types, and an AST walker with a typo in an attribute name does not fail — it silently matches
nothing and the guard passes forever. That is the failure mode R-31 already caught once by hand.
Alternative rejected: leaving `files = ["src"]`. The cost of the change was three fixes; the cost of
*not* making it is that the next guard to go silently green does so undetected.
Cost carried forward: new test code must type-check. `pytest` fixtures and `monkeypatch` typed fine;
the only friction found was `json.loads` returning `Any`, which is one annotated local.

## [M3] `publish` is the single source of truth for a blueprint's stored status
`put_blueprint(bp, publish)` normalises the stored document's `status` field to match the flag —
`published` when true, `draft` when false — so a row's `status` column and the `status` inside its
document can never disagree.
Reason: `Blueprint` carries a `status` field *and* the Protocol takes a `publish` argument, so two
values describe one fact. One of them has to win, and the argument is the one the caller passed
deliberately.
Consequence, deliberate: `put_blueprint(published_bp, publish=False)` is a *demotion*, which is a
mutation of a published version, so BP-016 refuses it like any other difference. Un-publishing a
version that datasets may already reference (DS-001 requires a published one) is not something this
store does quietly. If demotion is ever wanted it needs its own Protocol method and its own rule id.
Alternative rejected: `status = "published" if publish else bp.status`, which makes the flag
authoritative in one direction only and leaves a document that says `published` sitting in a `draft`
row.

## [M3] BP-016's storage-level guard raises; R-29's identical case returns
A differing document over a published `{agent_id, version}` raises
`PublishedVersionImmutableError`. A canonically identical one is a no-op success returning the stored
blueprint, per ruling R-29, compared with `json.dumps(sort_keys=True)`.
Reason: under R-23 a user-caused BP-016 is caught in `service/` and returned as a rule id with a
pointer, so a differing document arriving at storage means a write path skipped validation. The two
available responses are to raise or to overwrite a published version; raising is the one that does
not lose data. This is the same shape as the two `raise` statements `validation/` allows — a
programming-error guard, not a user-facing error path.
Alternative rejected: returning the stored blueprint silently for the differing case too. It makes
`put_blueprint` look total and turns a lost publish into a mystery.
Same reasoning applied twice more: `set_archived` and `mark_skeleton_submitted` raise
`RecordNotFoundError` for a row that does not exist, because their return types
(`DatasetSummary`, `None`) leave no way to say "no". Read methods never raise — they return `None`,
including for an id that is not a well-formed UUID.

## [M3] Archive flips the flag across the whole lineage, in the column *and* the document
`set_archived(dataset_id, archived)` takes no version, so it updates every version of that id, and
it rewrites `archived` inside each stored document as well as in the column, in one transaction.
It does not bump the version.
Reason three ways. It takes no version, so it is about the dataset, not one of its versions. It must
not bump the version, because a run pinned to version 1 has to keep reading version 1 — an archive
that created version 3 would leave the pinned version un-archived forever. And writing both copies
means an exported dataset carries its true archive state and nothing downstream has to know which
copy wins.
This is the only in-place update on a dataset row, and it does not violate "datasets are immutable
and versioned": the flag is store metadata, not fixture content.
Alternative rejected: `json_set` / `jsonb_set` to patch the document in SQL — two different
functions in the two SQL dialects and neither exists in Mongo. Archiving is a rare administrative
action on a handful of rows, so a portable read-modify-write inside one transaction is the right
trade. Also rejected: treating the column as authoritative and overlaying it onto the document on
read, which makes the stored bytes and the returned model disagree for no gain.
Left simple deliberately: a new version written after an archive takes `ds.archived` as given rather
than inheriting the lineage's flag. `service/` decides that policy; storage does not invent one.

## [M3] `find_datasets` returns one row per dataset id, at its latest version
Not one row per version.
Reason: `dataset_find` promises "deterministic ordering by `(created_at, id)`", and every version of
a dataset shares both of those values — so a row per version makes the promised order genuinely
ambiguous. It would also return five near-identical rows for a dataset edited five times, which is
the opposite of what a discovery surface is for. Implemented as a correlated
`version = (SELECT max(version) ... WHERE id = ...)`, which is portable across both SQL dialects and
index-friendly.
Alternative rejected: a window function (`ROW_NUMBER() OVER (PARTITION BY id ...)`), which also works
on both dialects but reads worse and buys nothing here.

## [M3] `runs` gains a `declared_bp_version` column that `contracts.md` section 7 does not have
**A reported discrepancy, not a silent divergence.** Contracts section 2.3 gives `Run` a
`declared_blueprint_version` field — what the agent *said* it was running, as against
`pin.blueprint_version`, which is what it is being served — and the section 7 `runs` DDL has no
column for it. Every other `Run` field maps to a column or is explained (`path` is reconstructed,
`steps` live in `run_steps`); this one is simply absent.
Chosen: add `declared_bp_version TEXT NULL`.
Reason: the alternative is dropping a model field on write, and that field is the input to the
`blueprint_version_mismatch` warning, which ground rule 3 requires be attached "to the response
*and* to the stored run". Dropping it also breaks the store-wide invariant that a write returns what
a read returns. It is nullable, and absent from `RunSummary` — which ruling R-05 derives from "the
`runs` DDL columns minus `outcome`" — so no read shape changes.
If the owner intends the field to be *derived* rather than stored (recoverable from the stored
warning's `detail`), the column comes out and `Run.declared_blueprint_version` becomes a
service-layer projection. Recorded as a question below.

## [M3] `Run.path` is reconstructed from `run_steps`, never stored
No column holds it, in the DDL or here. `get_run` rebuilds it from `run_steps` ordered by `seq`,
using each row's `fetched_at` as `PathStep.at`, and a `path` carried on the model on the way in is
ignored.
Reason: the model's own docstring states the principle — "the path is reconstructed, not declared:
the ordered sequence of calls carrying a run id *is* the traversal, so branch selection is learned
implicitly and the agent cannot lie about where it went". A column would make it declarable.
Consequence: `put_run` returns the reconstruction, not its argument, so `put_run(run)` and a later
`get_run(run.id)` agree. That is one instance of a store-wide invariant worth stating on its own —
**every write returns exactly what the matching read returns** — which also covers the allocated
dataset version and the allocated `seq`, and is what makes the conformance suite's equality
assertions meaningful rather than tautological.

## [M3] The stored document column is written with `exclude_unset=True`
`document` holds `model.model_dump(mode="json", exclude_unset=True)`, with only the two fields the
store owns overridden — a dataset's allocated `version` and a blueprint's normalised `status`.
Reason: ruling R-08 defines the round trip as
`model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw`, and the golden fixtures
omit many optional fields (`tool_name` on three nodes, `max_iterations` on eight, `input` on every
pool entry). A full dump would reintroduce those as `null`, so the bytes a dataset was submitted
with would stop being the bytes `dataset_export` emits — which is precisely the byte-stability M7's
"exported from SQLite, imported into Postgres" gate depends on. The conformance suite asserts the
golden dataset round-trips byte-for-byte through the store.
Alternative rejected: a full `model_dump()`. Pydantic equality ignores `fields_set`, so the *models*
would still compare equal and the loss would only surface at M7, in the milestone with three
backends in play.

## [M3] `UtcDateTime`, because SQLite silently discards `tzinfo`
A `TypeDecorator` over `DateTime(timezone=True)`: `TIMESTAMP WITH TIME ZONE` on Postgres, SQLite's
TEXT `DATETIME` elsewhere, with values normalised to UTC on bind and UTC re-attached on result.
Reason: SQLAlchemy's SQLite `DATETIME` bind processor formats the naive components and drops the
offset without a warning, so `2026-09-08T10:14:22Z` reads back naive and compares unequal to the
aware value the model carries. Every `created_at`, `started_at` and `fetched_at` assertion would
fail on SQLite and pass on Postgres, for a reason no reader would guess from the code.
Two properties are load-bearing beyond correctness. SQLAlchemy's fixed-width storage format sorts
lexicographically in chronological order, which is what makes `ORDER BY created_at` on SQLite match
Postgres — `find_datasets`'s deterministic-ordering promise. And `CURRENT_TIMESTAMP`, which
`DEFAULT now()` becomes on SQLite, produces a *prefix* of that format, so a defaulted value and a
bound value still sort correctly against each other.
Alternative rejected: storing ISO-8601 text in a `TEXT` column of our own format, which would sort
inconsistently against `CURRENT_TIMESTAMP` and would give up Postgres's native type for nothing.

## [M3] The two Postgres-only indexes live in a dialect branch, not in `METADATA`
`datasets_labels_gin` and `datasets_search` are created by the initial migration inside
`if op.get_bind().dialect.name == "postgresql"`, are absent from `METADATA`, and are filtered out of
Alembic's autogenerate comparison by `include_object` (defined in `storage/sql.py`, next to the
names it filters, and imported by `migrations/env.py`).
Reason: `create_all` would otherwise try to build a GIN index on SQLite, and autogenerate against a
Postgres database at M7 would see two indexes it does not know about and propose dropping them. The
filter makes `alembic check` clean on both dialects, which is what lets the check be a gate.
Alternative rejected: declaring them in `METADATA` with `postgresql_using="gin"`. SQLite ignores the
dialect kwarg and would create two useless B-tree indexes with misleading names.

## [M3] `storage/` duplicates `canonical()` rather than importing it
`sql.py` has a three-line `_canonical`, a duplicate of
`validation/jsonschemas.py`'s `canonical()`.
Reason: the layering rule is explicit — `storage/` may import `models/` and must not import
`validation/` — and R-23's ordering is the reason it exists: validation happens in `service/` before
storage sees a document, so an adapter that imported the catalogue would put it on the write path
twice and on the read path at all. R-29 names the comparison literally
(`json.dumps(sort_keys=True)`), so the duplicate is a specification, not a copy of an
implementation. `tests/unit/test_layering.py` now enforces the import rule for `storage/` as it
already did for `models/` and `validation/`.
Alternative rejected: moving `canonical()` into `models/` as a shared pure helper. Defensible, and
it would touch M2's module and give `models/` a function that belongs to no model. Reconsider if a
third layer needs it.

## [M3] Upserts are select-then-insert-or-update, not `ON CONFLICT`
`put_blueprint`, `put_skeleton` and `put_run` read the row inside a transaction and then insert or
update.
Reason: SQLite and Postgres spell `ON CONFLICT DO UPDATE` differently enough to need a dialect
branch, Mongo does not have it, and two of these three need to *inspect* the existing row anyway —
`put_blueprint` compares documents for BP-016, `mark_skeleton_submitted` compares the existing
`submitted_as`. A single-row upsert by primary key is not the thing worth optimising in an authoring
store.
Alternative rejected: `sqlalchemy.dialects.sqlite.insert(...).on_conflict_do_update(...)`, which
would be the first place in the module to ask what dialect it is talking to.

## [M3] `get_blueprint(agent_id, None)` is the newest published version, by semver, sorted in Python
Contracts section 4 documents `blueprint_get` as "Latest published when version omitted", so an
omitted version never returns a draft. "Latest" is greatest semver, computed in Python.
Reason: `ORDER BY version` on a `TEXT` column puts `1.10.0` before `1.9.0`, and no portable SQL
expression fixes that. BP-002 guarantees semver at write time, but a store must not raise on data it
can be handed, so an unparseable version sorts below every parseable one and ties break
lexicographically. The row count per agent is small enough that sorting in Python costs nothing.
Alternative rejected: a `version_sort_key` column maintained on write — real complexity, and it
would be the first column not in contracts section 7 for a reason other than a model field needing
somewhere to live.

## [M3] `health()` never raises, and its counts are row counts
An unreachable backend returns `healthy: false` with zero counts rather than propagating a
`SQLAlchemyError`. `counts` are table row counts, so an edited dataset counts once per version and
archived datasets are included (ruling R-05).
Reason: `store_status` exists to answer "is the backend there", and an exception is a worse answer
than "no". `StoreCounts`'s own docstring says "row counts per aggregate", and R-05 says the dataset
count includes archives because it is a store-health number, not a discovery number.
Alternative rejected: counting distinct dataset ids, which is a different and more useful number for
a human but is not what the type says and would make `counts.datasets` disagree with the row count
an operator sees in `psql`.

## [M3] SQLite gets `PRAGMA foreign_keys=ON`, set on the `connect` event
`create_engine_for` registers it per connection for SQLite engines; `migrations/env.py` uses the
same factory rather than `engine_from_config`.
Reason: SQLite parses `REFERENCES` and ignores it unless asked. Contracts section 7 declares four
foreign keys; declaring them without enforcing them would be claiming something untrue, and it would
make SQLite and Postgres behave differently in the one area the conformance suite is explicitly told
not to test (contracts section 8: Mongo has no foreign keys, so referential behaviour is tested
through `service/`). The suite therefore writes a blueprint before a dataset because that is the
honest order, not because SQLite forces it.
One trap, worth recording because it cost a debugging round: issuing the pragma on the *connection*
inside `env.py` opens a SQLAlchemy transaction before `context.configure`, Alembic concludes the
caller owns the transaction and never commits, and the result is a database with every table created
(pysqlite autocommits DDL) and an empty `alembic_version` — so `alembic check` reports "target
database is not up to date" immediately after a successful `alembic upgrade head`. Using the engine
factory, which sets the pragma on the DBAPI `connect` event, avoids it entirely.

## [M3] `--store` is repeatable, defaults to sqlite, and skips what is not built
`pytest_addoption` in `tests/conftest.py` with `choices=("sqlite", "postgres", "mongo")` and
`action="append"`; `pytest_generate_tests` in `tests/integration/conftest.py` parameterises every
`store`-taking test over the selection; the fixture skips with a reason for a backend not in
`IMPLEMENTED_STORE_BACKENDS`.
Reason: the milestone's own command is `uv run pytest -m integration --store postgres`, which must
be a sensible thing to type at M3 and a passing thing to type at M7. `choices` makes a typo a pytest
usage error rather than a confusing skip. The default of `sqlite` means plain `uv run pytest` runs
the whole conformance suite rather than skipping it.
M7's work is one tuple and two fixture branches. Not a line of the suite changes.

## [M3] Two integration modules are deliberately SQL-specific
`test_migrations.py` and `test_sql_version_allocation.py` do not take the `store` fixture and are not
parameterised over backends.
Reason: Alembic is SQL-only — contracts section 8 gives Mongo collections and indexes, not DDL — so
there is nothing for a document store to conform to; and version allocation's *mechanism* is a SQL
primary key, which is exactly what the backend-agnostic suite must not assert on. Keeping them
separate is what lets `test_store_conformance.py` stay honestly portable.
`test_migrations.py` runs Alembic's own autogenerate diff (`compare_metadata`, which is what
`alembic check` runs) against a freshly migrated database and asserts it is empty. That is what makes
it safe for the conformance fixture to build its schema with `create_all`: the two paths are proven
to produce the same schema. It also runs a write journey against the migrated database, because a
schema comparison can pass while a column type is unusable through the adapter.

## [M3] `schemas_dir()` asserts a sibling `pyproject.toml`
Deferred minor from M1's review, done. `repo_root()` resolves `parents[2]` and raises `RuntimeError`
naming the path if there is no `pyproject.toml` beside it; `schemas_dir()` is now
`repo_root() / "schemas"`.
Reason: the parent arithmetic is only true in a source checkout. Installed from a wheel it points at
whatever contains `site-packages/agentprops`, and `write_schemas` would `mkdir(parents=True)` and
write two files into a virtualenv, silently, reporting success.
`repo_root` takes an optional `module_file` purely so the guard is testable — the failing case cannot
be reached by calling it from inside the repository, which is the only place the tests run. The mild
smell of a test-only parameter is cheaper than an untested guard.

---

## Questions for the owner — M3

None of these block the build; each has a working decision above.

1. **`declared_blueprint_version` has no column in `contracts.md` section 7.** Contracts 2.3 gives
   the field to `Run` and the DDL omits it. A column was added (nullable, absent from `RunSummary`)
   because the alternative is dropping a model field on write. If it is meant to be *derived* from
   the stored `blueprint_version_mismatch` warning rather than stored, say so and the column comes
   out.
2. **Should a new dataset version inherit the lineage's archive flag?** `set_archived` flips every
   version; an edit written *after* an archive currently takes `ds.archived` as given, which can
   leave versions 1-2 archived and version 3 not. Storage does not invent the policy; `service/`
   will have to pick one at M5, and "an edit un-archives" and "an edit inherits" are both defensible.
3. **Does `upsert_step`'s strict idempotency leave `record_step` a seam at M8?** Contracts section 6
   says a second call with the same key "is a no-op that returns the existing record", which means
   `upsert_step` cannot be the thing that writes `actual` later. M8 needs either a new Protocol
   method for the recorded half or a `put_run` that rewrites its steps. Flagged now because it is a
   Protocol shape question, and the Protocol is contracts', not M3's.
4. **`list_blueprints` and `find_runs` orderings are unspecified.** Chosen: `(agent_id, semver)` for
   blueprints, `started_at DESC, id` for runs — the latter being the order `runs_lookup` is built
   for. Both are deterministic, which is the property that matters; if a tool surface wants a
   different one, it is a one-line change at each call site.

---

## [M3, fix round 1] Correction: "computed inside the transaction" was not a safety claim I could make
The `seq` entry above says `upsert_step` computes the value "inside the transaction", and the M3
report said the same. **That wording is withdrawn.** A transaction around a read and a write is not
a lock over the value read, and the review did not argue the point — it *reproduced* the failure,
replaying `upsert_step`'s statement sequence on two interleaved connections against one file-backed
SQLite database, and both committed `seq = 1`. pysqlite defers `BEGIN` until the first DML, so
neither the existence check nor the `max(seq)` read takes any lock; Postgres under READ COMMITTED
behaves the same way.
What makes it sound is `UNIQUE (run_id, seq)` plus a bounded retry — ruling R-37, and the entry
below. The uncomfortable part is that the dataset-version entry above gets this exactly right, names
the primary key as the guard, says "not a lock", and tests the losing side; `seq` is the one
allocation where the same author did not apply his own pattern. Recorded rather than quietly edited,
because the file is append-only and because a wrong safety claim in a decision log is worse than no
claim.

## [M3, fix round 1] `UNIQUE (run_id, seq)`, and the seq retry that the constraint makes sound (R-37)
Added to `run_steps` — an addition to `contracts.md` section 7 that ruling R-37 authorises, and
amended into section 7 in this round. `upsert_step` now allocates with the same
bounded-retry-on-`IntegrityError` shape `put_dataset` uses, and distinguishes three outcomes after
the error rather than collapsing them: the step key now exists (another writer served it first, so
the next pass returns *its* row and this call is the documented no-op), the `(run_id, seq)` pair is
taken (the race, lost — re-read and take the next number), or neither (a missing run tripping the
foreign key, re-raised as itself).
Reason: the same reason `PRIMARY KEY (id, version)` guards dataset versions. A constraint is the only
mechanism all three backends have — no `SELECT ... FOR UPDATE` on SQLite, no advisory lock on Mongo —
and it turns a lost race into a retry rather than into two rows that disagree.
Why it mattered more than it looked: `get_run` orders steps by `seq`, and `Run.path` is what step
resolution disambiguates against (`contracts.md` section 5 takes `run.path[-1]` as the head). A
duplicate `seq` would have surfaced at M6 as intermittently wrong tool-name resolution with no
visible cause — the most expensive shape of bug this build can ship.
Three tests, all of them the losing side: a stale allocation read must re-allocate to 2 rather than
duplicate 1; a step key that appears mid-race must return the winner's stored row; and a step for a
run that does not exist must raise the foreign-key error rather than eight retries reported as an
allocation failure.

## [M3, fix round 1] The step read order is `(seq, node_id, iteration)` (R-37)
Was `seq` alone.
Reason: `UNIQUE (run_id, seq)` means the two extra keys never decide anything today, which is the
point — the order is total whether or not the constraint holds. R-35 requires every list-returning
method to have a total order so identical inputs give byte-identical output on every backend, and
R-37 makes the step order a fourth alongside R-35's three. Free, unconditional, and it is the
ordering `Run.path` is built from.

## [M3, fix round 1] Revision `0001` was amended rather than superseded by a `0002`
The unique constraint went into the initial revision.
Reason: the rule worth keeping is "never edit a revision that has been applied somewhere", and this
one has not been — it is the initial schema of an unreleased milestone on an unmerged branch, and no
database outside a temporary test file has ever been migrated by it. There is nothing for an additive
revision to migrate *from*. A `0002` would instead make every future deployment replay a SQLite
table rebuild (Alembic batch mode, since SQLite cannot `ALTER TABLE ADD CONSTRAINT`) to add a
constraint that `0001`'s own `CREATE TABLE` can simply declare.
Alternative rejected: a second revision, for the sake of demonstrating that a second revision works.
The migration path is exercised either way — `alembic upgrade head`, `alembic check` and
`alembic downgrade base` all run in `test_migrations.py` — and buying that demonstration with a
permanent table rebuild in every deployment's history is the wrong trade.

## [M3, fix round 1] `set_step_actual` joins the Protocol at M3 (R-33)
`set_step_actual(run_id, node_id, iteration, actual) -> StepRecord`, on the Protocol, the adapter and
`contracts.md` section 6.
Reason: `upsert_step`'s documented strict idempotency — "calling it twice with the same key is a
no-op that returns the existing record" — means it can never be the method that writes `actual`, and
M6's gate depends on that no-op staying literal. M3 reported this as a seam for M8; R-33 rules it
into M3 instead, because the method belongs to the Protocol and M7 implements the Protocol against
two more backends. One method now, versus reopening three signed-off adapters at M8.
Semantics: write-once per key. `RecordNotFoundError` when no step record exists — an actual cannot be
reported for a step that was never served, and inventing the row would produce evidence with no
`served` half. An identical re-record is a no-op success and a differing one raises, the same replay
tolerance `mark_skeleton_submitted` and BP-016 have. The `UPDATE` carries
`WHERE actual IS NULL`, so a concurrent second writer cannot clobber, and the loop then re-reads and
takes the identical-or-refuse decision on the stored value.
`recorded_at` is stamped by `func.now()` — the *database's* clock, the same mechanism the sibling
`fetched_at` column uses as its default, which is ruling R-09's first category. R-33's signature
carries no timestamp, and leaving the column null forever would make it unreachable, since
`upsert_step` accepts a `recorded_at` only on insert and its repeat is a no-op. There is still no
clock read in Python anywhere in `storage/`, which is what the AST guard checks.

## [M3, fix round 1] `archived` is inherited from the lineage on every version after the first (R-34)
`put_dataset` reads the lineage's current archive state and writes that, into the column and into the
document; `ds.archived` is honoured only when the lineage has no versions yet.
Reason: R-34 closes the question the first round left open, and it follows from the Protocol's own
shape — `set_archived(dataset_id, archived)` takes an id and no version, so the flag is a property of
the dataset. Without the inheritance, editing an archived dataset silently un-hides it, and a
half-archived lineage has no coherent answer for `find_datasets`, which returns one row per lineage.
The first-version exception is what keeps an archived dataset importable as archived: whether a
bundle's dataset is archived is a fact about the bundle, not a state for M7's `dataset_import` to
reset.
Mechanically this also closed a smaller gap: `_document` now carries `archived` in its overrides
alongside `version`, so the column and the document agree on a *write* the same way they already
agreed after a `set_archived`.

## [M3, fix round 1] The `q` filter folds case in Python, and the match with it
Was `LOWER(title) LIKE '%term%'` in SQL against a term folded by Python's `str.lower()`. Now
`str.casefold()` on both sides, applied in `find_datasets` after the SQL filters and the ordering,
with `limit`/`offset` applied after the filter rather than in SQL.
Reason: those were two different fold functions inside one comparison, and no SQL expression can fix
it. Verified rather than assumed — SQLite returns `lower('BENGALŪRU')` as `'bengalŪru'` (its
`lower()` is ASCII-only), Postgres's is locale-aware, and Mongo's `$regex` with `i` is a third
answer. Folding the column in SQL therefore returns different rows per backend *whatever* is done
with the term, which is precisely what "identical results across backends" forbids and what PRD
design principle 2 rests on. One implementation in Python is the only version that is identical
everywhere.
`casefold` rather than `lower` because it is the Unicode operation designed for caseless comparison;
picking the weaker one would be choosing to be subtly wrong on purpose.
Two consequences, both accepted deliberately. **Pagination moves into Python for `q` queries only**,
because a page sliced before the filter runs comes back short — the same class of bug as filtering
archives after `LIMIT`, which the pagination test now also pins with an archived row that sorts
first. And **a `q` query materialises every row matching the other filters**, which is fine at "the
volumes a local authoring instance sees" (contracts section 7's own words) and is bounded by the
`agent_id`/`labels`/`author` filters. If it ever is not, the two-phase form is to select
`(id, version, title, intent)` first, fold, then fetch the page's full rows.
For M7: R-36 already rules that Postgres uses `ILIKE`/`pg_trgm` rather than full-text search, and an
SQL prefilter there is legitimate *only* as a superset of this Python fold. Postgres's `lower()` and
Python's `casefold()` are close enough for that to hold; the conformance suite is what would catch it
if they were not, and it now carries a non-ASCII case for exactly that.
Escaping is no longer a concern at all: with no `LIKE` in play, `%` and `_` are literal substrings by
construction. The wildcard test survives unchanged and now proves a property of the implementation
rather than of an `autoescape` flag.

## [M3, fix round 1] The three writers with a first-write race retry once
`put_blueprint`, `put_skeleton` and `put_run` took the insert branch on `row is None` with no
`IntegrityError` handling, so two concurrent first writes of one key surfaced a raw driver error.
Each now retries once and re-enters the select branch — `FIRST_WRITE_ATTEMPTS = 2` — and on the last
attempt re-raises the `IntegrityError` as itself rather than translating it.
Reason: for `put_blueprint` this partly undermined R-29's own motivation. "A CI pipeline that
publishes on every run is the normal case, not an abuse" — and two runners publishing the identical
blueprint simultaneously both see no row, both insert, and one loses. The right outcome is R-29's
no-op success, which is exactly what the second pass produces.
Re-raising on the last attempt rather than translating is what keeps a *different* `IntegrityError`
legible: `put_run` with a pin to a missing dataset version trips `runs_dataset_fkey`, fails the same
way on the retry, and reports the constraint it broke. Two tests cover both halves.
`put_blueprint`'s select moved into a `_blueprint_row(conn, ...)` helper that takes the connection,
which keeps the read and the write it decides on inside one transaction — without that, a version
could go draft → published between them and BP-016 would be checked against a status that no longer
holds — and makes the losing side of the race testable by monkeypatching one method.

## [M3, fix round 1] `JsonDocument` sets `none_as_null=True`
SQLAlchemy's default is the other one: a Python `None` bound to a `JSON` column is persisted as the
JSON value `null`, not as SQL `NULL`.
Reason: found by writing `set_step_actual`'s `WHERE actual IS NULL` guard and watching it match
nothing. It round-trips either way, so nothing looked wrong — but the three nullable JSON columns
(`runs.model`, `runs.outcome`, `run_steps.actual`) held a JSON value where the DDL says `NULL`, and
any SQL predicate about absence was silently false. "Absent" should be SQL `NULL` on both dialects.
Not a schema change — the rendered type is unchanged, and `alembic check` stays clean.

## [M3, fix round 1] `compare_server_default=True` in both the drift test and `alembic check`
Added to `test_migrations.py` and to `migrations/env.py`.
Reason: one column. Under ruling R-09 `datasets.created_at` must have **no** default, because a
`DEFAULT now()` would be re-stamped on re-import and `dataset_find`'s ordering would stop being
reproducible. Without the flag, the one load-bearing default in the schema sat outside the drift
gate. Clean on SQLite with no false positives, so it went into `env.py` too rather than only the
test — a gate a human runs should be at least as strict as the one CI runs.

## [M3, fix round 1] `STATUSES` removed
Unused. `STATUS_DRAFT` and `STATUS_PUBLISHED` are both used and stay.

## [M3, fix round 1] Two test additions that exist to constrain M7 rather than M3
- **A dotted `parts` key.** Two of ruling R-06's five section ids contain a dot (`nodes.core`,
  `nodes.branches`), and `parts` is keyed by section id — so a filled skeleton has object keys with
  dots in them. Legal JSON, legal in a SQL JSON document, and **not** dot-path addressable in Mongo,
  where `parts.nodes.core` reads as two levels of nesting. The conformance suite now fills one, so
  M7's Mongo adapter has to store `parts` opaquely rather than reaching into it, instead of M5 or M8
  discovering it through a section that silently fails to save.
- **An archived row in the pagination test.** It sorts *first*, so excluding archives in the `WHERE`
  clause returns a full page and filtering them out after `LIMIT` returns a short one. With two
  unarchived rows and nothing else, both implementations passed.

## [M3, fix round 1] Notes for M7, recorded so they are not rediscovered
- **`runs_lookup` is the likelier `alembic check` false positive, not the Postgres-only indexes.**
  `METADATA` declares `desc(runs.c.started_at)` while the migration writes
  `sa.text("started_at DESC")`, and Alembic's autogenerate cannot reliably reflect or compare
  expression and DESC index columns. It is clean on SQLite today. The M3 report singled out
  `datasets_labels_gin` and `datasets_search` as the drift risk; those are handled by
  `include_object`, and this one is not handled by anything.
- **The `sa.text("'{}'")` / `sa.text("'[]'")` server defaults have never run against a live
  Postgres.** They are verified only as compiled SQL. Postgres coerces an unknown-type literal to
  `jsonb`, which is why they are written that way, but "coerces" is a claim about the server and no
  server has been asked. First thing to check at M7.
- **The `q` filter's Python fold** is the one place a Postgres optimisation could change *results*
  rather than only speed. See the `q` entry above.

## [M3, fix round 1] One open question closed, one still open
**Closed by R-34:** whether a dataset version written after an archive inherits the flag. It does.
The question recorded at the end of M3's first round is answered and should be read as closed.
**Closed by R-33:** whether `upsert_step`'s strict idempotency leaves `record_step` a seam at M8. It
does not any more; `set_step_actual` is on the Protocol.
**Still open, and not a blocker:** nothing. R-32 keeps the `declared_bp_version` column and section 7
now carries it; R-35 ratifies both list orderings and section 4 now states them; R-36 keeps substring
`q`; R-38 ratifies the lineage grain. Every question M3's first round raised has a ruling.

---

## [M3, fix round 2] Correction: `set_step_actual` had two decision sites, and the second one always said yes
The fix-round-1 entry above says "the `UPDATE` carries `WHERE actual IS NULL`, so a concurrent second
writer cannot clobber, and the loop then re-reads and takes the identical-or-refuse decision on the
stored value". **The first half was true and the second half described an intention, not the code.**
What the code did after the conditional `UPDATE` was read the row back and return it whenever
*some* actual was stored:

```python
written = self._step_row(run_id, node_id, iteration)
if written.actual is not None:
    return _step(written)
```

It never compared `written.actual` with the caller's argument. So the losing writer in a genuine race
received a **successful `StepRecord` carrying the winner's value** and no exception — the exact
opposite of the write-once contract R-33 exists to guarantee, and of what the method's own docstring
promised. The review reproduced it against a real `SqlStore` on a real SQLite file: writer A stores
`{"result": "X-from-A"}`, writer B calls with `{"result": "Y-from-B"}` having seen `actual=None`, and
B gets a success whose `actual` is A's value.

**Fixed by looping back to the top rather than by adding a second comparison**, which is what the
reviewer preferred and is the right call: the comparison at the top of the loop is now the only place
this method decides anything, so there are no longer two sites that have to stay in agreement. It is
also the shape `upsert_step` already uses — its lost-race branch `continue`s and re-runs the full
existing-row check rather than returning from the middle.

Worth naming the pattern, because this is the second round in a row where the same class of defect
appeared in the one place a working pattern was not reused: `seq` was the one allocation that did not
copy `put_dataset`'s constraint-plus-retry, and `set_step_actual` was the one retry that returned
from the middle instead of looping. Both were caught by a reviewer replaying the interleaving rather
than reading the comment. The comment was wrong both times.

## [M3, fix round 2] Three race tests added, closing the set
The harness in `test_sql_allocation_races.py` tested the losing side of every allocation race except
three. Now none:

- **`set_step_actual`** — a stale reader with a *differing* actual must raise `StoreError`, and one
  with an *identical* actual must still be the no-op. Verified as a real regression guard, not just
  an assertion: with the fix-round-1 code restored, the test reports
  `Failed: DID NOT RAISE StoreError`, and it passes against the fix.
- **`put_skeleton`** — had no race test at all. Convergence here means last-write-wins on one row,
  since a skeleton is an in-progress fill and re-filling a section is explicitly allowed (R-06), so
  there is nothing to compare and nothing to refuse.
- **`put_run`'s convergence** — the existing test covered only the other branch of the same
  `except`, the unrelated foreign-key violation that must still surface. This one asserts the race
  the retry exists for actually converges on one row.

`put_skeleton` and `put_run` each gained a one-line existence-check helper (`_existing_skeleton`,
`_existing_run`) for the same two reasons `_blueprint_row` has one: the read and the write it decides
on stay in one transaction, and the losing side becomes reachable in a test. That the refactor was
needed at all is a small piece of evidence for the general rule — an inline read inside a retry loop
is a branch nobody can test.

## [M3, fix round 2] How the race tests force a race, stated rather than implied
Recorded because the M3 report's phrasing invited the wrong reading. Every race test monkeypatches
**one** internal read to return a stale answer once — the allocation read, an existence check, or the
step row — which is exactly what the loser of a real race sees. Nothing else is mocked: the `INSERT`
or `UPDATE` that follows, the constraint that rejects it, the `IntegrityError` and the retry all run
against a real file-backed SQLite database.

What that proves is that the **recovery** is correct given a stale read. That a stale read is
*reachable* is a separate claim, and it was established separately, by replaying the statement
sequence on two interleaved real connections — which is how the review found the `seq` race and what
ruling R-37 records. That reproduction was a throwaway script and is not in the diff; the report now
says so instead of implying the harder proof is committed.

Keeping the monkeypatch technique rather than promoting it to threads is deliberate: a threaded test
of a race this narrow is either flaky or has to be forced into determinism by the same kind of hook,
and the forced-stale-read version fails loudly and repeatably against a regression, as the
`set_step_actual` check above demonstrates.

## [M4] Ruling R-16's import path re-verified, and everything else the handoff claims with it
`from mcp.server import MCPServer` is correct against the installed `mcp` 2.2.0. `from mcp import
MCPServer` raises `ImportError` — `MCPServer` is not a top-level export.
Reason: R-16 is the one thing in the stack the register verified by hand, and it said to report a
drift rather than work around one. Nothing had drifted. Also re-verified while building, so the
report is evidence rather than recollection: the `@mcp.tool()` decorator, `run_stdio_async()`,
`run_streamable_http_async(host, port, streamable_http_path)`, and `mcp.Client` accepting an
`MCPServer` instance directly — which is what makes the whole tool-contract suite subprocess-free.
Two SDK details the handoff does not mention and that cost time: the v2 model fields are
**snake_case** (`tool.input_schema`, `result.is_error`, `result.structured_content`, not the v1
camelCase spellings), and `Client` takes a *transport*, so feeding it `stdio_client`'s `(read,
write)` tuple raises `TypeError: 'builtins.tuple' object does not support the asynchronous context
manager protocol` — pass `StdioServerParameters` to `Client` instead.
Alternative rejected: none; the ruling was right.

## [M4] Ruling R-20's premise is false for this SDK, so the raw-text boundary is an explicit argument
R-20 says to detect DS-013 "at the tool boundary, where the raw text still exists". **With the
documented `dataset: object` signature it does not exist there**, and that is a finding about the
ruling rather than a reason to skip it. Three layers of the installed SDK destroy a duplicate key
before any agentprops code runs, all verified:

1. `mcp/server/stdio.py:189` parses each line with `jsonrpc_message_adapter.validate_json`, and
   `mcp/server/_streamable_http_modern.py:402` calls `json.loads(body)` — both plain parses, no
   hook, no interception point exposed;
2. the in-memory `Client` never has raw text at all;
3. `MCPServer` **pre-parses a JSON-string argument itself**, with a bare `json.loads`
   (`mcp/server/mcpserver/utilities/func_metadata.py:256`, `pre_parse_json`), for every parameter
   whose annotation is not literally `str`. A parameter annotated `object` handed
   `'{"labels": {"persona": "a", "persona": "b"}}'` receives `{'labels': {'persona': 'b'}}`.

Chose: `dataset_validate` takes an additional optional `dataset_json: str`, annotated exactly `str`
so `pre_parse_json` skips it, and that argument is R-20's boundary. `service/documents.py` owns the
parse (`server/` may not import `validation/`), so M5's `dataset_submit` and M7's `dataset_import`
inherit the same seam. `docs/contracts.md` section 4's `dataset_validate` row is amended.
Reason: additive and optional, in the same class of refinement as R-05's six missing types and
R-32's missing column. The rule protects something real — a submitted dataset silently losing a
label value — and the raw-text path is the one M7 and M9 will both use.
Alternative rejected: R-20's own fallback, "retire DS-013". One optional argument is cheaper than
removing a rule, its registry entry and its corpus exemption.
Pinned by a test rather than by this entry:
`test_service_documents.py::test_the_sdk_collapses_a_duplicate_key_in_a_json_string_argument` calls
`pre_parse_json` directly and asserts both halves, so if a future SDK release passes the string
through, or parses it with a hook, the workaround's justification fails loudly.

## [M4] Every tool parameter is annotated `object`, with its real type in `json_schema_extra`
Chose: `TextArg = Annotated[object, Field(json_schema_extra={"type": "string"})]` and friends in
`server/args.py`; `ArgReader` coerces and reports `AP-001`.
Reason: `MCPServer` validates arguments against a Pydantic model built from the signature *before*
calling the function. With `agent_id: str`, a caller sending `agent_id: 123` gets
`isError: true` and a Pydantic message — not the section 1 envelope, which CLAUDE.md requires for
"a malformed argument". Verified: with `object`, the SDK accepts the value and the tool reports
`AP-001` with a pointer at the argument. `json_schema_extra` keeps the published schema honest for
LLM callers — the advertised contract is *stricter* than the implementation, which is the safe
direction.
Alternative rejected: natural annotations, accepting that malformed arguments answer outside the
envelope. Also rejected: bare `object` with no `json_schema_extra`, which loses the type an LLM
caller reads. `test_tool_surface.py::test_every_tool_argument_advertises_a_json_type` is what stops
that regressing silently.

## [M4] The success envelope's `data` always carries one named key
Chose: `{"ok": true, "data": {"blueprint": {...}}, "warnings": []}`, never `data` as the payload
itself. Keys: `blueprint`, `blueprints`, `dataset`, `datasets`, `summary`, `diff`, `status`,
`vocabulary`, `agents`.
Reason: `SuccessEnvelope.data` is `dict[str, Any]`, so `blueprint_list` and `dataset_find` cannot put
their rows there directly. Using the same shape for the object-valued tools costs one level of
nesting and buys a caller that never has to ask which tools wrap; a field added to `data` later can
never collide with a payload key. contracts section 4's "Returns" column describes the payload, not
where in the envelope it sits, so nothing is contradicted. Section 1 now records the convention.
Alternative rejected: `data` = payload for object-valued tools, wrapped for array-valued ones. Two
shapes for one field, and callers would have to memorise which is which.

## [M4] Five `AP-*` boundary codes, documented in a new contracts section 3.5 and disjoint from the registry
Chose: `AP-001` malformed argument, `AP-002` malformed JSON text, `AP-003` document shape no rule
owns, `AP-004` not found, `AP-005` store refused.
Reason: three failures a user can cause have no catalogue rule because they happen before or after a
document exists, and `RuleError.rule` is the only field an envelope has for a code. `AP-003` is the
residue of R-04 and R-23 and is not hypothetical — R-07's second amendment records that exact gap
producing a raw `ValidationError` where a rule id belonged. `AP-004` and `AP-005` are resolution
rather than validation, which section 1 covers explicitly ("on a validation **or resolution**
failure").
`contracts.md` gains section 3.5, marked as **not** part of the rule registry, and
`test_service_envelope.py` asserts the documented set equals `BOUNDARY_CODES` *and* that both are
disjoint from `RULE_REGISTRY` and from sections 3.1-3.4. Without the second assertion an `AP-*` id
could be registered as a rule and the drift test would start failing two files away from the cause.
Alternative rejected: reusing a `BP-*`/`DS-*` id for a boundary failure (a code with two meanings),
and omitting the code entirely (the model requires one).

## [M4] `blueprint_upsert` normalises `status` from `publish` *before* validating
Chose: `document["status"] = "published" if publish else "draft"`, then `validate_blueprint`, then
parse, then `put_blueprint`.
Reason: not tidiness — it closes a reachable crash. BP-016 compares the *submitted* document against
the stored one canonically (R-29); `put_blueprint` compares the *normalised* document. Submit a
document identical to a published version but carrying `status: "published"`, with `publish=False`:
BP-016 sees two identical documents and reports nothing, then `put_blueprint` normalises `status` to
`draft`, finds a difference against a published row, and raises
`PublishedVersionImmutableError` — a user-caused exception, which CLAUDE.md forbids. Normalising
first makes the two comparisons identical by construction. It costs nothing: no rule reads `status`,
so no finding's pointer moves, and `status` becomes a derived field a caller may omit.
Tested, not asserted: `test_demoting_a_published_version_reports_bp_016_rather_than_raising`.
Alternative rejected: catching the exception only. The service still does that as defence in depth,
with `test_a_store_that_refuses_a_write_becomes_a_bp_016_envelope` monkeypatching the store into
raising — because a guard whose only proof is "the code above cannot reach it" is the shape of claim
the M3 fix rounds were spent on.

## [M4] The `blueprint_diff` payload shape
**Superseded in one respect by `[M4, fix round 1]` below (ruling R-41): a fifth `blueprint` category
was added, and this entry's "Deliberately not in the diff" paragraph is retracted.**
contracts section 4 names four categories and no payload, so the shape is defined in
`service/diff.py` and recorded here:

```jsonc
{
  "agent_id": "location-onboarding",
  "from": {"version": "1.0.0", "present": true,  "status": "published"},
  "to":   {"version": "1.1.0", "present": false, "status": null},
  "nodes": { "added": ["id"], "removed": ["id"],
             "changed": [{"node_id": "x", "changes": [{"field": "kind", "from": "a", "to": "b"}]}] },
  "edges": { "added": [{"from": "a", "to": "b", "condition": null}], "removed": [] },
  "schemas": [{"node_id": "x", "field": "output_schema", "from": {}, "to": {}}],
  "labels": { "dimensions_added": [], "dimensions_removed": [],
              "values_added": [{"dimension": "tier", "values": ["national"]}], "values_removed": [] }
}
```

Five decisions inside it, each of which could have gone the other way:

- **`nodes.changed` and `schemas` partition a node's fields and never overlap.** `changed` reports
  the five scalar fields (`tool_name`, `kind`, `pool`, `max_iterations`, `notes`); `schemas` reports
  `input_schema` and `output_schema`. A node whose only difference is a schema appears in `schemas`
  and *not* in `nodes.changed`, so "which nodes differ at all?" is the union of three lists. The
  alternative — repeating the before/after schema blobs inside `nodes.changed` too — makes each
  category independently consumable at the cost of duplicating the largest values in the document,
  in a payload an LLM may be reading. `test_the_scalar_and_schema_field_sets_are_disjoint` pins the
  partition.
- **Edge identity is `(from, to, condition)`,** so a changed condition is one removal plus one
  addition. The contract gives edges only *added* and *removed*; under identity `(from, to)` alone a
  condition change would be neither, and therefore invisible.
- **A missing version is `present: false`, never an error,** plus a `blueprint_version_missing`
  warning from the service. "Never a failure signal" in the contract row is unconditional.
- **Everything is sorted** — node ids, edges (by canonical identity), schema entries, dimensions,
  values — because the acceptance criterion is byte-identical output across repeated identical calls
  and a set iteration order is not that.
- **No timestamp.** `generated_at` was the obvious field and is absent for the same reason.

Deliberately **not** in the diff, because none of the four categories covers it: `entry_node`,
`outcome_schema`, `description` and the **entity schemas**. See "Questions for the owner" below.

## [M4] Ruling R-22's warning vocabulary grows by one: `blueprint_version_missing`
Chose: `blueprint_diff` attaches it, once per absent side, with `{agent_id, version, side}`.
Reason: R-22 keeps `Warning.code` an open string precisely so the vocabulary can grow without a
model change, and `blueprint_diff` needs a way to say "this version does not exist" without failing.
Alternative rejected: `AP-004`. That would make the tool report a resolution failure, which the
contract forbids in that row.

## [M4] Warning-severity findings become `Warning` entries on the success envelope
Chose: `warnings_from(findings)` maps each warning-severity `RuleError` to
`Warning(code=rule_id, detail={pointer, message, section, context})`.
Reason: ruling R-13 makes BP-019, DS-007, DS-027 and DS-032 warnings, so a document that trips only
those must store — and ground rule 3 says the mismatch comes back as a warning "attached to the
response". `SuccessEnvelope` has no `errors`, so the finding has to ride somewhere, and nothing the
validator reported should be dropped on the way. The rule id becomes the code, which is what
`Warning.code`'s open string is for.
Alternative rejected: returning the validator's `ErrorEnvelope` from a write path. That would make a
successful write look like a validation result and lose the stored document.

## [M4] `ServiceContext` is two injected ports and nothing else
Chose: `ServiceContext(store: Store, clock: Clock = SystemClock())`, with `resolver` as a derived
property rather than a third field.
Reason (the property): a `StoreResolver` is stateless and there is exactly one correct resolver for a
given store. Letting a caller pass a different one would let a write path validate against a store
it does not then write to — which is precisely the BP-016 hazard R-29 exists around.
Reason (two, not three): a third port is the moment to ask whether the thing being added belongs in
the service at all. There is no LLM client, no HTTP client, no API key and no config object (ground
rule 4).
How tests freeze the clock: `tests/conftest.py`'s `context` fixture builds
`sqlite_context(tmp_path / "agentprops.db", clock=FrozenClock(FROZEN_NOW))`, and `FROZEN_NOW` moved
to the root conftest so `tests/integration/conftest.py` imports it rather than redefining it.
**M4 stamps nothing with the clock,** and that is not an oversight: `validated_at` is a dataset
field and the dataset write path is `dataset_submit`, which is M5's. M4 also deliberately puts no
clock reading into any *response* — `blueprint_diff`'s `generated_at` was the obvious candidate — so
that repeated identical calls stay byte-identical.
`test_layering.py::test_only_the_clock_module_reads_a_clock` asserts no module in `service/` or
`server/` other than `clock.py` calls a clock, because "a single injected clock" without that
assertion is a sentence in a docstring. Verified to fail on a violation.

## [M4] `FrozenClock` ships in `src/`, not in the test tree
Chose: `service/clock.py` exports `Clock`, `SystemClock` and `FrozenClock`.
Reason: four milestones need to freeze the clock (M4's determinism gate, M5's `validated_at`, M6's
and M8's run timestamps), and a helper each suite reinvents is a helper each suite gets subtly
differently. It is nine lines and frozen/hashable so a suite can hold one as a module constant.
Alternative rejected: a fixture in `tests/conftest.py`. Same code, four milestones later, in a place
the Python client cannot reach.

## [M4] The `MCPServer` instance lives in `server/app.py`, re-exported from `server/__init__.py`
`docs/build-handoff.md` puts "the `MCPServer` instance and both transports" in
`server/__init__.py`, and that is where they are *exported* from.
Reason for defining them one module down: a tool module has to import the instance to decorate
against it, and a tool module importing its own package's `__init__` while that `__init__` is
importing the tool module is a partially-initialised-module cycle.
Alternative rejected: registering the tool modules at the bottom of `__init__.py` behind
`# noqa: E402`. It works, and swaps a clean two-module split for a lint suppression and an import
whose position is load-bearing.

## [M4] One module-level binding, not a `ContextVar` and not a server per store
Chose: `server/app.py` holds the bound `ServiceContext` in a module attribute; `bind()` sets it,
`bound()` reads it at call time, and `binding()` is a context manager tests use.
Reason: tools are registered at *import* time by a decorator on a module-level function, so a tool
cannot close over a store that does not exist yet.
Alternative rejected — a fresh `MCPServer` per store with the tools as closures inside
`register(mcp, context)`: removes the global at the cost of nesting thirteen tool functions inside
three registration functions, where a reader looking for `blueprint_upsert` finds it indented inside
something else.
Alternative rejected — a `ContextVar`: looks safer and is not. The SDK runs a sync tool through
`anyio.to_thread.run_sync` and the in-memory `Client` runs the server in a task it creates itself,
so whether a value set by a fixture is visible inside a tool depends on when the fixture ran
relative to the client's task group. A module attribute has no such question.
`bound()` raises `RuntimeError` when nothing is bound, deliberately **not** an envelope: an envelope
would tell a caller their request was wrong when the server is misconfigured, and would make every
tool answer plausibly while doing nothing.

## [M4] The MCP client is a helper, not a pytest fixture
Chose: `tests/toolclient.py` with `connected(context)` and `invoke(target, name, **arguments)`; a
session per call by default, and `connected` for the tests that want several calls on one session.
Reason: the obvious shape — an async generator fixture yielding an entered `Client` — **does not
work**. `Client.__aenter__` opens an `anyio` task group, an `anyio` cancel scope must be exited by
the task that entered it, and pytest-asyncio 1.4 runs an async fixture's setup and its finalizer
through two separate `runner.run(...)` calls. Every test taking such a fixture failed teardown with
`RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`.
A session per call is not a compromise: every M4 tool is a read or a single write against the store,
all state lives in the store rather than the session, so it is indistinguishable from a shared
session *and* it exercises the initialise handshake on every assertion.
`test_one_session_serves_many_calls` covers the shared-session path so it is not left untested.

## [M4] `sqlite_context` requires a path; there is no in-memory default
Chose: `sqlite_context(path)` with no default, and `--store` defaulting to `./agentprops.db`.
Reason: found by running it, not by reasoning about it. `sqlite_url(None)` produces
`sqlite+pysqlite://`, whose engine uses SQLAlchemy's per-thread pool — and an in-memory SQLite
database belongs to its *connection*. The SDK runs a synchronous tool through
`anyio.to_thread.run_sync`, so the schema created on the calling thread was invisible to the thread
the tool ran on and every tool answered `no such table: blueprints`. A default that silently cannot
work is worse than no default.
Alternative rejected: teaching `create_engine_for` to use a `StaticPool` for in-memory URLs. That is
a change to M3's signed-off adapter to enable a mode nothing needs — every test uses a `tmp_path`
file, which is the established pattern and the mode CI uses.

## [M4] `dataset_find` takes a `DatasetQuery`, and `paginate` owns the defaults
Chose: `datasets.find(context, query: DatasetQuery)`, with `paginate(query)` supplying `limit`
(default 50) and clamping both `limit` and `offset` to `>= 0`.
Reason: `DatasetQuery` is *defined* as `dataset_find`'s parameters (ruling R-05), so the tool
function has somewhere to put its seven parsed arguments without a second parallel shape — which is
also what keeps `dataset_find` under the 20-line budget. `DatasetQuery` carries no `ge` constraint
(R-04) and its own docstring says the service decides the defaults, so this is where they are.
Clamping rather than refusing, because the service never gates and a negative `LIMIT` is a
per-backend accident (SQLite reads `LIMIT -1` as "no limit", Postgres rejects it) rather than a
portable answer. `paginate` is public so the clamping has a test that needs no store, and so M7's
`dataset_export` inherits the same defaults instead of inventing its own.
Alternative rejected: seven keyword parameters on `find`. Correct, and 22 formatted lines in the tool
function against a 20-line rule — which would have been resolved by weakening the rule.

## [M4] `dataset_get` returns an archived dataset with a `dataset_archived` warning
Chose: `ok: true`, the full document, and contracts 3.4's existing warning code.
Reason: ground rule 3, in the one place it is most tempting to gate. The asymmetry with
`dataset_find` — which excludes archives — lives in the store and exists so a run holding a pin
keeps reading a dataset archived after it started. Reusing 3.4's code rather than inventing one: the
condition is identical, and R-22 lists it as part of the vocabulary already.

## [M4] `label_vocabulary` and `agent_list` count *discovery*; `store_status` counts *health*
Chose: the two label/agent tools count through `find_datasets` (archives excluded, one row per
lineage); `store_status` reports `Store.health()` (archives included).
Reason: ruling R-05 says the health count "includes archived datasets — it is a store-health number,
not a discovery number", and the other two are review surfaces built on the method that hides
archives. The two numbers can therefore disagree, which reads like a bug unless it is pinned:
`test_the_two_counting_numbers_disagree_about_an_archived_dataset` pins it.
`label_vocabulary` deliberately does not page — a count over the first 50 rows is not a count — so
it calls `find_datasets` with no limit. That is a full scan bounded by the datasets for one blueprint
version, which is authoring volume. The named remedy if it ever matters is a counting method on the
Protocol, not a page size here.

## [M4] Label and agent orderings follow declaration order, not a sort
Chose: `label_vocabulary`'s dimensions and values keep the blueprint's declaration order;
`agent_list`'s agents and versions keep `list_blueprints`'s `(agent_id, semver)` order.
Reason: both are deterministic — a JSON object preserves order through `json.loads` and through the
`LabelSchema` model — and declaration order is also the order a human wrote, which is what a
reviewer wants to read. Re-sorting versions in Python would put `1.10.0` before `1.9.0` and undo
ruling R-35 one layer up. Nothing in `service/` re-sorts or re-filters what the store returned; R-35,
R-36 and R-38 are all one `sorted()` away from being undone.

## [M4] Tool-surface drift guards, in the shape `test_validation_drift.py` established
Four guards in `tests/unit/test_tool_surface.py`, and the reasoning is the drift test's transferred:
a tool table kept in prose and a surface kept in code will diverge unless a test compares them.

- `test_every_registered_tool_is_documented` — the running server's names are a subset of what
  `docs/contracts.md` section 4 tabulates. `tests/catalogue.py` gained `parse_tool_names` for it.
- `test_every_documented_tool_is_registered_or_deferred` — the other direction, scoped by a
  `DEFERRED` map that names the milestone owning each of the fifteen unbuilt tools. M5 through M10
  land by *deleting* entries, and `test_no_deferral_is_stale` fails if a deferred tool appears on the
  surface anyway.
- `test_every_registered_tool_answers_an_envelope` — runtime and fully mechanical: arguments are
  synthesised from each tool's own published input schema, so a tool added at M5 is covered the
  moment it is registered, with no test change.
- `test_every_registered_tool_has_a_dedicated_test` — the M4 acceptance criterion, mechanically
  enforced. Reads the AST of `tests/` and collects every string literal passed to a
  `call_tool`/`invoke`/`attempt` call. Add a tool without a test and it fails; delete a tool's only
  test and it fails. Verified by simulation: adding `run_start` to the registered set reports exactly
  `['run_start']` as uncovered.

Two guards on the guards, both copied from the drift test's own defences:
`test_the_source_scanner_finds_something` blames the scanner rather than the suite on an empty parse,
and `test_the_scanner_does_not_credit_a_name_nobody_calls` asserts `dataset_skeleton` — named in
`DEFERRED` and in prose, called by nothing — is *not* credited, so a scanner matching more than tool
calls fails rather than passing forever.
Alternative rejected: a hand-kept list of exercised tools. That is the thing the brief and the drift
test both exist to avoid.

## [M4] The layering rule gains five AST guards, all verified to fail on a violation
`server/` may import `service/` and `models/` and nothing else sideways; a tool function stays under
20 lines *and* 12 statements; a tool function contains no control flow but the argument guard; a tool
function never raises; only `service/clock.py` reads a clock.
Reason: CLAUDE.md says review enforces the layering rule. Forbidding `storage/` and `validation/` in
`server/` forbids the *thing* rather than the symptom — a tool cannot validate a document or query a
store without importing the layer that does it — which is what makes "no business logic in
`server/`" enforceable rather than a matter of taste. It is also why
`service.context_from_url`/`sqlite_context` exist: `server/__main__.py` has to open a store, and
doing it through `service/` keeps the rule literally true instead of true with an exemption.
The size budget is measured in **both** physical lines and statements, because either number fails
alone: a formatter can split one statement across ten lines, and an author can pack ten statements
into ten lines.
**Verified rather than assumed.** A throwaway probe fed the guards a synthetic tool module with an
over-budget function, an `if`, a `for`, a `raise`, a `datetime.now()` and a `from agentprops.storage
import`; all five reported. A guard that has never failed proves nothing, which is the M3 lesson
applied to a test rather than to a docstring.

## [M4] `tests/envelopes.py`, so a narrowing assertion says which envelope it wanted
Chose: `data()`, `warnings_of()`, `findings()`, `rules()`, `codes()` — each asserting the envelope
kind on the way to the field.
Reason: `Reply` is `SuccessEnvelope | ErrorEnvelope` and only one member has `data`, which is correct
and which `mypy --strict` enforces on `tests/` too. Sprinkling `assert isinstance` at every call site
would satisfy the checker and say nothing; these turn the requirement into a better failure message —
`data(reply)` reports "expected a success envelope, got ok=False errors=[...]" where `reply.data[...]`
would have raised `AttributeError` with no explanation.
Alternative rejected: loosening `Reply`. The union is the honest type.

## [M4] `python -m agentprops.server` exists, and is not the CLI phase 1 rules out
Chose: a five-argument entry point — `--transport {stdio,http}`, `--store PATH`, `--host`, `--port`,
`--path`.
Reason: M4's deliverable is "`MCPServer` with stdio and streamable HTTP transports", and a transport
with no way to start it cannot be shown to work — the stdio evidence in
`tests/integration/test_transports.py` spawns exactly this. The non-goal is an *authoring* CLI, which
this is not.
`--store` is a SQLite **file path**, not a URL, deliberately: the Postgres and Mongo adapters land at
M7, and a URL argument that silently ran `create_schema` against a migrated Postgres database would
be worse than not having one. `service.context_from_url` is the seam M7 wires in.

## [M4] Both transports get their own evidence, in `tests/integration/`
Chose: `test_transports.py`, marked `integration`. stdio through a real subprocess driven by
`Client(StdioServerParameters(...))`; streamable HTTP by running `run_streamable_http_async` in a
background task on an ephemeral port and connecting a `Client` to the URL.
Reason: the in-memory `Client` proves the *tools* and proves nothing about either transport, because
it never touches one. Those are separate claims. It lives in `tests/integration/` because it is the
only place in the suite that spawns a process, and CLAUDE.md says no subprocesses in unit tests.
The port is claimed by binding a socket and releasing it — a narrow race, rather than a fixed number
that collides with whatever else is running. Asking the server which port it bound is not available:
`run_streamable_http_async` takes a port and returns nothing until it stops.
The readiness loop has **one** exit — it returns on the first successful session and otherwise runs
out of attempts and reports the last exception — per the M3 rule that a retry loop with more than one
exit is a smell.
The HTTP suite also calls a tool that returns a *failure* envelope (`AP-004`) and one that must never
fail (`blueprint_diff`), so the transport is not proved by one read.

## Questions for the owner — M4

1. **RETRACTED — answered by ruling R-41; see the fix-round-1 retraction below.**
   **`blueprint_diff` cannot report an entity-schema change.** contracts section 4 names four
   categories — nodes, edges, per-node schemas, label vocabulary — and none of them covers
   `entities[*].schema`, `entry_node`, `outcome_schema` or `description`. Ruling R-27 extended BP-011
   to validate entity schemas precisely because a malformed one publishes and surfaces a milestone
   later; a *changed* one is the same class of surprise, and this diff will not show it. Adding a
   fifth `entities` category is a small change; it was not taken because it exceeds the four
   categories the contract names.
2. **Is a boundary code the right home for "not found"?** `AP-004` puts `ok: false` on `dataset_get`
   for an unknown id. contracts section 1 blesses it ("a validation **or resolution** failure") and
   ground rule 3 is untouched — this is "there is no such row", not a policy verdict — but a caller
   that wanted `ok: true, data: {dataset: null}` for a miss would have to change the envelope.
3. **`dataset_find`'s default `limit` is 50.** Nothing specifies one and `DatasetQuery` carries no
   constraint. If the web app at M9 wants a different page size, this is the constant to move.

## [M4, fix round 1] Correction: the `paginate` entry reasoned about one end of a boundary and left the other unclamped
The `[M4] dataset_find takes a DatasetQuery, and paginate owns the defaults` entry above says
clamping happens "because the service never gates and a negative `LIMIT` is a per-backend accident
(SQLite reads `LIMIT -1` as 'no limit', Postgres rejects it) rather than a portable answer". Every
word of that is true **about the negative direction**, and the entry stops there. The upper end was
left unclamped and untested, and the residue was an exception escaping three tool paths.

`ArgReader.number` accepts any Python `int`, and Python integers are unbounded; every backend's
integer column is signed 64-bit. So an integer above `2**63 - 1` reached pysqlite, raised
`OverflowError`, and the MCP SDK turned that into `UnexpectedToolError` — `is_error: true`,
`structured_content: None`. That is precisely the shape CLAUDE.md's "structured errors, never
exceptions, for anything a user could cause" exists to prevent, and under ruling R-40 ("the envelope
contract binds the code we write") it is ours, because it happens inside a tool function's own call
graph rather than at the SDK layer. Reproduced against the in-memory client, and the boundary is
exact:

```
dataset_find  {"limit":  2**63 - 1}                  is_error=False   envelope
dataset_find  {"limit":  2**63}                      is_error=True    structured_content=None
dataset_find  {"offset": 10**30}                     is_error=True    structured_content=None
dataset_get   {"version": 10**30}                    is_error=True    structured_content=None
```

**Fixed in `service/`, not in `ArgReader`,** because the bound is a fact about what a row can hold
rather than about what a JSON request may say. New `service/limits.py` carries `MAX_STORED_INT`,
`clamp()` and `storable()`, and the split between those two functions is the decision worth
recording:

- a **quantity** (`limit`, `offset`, and M7's `count`) is clamped, because every value above the
  range means the same thing as the largest value inside it — `limit = 2**63 - 1` already means
  "every row there will ever be", so nothing is refused and no storable value is altered. The clamp
  is representability, not a policy cap, so ground rule 3 is untouched.
- an **identifier** (`version`, and M6's `iteration`) is *not* clamped: a version outside the range
  is not a large version, it is no version, so `dataset_get` reports `AP-004`. Clamping an
  identifier to the nearest representable one would answer a question the caller did not ask.

**And the rule this is the third instance of.** M3's two defects and this one have the same shape: a
confident `DECISIONS.md` entry with no test behind its residue. So, stated as a rule rather than as
an apology — **when an entry reasons about a boundary, test both ends of it.**
`test_paginate_clamps_at_the_int64_boundary` and
`test_get_reports_ap_004_for_a_version_outside_the_storable_range` each assert `2**63 - 1` *and*
`2**63`, and all five new tests were verified to fail against the pre-fix code:

```
FAILED test_service_datasets.py::test_paginate_clamps_at_the_int64_boundary
FAILED test_service_datasets.py::test_get_reports_ap_004_for_a_version_outside_the_storable_range
FAILED test_service_datasets.py::test_find_survives_an_out_of_range_limit_and_returns_rows
FAILED test_tools_contract.py::test_an_out_of_range_integer_is_an_envelope_and_not_an_exception
FAILED test_tools_contract.py::test_an_out_of_range_limit_still_returns_every_row
```

Every other malformed-argument probe the review ran already returned a proper envelope — a
100k-character `agent_id`, empty version strings, NUL bytes in ids, 2000 unbalanced braces in
`dataset_json`, a self-referential `$ref`, a negative `version`, `10**40` in `max_iterations`. So
this was narrow rather than systemic, and the narrowness is why it survived: the one integer that
reached a *column* was the one nobody probed.

## [M4, fix round 1] Retraction: `blueprint_diff` has a fifth category (ruling R-41)
**The `[M4] The blueprint_diff payload shape` entry above is superseded in one respect, and its
"Deliberately **not** in the diff" paragraph is retracted.** That paragraph reads:

> Deliberately **not** in the diff, because none of the four categories covers it: `entry_node`,
> `outcome_schema`, `description` and the **entity schemas**. See "Questions for the owner" below.

**Owner question 1 in the "Questions for the owner — M4" section is likewise retracted**: it asked
whether the blind spot should be closed, and ruling R-41 answers yes. Both are left standing above
with this retraction rather than silently edited, so the reasoning that produced the wrong shape
stays readable — the same treatment M3's fix round 2 gave its own correction.

R-41's reasoning, which is better than the reasoning it replaces: `blueprint_diff` exists to answer
"what changed between these two versions", and R-27 had just made entity schemas a *validated* part
of a blueprint. A silent blind spot over a field the validator now polices is worse than a small
scope increase, and the increase really is small — the category is additive, no caller breaks by
receiving more detail, and it inherits totality and "never a failure signal" for free because
nothing in `diff.py` can raise.

Chose, for the shape: a fifth key `"blueprint"` with two halves.

```jsonc
"blueprint": {
  "changes": [{"field": "entry_node", "from": "receive_request", "to": "intake"}],
  "entities": {
    "added":   ["franchisee"],
    "removed": [],
    "changed": [{"entity_id": "store", "field": "schema", "from": { }, "to": { }}]
  }
}
```

Both halves **mirror shapes already in the payload** rather than inventing a third convention:
`changes` is the same `{field, from, to}` triple `nodes.changed` uses, produced by the same
`_field_changes` helper so the two cannot drift into two spellings of one thing, and `entities` is
the same added/removed/changed split `nodes` uses with `_entities_by_id` mirroring `_nodes_by_id`.
The `changed` entry carries `field: "schema"` the way a `schemas` entry does; an entity has exactly
two fields — its id, which is the identity, and its schema — so `schema` is the only field that can
appear today, and naming it keeps the entry readable if `EntitySchema` ever gains a second.

`outcome_schema` sits in `blueprint.changes` rather than in `schemas` because that category is
documented as *per-node* and this schema belongs to no node.
Alternative rejected: a sixth top-level `entities` key alongside `nodes` and `edges`. It reads
symmetrically and splits one answer ("what changed about the blueprint itself?") across two places.

**Four things moved with the code, and three of them were record rather than code** — landing the
implementation and leaving these would have left a future reader finding the code and the record in
direct disagreement, which is the failure mode this file exists to prevent:

1. `tests/unit/test_tools_contract.py`'s `assert set(diff) == {...}`.
2. `docs/contracts.md`'s `blueprint_diff` row, which named four categories twice.
3. `service/diff.py`'s "What is deliberately *not* in the diff" section, now "What is *still* not in
   the diff" — and the answer is "nothing structural".
4. This retraction, and owner question 1.

**A new guard, because the original gap was invisible.** Four categories left four fields
unreportable and no test failed.
`test_service_diff.py::test_every_blueprint_field_is_covered_by_some_category` walks
`Blueprint.model_fields` and asserts each one is either the diff's own subject (`agent_id`,
`version`), a category of its own (`nodes`, `edges`, `label_schema`, `entities`), reported in the
per-side envelope (`status`), or named in `BLUEPRINT_FIELDS`. A field added to the model in future
fails it. That is what should have existed the first time.

## [M4, fix round 1] `not_found` takes the pointer field by name
Chose: `not_found(what, *, field, **context)`.
Reason: it was `next(iter(context), "id")` — the first key of the context kwargs. Correct for both
callers, and correct by *luck*: `not_found("dataset", version=..., dataset_id=...)` would have
pointed at `/version`, and a pointer that depends on kwarg insertion order is a bug waiting for the
first person who writes the arguments in the other order.
`test_not_found_takes_the_pointer_field_by_name_not_by_kwarg_order` asserts the property directly
with the kwargs reversed, so the old implementation fails it.

## [M4, fix round 1] The every-tool envelope assertion encodes the real invariant, not the strict one
`test_tool_surface.py` asserted `set(payload) == {"ok", "data", "warnings"}` whenever `ok` was true.
That is **false as a contract**: `blueprint_validate` and `dataset_validate` return `{ok, errors}`
with `ok: true` on a clean document, which ruling R-13 requires and `ErrorEnvelope`'s own docstring
sanctions. It passed only because `sample()` synthesises arguments that make both of those tools
fail, so the branch was never taken. Nothing was broken; a reader at M5 or M6 would have taken the
assertion for the contract and been wrong.
Chose: assert the payload is one of the two envelopes, then per shape — a `{ok, data, warnings}`
payload must have `ok: true` and exactly one key under `data`; an `{ok, errors}` payload with
`ok: true` must come from a tool in `VALIDATE_TOOLS` and carry only warning-severity items; one with
`ok: false` must carry at least one error-severity item. The last two halves are ruling R-13 in both
directions, which nothing had asserted anywhere.
`VALIDATE_TOOLS` is a named two-element set tied to contracts section 4, with
`test_the_validate_tools_are_registered` keeping it from going stale.

## [M4, fix round 1] Note for M9: `agent_list` is an N+1
`service/admin.py`'s `agents()` issues one unlimited `find_datasets` per agent, and each of those is
a full lineage scan. Not fixed now, and recorded so it is not rediscovered.
Ruling R-42(c) ratified `label_vocabulary`'s unlimited scan because its size is bounded by the
**blueprint** — the dimensions an author declared. This one is not: it is bounded by agents ×
datasets, which is a different class of number. It is fine at authoring volume and it is behind an
admin tool, but M9 will put it behind a page load, which is where it will first be noticed.
The named remedy, when it matters: a counting method on the `Store` Protocol — one grouped query per
backend — not a page size on a tool whose whole answer is a count. Same shape as the remedy R-39(a)
records for the `q` filter, and the same trigger: a measured problem, not a suspected one.

## [M4, fix round 1] `docs/contracts.md` is excluded from `ruff format` for a reason, and passing it explicitly defeats that
Recorded as a hazard rather than a decision, because it cost a revert. `pyproject.toml` sets
`extend-exclude = ["docs", ".superpowers"]` with a comment explaining why: `docs/` holds the frozen
specification and `worked-example.md` is the byte-exact source for `tests/fixtures/`. But an
**explicitly named** path overrides an exclusion, so `uv run ruff format tests/ docs` reformatted
three fenced Python blocks in `contracts.md` and one in `worked-example.md` — reflowing a `Protocol`
signature and collapsing aligned trailing comments in the document of record.
Caught by reading `git diff` before committing, and reverted. The lesson is narrow and worth having
written down: **run `uv run ruff format` with no path argument.** The configured excludes are the
whole point, and naming a path is how you skip them.

## [M5] The `skeletons` table gains `labels` and `seed`, and contracts section 7 is amended
`dataset_skeleton(agent_id, version, labels, seed)` takes four inputs; ruling R-06 settles that
`labels` and `seed` are **inputs rather than fillable sections**; and `dataset_submit(skeleton_id)`
takes no other argument. So the two values have to survive on the skeleton row or the dataset cannot
be assembled at all — they exist nowhere else and cannot be derived from anything.
Chose: add `labels JSONB NOT NULL` and `seed BIGINT NOT NULL` to the `skeletons` DDL, to
`Skeleton`, to the SQLite adapter and to migration `0001`, and amend `docs/contracts.md` section 7
with the reason at the columns.
Reason: this is the same class of omission as ruling R-32's missing `runs.declared_bp_version` and
R-05's six missing Protocol types — a DDL refinement that lost a field the tool contract requires,
not a deliberate exclusion. The column types match `datasets`, so a skeleton and the dataset it
becomes agree about both values.
Alternatives rejected: **a reserved key inside `parts`** — `parts` is documented as "section id to
the content filled for it", and `filled`/`remaining` are derived from its keys, so a non-section key
would have to be filtered at every use and would break the first author who forgot; **making the
LLM fill them as part of a section** — R-06 forbids it and it would let the filling model change the
labels the caller asked for.
Migration `0001` is amended rather than superseded, on ruling R-39(c)'s reasoning exactly: this is
the initial schema of an unreleased milestone on an unmerged branch, and no database outside a
temporary test file has ever been migrated by it. **Migrations become append-only the moment this
branch merges.**

## [M5] The section manifest is data, and it partitions the dataset's top-level fields
Ruling R-06 fixes five sections in order and assigns `narrative` to `provenance` and `pools` to
`nodes.branches`. `service/skeletons.py::SECTION_FIELDS` is that ruling as a table: each section
owns whole **top-level fields of the dataset document**, and the five sections' fields partition
every authored field. `nodes.core` owns `/nodes`; `nodes.branches` owns `/pools`.
Reason: that partition is not a new decision. `validation/pointers.py::section_for_pointer`, written
at M2, already maps `/nodes/<non-pool id>` to `nodes.core` and `/pools/...` to `nodes.branches`, so
a `DS-*` finding's `section` and the manifest agree **by construction** rather than by coincidence.
`test_every_manifest_pointer_maps_back_to_its_section` is the assertion that keeps them tied; it is
the only place in the suite that compares the two.
R-06's "alongside the loop node's own fixture" resolves to exactly this: a `pool: true` node's
fixtures live in `pools` (ruling R-01), so the loop node's fixture **is** the `nodes.branches`
content, and the golden dataset's `request_docs` appears in `pools` and nowhere else.
Consequence, and the reason the partition is worth this much care: filling the five sections
reassembles `priya-missing-docs.json` **exactly**, except for `id`. That is acceptance criterion 6.

## [M5] A section's `content` is a fragment of the dataset document, keyed at the top level
`dataset_fill_part("nodes.core", {"nodes": {...}})`, not `dataset_fill_part("nodes.core", {...})`.
Reason: an RFC 6901 pointer into the content is then already a pointer into the assembled dataset,
so SK-003's findings and every `DS-*` finding address the same places and nothing has to translate
between two coordinate systems. It also makes the fill sequence derivable from the manifest alone —
a caller slices its own document by `section["pointers"]`, which is exactly what
`test_worked_example_fill.py` does and what M8's client will do.
The cost is one level of nesting on a section with a single field (`{"entities": {...}}`), and one
rule to learn instead of five shapes.
Alternative rejected: the bare value, with the service knowing which field each section means. That
puts the mapping in two places (the manifest's `pointers` and an unwritten convention) and makes a
two-field section — `provenance`, which owns `narrative` too — unexpressible.

## [M5] A re-fill replaces the whole section
Ruling R-06 makes re-filling explicitly legal; it does not say whether a re-fill merges.
Chose: replace.
Reason: PRD 6 flow B repairs "one part", and the part is the section. Merging would mean a caller
cannot *remove* a field it should not have written, and the stored state would depend on the order
of two calls rather than on the last one. Replacement also makes the `{filled, remaining}` response
a complete description of the state, which a merge would not.
Consequence, recorded because it is the obvious complaint: repairing a blank `provenance.title`
means re-sending the whole `provenance` section. That is one section out of five, which is the
granularity the ruling and the PRD both chose.

## [M5] `dataset_fill_part` validates the section at *type* level only, one field deep
The brief says the fill "validates it locally at schema level". That could mean parsing the section
into its Pydantic models, and it must not.
Chose: `content` must be an object, every key must be one the section owns, and each present field
must have the JSON type the `Dataset` model declares for it (`narrative` a string, the rest
objects). Nothing deeper.
Reason: **ruling R-45 requires DS-025 to be reachable for a filled provenance whose title is absent
or blank.** A model-level parse at fill time would reject the absent case as `AP-003` and the rule
id a ruling explicitly assigns to a rule would become unreachable through the tool surface. The same
argument covers DS-021 (`narrative` present, ≥30 characters) and DS-033.
So the boundary is: fill time owns the shape of the *request*, submit time owns the content of the
*document*. `test_a_missing_owned_field_is_accepted_and_becomes_a_ds_rule_at_submit` is that line as
an assertion.
An unowned key is `AP-001` rather than a rule id, because no `SK-*` rule describes the shape of
`content` and section 3.5 exists for exactly the failures a user can cause that no catalogue rule
covers (ruling R-43a).

## [M5] The `SK-*` rules run in two phases, and the phase lists are drift-guarded
SK-001 to SK-003 read a section name and its content, which a submit does not have; SK-004 only
means anything at submit; SK-005 guards both.
Chose: two runners in `validation/__init__.py` — `validate_fill` and `validate_submit` — driven by
`FILL_RULES` and `SUBMIT_RULES` in `validation/skeleton.py`, with
`test_every_skeleton_rule_runs_in_exactly_one_phase` asserting their union is exactly the `SK-*`
registry and their intersection is exactly `{SK-005}`.
Reason: a rule that is documented, registered and never run is worse than a missing rule, because
the drift test reports it as covered. The union assertion is what makes adding a sixth skeleton rule
without wiring it a failure.
Alternative rejected: one runner plus a phase check inside each rule body. That hides the same
decision in five places and puts a gate inside a predicate.
Both runners **stop at the first rule that reports**, unlike `validate_dataset`, which runs every
rule. A dataset document is complete and its findings are independently actionable; a fill request
is a single action, and SK-001's finding removes the ordinal SK-002 would need. The difference is
stated in both docstrings.

## [M5] The `SK-*` precedence, and where ruling R-45's half of it lives
Four skips, each inside the lower-priority rule so the rules stay order-free (ruling R-26's shape):
- **SK-001 before SK-002.** A section outside the manifest has no ordinal, so "is every
  earlier-ordinal section filled" has no answer.
- **SK-002 before SK-003.** SK-003 resolves node `entity_refs` against the *filled* `entities`
  section; `entities` is earlier-ordinal than both node sections, so when it is unfilled SK-002
  already fires and SK-003 would otherwise add one finding per reference.
- **SK-005 before everything**, on both tools. A skeleton that does not exist has no manifest.
- **SK-004 before every `DS-*` rule** — ruling R-45. This one is in `service/skeletons.py::submit`
  rather than in a rule body, because it decides whether the dataset catalogue runs *at all*: an
  unfilled required section returns SK-004 findings and the catalogue is never invoked. R-45's
  reason is the right one: "otherwise a submit of an empty skeleton would report most of the
  catalogue", and the finding that says what to do would be buried in it.
SK-003 checks the `entity_id` segment only, never `@revision`, matching the DS-006/DS-009 split
ruling R-18 makes. If it did otherwise, a fill would reject a reference the submit accepts.
SK-002 reports **one** finding, scoped to the earliest unfilled predecessor, with the full blocking
list in `context`. Reporting one finding per predecessor would scope four findings to four different
sections for a single mistake, and `section` is meant to name the part to repair.

## [M5] SK-005 owns an unknown `skeleton_id`, not `AP-004`
Chose: SK-005 for both halves — the skeleton does not exist, and the skeleton was already submitted.
Reason: the catalogue names the check ("`skeleton_id` exists and has not already been submitted"), so
a caller learns one vocabulary for one condition. `AP-004` exists for ids no rule describes; this one
is described.
It guards **both** tools. A submitted skeleton is frozen — datasets are immutable (ground rule 5) —
so filling another part of one could only mislead, and `test_a_fill_after_a_submit_is_sk_005` pins
it.

## [M5] The skeleton id is derived from the request, and a re-request resumes rather than overwrites
Ruling R-10 mints skeleton ids from `Seeded.uuid()`, so an id is a pure function of the request and
two identical `dataset_skeleton` calls derive the **same** id. Left alone, the second call's
`put_skeleton` would silently discard every filled section.
Chose: `_claim()` walks generations `0..63`, deriving one candidate id per generation, and stops at
the first candidate that is **free or unsubmitted**. A free id is used; an unsubmitted skeleton is
returned as-is, with its parts intact and **without a write**; a submitted one moves to the next
generation.
Reason: it makes the derived id safe in both directions. An LLM that lost its `skeleton_id` mid-fill
resumes instead of starting over, and a second dataset can still be authored from one label
combination once the first is submitted. Not writing on resume also means a re-request cannot race
with a fill in progress.
One loop with **one** exit predicate — *free or unsubmitted* — deliberately, because the `seq`
allocation defect at M3 came from two conditions that had to agree.
Both ends are tested: `test_a_second_request_with_identical_arguments_resumes_the_same_skeleton`
and `test_a_request_after_a_submit_mints_a_new_skeleton`.
The bound: `SKELETON_GENERATIONS = 64`, so one `(agent_id, version, labels, seed)` can author 64
datasets before the caller varies an argument. The `Store` Protocol offers no way to *enumerate*
skeletons — `get_skeleton` and nothing else — so probing by derived id is the only mechanism
available, and an unbounded probe would be a worse answer than a stated limit. Exhaustion returns
`AP-001` pointed at `/seed`, which is the argument to change;
`test_running_out_of_generations_is_reported_rather_than_overwriting` monkeypatches the bound to 1
so the branch is reachable and asserts the submitted skeleton's lineage survives.
`AP-001` is the closest existing code rather than a good fit. A sixth `AP-*` code would be better
and would mean amending contracts section 3.5, which M5 was not asked to do — recorded here rather
than done quietly.

## [M5] The dataset id is derived from the *skeleton*, never from the request
`Seeded(seed).uuid(f"dataset:{skeleton_id}")`.
Reason: deriving it from `(agent_id, version, labels, seed)` would give two skeletons authored from
one label combination the **same** dataset id, and `put_dataset` would then file the second as
*version 2 of the first* — two datasets silently collapsed into one lineage, which no rule would
report and which `dataset_find` (one row per lineage) would hide.
`test_the_dataset_id_is_derived_from_the_skeleton_not_the_request` asserts two distinct ids, both at
version 1.

## [M5] `Seeded.uuid` derivation: blake2b over length-prefixed components
`hashlib.blake2b(_encode(str(seed), self.salt, salt), digest_size=16, person=b"agentprops:uuid")`,
then version-4 and RFC 4122 variant bits stamped over the digest.
Three choices inside that, each with a failure it avoids:
- **`hashlib`, not `hash()`.** Python's built-in `hash()` is randomised per process by
  `PYTHONHASHSEED`, so a `hash()`-derived id would differ between two runs of identical code — the
  exact failure determinism exists to prevent, and one no single-process test suite would catch.
  `test_the_derivation_is_stable_across_processes` runs the derivation in three subprocesses with
  different `PYTHONHASHSEED` values.
- **Length-prefixed components, not a separator.** With a separator, `("a:b", "c")` and
  `("a", "b:c")` encode identically, so two different salts mint one id and `put_skeleton`
  overwrites one skeleton with another. A four-byte big-endian length in front of each UTF-8
  component is injective for every tuple of strings, including empty ones and ones containing the
  separator. `test_the_salt_encoding_is_injective` carries the colliding pairs.
- **`person=` for domain separation**, so a future `Seeded` method hashing the same `(seed, salt)`
  for another purpose cannot produce a value that collides with an id.
The signature keeps both salts: `Seeded(seed, salt="")` from contracts section 9, and `uuid(salt)`
from ruling R-10. They are distinct components of the derivation, so `Seeded(s, "a").uuid("")` and
`Seeded(s).uuid("a")` are different ids rather than an accidental collision. The constructor salt
defaults to empty so a caller with nothing to scope by writes `Seeded(seed)`.
Ruling R-46's requirement is met by `test_the_derivation_is_pinned`, with **literal** expected
UUIDs: a round trip through the implementation would pass against any derivation at all. Changing
`_encode` or `uuid` changes every id this service has ever minted, and the test docstring says so.
Only `uuid()` is built. R-46: `int()`, `choice()`, `shuffled()` and `timestamp()` arrive at M7 with
the expansion that needs them, because an unused generator method is untested surface.

## [M5] The scaffold: keys are blueprint facts, leaves are empty, and no placeholder may validate
"How much scaffolding does `dataset_skeleton` pre-fill" comes down to two rules.
**Keys are blueprint facts.** The non-pool node ids, the pool node ids and the entity ids are all
knowable from the published blueprint, and they are exactly what an author has to enumerate —
DS-002 wants a fixture per non-pool node, DS-018 an entry per pool node. So they are emitted as
keys, in blueprint declaration order, along with the three fields no section owns (`blueprint`,
`seed`, `labels`) at their real values.
**No placeholder may validate if it is left unedited.** An empty fixture `{}` reports DS-004, an
empty pool reports DS-019, an empty entity fails to construct. A more helpful-looking placeholder —
`{"base": {}}`, or `{"entity_refs": []}` — would pass every rule and store a nonsense dataset, which
is strictly worse than an error. `test_no_scaffold_placeholder_would_validate_if_left_unedited`
submits the scaffold verbatim and asserts it is rejected with rule ids.
Field-level guidance therefore lives in each section's `description`, interpolated from the
blueprint (entity ids, node ids, the pool's `max_iterations`), which is prose's job. A filled
section shows its stored content instead of its placeholder, so a resumed skeleton is a live view of
the work so far.
Residue, named: a section deliberately filled with an empty object is indistinguishable in the
scaffold from an unfilled one. `dataset_fill_part`'s `{filled, remaining}` is the authoritative fill
state and is where the difference is visible. Adding `filled`/`remaining` to `dataset_skeleton`'s
payload was rejected as a deviation from the four keys contracts section 4 names.

## [M5] The `instructions` string is composed from the manifest, and says only what a rule enforces
Generated from `manifest` and `SECTION_FIELDS` rather than written out, so renaming a section
renames it here too and it cannot describe sections that do not exist.
Content, and why each line is in it: the fill **order** (SK-002); that a re-fill is allowed and is
how a rejection is repaired (ruling R-06, PRD 6 flow B); the **keyed** content form, with each
section's fields listed; that a re-fill replaces the section; that `blueprint`, `seed` and `labels`
are not fillable (R-06) **and that `label_vocabulary` is the pre-flight for them**; and that submit
runs the cross-cutting pass and a skeleton becomes one dataset (SK-005).
No timestamp, no counter, no clock reading, so two calls for one blueprint version return
byte-identical text — which is M4's determinism criterion applied to M5's read-shaped tool, and
`test_dataset_skeleton_output_is_byte_identical_across_repeated_calls` asserts it over the whole
envelope.

## [M5] `dataset_skeleton` does not validate `labels`, and that is a choice
A label outside the blueprint's vocabulary, or an incomplete label set, is **not repairable by
re-filling** — R-06 makes labels an input, so the caller has to start a new skeleton. Failing fast
at `dataset_skeleton` is therefore tempting.
Chose: no label validation at skeleton time. DS-012 and DS-024 own labels, at submit, against the
assembled document.
Reason: M5's acceptance criterion says "a submit carrying partial labels is rejected with DS-024".
Pre-empting DS-024 at skeleton time would make that criterion unreachable through the tool surface,
and a rule that cannot fire on the normal path is a rule the corpus tests and the product does not.
`label_vocabulary` — M4's tool — already answers "which values does this blueprint declare", so the
pre-flight surface exists; `instructions_for` points at it, and that is the mitigation.
Alternative rejected: running DS-012 only, keeping DS-024 for submit. That splits one field's
validation across two moments for no gain in guidance.

## [M5] `submit` writes the dataset first, then marks the skeleton
**RETRACTED — ruling R-47 reverses this order. See "[M5, fix round 1] Correction: the write order
is reversed, and the crash residue flips with it" below.** The reasoning recorded here was sound
about crash recovery and wrong about what it was trading against: it weighed one residue against
another without noticing that only one of them was *silent*.

~~Order matters only for a crash between the two writes.~~
~~Chose: `put_dataset`, then `mark_skeleton_submitted`.~~
~~Reason: a crash then leaves a stored, validated dataset and an unmarked skeleton — visible, and
recoverable. The reverse leaves a skeleton SK-005 has closed and no dataset, and the filled work is
unreachable through any tool. It is also the honest order: you cannot record what a skeleton became
until it became it.~~

## [M5] Concurrency: two claims, both with the losing side tested
**PARTLY SUPERSEDED — ruling R-47 closes the submit half. The fill half stands, ratified by ruling
R-48.** The two tests named below still exist and still test the losing side; the submit one now
asserts the opposite outcome, which is what its own "if this changes, a CAS has landed" message
asked for. See "[M5, fix round 1] Ruling R-47: `mark_skeleton_submitted` becomes a compare-and-set"
below for what replaced it, including why the trigger this entry named was the wrong one.
`skeleton_id` is caller-supplied and the partial state is mutable across calls, so both of these are
tested rather than asserted in a docstring.
**A concurrent fill of another section is lost.** `dataset_fill_part` is a read-modify-write of the
whole `parts` object, because `put_skeleton` is a whole-aggregate upsert and the `Store` Protocol has
no compare-and-set. Two fills that both read before either writes end with only the second one's
section. `test_a_concurrent_fill_of_another_section_is_lost` replays that interleaving and asserts
the loss.
**Two interleaved submits both write, and the second becomes version 2.** Sequentially a second
submit is SK-005 and writes nothing — `test_a_second_submit_is_sk_005_and_writes_nothing` proves it,
including that the stored dataset is still at version 1. Concurrently it is **not** closed: both
calls read `submitted_as` as null before either writes, so both pass SK-005 and both reach
`put_dataset`, which allocates version 2 for the loser. The dataset id is derived from the skeleton,
so `mark_skeleton_submitted` sees the same id twice and its replay tolerance accepts it.
`test_two_interleaved_submits_both_write_and_the_second_becomes_version_two` is that case.
Not fixed now, and the reason is a cost, not a judgement that it cannot happen: closing it needs a
compare-and-set on the skeleton — `mark_skeleton_submitted` dropping its replay tolerance, or a
`claim_skeleton` method — which is a `Store` Protocol change that three adapters pay for and that
M7's conformance suite would have to cover on all three. An authoring session is one caller filling
one skeleton in sequence.
**The named remedy, with its trigger:** add `claim_skeleton(skeleton_id, dataset_id) -> bool` to the
Protocol, implemented as a conditional update (`WHERE submitted_as IS NULL`), and have `submit` call
it *before* `put_dataset`. Build it when a second writer exists — the web app at M9 is the first
plausible one. Both tests carry a message telling the next author to update this entry when they
land it.

## [M5] The assembled document is emitted in one canonical key order
`DATASET_FIELD_ORDER` — the golden fixture's own order — is applied to every assembled document.
Reason: dict equality ignores order, so nothing depends on it *today*. But five `dataset_fill_part`
calls can arrive in any order and the parts are stored as they arrive, so without this the stored
document's key order is a function of call order. M7's `dataset_export` gate is byte stability
across an export/import cycle, and one canonical order in the single write path is cheaper than a
canonicalising export.
`_ordered` keeps any unexpected field rather than dropping it: an unowned field is `AP-001` at fill
time, but ordering must never be the thing that silently discards a value, because a value dropped
there is a value no rule could report.

## [M5] The `SK-*` coverage exemption is a third kind, and it names its test
The corpus is a list of JSON Patches against two golden **documents**. A skeleton rule's subject is
the `parts`/`manifest`/`submitted_as` state of a skeleton row, so **no mutation can reach one**.
Chose: `PIPELINE_ONLY` in `test_validation_drift.py`, keyed by rule id and valued by the *name* of
the covering test, subtracted from the coverage gate, with
`test_every_pipeline_exemption_names_a_test_that_exists` and `test_no_pipeline_exemption_is_stale`
holding it up. This is ruling R-12's "corpus coverage or a **named** exemption", and it is the shape
ruling R-31 established for a rule *half*: an exemption is worth exactly what the test it names is
worth.
It is deliberately a third map rather than an entry in either existing one. `MUTATION_UNREACHABLE`
requires a raw-text case (R-20's DS-013), which a skeleton rule has no analogue of;
`RULE_HALF_EXEMPTIONS` is keyed by `(rule, half)` and asserts the rule *does* have a corpus case.
Alternative rejected: a second, differently-shaped mutation manifest carrying skeleton fixtures.
Five rules is not enough to justify a parallel corpus mechanism, and the state those rules read is
exactly what the service builds anyway — which is what `test_service_skeletons.py` exercises.

## [M5] Ruling R-44's fixture drift guard, and why it normalises line endings
`tests/unit/test_fixtures.py` now extracts the fenced JSON blocks from `docs/worked-example.md`
sections 3 and 4 and compares them with the two committed golden fixtures. Before this, nothing in
the suite pinned the fixtures to the document that declares itself their source: the byte-diff had
been done once, by hand, and never committed.
It covers **exactly two** files and the exclusions are stated at the assertion, in
`test_the_drift_guard_covers_exactly_the_two_byte_exact_fixtures`: `broken/manifest.json` was
deliberately extended past section 6's cases by ruling R-14 and is *supposed* to differ, and
`datasets/arun-escalated.json` was authored from section 5's prose and has no JSON block to compare
against. A test asserts the two maps are exhaustive over the four committed fixtures, so a new
fixture has to be classified.
**One concession, and it is git's doing.** Measured: on this Windows checkout with
`core.autocrlf=true`, the worktree copy of `worked-example.md` is CRLF while the two golden fixtures
are LF — even though the index stores all three as LF, so *in the repository* they are byte
identical. Comparing raw worktree bytes would fail on a fact about the checkout, on one platform,
for every reader. So both sides are read through `Path.read_text` (universal newlines) and the
docstring carries the measurement. Content, whitespace, indentation, key order and number formatting
are all still compared exactly; only the line terminator, which git owns and the specification does
not, is excluded.
Verified by making it fail: changing `"seed": 20260908` to `20260909` in the document's section 4
block reported drift on `priya-missing-docs.json`, and the document was restored byte-identically.

## [M5] `expansion/` gets the layering guards `validation/` and `storage/` already had
Three parametrised guards in `test_layering.py`: no sibling-layer imports but `models/`, no I/O, and
no clock or random source.
Reason: ground rule 9 and contracts section 9 both ask for it ("enforce with a ruff custom rule or a
test that greps the tree"), and this is the one layer where the module's whole purpose would be
defeated by a single call — "all randomness flows through `Seeded`" means nothing if `Seeded`
contains randomness. `uuid.UUID(bytes=...)` is not caught and should not be: ruling R-10 bans
*calls* to `uuid4()` and friends, not the type and the parser.
`test_the_expansion_guard_would_catch_a_random_source` feeds the guard a module that calls
`random.choice`, `uuid.uuid4` and `datetime.now` and asserts all three are reported — the same
verify-the-guard step M4 ran on its five layering guards, because a guard that has never been shown
to fail is a guard nobody has tested.

## [M5] `ArgReader` gains three readers, and `RequiredIntArg` exists so the schema stays honest
`integer` (a required integer), `mapping` (a required JSON object) and `required_labels` (a required
`{dimension: value}` object of strings), plus a `RequiredIntArg` annotation publishing
`{"type": "integer"}` rather than `["integer", "null"]`.
Reason: `dataset_skeleton`'s `seed` and `labels` and `dataset_fill_part`'s `content` have no
defaults, so an omitted argument must be `AP-001`. The existing `number()` returns `None` for an
omitted optional argument and **`0` is a legitimate seed**, so "absent" and "zero" cannot share a
return value. `integer` also rejects `True`, because `isinstance(True, int)` is true in Python and
the same trap already cost a rule (`validation.context.as_int` excludes `bool` so DS-020 does not
accept `"seed": true`) — the two layers have to agree about what an integer is.
`required_labels` treats an **empty** labels object as a meaningful value rather than an absence: it
is a dataset with no labels, which DS-024 rejects at submit with a rule id, which is better guidance
than a boundary code.
`mapping` is distinct from `document`, which passes a document argument through untouched for
ruling R-20's raw-text seam. A section fragment is not a document: no rule reads its raw text, and
DS-013 cannot apply because `labels` is not a fillable section.

## [M5] The negative control in `test_tool_surface.py` moves to a tool that will never land
`test_the_scanner_does_not_credit_a_name_nobody_calls` used `dataset_skeleton` as its example of a
name that appears as a string literal under `tests/` while never being called. M5 landed that tool,
so the control had to move — which is the flaw in choosing a deferred tool that will one day exist.
Chose: `blueprint_infer`, which contracts section 4 tags *(phase 1.5)* and says is "Not in phase 1",
plus a first assertion that the control name **does** appear as a string literal under `tests/`. A
control that stopped appearing would make the test pass vacuously, which is the failure mode a
control exists to prevent.

## Questions for the owner — M5

1. **RATIFIED by ruling R-49(a).** **`skeletons.labels` and `skeletons.seed` are an addition to
   contracts section 7.** They are
   forced rather than chosen — `dataset_submit(skeleton_id)` takes no other argument and ruling R-06
   makes both values non-section inputs, so a skeleton that does not carry them cannot be assembled
   into a dataset. Same class as ruling R-32's missing `runs.declared_bp_version`. Flagged because it
   is a schema change, not a code choice.
2. **OPEN. How many datasets should one `(agent_id, version, labels, seed)` be able to author?** The
   answer today is 64, because skeleton ids are derived (ruling R-10) and the `Store` Protocol can
   only *probe* for one, never enumerate. Exhaustion is an `AP-001` telling the caller to vary the
   seed. If the intended answer is "one" — i.e. a repeat request should be refused rather than
   walking to a fresh generation — the walk becomes a single probe.
3. **ANSWERED by ruling R-49(b) — `AP-006` exists now.** ~~Should exhausting the generation walk
   have its own boundary code? It is reported as `AP-001` pointed at `/seed`, which is the closest
   existing code rather than a good fit: nothing is malformed, the arguments simply cannot be
   served. A sixth `AP-*` code would be the honest answer and would mean amending contracts section
   3.5, which M5 was not asked to do.~~
4. **ANSWERED by ruling R-47 — the CAS is built.** ~~Concurrent `dataset_submit` on one skeleton is
   not closed. SK-005 refuses a *sequential* second submit and writes nothing; two truly
   simultaneous submits both pass it and the loser becomes version 2 of the same dataset. Both cases
   are tested. Closing it needs a compare-and-set on the skeleton — a `Store` Protocol change three
   adapters pay for — and the named remedy plus its trigger are in the concurrency entry above.
   Worth an explicit decision before M9 puts a second writer on the store.~~ The answer to "worth an
   explicit decision *before M9*" was no: **before M7**, because M7 implements this Protocol against
   two more backends and M9 arrives after it.
5. **RATIFIED by ruling R-49(c).** **A blueprint with no `pool: true` node still has to fill
   `nodes.branches` with `{"pools": {}}`.**
   All five sections are `required: True`, because the `Dataset` model requires all their fields and
   the alternative is the service inventing an empty container. One extra call per dataset for such
   a blueprint; the golden blueprint has a pool node, so nothing in this build pays it.

## [M5, fix round 1] Ruling R-50: every integer that can reach a store column is bounded, and a guard says so
**This was the second milestone running whose blocking finding was an unbounded integer.** M4:
`limit`, `offset` and `version` overflowed pysqlite and produced `is_error: true` with no envelope.
M5: `seed` did the same through `dataset_skeleton` —
`OverflowError: Python int too large to convert to SQLite INTEGER`, verified through the real MCP
surface. M4's fix was correct and local (`service/limits.py`, `clamp()` and `storable()`), and M5
then added a new integer entry point and did not use it. That is exactly the failure the M3
implementer generalised: **the defect sits in the one place a working pattern was not reused.**
Chose, per R-50's two parts:
1. `ArgReader.integer` range-checks through `storable()` and reports `AP-001` at the argument. This
   is the load-bearing half, and worth being precise about why: `_put` catches `StoreError`, and an
   `OverflowError` from a driver is not one, so routing alone would have fixed nothing.
2. `_write` is routed through `_put` anyway, so the *first* skeleton write gets the same error
   translation the re-fill write already had. Consistency, not a second line of defence.
3. `tests/unit/test_bounded_integers.py` — the guard.
**Rejected, not clamped**, and the asymmetry with `clamp()` is the point. `clamp` is for a
*quantity*: a `limit` of `2**64` means the same thing as the largest representable one. A `seed` is
neither a quantity nor an identifier of an existing row — it is an authored value that every derived
id depends on, so substituting a nearby one would author a dataset the caller did not ask for.
The guard's shape is the part that matters, and it has three pieces:
- `INTEGER_PARAMETERS` is read off the **running server's registered tools**, so it follows the
  surface. R-50 is explicit that "a guard that reads a literal list of today's four integers is the
  defect it exists to prevent".
- `COVERED` supplies the arguments each case needs to *reach* a store column, which no schema can
  give you — a valid `dataset_id`, a published blueprint. The enumeration is compared against the
  surface, so a new integer parameter with no entry **fails** rather than being skipped. M6's
  `iteration` on `fetch_step` and `limit`/`offset` on `run_find` will find out here.
- `test_the_in_range_control_reaches_the_store` is the non-vacuity half, and it is not decoration:
  with `agent_id=""`, `dataset_skeleton` reports `AP-004` before it ever looks at `seed`, so a guard
  built without it **would have passed against the code M5 shipped broken**. Every case's in-range
  call must succeed, which proves the out-of-range call is exercising the bound.
Both halves were verified to fail, not assumed to work. Removing the range check fails four
assertions including both ends of the range; removing one `COVERED` row fails the enumeration with
the message that names the missing pair.

## [M5, fix round 1] Ruling R-47: `mark_skeleton_submitted` becomes a compare-and-set
Succeeds only if `submitted_as` is currently null, and returns whether *this* call claimed the
skeleton. `dataset_submit` reorders to validate → parse → stamp → CAS → SK-005 on loss →
`put_dataset` on win; the dataset id is available before the write because R-10 derives it from
`(seed, salt)`.
Reason, and it corrects the trigger the original entry named: I wrote that the remedy should wait for
"a second writer — the web app at M9". **M7 is the real deadline**, because M7 implements this
Protocol against Postgres and Mongo, so a CAS added afterwards costs three signed-off adapters
instead of one conformance test. R-33 made exactly this argument for `set_step_actual` and I did not
apply it to my own residue.
The other half of the correction is about *why* it outranks the fill race, which R-48 accepts: a lost
fill is **observable and self-correcting** — every `dataset_fill_part` response carries `remaining`,
and R-06 permits re-filling — while a lost submit was **silent**: the loser received a success
envelope for a dataset version it did not mean to create, and no rule fired. My original entry
weighed residue against residue and missed that asymmetry.
Implementation detail worth keeping: the conditional `UPDATE ... WHERE submitted_as IS NULL` is
evaluated by the database, so exactly one of two concurrent statements reports a matched row, and
`rowcount` is the answer rather than a follow-up `SELECT` that would reintroduce the race one
statement later. A read-then-write inside a transaction would **not** be equivalent — pysqlite defers
`BEGIN` until the first DML and Postgres under READ COMMITTED behaves the same way, so the read takes
no lock. That is the defect ruling R-37 found in `seq` allocation, and the same remedy: let the
database decide, once.
Losing the CAS becomes **SK-005**, the same rule id a sequential second submit gets, rather than a
boundary code — one condition, one vocabulary. `_lost_the_claim` re-reads the skeleton and goes
through `validate_submit`, so the rule stays the single author of its own message and the finding
names the dataset id the *winner* claimed.
Two consequences, both handled: the conformance case that pinned the replay tolerance **inverted**
(`test_claiming_a_skeleton_succeeds_once_and_only_once` now asserts `True` then `False` then `False`,
for the same id and for a different one, because a CAS lets the *state* decide rather than the
argument), and the crash residue flips — see the correction below.

## [M5, fix round 1] Correction: the write order is reversed, and the crash residue flips with it
The retracted entry above chose `put_dataset` then `mark_skeleton_submitted`, reasoning that a crash
between them leaves "a stored, validated dataset and an unmarked skeleton — visible, and
recoverable", where the reverse leaves "a skeleton SK-005 has closed and no dataset".
Both halves of that are still true. What the entry got wrong is that it was not the trade being
made: putting the write first is what left the concurrent submit open, and *that* residue was
silent. So the order is now claim-then-write, and the residue is a **dead skeleton** after a crash
between the two — a skeleton claimed for a dataset that does not exist.
Why that is the better residue: a dead skeleton is visible to the author (the submit did not return
a dataset), it costs one re-fill of five sections, and nothing downstream reads it. A duplicate
dataset version is invisible to everyone, is served to runs by `dataset_get`, and `dataset_find`
shows one row per lineage so it does not even appear as an anomaly.
Recorded rather than quietly reversed, because leaving two contradictory rationales in an
append-only record is worse than either of them.

## [M5, fix round 1] Ruling R-49(b): `AP-006`, for an exhausted id space
Id-space exhaustion reported `AP-001` at `/seed`, which tells the caller to fix an argument that is
perfectly well formed.
Chose: a sixth boundary code in contracts section 3.5, `AP_ID_SPACE_EXHAUSTED` beside the other
five, and the one call site plus its assertion updated.
Reason: this and `AP-001` are the two ways a request can be unservable, and the distinction is what
makes either useful — `AP-001` means *this value is wrong*, `AP-006` means *this value is right and
its id space is full*. R-43(a)'s disjointness guard picked the new code up with no change, and
`test_the_boundary_codes_are_all_distinct_and_all_used` needed only its count updated, which is the
guard working.
Paired with the `seed` bound above deliberately: both were `AP-001` standing in for something more
specific, and fixing one without the other would have left the family's meaning still muddled.

## [M5, fix round 1] SK-004 suppresses only the sections that are actually unfilled
The first implementation returned from `submit` before the `DS-*` catalogue ran at all, so a submit
with four of five sections filled and a blank `provenance.title` reported SK-004 alone — and the
author paid a whole extra round trip to discover DS-025, in the repair loop the section-scoped error
envelope exists to make cheap.
Chose: run the catalogue, and drop only the findings whose `section` is one of the **unfilled**
sections. Ruling R-45 grants exactly that — SK-004 reports per unfilled section and "no `DS-*` rule
scoped to **those** sections fires at all" — *those*, not every section.
Sound because of SK-002: the filled sections are always a *prefix* of the manifest, so a finding
scoped to a filled section can never be an artefact of a later one being absent. There is no
reachable state where `nodes.core` is filled and `entities` is not, which is what would otherwise
make DS-006 report a missing entity that the author simply had not got to yet.
A section-less finding (DS-024 on partial labels, DS-020 on the seed) is kept, because it is not
scoped to an unfilled section and there is no later round in which it becomes visible.
The structural cost: `_load` now returns the skeleton *and* the findings rather than one or the
other, because SK-005 means "stop" while SK-004 means "report and keep reporting". It reads
`findings[0].rule` to tell them apart, which is sound rather than a shortcut — `_first_reporting`
stops at the first rule that reports, so every finding in one envelope comes from one rule, and
`SK_SKELETON_STATE` names that id once instead of spelling a literal in `service/`.

## [M5, fix round 1] `content` must carry at least one of the section's fields
`dataset_fill_part(section, {})` marked a section filled while contributing nothing, and the submit
then said nothing about it either: with `entities` and `nodes.core` both filled as `{}`, the submit
reported eight DS-002 findings scoped to `nodes.core` and not one word about the empty `entities` —
because SK-004 only asks whether a section is *in* `parts`.
Chose: `AP-001` at `/content`, naming the fields the section owns.
This does **not** touch ruling R-45's reachability requirement, and the distinction is worth stating
because they look adjacent: R-45 is about an individually absent *field* —
`{"provenance": {...}}` with no `narrative` is still accepted here, and DS-021 still reports it at
submit, which is the test directly below this one in `test_service_skeletons.py`. An empty *object*
is a different thing: it is a caller claiming to have filled a section it has not.

## [M5, fix round 1] `_ordered`'s tail is unreachable, and says so now
The docstring claimed "the tail is not dead code" and pointed at `assemble` running over
caller-supplied parts. That is weaker than it sounds: an unowned field is `AP-001` at fill time, so
no assembled document can carry one, and the branch is unreachable through the pipeline.
Chose: say so, and keep the branch. The reason to keep it is that the alternative fails *silently* —
a comprehension over a fixed field list would **drop** an unexpected value, and a value dropped by a
key-ordering helper is a value no rule could ever report. Defence in depth, labelled as such rather
than as a case.

## [M5, fix round 1] PRD flow B's repair loop gets one end-to-end test
Re-fill was proven at the rule layer (SK-002 permits it) and at the service layer (a re-fill
replaces the section) separately. Nothing drove the loop a caller actually experiences:
reject → re-fill the named section → submit succeeds.
Chose: `test_a_rejected_submit_is_repaired_by_refilling_one_section`, through the MCP client,
breaking `expected.comparison` — one word in one section, which is the shape of mistake an LLM makes
and a repair should cost one call to fix.
Reason: that loop is the product's headline authoring flow and the thing PRD 6 flow B is written
about, and it was three separately-proven pieces rather than a working flow. The assertions follow
what a caller depends on: rejected with the rule id, every finding naming `expected` so the caller
knows which part to regenerate, `remaining` empty after the one re-fill so it can tell it is ready,
and the resubmitted dataset **byte-identical to the golden fixture at version 1** — which is what
makes a re-fill a repair rather than a second draft.

## [M6] `run_start` is idempotent on the run id, and never re-pins
A second `run_start` for an existing run id returns the **stored** run and its pin unchanged, with
no write: no re-resolution of the selector, no new `started_at`, no change to
`declared_blueprint_version`, and the stored run's own warnings are what come back.
Reason: PRD 5.6 says a run "pins a version at start and reads that version for its whole life", and
the pin is only immutable if nothing re-writes it. The failing sequence is concrete and cheap to
reach — start a run, serve a step from version 1, edit the dataset (copy-on-write makes version 2),
then let a client retry `run_start` after a network timeout. A re-resolving implementation hands a
run that has already been served from version 1 a pin to version 2, which is the exact incoherence
copy-on-write exists to prevent, and nothing in the response would say so.
Alternative rejected: refusing a second start, or reporting a conflict when the freshly resolved pin
differs from the stored one. Both need a code the catalogue does not have, and both punish the one
caller the run id was designed for — the retrying client. The shape chosen is BP-016's: an identical
re-request is a no-op success. It differs from BP-016 in not erroring on a *differing* re-request,
because "differing" here is a property of the store's current state rather than of the request, and
a client cannot be told to fix an argument that was correct.
Residue, recorded rather than hidden: a second `run_start` naming a *different* selector silently
gets the first run. The response carries `pin`, so it is visible to anyone who looks, and
`test_starting_an_existing_run_id_returns_it_unchanged_and_never_re_pins` pins the behaviour with a
version 2 sitting in the store.
Argument checks still run **before** the existence check, so a malformed request is refused whether
or not the run exists — a bad request is a bad request.

## [M6] The run path is reconstructed by the store and never re-sorted by the service
`get_run` rebuilds `Run.path` from `run_steps` in `(seq, node_id, iteration)` order (ruling R-37) and
`fetch_step` records one row per served `(node_id, iteration)`. `service/runs.py` reads
`run.path[-1]` for resolution and `run_get` hands the store's list back untouched.
Reason: PRD 5.4 point 2 — "the path is reconstructed, not declared ... the agent cannot lie about
where it went". Any sort or filter in the service would be a second opinion about traversal order,
and the one that resolution depends on. R-35's lesson applies directly: a partial order that happens
to be stable on SQLite diverges on Postgres, and here it would surface as intermittently wrong
tool-name resolution rather than as a wrong list.
Alternative rejected: storing `path` as a column on `runs`. It would let an agent declare a path it
did not take, and it would need to stay in agreement with `run_steps` forever.
Consequence worth naming: `PathStep.at` comes from `run_steps.fetched_at`, which `fetch_step` sets
from the injected `Clock` (ruling R-09). A step row with a null `fetched_at` contributes no path
entry, which is why the column is `NOT NULL` with a `DEFAULT now()`.

## [M6] `fetch_step` returns the *stored* served document, from one decision site
`_serve` is the only place idempotency is decided, and **both** of its returns hand back a stored
document. The replay branch reads `served` off the step already on the run; the fresh branch returns
whatever `upsert_step` gives back, which is the existing row when a concurrent caller served the
same key first.
Reason: the M6 gate is "the same step key twice returns byte-identical fixtures and advances
nothing". Returning the locally drawn fixture would satisfy a test that compares two `fetch_step`
responses — the dataset is immutable, so two draws are equal — and would quietly stop being
idempotent the moment anything made them differ. So the test is written the other way round:
`test_a_repeated_fetch_returns_the_stored_document_not_a_fresh_draw` claims the step key first,
through `upsert_step`, with a marker document the dataset does not contain, and asserts the marker
comes back. Verified to fail against a `_serve` that returns the fresh draw.
The race branch has its own test, forced the way `DECISIONS.md` records for M3's races: one internal
read (`get_run`) is monkeypatched to return a run with no steps — exactly what the loser of two
concurrent first fetches sees — and everything after it runs against the real store. Also verified
to fail against the fresh-draw version.
Alternative rejected: an explicit "does this step exist" store read before drawing. It is a third
decision site for the same question and it would still race; `upsert_step`'s documented idempotency
is the thing that actually settles it.

## [M6] The served fixture is the authored `NodeFixture` document, `exclude_unset`, refs unresolved
`fetch_step` serves `fixture.model_dump(mode="json", exclude_unset=True)` — the keys the author
wrote, and only those. `entity_refs` is handed over as written; the service does not resolve
`store@after_docs` into the entity's state.
Reason: PRD design principle 2 is "hold the environment byte-identical", which is the basis of the
product's drift claim, so the read path hands over what the author wrote and computes nothing. Under
ruling R-08 the round trip is `model_validate(raw).model_dump(exclude_unset=True) == raw`, and the
golden fixtures omit `input` on every pool entry and `latency_hint_ms` on several nodes — a full dump
would hand the agent those keys as `null`, and
`test_the_served_fixture_is_the_authored_document_byte_for_byte` compares against the fixture file to
prove it does not.
Alternative rejected: expanding `entity_refs` into their states as a convenience. It makes the served
document something the service computed, which is the one thing PRD 5.6 point 4 says the runtime does
not do ("the runtime does not compute the second state, it serves it"), and M10's evidence bundle is
where a joined view belongs.

## [M6] Selection by labels assigns the first row and warns when several matched
`{labels}` goes through `find_datasets(agent_id, labels)` and takes `rows[0]` — the oldest match,
since the order is `(created_at, id)` and ruling R-35 makes it total. More than one match attaches a
`dataset_selection_ambiguous` warning naming how many matched, which one was assigned, and the
strategy. No match is RT-E04.
Reason: PRD 5.6 point 2 settles the hard half — "load testing needs *assignment*, choosing which
dataset each execution gets, not *reservation* ... the phase 2 work is a selection strategy, not a
locking scheme". So all that is left is which match to assign, and it has to be deterministic:
identical inputs must give identical output on every backend, which round-robin (stateful) and random
(seeded from what?) both break.
The warning is the part that is a choice rather than a consequence. Ruling R-22 keeps `Warning.code`
an open string precisely so a tool can add one, and M4 set the precedent with
`blueprint_version_missing`. A label query broader than its author realised is otherwise invisible:
the run just quietly gets a dataset. PRD 5.4's standard is "the developer is told loudly and decides
for themselves".
Alternative rejected: refusing an ambiguous selector. That is gating (ground rule 3), and it would
break the load-test case the selector exists for, where many runs deliberately share one label query.
The code is a module constant in `service/runs.py`, not in `models/errors.py`, so
`RUNTIME_WARNING_CODES` stays exactly contracts 3.4's three; `contracts.md` section 3.4 documents it
in prose, as M4's addition is, and `test_validation_drift.py` asserts both halves of that arrangement.

## [M6] `run_start` and an archived dataset: explicit id serves, label query does not
An explicit `{dataset_id}` starts the run and attaches `dataset_archived`. A `{labels}` selector never
selects an archived dataset, because `find_datasets` excludes them in its `WHERE` clause. A dataset
archived *after* a run started keeps serving every step, with the same warning.
Reason: this is the asymmetry `dataset_get` and `dataset_find` already have, applied one layer up, and
each half is load-bearing for a different reason. PRD 5.6 keeps an archived dataset "servable to any
run holding a pin to it", and contracts 3.4 defines `dataset_archived` as "The pinned dataset has since
been archived. Served anyway" — so the post-start case cannot refuse. But archiving is how an author
*retires* a dataset from the suite, so discovery handing one out would make archive meaningless for the
case it exists for.
RT-E04's wording — "Dataset not found, or archived and not pinned by this run" — reads as if
`run_start` should refuse an archived dataset, since the run has no pin yet. Read that way it would also
make `dataset_get`'s deliberate asymmetry pointless, and it would leave no way to re-run an archived
case on purpose. The reading taken is that the clause is about a *pinned* read, which is where the
question can actually arise.
Alternative rejected: warning on the label-query path and selecting the archived dataset anyway. It
turns "retired" into "retired unless nothing else matches", which is a worse answer than no match.
Also decided here, for the same reason: a dataset authored against a **different** `agent_id` than the
one `run_start` names is RT-E04 rather than a started run. Its node ids come from another graph, so
every `fetch_step` would report RT-E02 and the run would be unplayable — there is no dataset with that
id *for this agent*, which is resolution rather than policy.

## [M6] An unaddressable `iteration` is RT-E02, at both ends, and R-03 is untouched
`iteration` is refused — never clamped — when it is negative or outside the signed 64-bit range a
column can hold. Ruling R-03 assigns the negative case to RT-E02; the unstorable case gets the same
code for the same reason.
Reason: `iteration` is an *identifier* of a step within a run, and the M4 fix-round entry already put
it in that class — "an identifier (`version`, and M6's `iteration`) is *not* clamped: a version
outside the range is not a large version, it is no version". Clamping `2**63` to `2**63 - 1` would
answer a question the caller did not ask, and leaving it unchecked reaches `upsert_step`, where
pysqlite raises `OverflowError: Python int too large to convert to SQLite INTEGER` and the SDK turns
it into a protocol error. That is the M4 and M5 blocker, on M6's new integer, and R-50's guard caught
it before it shipped — the enumeration failed with `('fetch_step', 'iteration')`,
`('run_find', 'limit')` and `('run_find', 'offset')` named, which is the guard working rather than a
story about how it would have.
This does not revive RT-E05 and does not cap anything. R-03's rule is unconditional for every
*storable* index: `test_iterating_past_the_pool_is_never_an_error_however_far` asks for
`MAX_STORED_INT` on a node whose `max_iterations` is 3 and asserts it **serves**, with
`pool_exhausted`. What is refused is an argument that cannot name a step, not a loop that ran too far
— and a value wider than 64 bits cannot be a loop that happened.
Verified by removing the range check: the `above` case fails with the `OverflowError` above while the
`below` case still passes, because `index < 0` short-circuits first. That is precisely the trap M5's
guard hit — an earlier argument answering before the one under test is read — and it is why the case
is parametrised over both ends rather than written once.

## [M6] Step resolution's three tie-breaks, all of them the contract's
Contracts section 5's pseudocode fixes three things silently and `service/resolution.py` implements
them literally rather than improving them.
**`node_id` wins when both arguments are given.** Step 1 is unconditional, so `tool_name` is not read
at all. Refusing the pair as mutually exclusive — the way `dataset_validate` refuses `dataset` plus
`dataset_json` — would be a redesign of a stated algorithm, and a caller that sent both has already
said which node it means.
**Neither argument given is RT-E02**, per step 3, not `AP-001`. It reads like a missing-argument
boundary code and the algorithm says otherwise; the algorithm is the contract.
**A narrowing that eliminates every candidate is RT-E01**, because step 2 tests
`len(narrowed) == 1` and both zero and two-or-more fall through. That is also the *reachable* RT-E01:
BP-014 rejects a blueprint where two nodes reachable in one step from the same node share a
`tool_name`, so `len(narrowed) > 1` cannot occur for a stored blueprint, while `len(narrowed) == 0`
happens the moment an agent asks for a repeated tool from a position where neither candidate is next.
`test_bp_014_is_what_makes_the_narrowing_decisive` asserts that property of the golden blueprint, so
the claim is checked rather than asserted in prose.
The finding lists **every** candidate rather than the narrowed set, because the caller's next move is
to retry with one of those node ids — and `test_the_named_candidates_are_a_working_retry` proves each
named candidate actually resolves, so the recovery path is not a dead end dressed as one.
Adjacency comes from `validation/graph.py` rather than a second implementation of one-hop
reachability. `service/` may import `validation/`, that module is already total over edges naming
nodes that do not exist, and four rules already depend on it agreeing with itself.

## [M6] Warnings are merged onto the run only when the merge adds something
**Superseded in fix round 1 — see `[M6, fix round 1] Correction: the warning merge wrote every
column of the run, not its warnings` below.** The dedupe reasoning here stands (the key changed to
`(code, node_id, iteration)` under ruling R-54(b)); the *write* did not, and the residue paragraph
described a narrower blast radius than the code had.
`_flag` de-duplicates on the whole `(code, detail)` pair and issues `put_run` only if the merged list
grew. A replayed fetch recomputes an identical warning, finds it recorded, and performs no write at
all.
Reason: contracts 3.4 requires a warning "attached to both the response and the stored run" and PRD
5.2 says an exhausted pool "flags" the run, so the run has to carry them — but the M6 gate says a
repeated fetch "advances nothing", and a warning appended on every replay is advancing something.
De-duplicating on `(code, detail)` rather than on `code` alone is what keeps the two compatible while
still recording *which* iterations were served short: iteration 2 and iteration 3 are different
details and both are informative.
`test_a_replayed_warning_writes_nothing_to_the_run` monkeypatches `put_run` to raise on the second
fetch, so "no write" is asserted rather than described.
Residue, accepted: the merge is a read-modify-write on a JSON column, so two concurrent fetches of
the same exhausted iteration can both add the warning, and a lost update can drop one. Both are
recoverable and neither is state — the response always carries the warning, and the step row proves
the iteration was served past the pool, which is derivable from the run's steps and the pinned pool
length. Ruling R-48's standard applies: this is the visible, self-correcting kind of loss, not the
silent kind, and a CAS on an advisory list is not worth the third retry loop in this codebase.

## [M6] `run_start`'s payload is `{run, pin}` under one named key
`data` carries `{"start": {"run": ..., "pin": ...}}`; `fetch_step` carries
`{"step": {"fixture": ..., "resolved_node_id": ...}}`; `run_get` and `run_find` carry `{"run": ...}`
and `{"runs": [...]}`.
Reason: contracts section 4 documents `run_start` as returning `{run, pin, warnings}`, and M4's
convention is that `data` always holds the payload under **one named key** — the "Returns" column
describes the payload, not where in the envelope it sits. `warnings` is the envelope's own list, so
the payload is `{run, pin}`, and `dataset_skeleton`'s four-field payload under `"skeleton"` is the
precedent for a compound one.
`pin` is carried alongside the run as well as inside it. That is one duplicated object, and it is the
field a caller reads to learn what it was given — the M8 client will want it without walking into the
run document.

## [M6] The run id's shape and the `run_class` vocabulary are checked at the boundary
`run_start` reports `AP-001` for a run id outside contracts 2.3's "8 to 128 characters,
`^[A-Za-z0-9_.:-]+$`" and for a `run_class` outside `{dev, eval, load}`. Argument findings are
collected rather than short-circuited, so two bad arguments report twice.
Reason: neither constraint has a rule id and neither belongs in the model (ruling R-04 keeps value
constraints out of `models/`, and `Run.id` is a plain `str` there). `AP-001` — "a request argument is
missing, of the wrong JSON type, or mutually exclusive with another" — is the only code that can own
them, and the alternative is storing a run nobody can address. This is not gating: ground rule 3 is
about refusing to *serve* because the data looked wrong, and the surface already answers `AP-004` for
an unknown id and `AP-001` for a malformed one.
Both ends of the length range are tested (7 and 8, 128 and 129), per the rule M4's fix round recorded:
when an entry reasons about a boundary, test both ends of it.
`selector` is refused unless it carries exactly one of `dataset_id` or `labels`, and an *unknown* key
is refused rather than ignored — `{"label": {...}}` would otherwise be indistinguishable from an empty
selector, and the caller would be told "give one of two keys" while looking at a selector that has one.

## [M6] Clause 5 is three independent guards, and each was shown to fail
`tests/unit/test_runtime_is_read_only.py` asserts "no write path from `fetch_step` to a dataset" three
ways, because the milestone's reason to exist deserves better than one mechanism.
1. **A call-graph walk.** Every function reachable from `run_start`, `fetch_step`, `run_get` and
   `run_find` through `service/`, against a forbidden set **enumerated from the `Store` Protocol** —
   every method whose name starts with a mutating verb, minus the three run writes. A mutator M7 or M9
   adds is forbidden the day it appears on the Protocol, with no edit to the test. R-50's shape,
   applied to a different invariant.
2. **The one call site.** `put_dataset` is called from exactly one function in `src/`, in
   `service/skeletons.py`, reachable from `submit` and from neither `skeleton` nor `fill_part` nor any
   runtime entry point. That is ground rule 1's sentence as an assertion.
3. **Behaviour.** A delegating `RunOnlyStore` refuses every non-run mutator and a full walk runs
   against it. This is the half that catches what an AST cannot see.
Verified, not assumed. A planted `context.store.put_dataset(dataset)` in `fetch_step` fails all four
affected tests, each naming its own reason. A write planted as
`getattr(context.store, "put_" + "dataset")(dataset)` passes both AST guards — they are blind to it,
as their docstring says — and is caught by the wrapper and by the byte-identity check. That is the
argument for having three rather than one, demonstrated rather than claimed.
The wrapper is an explicit delegate rather than a `__getattr__` proxy so `mypy --strict` checks it
against the Protocol: if the Protocol grows a method, the wrapper stops satisfying `Store` and the type
checker says so instead of the guard silently narrowing.
`server/` is deliberately out of scope: `test_layering.py` already asserts no module in `server/`
imports `storage/` at all, so a tool function cannot reach a store method by any spelling. The guards
compose, and duplicating one here would suggest they do not.
Also mechanical, and cheap: `test_the_read_path_never_reads_max_iterations` asserts on the AST that
neither `runs.py` nor `resolution.py` reads `max_iterations`. Ruling R-03 deleted RT-E05, so the read
path has no iteration cap, and the way that regresses is somebody reading the field "just to check".

## [M6] The page-defaulting decision moved into `limits.py` rather than being copied
`service/limits.py` gains `DEFAULT_PAGE_LIMIT` and `page(limit, offset)`; `datasets.paginate` and
`runs.paginate` both call it, and `datasets.DEFAULT_FIND_LIMIT` is now an alias bound by assignment.
Reason: the M3 implementer's generalisation, adopted as build guidance and restated by R-50 — **the
defect sits in the one place a working pattern was not reused.** `run_find` needs the same two
decisions `dataset_find` makes (a default page size, and both ends of the clamp), and the way that
goes wrong is a second copy that agrees today.
Alternative rejected: importing `DEFAULT_FIND_LIMIT` from `service/datasets.py` into
`service/runs.py`. It works, and it makes the runtime depend on the dataset read module for a number
about paging.

## [M6] Two guards that were already stale, fixed rather than worked around
`tests/integration/test_transports.py` asserted `len(listed.tools) == 16` over HTTP, and the README's
"Serve the tools" section still listed thirteen tools and omitted M5's three. Both were counts written
down rather than derived.
The transport assertion now compares the HTTP surface to the in-process server's
(`{tool.name for tool in await mcp.list_tools()}`), which is the claim that test actually wants to make
— "HTTP reports the same surface" rather than "HTTP reports the sixteen tools that existed the day this
was written". It also now calls a `run_start` over HTTP, so the "one tool per module" property the
docstring claims covers the new module too.
The README count is a list rather than a number now, grouped by table, so a missing tool is visible.
`test_the_server_registered_its_tools`'s `== 20` stays a literal on purpose: that one exists to fail
when the surface changes without anyone noticing, which is the opposite requirement.

## Questions for the owner — M6
1. **`run_start` on an existing run id** returns the stored run unchanged, including when the second
   call names a different selector (see the entry above). If a divergent re-start should be an error
   instead, it needs a code — none of `RT-E01..04` or `AP-001..006` fits, and inventing one is a
   product decision rather than an implementation choice.
   **Closed by ruling R-53 in fix round 1**: it keeps returning the first run and attaches a
   `run_start_mismatch` warning, which is the option ground rule 3 already named and which neither
   of the two I weighed was.
2. **`dataset_selection_ambiguous`** is a new warning code on `run_start`. It is informational and
   R-22 permits it, but it is the second addition to a vocabulary the PRD wrote as three, so it is
   worth a look.
3. **Nothing reads `Run.status` at read time.** A `fetch_step` against a run that `run_finish` has
   already marked `finished` still serves, because the service never gates. If a finished run should
   stop serving, that is a policy decision and M8 is where it would land.
4. **R-03's open question is still open** (the ruling records it too): nothing stops an agent looping
   indefinitely against a pool. `max_iterations` is a *dataset* constraint (DS-023) and has no runtime
   effect, and `test_the_read_path_never_reads_max_iterations` now enforces that.

## [M6, fix round 1] Correction: the warning merge wrote every column of the run, not its warnings
The entry above says the merge is "a read-modify-write on a JSON column" whose worst case is a
duplicated or dropped **advisory warning**. That sentence is true of the warning list and false of
the code: `_flag` handed `put_run` a `model_copy` of the run snapshot `fetch_step` read at the top,
and `put_run` writes **every** column from the model it is given — `status`, `outcome`,
`finished_at`, `external_refs` included.
So a `fetch_step` that adds a `pool_exhausted` warning concurrently with, or from a stale read
before, M8's `run_finish` would revert the run to `status: "running"` with `outcome` and
`finished_at` null. **Nothing today fails, because `run_finish` does not exist yet** — which is
precisely why it would have cost M8 time rather than M6: the symptom is a run that un-finishes
itself under load, and the entry a reader would have found describes the accepted residue as a lost
advisory warning. This is the third round in this build where the defect was a `DECISIONS.md`
sentence that was more careful than its code; the pattern is worth naming again because it keeps
arriving in the same disguise — an accurate claim about the *intent* of a write standing in for a
claim about its *extent*.
**Fixed by narrowing the write, not by re-reading first.** New `Store.set_run_warnings(run_id,
warnings)` replaces a run's warning list and touches no other column; `service/runs.py::_flag`
computes the merge and calls it. The alternative the review offered — re-read the run immediately
before the merge and copy only `warnings` onto the fresh row — was rejected: it *narrows* the window
in which a concurrent `run_finish` is reverted and does not close it, and under ruling R-37 a
transaction around the read and the write would not close it either, because pysqlite defers `BEGIN`
until the first DML and Postgres under READ COMMITTED behaves the same way. The column bound is a
guarantee; a narrower window is a smaller version of the same bug.
That also settles the secondary finding for free: `put_run` re-`upsert_step`s every step it is
handed, so a flagged fetch on a long run issued N extra round trips. The narrow write issues one
`UPDATE` and one `SELECT`.
Where the merge lives is deliberate. The **service** computes the merged list because deduplication
is policy — R-54(b) fixes the key — and `storage/base.py` says in its own docstring that the
Protocol validates nothing and decides no policy. The **store** owns the column bound, because that
is the part no caller can get wrong.
Cost, stated: this is a Protocol addition at M6, so `contracts.md` section 6 is amended and M7's two
adapters implement one more method. R-33 made exactly this argument for `set_step_actual` and it is
the reason to add it now rather than at M8 — a method added after M7 costs three signed-off adapters
instead of one conformance test.
Tested on the losing side, twice, and both were verified to fail against the pre-fix write:
`test_a_stale_fetch_that_flags_a_warning_leaves_the_run_lifecycle_alone` (service, forced with the
M3 stale-read technique, `assert 'running' == 'finished'` against the old code) and
`test_set_run_warnings_writes_that_column_and_nothing_else` (conformance, so M7 inherits it). Both
say in their docstrings that the finished state is written through `put_run` directly because
`run_finish` does not exist yet, so M8 knows what to replace. Note that a *sequential* version of
either test cannot fail: a fresh snapshot carries the finished state, so writing every column writes
it back unchanged. The stale read is the test.
One test lost its teeth in the move and was rewritten rather than left green:
`test_a_replayed_warning_writes_nothing_to_the_run` monkeypatched `put_run`, which `_flag` no longer
calls. It now patches **both** run writes. A test named for a write should name every write that
could satisfy it.

## [M6, fix round 1] R-54(b): the merge key is `(code, node_id, iteration)`
`_warning_key` reads the code plus those two detail fields, rather than comparing whole warnings.
Reason: the ruling asks for it so that losing a duplicate concurrent write is *provably* a no-op.
Worth recording why nothing was wrong before, since the tests did not change: whole-`detail`
equality was **incidentally** equivalent for `pool_exhausted`, because `pool_length` and
`served_index` are functions of the pinned pool and the iteration, so two calls for one key cannot
produce different details. That equivalence was written nowhere and any added detail field would
have broken it silently. `test_an_exhausted_pool_flags_the_run_once_per_iteration` asserted the
property rather than the mechanism, and passed across the change unmodified — which is the argument
for asserting properties.
Consequence, deliberate: a code carrying neither field — `dataset_archived`,
`blueprint_version_mismatch`, `run_start_mismatch` — keys on `(code, None, None)` and is recorded
**once per run**. A client retrying a diverging `run_start` in a loop therefore cannot grow the run's
warning list without bound, while every response still carries its own current detail.
`test_the_divergence_warning_is_recorded_once_however_many_retries` pins it.

## [M6, fix round 1] R-53: a diverging `run_start` warns, on the response and on the run
`run_start` on an existing run id now resolves the selector again purely to compare it, and attaches
`run_start_mismatch` when the request does not match the run: a different `agent_id`, a selector
resolving to a different pin, or a selector resolving to nothing. The run itself is untouched — same
pin, same `started_at`, same `declared_blueprint_version` — and the reply is still `ok`.
Reason: ruling R-53. Ground rule 3 decides it without a new principle — "mismatches produce warnings
attached to the response and to the stored run" — and this is the same shape
`blueprint_version_mismatch` already handles. My original entry weighed "silently return the first
run" against "error", found no code that fit the error, and stopped; the third option was the one
the ground rule already names.
**On the response and on the stored run**, so it goes through `_flag`'s merge and not through
`_started`, which passes the *stored* list on a replay. That is why all three of this round's changes
are in the same function.
Three decisions inside it:
- **An unresolvable selector counts as divergence**, and it is the case the ruling actually names
  ("the one that surprises a caller who mistyped a `dataset_id`"). A mistyped id does not resolve to
  a different pin, it resolves to nothing, so "cannot compare, say nothing" would have missed the
  motivating example. `requested.pin` is then null.
- **The findings from that re-resolution are dropped.** A retry must not become an error because the
  world changed, so an RT-E04 informs the warning rather than replacing the reply. `_select`'s own
  warnings (`dataset_archived`, `dataset_selection_ambiguous`) are dropped too, for a different
  reason: they describe a pinning decision and this call is not making one.
- **`agent_id` is compared as well as the selector**, which the review found and the ruling was
  extended to cover: `run_start(same_id, other_agent, …)` silently returned another agent's run.
  `agent_id` cannot diverge *alone* — a dataset belongs to one agent, so the selector cannot resolve
  to the same pin under a different one — so both fields are reported and the test asserts
  membership rather than equality.
Visible consequence worth naming: editing a dataset makes every later replay warn, because the label
query now resolves to a newer version than the pin. That is a genuine divergence and the caller
should hear it — `test_starting_an_existing_run_id_returns_it_unchanged_and_never_re_pins` now
asserts the warning alongside the unchanged pin, so the two properties are read together.
Cost: one warning code and one extra store read per replay. The read is not avoidable — divergence
cannot be detected without resolving the selector.

## [M6, fix round 1] The read-only guard's durability claim is now enforced, not assumed
`test_every_store_method_is_classifiable` asserts that every public `Store` method starts with a
mutating **or** a reading prefix, and fails on anything else.
Reason: the report claimed "a mutator M7 or M9 adds is forbidden the day it appears on the Protocol,
with no edit to the test". That holds only for the five prefixes in `MUTATING_VERBS`. A future
`archive_dataset`, `record_outcome` or `bump_version` would write the world and be classified as a
**read** by mechanisms 1 and 2, leaving only mechanism 3 to notice — and the guard's guard asserted
non-emptiness plus three memberships, so nothing would have failed. All sixteen methods classify
correctly today; the claim was stronger than the mechanism.
Now an unclassifiable name fails, so adding one is a visible decision — put the verb in
`MUTATING_VERBS` or in `READING_VERBS` — rather than a silent widening of what the runtime may
reach. `set_run_warnings`, added in this round, classifies as a mutator by its `set_` prefix and is
allowlisted in `RUN_WRITES` because it writes a run.
Note the wrapper made the same point by itself and without being asked: adding `set_run_warnings` to
the Protocol broke `mypy --strict` on `RunOnlyStore` with "missing following Store protocol member",
which is exactly the property its docstring claims and the reason it is an explicit delegate rather
than a `__getattr__` proxy.

## [M6, fix round 1] Three smaller closures
**`resolution.py`'s `len(narrowed) > 1` branch has a test.** Unreachable through the store — BP-014
rejects a blueprint whose one-hop successors share a `tool_name` — but `resolve` is pure over a
`Blueprint` model, so a synthetic 1.0.0 with two such successors reaches it in three lines.
"Unreachable because another layer's rule forbids it" is a claim worth a test rather than a reason to
leave a branch uncovered, and the test names BP-014 so a reader knows which invariant is load-bearing.
**`_draw`'s node lookup takes a default.** `next(… )` without one raises `StopIteration` if `resolve`
ever returns an id the blueprint does not declare. Unreachable by contract, and it sat beside
`_no_blueprint`/`_no_dataset`/`_no_fixture`, which exist precisely so the read path answers an
envelope when an invariant one layer down breaks. Now it answers `AP-004` like its three neighbours.
**`run_start` has a case for an unparseable `dataset_id`.** Safe by construction — the adapter
returns `None` for an id that is not a well-formed UUID, so it becomes RT-E04 — and `run_find`'s
equivalent was tested while `run_start`'s was not. Any string can arrive as a `dataset_id`.
**The prose half of the warning-code drift guard.** The table guard compares §3.4's table to
`RUNTIME_WARNING_CODES`, which by construction cannot see a tool-local addition; deleting the
sentence that documents one left every test green. The guard now also asserts each addition appears
in `contracts.md`, and it caught `run_start_mismatch` as undocumented the moment it was written,
which is the shortest useful life a guard has had in this build.

## [M6, fix round 1] Noted for M9 and the load-test work: `_by_labels` is an unbounded read
`_by_labels` calls `find_datasets` with no `limit` and takes `rows[0]`, so assigning one dataset
materialises every matching lineage's summary — on the path PRD 5.6 point 2 describes for load
testing, where many runs share one label query.
Not narrowed, and deliberately not: `matched=len(rows)` in the `dataset_selection_ambiguous` warning
is accurate *because* the read is unbounded. A `limit=2` would make the count a lie ("matched: 2"
when fifty match), and a `limit=1` would remove the warning's whole basis. The trade is a real one
and it is stated rather than resolved: at authoring volumes the scan is free; at load-test volumes
the remedy is a counting method on the Protocol, or a selection strategy that takes a page rather
than a row — which is the phase-2 work PRD 5.6 already assigns. `label_vocabulary` made the same
choice at M4 for the same reason ("a count over the first 50 rows is not a count") and records it.

---

## [M7] `storage/common.py`: what two adapters must answer identically lives in one place
The `q` case fold, the semver ordering, `canonical()`, the retry budgets, `stored_document()` and
every record-to-model mapper moved out of `sql.py` into a new `storage/common.py`, which `mongo.py`
also imports. `sql.py` converts a `Row` to a plain `dict` (`_fields`) and hands it over;
`mongo.py` hands over the decoded document.
Reason: ruling R-39(a) put the fold in Python *because* no query expression folds case identically
across the three backends, and R-36 makes substring semantics the contract. A second copy of that
fold inside `mongo.py` would be exactly the divergence those rulings exist to prevent, and it would
have drifted silently — every fixture in the suite is ASCII except one `q` case, so the two
implementations could disagree for a year without a red test. The same argument applies to
`semver_key` (`1.10.0` after `1.9.0`) and to `DatasetSummary`'s 200-character narrative excerpt: a
second implementation could satisfy `mypy --strict` and still return a different answer than its
sibling, and "the conformance suite asserts identical results" would then be asserting a
coincidence.
Alternative rejected: `mongo.py` importing `sql.py` for the helpers. It works and it is one fewer
module, but it makes a Mongo-only deployment import SQLAlchemy for a case fold, and it puts the
shared code in the file whose docstring is about dialects.

## [M7] Postgres needed no adapter, and three fixes only a real server could show
M3's report said "there is no second adapter class" and that was right: `SqlStore.from_url` plus
one dependency. What it could not predict were three things that need a server to observe.
**1. `datasets_search` is dropped, not replaced.** Ruling R-36 offers "a `pg_trgm` GIN index, or it
is dropped where the extension is unavailable", and the answer is *dropped* on a stronger ground
than availability: R-39(a) moved both the fold and the match into Python, so `find_datasets` emits
no SQL text match at all and neither a `to_tsvector` nor a `pg_trgm` index has a query to serve.
**And an `ILIKE` prefilter is not available either**, which is worth recording because R-36 names
`ILIKE` explicitly. A prefilter is only sound if it matches a *superset* of the Python fold.
Measured against Postgres 17 (UTF8, `en_US.utf8`):

```
title                  term         pg ILIKE   pg lower   python casefold
Straße operator        strasse      False      False      True
strasse operator       Straße       False      False      True
BENGALURU              bengaluru    True       True       True
```

`str.casefold` folds `ß` to `ss` and no SQL fold does, in **both** directions. A prefilter that
drops rows the contract promises is worse than a scan. R-39(a)'s named remedy — a pre-folded
`title || intent` search column computed in Python at write time — remains the answer if this is
ever measured as a problem, and it says "do not build it now".
**2. A `TEXT` tie-break needs `COLLATE "C"` on Postgres.** New `SqlStore._byte_ordered`. Ruling
R-35 requires every list ordering to be total so that "identical inputs give byte-identical output
on every backend", and `ORDER BY` on a `TEXT` column does not deliver that: SQLite compares with
`BINARY`, Mongo compares byte-wise, and Postgres uses `LC_COLLATE`. Measured: `SELECT 'run-b' <
'runa'` is **false** on `en_US.utf8` and true on the other two, because a locale collation weights
punctuation below letters. So two runs sharing a `started_at` came back in a different order on
Postgres. Applied to `runs.id` in `find_runs`, where it decides real cases, and to
`run_steps.node_id` in `get_run`, where `UNIQUE (run_id, seq)` means it never does — kept there
because R-37 put those keys in the order precisely for the case where the constraint has not held,
and an ordering that is only total when nothing has gone wrong is not what R-37 asked for.
`datasets.id` needs nothing: a `UUID` column is not collated, and BSON binary subtype 4 sorts by
bytes, which is the same order.
`test_find_runs_breaks_a_tie_by_byte_order` was verified to fail without the fix, returning
`['runa', 'run-b']` on Postgres and `['run-b', 'runa']` on SQLite.
**3. `create_engine_for` names the psycopg 3 driver.** See its own entry below.
Alternative rejected for (2): sorting the tie-break in Python. It works and needs no dialect
branch, but it gives up `LIMIT`/`OFFSET` pushdown for every `find_runs` call to fix an ordering
that one backend gets wrong.

## [M7] M3's two open notes, both closed by asking a real Postgres 17
Recorded because M3 flagged both as suspicions rather than facts and asked M7 to determine which.
**`runs_lookup` is *not* an `alembic check` false positive.** `METADATA` declares
`desc(runs.c.started_at)` while the migration writes `sa.text("started_at DESC")`, and autogenerate
cannot reliably reflect or compare an expression index column — so the suspicion was reasonable. In
practice `alembic upgrade head` creates `btree (agent_id, run_class, started_at DESC)` and
`alembic check` reports "No new upgrade operations detected". Nothing needed fixing, so nothing was
changed; `test_the_migrations_schema_matches_sql_py` now runs on both dialects and
`test_the_runs_index_keeps_its_descending_column` reads the reflected `indexdef` on Postgres, which
is where a lost `DESC` would show and a column list would not.
**The `sa.text("'{}'")` / `"'[]'"` server defaults do coerce.** Verified only as compiled SQL at M3.
Against a live Postgres 17 they land as `'{}'::jsonb`, `'[]'::jsonb` and `'dev'::text`, and
`test_the_json_and_boolean_defaults_execute_on_this_dialect` now *executes* an insert that omits
them rather than compiling the DDL — which is the difference between the claim and the check.

## [M7] Mongo: object keys are percent-escaped, and only keys
Every opaque JSON value stored by `mongo.py` passes through `encode_keys`, which escapes `%` to
`%25`, `.` to `%2E` and `$` to `%24` in that order, and `decode_keys` reverses it in the opposite
order. Values are untouched.
Reason: two of ruling R-06's five section ids are `nodes.core` and `nodes.branches`, `Skeleton.parts`
is keyed by section id, and Mongo reads `parts.nodes.core` as two levels of nesting — M3 put a
dotted key in the conformance suite precisely so this landed in the adapter. The same hazard applies
to every *authored* document: a fixture's `output`, an entity's `state` and a step's `served` are
arbitrary caller JSON whose keys this adapter does not get to constrain, and a leading `$` looks
like an operator. The promoted, queryable fields stay native, and `labels` is *queried* through the
same escape so a label dimension containing a dot is filterable rather than a silent no-match.
The order is what makes it reversible: every literal `%` becomes `%25` before the other two
introduce any `%`, so a literal `%2E` survives as `%252E`, which contains no `%2E` substring.
`tests/unit/test_mongo_key_codec.py` is a `hypothesis` property test over an alphabet made almost
entirely of the escape characters, asserting both the round trip and injectivity — because "provably
injective" in a docstring is worth nothing next to a test of the losing side.
Alternatives rejected: **storing the blob as a JSON string**, which is simpler and lossless but
makes `labels` unqueryable, so `find_datasets`'s label filter would become a full scan and a decode
per row. **Replacing the dot with a lookalike** (`U+FF0E`), which is not injective — a key that
already contained the lookalike collides.
Residue, stated: an **empty** object key (`""`) is legal JSON and is escaped to `""`, and MongoDB's
own tolerance for empty field names has varied by version. No fixture, schema or authored document
in this project has one, and the codec cannot fix it without a prefix scheme that changes every
stored key. If it ever matters, the fix is to prefix every encoded key with a sentinel character.

## [M7] Mongo: the clock is the server's, read with `hello`, not `$$NOW` in a pipeline
`MongoStore._server_now()` runs `db.command("hello")` and takes `localTime`. Used in exactly three
writes — `put_blueprint`'s insert (`created_at`), `upsert_step`'s `fetched_at` fallback, and
`set_step_actual`'s `recorded_at` — which are the three places the SQL adapter relies on
`DEFAULT now()` or `func.now()`.
Reason: ruling R-09 allows a timestamp from "a DB column default, the client, authored content, or
`Seeded.timestamp()`", and R-39(d) reads the first as "a database clock, whether a column default
or an explicit `func.now()`". Mongo has neither column defaults nor a `now()` function in a plain
update, so a command that returns the server's clock is the only mechanism in that category — and it
keeps `storage/` free of any Python clock read, which `tests/unit/test_layering.py` asserts. `hello`
is the connection handshake: no privileges, no version floor worth stating. `serverStatus`,
`hostInfo` and `$collStats` all need an admin action; `$documents` needs 5.1+.
**Rejected: an aggregation-pipeline update using `$$NOW`.** It costs no extra round trip and it was
the first implementation. In a pipeline update every literal is an *expression*, so a stored value
that happened to be the string `"$total"` would silently resolve as a field path, and a `$literal`
wrapper would have to be remembered at every site. A codec that escapes keys next to a pipeline that
reinterprets values leaves exactly one hole, in the one place — authored fixture content — where the
values are least predictable. Three round trips on three rare writes is the cheaper price.
Cost, stated: one extra round trip per blueprint insert and per *first* fetch of a step.

## [M7] Mongo: the natural key is the `_id`, and the `labels` index is a wildcard index
**`_id`.** `blueprints._id` is `{agent_id, version}`, `datasets._id` is `{id, version}`,
`run_steps._id` is `{run_id, node_id, iteration}`, and `skeletons`/`runs` use their own id.
Reason: contracts section 8 says each collection is "keyed by" exactly these, so making the natural
key the `_id` gets uniqueness from the server rather than from a second index that could be missing
— which matters most for `run_steps`, where that key *is* `upsert_step`'s idempotency. And a
driver-generated `ObjectId` embeds a client clock plus a per-process random value, which ground rule
9 forbids in this package. A subdocument `_id` matches by exact field order, so each one is built by
a single function and never spelled inline.
**The `labels` index.** Contracts section 8 says "a multikey index on `labels`" and this is a
wildcard index on `labels.$**` instead. "Multikey" describes an index over an array-valued field;
`labels` is an object, and a plain index on it serves only whole-object equality. A wildcard index
is what actually accelerates `labels.tier == "regional"`, which is the query `find_datasets` issues.
Recorded as a deviation rather than silently, and amended into contracts section 8.
**No text index on `{title, intent}`**, which section 8 also asked for. Same reason as
`datasets_search`: R-36 makes `q` substring matching and R-39(a) puts the fold in Python, so a
stemming index would serve a query nobody issues. `$regex` with `i` is a third answer to case
folding and would be the divergence R-36 forbids.

## [M7] The exception-mapping strategy across three drivers
The three backends raise three different things for one condition, and the strategy is: **one
pattern per condition, translated at the adapter, never at the service.**
- **duplicate key**: `IntegrityError` on both SQL dialects, `DuplicateKeyError` on Mongo. Every
  retry in both adapters is the same bounded-retry-on-duplicate-key shape, with the budgets in
  `common.py` — because the *guard* is a unique key on all three backends, which is what rulings
  R-37 and R-39 both turn on. Each adapter catches its own driver's type and re-raises anything that
  is not the race, so a real fault is not buried under eight attempts.
- **failed CAS**: not an exception on any backend. `mark_skeleton_submitted` compares `rowcount`
  (SQL) or `matched_count` (Mongo) from a *conditional* write, so the server decides once. Ruling
  R-47's requirement survives unchanged on both.
- **write-once refusal**: `set_step_actual` is the same single-decision-site loop in both adapters,
  with `WHERE actual IS NULL` / `{"actual": None}` in the filter. M3's fix round 2 got this wrong
  once by checking that *some* actual was stored rather than that it was *this* one; the shape was
  copied to Mongo along with the reason.
- **oversized integer**: `OverflowError` from pysqlite, `DataError` from psycopg, `OverflowError`
  from bson. **None of them is translated**, deliberately: ruling R-50 bounds the value at the
  *boundary*, where `AP-001` is the answer, and `test_bounded_integers.py` asserts the envelope
  there. The new conformance test `test_an_oversized_seed_is_never_silently_truncated` asserts the
  property underneath instead of the type — "round-trips exactly, or is not stored at all" — because
  a Protocol-level test that named a driver exception would be asserting the thing that differs.
- **store unreachable**: `SQLAlchemyError` / `PyMongoError`, both caught by `health()`, which never
  raises. Mongo needs a short `serverSelectionTimeoutMS` for that to be a prompt "no" rather than a
  thirty-second one.
Every `except` clause in `sql.py` was reviewed against this list, and the outcome is that none of
them needed widening: the ones that catch `IntegrityError` are about a unique key, and Mongo's
equivalent is a different class in a different module.

## [M7] The bundle format
`{format: "agentprops.bundle", format_version: 1, agent_id, blueprints[], datasets[]}`, keys in that
order, documents dumped `exclude_unset`.
Reason for each part. **The blueprints travel** because contracts section 4 says "blueprint version
plus datasets" and because DS-001 requires a dataset to name an existing *published* blueprint — a
bundle of datasets alone fails its own validation on arrival anywhere that has not already seen the
blueprint. Only the referenced versions are carried: an export is a bundle of *those datasets*, not
a backup of the agent. **`exclude_unset`** is ruling R-08's round-trip criterion, and it is what
makes an export/import/export cycle return the same bytes rather than a document with eleven
reintroduced `null`s. **The key order is fixed in one function** because a bundle is a file a human
diffs, and a dict assembled in two places is a dict that eventually differs. **`format` and
`format_version` are checked before any rule runs**, and are `AP-001`: a caller who passed the wrong
object should be told that once rather than handed the catalogue's opinion of a document that was
never a dataset.
Alternatives rejected: **a bare array of datasets** (fails DS-001 on arrival, and says nothing about
what produced it); **a tarball or JSONL** (a bundle has to be a *tool argument*, and the MCP surface
takes objects); **carrying every published version of the agent's blueprint** (an export of two
datasets would drag in six blueprint versions nothing references).
Two behaviours worth knowing. **A version number does not travel** — `put_dataset` allocates, so a
dataset exported at version 2 arrives at version 1 in an empty store; `created_at` does travel,
because it is authored content, which is the whole of ruling R-09 and what keeps `dataset_find`'s
ordering identical across the crossing. Both ends of that are now tested. **An import is not atomic
against a crash**: there is no transaction across the `Store` Protocol, so the residue is a partial
import, visible in `dataset_find`, and re-importing the same bundle is safe because an identical
re-publish is a no-op (R-29) and a re-imported dataset becomes a new version.

## [M7] `_BundleResolver`: import validates against the store *plus* the bundle
`import_bundle` validates the blueprints against the plain store resolver, then validates the
datasets against a resolver that answers `get_published_blueprint` and `dataset_exists` from the
receiving store **plus this bundle**, and only then writes anything.
Reason: DS-001 asks whether the dataset's blueprint is published *in this store*, and for a new
agent it is not until the bundle's blueprints are written. So the obvious implementation is publish
first, validate second — and a bundle whose datasets then fail leaves behind a published blueprint
version that BP-016 has made **immutable forever**. A failed import that permanently changed the
store it was rejecting is a worse failure than the one it was reporting. The overlay is not a
different answer to the two lookups; it is the answer they will give once the import finishes, which
is the question worth asking before writing. Staging the bundle's dataset ids for DS-031 falls out
of the same argument: a bundle that exports a superseding pair has to be importable into a fresh
store, or the rejection names a lineage the same bundle supplies.
There is no case where the store and the overlay can disagree: blueprint validation runs against the
plain resolver first, so a bundle version that differs from a published one fires BP-016 and the
import stops before the overlay is consulted.
Verified both ways. `test_a_refused_import_publishes_no_blueprint` is the guard, and removing the
overlay was checked to fail the *happy path* with DS-001 — so the overlay is load-bearing rather
than defensive.
Alternative rejected: publishing first and accepting the orphan. It is simpler, and R-29 makes an
orphaned published version inert rather than corrupt — but "nothing is written until all of it
passes" is a sentence worth being able to write truthfully, and this is what it costs.

## [M7] What `dataset_expand` seeds from, and what an expanded entry is
**Seeds from** the dataset's own `seed`, salted `expand:<node_id>:<index>` where `index` is the
entry's final position in the pool. One `Seeded` per position, not one stream per batch.
Reason: design principle 3 is "same seed plus same blueprint version yields the same dataset", so a
per-call seed argument would make one dataset's pool depend on which call grew it — and the dataset
already carries the value everything else about it was derived from (DS-020 owns it). Addressing by
position rather than drawing from a batch stream is what makes the entries at positions already
written **stable across versions**: expanding version 2 leaves version 1's entries byte-identical,
which is the property a reviewer reading a lineage depends on.
**An expanded entry is a deterministically chosen copy of an authored fixture**, with
`latency_hint_ms` jittered when the template carries one. Not invented content, and that is a
constraint rather than a shortcut: DS-019 validates every pool fixture against the node's
`output_schema`, the schema is user-supplied JSON Schema, and generating a conforming instance of an
arbitrary schema is a model's job — which ground rule 4 forbids anywhere in this service. PRD
section 4 says the same from the other side: "deterministic expansion from `seed` fills volume,
repeated rows and pool entries". Repetition is the feature. `latency_hint_ms` is the one field
varied because it is the one field on a `NodeFixture` that no schema constrains and that the entity
timeline does not depend on.
**Correction, found by the test that was written to assert the opposite.** The first version of
`expansion.py` claimed in its docstring that expanding by 5 equals expanding by 2 then 3. It does
not: a new entry is chosen from the pool it is being added to, and after the first call that pool is
longer, so the second call is expanding a *different dataset* and a draw over five entries is not a
draw over two. Measured: the two agree up to position 4 and differ at 5. The guarantee is narrower
and is now what is asserted — the prefix is preserved, the continuation may differ — and
`test_a_split_expansion_keeps_the_prefix_and_may_diverge_after_it` pins both halves so the residue
is a stated trade rather than a surprise. Making them agree would need the store to remember which
entries were *authored* as against generated, and nothing does.
`MAX_EXPAND_COUNT = 10000` is a **stated** bound reported as `AP-001` naming the maximum, never a
clamp. R-56 forbids silently serving a smaller expansion than was asked for, and DS-023 cannot be
the only bound: it caps a *loop* node, ruling R-51 allows a `pool: true` node that is not a loop
node, and R-23 requires the document be built before it is validated — so nothing in the catalogue
stops `count = 2**62` from being *attempted*, and that is an out-of-memory rather than a validation
failure. Ten thousand because PRD section 4 names "a 10,000-row load-test dataset" as the volume
expansion exists for.

## [M7] `create_engine_for` names the psycopg 3 driver, at the one seam
A plain `postgresql://` URL selects SQLAlchemy's default Postgres driver, which is psycopg2 — not a
dependency of this project. `create_engine_for` normalises through `postgres_url()`.
Reason, and it is a bug this found rather than a precaution: `context_for` normalised and
`migrations/env.py` did not, so `docker compose --profile shared up` started, ran
`alembic -x url="postgresql://..." upgrade head` and died on
`ModuleNotFoundError: No module named 'psycopg2'` — while the *service* half of the very same URL
would have worked. Found by running the compose profile, not by reasoning about it.
Fixed at the seam rather than at the second caller. `create_engine_for` is the one place a SQL URL
becomes an engine, so it is the one place that can be the answer for every caller — and
`context_for`'s own call was removed, because two normalisations are two places to forget. This is
the M3 implementer's rule applied again: the defect sits in the one place a working pattern was not
reused.
A URL that already names a driver is left alone, so `postgresql+asyncpg://` is not silently
rewritten to something else. `test_a_plain_postgres_url_names_the_installed_driver` needs no server
— `create_engine` resolves and imports the DBAPI without connecting, which is the step that was
failing — and it was verified to fail against the pre-fix code.

## [M7] `context_for`: one store target, three backends, and three answers about the schema
`service/context.py` gains `context_for(target)`, dispatching on the URL prefix: Mongo, Postgres,
SQLite URL, or a bare filesystem path as the fall-through. `server/__main__.py` calls it with
`--store` / `AGENTPROPS_STORE`.
The dispatch itself is unremarkable; **who creates the schema** is the decision. A SQLite file is
created if absent, which is the containerless throwaway CLAUDE.md describes and what `--store
./agentprops.db` has meant since M4. Mongo's indexes are **declared on startup**, because contracts
section 8 gives collections and indexes rather than DDL, there is no Alembic for a document store,
and `create_index` is idempotent — so a startup declaration is the only mechanism available and a
safe one. Two of those indexes are load-bearing rather than cosmetic: the unique `{run_id, seq}`
index is what makes `seq` allocation sound (R-37), and a process that skipped the declaration would
serve every read correctly while losing that guarantee silently.
A **Postgres** URL creates nothing, which is the warning `server/__main__.py` recorded at M4 before
this function existed: "a URL argument that silently ran `create_schema` against a migrated Postgres
database would be worse than not having one". `docker-compose.yml`'s `shared` profile runs
`alembic upgrade head` before it serves, for exactly that reason.
The fall-through is the bare path rather than an error, because that is the spelling a human types
without thinking about schemes.

## [M7] `docker-compose.yml`: two services behind one anchor, and overridable host ports
The service is defined once in an `x-service` anchor and used twice, so the only difference between
the `local` and `shared` profiles is the store URL. A copied-and-edited block is where two
deployments quietly diverge.
The `shared` service has a `command` and the `local` one does not, and the asymmetry is the schema
decision above: Postgres is migrated, Mongo declares its indexes on startup, so `--profile local up`
is genuinely one command.
Both databases declare a healthcheck and both services wait on `condition: service_healthy`, which
is what makes `up` a single command rather than a race the service loses on a cold start.
`pg_isready` names the user and the database, because the bare command answers for the *default*
database and reports ready while the init scripts are still creating this one.
**The three published host ports are overridable** (`AGENTPROPS_SERVICE_PORT`,
`AGENTPROPS_POSTGRES_PORT`, `AGENTPROPS_MONGO_PORT`), and that is not speculative generality. On
Windows a host Postgres bound to `0.0.0.0:5432` wins the race for IPv4 over Docker's port proxy, so
the container is created, healthy and listening while every connection lands on the other server and
is told "password authentication failed for user agentprops" — for a role it has never heard of.
Diagnosed by `Get-NetTCPConnection -LocalPort 5432`, which showed two listeners. The whole M7
Postgres verification ran on 5433 for that reason.
The image healthcheck is a **TCP connect**, not an HTTP request: the MCP streamable HTTP transport
answers `/mcp` only for a POST carrying a protocol handshake, so a `GET /` check would report
unhealthy on a working server. It also keeps `curl` out of the image.
`tests/unit/test_containers.py` is the drift guard, and it tests the one thing a running container
cannot: whether the compose file and the Python it starts still agree about the environment variable
names, the URL schemes `context_for` dispatches, and the health dependency. Rename a variable on
either side and the container starts, serves, and silently uses a SQLite file inside itself that is
discarded on the next `up`.

## [M7] `Seeded` has two modes, and `int()` is the only one that hashes for a value
`uuid()` is **addressed** — a pure function of `(seed, self.salt, salt)` with no hidden state. The
four value generators are a **stream** — each draw advances a per-instance counter that is mixed
into the digest.
Reason: ruling R-10 makes an id re-derivable from its seed and R-46 pins the derivation with a
literal test, so an id that depended on how many other values had been drawn first would not be
re-derivable at all. Meanwhile contracts section 9 gives `int`, `choice`, `shuffled` and `timestamp`
**no salt parameter**, and expansion needs *N* distinct draws — so a counter is the only way to read
that signature honestly. Two fresh `Seeded(seed, salt)` instances produce identical sequences, which
is the determinism guarantee; one instance does not repeat itself.
`int()` is the only method that hashes for a value; the other three are written in terms of it.
Four independent derivations would be four things to pin, four places for a bias to hide, and four
ways for an edit to change one method's output without changing the others'.
`int()` draws by **rejection sampling** against the largest multiple of the span that fits in 64
bits. `drawn % span` over-represents the first `2**64 % span` values; the bias is tiny but it is a
bias, and a generator whose whole job is reproducible fixtures should not also be quietly skewed.
The loop has exactly one exit and terminates because the acceptance probability is never below one
half.
`timestamp()` drifts **forward** by 0 to `drift_s` seconds rather than symmetrically. Contracts
section 9 gives the signature and not the direction; a dataset encodes a timeline ordered by
`after_node` and read by DS-010, so a drift that could move a derived timestamp *before* its base is
the one direction that can make an expanded timeline incoherent. A caller wanting a symmetric jitter
of `d` writes `timestamp(base - timedelta(seconds=d), 2 * d)`, which is explicit about it.
`_Int = int` exists because `Seeded.int` shadows the builtin inside the class scope, so an
annotation on a member declared after it resolves to the *method* and `mypy --strict` reports
"Function ... is not valid as a type". Renaming the method would diverge from the contract, so the
type got the second name.

## [M7] The layering guard traded a bare `choice` suffix for an import ban
`tests/unit/test_layering.py`'s random-source check no longer matches the bare suffix `choice`. In
its place: `random.choice` and `secrets.choice` are matched by their **dotted** names, and
`random`/`secrets` may not be **imported** in any of the six guarded layers.
Reason: ground rule 9 is "all randomness inside the service flows through `Seeded`", contracts
section 9 names `Seeded.choice` as one of its methods, and `service/expansion.py` calls it — so the
guard reported the module for doing exactly what the ground rule requires. A bare-suffix match
cannot tell `source.choice(templates)` from `random.choice(templates)`.
**Strictly stronger than what it replaced, not weaker.** `random.choice` cannot be reached without
naming `random`: either as `import random`, and then the call is dotted and caught, or as
`from random import choice`, and then the import is caught. The import ban also fails on the line
that made the mistake possible rather than on the line that made it, and it sees a route this file
has not thought of.
Verified in both directions, because a fix that defangs its own guard has happened three times in
this build. A planted `import random` plus `random.choice(...)` in `service/expansion.py` fails
**both** halves — `['line 277: random.choice()']` from the call guard and the import from the new one
— and `Seeded(1).choice([1, 2])` fails neither. `test_the_import_guard_would_catch_a_random_source_imported_by_name`
carries the second half as a permanent assertion.
`shuffle` stayed on the suffix list and does not collide: the seeded method is `shuffled`.

## [M7] Three guards widened because M7 would otherwise have slipped past them
**The no-delete guard learned pymongo's spellings.** `DELETING_CALLS` was
`{delete, delete_all, bulk_delete, truncate}` — and **not one** of `delete_one`, `delete_many`,
`find_one_and_delete`, `drop` or `drop_database` matches any of them. The set that guarded `sql.py`
perfectly would have said nothing about an adapter that emptied a collection on every write.
`test_no_adapter_declares_a_delete_method` is now parameterised over both adapters, because "the
adapter" stopped being singular.
**`test_put_dataset_has_exactly_one_call_site` became an enumeration of three.** M7 added two
legitimate dataset writers — `dataset_expand` and `dataset_import`, both the authoring flow — so
"one call site" stopped being the invariant while "every call site is a named authoring writer" still
is. Kept as an enumeration rather than relaxed to "anything in `service/`", because the thing worth
noticing is a *fourth* writer appearing: adding one now means adding a row and saying which tool it
serves. `test_no_runtime_entry_point_reaches_a_world_write` is unchanged and still asserts the other
half over the call graph.
**The bounded-integer enumeration found `dataset_expand.count`, as designed.** It answers a *third*
way, which is why `limits.py` needed three names rather than two: above `2**63` it is `AP-001` like
`seed` (a count is not an identifier, but "make 2**63 entries" and "make 2**63 - 1" are both
impossible rather than equivalent), above the per-call maximum it is also `AP-001` naming that
maximum (R-56 forbids silently serving less), and only a *negative* count is clamped.

## [M7] Three conformance tests added, none changed
The gate is that the suite passes "identically against SQLite, Postgres and Mongo", and it does with
**not a line of `test_store_conformance.py` changed** — which was the point of writing it against
the Protocol at M3. `tests/conftest.py` gained two URLs and `tests/integration/conftest.py` gained
two fixture branches, exactly as M3's report predicted.
Three tests were **added** at the end, for properties that only became checkable once three servers
could disagree, and each names the measurement it came from rather than the intuition:
`test_find_runs_breaks_a_tie_by_byte_order` (a locale-collated Postgres returns the other order),
`test_find_q_folds_the_way_python_does_and_not_the_way_sql_does` (the `ß`/`ss` case, which the
existing accented-vowel test does *not* catch because Postgres folds that one correctly), and
`test_an_oversized_seed_is_never_silently_truncated`. The third asserts a *property* rather than an
exception type — "round-trips exactly, or is not stored at all" — because three drivers raise three
different classes for one condition and a Protocol-level test that named one would be asserting the
thing that differs.
Recorded as an addition rather than presented as "nothing changed": the brief asked for a finding if
a conformance test needed touching, and these are the findings. What did not happen is a test being
relaxed to make a backend pass.

## [M7] Postgres and Mongo isolation in the suite: reset, not recreate
The `store` fixture gives every test an **empty** store, because half the conformance suite counts
rows. SQLite gets a new file per test. Postgres gets `METADATA.drop_all` then `create_all`; Mongo
gets `drop_database` then the index declarations. The engine and the client are **session-scoped**.
Reason: a per-test connection to Postgres is most of the wall clock of a run this size, and the
reset is a handful of statements. Dropping first rather than after means a run interrupted mid-test
leaves nothing for the next one to trip over. Dropping a *table* is DDL on a throwaway database
rather than the deletion of a row anyone authored, so ground rule 6 is untouched — the same
reasoning `test_the_migration_is_reversible` already recorded for `downgrade base`.
The Mongo database name is a constant in the fixture (`agentprops_conformance`) and is deliberately
**not** taken from the URL's path: this fixture drops the database, and a URL is the wrong place to
take that decision from.

## [M7] `tests/integration/test_backend_selection.py`: the guard against a green run over nothing
A `--store postgres` run that collects nothing, or collects everything and skips it, reports green —
and so does a run where the `store` fixture silently fell back to SQLite. Both are the milestone gate
being claimed without being met, and neither leaves a mark in the summary line anyone reads.
Four properties. `STORE_BACKENDS == IMPLEMENTED_STORE_BACKENDS`, which inverts the state M3
deliberately left, in the same way M5 inverted the test that pinned the absence of the `SK-*` rules.
The default is SQLite alone, because CI has no Docker. Every conformance test takes `store`
**directly**, which is what `pytest_generate_tests` keys off — a test that reached a store through
`published` or `pinned` alone would run once instead of once per backend and nothing else would
notice. And the sharpest one: the store must report `backend` equal to the id that was selected, so a
fixture that fell through to SQLite fails here and only here, because SQLite passes every conformance
test correctly.

## [M7] `test_migrations.py` is parameterised over the two SQL dialects, not the three backends
Mongo is absent because there is no Alembic for a document store — contracts section 8 gives
collections and indexes rather than DDL, and `MongoStore.create_schema()` declares them, which the
conformance suite exercises on every test.
Adding the Postgres parameter is what closes M3's two open notes and keeps them closed, and it needs
the schema dropped and recreated per test: a shared database carrying a previous run's tables would
satisfy `test_the_migration_creates_every_table` without the migration having done anything.
The Postgres fixture hands alembic the **plain** `postgresql://` URL rather than a driver-qualified
one, deliberately — that is what a compose file and an environment variable carry, and it is
therefore what exercises the normalisation in `create_engine_for` that
`docker compose --profile shared up` needs.

## [M7] Revision `0001` amended a third time
The `datasets_search` index was removed from the initial revision rather than dropped by a `0002`.
Ratifying R-39(c) for the third time and for its own reason: this is the initial schema of an
unreleased milestone on an unmerged branch, no database outside a temporary test database has ever
been migrated by it, and a follow-up revision would make every future deployment create an index
and then drop it. **Migrations become append-only the moment this branch merges.**
Stated because the brief asked which was done and why: amended, and this is the last milestone that
can.

## Questions for the owner — M7
1. **Is `expansion_added_nothing` the right answer to `count = 0`?** The alternative is `AP-001`
   ("count must be at least 1"). The warning was chosen because the argument is well formed and
   ground rule 3 is about not refusing well-formed requests, and because writing a byte-identical
   new version would put a meaningless entry in a lineage a reviewer reads. If a refusal is
   preferred it is one predicate.
2. **`MAX_EXPAND_COUNT = 10000` is invented.** No document gives a per-call bound and DS-023 cannot
   be one for a non-loop pool node (R-51). Ten thousand is PRD section 4's own load-test figure. A
   caller who wants more makes a second call and gets the same entries either way, because an entry
   is addressed by its position.
3. **Should a split expansion equal a single one?** It does not, and the residue is recorded above
   with the measurement. Making it hold would need the store to distinguish authored entries from
   generated ones — a column, or a marker inside the fixture that DS-019 would then have to exempt.
   The narrower guarantee (the prefix never moves) seems like the one that matters for a reviewer
   reading a lineage, but this is a product question.
4. **`bundle` is `format_version: 1` with no migration path.** An older service reading a future
   bundle answers `AP-001` naming the version it expects, which is the honest failure. If bundles
   are ever expected to be forward-compatible, the shape needs a "unknown keys are ignored" rule
   stated now rather than discovered later.
5. **An empty object key is not addressable in Mongo**, and the codec cannot make it so without a
   prefix scheme that changes every stored key. Nothing in this project produces one. Flagged rather
   than fixed.

---

## [M7, fix round 1] Correction: `find_datasets` filtered the wrong version on Mongo
The Mongo aggregation put the four promoted filters in a `$match` **before** the
`$sort`/`$group`/`$replaceRoot` that reduces a lineage to its latest version. That reads
perfectly and answers a different question: the group then sees only *matching* versions, so
`$first` returned the latest version **that matched the filter** rather than the latest version of
the lineage. SQL picks `version == max(version)` for the lineage first and filters that row.

For a lineage whose promoted fields differ between versions the two are different rows, and Mongo
returned a non-latest one — contradicting ruling R-38's "one row per lineage, at its **latest**
version" literally. Reproduced on all four filters (`labels`, `author`, `blueprint_version`,
`agent_id`); each returned `(…001, 1)` on Mongo where SQL returned `[]`.

**Why it matters rather than merely differs.** `find_datasets` is the review surface M9 builds on —
PRD 5.7, "a stranger has to judge relevance without opening anything" — so a search for
`tier=regional` that surfaces a dataset which is now `national` is the surface lying rather than a
near miss. And it is reachable through a feature this same milestone shipped: `dataset_import`
writes a bundle's document as a new version of an existing lineage id, so a bundle whose labels,
blueprint version or author differ from the store's copy produces exactly such a lineage.

Fixed by moving **every** filter after `$replaceRoot`. `archived` moved with the rest, even though
R-34 makes it lineage-level and pre-filtering it would therefore be equivalent *today*: an
optimisation whose correctness depends on an invariant enforced two modules away is the shape of
thing that outlives the invariant, and SQL does not pre-filter it either.
Cost, stated: the group now runs over the whole collection rather than over the filtered subset.
`datasets_lineage` (`{id, version desc}`) serves the sort that feeds it, and the volumes are a local
authoring instance's — the same trade R-39(a) accepted for the `q` filter and M6 accepted for
`_by_labels`. Identical results first; contracts section 7 licenses the different plan.

**And the conformance gap is the more important half.** Every other test in the file writes the
*same* document twice, so both readings of R-38 agree on every fixture that exists and the suite was
blind to the difference. Two tests added, immediately after the test whose blind spot they fill:
`test_find_filters_the_latest_version_and_not_whichever_version_matched` (version 1 matches, version
2 does not — the discriminating direction) and
`test_find_matches_a_lineage_that_only_the_latest_version_satisfies` (the converse, which both
readings pass and which pins that the filter reads the *latest* version's fields). All four filters
each, because the defect was in one `$match` carrying all of them and fixing one would have fixed
none. A `heterogeneous` fixture publishes two more blueprints, because `datasets` has a foreign key
on `(agent_id, bp_version)` and a version-2 document pointing elsewhere needs somewhere else to
exist.
Verified: 4 failed on Mongo, 8 passed on SQLite and 8 on Postgres, before the fix.

## [M7, fix round 1] Ruling R-57 applied to every site carrying the struck claim, found by grepping for the claim
`sql.py` still told the next author, in three places, that an `ILIKE`/`pg_trgm` prefilter was fine
"provided it matches a *superset* of what the Python fold matches; Postgres's `lower()` and Python's
`casefold()` are close enough for that to hold" — the exact sentence M7 measured false. The
correction lived ~1200 lines away in `POSTGRES_ONLY_INDEXES`, so the file contradicted itself and
**the stale half was the one sitting on `_apply_dataset_filters`**, which is the method a future
author would edit.

This is R-57's own general lesson reproduced one layer down: a claim naming an implementation, left
un-re-read when the mechanism changed. It happened twice in one milestone — the same shape as the
splitting guarantee below — which is the argument for **grepping for the claim rather than fixing
the site you remember**. Doing that found two sites the review had not listed:
`tests/unit/test_service_expansion.py`'s own module docstring still asserted the splitting property
its own test disproves, and `service/expansion.py::_grown` contradicted its own module docstring.
Both fixed. All three `sql.py` sites now cite R-57 and say **do not add one**, with the measurement
and with R-39(a)'s named remedy (a pre-folded search column) as the thing that would actually work.
`_byte_ordered` cites R-58 the same way.

## [M7, fix round 1] Correction: the disproved splitting guarantee was published on the tool surface
`dataset_expand`'s docstring — which is what an LLM caller reads when it lists the tools — said
"expanding by 5 equals expanding by 2 then 3". The module docstring and the test recorded that this
is **false**; the caller-facing description and `_grown`'s docstring did not.

A false determinism guarantee on the tool surface is worse than one in a comment, because a caller
can act on it: an agent that believed it could split a 10,000-entry expansion into ten calls and get
the same pool would get ten different pools and no error. Replaced on both with the guarantee that
was actually proved — the same request against the same dataset reproduces exactly, an entry already
written keeps its content when the pool grows again, and expanding in two calls is *not* the same as
expanding in one because a new entry is drawn from the pool it is added to.

My report said the guarantee "is now what is asserted", which was two-thirds true. Recording that
rather than quietly fixing it: the fix I described was the fix I had made to two of the four sites.

## [M7, fix round 1] `Seeded.int` enforces the premise of its own termination proof
`ceiling = _DRAW_SPACE - (_DRAW_SPACE % span)` is **0** when `span > 2**64` — `span == 2**64 + 1`
gives `_DRAW_SPACE % span == 2**64` — so `while True:` never satisfies `drawn < ceiling` and the
method **hangs**. The docstring stated the precondition as a fact ("`span` is at most `2**64`") and
nothing enforced it, and the two tests around it stopped at exactly the last value that works:
`test_int_covers_the_whole_storable_range` uses a span of exactly `2**64`, and the property test
capped `width` at `10**9`.

Fixed with one `raise` beside the existing `lo > hi` guard, and the docstring now says the bound is
enforced rather than assumed. **A termination proof whose premise nothing checks is not a proof.**

Latent today — `service/limits.py` bounds every integer that reaches here to 64 bits — and the
reason it is worth a guard rather than a note is the shape of the failure it would become: an
unbounded `drift_s` arriving at `Seeded.timestamp` at M8 or M9 is a wedged process with **no
exception and no log**, which is the most expensive thing to diagnose. `timestamp` is the reachable
route and has its own test.
Both ends, per the standing rule: `test_the_widest_working_span_is_exactly_two_to_the_sixty_four`
covers `2**64` from three directions and `test_int_refuses_a_span_wider_than_a_draw_instead_of_hanging`
covers `2**64 + 1` from three. Verified the old code hangs rather than fails: with the guard removed,
`Seeded(5).int(0, 2**64)` on a daemon thread was **still running after 5 seconds**.

## [M7, fix round 1] The Mongo losing side has its own race suite
`test_sql_allocation_races.py` is SQL-only by design and says so, which left the equivalent branches
in `mongo.py` **never executed** — the `continue`-versus-`raise` discrimination that decides whether
a real fault gets buried under eight retries, and `set_step_actual`'s loop-back that decides whether
a losing writer receives the winner's value.

Those branches are the whole reason the retry pattern is portable. R-37 and R-39 both turn on "the
guard is a unique key on every backend"; the guard being a *different exception class* on this one is
exactly the kind of thing that looks handled and is not. So
`tests/integration/test_mongo_allocation_races.py` mirrors the SQL file with `DuplicateKeyError` in
place of `IntegrityError`: six tests covering the version race, the unrelated-duplicate
discrimination, the `seq` race, the step-key no-op convergence, `set_step_actual`'s losing writer,
and two concurrent first writes of one blueprint converging on R-29's no-op success.

**It needed three test seams in the adapter, and adding them is the point rather than a cost.**
`put_blueprint`, `put_skeleton` and `put_run` read existence inline, so the losing branch had no way
in. They now go through `_blueprint_doc`, `_skeleton_exists` and `_run_exists` — which is exactly
what `sql.py` did at M3 and for exactly the reason its docstring records: "the losing side of a
concurrent first write is reachable in a test by making this return `None` once". A read spelled
inline is a branch with no way in.

## [M7, fix round 1] Correction: the server-defaults test exercised one of nine
Its docstring promised a row omitting "every one of them" and named `sa.false()` as the sharpest
case; the body inserted one `skeletons` row and read back `parts`. `datasets.archived`,
`runs.warnings`, `runs.external_refs`, `runs.run_class` and the four defaulted timestamps were never
exercised, and the M7 report repeated the overclaim.

Now `SERVER_DEFAULTS` is a table of all nine and `_defaulted_rows` inserts one row per table omitting
every defaulted column, on both dialects, through SQLAlchemy Core rather than raw SQL — because a
`jsonb` column needs a cast from a text parameter on Postgres and does not on SQLite, and
hand-writing that is how a test ends up proving something about its own SQL.

`test_the_defaults_table_covers_every_declared_default` compares the table against `METADATA` itself,
so a column that gains or loses a `server_default` fails rather than being quietly unexercised —
which is precisely the failure the original had. Verified: removing `server_default=false()` from
`datasets.archived` fails it with `stale: [('datasets', 'archived')]`.

## [M7, fix round 1] The one field expansion varies is now tested, and the clamp is tested exactly
No golden pool entry carries `latency_hint_ms`, so `_replicated`'s jitter — the only `Seeded.int`
call site in `src/` — was never exercised and the clamp arithmetic had no case.

**The first attempt at the clamp test was vacuous and that is the part worth recording.** It asserted
the *stored* hint fell inside the expected band, and at `MAX_STORED_INT` it **passed without the
clamp**: the draw happened to land below the column width, so the test proved nothing about the case
it was written for. Verified by removing the clamp — the negative case failed, the at-the-width case
did not.

So the range became the unit. `jittered_range(hint)` is now a named public function, and
`test_the_jitter_range_clamps_at_both_ends` asserts it exactly at seven points including
`doubles-past-the-width`, where the unclamped upper bound is above `MAX_STORED_INT` while the lower
bound is not — the case no draw can hide. A `hypothesis` property test asserts `lo <= hi` and both
ends storable over every `int`. With the clamp removed, five of ten fail, including that case and
`hint=-1` from the property test.

The two clamps prevent different failures and both are R-50 one layer in from the boundary: the top
stops the expansion writing a `latency_hint_ms` no backend can store, and the bottom stops
`lo > hi` raising `ValueError` out of `Seeded.int` where an envelope belongs. A negative
`latency_hint_ms` is nonsense but nothing forbids it — R-04 keeps value constraints out of the models
and no `DS-*` rule has an opinion about this field.

## [M7, fix round 1] The compose anchor's claim is now literally true
`x-service` claimed "the only difference between the two profiles is the store URL", and YAML's `<<`
**replaces** a mapping rather than merging into it — so both services re-spelled
`AGENTPROPS_HTTP_HOST` and `AGENTPROPS_HTTP_PORT` and the claim was not literally true.

Made true rather than corrected: the two HTTP variables were already set by the image (`ENV` in the
Dockerfile), so they came out of the compose file entirely and each service's `environment` now
carries the store URL and nothing else. That is one place per variable, and it is right for a bare
`docker run` too. Putting them in the anchor would have been worse than either option — they would
have looked shared and been dead, discarded by both services the moment each declared an
`environment` of its own, which both must for the store.
`test_containers.py` follows: `test_each_service_names_the_store_variable_the_entry_point_reads`
asserts each service's environment is **exactly** `[AGENTPROPS_STORE]`, which is what makes the claim
checkable, and a new `test_the_image_sets_the_http_host_and_port_the_entry_point_reads` asserts the
other half in the other file. The guard now checks both places, which is stronger than the one it
replaced.

## Questions for the owner — M7 fix round 1
1. **`find_datasets` now groups over the whole `datasets` collection on Mongo** rather than over a
   pre-filtered subset, because a pre-filter changes which version is the row. If Mongo is ever the
   backend for a large shared instance, the shape that keeps both the grain and the pre-filter is a
   `latest: true` flag maintained by `put_dataset` — one more write per version, and an invariant to
   keep. Not built: R-39(a) and M6 both accepted the same trade for the same reason, and a measured
   problem is the trigger.

---

## [M7, fix round 2] Ruling R-60: the published host ports and the test defaults move off 27017 and 5432
`docker-compose.yml` publishes Mongo on **27117** and Postgres on **5442**, and
`tests/conftest.py`'s default test URLs dial exactly those. Container-internal the ports are still
27017 and 5432; only what the host publishes and what the test defaults reach have changed.

Reason, and it is a count rather than an argument: **the wrong-server hazard fired three times.**
M7's first `--store mongo` run passed against an unrelated host MongoDB. It recurred while I
measured the containerless figure. And the third was the reviewer's own gate run, which returned
`1384 passed / 17 skipped` against my `1400/1` (servers up) and `1370/31` (URLs at dead ports) — a
difference of exactly **14**, the same 14 Mongo tests, the same foreign server.

R-59(b)'s mitigation was overridable ports plus skip messages naming the URL. That made the hazard
*diagnosable*, which is how the third occurrence was found, and it did nothing about the hazard
itself: neither an override nor a message changes what the **default** reaches. A default that can
connect to a foreign server is the defect.

**The damage was never the stray database.** It was three different test counts for one commit,
which makes the number evidence of very little. The count is the thing this project sells — PRD
design principle 2 is "hold the environment byte-identical" — so a suite whose own count depends on
what happened to be listening is failing at its own thesis.

**Structural rather than a probe.** Rejected: a sentinel "is this our schema?" check. It adds a
round trip and a failure mode, it needs explaining, and it still has to connect to the wrong server
to discover the server is wrong. Choosing a port nobody else uses ends the question instead of
detecting it. R-50 set the precedent that two occurrences is a pattern and three is a policy
failure; this is the third, and the answer is the same shape — remove the reachability rather than
document the risk.

Updated everywhere the ports are named: the compose file (with the reasoning, both failure shapes),
the test defaults, `CLAUDE.md`'s commands block, the README, and the compose drift guard. The skip
messages interpolate the URL and therefore came along for free — and they stay, because they are
what made the third occurrence diagnosable.

**Two guards, in opposite directions, and both were verified to fail.**
`test_the_published_host_port_is_not_the_one_a_foreign_server_sits_on` fails if either port drifts
back to 27017 or 5432 — the hazard returning. `test_the_published_host_port_is_the_one_the_test_urls_dial`
fails if `docker-compose.yml` and `tests/conftest.py` drift apart, which is the **silent** direction:
the suite would dial a port nothing publishes, every backend test would skip, the run would be green,
and the skip message would name a URL that looks perfectly reasonable. That is the same class of
failure R-60 is about, so it gets the same kind of guard.
The parser earned a note of its own: `str(mapping).split(":")[-2]` on
`"${AGENTPROPS_MONGO_PORT:-27117}:27017"` returns `-27117}`, which `int()` reads as a **negative**
port — so the guard failed, correctly, for the wrong reason. It now matches the interpolated form
with a regex and raises rather than returning a number it could not parse.

**Three sites the first pass missed, and how.** Grepping for the *number* rather than for the files
I remembered editing - fix round 1's lesson - turned up `server/__main__.py`'s usage block telling a
developer to run `--store mongodb://localhost:27017/agentprops`, and two `storage/mongo.py` URL
examples on `:27017`. Only the first is a copyable instruction; the other two illustrate path
parsing, where the port is incidental. All three moved anyway, so that a grep for `27017` under
`src/` returns nothing but the R-60 explanations. An invariant is cheaper to hold than a judgement
about which examples somebody might copy.

The Mongo test database name stays a constant in the fixture and is still never taken from the
URL's path. The port move removes the *reach*; the constant removes the *blast radius*, and an
override can still point the suite anywhere.

## [M7, fix round 2] Ruling R-61: the `alembic check` residue is stated beside the evidence, not fixtured away
Ratified as diagnosed. The requirement is on the *report* rather than the code: the acceptance
evidence — "`alembic upgrade head` then `alembic check` clean against real Postgres" — is only
reproducible **from a clean database**, and the report now says so with the reset step pasted above
the output.

**Explicitly not fixed by stamping `alembic_version` in the fixture.** That would make `alembic
check` pass by fabricating the state it exists to verify, which is the same defect class as a
vacuous test — and this build has now caught four of those. The residue is correct behaviour for a
fixture that owns its schema with `create_all` and for a test that exercises `downgrade base`.
`test_the_migrated_schema_matches_sql_py[postgres]` runs the same comparison `alembic check` runs,
against a database it migrated itself, and is the assertion that actually holds the line — the CLI
invocation is evidence for a human, not the guard.

## [M7, fix round 2] The reproducible test count, and what a backend number means without its URL
Three figures were reported for one commit during M7. After R-60 there is one per configuration and
both containerless spellings agree, which is the property that makes the number worth quoting:

| configuration | count |
|---|---|
| nothing running, nothing set — **the CI shape** | 1374 passed / 31 skipped |
| both test URLs forced at dead ports | 1374 passed / 31 skipped |
| both containers up on 27117 / 5442, nothing set | 1404 passed / 1 skipped |

1405 either way, which is the arithmetic that says no test was lost. The 30 skips are the three
suites that need a *second* store by definition: `test_promotion_crossing.py` (16),
`test_migrations.py`'s Postgres dialect parameter (8) and `test_mongo_allocation_races.py` (6).

**And the general rule R-60 asks be recorded: a backend count without its URL is not a claim.** A
passing `--store mongo` proves the adapter works against *something*; only the URL in the connect or
skip message says what. Every backend figure in the M7 report now carries the server version and the
port it was measured against.

## [M7, fix round 2] Every backend figure was measured with one pytest process, because the reset is destructive
Found while measuring this round, and it belongs beside R-60 because it is the same shape: **a
number that depends on what else was running.**

To save wall clock I started a second `pytest -m integration --store mongo` while the gate's own
Mongo leg was running. Both share one MongoDB and one database name, and the `store` fixture
**drops that database before every test**, so the two runs deleted each other's rows: `14 failed,
118 passed` on one and `5 failed, 263 passed, 9 errors` on the overlapped triple-store leg. Every
failure was a row count or a latest-version assertion - exactly the signature of a foreign
`drop_database` mid-test - and `263 + 5 + 9 == 277`, the total the leg passes alone. Re-run
sequentially, both are green: 133 and 277.

The fixture is not wrong. Per-test isolation *within* a run is what it promises and it delivers
that; isolation *between* concurrent runs is not something a constant database name and a
destructive reset can provide. So the constraint is recorded rather than coded around: **one pytest
process at a time against a shared server**, which now sits in the reproduction recipe next to
R-61's "from a clean database" and R-60's URL.

Deliberately **not** fixed here by suffixing the database name per session. That edits the fixture
that owns the destructive reset, in a round whose instruction was narrow, and the round's own lesson
is that a fix can defang the thing that guarded it. Left as a reported concern. Whatever the suffix
ends up being, the blast-radius rule survives it: the name stays a constant *of ours* plus a
session suffix, and is never a value read from the URL.

Worth writing down mainly because of how close it came to being reported as a result. A 14-failure
Mongo run, in the round that exists because a number was not reproducible, would have been the same
mistake in a new costume.

## [M7, fix round 2] Where `hypothesis` actually is, and a summary sentence that denied it
`build-handoff.md` line 290 reads "Property-based testing with `hypothesis` is worth it in exactly
one place: the seeded expansion". The tree has **eight** property tests in **three** files:
`tests/unit/test_seeded.py` (5, the primitive), `tests/unit/test_service_expansion.py` (1,
`jittered_range`, added in fix round 1) and `tests/unit/test_mongo_key_codec.py` (2, the escape
codec).

The first two are the subject line 290 names. The codec is not, and it is a deliberate deviation:
its whole property is injectivity over an adversarial alphabet - `%.$2E45abc`, almost entirely
escape characters - which is exactly what a property test buys and exactly what an example table
cannot cover, since the interesting inputs are the ones nobody thinks to write down. Two tests, one
file. Recorded when it was made, above, and described in section 4 of the M7 report.

**What was wrong was the summary sentence.** Section 2 of that report claimed `hypothesis` "is used
in exactly the one place `build-handoff.md` names - five property tests in `tests/unit/test_seeded.py`",
and section 4 of the same report then describes the codec property test in plain words. The report
contradicted itself from the day it was written, and the false half was the half a reader auditing
the constraint would stop at. A summary that denies the detail beneath it is worse than no summary,
because the summary is the part that gets quoted.

Corrected in the fix-round-2 appendix rather than edited in place, the same way the superseded test
counts are listed rather than silently rewritten. If line 290 is meant as a cap on the total rather
than as a naming of the subject, this needs a ruling; either way it needed stating rather than
denying.

## [M7, fix round 3] Ruling R-62: `build-handoff.md` line 290 names a subject, not a ceiling
Ratified as I read it, and the requirement is on the *report* rather than the tests: section 2's
claim that `hypothesis` is used "in exactly the one place `build-handoff.md` names - five property
tests in `tests/unit/test_seeded.py`" is corrected to match section 4, which describes the Mongo key
codec's property tests in plain words. **All eight stay.**

The ruling's reasoning is the part worth keeping: an escaping scheme is a function that must be
reversible and collision-free over an infinite input space, and no table of examples establishes
that. `encode_keys`/`decode_keys` is what stands between a section id containing a dot -
`nodes.core`, `nodes.branches`, which M7 was instructed not to rename - and a silently mangled
`Skeleton.parts`. Round-trip *and* injectivity over an alphabet built from the escape characters
themselves (`%.$2E45abc`) is precisely what the technique buys.

So the deviation was in the *summary*, not in the tests: the decision had been recorded here and
described in the report, and then a sentence eighty lines above denied it. Corrected in the fix-round
appendix rather than edited in place, which is the same treatment the superseded test counts get.

## [M7, fix round 3] Ruling R-63: the test database is named per process
`conftest.TEST_DATABASE_NAME` is `agentprops_conformance_<pid>_<random>`, computed once at import so
it is constant for the life of a process and different in any other. Postgres gets a real
`CREATE DATABASE` of that name; Mongo simply uses it. Both are dropped on session exit.
`postgres_test_url()` is now the **server** - the database that is connected to in order to create
the session's own - and `postgres_session_url()` is what the tests and alembic receive.

**Why a convention was not enough, which is the whole content of the ruling.** The `store` fixture
drops its database *before every test*, because half the conformance suite counts rows. That is
correct isolation within a run and none at all between two of them. And the failure does not look
like a collision: it looks like a suite finding bugs. Measured, deliberately, as a negative control -
R-63 removed, two concurrent `--store mongo` runs on one server:

| | with a shared name | with the per-session name |
|---|---|---|
| run A | 6 failed, 123 passed, 4 errors | **133 passed** |
| run B | 10 failed, 98 passed, 25 errors | **133 passed** |

Every failure is a row count, a latest-version assertion or a fixture error. M7 came within one
paste of reporting one such run as a real result.

**This is R-60's lesson applied the same day it was ruled**, and R-63 is right that it is the worse
of the two: the wrong-server case produced suspiciously *passing* numbers, which is the easier thing
to notice, while this one produces a plausible failure count. Of the three reproduction preconditions
M7 was about to leave documented-but-unenforced - clean database, one process, the URL beside any
backend count - R-60 converted one to structure and this converts the second. R-61's stays
documented, and correctly: a clean database is inherent to a fixture that owns its schema, and
stamping `alembic_version` to avoid it would fabricate the state the check verifies.

**The pid is in the name on purpose and it is the useful half.** Live processes always have distinct
pids, so no two concurrent sessions can collide. And a *stray* database is triageable, because a
developer can ask whether that pid is still running. The random suffix is only there to stop a
recycled pid from silently adopting an older run's leftovers.

**Cleanup, and the sweep for when cleanup cannot run.** Both fixtures drop their database on the way
out; verified by checking for stragglers after a clean exit and after two concurrent runs - zero on
both backends each time. A process that is *killed* runs no teardown, so the prefix is a constant a
shell pattern can match, and the sweep lives in `tests/integration/conftest.py`'s module docstring.
Both halves were run against the containers, and the Postgres half needed a second attempt: the
generated `format('drop database %I with (force)', ...)` had no trailing semicolon, so `psql` read
two statements as one, reported a syntax error and dropped nothing. It is in the docstring with the
semicolon and with that note.

Cleanup **warns rather than raises** when it fails, and the warning carries the database name. A
green run that could not tidy up is still a green run; turning teardown into an error would make the
test count depend on the tidying, which is the class of problem R-60 and R-63 both exist to remove.

Rejected: a per-session Postgres **schema** with `search_path` instead of a database. It needs
alembic's `version_table_schema` plumbed through, it puts every concurrent run in one database's
catalogs, and it is a second mechanism to explain. A database costs one `CREATE DATABASE` in
`AUTOCOMMIT` and needs no plumbing at all, because the isolation lives in the URL.

**Three guards, and all three were verified to fail.** `test_two_sessions_compute_different_database_names`
asks three real subprocesses for the name, because a per-process value compared with itself inside
one interpreter agrees by construction - with R-63 removed it reports
`['agentprops_conformance', 'agentprops_conformance', 'agentprops_conformance']`.
`test_every_session_name_starts_with_the_prefix_the_sweep_looks_for` protects the sweep, and fails on
the same removal. And `test_no_test_file_uses_the_bare_prefix_as_a_database_name` covers the silent
direction: one hard-coded name in a suite of unique ones is worse than uniform sharing, because
everything passes until two runs overlap and then only *those* tests corrupt each other. Planting
`MongoStore(client, "agentprops_conformance")` in `test_mongo_allocation_races.py` fails it with the
file and the line number.

**That last guard earned a rewrite.** It began as a regex over the file text and failed on its own
module docstring, which quotes the mistake concretely. A guard that cannot tell prose from code has
to be exempted from itself - making the one file most likely to describe the mistake the one file not
checked for it - or it forces the documentation to stop being concrete. So it parses: an `ast` walk
that collects string constants equal to the prefix and excludes docstrings by position. Comments
never reach the AST at all. `test_the_bare_prefix_guard_sees_a_value_and_not_a_docstring` pins all
four cases, including a function that has both a docstring and a value.


## [M8] `record_step` and `run_finish` answer a re-write the way `run_start` answers a re-start
Both tools can meet a value that is already recorded, which no read path can. Ruling R-53 already
settled the shape for `run_start` — keep the stored value, return it, attach a warning naming the
divergence — and M8 applies it to both writes rather than inventing a second answer: a repeated
identical call is a **silent** no-op success, and a divergent one is a success carrying
`step_actual_conflict` or `run_finish_mismatch`.
Reason: ground rule 3 decides it without a new principle. "Mismatches produce warnings attached to
the response and to the stored run", and a retried request after a network blip must neither fail
nor overwrite — which is the whole reason the run id is generated up front. A step's recorded
`actual` and a run's `outcome` are *evidence*, and evidence a second caller can overwrite is worth
less than none; evidence a second caller cannot report at all is a gate.
Alternative rejected: an error envelope for the conflicting write, on the model of `SK-005` for a
lost skeleton CAS. SK-005 is the **authoring** flow, where validation is strict at write time; these
two are the runtime, where the service never gates. There is also no code that fits — `RT-E01..04`
are resolution failures and `AP-001..006` are boundary conditions — so refusing would have meant
inventing a rule id for a policy verdict, which is what ground rule 3 forbids.
Also rejected: silently accepting the second write. That is the only option that loses information,
and it loses the half a developer needs (the agent produced two different answers for one step).
Residue, stated: a caller that ignores warnings sees a success and assumes its own document was
stored. The response carries the **stored** step, so the value it gets back is the recorded one
rather than its own — which is what makes ignoring the warning survivable rather than silent.

## [M8] `Store.mark_run_finished`: a compare-and-set on three columns
`run_finish` writes through a new narrow Protocol method rather than through `put_run`.
Reason, twice over. **The column bound** is `set_run_warnings`' guarantee mirrored: `put_run` writes
every column from the model it is handed, so finishing a run through an edited snapshot would revert
the `warnings` a concurrent `fetch_step` had just merged in. M6 fixed that bug in one direction and
recorded that it was unreachable only because `run_finish` did not exist; it exists now, so the
counterpart write had to be equally narrow or the fix would have been half a fix. **The
compare-and-set** is ruling R-47's argument: an unconditional update makes the loser of two
concurrent finishes disappear silently *and* hands both callers a success envelope naming their own
outcome while the row holds one of them. The database decides once, and the boolean it returns is
the whole of the service's branch — one decision site, no read-then-write.
`finished_at IS NULL` is the predicate rather than `status = 'running'`: both terminal statuses set
it, so one clause covers `finished` and `abandoned` and the Protocol holds no opinion about a
vocabulary it does not own.
Cost, stated: a Protocol method added after M7, so two signed-off adapters implement one more
method. R-33 and R-55 both made this trade in the other direction (add it early, cheaply); this one
could not be, because the tool that needs it is M8's. Four conformance cases, so both adapters
inherit them.
Alternative rejected: `put_run` with a fresh read. Observably identical in a sequential test — which
is exactly why it is dangerous, and it is recorded in the conformance test's own docstring: a
planted fresh-read `put_run` **passes** that test, and only the service-level stale-read test catches
it. A narrower window is a smaller version of the same bug (ruling R-37).

## [M8] What `run_finish` touches, and what it deliberately does not
Touches: `status`, `outcome`, `finished_at`. Nothing else — not `warnings`, not `steps`, not
`external_refs`, not `declared_bp_version`, not `started_at`.
Does **not** validate `outcome` against the blueprint's `outcome_schema`, and does not compare it
with `expected.final`. Ground rule 2: the service stores expectations and emits evidence, and the
comparison lives in the client. A run whose agent produced nonsense is a run whose evidence records
nonsense; validating here would turn a finding *about the agent* into a refusal to record what the
agent did, and would put the client's own comparison on the write path.
There are tests for the losing side of both halves — an `actual` the node's `output_schema` rejects
and an `outcome` the blueprint's `outcome_schema` rejects are both stored verbatim.
`status` is `finished` or `abandoned`; `running` is refused with `AP-001` naming the vocabulary, the
way `run_class` is. Reason: the tool *closes* a run, and accepting `running` would advertise an
un-finish that the compare-and-set cannot perform anyway.

## [M8] Ruling R-54(c)'s open half is taken as the warning, not the refusal
`fetch_step` and `record_step` against a run whose `finished_at` is set attach a
`run_already_finished` warning and serve normally.
Reason: R-54(c) says a finished run still serves and "M8 may add a warning if it proves useful; it
must not add a refusal". It proves useful — an agent still fetching steps after its harness closed
the run is a real defect in the harness, and nothing else in the response says so. The read path is
pin-scoped and immutable, so serving is not the defect.
Keyed off `finished_at` rather than `status`, for `mark_run_finished`'s reason: one predicate covers
both terminal statuses. The detail carries no `node_id`, so R-54(b)'s merge key records it **once
per run** rather than once per step — an agent looping against a closed run must not grow the stored
warning list without bound, and every response carries its own copy regardless.
Tested from the losing side: the fixture served after the close is compared **byte for byte** with
the one served before it, and a step the run had never served is fetched *after* the close. A test
that checked only the warning would pass against a `fetch_step` that had started refusing, and a
planted refusal fails both.

## [M8] `record_step`'s two failure classifications, and why the service re-reads to choose
`set_step_actual` raises `StoreError` for two different conditions: a *different* actual is already
recorded, and its retry budget ran out with nothing recorded. `service/runs.py::_conflicted`
re-reads the step and classifies — a recorded differing actual becomes the `step_actual_conflict`
warning, and no recorded actual at all becomes `AP-005`.
Reason: reporting contention as a conflict would tell a caller its evidence lost to a value that
does not exist. This is not a second decision site for the *write* — the store made that decision,
once, in its own single-decision loop — it is classification of the refusal the store returned.
An actual for a step that was never served is `AP-004` rather than an `RT-*` code, because
resolution *succeeded*: the node exists in the blueprint and the run exists in the store, and what
names nothing is the step key. The pointer addresses whichever argument the caller used to address
the step, so a `tool_name` caller is not pointed at a `node_id` it never sent, and
`resolved_node_id` rides in the context either way.
Verified: with the classification removed, the contention case reports a conflict warning on a
success envelope, and the test fails.

## [M8] The client is packaged separately, and depends on nothing of the service's
`client/python/` is its own distribution: `agent-props-client`, its own `pyproject.toml`, its own
`hatchling` build, and **no** dependency on `agentprops` — not a path dependency, not a version
pin, not an import.
Reason: a client speaks MCP to a *server*, which may be another process, another machine or another
language (M11 is the TypeScript one). A path dependency would make "separately installable" false
and would be invisible in this repository, where the service is installed anyway. So the evidence is
a wheel built into a throwaway venv that then imports the client and grades a document, with the
service's *absence* asserted — `tests/unit/test_client_packaging.py`.
It is wired into this repository's **dev** dependency group with a `[tool.uv.sources]` path entry, so
one `uv run pytest` covers both packages: the end-to-end test needs the client and the server in one
process. The dependency direction is service→client for *testing* only, and the client's own
metadata carries none of it.
Alternative rejected: one distribution with an extra (`agent-props[client]`). It would make the
client's release cadence the service's, and it would put the whole service — SQLAlchemy, pymongo,
alembic, opentelemetry — behind `pip install` for someone who wanted three comparison functions.

## [M8] `compare.py` imports `jsonschema` and nothing else, and a guard measures it
The three helpers depend on exactly one third-party library, for the Draft 2020-12 validator
`schema` mode needs. `run.py` and `envelope.py` depend on none. `session.py` is the only module that
imports a transport, and the package's `__init__` reaches `connect`/`connect_async` through PEP 562's
module `__getattr__` so that `from agentprops_client import subset` pays for nothing.
Reason: ground rule 2 promises the helpers are usable by someone with two documents, no server and
no network. That is only a real property if the import graph says so.
**The guard is an allowlist over the whole transitive import set, and the allowlist is measured
rather than typed**: in a fresh subprocess, what `agentprops_client.compare` imports minus what
`jsonschema` alone imports must be the client's own modules and nothing else. A denylist of today's
network libraries holds only against the names someone thought of; this fails on anything new, by
name, with no edit to the guard. An audit hook fails the probe on any socket or SSL audit event
during import, which covers a module that opens a connection without `socket` ever appearing.
`socket` is the named sentinel because it is the chokepoint — every stdlib network path reaches the
OS through it. `_socket`, the C extension, is deliberately **not**: `typing_extensions` imports it to
read a C-API capsule, so a guard on `_socket` would fail today because of a *typing* library. That is
measured in the test rather than asserted in prose.
Verified failing against three plants: `import socket` in `compare.py`, a transport import in
`compare.py`, and an eager `session` import in `__init__.py`. An eager **run** import passes all
seventeen assertions — so the lazy import is load-bearing for `session.py` and a consistency choice
for `run.py`, and the module docstring says exactly that rather than claiming both.

## [M8] The sync/async split: two facades over one set of pure functions
`RunClient` and `AsyncRunClient` share `_Requests` (argument dicts) and `_Responses` (envelope
readers). Each method is one line: build the request, call the transport, read the response.
Reason: the two APIs cannot disagree about what a request looks like or what a response means, and
`test_the_two_clients_send_identical_requests` records both and compares them — so a parameter added
to one facade and forgotten in the other fails a test rather than shipping.
Alternatives rejected. **Generating the sync client from the async one** (an `unasync`-style build
step) makes the code a reader sees different from the code that runs. **Wrapping every async method
in `asyncio.run`** re-opens the session per call, which for a stdio server spawns the process again
per step, and hides the lifecycle where a caller cannot see it.
The transport owns the bridge instead, once: `anyio.from_thread.start_blocking_portal` plus
`portal.wrap_async_context_manager`. Not a hand-rolled loop thread — an `anyio` cancel scope must be
exited **by the task that entered it**, which `tests/toolclient.py` already paid for with
`RuntimeError: Attempted to exit cancel scope in a different task`, and the portal's context-manager
wrapper is the supported primitive for exactly that. The sync end-to-end test is a *synchronous*
test function, so the portal is doing real work rather than being exercised from inside a loop.

## [M8] `subset`'s array semantics: positional, same length, element-wise
`subset` recurses into objects — a nested extra field is ignored — and over arrays it requires the
**same length** and subsets element *i* against element *i*.
Reason: PRD 5.2 says "extra **fields** ignored", and an array is a value rather than a bag of
fields. An agent that assigned three training modules where two were expected produced a different
answer, not a superset of the right one. Element-wise recursion is what makes `subset` useful for
arrays of objects (`[{"code": "late"}]` matches `[{"code": "late", "days": 4}]`), so the relaxation
that matters is kept.
Alternative rejected: **set-like containment** — every expected element appears somewhere. It would
call the three-module answer correct, it makes order meaningless (which the golden
`assigned_modules` relies on), and matching a multiset under *subset* semantics is a bipartite
matching problem: a grader whose cost is not obvious from its contract is a grader nobody trusts.
Also rejected: whole-array equality, which would stop the recursion at the array and make
`subset` useless for arrays of objects — a caller who wants that has `exact`.
`test_a_superset_array_is_not_a_subset` pins the decision *against* containment specifically: every
expected element is present, in order, with one appended, so a containment implementation answers
`ok` and this one does not. Six rows fail against a planted containment version.

## [M8] Two more comparison decisions PRD 5.2's one-liners leave open
**Numbers compare across `int`/`float`; `bool` is not a number.** `1` and `1.0` are the same JSON
value and differ only in how a decoder spelled them, so `exact` and `subset` treat them as equal.
`True` is a *different JSON type* from `1`, and Python's `True == 1` is the trap — `server/args.py`
records the same trap one layer out and `validation.context.as_int` excludes `bool` so DS-020 cannot
accept `"seed": true`. A grader that called `{"compliant": 1}` a match for `{"compliant": true}`
would pass an agent that returned the wrong type. Four rows fail against a plain `==`.
**`null` is not absence.** `subset` requires the expected key to be *present*, so
`{"escalated_to": None}` does not match `{}`. This is the case a naive `actual.get(key) == value`
gets wrong in the direction that looks like success, which is why it is a row rather than a remark.
Deliberately out of scope: `NaN`. JSON has no `NaN`, the documents come from a JSON store, and the
IEEE rule would make a document unequal to itself.

## [M8] What `schema` validates against, and what it refuses to do
`schema(instance, outcome_schema)` validates the **actual** against the blueprint's `outcome_schema`
and ignores `expected.final` entirely — PRD 5.2's "validates against `outcome_schema` only", taken
literally. Its signature therefore takes a schema rather than an expected document, and
`grade(mode="schema", ...)` requires `outcome_schema` and raises `ValueError` without one.
Reason for raising rather than answering `ok: False`: a missing schema is a programming error in the
caller, and reporting it as a failed comparison would say the agent failed when the harness is
misconfigured. That is the distinction `server/app.py::bound` draws for an unbound store.
`Draft202012Validator` is **pinned**, per CLAUDE.md's style rule and contracts 2.1: letting
`jsonschema` infer a dialect from an absent `$schema` would make the answer depend on the installed
library's default. Every failure is reported, not the first, with the failing keyword as the
difference's `reason` so a caller can branch on `additionalProperties` versus `required` versus
`type` without parsing prose.
Two behaviours inherited rather than smoothed over, and both are table rows so they are visible:
`additionalProperties: false` does not see properties declared in a sibling `allOf` (so such a
schema rejects everything), and a whole float **is** an integer to JSON Schema — `0.0` passes
`{"type": "integer"}` where `exact` would also accept it, and `0.5` does not. That the two modes
happen to agree about numbers is a coincidence, not a shared rule.
The reference registry is empty, so a remote `$ref` is *unresolvable* rather than fetched — which is
what keeps the no-network claim true of the module's behaviour as well as its imports, and there is
a test for it.

## [M8] The client raises for a failure envelope, and never for a warning
The typed methods (`run_start`, `fetch_step`, `record_step`, `run_finish`) raise `ToolError` on
`{ok: false}`; `call()` returns the parsed envelope and raises nothing.
Reason: "structured errors, never exceptions" is a rule about the **service** — an exception cannot
cross a protocol boundary as an answer — and it has already been honoured by the time a response
reaches the client: the failure arrived as data, with a rule id and a pointer. What a Python caller
then wants is a return type it can rely on, so `fetch_step` can be annotated `-> Step` and mean it.
The alternative, a union at every call site, moves the check everywhere and makes the common path
noisy, which is how a caller ends up not checking.
`call()` is the escape hatch and it is load-bearing rather than decorative: the authoring tools are
not part of a run, and PRD 6 flow B's repair loop depends on reading `SK-002` and `DS-017` as
*information*. The end-to-end test's step 3 uses it for exactly that.
**Warnings never raise**, on any path. A client that turned `pool_exhausted` into an exception would
re-introduce the gate the service refuses to be.
A protocol-level fault is kept separate from both: `is_error: true` means the tool *raised*, which
the service promises never to do, so it becomes a `RuntimeError` rather than an empty `ToolError` —
"the server said no" and "nobody answered" need different handling.

## [M8] The end-to-end test is not `expected.expected_path`, and cannot be
The run's reconstructed path is asserted against the **script's own call sequence**, key for key,
plus the first-visit order of every node `expected.expected_path` declares. Not against
`expected_path` itself.
Reason, and it is a property of the product rather than of the test. `expected_path` is a
**traversal** and may revisit a node — the golden one visits `check_docs` twice. A reconstructed
path is the distinct *steps* that were served, and `fetch_step` is idempotent on
`(run_id, resolved_node_id, iteration)`, so the loop's second visit to a `pool: false` node is the
same step: it replays and appends no path entry. Only a pool node can appear twice, because only a
pool node has a second iteration. Separately, section 7's script draws **four** `request_docs`
iterations on purpose, to reach the `pool_exhausted` boundary either side, where the dataset declares
one.
So the two disagree by construction, and a test asserting equality would have to be "fixed" by
weakening the walk — which is the failure mode M7's round warned about. The reframing is stated in
the test's own docstring, with both reasons.

## [M8] `record_step`'s payload carries the resolved node id, not just `{ok}`
contracts section 4 documents the return as `{ok}`, which the envelope itself carries. The named key
(`record`) holds the stored step plus `resolved_node_id`.
Reason: a caller that addressed the step by `tool_name` has no other way to learn which node the key
was built from — the same reason `fetch_step` returns it — and a caller that hit
`step_actual_conflict` needs to see the actual it is disagreeing with. The step comes back **as
stored**, so a caller that ignored the warning still cannot mistake its own document for the
recorded one.
The envelope's `warnings` is **this call's** list rather than the run's, which is `fetch_step`'s
convention. `run_finish` differs deliberately and the difference is recorded in `_finished`: its
payload *is* the run, so the run is dumped with the merged warning list substituted, while the
envelope carries what this call found. Without that split, a second diverging finish would report
the *first* divergence's detail, because `_flag` dedupes `run_finish_mismatch` by `(code, None, None)`.

## [M8] Two guards fired on their own during this milestone, which is the point of them
`test_bounded_integers.py`'s enumeration failed with `('record_step', 'iteration')` named, before
that parameter had a case — the fourth time it has caught a new integer entry point (M6 twice, M7
once). Its `COVERED` row then needed something no input schema could supply: an actual can only be
recorded for a step that was **served**, so the fixture had to fetch one first or the in-range
control would answer `AP-004` and the out-of-range assertion would prove nothing.
`test_tool_surface.py`'s three guards failed together: the tool count, the stale `DEFERRED` entries,
and the per-tool coverage scan naming `record_step` and `run_finish` as registered-but-untested.
That is the mechanism working for the fourth milestone running, and both are recorded here because a
guard that has only ever been green is a guard nobody has evidence for.

## Questions for the owner — M8
1. **`step_actual_conflict` and `run_finish_mismatch` are warnings, not errors.** Ruling R-53 and
   ground rule 3 point that way and the entry above argues it, but the alternative reading — that a
   *write* which cannot be performed should be a failure envelope, the way `SK-005` is for the
   authoring flow — is defensible. If it is wanted, it needs a code: `RT-E01..04` are resolution
   failures and `AP-001..006` are boundary conditions, and neither family fits a policy verdict.
2. **`run_already_finished` is a third addition to the warning vocabulary this milestone.** R-54(c)
   explicitly permits it ("may warn"), and R-22 keeps the vocabulary open. Worth an owner's glance
   because the alternative reading is that a closed run should say nothing at all, which is what M6
   shipped.
3. **`run_finish` accepts `finished` and `abandoned` and refuses `running`.** contracts 2.3 lists
   three statuses; a caller who wants to *reopen* a run has no tool, deliberately. If reopening is
   ever wanted it is a new tool rather than a status value, because `mark_run_finished`'s
   compare-and-set is what makes the first close authoritative.
4. **`subset` over arrays is positional.** Recorded above with its alternatives. It is the one
   comparison decision where a reasonable person might want the other answer, and changing it later
   changes what a stored dataset means rather than only what a helper does.
5. **`grade` is a fourth exported function** beside the three the milestone names. It dispatches on
   `expected.comparison` and adds no comparison logic of its own. PRD 5.2's "every consumer grades
   it the same way instead of each inventing its own rule" is the argument for it existing;
   a purist reading of "three comparison helpers" would leave the `if` to every caller.
6. **The client's `mcp` dependency is required, not an extra.** So `pip install agent-props-client`
   pulls a transport even for a grading-only user. The import graph is clean either way (the guard
   measures that), but a `[grading]`/`[client]` extra split would make the packaging match the
   layering. Left as one distribution because the client's primary job is talking to a server, and a
   default install that cannot do that is user-hostile.

# Fix round 1 — M8

## [M8, fix round 1] Correction: a differing re-write is `ok: false` with `AP-007` (ruling R-65)
The entry above — "`record_step` and `run_finish` answer a re-write the way `run_start` answers a
re-start" — argued that **both** halves of a repeated write are warnings on a success, from R-53 and
ground rule 3. It also flagged, as question 1, that the SK-005-shaped reading was defensible. R-65
rules against it, and the reasoning is the part worth keeping: `ok: true` for a write that **did not
happen** misreports what the store now holds, which is M3's silent-success defect said louder. And
R-33 already makes `set_step_actual` *raise* on a differing actual, so the tool was reporting a
success the storage layer had explicitly declined to give it.
**What was wrong with my ground-rule-3 argument**, precisely: I read "the service never gates" as
covering every refusal. R-65 draws the line where R-56 already drew it — ground rule 3 governs
refusing to **serve**, and a refused *write* is `ok: false` and is not gating. R-56 says it in terms
for DS-023 and I had that ruling in front of me. This is the second time in this build that a ground
rule was stretched one layer past its subject; the remedy both times was a ruling that names the
subject rather than the rule.
**And `AP-*` does fit**, which I said it did not. R-43 created the family for errors "no registry
rule can own", and a write-once conflict is one. One code, `AP-007`, not a new family.
So the split is: **identical → no-op success with a warning; differing → `ok: false` with
`AP-007`.** Two branch points, `_conflicted` and `_finish_divergence`, and nothing structural moved
— no adapter, no Protocol change, no model change.
Where the finding points, and what it carries. `record_step` points at `/actual`: the address
resolved and the run exists, so the only thing wrong with the request is the document it carries.
`run_finish` points at the **first** diverged field in a fixed order, so a status-only divergence
points at `/status` and an outcome-only one at `/outcome`, deterministically — a pointer can address
only one field and picking by a rule beats picking by whichever comparison ran first, with
`diverged` in the context naming every field. Neither carries the *documents*: an `actual` and an
`outcome` are arbitrary agent output with no size bound, and `run_get` is where a caller reads them.
What the caller needs from the finding is that a value is there, under which key, and that it is not
theirs.
Verified from the losing side: with the pre-ruling warn-on-success behaviour planted back, **seven**
tests fail — four service, the wire contract test, and the read-only walk.

## [M8, fix round 1] `AP-007` is distinct from `AP-005`, and that distinction is the reason both exist
`AP-005` means the store refused a write with **nothing** recorded — contention, its retry budget
exhausted. `AP-007` means a value is there and it is not the caller's.
Reason: telling a caller its evidence lost to a value that does not exist is the specific error
`_conflicted`'s classification was written to avoid, and collapsing the two would reintroduce it
under a new name. `_conflicted` re-reads the step and branches on which condition actually holds,
which is not a second decision site for the *write* — the store made that decision, once, in its own
single-decision loop — but classification of the refusal it returned.
`_conflicted`'s third branch is **unreachable by construction** and is written out rather than
asserted away: an identical re-record never reaches it, because `set_step_actual` returns the
existing record and does not raise. Reaching it would mean a value *equal* to the caller's appeared
between the raise and the re-read, which write-once makes impossible; if it ever did, the caller's
intent is satisfied and a success is the honest answer, so that is what it returns rather than
raising a second exception inside an error path.

## [M8, fix round 1] R-65's no-op half carries a warning, and this is the one place I went past the listed scope
The coordinator's scoping said the same-value paths were "unchanged" and that my existing tests
"already do this and stay". They did not: my paths were **silent**. R-65's bullet reads "**no-op
success with a warning** ... nothing changed, and saying so is honest", and the round's verification
step asks to show "an identical one still returning a **warned** success". Two statements of the
ruling against one aside that misdescribed my code, so I implemented the warning.
`run_finish` needed **no new code**: an identical re-finish reports `run_already_finished`, which is
the same code, the same detail and the same helper (`_lifecycle`) that `fetch_step` uses for a closed
run — because the condition *is* the same. `record_step` needed one, `step_actual_already_recorded`,
keyed per step (`node_id` and `iteration` in the detail) because the fact is about a step where
`run_already_finished` is about the run.
Worth recording that R-65's two cited precedents both point the other way: R-29's byte-identical
re-publish is silent, and R-47's CAS loser gets an *error*. The bold text and the verification
instruction are what decided it, and the difference is defensible on its own terms — a re-publish
tells a caller nothing it did not know, where a second writer on one run step is a fact about the
harness.
**How the repeat is detected**, and why that is not a second decision site: `set_step_actual` returns
a `StepRecord` for both a first record and an identical repeat and does not say which it did, so
`record_step` reads the run snapshot *before* the write. It decides nothing about whether to write —
only what to say afterwards, which is the shape `run_start`'s `_replayed` already uses. Residue,
stated: a concurrent writer landing an *equal* actual between the snapshot and the call makes this
call look like the first and report no warning. The stored value is correct either way, and a
*differing* concurrent write is refused with `AP-007` rather than mis-reported.
Cost: one warning-vocabulary addition, on top of the two R-65 removed. Net one fewer than M8 shipped.

## [M8, fix round 1] R-66: `subset`'s array semantics are documented beside the `comparison` field
The implementation was already positional and already pinned by a test. What this round adds is the
**documentation**, in `contracts.md` section 2.2 next to the `comparison` field rather than only in
the helper's docstring.
Reason, which is R-66's: `expected.comparison` is stored in every dataset, so what each mode *means*
is part of what a stored dataset means, and switching to set-like later would silently re-grade every
stored dataset that uses `subset` over an array. A decision with that blast radius belongs where a
dataset author reads, not only where a helper is implemented. DS-017's row now points at it.
The three reasons are the ruling's and worth keeping together: a positional mismatch **names an
index** where set-like matching can only say "no element matched", and an error a dataset author
cannot act on is a worse product than a stricter rule; set-like matching would **mask an ordering
bug**, and R-35 and R-58 spent two rulings making every ordering total and collation-stable so that
order is meaningful; and the **escape hatch already exists** — an author who genuinely does not care
about order uses `schema` mode, whose JSON Schema can describe a set.
The two neighbouring decisions are documented in the same place for the same reason: `null` is not
absence, and numbers cross `int`/`float` while `bool` crosses nothing. Including the note that
`schema` mode is *more* permissive about whole floats than `exact` is — JSON Schema says `0.0` is an
integer — so the modes agreeing about `1` and `1.0` is a coincidence rather than a shared rule.

## [M8, fix round 1] Two docstrings corrected rather than reworded
Both found by the review, and both are the shape this build keeps finding: a docstring more careful
than what it describes.
**`read_envelope` claimed to be "tolerant of the fields it does not need"**, and said that rejecting
a new optional field would break against a newer server. The tolerance is real only *inside* `data`,
`warnings` and `errors`; an unknown **top-level** key is rejected, and
`test_client_run.py::test_a_protocol_level_error_is_not_mistaken_for_a_failure_envelope` pins that.
The docstring now says which half is which, why that is the right way round — contracts section 1
fixes the envelope at two shapes, so a third top-level key means the transport handed back something
that is not an agent-props response — and what the trade costs: a future envelope-level addition
needs the client updated rather than being ignored.
**`connect` said the portal and the session close "in that order"**, which is the opposite of what
the `with` does. The *code* is right and the prose was wrong: the session's `__aexit__` has to run on
the portal's loop and in the task that entered it, so the portal must outlive it — which is exactly
the failure the whole arrangement exists to avoid, and why the two are one `with` rather than two.

## [M8, fix round 1] The end-to-end test asserts the warnings that must be absent
Its non-pool fetches asserted resolution and not an empty warning list, so "nothing spurious" rested
entirely on the pool loop's `== []` and a unit negative control one directory away.
Reason it matters *here* specifically: this file is the milestone's most quotable artifact, and a
walk that asserted only where a warning **is** expected would pass against a server that warned on
everything. Every non-pool fetch on a running run now asserts `warnings == ()`, inside the artifact
rather than only in a guard nobody reads alongside it.
Also: step 4 iterated the *golden* document's keys, so a submitted dataset carrying an **extra**
top-level field would never have appeared in `differing`. The key sets are compared first, which is
what makes "the golden fixture but for its id" exact rather than one-directional.

## [M8, fix round 1] `connect` is driven against a real stdio subprocess
Every other test handed `connect` the in-process `MCPServer` object. That is a real MCP session over
the real server and store (R-16) and it satisfies clause 1 — but it is the one target for which the
blocking portal's whole reason for existing is invisible, because there is no process to re-spawn.
So the script is walked once against `python -m agentprops.server --transport stdio` over a real
pipe, from a **synchronous** test function. What that adds over `test_transports.py`, which already
spawns this process: that file drives it with a raw `mcp.Client` in an `async` test, and this drives
it with the client library, synchronously — the combination a real user has, and the one where a
mistake in the portal's lifecycle surfaces as `RuntimeError: Attempted to exit cancel scope in a
different task` rather than as a failed assertion.
**What it does not prove, stated in the test rather than left implied.** "One session for the whole
block, not one per call" is structural — `connect` enters
`portal.wrap_async_context_manager(Client(target))` exactly once — and no assertion here can observe
the spawn count, because the subprocess's state lives in a SQLite *file* that would survive being
re-spawned. What it does show is that the claim is now made about a target where being wrong costs
eleven process launches, and that the whole authoring flow plus a run walk completes on one session.
It is the only test outside `test_transports.py` that spawns a process, which is why it is in
`tests/integration/` (CLAUDE.md: "no subprocesses in unit tests").

## Questions for the owner — M8 fix round 1
1. **R-65's no-op half now warns, which is one more warning code than the round's scope listed.**
   The ruling's bold text and the verification step both say "with a warning"; the scoping line said
   my silent paths were unchanged and already did it, which was a misreading of my code rather than
   a decision. I followed the ruling. If the intent was silence, deleting
   `step_actual_already_recorded` and dropping `_lifecycle` from `finish`'s success path reverts it
   in about ten lines, and the two tests say which assertions to flip.
2. **`run_finish` reuses `run_already_finished` for an identical repeat.** The condition is
   genuinely the same one `fetch_step` reports, so no code was invented — but it does mean the code
   now appears on a *write* as well as a read. A distinct `run_already_closed_with_this_outcome`
   would separate them at the cost of a fifth vocabulary addition.
3. **`AP-007`'s context omits the documents.** A caller that wants to see what it disagreed with
   makes a second call (`run_get`). The alternative is putting an unbounded agent-produced document
   into an error envelope, which no other finding in this product does.

# Fix round 2 — M8

## [M8, fix round 2] Retraction: the identical re-write is **silent** (R-65 amended)
**The `[M8, fix round 1] R-65's no-op half carries a warning` entry above is retracted in full.**
It is left standing rather than edited, so the reasoning that produced the wrong answer stays
readable — the treatment M3's fix round 2 and M4's fix round 1 gave their own corrections.

What it decided, and what replaces it:

> `run_finish` needed **no new code** ... `record_step` needed one,
> `step_actual_already_recorded`, keyed per step

`step_actual_already_recorded` is **deleted**, and `run_already_finished` returns to the **read
path only** — attached by `fetch_step` and by nothing else, which is where R-54(c) put it and
R-67(a) ratified it. An identical re-record and an identical re-finish are both **silent** no-op
successes.

**Why the entry was wrong, and the part worth keeping.** I implemented R-65's bold text while
recording that its two cited precedents contradicted it — R-29's byte-identical re-publish is
silent and R-47's compare-and-set loser *errors*, so neither supports warning. That was the right
order of operations: implement the ruling, flag the broken justification, and name the revert. The
owner amended the ruling. **The lesson is not "trust the citation over the text" but "a ruling
whose precedents point the other way is a ruling to query, not to work around"** — and the query
belongs in the report, which is where it went.

**And silent is better on merits**, which is the reasoning to keep rather than the citation
archaeology. A caller re-recording an identical actual is a client **retrying after a timeout** —
the case R-53 kept `run_start` idempotent for — so success is the *expected* outcome. A warning on
an expected outcome is noise, and a vocabulary that fires on expected outcomes trains callers to
ignore it. Warnings in this product mean "something happened you would not have predicted":
`pool_exhausted`, `dataset_archived`, `blueprint_version_mismatch`, `run_already_finished` are all
that shape. A retry is not.

**It also dissolves a residue I had flagged**, which is the strongest signal it was right. The
identical-repeat detection read a pre-write snapshot, so a concurrent *equal* write reported no
warning where the sequential case did — an inconsistency I documented at `_already_recorded` and
listed as a concern. With no warning to report there is nothing to be inconsistent about, and the
snapshot read is gone with it. A simpler answer that removes a caveat rather than adding one is
usually the right answer.

**`run_already_finished` on a write was the same mistake in miniature**, and it is worth stating
separately because reusing an existing code felt like the frugal choice. The code means "a finished
run **served** you a fixture anyway" — that is what makes it worth saying under R-54(c), whose whole
subject is the read path continuing to serve. A write is not serving, so attaching it there put a
second meaning under one name. `_lifecycle`'s docstring now says it is called by `fetch_step` and by
nothing else.

**What did not change**, and it is the half that matters: a **differing** re-write is still
`ok: false` with `AP-007`, still writes nothing, and the pointer and context are unchanged. Verified
after the revert by planting the differing branch back to a silent success — seven tests fail,
including the wire contract test and the read-only walk. Neither half of R-65 can regress into the
other unnoticed.

Net vocabulary across M8: shipped three, ended with **one** (`run_already_finished`, read path).

## [M8, fix round 2] `AP-007` carries the step key and not the documents, and that is now said at the code
Ruled by the owner at fix round 1 rather than left to taste, so `_step_conflict` and
`_finish_conflict` now carry the reasoning in their own docstrings instead of only in the report.
Two reasons: an `actual` and an `outcome` are **arbitrary agent output with no size bound**, so
putting one inside an error context is a real hazard — no other finding in this product carries a
document — and `run_get` is the right place to look for it. What a caller needs from the finding is
that a value is there, which key it is under, and that it is not theirs.

## [M8, fix round 2] Retraction: the two `DECISIONS.md` references to M11 (ruling R-68)
**Ruling R-68 descopes M11, the TypeScript client, by owner decision. Phase 1 ends at M10.** This
file is append-only, so the two entries that reference it are left standing and retracted here:

- **`[M0] client/python/, client/typescript/, web/, Dockerfile, docker-compose.yml and alembic.ini
  not created at M0`** says "`client/python` and `client/typescript` are M8 and M11's deliverables
  respectively". The M8 half is now built. **The M11 half is retracted**: `client/typescript/` is
  not a deliverable of any milestone and will not be created. The *entry's own reasoning is
  untouched* and was correct — a directory is created by the milestone that owns it, and this one
  turned out to be owned by nobody, which is the argument working rather than failing.
- **`[M8] The client is packaged separately, and depends on nothing of the service's`** says "(M11
  is the TypeScript one)" as an aside in its argument for a separate distribution. **The aside is
  retracted; the argument is not.** It stands on the other two grounds it gave — a client speaks MCP
  to a *server*, which may be another process or another machine, and a path dependency would make
  "separately installable" false and be invisible in this repository. A second language was the
  vivid case, not the load-bearing one.

`.gitignore`'s `client/typescript/dist/` line is deliberately left in place: it ignores a directory
that will now never exist, which costs nothing and instructs nobody. `README.md`'s three references
were corrected in `d822a89`, and `docs/prd.md`'s three by the owner.

## Questions for the owner — M8 fix round 2
None. Fix round 1's three questions are all answered: R-65 amended (question 1),
`run_already_finished` confined to the read path (question 2), and `AP-007`'s context omission
ratified and now documented at the code (question 3).

# M9 — the web app

## [M9] Finding F-16 ruled: the browser reaches the tools **same-origin**, and the service is not changed
**The transport question, measured rather than reasoned about.** F-16 was deferred to M9 as "the web
app's transport to the service" and the pre-flight scan's suspicion was right: **a browser cannot
drive `mcp` 2.2.0's streamable-HTTP transport cross-origin.** Three measurements against the running
service, all reproducible with `curl`:

| Probe | Result |
|---|---|
| `OPTIONS /mcp` with `Origin` and `Access-Control-Request-*` | **405 Method Not Allowed**, `allow: GET, POST, DELETE` |
| `access-control-*` headers on any `/mcp` response | **zero**, for any `Origin` |
| `access-control-expose-headers` for `mcp-session-id` | absent |

Each of the three is on its own fatal. A POST carrying `content-type: application/json` plus the
`mcp-session-id` header is not a CORS-simple request, so the preflight is mandatory and it 405s.
Even a simple request's response could not be read without `access-control-allow-origin`. And even
with both, JavaScript could not read `mcp-session-id` off the `initialize` response — the header
every subsequent request has to echo. The cause is in the SDK: `CORSMiddleware` is wired only into
its OAuth routes (`mcp/server/auth/routes.py`), `streamable_http_app()` takes no CORS parameter, and
`run_streamable_http_async` builds the app and runs uvicorn with no hook to wrap it.

**Chosen: serve the app same-origin with the service.** Vite's dev server and its preview server
proxy `/mcp` to `AGENTPROPS_SERVICE_URL` (default `http://127.0.0.1:8000`); a deployment serves the
built assets behind the same origin as the service. This *removes* CORS from the picture rather than
working around it, and — the part that decided it — **it needs no change to the service at all.**
`serve_http` still calls `run_streamable_http_async`, which is M4's deliverable untouched.

The browser speaks **MCP JSON-RPC to `/mcp` and nothing else**. There is no REST API beside the tool
surface, which is ruling R-17's requirement and what makes clause 5 checkable.

**Alternative rejected: an opt-in `--allow-origin` on the service.** It is about twenty-five lines —
build the app with `streamable_http_app()`, wrap it in `CORSMiddleware` with explicit origins and
`expose_headers=["mcp-session-id"]`, run uvicorn directly — and it was rejected on two grounds.
It is not the *minimum* shape, and phase 1 has **no auth**: a permissive CORS on a service exposing
`blueprint_upsert` and `dataset_import` would let any page a developer happens to visit drive their
authoring store. Same-origin has no such hazard because it grants no origin anything.

**The named remedy, if a cross-origin deployment is ever genuinely wanted:** exactly that patch,
default-off, explicit origins, never `*`, `allow_credentials=False`, taken only when origins are
configured so the default path stays byte-identical to today's. **Do not build it now.** The trigger
is a real deployment that cannot put the assets behind the service's origin, and until then it is a
config surface with no caller. (Same shape as ruling R-39(a)'s recorded-not-built search column.)

**Cost if wrong.** If the owner wants cross-origin, it is the patch above plus a flag. Nothing in
the app changes: `callTool` posts to a relative path, so it works under any origin that proxies
`/mcp`, and `tests/unit/test_web_writes_through_tools.py` asserts that the path stays relative —
an absolute URL would mean the app could be pointed at a second service.

## [M9] The local half of validation is **shape**; the catalogue round-trips, and both run before save
The second half of F-16. Ajv — which `vanilla-jsoneditor` uses — cannot express BP-005
(reachability), BP-018 (a cycle not through a loop node), DS-008 (entity drift) or DS-010 (revision
ordering), because none of them is a property of one document's shape. **That is not a gap, it is
ruling R-04**, which put constraints in the catalogue and shape in the models on purpose, and
`schema_export.py` already said the emitted schemas "carry shape only, not policy".

So the split is:

| Half | Who | When | Rule id |
|---|---|---|---|
| Shape | Ajv 2020-12 plus `ajv-formats`, in the browser, against `schemas/*.schema.json` | every keystroke, no network | `SHAPE` |
| Policy | `blueprint_validate` / `dataset_validate` over MCP, debounced 450 ms | before save, stores nothing | the catalogue's own |

**The thing that makes clause 1 work is that the validate tools do not store** (contracts section 4).
That turns the remote half from a submit into a pre-save check, so "inline errors *before* save" is
true of the catalogue half as well as the shape half. `test_web_review_surface.py` asserts it
directly: `dataset_validate` reports DS-026 and the lineage stays at version 1.

Three consequences worth recording:

- **Shape findings carry a synthetic `SHAPE` id, not a `DS-*` one.** Minting a catalogue id for a
  condition no rule describes is what ruling R-43(a) created the `AP-*` family to avoid. A reviewer
  seeing `SHAPE` knows the document does not match the emitted schema; seeing `DS-026` knows a rule
  fired. They render in the same list, in different colours, both against RFC 6901 pointers.
- **Ajv needs the 2020-12 dialect explicitly.** The emitted schemas declare
  `$schema: draft/2020-12` and Ajv's default export is draft-07, which throws on an unknown
  meta-schema. `createAjvValidator`'s `onCreateAjv` hook returns our own `Ajv2020`, configured in
  one place (`src/lib/validation.ts::newAjv`) — so the editor's inline squiggles and the finding
  list below it come from the same configuration and cannot disagree.
- **`ajv-formats` is added deliberately.** Without it Ajv logs "unknown format uuid ignored" and
  validates *less shape than the schema describes*. `format: uuid` and `format: date-time` are in
  the emitted schemas because M1's models have those types; honouring them is honouring R-04's
  division, not extending it.

**Save is disabled while findings stand, and that is a courtesy rather than a safety mechanism.**
The service is what refuses a broken write, at write time, through the full catalogue (ground rule
7). A disabled button only declines to waste the reviewer's round trip. When a rejection does come
back it renders in the same inline list, so a reviewer never has to learn a second place to look.

**Alternative rejected: local-only validation.** Faster and wrong. It would show a perfect document
that the store then refuses, which is the worst possible order to learn things in.

## [M9] The dataset edit saves through `dataset_import`, because it is the only copy-on-write path
Ruling R-17 says the web app writes "producing a new dataset version by **copy-on-write**". The tool
surface has no `dataset_upsert`. Its dataset writes are `dataset_submit` (from a skeleton),
`dataset_import` (from a bundle), `dataset_expand`, and the two archive flags — so the choice is
narrower than it looks, and only one of them is an *edit*:

- **`dataset_submit` mints a new lineage.** A skeleton produces a new `dataset_id`, so submitting an
  edited document creates a *different dataset*, not a new version of this one. That is not
  copy-on-write; it is the twenty-first dataset PRD 5.7 complains about.
- **`dataset_import` is copy-on-write.** `put_dataset` allocates `max(version) + 1` for the lineage
  keyed on the document's own `id` (`storage/sql.py`), so a bundle carrying an edited dataset that
  keeps its id becomes version *n+1* and every earlier version stays readable by
  `dataset_get(id, version)`. Verified end to end in `test_web_review_surface.py`: version 2 written,
  version 1 unchanged, and `dataset_find` still showing **one** row for the lineage (ruling R-38).

It also re-runs the **whole** `DS-*` catalogue against the receiving store and writes nothing unless
all of it passes, which is exactly the write-time strictness ground rule 7 asks for on an edit.

**`blueprints: []` in the bundle is deliberate.** Import validates against a resolver that answers
from the receiving store *plus the bundle*, so a dataset whose blueprint is already published here
satisfies DS-001 without the bundle re-carrying it — and re-sending an immutable published version
for no reason is worse, not more thorough.

**Blueprint saves use `blueprint_upsert` with `publish: false`, always.** A published version is
immutable (BP-016), so an editor that published on every save would make the *second* save an error.
Publishing is a deliberate act on a finished blueprint, not what pressing save in a JSON editor
should mean.

**Alternative rejected: asking for a `dataset_upsert` tool.** It would be the ergonomic answer and
it is a new tool on a locked surface for a milestone that has a working path. Recorded as a question
for the owner instead.

## [M9] Clause 5 is guarded by "one way out", not by an allowlist of tool names
`tests/unit/test_web_writes_through_tools.py`. The brief asked for clause 5 to be mechanical and
named `test_runtime_is_read_only.py` and `test_bounded_integers.py` as the patterns — both of which
**enumerate from a real surface** rather than from a literal list.

Three derivations, no lists:

1. **Which tools exist** — `mcp.list_tools()`, the running registry, as R-50's integer guard reads it.
2. **Which of them write** — an AST walk from each `@mcp.tool()` function in `server/` through
   `service/`, asking whether it reaches a method whose name starts with a mutating verb **on the
   `Store` Protocol**. So a `dataset_upsert` added later is classified as a write the day it lands.
3. **Which the web app names** — `callTool("<name>")` call sites, plus any registered tool name
   appearing as a string literal in `web/src`.

**The load-bearing assertion is not the tool-name allowlist.** "No mutation outside the tool surface"
is not "only these tool names are called": a `fetch('/api/datasets', {method:'PUT'})` names no tool
at all and would sail past an allowlist. So the guard's real claim is narrower and stronger —
**the only way out of the app is one function.** No network primitive (`fetch(`,
`XMLHttpRequest`, `WebSocket(`, `EventSource(`, `sendBeacon(`, `axios`, `<form`) appears anywhere in
`web/src` outside `src/mcp/transport.ts`; `callTool` is called from one module; every argument to it
is a **literal** (a computed name would make the scan a lower bound rather than an enumeration); and
the transport posts to `MCP_PATH` and to no absolute URL. The tool-name checks then constrain what
goes *through* the one door.

**A comment stripper was necessary, and it is a decision rather than a detail.** The broad tool-name
scan initially failed on `web/src/mcp/tools.ts` — whose module comment names `dataset_submit`,
`dataset_expand`, `dataset_archive` and `dataset_restore` in order to say why each is *not* the tool
an edit uses. That explanation is the most valuable text in the file, and a guard that forced its
deletion would push the reasoning out of the code to protect a regex. So the scan blanks comment
bodies first, preserving string literals (a regex stripping `//` to end of line eats the rest of any
line containing an `https://` URL, so it is a character pass). The stripper has its own three tests,
and one asserts the concrete case against the real file: those four names present in the raw source,
absent after stripping.

**Every exclusion is guarded.** Test files are excluded — they stub `fetch` deliberately, which is
how `DatasetList.test.tsx` counts requests, and they name a deliberately unregistered tool to
exercise the app's error path. `test_the_app_and_test_partition_is_real` asserts both halves are
non-empty and disjoint and that together they are the whole tree, so the exclusion cannot quietly
become "scan nothing".

**Shown failing, against the real tree.** Two mutations planted in `web/src`: a
`fetch('/api/datasets/…', {method: 'PUT'})` in `DatasetDetail.tsx`, and a
`callTool("dataset_archive", …)` in `tools.ts`. Both were reported, by name, with the file named —
transcript in the M9 report. The synthetic falsifiers in the file itself
(`test_the_network_scan_rejects_a_planted_call` and its siblings) keep that property under CI, and
one `parametrize` asserts that **every** entry in the primitive list actually trips the scan, because
a typo in that list would silently stop guarding one of them.

## [M9] The graph layout is a forty-line BFS, not a third graph library
React Flow needs an `{x, y}` per node and computes none. The usual answers are `dagre` and `elkjs`
and **neither is in the locked stack**, so `src/graph/topology.ts` is a layered layout instead:
breadth-first depth from `entry_node` for the row, the blueprint's own `nodes` order for the column.

BFS is the right choice specifically **because the golden blueprint is cyclic.** It reaches
`check_docs` at depth 2 by the forward path and never re-deepens it when the loop arrives from
`recheck_store`, so `recheck_store` to `check_docs` draws as a back edge instead of stretching the
diagram down the page. The edge is marked `isLoopBack`, drawn in a different colour and animated, so
"renders the loop edge" means the reviewer can see it *is* a loop.

The layout is a **total function of the document** — same blueprint, same drawing, asserted — which
is the presentation-layer echo of rulings R-35 and R-58 making every service ordering total and
collation-stable. A layout depending on `Set` iteration order or `Object.keys` would undo that at
the last step.

**A node unreachable from `entry_node` is drawn, in a final row, marked.** It is a BP-005 error and
the blueprint will be rejected — but the editor shows invalid documents on purpose, and a view that
silently hid the node whose absence *is* the error would be the worst possible answer.

**Alternative rejected: `dagre`.** Prettier for a wide graph, and a dependency outside the locked
stack for one screen. If a blueprint arrives whose graph this layout renders unreadably, that is the
trigger to revisit — not before.

## [M9] The list gets its whole review surface in **one** fetch, and that is a property of `DatasetSummary`
Clause 3 — "the list shows enough to judge relevance without a detail fetch" — needed no new shape,
because contracts 2.2.1 already put title, intent, the complete label set, the author and a
narrative excerpt on every `dataset_find` row, and said why: "it exists so a reviewer can judge a
dataset without fetching it". Ruling R-38 made it one row per lineage at its latest version. So the
list calls `dataset_find` once and `label_vocabulary` once — the latter per *blueprint* and bounded
by the blueprint (R-42(c)), not per row — and nothing else.

**The intent is rendered in full, deliberately un-truncated.** `narrative_excerpt` is capped at 200
characters by the contract; `intent` is not, and must not acquire a cap. PRD 5.7 makes it the field a
reviewer reads to decide relevance, and Priya's is 306 characters — so a list that clipped both
"for consistency" would look tidy and destroy the thing the screen is for.

**The filters do no client-side work.** Each maps to one `dataset_find` parameter and the app renders
what comes back. Ruling R-36 pinned `q` as a case-folded substring match on every backend and R-39(a)
moved the fold into Python so the three agree; a browser that re-filtered the returned rows would be
answering a different question from the service, and a reviewer would have no way to tell which
answer they were looking at.

**The label filter shows per-value counts** from `label_vocabulary`, so a reviewer sees that
`edge_case=timezone-boundary` has no datasets *before* selecting it. That is PRD 5.7's
"twenty-first dataset" problem caught one screen earlier.

## [M9] What the tests actually assert about the two design clauses, and what they cannot
Recorded because the brief asked for it plainly, and because "something can pass while proving
nothing" is this build's most-repeated lesson.

**Clause 3, "enough to judge relevance", is a design claim and no test reaches it.** What the pair of
tests assert is the two facts underneath it: every field the brief names is present on the
`dataset_find` row at full length (`test_web_review_surface.py`), and rendering the list makes
exactly **one** dataset call with zero `dataset_get`s — asserted again at twenty rows, so two rows
and one call cannot be a coincidence of a small list (`DatasetList.test.tsx`). If the brief's premise
is wrong and a reviewer needs a fifth field, both tests still pass. That is the limit.

**Clause 4's "full topology including the loop edge" is asserted as an edge *set*, not a snapshot.**
A rendered snapshot, or a count of nine, would also pass with one edge missing and another
duplicated. Comparing the produced set with the document's set cannot: drop
`recheck_store -> check_docs` and the sets differ by that member and the message names it. There is a
test that plants exactly that deletion and asserts the comparison notices. The loop edge is asserted
three ways — present in the set, flagged `isLoopBack` and the *only* edge so flagged, and all three
edges of the cycle drawn.

**The rendered graph is asserted separately and weakly, on purpose.** jsdom reports every element as
0 by 0, so React Flow paints no edge geometry; `BlueprintGraph.test.tsx` asserts only that the nine
nodes mount and that no node carries React Flow's `draggable`/`selectable` classes. Asserting on
edge SVG under jsdom would either fail for a layout reason or pass against a stub, and neither says
anything about topology — which is why that assertion lives in the pure test, where there is nothing
to measure.

**Clause 1's editor is stubbed in the component test, and the reason is not convenience.**
`vanilla-jsoneditor` mounts CodeMirror, which needs layout jsdom does not provide; driving a code
editor through synthetic keystrokes tests CodeMirror. The stub is a `textarea` calling the same
`onChange` with the same two arguments, so the component under test is real and only the text input
is fake. `JsonEditor.tsx` itself is covered by `vite build` (which fails on a wrong import or type)
and by the live-service walk in the report.

## [M9] The transport is ~150 lines of app code, and that is not a TypeScript client
Ruling R-68 descoped `client/typescript/` — a published, separately installable client package. The
web app still needs *some* way to speak to the tools, and `src/mcp/transport.ts` is it: `initialize`,
the `notifications/initialized` acknowledgement, `tools/call`, and nothing else.

Hand-rolled rather than pulling `@modelcontextprotocol/sdk`, for three reasons. It is genuinely
small, because the app receives no server-initiated requests and so needs none of the `GET` stream.
It gives clause 5's guard **one file to point at**, which an SDK's internals would not. And it
changes nothing about the transport answer either way — the SDK hits the identical CORS wall.

Two details worth recording:

- **`structuredContent` carries the envelope directly**, so nothing re-parses `content[0].text`
  unless the server omitted it. And both `text/event-stream` and `application/json` bodies are
  handled, so a service started with `json_response=True` works unchanged.
- **The session memo is on the promise, not the resolved value.** Memoising the value lets ten
  components mounting together each open a session, and each of those is server-side state. One
  decision site, one `initialize`, asserted with two concurrent first calls.

The declared handshake version is `2025-11-25` and every later request echoes **whatever the server
answered with**, not what was declared — so a server that negotiates down keeps working.

## [M9] No router, and no archive button
Two things deliberately absent.

**No router.** PRD 10.4 scopes phase 1 to "browse and edit"; the app is three screens with one
selected agent between them, and a router would be a sixth library in a locked five-library stack.
Selection lives in component state. A URL scheme is the obvious next addition and nothing here
blocks it — but a deep-linkable dataset is a feature nobody asked for, and the screens were split
into `src/screens/` for testability, which is the change that would make routing trivial.

**No `dataset_archive` / `dataset_restore`.** Both are writes M9's brief does not ask for, and the
smallest write surface that satisfies the milestone is the one worth having. Adding them would widen
`PERMITTED_WRITES` in the clause-5 guard, which is exactly the moment that decision should be
visible.

## Questions for the owner — M9
1. **Should there be a `dataset_upsert` tool?** The web app's dataset edit goes through
   `dataset_import` with a one-dataset bundle, which is correct copy-on-write and re-validates in
   full — but it is a promotion tool being used as an edit tool, and a reader of `tools.ts` needs a
   paragraph to understand why. A `dataset_upsert(dataset)` would be the honest name for what the
   app does. Not built: it is a new tool on a locked surface and the existing path works.
2. **Is a cross-origin deployment wanted?** The app is same-origin with the service by design (F-16
   above). If the assets will ever be served from a different origin, the service needs the
   default-off `--allow-origin` patch described there. Not built, because no such deployment exists
   and phase 1 has no auth.
3. **Should the editor be lazy-loaded?** The bundle is 1.58 MB raw / 482 kB gzipped, almost all of it
   CodeMirror and React Flow, both behind a click. `React.lazy` on `JsonEditorPane` and
   `BlueprintGraph` would cut first paint substantially. Not done: it is a performance change with no
   measured problem on a same-origin internal tool.
4. **Does the graph need a real layout engine?** The BFS layout is deterministic and readable for the
   nine-node worked example. A blueprint with a wide fan-out may need `dagre`, which is outside the
   locked stack.

# M9 fix round 1

## [M9, fix round 1] The envelope has three shapes, and the second one was unread (ruling R-72)
**The editor discarded every warning-severity catalogue finding and reported "clean".** The
mechanism is a seam between two rulings, and neither anticipated it:

- **R-13**: a clean-but-warned document is `ok: true` with warning-severity items in `errors`, for
  BP-019, DS-007, DS-027 and DS-032.
- **R-43(b)**: every success payload sits under one named key in `data`.

`blueprint_validate` and `dataset_validate` return an **`ErrorEnvelope`** — `{ok, errors}` with no
`data` key, on *every* outcome. So a warned-clean reply is `ok: true` *and* has no `data`, which
R-43(b)'s convention does not describe. `errorsOf` took the truthy-`ok` branch, called `payload()`,
which called `Object.keys(undefined)` and threw; `DocumentEditor`'s `.catch` swallowed it;
`findingsOf` returned `[]`; the status line said "shape and catalogue clean". **DS-027 — the rule
`DatasetDetail.tsx` cites as the mistake that screen exists to catch — was invisible in the
editor**, and the same exception fired on every clean document's debounce tick.

Measured before fixing, and the model had said so all along: `models/errors.py::ErrorEnvelope`'s
docstring names the warned-clean case explicitly. The TypeScript side did not.

**The fix, in three parts.**

1. `Envelope` is `SuccessEnvelope | FindingsEnvelope`, discriminated on `"data" in envelope` rather
   than on `ok`. `findingsIn()` reads `errors` **directly** for both the validate-clean and failure
   shapes; `payload()` now throws a *named* error for a findings envelope instead of a bare
   `TypeError`, and the message says which reader to use.
2. Only **error**-severity findings block save. Ground rule 3 says warnings never block, and the
   service will store a warned document — so an app that refused to save one would be inventing a
   gate the service does not have. `blocking()` filters by severity; the status line distinguishes
   errors, warnings, a rejection and a pending check, and a warned document can no longer read as
   clean. `describe()` is its own function because it has five outcomes and the nested ternary it
   replaced had three, one of which was the lie.
3. The `.catch` no longer *can* be silent. An unexpected envelope shape becomes an `AP-000` finding
   rather than an empty list. Emptiness on the failure path is what hid this for a milestone, and
   a boundary that reports nothing when it does not understand a reply is the shape of the bug, not
   an implementation detail of it.

Documented in `contracts.md` section 1 as R-72 requires, with both consequences a client must handle
spelled out: `ok: true` does not imply `data`, and a validate reply's warnings are in `errors`
rather than in `warnings`.

**Cost if wrong.** Reading `errors` directly is strictly more permissive than routing through
`data`; nothing that worked stops working.

## [M9, fix round 1] A harness that cannot build a reply the service sends manufactures agreement
R-72's deeper half, and the more valuable one. The bug survived because
`web/src/test/server.ts` had **one** envelope constructor — `ok(key, value)`, which can only build
success-with-`data`. So all six clause-1 tests stubbed the validators as `ok("report", {ok, errors})`:
**a wrapper no validate tool produces.** Six tests agreed with each other about a shape the service
never sends, and the bug they were written to catch lived in the reader for the shape they never
constructed.

**Three constructors now, one per documented shape**, named after `contracts.md` section 1's shapes,
plus `warns()` for a warning-severity finding — its own function rather than a `severity` argument
on `rule()`, because a default argument is exactly how the warning path went untested: every
existing case used `rule()` and therefore error severity, so nothing ever called the other branch.

`validated()` **computes** `ok` from the findings rather than taking it. Per R-13 a validate reply is
`ok: true` exactly when nothing is error-severity, so a test can no longer construct
`{ok: true, errors: [an error]}`. A harness able to build an *impossible* reply is how this hid; it
should not be able to build a *contradictory* one either.

**And the assertion R-72 asks for, from two directions**, because either alone is weak:

- `web/src/test/server.test.ts` asserts every shape has a constructor, that each produces exactly
  one shape, that no envelope matches two predicates, and that the app's three readers survive all
  three shapes.
- `tests/unit/test_web_envelope_shapes.py` calls **every tool the web app names** — both validators
  in all three outcomes each — against a real store, classifies each reply structurally, and asserts
  that exactly three shapes exist, that all three are **observed**, and that the harness declares a
  constructor for each **observed** shape.

The Python half is what makes the TypeScript list a measurement rather than a declaration: without
it, a fourth shape on the service would leave both sides agreeing about three. It is a text read of
`ENVELOPE_SHAPES` rather than a generated artefact, deliberately — a generated file is a third thing
to keep in step, and the guard's job is to notice that two independent statements have drifted.

It also carries `test_the_harness_cannot_forge_a_validate_reply_with_a_data_key`, which fails if
`ok("report", …)` reappears in the web suite. `server.test.ts` is the one exemption, and the
exemption is **proved**: that file must still construct the fiction, because it constructs it in
order to assert `findingsIn` refuses it.

**Shown failing.** Removing the `validate-clean` constructor produced three failures naming it,
including "the service emitted [3 shapes] and the harness can build [2]".

## [M9, fix round 1] A rejected save must not disable the button that clears the rejection
`blocked` was `all.length > 0`, and `all` folded in `findingsOf(saveError)`. TanStack Query clears
`mutation.error` only on the next `mutate()` or an explicit `reset()`, and neither screen called
`reset()`. **So the button that would clear the error was the button the error had disabled.**
Editing did not help; leaving the editor did not help; only a screen change or a reload recovered.

Reachable on any write-time-only rejection — which `DocumentEditor`'s own docstring explicitly
designs for — and on the `agent_id: ""` bundle the dataset screen built when no agent was selected.

**Three changes, and the third is the one that made the first safe.**

1. A rejection **renders and does not block**. Pressing save again retries, which is what clears
   `mutation.error`. A retry that reproduces the same rejection is correct behaviour; a state with no
   exit is not.
2. `onDirty` fires on the first edit after a save attempt and the screens wire it to `save.reset()`,
   so a rejection does not outlive the document it was about. Once per attempt, not per keystroke.
3. **`policyPending` now blocks.** It did not, so a reviewer could press save inside the 450 ms
   debounce window, before the catalogue half had answered — and receive a rejection the app already
   had the means to predict. Fixing (1) without this would have widened that window's consequences.

The `agent_id` for the bundle now comes from the **dataset's own `blueprint.agent_id`** rather than
from the screen's selection. `agentId ?? ""` was wrong twice: it sent an empty id when nothing was
selected, and a selection that disagreed with the dataset's blueprint would have produced a bundle
whose `agent_id` contradicted its contents.

## [M9, fix round 1] A tracked source file must be readable by the tools that review it (ruling R-73)
`web/src/lib/validation.ts` used a raw **NUL byte** as a dedup separator — chosen because no rule id
or JSON pointer can contain one, which is true and was the wrong reason. Git's binary heuristic
classified the file as binary, so the review diff read `Bin 0 -> 6841 bytes` and `git grep`, ripgrep
and GitHub's diff view all skipped it. **The module implementing clause 1's entire local half was
never visible to the review.** Not misdescribed, not under-tested — unreadable.

The separator is now `JSON.stringify([rule, pointer])`. A printable separator would have satisfied
the ruling; a JSON array is unambiguous *by construction*, and that matters because an RFC 6901
pointer **may** contain a space, so a space-joined key could collide in principle. Reasoning about
which characters are safe to join on is what produced the NUL.

**`tests/unit/test_tracked_source_is_text.py` is the guard**, and it enumerates the way this build's
other guards do — `git ls-files`, the **index** rather than the filesystem, because a tracked file is
exactly what a diff shows and walking the tree would report `node_modules` and every build artefact.

**Two checks per file, and they are not the same check.** No NUL is git's actual heuristic and so
decides whether a diff is readable; decoding as UTF-8 decides whether `ruff`, `mypy`, `tsc` and
`eslint` can open it. A file can pass either and fail the other, and a parametrised test proves it:
latin-1 text has no NUL and does not decode, UTF-16 decodes and is full of NULs. A guard with one
of the two would miss one of them.

**A third check asks git directly.** `git diff --numstat --cached` reports `-` for both counts on a
path it calls binary. `is_text()` re-implements git's heuristic, and a guard that only ever checked
its own re-implementation would pass if the re-implementation were wrong. It is compared against the
**index**, which is what caught a real intermediate state: with the fix in the working tree but not
staged, the git check failed and the byte check passed — because the committed blob was still binary,
which is precisely the thing that matters.

**`docs/` is outside R-73's scope, and `docs/spec-rulings.md` currently contains two NUL bytes** — in
the passages where R-73 quotes the offending expression. So the ruling forbidding NUL bytes is itself
one of the files git will not grep. That is recorded rather than fixed:
`test_the_docs_measurement_is_recorded_rather_than_enforced` takes the measurement without failing
over it, because the ruling set the scope and an implementer does not widen it silently. Moving
`docs/` into `GUARDED_PREFIXES` is a one-line change when the owner wants it. **Raised as question 1.**

**Shown failing.** With the NUL planted back and staged, both halves fire: the byte scan names the
file and the offset, and git independently reports it as binary.

## [M9, fix round 1] Warnings are rendered, and `ToolWarning` had the wrong field names
`Warnings` was exported and imported by nothing, and `payload()` discarded `envelope.warnings`
unconditionally — so ground rule 3's **only** channel for a policy problem was unreachable. Wired
rather than deleted, because the channel is the mechanism: no tool refuses, so anything the service
wants to say about a request it served anyway arrives there.

**And the component read a field that does not exist.** `ToolWarning` declared
`{code, message, context}`; `models/errors.py::Warning` is `{code, detail}`. Two of three field
names wrong, and nothing failed — because nothing rendered a warning. A field name no response
carries is invisible until something reads it, which is the same lesson as R-72 in miniature. The
Python guard now asserts the field set **exactly** against a real `dataset_archived` reply.

Two render sites, both reachable and both measured:

- **`dataset_get` warns `dataset_archived`** for a dataset reached by explicit id. An archived
  dataset is hidden from `dataset_find` and still readable, so a reviewer following a link needs
  telling it has been withdrawn from discovery. Rendered above the detail view.
- **The two writes** return warnings, rendered beside the saved message.

`datasetGet`, `datasetSave` and `blueprintSave` return `WithWarnings<T>` rather than the bare value.
A `Warnings warnings={[]}` placed somewhere it can never fire was written and then removed: an
unreachable renderer is what this entry is about, and adding a second one while fixing the first
would have been comic.

## [M9, fix round 1] Blueprint versions are selectable, and the count says what it counts
Two gaps rather than decisions.

**`blueprint_list` was wired at the query layer and consumed by nothing**, so only the latest
published version of the selected agent was reachable while `agent_list`'s `versions[]` was fetched
and never shown. The brief says "browse agents, blueprints and datasets", and a blueprint with three
versions of which one is visible is not being browsed. The picker reads `blueprint_list` rather than
`agent_list.versions` because it carries each version's `status` — a reviewer needs to know whether
what they are reading is a draft or an immutable published version *before* they edit it, and the
editor's hint now says which.

**The dataset footer printed a page count as a total.** `dataset_find`'s default limit is 50
(R-42(b)) and this app has no paging, so a store with sixty datasets showed "50 datasets" and no hint
of the other ten. The limit is now sent **explicitly** — so the app knows the boundary it is
displaying rather than inferring it from a server default — and the footer reads "N shown · M in this
agent", with M from `label_vocabulary`'s `dataset_count`, plus a "page limit reached" note when the
page is full. Paging itself is not built: it is a feature the brief does not ask for, and a count
that lies is a defect.

## [M9, fix round 1] A successful save clears the draft
`draft` was never cleared, so after a save the editor **displayed** the refetched document while
**validating and re-saving** the stale draft until the reviewer typed again. The two panes agreed on
screen and disagreed in memory, which is the worst arrangement: nothing looks wrong.

Cleared on the `success` transition, so `current` falls back to `loaded`.

## [M9, fix round 1] Both guards over the network boundary, not one and a decoration
The reviewer's recommendation, taken. The ESLint half restricted the **bare `fetch` global** only —
one spelling out of six. It said nothing about `window.fetch`, `globalThis.fetch`,
`new XMLHttpRequest()`, `new WebSocket()`, `new EventSource()`, `navigator.sendBeacon()` or `<form>`,
every one of which the Python scan catches.

Two guards over one property are worth having when they fail at different moments — ESLint in the
editor, the Python scan in CI and across the language boundary. Two guards where one covers a sixth
of the property is one guard and a decoration. So `no-restricted-syntax` now covers the
`new`-expression forms, the qualified-member forms, `sendBeacon`, `importScripts` and a `<form>`
JSX element, scoped to app files; verified by running ESLint against a probe containing all six and
getting eight errors.

`WEB_SUFFIXES` in the Python scan widens from `.ts`/`.tsx` to every dialect that can make a
request — `.mts`, `.cts`, `.js`, `.jsx`, `.mjs`, `.cjs`. A guard whose coverage depends on a file
extension nobody chose deliberately is a guard with a gap.

## [M9, fix round 1] The `blueprints: []` literal is guarded, because ruling R-70 rests on it
R-70 accepted `dataset_import` as the dataset-edit path on three facts, the first being that with an
empty blueprint list it **cannot publish a blueprint as a side effect of saving a dataset**. Nothing
held that. The clause-5 guard checks tool *names*; the bundle's contents are an *argument*; and the
Python review-surface test re-implements the bundle rather than deriving it from the app — so both
sides could have agreed while the app sent something else.

One literal assertion over `tools.ts`, beside the existing `"dataset_import"` check, for
`blueprints: []` and `publish: false`, each with the reason it is load-bearing recorded next to it.
Asserted against **comment-stripped** source and separately against the raw source, because
`tools.ts` discusses `blueprints: []` at length and a scan over prose would pass on the explanation
alone.

## [M9, fix round 1] The inaccurate comment in `envelope.ts`
`payload()`'s docstring claimed a key rename "shows up as a type error at the call site". It cannot:
the return is an unchecked `as T` cast. Corrected to say what is actually true — a rename is a
*runtime* failure naming the tool and the keys found, rather than a silent `undefined` three
components away — and to say why it cannot be a compile-time one: the wire is untyped and this is
the boundary.

Small, and worth an entry: a docstring that overstates a guarantee is the failure mode this build
has spent nine milestones learning to distrust, and it was in the module that unwraps every response.

## Questions for the owner — M9 fix round 1
1. **Should R-73's scope include `docs/`?** `docs/spec-rulings.md` currently holds two NUL bytes, in
   the text of R-73 itself, so the most-read file in this build is one git will not grep. The guard
   is written so extending it is moving one prefix into `GUARDED_PREFIXES`; it is not done, because
   the ruling named four directories and `docs/` is the frozen specification.
2. **Should there be a `dataset_upsert` tool?** Carried forward from M9. R-70 accepted
   `dataset_import` and named "a second consumer" as the trigger. The bundle-wrapping idiom is now
   guarded by a literal assertion, which makes the current arrangement safe rather than obvious.
3. **Should the dataset list page?** It now reports honestly that a page limit was reached, which is
   the smaller half of the fix. Real paging is an `offset` and two buttons; not built, because the
   brief does not ask for it and 50 suits the review surface R-42(b) sized it for.
4. **Is a warning severity ever meant to block a write?** Ground rule 3 says no and the editor now
   behaves that way, so a DS-027 document is savable. Worth confirming, because it means the web app
   will happily store a dataset whose intent duplicates its narrative — which PRD 5.7 calls "what a
   hurried author does". The warning is rendered prominently; nothing stops the author.

## [M9.5] Two registration modules, two composition modules, and one new package constant each
Ruling R-76's layering clause is "registration is `server/`, thin as ever. Any prompt whose text
depends on store contents composes in `service/`". Taken literally and symmetrically:
`server/prompts.py` and `server/resources.py` register; `service/prompts.py` composes text and
`service/examples.py` composes the resource bodies. Every prompt here reads the store — even
`fill-a-dataset`, which has to resolve "the latest published version" before it can name one — so
in practice nothing was left in `server/` but four `@mcp.prompt()` and five `@mcp.resource()`
one-liners.

`PROMPT_MODULES` and `RESOURCE_MODULES` join `TOOL_MODULES` in `server/__init__.py`, rather than
the new modules being added to `TOOL_MODULES`. They are not tool modules: the M4 layering guards
match on the `@mcp.tool()` decorator and `test_the_tool_module_table_is_not_empty` asserts there are
exactly four, so folding them in would have made a tool guard silently cover something that
registers nothing callable through `tools/call`.

`server/prompts.py` imports the service module as `prompt_text` rather than as `prompts`. The plain
spelling works — the local name shadows nothing — but `prompts.author_a_blueprint(...)` inside a
module *named* `agentprops.server.prompts` reads as a self-call, and one alias is cheaper than a
reader having to check.

## [M9.5] Prompt arguments are `str`, and only `agent_id` on two prompts is required
Every *tool* parameter on this surface is annotated `object` with its real type in
`json_schema_extra`, so a malformed argument comes back as an `AP-001` envelope rather than an SDK
protocol error (`server/args.py`). That does not transfer to prompts, and the reason is the protocol
rather than a preference: `GetPromptRequestParams.arguments` is typed `dict[str, str] | None`, so a
non-string is refused by the request-params model one layer above a prompt function and there is no
wrong-type case left to catch. There is also no envelope on this surface to put a finding in.

So prompt parameters are plain `str`, and a missing subject is answered with *text* —
`service/prompts.py::_no_such_blueprint` — which is the only shape `prompts/get` offers and, unlike
an error, is something a model can act on.

`version` is optional on all three prompts that take one, defaulting to the latest published
version. R-76's scope line reads "an `agent_id`, a `version`, a scenario list", which names the
arguments rather than their requiredness; making `version` mandatory would have been the only place
on this whole surface where an omitted version is not "the latest published", and the prompt
interpolates the *resolved* version into its text either way, so nothing is left ambiguous. The
alternative — required `version` — was rejected on that consistency ground.

`Field(description=...)` on each argument is load-bearing rather than decoration: it becomes
`PromptArgument.description`, which is what a caller reads in the slash-command list these prompts
exist to populate. `test_prompt_and_resource_surface.py` asserts every registered argument has one,
enumerated from the registry.

## [M9.5] A fourth prompt, `wire-an-agent`, because README step 4 carried a prompt too
R-76 names three prompts "at minimum". The brief for this milestone also says to replace the
README's step-2, step-3 **and step-4** prompt bodies with a pointer to the registered prompts — and
step 4's body is about editing the caller's own agent, which none of the three covers. Leaving that
one body in the README would have left exactly the second place to drift the milestone exists to
close, so it is registered as `wire-an-agent`.

It composes in `service/` like the others, and it earns that in one respect the README version could
not: the `run_start` selector it shows is a **real dataset's own label set** read out of the store.
The README's snippet hard-coded `{"scenario": "missing-documents"}`, which is the same defect R-76
cites one step earlier — wrong in any store that has not seeded the demo fixtures. When the store
holds no dataset the prompt says so and points at `fill-a-dataset` instead of showing a selector
that matches nothing.

The alternative was leaving step 4 as prose with no prompt. Rejected: the four-step structure is
what a caller follows, and three of four steps being a slash command and one being a paste is the
inconsistency that makes people paste all four.

## [M9.5] The resource URI scheme: `agentprops://`, three static, two templates
```text
agentprops://catalogue                            the index
agentprops://examples/blueprint                   one known-good blueprint, no id needed
agentprops://examples/dataset                     one known-good dataset, no id needed
agentprops://blueprint/{agent_id}/{version}       every published blueprint, addressably
agentprops://dataset/{agent_id}/example           one example dataset per agent
```
A custom `agentprops://` scheme rather than `file://` or an `https://` URL, because neither is
true: these are documents in a store, not files and not web pages, and `Resource.uri` is a plain
`str` in `mcp` 2.2.0 so nothing normalises the path out from under us.

The two static `examples/*` URIs are what makes R-76's "find something to imitate **without being
told an id**" literally true: they take no argument and return a whole document. The two templates
are the per-agent halves R-76 asks for by name, and `agentprops://catalogue` enumerates the concrete
URIs of both, so a caller who has listed the resources never has to be handed an id.

`agentprops://dataset/{agent_id}/example` puts the literal `example` at the **end** rather than
spelling it `agentprops://examples/dataset/{agent_id}`. The prefix form would have shared a prefix
with the static `agentprops://examples/dataset`, and the coverage guard asks "does any tested URI
match this template" — which the *static* resource's own test could then have satisfied, leaving the
template covered by nothing. A URI shape chosen so a guard cannot be fooled is worth one odd-looking
segment order.

The URI strings live in `service/examples.py`, not in `server/resources.py`, even though a URI is
protocol surface. `catalogue()` puts them *inside* a document and `service/prompts.py` names them in
prose, so `server/` owning them would mean handing them down or spelling them twice — and a prompt
naming `agentprops://examples/blueprint` while the decorator registers `agentprops://example/
blueprint` is precisely the drift R-76 exists to close. One home; the decorators import it.

`BLUEPRINT_URI_TEMPLATE` is used as both an RFC 6570 template (in the decorator) and a `str.format`
template (in `blueprint_uri`), because the two syntaxes coincide for the simple `{name}` form. That
coincidence is load-bearing — it is what stops the registered URI drifting from the built one — so
`test_a_template_and_its_builder_agree` round-trips `UriTemplate.parse(TEMPLATE).match(builder(...))`
including a value that needs percent-escaping, rather than trusting it.

## [M9.5] `resources/list` is a fixed set of three; the two ways to make it live were both worse
R-76 reads as though `resources/list` should enumerate one entry per published blueprint. On
`mcp` 2.2.0 that is not reachable through the public API, and both workarounds are worse than an
index:

- `MCPServer` serves `resources/list` from `ResourceManager._resources`, populated at *import* time.
  There is no listing callback, and `Extension.methods()` explicitly refuses to replace an
  already-registered handler ("extension methods are additive and cannot replace another handler").
  The only override left is `mcp._lowlevel_server.add_request_handler` — a private attribute, and
  ruling R-16 exists because this build already paid for one wrong assumption about this SDK's
  surface.
- `mcp.add_resource(...)` **is** public and could be called from `bind()`. It would freeze the list
  at bind time, so a blueprint published mid-session never appears; and because `binding()` nests
  and restores in tests while the resource registry does not, one test's store would leak resources
  into the next. A stale list that also cross-contaminates is not an improvement.

So the *set* of resources is a property of the server and only the *bodies* are store-derived. R-76's
intent is met in full — a caller lists, reads the catalogue, and has every id — and the cost is one
indirection, recorded in `server/resources.py` beside the code.

## [M9.5] The empty store: one fixed key set per resource, always carrying `available` and `note`
`resources/list` cannot be `[]` here, for the reason above, so the honesty has to live in the
bodies. Every example resource returns the **same keys** whether or not there is anything to show —
`{available, agent_id, version, uri, blueprint, note}` and the dataset equivalent — with the
identifying fields `null` and `note` saying why there is none and what to do next. `catalogue()`
answers `{"agents": []}` with a `next_step` that names the `author-a-blueprint` prompt.

Two alternatives were rejected. **Returning the bare document when available and a different object
when not** gives one URI two shapes, so a reader cannot tell which they got without inspecting. **A
protocol error for the missing case** is the same dishonesty as a lie with less information: the
caller learns nothing about why, and CLAUDE.md's "structured errors, never exceptions, for anything
a user could cause" points the other way.

The prompts get the same treatment from the other side: each one resolves what it is about to name
*before* naming it, so an empty store produces a prompt that says "this store holds no published
blueprint" and still carries the full shape contract, rather than an instruction to imitate an id
that does not resolve. Both ends are tested — `test_prompts_contract.py` writes every
store-dependent assertion twice, once seeded and once empty.

**These bodies are not envelopes**, and that is ruling R-43(b) rather than an oversight: there is no
`ok`, no `warnings`, no `errors` and no single named `data` key on either surface, and
`test_no_resource_body_is_an_envelope` / `test_no_prompt_message_is_an_envelope` enforce it
mechanically over the whole registered surface.

## [M9.5] Which document is "the example": two decisions, one site each, deliberately uncoupled
**The example blueprint** is the latest *published* version of the first agent in
`list_blueprints(published)` order — total since ruling R-35. "Latest published" is not a second
choice: it is exactly what `get_blueprint(agent_id, None)` already means, so this reuses the store's
resolution rather than inventing one. `examples.example_agent_id` is the only place that picks, and
both the prompts and `resources/read` read it, which `test_the_prompts_and_the_resources_agree_
about_the_example` measures across the two surfaces.

**The example dataset** is the first row of `find_datasets` — the oldest lineage, at its latest
version, archives excluded, in the total `(created_at, id)` order rulings R-38 and R-09 fix.

They are computed **independently**, and the coupling was considered and rejected. Pinning the
store-wide example dataset to the example blueprint's agent would give a caller a matching pair,
which reads better; it would also report "no example dataset" whenever *that* agent has none while
the store holds plenty for other agents. A false "unavailable" is the exact failure this milestone
is about, and a matching pair is not worth buying one. Every body names its own `agent_id`, so
nothing is ambiguous. `test_the_store_wide_example_dataset_is_not_pinned_to_the_example_blueprint`
constructs precisely that arrangement.

The per-agent template never falls back either: `agentprops://dataset/<agent>/example` for an agent
with no dataset is `available: false`, not somebody else's dataset.

## [M9.5] Published-only, and the `status` check a test had to find
A resource is "a known-good document in *this* store" (R-76), and a draft is not known-good. So
`examples.published()` filters on `STATUS_PUBLISHED` and the catalogue lists published agents only;
`agent_list` and `blueprint_list` remain the surfaces that show drafts.

The `status` check inside `example_blueprint` looked redundant and is not. `Store.get_blueprint`
documents itself as "with `version`, that exact version **whatever its status**", so
`agentprops://blueprint/<agent>/1.0.0` served a *draft* as a canonical example while the module
docstring claimed it did not. `test_a_draft_version_is_not_served_as_a_resource` caught it on its
first run — a docstring claim contradicted by the code, which is the failure mode this build keeps
finding. The test asserts **both** halves now: the resource refuses the draft and `blueprint_get`
still returns it, so the difference stays a tested decision rather than becoming an accident again.

## [M9.5] `cover-the-label-space` reports per-value gaps and present tuples, never the cross-product
"Name the combinations that currently have zero datasets" has an obvious reading — the cross-product
of every dimension — and it is not a list worth serving. The golden blueprint declares five
dimensions whose product is **432**; a realistic vocabulary is worse. A prompt enumerating that
hands a caller a task nobody can finish, which is its own kind of dishonesty, and it is not what the
measurement supports: `label_vocabulary` counts **per value**, and the PRD's exit criterion is twenty
datasets *spanning* the declared labels.

So the prompt reports two things, both **complete and bounded**: every `dimension=value` with no
dataset (bounded by the vocabulary) and every label tuple already stored with how many datasets carry
it (bounded by the dataset count). The cross-product's size is stated as a number. A threshold —
"enumerate the product when it is small" — was rejected: it is a magic number and a second exit from
one decision.

The zero-count arithmetic reuses `service/admin.py::value_counts`, promoted from `_counts` for the
purpose, so "how many datasets carry this value" has one implementation shared with
`label_vocabulary`. `test_cover_the_label_space_names_the_values_with_no_dataset` cross-checks the
prompt against that tool's own output over the same session, so the two cannot disagree about what
is empty.

## [M9.5] Two private helpers promoted to public, so a resource and a tool serve the same bytes
`service/blueprints.py::_document` and `service/datasets.py::_document` became `document`, and
`service/admin.py::_counts` became `value_counts`. A resource serving a blueprint has to produce
ruling R-08's `exclude_unset` dump, and a third copy of that one-liner is a third thing to keep in
step — the bytes a caller reads out of `blueprint_get` are the bytes BP-016 compares a re-publish
against, so "agrees today" is not good enough.

Sharing the call is not proof that the bytes match, so
`test_the_blueprint_resource_is_byte_identical_to_blueprint_get` and its dataset twin compare
`json.dumps` of both over one session.

The rename also removed a shadowing hazard it created: the local `document = read_document(...)` in
three `validate`/`upsert` functions now shadowed a module-level `document`, harmless today and a
confusing failure the first time somebody calls it there. Those locals are `resolved` now.

## [M9.5] The `BP-*` count in `author-a-blueprint` is counted, not typed
The README's step-2 text said "Nineteen `BP-*` rules". It is nineteen today. It is a claim that goes
stale the milestone a twentieth lands — the class of false statement this build has spent nine
milestones learning to distrust, and one that survived in a README precisely because nothing tested
it.

`service/prompts.py::_validate_loop` counts the `BP-` prefixed keys of `RULE_REGISTRY` at call time,
and `test_the_prompt_states_the_live_blueprint_rule_count` re-derives the number independently from
the registry and asserts the prompt states it. `service/` may import `validation/`, so this costs
nothing structurally. The README no longer states a count at all.

The same treatment for the endpoint `wire-an-agent` shows: `service/` may not import `server/`, so
`DEFAULT_HTTP_URL` is a literal there — and
`test_the_endpoint_the_wiring_prompt_shows_is_the_one_the_server_serves_on` rebuilds it from
`DEFAULT_HTTP_HOST`/`PORT`/`PATH` and compares, so the literal cannot go quietly wrong.

## [M9.5] The coverage guards enumerate the registered surface, and the AST walk is shared
`tests/unit/test_prompt_and_resource_surface.py` is M4's tool guard applied to the two new surfaces,
and the property that matters is the one R-76 names: it enumerates what the running `MCPServer`
reports and asks whether each entry appears in a `get_prompt` / `read_resource` call somewhere under
`tests/`. Nothing in the file names a prompt or a resource except the two negative controls. A fifth
prompt fails the guard the moment it is registered, and the failure message names it.

Resource **templates** are matched with the SDK's own `UriTemplate.match` rather than by string
prefix, so the guard agrees with the resource manager about which template answers a URI — the only
definition of "covered" that means anything here.

The AST walk moved to `tests/sourcescan.py` and `test_tool_surface.py` now calls it too. Two copies
of a scanner is two things to keep in step, and a scanner that has quietly stopped matching makes
every coverage assertion pass vacuously. What did **not** move is each guard's non-vacuity controls —
"the scan finds something" and "the scan does not credit a name nobody calls" — because those are
claims about a particular surface.

The controls are `infer-a-blueprint` and `agentprops://inferred-blueprint/never-registered`, both
named for `blueprint_infer`, which contracts section 4 tags *(phase 1.5)* and says is "Not in phase
1" — so unlike a deferred tool that will one day land they stay valid for the whole build. That was
a flaw `test_tool_surface.py` had to fix once already (`DECISIONS.md` `[M5]`). The first draft of
this file picked a control URI it also passed to `read_resource` in the unknown-URI test, so the
control credited itself and the guard's own guard passed while proving nothing; caught by running it.

**Both guards were shown failing, and failing for the stated reason.** A pytest plugin
(`-p planter`) registers `planted-prompt`, `agentprops://planted` and
`agentprops://planted/{agent_id}/thing` before the guard module is imported — the only window in
which a plant is visible to a module-level enumeration. Exactly three tests fail, each naming
exactly the planted entry:

```text
AssertionError: these prompts are registered but no test calls them by name: ['planted-prompt']
AssertionError: these resources are registered but no test reads them by URI: ['agentprops://planted']
AssertionError: no test reads a URI served by these templates: ['agentprops://planted/{agent_id}/thing']
3 failed, 47 passed
```

And a second plugin blunts `literals_passed_to` into "every string literal in the test tree", which
is the defect the negative controls exist to catch. Both controls fire — the new one *and* M4's, so
the shared-scanner refactor did not defang the guard it inherited:

```text
FAILED test_prompt_and_resource_surface.py::test_the_scanners_do_not_credit_a_name_nobody_calls
FAILED test_tool_surface.py::test_the_scanner_does_not_credit_a_name_nobody_calls
2 failed, 75 passed
```

## [M9.5] The three thinness guards widened to `@mcp.prompt()` and `@mcp.resource()` functions
R-76 says registration is "`server/`, thin as ever". `test_layering.py`'s three shape guards — under
twenty lines, no control flow, never raises — matched only on `@mcp.tool()`, so a prompt function
could have grown a store loop and nothing would have said so. `tool_functions` is now
`registered_functions(path, decorators)` with `REGISTRATION_DECORATORS` covering all three, the
guards run over `TOOL_MODULE_FILES + SURFACE_MODULE_FILES`, and
`test_every_surface_module_registers_at_least_one_prompt_or_resource` keeps the widening from
passing against a module that registers nothing. `tool_functions` survives as the M4 spelling so its
own non-vacuity test still measures tools specifically.

## [M9.5] No prompt or resource table in `docs/contracts.md`
The tool guard compares the registered surface against `docs/contracts.md` section 4, and the
symmetrical move would be a prompts table there with a matching drift test. Not done, and the reason
is R-76's own risk clause: a documented table is a second place to state what a prompt says, and the
prompt descriptions are already the thing a caller reads. The guards enumerate from the **registered**
surface, which is stronger than agreement between two documents; `contracts.md` is also the frozen
catalogue of record (R-25) and this milestone changes no rule, envelope or storage contract.

Recorded rather than assumed, because the asymmetry with the tool surface is deliberate and a future
reader will notice it.

## [M9.5] What moved out of the README, and what stayed
**Moved into a prompt** — deleted from `README.md`, not copied:

- step 2's whole blockquote: the node/edge/entity/label contract, the `entity:<id>` reference form,
  the validate loop, the report line. It is `author-a-blueprint` now, and the `BP-*` count it used to
  state is derived rather than typed.
- step 3's blockquote: the orientation calls, the per-scenario sequence, the narrative-versus-intent
  warning (which `dataset_skeleton`'s own section description already carried, so the prompt does not
  repeat it either), the scenario list. It is `fill-a-dataset` plus `cover-the-label-space`.
- step 4's blockquote: the seam instruction, the client call sequence, the three gotchas, grade-in-
  the-test, the env-var opt-in. It is `wire-an-agent`.

**Stayed in the README**, because none of it is instruction to a model:

- the four-step structure and every step's surrounding explanation — *why* step 2's prompt is the
  long one, why step 3's is short, why the validate loop matters, what BP-014 costs;
- step 1's `.mcp.json` and the `initialize` probe, which are shell and config;
- step 4's `pyproject.toml`, `uv sync`, the import-isolation note, and the worked-example Python
  script with its real output and the three things it demonstrates;
- the "Rough edges" list, plus one new bullet: `resources/list` is a fixed set of three and the
  per-agent URIs are templates;
- the "Could Claude do this without the prompts?" subsection, **rewritten**. Leaving it would have
  shipped a measured claim that is now false — it stated `prompts: []` as the present tense. It keeps
  its reasoning, in the past tense, and prints the surface as it is now. R-68's own correction is
  about exactly this: a stale measured claim inside a document about stale claims.

The README no longer contains a single `>` blockquote line, which is the mechanical version of "the
prompt bodies are gone".

## Questions for the owner — M9.5
1. **Should `resources/list` enumerate one entry per stored document rather than an index?**
   R-76's wording reads that way and I could not reach it through the SDK's public API — the two
   routes and why each is worse than the index are recorded above and in
   `src/agentprops/server/resources.py`. If a live per-document list matters more than staying off
   `mcp._lowlevel_server`, say so and it is a `resources/list` request handler plus a guard.
2. **Is a fourth prompt in scope?** R-76 names three "at minimum" and the milestone brief asks for
   the README's step-4 prompt body to be replaced too, which needs one. `wire-an-agent` is that
   fourth. Deleting it means step 4 goes back to being the only pasted prompt of the four.
3. **Should `docs/contracts.md` document the prompt and resource surfaces?** Not done, on the
   grounds that a table is a second place to state what a prompt already says and the guards
   enumerate from the registered surface instead. The asymmetry with the tool surface is
   deliberate; it is one table plus one drift test if you want it.
4. **Should `cover-the-label-space` name whole label tuples that are missing, not just values?**
   It names every `dimension=value` with no dataset and every tuple already present, and states the
   cross-product's size — 432 for the golden blueprint — without listing it. Naming missing tuples
   means choosing which of hundreds to name, which is a policy decision I did not want to invent.
5. **`fill-a-dataset` and `cover-the-label-space` require `agent_id` and default `version` to the
   latest published.** Consistent with every tool on the surface. Worth confirming, because R-76's
   scope line names `version` among the arguments and a reader could take that as "required".
