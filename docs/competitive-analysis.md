# Competitive analysis: blueprint-driven fake data + runtime serving + run recording

Date: 2026-09-08
Scope: the sharpened concept, namely (1) a JSON blueprint declaring the data shape for every step / function call of a named agent, (2) an LLM ingestion flow that fills the blueprint with story-based data into Mongo or SQLite, (3) a runtime endpoint agents fetch from during execution, and (4) run recording of the full fixture chain plus per-step outputs, used for drift detection, model-upgrade regression and load testing.

## Summary judgement

The concept decomposes into four capabilities. Two are unclaimed. Two are crowded and well funded.

| # | Capability | Market state | Recommendation |
|---|---|---|---|
| 1 | Blueprint: per-agent, per-step declared data contract | **Unclaimed** | Build. This is the wedge. |
| 2 | LLM fills the blueprint with a coherent story | **Unclaimed as a product** | Build. Second half of the wedge. |
| 3 | Runtime serving of fixture data into a live agent | **Crowded, good OSS** | Build thin, or adapt to existing formats |
| 4 | Run recording, drift, model-version comparison | **Very crowded, funded** | Integrate, do not rebuild |

**The one defensible claim:** every incumbent in category 3 is a *record-and-replay* tool. They can only replay traffic that already happened. That means they cannot test a path that has never occurred in production, cannot test an agent before it has any traffic, and can only vary faults, never the content of the world. A blueprint-first approach inverts this: the world is declared before any traffic exists. For a new agent shipping against a new tenant with zero history, that inversion is the entire value.

---

## Category 3 incumbents: runtime fixture serving

### AIMock (CopilotKit) : the strongest direct overlap
Open source, zero dependencies, Node builtins only. Mocks the whole agentic stack from one config file: 11 LLM providers with streaming and tool calls, a local MCP server speaking full JSON-RPC 2.0, A2A agent-to-agent, vector DBs (Pinecone, Qdrant, ChromaDB), plus search, rerank and moderation. Fixtures map user messages to consistent responses for deterministic replay. Proxies real API calls, saves them as fixtures, replays in CI. Runs daily against real provider APIs to catch response-format drift within 24 hours. Chaos injection. Production adoption via AG-UI across LangGraph and AWS Bedrock.

Overlap: config-file-driven mocking, deterministic fixtures, drift detection.
Gap: fixtures are recorded or hand-written, message-keyed rather than schema-keyed. No narrative generation. No per-step data contract for an agent.

### agent-vcr
MIT. `.vcr` cassettes, JSON, cross-language (Python production-ready, TypeScript source-complete). Records JSON-RPC over stdio and SSE, replays as a mock server. `diff` and `diff-batch` compare recordings, flag breaking changes and latency deltas, with `--fail-on-breaking` as a CI gate. Tags recordings by `--session-id`, `--endpoint-id`, `--agent-id` with indexing and search. Small project (7 stars, 33 commits) but the format is well designed.

Notable: the tagging model here is the closest existing thing to what is wanted, and the `.vcr` format is a plausible interop target.

### resilireplay
Apache-2.0, TypeScript, v0.1.0, very early (1 star). Deterministic seed-controlled fault injection (latency, timeouts, malformed JSON, workflow errors), replays traces with faults and scores recovery 0 to 100, compiles failed traces into executable regression tests. Audits MCP servers over stdio and HTTP with schema discovery. Local-first, no API keys or LLM judges. Reports as Terminal, JSON, HTML, JUnit, SARIF with SHA-256 artifact manifests.

### AgentCheck (arXiv 2607.11098)
MIT. Turns an MCP server into an intervention surface. Three-run model: clean run caches all tool responses, faulted run replays the cache with one response modified at an injection point, optional mitigated run re-applies the same fault after a fix. Ships 120 precomputed scenarios across 12 fault types and 5 domains. Explicitly does **not** generate synthetic step data, it perturbs captured responses.

Its core insight is worth stealing verbatim: holding the environment byte-identical is what makes a measured difference attributable to the model rather than to the world.

### Others
- **MockServer MCP tools**, **MCPJam** (protocol conformance and cross-client tool-selection metrics), **MCP Inspector** (protocol compliance, no behavioural scoring), **pytest-mcp**.
- **mcp-eval** (lastmile-ai): lightweight eval framework for MCP servers built on mcp-agent, OpenTelemetry-based assertions on tool usage and execution path, and it *generates test cases from server tools*. This is the closest existing thing to blueprint inference.
- **langchain-replay** (sixty-north): records and replays LangChain/LangGraph agent decisions while preserving real filesystem tool execution.
- **cagent session recording** (Docker): deterministic AI testing via session recording.

