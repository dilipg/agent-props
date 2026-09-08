# Landscape research: MCP toolsets for schema + narrative seeding + label-based retrieval

Date: 2026-09-08
Question: does a live solution already exist that works as an MCP tool set to (a) create a schema, (b) seed fake data, and (c) retrieve it by label/tag, so agents can be automated against narrative-oriented data rather than random rows?

## Verdict

No. Nothing shipping today does all three. The market has split cleanly into two halves and neither half has the label layer:

- **Generators and DB seeders** get schema and referential integrity right, but the data is semantically arbitrary and there is no way to address a record by meaning after it exists.
- **World and service simulators** get narrative coherence and determinism right, but you write a mock service, not a schema, and there is no tag-addressed query surface over the seeded state.

The label/tag retrieval idea, "give me the churn-risk enterprise customer with an open P1 ticket, same one every run", exists nowhere as a first-class primitive. Today it lives only inside eval platforms (Langfuse, Braintrust, LangSmith dataset tags), and those tag *test cases*, not the world state the agent acts on.

Also worth noting: the Anthropic connector registry has no fake-data or seeding connector at all. This category is unclaimed on the first-party surface.

---

## Category 1: stateless fake-data generators over MCP

Faker wrappers. Useful for a single field or a throwaway array, useless for a storyline.

