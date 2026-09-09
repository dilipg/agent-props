/**
 * The graph view, rendered: every node in the DOM, and read-only.
 *
 * `topology.test.ts` owns the edge-set assertion, which is the one clause 4
 * turns on. This file covers what only a render can: that React Flow actually
 * mounts the nine step nodes, and that no handler capable of changing the
 * document is wired up.
 *
 * React Flow measures the container, and jsdom reports every element as 0×0, so
 * edges are not painted in this environment — which is exactly why the edge
 * assertion lives in the pure test and not here. Asserting on edge SVG paths
 * under jsdom would either fail for a layout reason or pass against a stub, and
 * neither says anything about the topology.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BlueprintGraph } from "./BlueprintGraph";
import { GOLDEN_BLUEPRINT } from "@/test/fixtures";

describe("the read-only blueprint graph", () => {
  it("mounts one node per blueprint node", async () => {
    render(<BlueprintGraph blueprint={GOLDEN_BLUEPRINT} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("graph-node")).toHaveLength(GOLDEN_BLUEPRINT.nodes.length);
    });
    const drawn = screen.getAllByTestId("graph-node").map((node) => node.getAttribute("data-node-id"));
    expect(new Set(drawn)).toEqual(new Set(GOLDEN_BLUEPRINT.nodes.map((node) => node.id)));
  });

  it("shows the loop node as a loop, with its pool and iteration bound", async () => {
    render(<BlueprintGraph blueprint={GOLDEN_BLUEPRINT} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("graph-node").length).toBeGreaterThan(0);
    });
    const loop = screen
      .getAllByTestId("graph-node")
      .find((node) => node.getAttribute("data-node-id") === "request_docs");
    expect(loop).toHaveAttribute("data-kind", "loop");
    expect(loop).toHaveTextContent("pool");
    expect(loop).toHaveTextContent("max 3");
  });

  it("marks the entry node", async () => {
    render(<BlueprintGraph blueprint={GOLDEN_BLUEPRINT} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("graph-node").length).toBeGreaterThan(0);
    });
    const entry = screen
      .getAllByTestId("graph-node")
      .find((node) => node.getAttribute("data-node-id") === GOLDEN_BLUEPRINT.entry_node);
    expect(entry).toHaveTextContent("entry");
  });

  it("renders no draggable node — read-only is structural, not a style", async () => {
    render(<BlueprintGraph blueprint={GOLDEN_BLUEPRINT} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("graph-node").length).toBeGreaterThan(0);
    });
    // React Flow adds `draggable` to a node wrapper it will let the user move.
    expect(document.querySelectorAll(".react-flow__node.draggable")).toHaveLength(0);
    expect(document.querySelectorAll(".react-flow__node.selectable")).toHaveLength(0);
  });
});
