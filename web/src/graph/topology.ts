/**
 * A blueprint's node graph, as React Flow nodes and edges. Pure, no React.
 *
 * Read-only by construction, which is the locked stack's requirement for the
 * graph view: it visualises, it does not author. Nothing here produces a
 * mutation, and the component that renders it passes React Flow's interaction
 * flags off.
 *
 * Why the layout is hand-rolled
 * -----------------------------
 *
 * React Flow needs an `{x, y}` per node and computes none. The usual answer is
 * `dagre` or `elkjs`, and neither is in the locked stack — so this is a layered
 * layout in forty lines instead of a third graph library for one screen.
 *
 * Depth is a **breadth-first** distance from `entry_node`, which is the right
 * shape for a cyclic graph: BFS reaches `check_docs` at depth 2 by the forward
 * path and never re-deepens it when the loop arrives from `recheck_store`, so
 * the cycle draws as a back edge rather than stretching the diagram. Ordering
 * within a depth follows the blueprint's own `nodes` order, which makes the
 * whole layout a **total function of the document** — the same blueprint always
 * draws identically, in the spirit of rulings R-35 and R-58 about every
 * ordering being total.
 *
 * A node unreachable from `entry_node` is BP-005 and the blueprint will be
 * rejected, but the editor shows invalid documents on purpose — that is the
 * point of live validation — so such a node is placed in a final row rather
 * than dropped. A view that silently hid the node whose absence is the error
 * would be the worst possible answer.
 */

import type { BlueprintEdge, BlueprintNode, BlueprintView } from "@/mcp/types";

/** Horizontal spacing between nodes in a row, in pixels. */
const COLUMN_WIDTH = 260;
/** Vertical spacing between depths, in pixels. */
const ROW_HEIGHT = 130;

export interface GraphNode {
  readonly id: string;
  readonly position: { readonly x: number; readonly y: number };
  readonly data: {
    readonly label: string;
    readonly kind: BlueprintNode["kind"];
    readonly toolName: string | null;
    readonly pool: boolean;
    readonly maxIterations: number | null;
    readonly isEntry: boolean;
    readonly reachable: boolean;
  };
  readonly type: "step";
}

export interface GraphEdge {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  /** The JSON Logic condition rendered for a label, or null for an unconditional edge. */
  readonly label: string | null;
  /**
   * True when the edge points at a node at the same depth or shallower — the
   * edge that closes a cycle. `recheck_store -> check_docs` in the worked
   * example, which is the loop the M9 gate names.
   */
  readonly isLoopBack: boolean;
  readonly type: "smoothstep";
}

export interface Topology {
  readonly nodes: readonly GraphNode[];
  readonly edges: readonly GraphEdge[];
  /** Node ids not reachable from `entry_node`. BP-005 territory. */
  readonly unreachable: readonly string[];
}

/**
 * Breadth-first depth per node id, from `entry_node`.
 *
 * Nodes the walk never reaches are absent from the map, which is what
 * {@link topology} reads to place them and to report them.
 */
export function depths(blueprint: BlueprintView): Map<string, number> {
  const outgoing = new Map<string, string[]>();
  for (const edge of blueprint.edges) {
    const existing = outgoing.get(edge.from);
    if (existing === undefined) {
      outgoing.set(edge.from, [edge.to]);
    } else {
      existing.push(edge.to);
    }
  }
  const depth = new Map<string, number>();
  const known = new Set(blueprint.nodes.map((node) => node.id));
  if (!known.has(blueprint.entry_node)) {
    return depth;
  }
  depth.set(blueprint.entry_node, 0);
  let frontier = [blueprint.entry_node];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const id of frontier) {
      const here = depth.get(id) ?? 0;
      for (const target of outgoing.get(id) ?? []) {
        if (!known.has(target) || depth.has(target)) {
          continue;
        }
        depth.set(target, here + 1);
        next.push(target);
      }
    }
    frontier = next;
  }
  return depth;
}

/** A JSON Logic condition as a short label, or null when there is none. */
export function conditionLabel(condition: BlueprintEdge["condition"]): string | null {
  if (condition === null || condition === undefined) {
    return null;
  }
  const entries = Object.entries(condition);
  const first = entries[0];
  if (entries.length === 1 && first !== undefined && Array.isArray(first[1])) {
    const operator = first[0];
    const operands: readonly unknown[] = first[1];
    const left = operands[0];
    const variable =
      typeof left === "object" && left !== null && "var" in left
        ? String(left.var)
        : JSON.stringify(left);
    return `${variable} ${operator} ${JSON.stringify(operands[1])}`;
  }
  return JSON.stringify(condition);
}

/**
 * The whole layout, in one pass over the document.
 *
 * Every edge in `blueprint.edges` produces exactly one {@link GraphEdge},
 * including the one that closes the cycle — which is why M9's fourth clause can
 * be asserted as an edge *set* rather than a rendered snapshot. Edge ids are
 * `from->to#index`, so a blueprint with two edges between the same pair (one
 * per branch condition) still yields two distinct edges rather than one that
 * silently replaced the other.
 */
export function topology(blueprint: BlueprintView): Topology {
  const depth = depths(blueprint);
  const maxDepth = Math.max(-1, ...depth.values());
  const unreachableRow = maxDepth + 1;

  const rows = new Map<number, string[]>();
  for (const node of blueprint.nodes) {
    const row = depth.get(node.id) ?? unreachableRow;
    const bucket = rows.get(row);
    if (bucket === undefined) {
      rows.set(row, [node.id]);
    } else {
      bucket.push(node.id);
    }
  }

  const nodes = blueprint.nodes.map((node): GraphNode => {
    const row = depth.get(node.id) ?? unreachableRow;
    const bucket = rows.get(row) ?? [];
    const column = bucket.indexOf(node.id);
    const offset = (bucket.length - 1) / 2;
    return {
      id: node.id,
      type: "step",
      position: { x: (column - offset) * COLUMN_WIDTH, y: row * ROW_HEIGHT },
      data: {
        label: node.id,
        kind: node.kind,
        toolName: node.tool_name ?? null,
        pool: node.pool ?? false,
        maxIterations: node.max_iterations ?? null,
        isEntry: node.id === blueprint.entry_node,
        reachable: depth.has(node.id),
      },
    };
  });

  const edges = blueprint.edges.map((edge, index): GraphEdge => {
    const from = depth.get(edge.from);
    const to = depth.get(edge.to);
    return {
      id: `${edge.from}->${edge.to}#${String(index)}`,
      source: edge.from,
      target: edge.to,
      label: conditionLabel(edge.condition),
      isLoopBack: from !== undefined && to !== undefined && to <= from,
      type: "smoothstep",
    };
  });

  return {
    nodes,
    edges,
    unreachable: blueprint.nodes.filter((n) => !depth.has(n.id)).map((n) => n.id),
  };
}
