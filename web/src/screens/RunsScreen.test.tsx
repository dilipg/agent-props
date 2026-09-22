/**
 * The runs screen: two views of one bundle, both reaching the service through
 * the tool surface.
 *
 * Mounted against the fake `/mcp` server rather than with mocked hooks, for the
 * reason `src/test/server.ts` gives: the claims worth making here are about
 * which tools get called, and a mocked `callTool` would assert against a stub
 * of the layer the claim is about. {@link Recorder.toolNames} is the evidence.
 *
 * The load-bearing assertion is the last one. Switching from sequence to graph
 * must not change what a step says, because two views that disagreed about the
 * same run would be worse than one view — and they render through different
 * components, so nothing but a test holds them together.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { installServer, ok } from "@/test/server";
import type { Recorder } from "@/test/server";
import { GOLDEN_BLUEPRINT, GOLDEN_EVIDENCE } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { RunsScreenForTest } from "@/test/harness";

const RUN = {
  id: GOLDEN_EVIDENCE.run.id,
  agent_id: GOLDEN_EVIDENCE.run.agent_id,
  status: "finished",
  dataset_id: GOLDEN_EVIDENCE.pin.dataset_id,
  dataset_ver: GOLDEN_EVIDENCE.pin.dataset_version,
  bp_version: GOLDEN_EVIDENCE.pin.blueprint_version,
  run_class: "dev",
  model: null,
  started_at: GOLDEN_EVIDENCE.run.started_at,
  finished_at: GOLDEN_EVIDENCE.run.finished_at,
  external_refs: {},
  warnings: [],
};

let server: Recorder;

beforeEach(() => {
  server = installServer({
    run_find: () => ok("runs", [RUN]),
    run_evidence: () => ok("evidence", GOLDEN_EVIDENCE),
    blueprint_get: () => ok("blueprint", GOLDEN_BLUEPRINT),
  });
});

afterEach(() => {
  server.restore();
});

type Opened = { readonly datasetId: string; readonly version: number };

async function pickTheRun(onOpenDataset?: (datasetId: string, version: number) => void): Promise<void> {
  const user = userEvent.setup();
  renderWithQuery(
    <RunsScreenForTest
      agentId={GOLDEN_EVIDENCE.run.agent_id}
      {...(onOpenDataset ? { onOpenDataset } : {})}
    />,
  );
  await waitFor(() => {
    expect(screen.getByTestId("run-row")).toBeInTheDocument();
  });
  await user.click(screen.getByTestId("run-row"));
  await waitFor(() => {
    expect(screen.getByTestId("run-header")).toBeInTheDocument();
  });
}

describe("the runs screen", () => {
  it("lists runs and opens one with a single evidence call", async () => {
    await pickTheRun();
    // Not `run_get` as well: the bundle already carries the steps, and fetching
    // both would read them twice.
    expect(server.toolNames().filter((name) => name === "run_evidence")).toHaveLength(1);
    expect(server.toolNames()).not.toContain("run_get");
  });

  it("names the pinned dataset version and the comparison mode", async () => {
    await pickTheRun();
    const header = screen.getByTestId("run-header");
    expect(header).toHaveTextContent(GOLDEN_EVIDENCE.comparison);
    expect(header).toHaveTextContent(`v${String(GOLDEN_EVIDENCE.pin.dataset_version)}`);
    expect(header).toHaveTextContent(GOLDEN_EVIDENCE.pin.blueprint_version);
  });

  it("shows the run outcome apart from any step's output", async () => {
    // `run_finish`'s outcome is a different thing from the last step's actual,
    // and a screen that showed only steps would have nowhere to put it.
    await pickTheRun();
    expect(screen.getByTestId("run-outcome")).toHaveTextContent("recorded outcome");
  });

  it("offers both views and starts on the sequence", async () => {
    await pickTheRun();
    expect(screen.getByTestId("run-view-sequence")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("run-view-graph")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("run-sequence")).toBeInTheDocument();
  });

  it("fetches the pinned blueprint version for the graph, not the latest", async () => {
    const user = userEvent.setup();
    await pickTheRun();
    await user.click(screen.getByTestId("run-view-graph"));
    await waitFor(() => {
      expect(server.toolNames()).toContain("blueprint_get");
    });
    const call = server.calls.find((entry) => entry.tool === "blueprint_get");
    expect(call?.args).toMatchObject({
      agent_id: GOLDEN_EVIDENCE.run.agent_id,
      version: GOLDEN_EVIDENCE.pin.blueprint_version,
    });
  });

  it("folds the run onto the graph and dims what it never reached", async () => {
    const user = userEvent.setup();
    await pickTheRun();
    await user.click(screen.getByTestId("run-view-graph"));
    await waitFor(() => {
      expect(screen.getAllByTestId("graph-node").length).toBeGreaterThan(0);
    });

    const visited = new Set(GOLDEN_EVIDENCE.nodes.map((node) => node.node_id));
    const drawn = screen.getAllByTestId("graph-node");
    for (const element of drawn) {
      const id = element.getAttribute("data-node-id") ?? "";
      expect(element.hasAttribute("data-not-visited")).toBe(!visited.has(id));
    }
  });

  it("marks a node the run visited more than once", async () => {
    const user = userEvent.setup();
    await pickTheRun();
    await user.click(screen.getByTestId("run-view-graph"));
    await waitFor(() => {
      expect(screen.getAllByTestId("graph-node").length).toBeGreaterThan(0);
    });

    const counts = new Map<string, number>();
    for (const node of GOLDEN_EVIDENCE.nodes) {
      counts.set(node.node_id, (counts.get(node.node_id) ?? 0) + 1);
    }
    const [repeated] = [...counts].filter(([, times]) => times > 1);
    expect(repeated).toBeDefined();

    const element = screen
      .getAllByTestId("graph-node")
      .find((candidate) => candidate.getAttribute("data-node-id") === repeated?.[0]);
    expect(element?.getAttribute("data-visits")).toBe(String(repeated?.[1]));
    expect(within(element as HTMLElement).getByTestId("graph-node-visits")).toHaveTextContent(
      `x${String(repeated?.[1])}`,
    );
  });

  it("opens a node's evidence on click, and it says the same as the sequence view", async () => {
    const user = userEvent.setup();
    await pickTheRun();

    const step = GOLDEN_EVIDENCE.nodes.find((node) => node.recorded && node.actual !== null);
    expect(step).toBeDefined();

    // What the sequence view says about this step.
    const inSequence = screen
      .getAllByTestId("step-evidence")
      .find((element) => element.getAttribute("data-node-id") === step?.node_id);
    await user.click(within(inSequence as HTMLElement).getByTestId("step-evidence-header"));
    const sequenceOutput = within(inSequence as HTMLElement).getByTestId("step-actual").textContent;

    // The same step, arrived at through the graph.
    await user.click(screen.getByTestId("run-view-graph"));
    await waitFor(() => {
      expect(screen.getAllByTestId("graph-node").length).toBeGreaterThan(0);
    });
    expect(screen.getByTestId("graph-no-node-selected")).toBeInTheDocument();

    const node = screen
      .getAllByTestId("graph-node")
      .find((candidate) => candidate.getAttribute("data-node-id") === step?.node_id);
    await user.click(node as HTMLElement);

    await waitFor(() => {
      expect(screen.getByTestId("step-evidence")).toBeInTheDocument();
    });
    expect(screen.getByTestId("step-actual").textContent).toBe(sequenceOutput);
  });

  it("hands the navigation callback the PINNED dataset version, not just the id", async () => {
    // The whole point of the link. A run reads one frozen version for its life,
    // so opening the lineage's latest would show a document this run never saw -
    // and when the dataset has never been edited the two coincide, which is why
    // nothing on screen would reveal the mistake.
    const user = userEvent.setup();
    const opened: Opened[] = [];
    await pickTheRun((datasetId, version) => opened.push({ datasetId, version }));

    await user.click(screen.getByTestId("run-dataset-link"));

    expect(opened).toEqual([
      {
        datasetId: GOLDEN_EVIDENCE.pin.dataset_id,
        version: GOLDEN_EVIDENCE.pin.dataset_version,
      },
    ]);
  });

  it("never names a run-writing tool — a reviewer reads runs, it does not make them", async () => {
    await pickTheRun();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("run-view-graph"));
    for (const name of server.toolNames()) {
      expect(["run_start", "record_step", "run_finish"]).not.toContain(name);
    }
  });
});
