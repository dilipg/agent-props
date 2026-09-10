/**
 * Option B: the run end to end, in the order it happened.
 *
 * A blueprint is a graph, but a *run* is a line — one traversal of that graph,
 * and the thing a reviewer usually wants is to read it top to bottom and find
 * where it went wrong. Ordered by `seq`, which is the order the agent fetched
 * the steps in, so a loop node appears once per iteration in the position it
 * actually ran rather than once in the position it is drawn.
 *
 * The counterpart is `RunGraph`'s topology view, and the two share
 * `StepEvidence` so a step reads identically whichever way you arrived at it.
 */

import { useMemo } from "react";

import { StepEvidence } from "@/components/StepEvidence";
import type { EvidenceNode } from "@/mcp/types";

export function RunSequence({
  nodes,
}: {
  readonly nodes: readonly EvidenceNode[];
}): React.JSX.Element {
  // `seq` is authoritative; the bundle's own order is not promised to be it.
  const ordered = useMemo(() => [...nodes].sort((a, b) => a.seq - b.seq), [nodes]);

  if (ordered.length === 0) {
    return (
      <p data-testid="run-sequence-empty" className="p-4 text-xs text-slate-500">
        This run fetched no steps. A run exists from <code>run_start</code>, so an empty one
        means the agent pinned a dataset and then never called <code>fetch_step</code>.
      </p>
    );
  }

  return (
    <ol data-testid="run-sequence" className="flex flex-col gap-2 p-2">
      {ordered.map((node) => (
        <li key={`${node.node_id}:${String(node.iteration)}:${String(node.seq)}`}>
          <StepEvidence node={node} defaultOpen={false} />
        </li>
      ))}
    </ol>
  );
}
