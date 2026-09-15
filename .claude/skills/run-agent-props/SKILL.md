---
name: run-agent-props
description: Build, launch, seed and drive agent-props - the MCP fixture service and its React review app. Use when asked to run, start, serve, screenshot, or manually check the app or the web UI, or to see a change working in the real app rather than in tests.
---

# Run agent-props

Two processes, and the web app **requires** both: the MCP service on `:8000`, and the
Vite dev server on `:5173` which proxies `/mcp` to it. A browser cannot reach the
service directly — ruling R-70 measured it (`OPTIONS /mcp` → 405, no `access-control-*`
headers), so same-origin is the only shape that works.

Drive the UI with the committed driver:
**`.claude/skills/run-agent-props/driver.py`** (Playwright, reusing an installed Chrome
when it finds one). The driver exists because the app has no router, so no URL can
reach any state but the landing one - see the first gotcha.

All paths below are relative to the repo root.

## Prerequisites

`uv` and `node`/`npx`. The driver looks for an installed Chrome in the usual places per
platform, honours `AGENTPROPS_CHROME` if set, and otherwise falls back to Playwright's
own Chromium (`playwright install chromium`).

Run `npm install` in `web/` first if `web/node_modules` is missing.

## Run (agent path)

### 1. Seed a store with the worked-example agent

```bash
uv run python scripts/seed_demo.py demo.db
```

Publishes `location-onboarding@1.0.0` and imports both golden datasets. It writes
through the **app's own write path** (`blueprint_upsert` then `dataset_import`), because
that is the path clause 5 constrains — a store seeded via `Store.put_dataset` would show
data this app never writes. It deletes `demo.db` first, so re-running is safe.

Expected output:

```
published location-onboarding 1.0.0
  imported 'Multi-unit operator missing one FSSAI licence' by pnair
  imported 'First-time franchisee escalated for lapsed fire-safety compliance' by claude-code

store ready at demo.db
```

### 2. Start both processes

```bash
nohup uv run python -m agentprops.server --transport http --store demo.db \
  --host 127.0.0.1 --port 8000 > .shots/svc.log 2>&1 &
cd web && nohup npx vite --host 127.0.0.1 --port 5173 --strictPort > ../.shots/web.log 2>&1 &
```

**Both will report "completed" immediately** — `nohup` detaches them. Do not trust that;
port-check instead:

```bash
uv run --with playwright python .claude/skills/run-agent-props/driver.py up
```

```
service  :8000  UP
web      :5173  UP
```

Confirm the proxy actually reaches the service, which is the thing most likely to be
silently broken:

```bash
curl -s -X POST http://127.0.0.1:5173/mcp -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  | head -c 120
```

Answers `event: message` / `data: {"jsonrpc":"2.0","id":1,"result":{...}}`.

### 3. Drive the UI

```bash
uv run --with playwright python .claude/skills/run-agent-props/driver.py flow
```

Walks the five states a reviewer meets, screenshots each into `.shots/` (gitignored),
prints the dataset rows visible at each step, and exits non-zero on any page error:

```
[01-landing] both datasets, unfiltered
[02-search] q="repeat operator" -> expect 1 row
[03-author] author=pnair -> expect 1 row
[04-detail] narrative | intent
[05-graph] read-only node graph, incl. the loop edge
---- no page or console errors (favicon 404 ignored) ----
```

**Look at the screenshots.** `04-detail.png` should show narrative and intent in two
panes; `05-graph.png` should show 9 nodes, 9 edges and a dashed loop edge from
`recheck_store` back to `check_docs`. A blank frame means the app did not mount.

Other commands: `shot NAME` (one screenshot), `text` (rendered page text — good for
grepping without an image), `eval 'JS'` (escape hatch into the page).

### 4. Stop

```bash
uv run --with playwright python .claude/skills/run-agent-props/driver.py up || true
```

Then kill the two processes holding `:8000` and `:5173`. `demo.db` is disposable.

## Run (human path)

Same two processes, then open **http://127.0.0.1:5173** in a real browser. Useful for
judging design; useless headless.

## Test

```bash
uv run pytest -q                 # containerless; backend suites skip with a reason
cd web && npx vitest run         # the web app's own suite
```

Backend suites need containers on the **non-default** ports 27117 / 5442 (ruling R-60 —
the defaults were moved off 27017 / 5432 because a foreign server answered them three
times):

```bash
uv run pytest -m integration --store postgres
```

Skips with a reason naming the URL when the server is down. **Put the URL beside any
backend count** — a backend number without its URL is not a claim.

## Gotchas

- **The app has no router.** URL query params are ignored. Three headless screenshots
  taken with different `?q=` / `?view=` came back **byte-identical** (82343 bytes each).
  You must interact with the page; `--screenshot` alone cannot reach any state but the
  landing one. This is why the driver exists.
- **A `playwright` on PATH may be the Node CLI, not the Python package.** `uv run --with
  playwright` installs the Python one ephemerally without touching `pyproject.toml`, and
  the driver reuses an installed Chrome when it finds one so Playwright need not download
  a second browser.
- **`/tmp` is ambiguous under Git Bash on Windows.** It resolves to a different directory
  in the shell than in Python, so screenshots the driver wrote could not be found by a
  shell `ls`. The driver uses repo-relative `.shots/` for exactly this reason. Do not
  "simplify" it back to `/tmp`.
- **The favicon 404's console text never names the file.** Chrome logs
  `Failed to load resource: ... 404`, so a filter on `"favicon.ico"` matches nothing and
  every clean run looks like it has an error. The driver filters on the response URL as
  well as the generic text.
- **Service functions are synchronous and take their payload positionally.**
  `upsert(context, payload, *, publish)` — not `upsert(context, blueprint=...)` — and
  they return envelope **models** (`.ok`), not dicts (`.get("ok")`). Two seeder attempts
  died on this: first `TypeError: unexpected keyword argument 'blueprint'`, then
  `TypeError: object SuccessEnvelope can't be used in 'await' expression`.
- **Backgrounded servers report success immediately.** `nohup` detaches, so the task
  status says "completed" while the process runs fine. Port-check.
- Warnings do **not** block a save (ground rule 3), so the editor will store a dataset
  carrying a DS-027 warning. That is intended, not a bug.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `not listening: web :5173` from the driver | The Vite process died. `tail .shots/web.log`. |
| Driver screenshots exist but `ls` cannot find them | You edited `SHOTS` back to `/tmp`. See the path gotcha. |
| `ModuleNotFoundError: No module named 'playwright'` | You dropped `--with playwright` from the `uv run`. |
| Every driver run reports one console error | The favicon filter was removed; see the 404 gotcha. |
| Proxy returns 404/502 for `/mcp` | The service is down, or Vite started before it. Restart Vite. |
| Blank page, no errors | Check `web/node_modules` exists. |
| `TypeError: ... 'blueprint'` while seeding | Positional payload; see the service-signature gotcha. |
