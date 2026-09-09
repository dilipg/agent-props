# agent-props-client

The Python client for [agent-props](https://github.com/) — a blueprint-driven narrative fixture
service for agentic systems. Separately installable from the service: this package talks to a
server over MCP, and it grades runs without one.

```
pip install agent-props-client
```

## Two halves, and why they are separate

**Grading is pure.** `exact`, `schema` and `subset` are functions over two documents. They open
no socket, and importing them pulls in no network library — a test asserts that mechanically, by
importing the module in a fresh interpreter and inspecting what it loaded. The service never
grades (it stores expectations and emits evidence), so the comparison has to live somewhere a
caller with two JSON documents and no server can reach it.

```python
from agentprops_client import grade, subset

subset({"onboarding_status": "complete"}, {"onboarding_status": "complete", "notes": "…"})
# Comparison(ok=True, mode='subset', differences=())

# Or dispatch on the mode the dataset itself declares:
grade(dataset["expected"]["comparison"], dataset["expected"]["final"], run["outcome"])
```

`exact` is deep equality, `subset` is "every field in the expected document is present and equal
in the actual, extra fields ignored", and `schema` validates the actual against the blueprint's
`outcome_schema` and ignores the expected. Three details worth knowing, because they are the
places a naive implementation looks right and is wrong: `subset` recurses into nested objects but
is **positional** over arrays (same length, element by element); an expected `null` requires the
key to be **present**; and `1` equals `1.0` while `true` never equals `1`.

## Running an agent through a dataset

The run id is generated when the client is constructed, before the first call, and carried on
every call for the whole execution. That is what makes retries safe: `(run_id, step, iteration)`
is an idempotency key, so a re-`fetch_step` returns the identical fixture and advances nothing.

```python
from agentprops_client import connect

with connect("http://localhost:8000/mcp", agent_id="location-onboarding") as client:
    client.run_start({"labels": {"scenario": "missing-documents"}})

    step = client.fetch_step(node_id="receive_request")
    step.output  # what the world returns to the agent

    step = client.fetch_step(tool_name="delightree.stores.get")
    step.resolved_node_id  # 'fetch_store_profile' — resolved by position

    client.record_step({"store_id": "ST-4471"}, node_id="receive_request")
    run = client.run_finish({"onboarding_status": "complete"})
```

`connect_async` is the same surface awaited. Both accept anything `mcp.Client` accepts: a URL, a
`StdioServerParameters`, or a server object for an in-process session with no subprocess.

## What raises and what does not

A tool that answers `{"ok": false, "errors": [...]}` becomes a `ToolError` carrying every
finding — read `rule` and `pointer`, never `message`. **Warnings never raise**: a `pool_exhausted`
draw, an archived dataset, a version mismatch and a closed run all arrive as `warnings` on a
successful result, because the service never gates on the read path and neither does this client.

A **write** is different, and the difference is worth knowing before you write a retry loop.
`record_step` and `run_finish` are write-once, so a repeat splits two ways: sending the *same*
value again returns normally with `step_actual_already_recorded` or `run_already_finished` on the
result, and sending a *different* one raises `ToolError` carrying `AP-007` — nothing was written,
and the recorded value is still the first one. So a retry is always safe; only a genuine
disagreement is an error.

`client.call("dataset_submit", skeleton_id=...)` reaches any tool and returns the parsed envelope
without raising, which is how you inspect a rejection as data.
