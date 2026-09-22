/**
 * The runs panel inside a dataset's detail view.
 *
 * The assertion that matters is the first: the filter is sent to `run_find` as
 * `dataset_id` rather than applied to a full list in the browser. A store with
 * fifty runs across six datasets would still *look* right filtered client-side,
 * because `run_find` defaults to fifty rows - the panel would silently show a
 * truncated set, and only on a busy store. Asserting the request is the only way
 * to tell the two apart.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DatasetRuns } from "./DatasetRuns";
import { installServer, ok } from "@/test/server";
import type { Recorder } from "@/test/server";
import { renderWithQuery } from "@/test/render";

const DATASET_ID = "3f8c1a20-0000-4000-8000-000000000001";
const AGENT = "location-onboarding";

/** Two runs against the same lineage, pinned to different versions. */
const RUNS = [
  {
    id: "aaaaaaaa-0000-4000-8000-000000000001",
    agent_id: AGENT,
    status: "finished",
    dataset_id: DATASET_ID,
    dataset_ver: 2,
    bp_version: "1.0.0",
    run_class: "dev",
    model: null,
    started_at: "2026-09-20T10:00:00Z",
    finished_at: "2026-09-20T10:00:09Z",
    external_refs: {},
    warnings: [],
  },
  {
    id: "bbbbbbbb-0000-4000-8000-000000000002",
    agent_id: AGENT,
    status: "running",
    dataset_id: DATASET_ID,
    dataset_ver: 1,
    bp_version: "1.0.0",
    run_class: "dev",
    model: null,
    started_at: "2026-09-19T09:00:00Z",
    finished_at: null,
    external_refs: {},
    warnings: [],
  },
];

let server: Recorder;

afterEach(() => {
  server.restore();
});

describe("the runs against one dataset", () => {
  beforeEach(() => {
    server = installServer({ run_find: () => ok("runs", RUNS) });
  });

  it("asks the service to filter by dataset_id rather than filtering in the browser", async () => {
    renderWithQuery(<DatasetRuns agentId={AGENT} datasetId={DATASET_ID} onOpenRun={() => {}} />);
    await waitFor(() => {
      expect(server.toolNames()).toContain("run_find");
    });
    const call = server.calls.find((entry) => entry.tool === "run_find");
    expect(call?.args).toMatchObject({ dataset_id: DATASET_ID });
  });

  it("shows which version each run pinned, since a lineage has several", async () => {
    renderWithQuery(<DatasetRuns agentId={AGENT} datasetId={DATASET_ID} onOpenRun={() => {}} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-run-row")).toHaveLength(2);
    });
    const versions = screen
      .getAllByTestId("dataset-run-row")
      .map((row) => row.getAttribute("data-dataset-version"));
    expect(versions).toEqual(["2", "1"]);
  });

  it("opens a run when its row is clicked", async () => {
    const user = userEvent.setup();
    const opened: string[] = [];
    renderWithQuery(
      <DatasetRuns
        agentId={AGENT}
        datasetId={DATASET_ID}
        onOpenRun={(runId) => opened.push(runId)}
      />,
    );
    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-run-row").length).toBeGreaterThan(0);
    });
    await user.click(screen.getAllByTestId("dataset-run-row")[0] as HTMLElement);
    expect(opened).toEqual([RUNS[0]?.id]);
  });
});

describe("a dataset nothing has run against", () => {
  beforeEach(() => {
    server = installServer({ run_find: () => ok("runs", []) });
  });

  it("says so rather than rendering an empty box", async () => {
    renderWithQuery(<DatasetRuns agentId={AGENT} datasetId={DATASET_ID} onOpenRun={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTestId("dataset-runs-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("dataset-runs-empty")).toHaveTextContent("run_start");
  });
});
