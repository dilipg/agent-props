# Brief: connect your agent repo to agent-props

**Give this file to the coding agent working in your own repository.** Both repositories are on
one machine, so it can read this path directly rather than being sent a copy — which also means
it cannot drift from the tool surface it describes.

```text
C:/Users/Dilip/Documents/GitHub/agent-props/docs/connect-your-agent-repo.md
```

Revision 1.0 · 2026-09-10 · written against phase 1 (M0–M10 plus M9.5)

---

## What you are being asked to do

`agent-props` serves **fixtures** to a running agent over MCP. Instead of hand-mocking each tool
call, you describe your agent's steps once as a **blueprint**, author **datasets** — one coherent
world each — and then your agent calls `fetch_step` where it used to call a real tool.

Four things to do, in order. Each has a **registered MCP prompt** that carries the detail, so this
brief covers only what a prompt cannot: getting connected, installing the client, and the order.

| | Step | Prompt to use |
|---|---|---|
| 0 | Connect the MCP server | — (this brief; you need MCP before you can read a prompt) |
| 1 | Model your agent as a blueprint | `author-a-blueprint` |
| 2 | Author datasets | `fill-a-dataset`, then `cover-the-label-space` |
| 3 | Wire your agent to the fixtures | `wire-an-agent` |

**Do not ask this brief for the blueprint contract or the dataset rules.** They live in the
prompts, deliberately: a prompt sits beside the tools it drives and cannot fall out of step with
them, and a guard in the agent-props repo fails if any of that text is copied into a document.
Read the prompt.

---

## Step 0 — connect the server

One command runs it. `--directory` is what lets it execute against its own environment from your
repository's working directory:

```text
uv run --directory C:/Users/Dilip/Documents/GitHub/agent-props \
  python -m agentprops.server \
  --transport stdio \
  --store C:/Users/Dilip/Documents/GitHub/agent-props/demo.db
```

Put that in whichever MCP config your harness reads. The shape below is Claude Code's
(`.mcp.json` at your repo root); Cursor uses `.cursor/mcp.json` with the same `command`/`args`
keys, and other harnesses vary — **check your own harness's MCP documentation for the file
location, but the `command` and `args` are the same everywhere.**

```json
{
  "mcpServers": {
    "agent-props": {
      "command": "uv",
      "args": [
        "run", "--directory", "C:/Users/Dilip/Documents/GitHub/agent-props",
        "python", "-m", "agentprops.server",
        "--transport", "stdio",
        "--store", "C:/Users/Dilip/Documents/GitHub/agent-props/demo.db"
      ]
    }
  }
}
```

`--store` is a SQLite file path and it is created on first use. Point it at a file you keep; every
blueprint and dataset you author lives there. It also accepts a `postgresql://` or `mongodb://`
URL if you would rather share a store.

**Verify before trusting it.** This should answer with a JSON-RPC result naming the server's
capabilities:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  | uv run --directory C:/Users/Dilip/Documents/GitHub/agent-props \
      python -m agentprops.server --transport stdio --store demo.db | head -c 100
```

Then restart your harness so it picks up the server, and confirm you can see **27 tools, 4
prompts, 3 resources and 2 resource templates**. If the tools are there but the prompts are not,
your harness does not surface MCP prompts in its UI — that is fine, the protocol still serves
them. Call `prompts/list` and `prompts/get` directly and follow what comes back.

**Orient yourself before authoring anything.** `resources/list` gives you
`agentprops://catalogue`, which names what this store already holds, plus
`agentprops://examples/blueprint` and `agentprops://examples/dataset`. Read those two — they are
real documents from this store, not fixtures, so imitating their structure is safe. There are also
templates, `agentprops://blueprint/{agent_id}/{version}` and
`agentprops://dataset/{agent_id}/example`, for fetching a specific one.

---

## Step 1 — model your agent as a blueprint

Run the **`author-a-blueprint`** prompt. Optional argument `agent_id`: a published blueprint in
this store to imitate; it picks one for you if you omit it.

It will tell you what a blueprint must contain. Two things about *your* side of the work that the
prompt cannot know:

- **Read your agent's actual code, not its documentation.** The blueprint's value comes from
  `tool_name` matching the tool your agent really calls, verbatim. A blueprint modelled from a
  README describes an agent nobody is running.
- **The validate loop is not optional.** The prompt tells you to call `blueprint_validate` and fix
  every rule id before publishing. Do that. Nineteen `BP-*` rules will find an unreachable node, a
  cycle with no loop node, and a condition referencing a field no output schema declares — and
  **BP-014**, which is the one that makes an agent untestable at runtime. The prompt explains what
  it catches and why it matters; it is far cheaper to hear now than at the first run.

You are done when `blueprint_upsert` succeeds with `publish: true`.

---

## Step 2 — author datasets

Run **`fill-a-dataset`** once per scenario. Required argument `agent_id`; optional `version` and
`scenarios`.

The prompt is short because `dataset_skeleton` returns an `instructions` field that carries the
mechanics — fill order, how to repair a rejection, the exact shape each section takes. **Read that
field and follow it.** You supply the intent: which scenarios matter for your agent.

