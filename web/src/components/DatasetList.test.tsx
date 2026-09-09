/**
 * M9's second and third acceptance clauses, against the real golden fixtures.
 *
 * **Clause 2** — "a reviewer can find the Priya dataset by searching 'repeat
 * operator' and by filtering `author=pnair`" — is an assertion about real data,
 * so the first test in this file checks the data before anything checks the UI:
 * `priya-missing-docs.json` really does carry "repeat operator" in its `intent`
 * and `pnair` as its handle, and `arun-escalated.json` really does carry
 * neither. Both halves matter. Without the second, a filter that returned
 * *everything* would pass.
 *
 * The filter itself is the service's, not the app's: the app passes `q` and
 * `author` to `dataset_find` and renders what comes back (see
 * `DatasetFilters.tsx` for why it must not re-filter locally). So the test
 * asserts the two things the app is responsible for — that the reviewer's
 * keystrokes reach the tool as `q` and `author`, and that the row that comes
 * back is rendered — and `tests/unit/test_web_review_surface.py` asserts the
 * service half against a real store.
 *
 * **Clause 3** — "the list shows enough to judge relevance without a detail
 * fetch". What this test actually asserts, stated plainly because the brief
 * asks for it:
 *
 * 1. Every row's DOM contains the title, the author handle, **every** label
 *    dimension the blueprint declares, and the intent **in full** — the exact
 *    four things PRD 5.7 says provenance exists to answer.
 * 2. Rendering the list makes **exactly one** tool call, and it is
 *    `dataset_find`. No `dataset_get` fires for any row.
 *
 * What it **cannot** assert is that those four fields are *enough* for a human
 * to judge relevance. That is a design claim about a person's judgement, and no
 * test reaches it. The closest a test gets is (1) and (2): the fields the brief
 * names are present, in full, and no row needed a second request to get them.
 * If the brief's premise is wrong — if a reviewer needs a fifth field — this
 * test will still pass. That is the limit, and it is stated rather than papered
 * over.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { DatasetsScreenForTest } from "@/test/harness";
import {
  ARUN,
  ARUN_SUMMARY,
  GOLDEN_BLUEPRINT,
  PRIYA,
  PRIYA_SUMMARY,
  VOCABULARY,
} from "@/test/fixtures";
import { installServer, ok } from "@/test/server";
import type { Recorder } from "@/test/server";
import { renderWithQuery } from "@/test/render";
import type { DatasetSummary } from "@/mcp/types";

const SEARCH = "repeat operator";
const HANDLE = "pnair";
const DIMENSIONS = Object.keys(
  (GOLDEN_BLUEPRINT as unknown as { label_schema: { dimensions: Record<string, string[]> } })
    .label_schema.dimensions,
);

let server: Recorder | undefined;

afterEach(() => {
  server?.restore();
  server = undefined;
});

/**
 * A `dataset_find` that filters exactly as the service does — case-folded
 * substring over title and intent (ruling R-36), exact match on the author
 * handle — so the harness cannot answer a question the real service would
 * answer differently.
 */
function findLike(rows: readonly DatasetSummary[]) {
  return (args: Readonly<Record<string, unknown>>) => {
    const q = typeof args["q"] === "string" ? args["q"].toLowerCase() : undefined;
    const author = typeof args["author"] === "string" ? args["author"] : undefined;
    const labels = (args["labels"] ?? {}) as Record<string, string>;
    const matched = rows.filter((row) => {
      if (q !== undefined && !`${row.title} ${row.intent}`.toLowerCase().includes(q)) return false;
      if (author !== undefined && row.author.handle !== author) return false;
      for (const [dimension, value] of Object.entries(labels)) {
        if (row.labels[dimension] !== value) return false;
      }
      return true;
    });
    return ok("datasets", matched);
  };
}

function serve(rows: readonly DatasetSummary[] = [PRIYA_SUMMARY, ARUN_SUMMARY]): Recorder {
  return installServer({
    dataset_find: findLike(rows),
    label_vocabulary: () => ok("vocabulary", VOCABULARY),
    dataset_get: (args) =>
      ok("dataset", args["dataset_id"] === PRIYA.id ? PRIYA : ARUN),
  });
}

describe("the data clause 2 asserts against", () => {
  it("Priya's intent carries 'repeat operator' and her handle is pnair", () => {
    expect(PRIYA.provenance.intent).toContain(SEARCH);
    expect(PRIYA.provenance.author.handle).toBe(HANDLE);
  });

  it("Arun's does not — so both filters discriminate rather than pass everything", () => {
    expect(`${ARUN.provenance.title} ${ARUN.provenance.intent}`).not.toContain(SEARCH);
    expect(ARUN.provenance.author.handle).not.toBe(HANDLE);
  });
});

