/**
 * The sequential view: the run as it happened, not as it was drawn.
 *
 * The assertion that earns this view its place beside the graph is the loop
 * one. `request_docs` runs twice in the worked example, and a graph draws that
 * node once — so a reviewer looking only at the topology sees one box for two
 * different fixtures and two different outputs. Here it appears twice, in the
 * positions it actually ran.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunSequence } from "./RunSequence";
import { GOLDEN_EVIDENCE } from "@/test/fixtures";

const NODES = GOLDEN_EVIDENCE.nodes;

describe("the run, end to end", () => {
  it("renders one entry per step, not one per node", () => {
    render(<RunSequence nodes={NODES} />);
    const rendered = screen.getAllByTestId("step-evidence");
    expect(rendered).toHaveLength(NODES.length);
    // The premise: the run really does revisit a node, or this proves nothing.
    expect(new Set(NODES.map((node) => node.node_id)).size).toBeLessThan(NODES.length);
  });

  it("shows each iteration of a loop node separately", () => {
    render(<RunSequence nodes={NODES} />);
    const revisited = NODES.filter(
      (node) => NODES.filter((other) => other.node_id === node.node_id).length > 1,
    );
    const nodeId = revisited[0]?.node_id;
    expect(nodeId).toBeDefined();

    const entries = screen
      .getAllByTestId("step-evidence")
      .filter((element) => element.getAttribute("data-node-id") === nodeId);
    expect(entries.length).toBeGreaterThan(1);
    const iterations = entries.map((element) => element.getAttribute("data-iteration"));
    expect(new Set(iterations).size).toBe(entries.length);
  });

  it("orders by seq rather than by the order the bundle happened to arrive in", () => {
    const shuffled = [...NODES].reverse();
    render(<RunSequence nodes={shuffled} />);
    const drawn = screen
      .getAllByTestId("step-evidence")
      .map((element) => element.getAttribute("data-node-id"));
    const bySeq = [...NODES].sort((a, b) => a.seq - b.seq).map((node) => node.node_id);
    expect(drawn).toEqual(bySeq);
  });

  it("explains an empty run rather than rendering a blank panel", () => {
    render(<RunSequence nodes={[]} />);
    expect(screen.getByTestId("run-sequence-empty")).toHaveTextContent("fetch_step");
  });
});