| Tool | What it is | Schema | Persist | Tags | Notes |
|---|---|---|---|---|---|
| [faker-mcp](https://github.com/funsjanssen/faker-mcp) | Faker.js exposed as MCP | no | no | no | Most-cited "fake data MCP" |
| [Real Fake Data MCP](https://real-fake-data.com/mcp/) | 327 generators, 27 EU countries + US/CA | no | no | no | `list_generators` + `generate`, seeded, valid national IDs / tax numbers / IBANs |
| [sample-data-mcp](https://glama.ai/mcp/servers/@cdelashmutt-pivotal/sample-data-mcp) | Fixed-length records from a field spec | field spec only | no | no | Single tool, 5 field types |
| [mockmcp.io](https://github.com/K-Cupples/mockmcp) | Hosted, 12 tools (users/products/orders/events/email/KB) | fixed | **explicitly none** | no | Deterministic (same input, same output), zero setup, rate limited |

Gap: no cross-record coherence. Two calls produce two unrelated worlds.

## Category 2: schema-aware DB seeders over MCP

These read a real schema and write rows whose foreign keys resolve. This is the strongest live MCP category.

| Tool | Model | Strength | Missing |
|---|---|---|---|
| [Seedfast MCP](https://seedfa.st/blog/mcp-test-data) | Commercial, credits ($0 / $16 / $69) | Reads live Postgres/Supabase/Neon schema, writes FK-resolving rows in place from a plain-language description | No labels, no scenario library, no retrieval |
| [Tonic Fabricate MCP](https://www.tonic.ai/blog/generate-synthetic-data-in-claude-cursor) | Commercial, ~$29/mo Plus | Hosted generation agent, multi-engine (Postgres, MySQL, Oracle, Databricks), thousands of rows across linked tables with business logic intact, OAuth or API key | Same |
| [Relational DB Seeder (NamanT98)](https://glama.ai/mcp/servers/NamanT98/DB-Seeder) | **Open source** | `get_database_status`, `get_schema`, `insert_graph`, `execute_query`. Topologically sorts tables, resolves `ref:users:alice_temp` style references to real PKs | Labels are payload-scoped, not persisted or queryable |

Adjacent but **not MCP** (still worth mining for design):

- [SeedBase](https://seedbase.dev/) : FK-consistent data from SQL, Django and Prisma schemas. Ships as VS Code and JetBrains extensions, not an MCP server.
- [@snaplet/seed → supabase-community/seed](https://github.com/supabase-community/seed) : TypeScript client generated from your DB structure, fully deterministic generation via Copycat, auto-creates relational entities. Snaplet went open source under Supabase; still active. Optional LLM hook for text fields. No MCP.
- [Neosync](https://github.com/nucleuscloud/neosync) : anonymization and synthetic data orchestration across environments. No MCP server.

Gap for all of Category 2: integrity without meaning, and nothing to retrieve by.

## Category 3: stateful world / service simulators over MCP

This is where narrative coherence actually exists today.

### mockworld-mcp (closest thing to prior art)

[PyPI](https://pypi.org/project/mockworld-mcp/), Apache-2.0, Python 3.11+, v0.3.1 (4 Sep 2026), classified alpha. By Swarm Proof. Billed as "a synthetic internet for agents: deterministic, LLM-free fake services."

What it has that matters:

- **Composed worlds with a shared identity namespace.** payments + crm + email run over the same 50 customers, so a charge, a CRM update and an email stay consistent. This is narrative coherence, achieved structurally rather than by prompting.
- **Seeded entropy funnel.** Clock, IDs, RNG and fault dice all draw from one seed, so `--seed 42` gives byte-identical behaviour across runs and parallel workers. The validator enforces that no entropy escapes the funnel.
- **Snapshots.** `mockworld snapshot save/load` captures dirtied world state as portable JSON.
- **Declarative definition.** `mock.yaml` plus an optional Python handler, Pydantic-validated, with `mockworld validate` checking schema and determinism.
- **A public registry.** `mockworld search` / `mockworld add`, index-as-repo, PR-based contribution, checksum verified.
- **Business-logic fault injection** (declines, insufficient funds, rate limits), not transport noise.
- **Persona swarms** producing "Agent Readiness Reports".
- Built-ins: `mock:payments` (Stripe-like), `mock:crm`, `mock:exchange`, `mock:email`, `mock:files` (S3-like), `mock:hello`.

What it does not have:

- You author a **service**, not a schema. An agent cannot declare an arbitrary domain at runtime and get a world back.
- No label/tag query layer. Registry search is over mocks, not over the entities inside a world.
- Alpha maturity, single maintainer.

### Others in this category

- [WireMock Cloud MCP](https://www.wiremock.io/mcp-server) : exposes the whole platform as tools. Stateful stubs via a key-value store, scenarios with timeouts and 5xx faults, and a `/convert-to-data-driven` skill that backs stubs with CSV or a database. API-shaped rather than data-shaped, cloud-first, no tags.
- [MockLoop MCP](https://docs.mockloop.com/api/mcp-tools/) : `generate_mock_api`, `query_mock_logs`, `discover_mock_servers`, `manage_mock_data`. Named scenarios with create / switch / list, hot-swapped without restart. But a scenario is an endpoint-to-response map, not an entity graph. No tags.
- [mcpland/mock-mcp](https://github.com/mcpland/mock-mcp) : AI-generated mocks from OpenAPI JSON Schema, batch lease model (`claim_next_batch`, `provide_batch_mock_data`). In-test generation, no stored fixtures.

## Category 4: research prior art worth stealing from

- **[MCP-Persona](https://arxiv.org/html/2606.02470v1)** (repo: wwh0411/MCP-Persona) is the academic version of exactly this idea. Their **Context-Tree** builds a hierarchical structure matching each app's domain model (User → Calendar → Event) and populates it from four sources: enumerated sets, LLM free-form, random, and sanitized authentic text. Their **Persona-Gen** samples tool-invocation chains that preserve semantic coherence, then injects context and deliberately obfuscates the instruction to mimic real user ambiguity. 173 human-verified tasks. It is a benchmark, not a reusable tool.
- **[tau2-bench](https://github.com/sierra-research/tau2-bench)** ships domain databases (airline, retail, telecom) as seeded JSON state alongside task definitions. Note the existence of **[tau2-bench-verified](https://github.com/amazon-agi/tau2-bench-verified)** by Amazon, created specifically because "task definitions, expected actions, and evaluation criteria did not properly align with the stated policies or database contents." Hand-curated narrative state drifts from the assertions written against it. That is the single most important failure mode to design out.
- **[persona-hub](https://github.com/tencent-ailab/persona-hub)** : 1B personas for scaling synthetic data creation. Personas, not relational worlds, but a useful seed vocabulary.

---

## The gap, stated precisely

Nobody offers this triad:

1. **Agent-declared schema at runtime.** Not "point me at your Postgres" (Seedfast, Fabricate) and not "write a mock.yaml" (mockworld), but a `define_schema` call an agent makes mid-run for an arbitrary domain.
2. **Narrative-coherent generation across entities.** A storyline that constrains every generated row, so the data reads like one company's month rather than 500 independent samples. mockworld gets this via a shared namespace; nobody gets it via a described scenario.
3. **Label-addressed retrieval.** A stable, queryable tag space over the seeded entities, so step 7 of an agent flow can ask for "the enterprise account with an unresolved escalation" and get the same entity it got last run. This exists nowhere.

## Design implications for the build

- **Determinism is table stakes and it is solved.** Copy mockworld's seeded entropy funnel (one seed drives clock, IDs, RNG, fault selection) and its validator concept. Copy `snapshot save/load` as portable JSON.
- **`insert_graph` + `ref:` is 80% of your tagging primitive already.** The Relational DB Seeder resolves `ref:users:alice_temp` to real PKs inside one payload. Make those labels persistent, multi-valued and queryable across runs and you have capability (3) for free.
- **Context-Tree is the right schema shape.** A domain-model hierarchy beats a flat table list for narrative generation, because the tree gives you the traversal order a story follows.
- **Labels must be the single source of truth**, read by both the seeder and the assertions. tau2-bench-verified exists because that was not true there. If a scenario says "churn-risk customer" and the seeder tags one entity that way, an assertion should never re-derive that set independently.
- **Two-phase generation.** Phase 1: LLM writes the narrative and the label plan against the schema (cheap, once, cacheable). Phase 2: deterministic expansion fills volume from a seed (no LLM, reproducible). This keeps cost and determinism sane while preserving the story.
- **Tool surface sketch:** `define_schema`, `define_scenario` (narrative + label plan), `seed` (scenario + seed + volume), `resolve_by_label` / `query_by_label`, `list_labels`, `snapshot_save` / `snapshot_load`, `reset`.

## Competitive read

The commercial money (Seedfast, Tonic Fabricate) is going into schema-aware DB seeding, which is a crowded and monetized lane. The narrative lane has exactly one alpha-stage open-source entrant (mockworld) with a different architectural bet (author services, not schemas). A schema-first, label-addressed, narrative-coherent seeder is genuinely open territory, and the strongest wedge is capability (3), because it is the one thing no competitor can bolt on without re-architecting.

## Sources

- https://github.com/funsjanssen/faker-mcp
- https://real-fake-data.com/mcp/
- https://github.com/K-Cupples/mockmcp
- https://glama.ai/mcp/servers/@cdelashmutt-pivotal/sample-data-mcp
- https://seedfa.st/blog/mcp-test-data
- https://www.tonic.ai/blog/generate-synthetic-data-in-claude-cursor
- https://glama.ai/mcp/servers/NamanT98/DB-Seeder
- https://seedbase.dev/
- https://github.com/supabase-community/seed
- https://github.com/nucleuscloud/neosync
- https://pypi.org/project/mockworld-mcp/
- https://www.wiremock.io/mcp-server
- https://docs.mockloop.com/api/mcp-tools/
- https://github.com/mcpland/mock-mcp
- https://arxiv.org/html/2606.02470v1
- https://github.com/sierra-research/tau2-bench
- https://github.com/amazon-agi/tau2-bench-verified
- https://github.com/tencent-ailab/persona-hub
