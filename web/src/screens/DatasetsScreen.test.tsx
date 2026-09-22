/**
 * Arriving at a dataset from somewhere else in the app.
 *
 * The first test is the one the whole feature rests on. A run reads one frozen
 * dataset version for its life, so following a run's link has to open *that*
 * version. On a store where nothing has been edited, the pinned version and the
 * latest coincide — so a screen that ignored the version would look correct
 * everywhere until the first edit, then quietly show a document the run never
 * saw. Asserting the `dataset_get` argument is the only way to tell them apart
 * before that happens.
 */

import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { installServer, ok } from "@/test/server";
import type { Recorder } from "@/test/server";
import { renderWithQuery } from "@/test/render";
import { DatasetsScreenForTest } from "@/test/harness";
import { GOLDEN_BLUEPRINT, PRIYA, PRIYA_SUMMARY, VOCABULARY } from "@/test/fixtures";

const AGENT = GOLDEN_BLUEPRINT.agent_id;

let server: Recorder | undefined;

afterEach(() => {
  server?.restore();
  server = undefined;
});

/**
 * `latestVersion` reads the lineage's newest version off the list row, because
 * `dataset_find` returns one row per lineage at its latest. `serve(latest)`
 * therefore sets what the list claims, while `dataset_get` keeps returning the
 * committed v1 document — which is exactly the real shape: a dataset edited
 * twice since the run that pinned v1.
 */
function serve(latest = PRIYA.version): Recorder {
  return installServer({
    dataset_find: () => ok("datasets", [{ ...PRIYA_SUMMARY, version: latest }]),
    label_vocabulary: () => ok("vocabulary", VOCABULARY),
    dataset_get: () => ok("dataset", PRIYA),
    run_find: () => ok("runs", []),
  });
}

describe("opening a dataset that another screen asked for", () => {
  it("fetches the version it was given, not the lineage's latest", async () => {
    server = serve();
    renderWithQuery(
      <DatasetsScreenForTest agentId={AGENT} focus={{ datasetId: PRIYA.id, version: 1 }} />,
    );

    await waitFor(() => {
      expect(server?.toolNames()).toContain("dataset_get");
    });
    const call = server.calls.find((entry) => entry.tool === "dataset_get");
    expect(call?.args).toMatchObject({ dataset_id: PRIYA.id, version: 1 });
  });

  it("says which version is on screen when it is not the latest", async () => {
    // The lineage has moved on to v3; the run pinned v1. Saying so is what stops
    // a reviewer editing what they believe is current.
    server = serve(3);
    renderWithQuery(
      <DatasetsScreenForTest agentId={AGENT} focus={{ datasetId: PRIYA.id, version: 1 }} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("dataset-version-note")).toBeInTheDocument();
    });
    expect(screen.getByTestId("dataset-version-note")).toHaveTextContent("v1");
  });

  it("says nothing about versions when the pinned one IS the latest", async () => {
    // Without this, a note rendered unconditionally would pass the test above
    // while telling every reviewer their current dataset is stale.
    server = serve(PRIYA.version);
    renderWithQuery(
      <DatasetsScreenForTest agentId={AGENT} focus={{ datasetId: PRIYA.id, version: 1 }} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("dataset-runs-section")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("dataset-version-note")).not.toBeInTheDocument();
  });

  it("shows the runs made against the dataset", async () => {
    server = serve();
    renderWithQuery(
      <DatasetsScreenForTest agentId={AGENT} focus={{ datasetId: PRIYA.id, version: 1 }} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("dataset-runs-empty")).toBeInTheDocument();
    });
  });
});

describe("a dataset opened by clicking the list", () => {
  it("asks for no particular version, so the latest is served", async () => {
    server = serve();
    renderWithQuery(<DatasetsScreenForTest agentId={AGENT} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("dataset-row").length).toBeGreaterThan(0);
    });
    (screen.getAllByTestId("dataset-row")[0] as HTMLElement)
      .querySelector("button")
      ?.click();

    await waitFor(() => {
      expect(server?.toolNames()).toContain("dataset_get");
    });
    const call = server.calls.find((entry) => entry.tool === "dataset_get");
    expect(call?.args).not.toHaveProperty("version");
  });
});
