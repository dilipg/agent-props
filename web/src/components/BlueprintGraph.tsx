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
 *
 * The run overlay, and why clicking does not break the read-only claim
 * --------------------------------------------------------------------
 *
 * `overlay` and `onSelect` are optional, and with neither passed this renders
 * exactly what it always did. With them, the same graph becomes the run view's
 * option A: each node wears what happened to it on this run, and clicking one
 * opens its evidence.
 *
 * The click is a plain `onClick` on the node's own element, deliberately **not**
 * React Flow's selection. `elementsSelectable` stays `false` and no
 * `.react-flow__node.selectable` is emitted - which is the DOM fact
 * `BlueprintGraph.test.tsx` asserts, and it still holds. Read-only is about the
 * *document*: choosing which node's evidence to read changes app state and
 * nothing else, the same as clicking a row in a list.
 */

import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import type { Edge, Node, NodeProps } from "@xyflow/react";
import { Handle, Position } from "@xyflow/react";
import { useMemo } from "react";

import type { GraphNode } from "@/graph/topology";
import { topology } from "@/graph/topology";
import type { BlueprintView } from "@/mcp/types";
import "@xyflow/react/dist/style.css";

/**
 * What one run did to one node, folded onto the graph.
 *
 * `visits` rather than a boolean because a loop node runs more than once, and a
 * graph showing "visited" for a node the agent went round three times would
 * hide the very thing a reviewer opens the loop dataset to see.
 */
export interface RunOverlay {
  readonly visits: number;
  readonly firstSeq: number;
  readonly recorded: number;
  readonly faults: number;
}

/**
 * What the node view needs beyond the blueprint's own data.
 *
 * A `type` rather than an `interface`: React Flow constrains node data to
 * `Record<string, unknown>`, and only a type alias picks up the implicit index
 * signature that satisfies it. An interface here fails to compile.
 */
type RunData = {
  readonly overlay: RunOverlay | null;
  readonly selected: boolean;
  readonly onSelect: ((nodeId: string) => void) | null;
  /** True when this graph is showing a run at all, visited or not. */
  readonly inRun: boolean;
};

/** Per-kind accent, so the graph reads as a graph of *kinds* at a glance. */
const KIND_STYLE: Readonly<Record<GraphNode["data"]["kind"], string>> = {
  tool_call: "border-sky-300 bg-sky-50",
  llm: "border-violet-300 bg-violet-50",
  decision: "border-amber-300 bg-amber-50",
  loop: "border-rose-300 bg-rose-50",
  terminal: "border-slate-300 bg-slate-100",
};

type StepNode = Node<GraphNode["data"] & RunData, "step">;

function StepNodeView({ data }: NodeProps<StepNode>): React.JSX.Element {
  const { overlay, inRun, selected, onSelect } = data;
  // In a run, a node the agent never reached is dimmed - the visual answer to
  // "which branch did it take". Outside a run nothing is dimmed but the
  // unreachable ones, which is the blueprint view's existing meaning.
  const missed = inRun && overlay === null;
  const clickable =
    onSelect === null
      ? {}
      : {
          role: "button",
          tabIndex: 0,
          onClick: () => {
            onSelect(data.label);
          },
          onKeyDown: (event: React.KeyboardEvent) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onSelect(data.label);
            }
          },
        };
  return (
    <div
      data-testid="graph-node"
      data-node-id={data.label}
      data-kind={data.kind}
      {...(overlay === null ? {} : { "data-visits": String(overlay.visits) })}
      {...(missed ? { "data-not-visited": "true" } : {})}
      {...clickable}
      {...(onSelect === null
        ? {}
        : // React Flow puts `pointer-events: none` on a node that is neither
          // draggable, selectable nor connectable - which is every node here,
          // and is the read-only posture worth keeping. A clickable node has to
          // re-enable pointer events on its own element, and it has to be an
          // inline style rather than a utility class: this must hold wherever
          // the component renders, including a jsdom test with no stylesheet.
          { style: { pointerEvents: "auto" as const } })}
      className={`min-w-[180px] rounded border px-2.5 py-2 shadow-sm ${KIND_STYLE[data.kind]} ${
        data.reachable ? "" : "border-dashed opacity-60"
      } ${missed ? "opacity-40" : ""} ${
        // `nopan` is React Flow's own opt-out: its d3-zoom filter refuses any
        // gesture starting inside one. Without it the pane beneath treats the
        // mousedown as the start of a pan, so the graph lurches under the
        // cursor as the evidence panel opens. Stopping the React event would
        // not do it - React dispatches at the root container, by which point
        // the pane's native listener has already run.
        onSelect === null ? "" : "nopan cursor-pointer"
      } ${
        selected ? "ring-2 ring-slate-900" : ""
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
      {overlay !== null && (
        <div data-testid="graph-node-run" className="mt-1 flex flex-wrap items-center gap-1">
          <span className="rounded bg-slate-900 px-1 text-[10px] text-white">
            step {overlay.firstSeq}
          </span>
          {overlay.visits > 1 && (
            <span
              data-testid="graph-node-visits"
              className="rounded bg-rose-100 px-1 text-[10px] text-rose-800"
            >
              x{overlay.visits}
            </span>
          )}
          {overlay.recorded < overlay.visits && (
            <span className="rounded bg-slate-200 px-1 text-[10px] text-slate-700">
              {overlay.visits - overlay.recorded} unrecorded
            </span>
          )}
          {overlay.faults > 0 && (
            <span className="rounded bg-orange-100 px-1 text-[10px] text-orange-800">fault</span>
          )}
        </div>
      )}
      {missed && <div className="mt-1 text-[10px] text-slate-500">not visited</div>}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

const NODE_TYPES = { step: StepNodeView };

export function BlueprintGraph({
  blueprint,
  overlay,
  selected,
  onSelect,
}: {
  readonly blueprint: BlueprintView;
  /** Per-node run state. Absent means "no run": the plain blueprint view. */
  readonly overlay?: ReadonlyMap<string, RunOverlay>;
  readonly selected?: string | undefined;
  readonly onSelect?: (nodeId: string) => void;
}): React.JSX.Element {
  const { nodes, edges } = useMemo(() => {
    const layout = topology(blueprint);
    const flowNodes: StepNode[] = layout.nodes.map((node) => ({
      id: node.id,
      type: "step",
      position: { x: node.position.x, y: node.position.y },
      data: {
        ...node.data,
        overlay: overlay?.get(node.id) ?? null,
        inRun: overlay !== undefined,
        selected: node.id === selected,
        onSelect: onSelect ?? null,
      },
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
  }, [blueprint, overlay, selected, onSelect]);

  return (
    <div data-testid="blueprint-graph" className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        // React Flow will not zoom out past `minZoom` to fit, and the default
        // is 0.5 - so a tall topology in a short pane silently renders clipped
        // at both ends rather than fitted. The run view puts this graph in half
        // a pane, which is exactly where the default bites.
        minZoom={0.15}
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
