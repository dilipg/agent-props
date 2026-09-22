/**
 * The runs screen: pick a run, then read it as a graph or as a sequence.
 *
 * Two views of one bundle, because the two questions reviewers arrive with are
 * different shapes:
 *
 * - **graph** — "which way did it go, and what happened at that node?" The
 *   blueprint's own topology with the run folded onto it; click a node for its
 *   evidence. Answers branch and reachability questions a list cannot.
 * - **sequence** — "walk me through it end to end." Every step in `seq` order,
 *   loop iterations appearing once each where they actually ran. Answers "where
 *   did it start going wrong", which a graph makes you hunt for.
 *
 * Both read the same `run_evidence` bundle and render steps through the same
 * `StepEvidence`, so switching view never changes what a step says.
 *
 * The graph needs the blueprint as well, and it must be the **pinned** version:
 * `bundle.pin.blueprint_version`, not the agent's latest. A run against 0.2.0
 * drawn on 0.3.0's topology would show nodes that did not exist when it ran.
 */

import { useEffect, useMemo, useState } from "react";

import { BlueprintGraph } from "@/components/BlueprintGraph";
import type { RunOverlay } from "@/components/BlueprintGraph";
import { Warnings } from "@/components/Findings";
import { RunList } from "@/components/RunList";
import { RunSequence } from "@/components/RunSequence";
import { StepEvidence } from "@/components/StepEvidence";
import type { EvidenceBundle, EvidenceNode } from "@/mcp/types";
import { useBlueprint, useRunEvidence, useRuns } from "@/queries";

type View = "graph" | "sequence";

/** One entry per node the run touched, folding its iterations together. */
function overlayFor(nodes: readonly EvidenceNode[]): ReadonlyMap<string, RunOverlay> {
  const byNode = new Map<string, RunOverlay>();
  for (const node of nodes) {
    const seen = byNode.get(node.node_id);
    byNode.set(node.node_id, {
      visits: (seen?.visits ?? 0) + 1,
      firstSeq: Math.min(seen?.firstSeq ?? node.seq, node.seq),
      recorded: (seen?.recorded ?? 0) + (node.recorded ? 1 : 0),
      faults: (seen?.faults ?? 0) + (node.fault === null ? 0 : 1),
    });
  }
  return byNode;
}

function Field({
  label,
  children,
}: {
  readonly label: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="truncate font-mono text-[11px] text-slate-700">{children}</dd>
    </div>
  );
}

/**
 * The run header: the pin, the mode, and the run-level outcome.
 *
 * `comparison` is shown because it decides what "expected vs actual" even
 * means. Under `subset` the two are allowed to differ; a reviewer told only
 * "expected" and "actual" would read a passing run as a failing one.
 */
function RunHeader({
  bundle,
  onOpenDataset,
}: {
  readonly bundle: EvidenceBundle;
  readonly onOpenDataset?: ((datasetId: string, version: number) => void) | undefined;
}): React.JSX.Element {
  return (
    <div data-testid="run-header" className="border-b border-slate-200 bg-white px-3 py-2">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-5">
        <Field label="run">{bundle.run.id.slice(0, 8)}</Field>
        <Field label="status">{bundle.run.status}</Field>
        <Field label="blueprint">{bundle.pin.blueprint_version}</Field>
        <Field label="dataset">
          {onOpenDataset === undefined ? (
            `${bundle.pin.dataset_id.slice(0, 8)} v${String(bundle.pin.dataset_version)}`
          ) : (
            <button
              type="button"
              data-testid="run-dataset-link"
              onClick={() => {
                onOpenDataset(bundle.pin.dataset_id, bundle.pin.dataset_version);
              }}
              className="text-sky-700 underline underline-offset-2 hover:text-sky-900"
            >
              {bundle.pin.dataset_id.slice(0, 8)} v{bundle.pin.dataset_version}
            </button>
          )}
        </Field>
        <Field label="comparison">{bundle.comparison}</Field>
      </dl>
      <p className="mt-1 text-[10px] text-slate-500">
        Expected and actual are shown side by side and nothing here computes a verdict — under{" "}
        <code>{bundle.comparison}</code> the two may legitimately differ. Grade with{" "}
        <code>agentprops_client.compare.grade</code>.
      </p>
    </div>
  );
}

/** The run-level outcome, which is a different thing from any step's output. */
function RunOutcome({ bundle }: { readonly bundle: EvidenceBundle }): React.JSX.Element {
  return (
    <div data-testid="run-outcome" className="grid gap-2 p-2 lg:grid-cols-2">
      <section className="min-w-0 rounded border border-amber-200 bg-amber-50/60">
        <header className="border-b border-white/70 px-2 py-1">
          <h4 className="text-[11px] font-semibold text-slate-800">expected outcome</h4>
          <p className="text-[10px] text-slate-500">the dataset author&rsquo;s expected.final</p>
        </header>
        <pre className="max-h-64 overflow-auto px-2 py-1.5 font-mono text-[11px] text-slate-700">
          {JSON.stringify(bundle.expected.final ?? null, null, 2)}
        </pre>
      </section>
      <section className="min-w-0 rounded border border-emerald-200 bg-emerald-50/60">
        <header className="border-b border-white/70 px-2 py-1">
          <h4 className="text-[11px] font-semibold text-slate-800">recorded outcome</h4>
          <p className="text-[10px] text-slate-500">what run_finish stored, verbatim</p>
        </header>
        <pre className="max-h-64 overflow-auto px-2 py-1.5 font-mono text-[11px] text-slate-700">
          {JSON.stringify(bundle.actual, null, 2)}
        </pre>
      </section>
    </div>
  );
}

