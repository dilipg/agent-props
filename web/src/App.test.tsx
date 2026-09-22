/**
 * The navigation between the two screens, which is the only part no component
 * test can reach.
 *
 * `DatasetRuns`, `DatasetsScreen` and `RunsScreen` are each tested against their
 * own callbacks — and every one of those tests would still pass if `App` never
 * wired the callbacks up. The shell is where a working feature and a dead link
 * look identical from below, so it gets its own test.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { App } from "./App";
import { installServer, ok } from "@/test/server";
import type { Recorder } from "@/test/server";
import { renderWithQuery } from "@/test/render";
import {
  GOLDEN_BLUEPRINT,
  GOLDEN_EVIDENCE,
  PRIYA,
  PRIYA_SUMMARY,
  VOCABULARY,
} from "@/test/fixtures";

const AGENT = GOLDEN_BLUEPRINT.agent_id;

const RUN = {
  id: GOLDEN_EVIDENCE.run.id,
  agent_id: AGENT,
  status: "finished",
  dataset_id: PRIYA.id,
  dataset_ver: 1,
  bp_version: GOLDEN_EVIDENCE.pin.blueprint_version,
  run_class: "dev",
  model: null,
  started_at: GOLDEN_EVIDENCE.run.started_at,
  finished_at: GOLDEN_EVIDENCE.run.finished_at,
  external_refs: {},
  warnings: [],
};

/** The evidence bundle, re-pinned at PRIYA so both directions name one dataset. */
const EVIDENCE = {
  ...GOLDEN_EVIDENCE,
  pin: { ...GOLDEN_EVIDENCE.pin, dataset_id: PRIYA.id, dataset_version: 1 },
};

let server: Recorder;

beforeEach(() => {
  server = installServer({
    store_status: () =>
      ok("status", { backend: "sqlite", healthy: true, counts: { blueprints: 1, datasets: 1, runs: 1 } }),
    agent_list: () => ok("agents", [{ agent_id: AGENT, dataset_count: 1, blueprint_versions: ["1.0.0"] }]),
    dataset_find: () => ok("datasets", [PRIYA_SUMMARY]),
    dataset_get: () => ok("dataset", PRIYA),
    label_vocabulary: () => ok("vocabulary", VOCABULARY),
    run_find: () => ok("runs", [RUN]),
    run_evidence: () => ok("evidence", EVIDENCE),
    blueprint_get: () => ok("blueprint", GOLDEN_BLUEPRINT),
  });
});

afterEach(() => {
  server.restore();
});

describe("navigating from a run to the dataset it used", () => {
  it("switches screens and asks for the pinned version", async () => {
    const user = userEvent.setup();
    renderWithQuery(<App />);

    await user.click(screen.getByTestId("screen-runs"));
    await waitFor(() => {
      expect(screen.getByTestId("run-row")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("run-row"));
    await waitFor(() => {
      expect(screen.getByTestId("run-dataset-link")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("run-dataset-link"));

    await waitFor(() => {
      expect(screen.getByTestId("dataset-runs-section")).toBeInTheDocument();
    });
    const call = server.calls.find((entry) => entry.tool === "dataset_get");
    expect(call?.args).toMatchObject({ dataset_id: PRIYA.id, version: 1 });
  });
});

describe("navigating from a dataset to a run made against it", () => {
  it("switches screens and opens that run", async () => {
    const user = userEvent.setup();
    renderWithQuery(<App />);

    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row").length).toBeGreaterThan(0);
    });
    await user.click(
      (screen.getAllByTestId("dataset-row")[0] as HTMLElement).querySelector(
        "button",
      ) as HTMLElement,
    );
    await waitFor(() => {
      expect(screen.getByTestId("dataset-run-row")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("dataset-run-row"));

    await waitFor(() => {
      expect(screen.getByTestId("run-header")).toBeInTheDocument();
    });
    const call = server.calls.find((entry) => entry.tool === "run_evidence");
    expect(call?.args).toMatchObject({ run_id: RUN.id });
  });
});
