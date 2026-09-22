/**
 * Which runs have been made against one dataset.
 *
 * The other half of the dataset detail view's question. Narrative and intent say
 * what this world is and why it is in the suite; this says whether anything has
 * ever been run through it — which is how a reviewer spots a dataset that was
 * authored and then forgotten.
 *
 * The filter goes to `run_find` as `dataset_id`, not to a client-side `filter()`
 * over every run. That tool pages at fifty, so filtering here would show a
 * truncated set on exactly the stores where the panel matters most, and would
 * look correct on a small one.
 *
 * `dataset_id` is the **lineage** id, so this lists runs across every version of
 * the dataset. Each row therefore says which version it pinned: two runs of "the
 * same dataset" a month apart may have been served different documents, and a
 * list that hid that would invite the wrong conclusion from a differing result.
 */

import type { RunSummary } from "@/mcp/types";
import { useRuns } from "@/queries";

const STATUS_STYLE: Readonly<Record<string, string>> = {
  finished: "bg-emerald-100 text-emerald-800",
  running: "bg-sky-100 text-sky-800",
  abandoned: "bg-slate-200 text-slate-700",
};

function clock(stamp: string): string {
  const parsed = new Date(stamp);
  return Number.isNaN(parsed.getTime()) ? stamp : parsed.toLocaleString();
}

export function DatasetRuns({
  agentId,
  datasetId,
  onOpenRun,
}: {
  readonly agentId: string | undefined;
  readonly datasetId: string;
  readonly onOpenRun: (runId: string) => void;
}): React.JSX.Element {
  const runs = useRuns(agentId, datasetId);

  if (runs.error !== null) {
    return (
      <p data-testid="dataset-runs-error" className="px-2 py-1.5 text-[11px] text-red-700">
        {runs.error.message}
      </p>
    );
  }

  const rows: readonly RunSummary[] = runs.data ?? [];

  if (runs.data !== undefined && rows.length === 0) {
    return (
      <p data-testid="dataset-runs-empty" className="px-2 py-2 text-[11px] text-slate-500">
        Nothing has run against this dataset yet. A run appears here once an agent calls{" "}
        <code>run_start</code> with it pinned.
      </p>
    );
  }

  return (
    <ul data-testid="dataset-runs">
      {rows.map((run) => (
        <li key={run.id}>
          <button
            type="button"
            data-testid="dataset-run-row"
            data-run-id={run.id}
            data-dataset-version={run.dataset_ver}
            onClick={() => {
              onOpenRun(run.id);
            }}
            className="flex w-full items-baseline gap-2 border-b border-slate-100 px-2 py-1.5 text-left hover:bg-slate-50"
          >
            <span className="font-mono text-[11px] text-slate-900">{run.id.slice(0, 8)}</span>
            <span
              className={`rounded px-1 text-[10px] ${
                STATUS_STYLE[run.status] ?? "bg-slate-200 text-slate-700"
              }`}
            >
              {run.status}
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              pinned v{run.dataset_ver} · blueprint {run.bp_version}
            </span>
            <span className="ml-auto text-[10px] text-slate-400">{clock(run.started_at)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
