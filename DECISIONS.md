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
