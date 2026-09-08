# agent-props

A blueprint-driven narrative fixture service for agentic systems.

A **blueprint** describes an agent's steps as a graph (nodes, edges, entity schemas, label
vocabulary). A **dataset** is one hand-authored, immutable, versioned world for that blueprint: a
fixture per node, a coherent entity timeline, a narrative, and an expected outcome. A running agent
calls `fetch_step` over MCP and gets its step's fixture back. Instead of mocking each tool call by
hand, you author the world once and run the agent through it.

See [`CLAUDE.md`](CLAUDE.md) for the invariants and layering rules that govern this codebase, and
[`docs/`](docs/) for the full specification: [`docs/prd.md`](docs/prd.md) (product rationale),
[`docs/build-handoff.md`](docs/build-handoff.md) (stack, repo layout, milestone plan),
[`docs/contracts.md`](docs/contracts.md) (schemas, rule catalogue, tool signatures), and
[`docs/worked-example.md`](docs/worked-example.md) (the fixtures under `tests/fixtures/`).

## Status

Phase 1 is being built one milestone at a time, M0 through M11 (see
[`docs/build-handoff.md`](docs/build-handoff.md) section 4). This repository currently has the
scaffold from M0: package layout, tooling, CI, and the golden fixtures. There is no working service
yet — `src/agentprops/` subpackages are empty modules waiting on their milestone.

## Install

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

This creates `.venv/` and installs both the service dependencies and the dev tools (`pytest`,
`ruff`, `mypy`).

## Run the checks

```bash
uv run ruff check          # lint
uv run ruff format --check # formatting
uv run mypy src            # types, strict
uv run pytest              # tests
uv run pytest -m integration --store sqlite   # integration tests, once a backend exists
```

The first four must pass before any commit. The integration run is opt-in: it needs a storage
backend, which arrives at M3.

## Repository layout

```
src/agentprops/   the service: models, validation, storage, service, server, expansion, export
tests/            unit/, integration/, and fixtures/ (blueprints, datasets, broken)
docs/             the specification (PRD, build handoff, contracts, worked example)
```

`client/python`, `client/typescript`, `web/`, and the container files arrive with the milestones
that own them (M8, M11, M9, M7 respectively) — they are not part of the M0 scaffold.