export function RunsScreen({
  agentId,
  focus,
  onOpenDataset,
}: {
  readonly agentId: string | undefined;
  /** A run another screen asked this one to open. */
  readonly focus?: string | undefined;
  /**
   * Open a dataset elsewhere in the app. Given the **pinned** version, not the
   * lineage's latest: a run reads one frozen version for its whole life, so the
   * latest may be a document this run never saw.
   */
  readonly onOpenDataset?: ((datasetId: string, version: number) => void) | undefined;
}): React.JSX.Element {
  const [selectedRun, setSelectedRun] = useState<string | undefined>(undefined);
  const [view, setView] = useState<View>("sequence");
  const [selectedNode, setSelectedNode] = useState<string | undefined>(undefined);

  // A focus is a request from another screen, so it wins over local selection.
  useEffect(() => {
    if (focus === undefined) return;
    setSelectedRun(focus);
    setSelectedNode(undefined);
  }, [focus]);

  const runs = useRuns(agentId);
  const evidence = useRunEvidence(selectedRun);
  const bundle = evidence.data?.value;

  // The pinned version, so the topology matches the run rather than the latest.
  const blueprint = useBlueprint(bundle?.run.agent_id, bundle?.pin.blueprint_version);

  const overlay = useMemo(() => overlayFor(bundle?.nodes ?? []), [bundle?.nodes]);
  const focused = useMemo(
    () => (bundle?.nodes ?? []).filter((node) => node.node_id === selectedNode),
    [bundle?.nodes, selectedNode],
  );

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(300px,0.6fr)_1.6fr]">
      <section className="flex min-h-0 flex-col overflow-hidden border-r border-slate-200 bg-white">
        <header className="border-b border-slate-200 px-3 py-2">
          <h2 className="text-xs font-semibold text-slate-700">runs</h2>
          <p className="text-[10px] text-slate-500">
            {runs.data === undefined ? "loading…" : `${String(runs.data.length)} in this store`}
          </p>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {runs.error !== null ? (
            <p className="p-4 text-xs text-red-700">{runs.error.message}</p>
          ) : (
            <RunList
              runs={runs.data ?? []}
              selected={selectedRun}
              onSelect={(runId) => {
                setSelectedRun(runId);
                setSelectedNode(undefined);
              }}
            />
          )}
        </div>
      </section>

      <section className="flex min-h-0 flex-col overflow-hidden bg-slate-50">
        {bundle === undefined ? (
          <p data-testid="run-none-selected" className="p-4 text-xs text-slate-500">
            {evidence.error === null
              ? "Pick a run to see the fixture served at each step and the output the agent recorded."
              : evidence.error.message}
          </p>
        ) : (
          <>
            <RunHeader bundle={bundle} onOpenDataset={onOpenDataset} />
            <Warnings warnings={evidence.data?.warnings ?? []} />

            <nav className="flex shrink-0 gap-1 border-b border-slate-200 bg-white px-3 py-1.5 text-xs">
              {(["sequence", "graph"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  data-testid={`run-view-${option}`}
                  aria-pressed={view === option}
                  onClick={() => {
                    setView(option);
                  }}
                  className={`rounded px-2 py-1 ${
                    view === option
                      ? "bg-slate-900 text-white"
                      : "text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {option}
                </button>
              ))}
              <span className="ml-auto self-center text-[10px] text-slate-400">
                {bundle.nodes.length} steps · path {bundle.path.actual.length} long
              </span>
            </nav>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {view === "sequence" ? (
                <>
                  <RunOutcome bundle={bundle} />
                  <RunSequence nodes={bundle.nodes} />
                </>
              ) : (
                <div className="flex h-full min-h-0 flex-col">
                  <div
                    className={`${
                      focused.length === 0 ? "flex-1" : "h-[55%]"
                    } min-h-[260px] border-b border-slate-200 bg-white`}
                  >
                    {blueprint.data === undefined ? (
                      <p className="p-4 text-xs text-slate-500">
                        {blueprint.error === null
                          ? "loading the pinned blueprint…"
                          : blueprint.error.message}
                      </p>
                    ) : (
                      <BlueprintGraph
                        blueprint={blueprint.data}
                        overlay={overlay}
                        selected={selectedNode}
                        onSelect={setSelectedNode}
                      />
                    )}
                  </div>
                  <div
                    className={`min-h-0 overflow-y-auto p-2 ${
                      focused.length === 0 ? "shrink-0" : "flex-1"
                    }`}
                  >
                    {focused.length === 0 ? (
                      <p data-testid="graph-no-node-selected" className="p-2 text-xs text-slate-500">
                        Click a node to see the fixture it was served and the output the agent
                        recorded for it. Dimmed nodes are ones this run never reached.
                      </p>
                    ) : (
                      <div className="flex flex-col gap-2">
                        {focused.map((node) => (
                          <StepEvidence key={`${node.node_id}:${String(node.iteration)}`} node={node} />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
