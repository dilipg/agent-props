/**
 * The run picker: one row per run, enough to choose without opening one.
 *
 * The same principle as `DatasetList` — a row a reviewer can judge — but the
 * judgeable facts differ. For a dataset it is title, intent and labels; for a
 * run it is which dataset version was pinned, whether it finished, and when.
 * `run_find` returns all of that, so this needs no per-row detail call.
 */

import type { RunSummary } from "@/mcp/types";

const STATUS_STYLE: Readonly<Record<string, string>> = {
  finished: "bg-emerald-100 text-emerald-800",
  running: "bg-sky-100 text-sky-800",
  abandoned: "bg-slate-200 text-slate-700",
};

/** `2026-09-10T04:17:14.905396Z` is unreadable in a list; the clock time is not. */
function clock(stamp: string): string {
  const parsed = new Date(stamp);
  return Number.isNaN(parsed.getTime()) ? stamp : parsed.toLocaleString();
}

export function RunList({
  runs,
  selected,
  onSelect,
}: {
  readonly runs: readonly RunSummary[];
  readonly selected: string | undefined;
  readonly onSelect: (runId: string) => void;
}): React.JSX.Element {
  if (runs.length === 0) {
    return (
      <p data-testid="run-list-empty" className="p-4 text-xs text-slate-500">
        No runs for this agent yet. A run appears here once something calls{" "}
        <code>run_start</code> — this app never creates one.
      </p>
    );
  }

  return (
    <ul data-testid="run-list">
      {runs.map((run) => (
        <li key={run.id}>
          <button
            type="button"
            data-testid="run-row"
            data-run-id={run.id}
            aria-current={run.id === selected}
            onClick={() => {
              onSelect(run.id);
            }}
            className={`flex w-full flex-col gap-0.5 border-b border-slate-100 px-3 py-2 text-left hover:bg-slate-50 ${
              run.id === selected ? "bg-slate-100" : ""
            }`}
          >
            <span className="flex items-baseline gap-2">
              <span className="font-mono text-[12px] text-slate-900">{run.id.slice(0, 8)}</span>
              <span
                data-testid="run-status"
                className={`rounded px-1 text-[10px] ${
                  STATUS_STYLE[run.status] ?? "bg-slate-200 text-slate-700"
                }`}
              >
                {run.status}
              </span>
              <span className="ml-auto text-[10px] text-slate-400">{clock(run.started_at)}</span>
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              {run.agent_id} · blueprint {run.bp_version} · dataset {run.dataset_id.slice(0, 8)} v
              {run.dataset_ver}
            </span>
            {run.warnings.length > 0 && (
              <span data-testid="run-row-warnings" className="text-[10px] text-amber-700">
                {run.warnings.length} warning{run.warnings.length === 1 ? "" : "s"}
              </span>
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}