Then run **`cover-the-label-space`** (`agent_id`, optional `version`). This one reads the store and
tells you which label values still have no datasets, so you can aim at coverage rather than a
count. That is the measure that matters — a suite of twenty datasets clustered on one label
combination tests less than eight spread across the space.

---

## Step 3 — wire your agent to the fixtures

Two parts: install the client, then run the prompt.

### Install

The client is a separate package and depends on nothing of the service's. Add to your
`pyproject.toml`:

```toml
[project]
dependencies = ["agent-props-client"]

[tool.uv.sources]
agent-props-client = { path = "C:/Users/Dilip/Documents/GitHub/agent-props/client/python" }
```

```bash
uv sync
```

Verify the separation holds — `find_spec("agentprops")` must be `None`, because the grading
helpers are meant to work with no server and no network:

```bash
uv run python -c "
import importlib.util as u
from agentprops_client import compare, connect, new_run_id
print('helpers:', [n for n in ('exact','schema','subset','grade') if hasattr(compare,n)])
print('service package required?', u.find_spec('agentprops') is not None)"
```

```text
helpers: ['exact', 'schema', 'subset', 'grade']
service package required? False
```

Not using Python? There is no client for another language — the TypeScript one was descoped. Call
the MCP tools directly over stdio or streamable HTTP; `run_start`, `fetch_step`, `record_step` and
`run_finish` are ordinary tool calls, and the three comparison helpers are pure functions you would
reimplement in ~100 lines. The Python client is the reference for their semantics.

### Run the prompt

Start the service over HTTP so your agent's process can reach it:

```bash
uv run --directory C:/Users/Dilip/Documents/GitHub/agent-props python -m agentprops.server \
  --transport http --port 8000 --store C:/Users/Dilip/Documents/GitHub/agent-props/demo.db
```

Then run **`wire-an-agent`** (optional `agent_id`, optional `url`). It describes the seam: one
injected client, fixture mode opt-in, grading in the test rather than in the agent.

The public surface you will use, from `agentprops_client`:

| | |
|---|---|
| `connect(target, *, agent_id, run_id=None)` | context manager, sync; `connect_async` for async |
| `RunClient.run_start(selector, ...)` | pins a dataset; `selector` is `{"dataset_id": …}` or `{"labels": {…}}` |
| `.fetch_step(*, node_id=…, tool_name=…, iteration=…)` | one step's fixture |
| `.record_step(actual, *, node_id=…, …)` | what your agent produced |
| `.run_finish(outcome, *, status="finished")` | closes the run |
| `compare.grade(mode, expected, actual, *, outcome_schema=None)` | dispatches on the dataset's declared mode |
| `compare.exact` / `.schema` / `.subset` | the three modes directly |
| `new_run_id()` | the client mints run ids; do not invent one |

---

## Verify the whole thing works

Fetch a step and grade an outcome. If this runs, all four steps are done:

```python
from agentprops_client import compare, connect

with connect("http://127.0.0.1:8000/mcp", agent_id="<your agent_id>") as props:
    start = props.run_start({"labels": {"<dimension>": "<value>"}})
    print("pinned", start.pin)

    step = props.fetch_step(node_id="<your entry node>")
    print("served", step.resolved_node_id, [w.code for w in step.warnings])
```

---

## Rough edges — meet them here rather than mid-session

- **Nothing infers a blueprint from your code.** `blueprint_infer` is documented and unbuilt
  (phase 1.5). You author it; the validator is what keeps you honest.
- **Fetch before you record.** `record_step` on a step you never fetched returns `AP-004`, by
  design — you cannot report an actual for a step that was never served.
- **Warnings are typed objects.** `w.code`, not `w["code"]`.
- **Warnings never block.** A warning-severity rule stores anyway; that is deliberate, not a bug.
  Read them.
- **The envelope has three shapes**, and a client that assumes two will break on the third:
  success with `data`, failure with `errors`, and — from `blueprint_validate` / `dataset_validate`
  only — `ok: true` with `errors` holding warning-severity findings and **no `data` key**.
- **The service never grades.** `record_step` and `run_finish` store what you give them verbatim,
  checked against nothing. Comparison is yours, in the client.
- **One process per SQLite store.** Point the harness's MCP server and your agent's HTTP service at
  the same `--store` file, not two copies.
- **`expected_path` and the reconstructed path are not comparable with `exact`.** `fetch_step` is
  idempotent per step key, so a path that revisits a node records one entry where the expected path
  lists two. `run_evidence` juxtaposes both and computes nothing; compare them per-visit yourself,
  or not at all.

## Where to look when this brief is not enough

- The four prompts, for anything about blueprint or dataset content.
- `agentprops://catalogue` and the two example resources, for what this store holds.
- In the agent-props repo: `docs/contracts.md` for tool signatures and the rule catalogue,
  `docs/spec-rulings.md` for the 83 binding decisions that override the other documents, and
  `README.md` for the same four steps written for a reader of that repo.
