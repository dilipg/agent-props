# agent-props web

Browse, review and edit `agent-props` blueprints and datasets. Every write goes through the MCP tool
surface — there is no REST API beside it (ruling R-17), and
`tests/unit/test_web_writes_through_tools.py` enforces that mechanically.

## Run it

The app is served **same-origin** with the service. Start the service first:

```sh
# from the repository root
uv run python -m agentprops.server --transport http --port 8000 --store ./agentprops.db
```

Then, in `web/`:

```sh
npm install
npm run dev          # http://localhost:5173, proxying /mcp to the service
```

`AGENTPROPS_SERVICE_URL` overrides the proxy target (default `http://127.0.0.1:8000`). The built app
is served the same way:

```sh
npm run build
npm run preview      # http://localhost:4173, same proxy
```

An empty store shows an empty app. To load the worked example:

```sh
# from the repository root, with the service stopped
uv run python - <<'PY'
import asyncio, json, sys
sys.path.insert(0, "tests")
from pathlib import Path
from agentprops.service import context_for
from toolclient import connected

read = lambda p: json.loads(Path(p).read_text())
blueprint = read("tests/fixtures/blueprints/location-onboarding-1.0.0.json")
datasets = [read("tests/fixtures/datasets/priya-missing-docs.json"),
            read("tests/fixtures/datasets/arun-escalated.json")]

async def main():
    async with connected(context_for("agentprops.db")) as client:
        await client.call_tool("blueprint_upsert", {"blueprint": blueprint, "publish": True})
        await client.call_tool("dataset_import", {"bundle": {
            "format": "agentprops.bundle", "format_version": 1,
            "agent_id": blueprint["agent_id"], "blueprints": [], "datasets": datasets}})

asyncio.run(main())
PY
```

## Why same-origin, and not CORS

A browser **cannot** drive `mcp` 2.2.0's streamable-HTTP transport cross-origin. Measured against the
running service:

| Probe | Result |
|---|---|
| `OPTIONS /mcp` with `Origin` and `Access-Control-Request-*` | `405 Method Not Allowed`, `allow: GET, POST, DELETE` |
| `access-control-*` on any `/mcp` response | none, for any `Origin` |
| `access-control-expose-headers` for `mcp-session-id` | absent |

Any one of those is fatal: a POST carrying `content-type: application/json` plus `mcp-session-id` is
not a CORS-simple request so the preflight is mandatory, the response could not be read without
`access-control-allow-origin`, and JavaScript could not read `mcp-session-id` off the `initialize`
response — the header every later request must echo. The SDK wires `CORSMiddleware` only into its
OAuth routes and `streamable_http_app()` takes no CORS parameter.

Proxying makes the whole conversation same-origin, which removes CORS rather than working around it,
and needs no change to the service. A deployment serves `dist/` behind the same origin as `/mcp`.
The full finding, and the named remedy if a cross-origin deployment is ever wanted, is in
`DECISIONS.md` under `[M9] Finding F-16 ruled`.

## Commands

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server, `/mcp` proxied |
| `npm run build` | `tsc --noEmit` then `vite build` into `dist/` |
| `npm run preview` | serve `dist/`, `/mcp` proxied |
| `npm test` | Vitest, once |
| `npm run test:watch` | Vitest, watching |
| `npm run lint` | ESLint, type-aware |
| `npm run typecheck` | `tsc --noEmit` |

## What is where

```
src/
  mcp/
    transport.ts    the ONLY module that touches the network: initialize, tools/call
    tools.ts        the ONLY module that names tools; two writes, both documented
    envelope.ts     the two envelopes from contracts section 1, and payload()
    types.ts        the payload shapes this app reads
  lib/validation.ts shape (Ajv, local) vs policy (the catalogue, remote) — ruling R-04
  graph/topology.ts blueprint -> React Flow nodes and edges. Pure, no React
  components/       the review surface, the detail view, the editor, the graph
  screens/          the two screens, split out so a test can mount one
  queries/          TanStack Query hooks over the tool surface
```

Two invariants worth knowing before you edit:

- **A network call outside `src/mcp/transport.ts` fails the build's guard**, and so does a
  `callTool` outside `src/mcp/tools.ts` or with a computed name. That is M9's fifth acceptance
  clause made mechanical; see `tests/unit/test_web_writes_through_tools.py` for what it checks and
  why the tool-name allowlist is the *weaker* half.
- **The graph is read-only.** It has no `onNodesChange`, no `onEdgesChange`, and every React Flow
  interaction flag that could produce a change is off. It visualises; it does not author.

## Validation, in two halves

| Half | Who | When | Catches |
|---|---|---|---|
| Shape | Ajv 2020-12, in the browser, against `../schemas/*.schema.json` | every keystroke | required fields, types, formats, unknown keys |
| Policy | `blueprint_validate` / `dataset_validate` over MCP | debounced, **before** save | BP-005, BP-018, DS-008, DS-010, DS-024/025/026, DS-001 — the whole catalogue |

Both render inline, against the same RFC 6901 pointers. Shape findings carry a synthetic `SHAPE`
rule id so they are visibly not catalogue findings. Neither half writes anything: the validate tools
store nothing, which is what makes "before save" true.

**Only `error`-severity findings block save.** Ground rule 3: warnings never block, and the service
will store a warned document — DS-027 (intent identical to narrative) warns, and refusing to save it
would be this app inventing a gate the service does not have. Warnings render prominently and the
status line says so.

### The three envelope shapes

The validate tools return `{ok, errors}` with **no `data` key**, on every outcome — including
`ok: true` with *warning*-severity items for a document that trips only BP-019, DS-007, DS-027 or
DS-032. That is the third shape in `docs/contracts.md` section 1 and ruling R-72 exists because this
app read only two: routing a validate reply through the `data` reader threw, the throw was swallowed,
and the editor announced "clean" over a warned document.

`src/mcp/envelope.ts` discriminates on `"data" in envelope` rather than on `ok`, and
`src/test/server.ts` has one constructor per shape. Two tests keep them honest:
`src/test/server.test.ts` asserts the harness can build all three, and
`tests/unit/test_web_envelope_shapes.py` asserts the **real service** emits exactly those three and
that the harness declares a constructor for each. A harness that cannot construct a reply the
service sends does not merely miss a bug; it manufactures agreement.
