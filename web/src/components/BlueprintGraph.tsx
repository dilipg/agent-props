/**
 * The read-only React Flow view of a blueprint's node graph.
 *
 * "It visualises, it does not author" — the locked stack says so twice, so
 * every interaction flag that could produce a change is off: no dragging, no
 * connecting, no selecting, no deleting, no edge updates. Pan and zoom are the
 * only affordances, because they change the viewport and not the document.
 * There is no `onNodesChange` and no `onEdgesChange`, which is what makes the
 * read-only claim structural rather than a prop that could be flipped back.
 *
 * The layout comes from `src/graph/topology.ts`, a pure function of the
 * document. The cycle in the worked example — `check_docs → request_docs →
 * recheck_store → check_docs` — draws as a back edge marked `isLoopBack`, in a
 * different colour and animated, and `src/graph/topology.test.ts` asserts the
 * whole edge set including it rather than snapshotting a render.
 */

import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import type { Edge, Node, NodeProps } from "@xyflow/react";
import { Handle, Position } from "@xyflow/react";
import { useMemo } from "react";

import type { GraphNode } from "@/graph/topology";
import { topology } from "@/graph/topology";
import type { BlueprintView } from "@/mcp/types";
import "@xyflow/react/dist/style.css";

/** Per-kind accent, so the graph reads as a graph of *kinds* at a glance. */
const KIND_STYLE: Readonly<Record<GraphNode["data"]["kind"], string>> = {
  tool_call: "border-sky-300 bg-sky-50",
  llm: "border-violet-300 bg-violet-50",
  decision: "border-amber-300 bg-amber-50",
  loop: "border-rose-300 bg-rose-50",
  terminal: "border-slate-300 bg-slate-100",
};

type StepNode = Node<GraphNode["data"], "step">;

function StepNodeView({ data }: NodeProps<StepNode>): React.JSX.Element {
  return (
    <div
      data-testid="graph-node"
      data-node-id={data.label}
      data-kind={data.kind}
      className={`min-w-[180px] rounded border px-2.5 py-2 shadow-sm ${KIND_STYLE[data.kind]} ${
        data.reachable ? "" : "border-dashed opacity-60"
      }`}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[12px] font-semibold text-slate-900">{data.label}</span>
        {data.isEntry && (
          <span className="rounded bg-emerald-100 px-1 text-[10px] text-emerald-800">entry</span>
        )}
      </div>
      <div className="mt-0.5 text-[10px] text-slate-500">{data.kind}</div>
      {data.toolName !== null && (
        <div className="mt-0.5 font-mono text-[10px] text-slate-400">{data.toolName}</div>
      )}
      {data.pool && (
        <div className="mt-1 text-[10px] text-rose-700">
          pool{data.maxIterations === null ? "" : ` · max ${String(data.maxIterations)}`}
        </div>
      )}
      {!data.reachable && (
        <div className="mt-1 text-[10px] text-red-700">unreachable from entry (BP-005)</div>
      )}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

const NODE_TYPES = { step: StepNodeView };

export function BlueprintGraph({
  blueprint,
}: {
  readonly blueprint: BlueprintView;
}): React.JSX.Element {
  const { nodes, edges } = useMemo(() => {
    const layout = topology(blueprint);
    const flowNodes: StepNode[] = layout.nodes.map((node) => ({
      id: node.id,
      type: "step",
      position: { x: node.position.x, y: node.position.y },
      data: node.data,
      draggable: false,
      selectable: false,
      connectable: false,
      deletable: false,
    }));
    const flowEdges: Edge[] = layout.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: edge.type,
      ...(edge.label === null ? {} : { label: edge.label }),
      animated: edge.isLoopBack,
      deletable: false,
      selectable: false,
      reconnectable: false,
      style: edge.isLoopBack
        ? { stroke: "#e11d48", strokeWidth: 2 }
        : { stroke: "#94a3b8", strokeWidth: 1.5 },
      labelStyle: { fontSize: 10, fill: "#475569" },
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
      data: { isLoopBack: edge.isLoopBack },
    }));
    return { nodes: flowNodes, edges: flowEdges };
  }, [blueprint]);

  return (
    <div data-testid="blueprint-graph" className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        // Read-only: nothing below can change the document.
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        deleteKeyCode={null}
        selectionKeyCode={null}
        multiSelectionKeyCode={null}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}
