/**
 * M9's fourth acceptance clause: the graph renders the worked example's full
 * topology **including the loop edge**.
 *
 * What this test asserts, and what it deliberately does not
 * ---------------------------------------------------------
 *
 * It asserts the **edge set**, as a set of `from->to` pairs compared for exact
 * equality against the blueprint document — not a snapshot, and not a count.
 * The reason is the failure mode the brief names: a snapshot of a rendered
 * graph, or an assertion that "9 edges were produced", would also pass with an
 * edge missing and a different one duplicated. An exact set comparison cannot:
 * drop `recheck_store -> check_docs` and the sets differ by exactly that
 * member, and the failure message names it.
 *
 * It also asserts, separately, that the back edge is *identified* as the back
 * edge — `isLoopBack` — because "renders the loop edge" means the reviewer can
 * see it is a loop, not merely that a line exists between two boxes. And it
 * asserts the cycle those three edges form, because the loop's meaning is the
 * cycle rather than the single edge.
 *
 * What it cannot assert is that the drawing is *legible*. That is a design
 * claim, and `BlueprintGraph.test.tsx` gets as close as a test can — every node
 * and the loop edge present in the DOM React Flow produced.
 */

import { describe, expect, it } from "vitest";

import { conditionLabel, depths, topology } from "./topology";
import { GOLDEN_BLUEPRINT } from "@/test/fixtures";
import type { BlueprintView } from "@/mcp/types";

/** `from->to` for every edge in the document. The ground truth for the set. */
const DOCUMENT_EDGES = GOLDEN_BLUEPRINT.edges.map((edge) => `${edge.from}->${edge.to}`);

/** The back edge that closes the loop in the worked example. */
const LOOP_BACK_EDGE = "recheck_store->check_docs";

/** The cycle it belongs to. `docs/worked-example.md` section 3. */
const LOOP_CYCLE = ["check_docs->request_docs", "request_docs->recheck_store", LOOP_BACK_EDGE];

function pairs(blueprint: BlueprintView): string[] {
  return topology(blueprint).edges.map((edge) => `${edge.source}->${edge.target}`);
}

describe("the worked example's topology", () => {
  it("is a nine-node, nine-edge cyclic graph, which is what makes this test worth having", () => {
    // If the fixture ever stops being cyclic, every assertion below about the
    // loop edge becomes vacuous rather than false. This is that guard.
    expect(GOLDEN_BLUEPRINT.nodes).toHaveLength(9);
    expect(GOLDEN_BLUEPRINT.edges).toHaveLength(9);
    expect(DOCUMENT_EDGES).toContain(LOOP_BACK_EDGE);
    expect(GOLDEN_BLUEPRINT.nodes.filter((node) => node.kind === "loop")).toHaveLength(1);
  });

  it("produces exactly the document's edge set — every edge, no extras", () => {
    expect(new Set(pairs(GOLDEN_BLUEPRINT))).toEqual(new Set(DOCUMENT_EDGES));
    // One graph edge per document edge, so a duplicate cannot hide a missing one.
    expect(pairs(GOLDEN_BLUEPRINT)).toHaveLength(GOLDEN_BLUEPRINT.edges.length);
  });

  it("includes the loop edge and marks it as the one that closes the cycle", () => {
    const { edges } = topology(GOLDEN_BLUEPRINT);
    const back = edges.filter((edge) => edge.isLoopBack);
    expect(back.map((edge) => `${edge.source}->${edge.target}`)).toEqual([LOOP_BACK_EDGE]);
  });

  it("renders the whole three-edge cycle, not just the back edge", () => {
    const produced = new Set(pairs(GOLDEN_BLUEPRINT));
    for (const edge of LOOP_CYCLE) {
      expect(produced).toContain(edge);
    }
  });

  it("fails visibly when the loop edge is dropped — the guard's own proof", () => {
    // The mutation the set assertion exists to catch. A count assertion would
    // pass against this if any other edge were duplicated; the set does not.
    const withoutLoop: BlueprintView = {
      ...GOLDEN_BLUEPRINT,
      edges: GOLDEN_BLUEPRINT.edges.filter(
        (edge) => `${edge.from}->${edge.to}` !== LOOP_BACK_EDGE,
      ),
    };
    expect(new Set(pairs(withoutLoop))).not.toEqual(new Set(DOCUMENT_EDGES));
    expect(topology(withoutLoop).edges.filter((edge) => edge.isLoopBack)).toHaveLength(0);
  });

  it("produces one node per document node, every one placed", () => {
    const { nodes } = topology(GOLDEN_BLUEPRINT);
    expect(nodes.map((node) => node.id)).toEqual(GOLDEN_BLUEPRINT.nodes.map((node) => node.id));
    for (const node of nodes) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
    }
  });

  it("reaches every node from the entry node, so nothing is reported unreachable", () => {
    expect(topology(GOLDEN_BLUEPRINT).unreachable).toEqual([]);
    expect(depths(GOLDEN_BLUEPRINT).get("receive_request")).toBe(0);
    // BFS depth, not a DFS path length: `check_docs` is two hops away by the
    // forward path and stays there when the loop arrives from `recheck_store`.
    expect(depths(GOLDEN_BLUEPRINT).get("check_docs")).toBe(2);
    expect(depths(GOLDEN_BLUEPRINT).get("recheck_store")).toBe(4);
  });

  it("is a total function of the document — the same input draws identically", () => {
    // Rulings R-35 and R-58 made every service ordering total and
    // collation-stable so identical inputs give identical output. A layout that
    // depended on iteration order of a Set or on Object.keys of a map would
    // undo that at the last step.
    expect(topology(GOLDEN_BLUEPRINT)).toEqual(topology(GOLDEN_BLUEPRINT));
  });

  it("carries the loop node's pool and iteration bound onto its node data", () => {
    const requestDocs = topology(GOLDEN_BLUEPRINT).nodes.find(
      (node) => node.id === "request_docs",
    );
    expect(requestDocs?.data.kind).toBe("loop");
    expect(requestDocs?.data.pool).toBe(true);
    expect(requestDocs?.data.maxIterations).toBe(3);
  });

  it("reports a node no edge reaches, rather than dropping it from the drawing", () => {
    // BP-005 territory. The editor shows invalid documents on purpose, so the
    // node whose absence *is* the error must still be drawn.
    const orphaned: BlueprintView = {
      ...GOLDEN_BLUEPRINT,
      edges: GOLDEN_BLUEPRINT.edges.filter((edge) => edge.to !== "escalate"),
    };
    const result = topology(orphaned);
    expect(result.unreachable).toEqual(["escalate"]);
    expect(result.nodes.map((node) => node.id)).toContain("escalate");
    expect(result.nodes.find((node) => node.id === "escalate")?.data.reachable).toBe(false);
  });
});

describe("edge condition labels", () => {
  it("renders a JSON Logic branch as a readable predicate", () => {
    expect(conditionLabel({ "==": [{ var: "documents_complete" }, false] })).toBe(
      "documents_complete == false",
    );
  });

  it("has no label for an unconditional edge", () => {
    expect(conditionLabel(null)).toBeNull();
    expect(conditionLabel(undefined)).toBeNull();
  });

  it("labels both branch conditions in the worked example", () => {
    const labels = topology(GOLDEN_BLUEPRINT)
      .edges.filter((edge) => edge.label !== null)
      .map((edge) => edge.label);
    expect(labels).toContain("documents_complete == false");
    expect(labels).toContain("documents_complete == true");
    expect(labels).toContain("compliant == true");
    expect(labels).toContain("compliant == false");
  });
});