describe("clause 2: finding the Priya dataset", () => {
  it("searching 'repeat operator' sends q and leaves only her row", async () => {
    server = serve();
    const user = userEvent.setup();
    renderWithQuery(<DatasetsScreenForTest agentId={GOLDEN_BLUEPRINT.agent_id} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(2);
    });

    await user.type(screen.getByTestId("filter-q"), SEARCH);

    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(1);
    });
    expect(screen.getByTestId("row-title")).toHaveTextContent(PRIYA.provenance.title);
    // The filter reached the tool as `q`. If the app had filtered locally, this
    // would be absent and the app would be answering a different question from
    // the service.
    const sent = server.calls.filter((call) => call.tool === "dataset_find");
    expect(sent.some((call) => call.args["q"] === SEARCH)).toBe(true);
  });

  it("filtering author=pnair sends author and leaves only her row", async () => {
    server = serve();
    const user = userEvent.setup();
    renderWithQuery(<DatasetsScreenForTest agentId={GOLDEN_BLUEPRINT.agent_id} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(2);
    });

    await user.type(screen.getByTestId("filter-author"), HANDLE);

    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(1);
    });
    expect(screen.getByTestId("row-author")).toHaveAttribute("data-handle", HANDLE);
    expect(
      server.calls.some((call) => call.tool === "dataset_find" && call.args["author"] === HANDLE),
    ).toBe(true);
  });

  it("filtering a label dimension sends labels, not a client-side filter", async () => {
    server = serve();
    const user = userEvent.setup();
    renderWithQuery(<DatasetsScreenForTest agentId={GOLDEN_BLUEPRINT.agent_id} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(2);
    });

    await user.selectOptions(screen.getByTestId("filter-label-persona"), "multi-unit-operator");

    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(1);
    });
    expect(
      server.calls.some(
        (call) =>
          call.tool === "dataset_find" &&
          (call.args["labels"] as Record<string, string> | undefined)?.["persona"] ===
            "multi-unit-operator",
      ),
    ).toBe(true);
  });
});

describe("clause 3: judging relevance without a detail fetch", () => {
  it("shows title, author handle, every declared label dimension, and the full intent", async () => {
    server = serve();
    renderWithQuery(<DatasetsScreenForTest agentId={GOLDEN_BLUEPRINT.agent_id} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(2);
    });

    const [row] = screen.getAllByTestId("dataset-row");
    if (row === undefined) throw new Error("no row rendered");
    expect(row).toHaveTextContent(PRIYA.provenance.title);
    expect(row.querySelector('[data-testid="row-author"]')).toHaveAttribute(
      "data-handle",
      PRIYA.provenance.author.handle,
    );

    // Complete labels, not partial. PRD 5.7: "every dimension the blueprint
    // declares gets a value", so every dimension must be shown.
    const shown = [...row.querySelectorAll("[data-dimension]")].map((chip) =>
      chip.getAttribute("data-dimension"),
    );
    expect(new Set(shown)).toEqual(new Set(DIMENSIONS));
    for (const dimension of DIMENSIONS) {
      expect(row.querySelector(`[data-dimension="${dimension}"]`)).toHaveAttribute(
        "data-value",
        PRIYA.labels[dimension] ?? "",
      );
    }

    // The intent, in full. A truncated intent is the failure this list exists
    // to avoid, so the assertion is the whole string rather than a prefix.
    const intent = row.querySelector('[data-testid="row-intent"]');
    expect(intent?.textContent).toBe(PRIYA.provenance.intent);
    expect((intent?.textContent ?? "").length).toBeGreaterThan(200);
  });

  it("makes exactly one dataset call for the whole list, and it is not dataset_get", async () => {
    server = serve();
    renderWithQuery(<DatasetsScreenForTest agentId={GOLDEN_BLUEPRINT.agent_id} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(2);
    });

    const finds = server.calls.filter((call) => call.tool === "dataset_find");
    const gets = server.calls.filter((call) => call.tool === "dataset_get");
    expect(finds).toHaveLength(1);
    expect(gets).toHaveLength(0);
    // The only other call is the label vocabulary, which is per-blueprint and
    // bounded (ruling R-42(c)) rather than per row.
    expect(new Set(server.toolNames())).toEqual(new Set(["dataset_find", "label_vocabulary"]));
  });

  it("still makes no per-row fetch when the list grows", async () => {
    // The vacuity check for the assertion above: two rows and one call could be
    // a coincidence of a small list. Twenty rows and one call cannot.
    const many: DatasetSummary[] = Array.from({ length: 20 }, (_, index) => ({
      ...PRIYA_SUMMARY,
      id: `3f8c1a20-0000-4000-8000-0000000${String(index).padStart(5, "0")}`,
      title: `dataset ${String(index)}`,
    }));
    server = serve(many);
    renderWithQuery(<DatasetsScreenForTest agentId={GOLDEN_BLUEPRINT.agent_id} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(20);
    });
    expect(server.calls.filter((call) => call.tool === "dataset_get")).toHaveLength(0);
    expect(server.calls.filter((call) => call.tool === "dataset_find")).toHaveLength(1);
  });

  it("fetches the full dataset only when a row is selected", async () => {
    // The other side: the detail view *does* need a fetch, and that is where it
    // happens. Without this the test above could pass on an app that never
    // fetches a dataset at all.
    server = serve();
    const user = userEvent.setup();
    renderWithQuery(<DatasetsScreenForTest agentId={GOLDEN_BLUEPRINT.agent_id} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row")).toHaveLength(2);
    });
    expect(server.calls.filter((call) => call.tool === "dataset_get")).toHaveLength(0);

    await user.click(screen.getAllByTestId("dataset-row")[0]?.querySelector("button") as Element);

    await waitFor(() => {
      expect(screen.getByTestId("dataset-detail")).toBeInTheDocument();
    });
    expect(server.calls.filter((call) => call.tool === "dataset_get")).toHaveLength(1);
  });
});