## Category 4 incumbents: run recording, drift, model-version comparison

This lane is the least differentiated and the most expensive to enter.

- **Braintrust**: explicitly does the model-upgrade use case. "Experiment comparison places server versions, models, or prompt configurations side by side and shows score changes for each task." Runs real agents against MCP servers with custom scorers and repeated trials.
- **Langfuse**: datasets with JSON Schema enforcement, folder organisation, metadata, and full version history (every add/update/delete/archive produces a new dataset version). Versioned dataset experiments shipped Feb 2026, experiments rebuilt as a first-class concept Apr 2026. Dataset run comparison view. Has a published cookbook for synthetic dataset generation.
- **LangSmith, Maxim, Galileo, Arize, Confident AI / DeepEval**: all serve golden datasets, regression gates and drift monitoring.

Langfuse dataset items are `{input, expected_output, metadata}` pairs. They are **not** modelled per step, and there is no per-step tool-response fixture. That is the seam. But it is a seam an incumbent can close with a schema change, so it is not by itself a moat.

## Categories 1 and 2: what nobody has

**Nothing declares the data contract of an agent step.** The nearest neighbours solve adjacent problems:

- Microsoft's [declarative agent manifest](https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/declarative-agent-manifest) and [Entra Agent ID blueprints](https://learn.microsoft.com/en-us/entra/agent-id/create-blueprint) describe agent identity, capability and permission, not the shape of the data each step consumes and produces.
- [Oracle agent-spec](https://github.com/oracle/agent-spec) describes agent structure.
- mockworld's `mock.yaml` declares a *service*, not an agent's step graph.
- mcp-eval infers test cases from a server's tool list, which is one step, not a chain.

**Nothing productises LLM-authored narrative fill.** Langfuse has a cookbook. MCP-Persona has a research pipeline (Context-Tree plus Persona-Gen). Neither is a tool a developer installs.

## Risk register

1. **The middle is contested.** If v1 tries to own runtime serving *and* trace storage *and* comparison UI, it competes with AIMock (free, OSS, adopted) on one side and Braintrust and Langfuse (funded, mature) on the other, while the actual novelty sits unbuilt.
2. **Blueprint authoring is a chore.** Nobody wants to hand-write a per-step JSON schema. Blueprint inference (from an OpenAPI spec, an MCP server's tool list, a LangGraph graph, or a single recorded trace) is not a nice-to-have, it is the adoption gate.
3. **The tau2-bench-verified lesson applies again.** Amazon had to publish a corrected fork because task definitions drifted out of alignment with database contents. If the blueprint, the generated data and the assertions are three separate artifacts that can disagree, the same bug is reproduced. The blueprint must be the single source of truth that generation and assertion both derive from.
4. **Narrative coherence is expensive to verify.** "The story hangs together" is not machine-checkable without work. Needs a validation pass (referential integrity across steps, temporal ordering, label uniqueness) or users will not trust the output.
5. **Load testing pulls the design toward a different product.** Serving thousands of concurrent varied datasets is a throughput problem, not a fixture-authoring problem, and it argues for a different storage engine than "SQLite for local dev".

## Recommended posture

Build categories 1 and 2 as the product. Ship category 3 as a thin serving layer that can also **emit** existing formats (agent-vcr `.vcr` cassettes, AIMock fixture configs) so a team already using those is a convert rather than a rip-and-replace. For category 4, emit OpenTelemetry and write run records into Langfuse or Braintrust rather than building a comparison UI.

Positioning sentence to test: *"Record-and-replay can only test what already happened. Declare the world instead, and test the agent you are about to ship."*

## Sources

- https://www.copilotkit.ai/blog/aimock-one-tool-to-mock-your-entire-ai-stack
- https://github.com/Jarvis2021/agent-vcr
- https://github.com/aliengineering-byte/resilireplay
- https://arxiv.org/html/2607.11098
- https://github.com/lastmile-ai/mcp-eval
- https://github.com/sixty-north/langchain-replay
- https://www.braintrust.dev/articles/best-mcp-testing-tools-agent-evals-2026
- https://langfuse.com/docs/evaluation/experiments/datasets
- https://langfuse.com/changelog/2026-02-11-versioned-dataset-experiments
- https://langfuse.com/changelog/2026-04-13-experiments-rebuild
- https://langfuse.com/guides/cookbook/example_synthetic_datasets
- https://github.com/langwatch/scenario
- https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/declarative-agent-manifest
- https://github.com/oracle/agent-spec
