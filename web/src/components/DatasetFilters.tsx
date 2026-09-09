/**
 * The three filters M9 asks for: label, author handle, and free text.
 *
 * Each maps to one `dataset_find` parameter and adds no client-side filtering
 * of its own — which matters more than it looks. Ruling R-36 pinned `q` as a
 * **case-folded substring** match over `title` and `intent` on every backend,
 * and R-39(a) moved the fold into Python so all three backends agree. A browser
 * that re-filtered the rows it got back would answer a different question from
 * the one the service answers, and the reviewer would have no way to tell which
 * they were looking at.
 *
 * The label filter reads its vocabulary from `label_vocabulary`, so the
 * dimensions and their permitted values come from the blueprint rather than
 * from the datasets that happen to exist. The per-value counts come with it, so
 * a reviewer can see that `edge_case=timezone-boundary` has no datasets before
 * selecting it — which is the "twenty-first dataset" problem PRD 5.7 describes,
 * caught one screen earlier.
 */

import type { DatasetFilter, LabelVocabulary } from "@/mcp/types";

export function DatasetFilters({
  filter,
  vocabulary,
  onChange,
}: {
  readonly filter: DatasetFilter;
  readonly vocabulary: LabelVocabulary | undefined;
  readonly onChange: (next: DatasetFilter) => void;
}): React.JSX.Element {
  const labels = filter.labels ?? {};
  const dimensions = Object.entries(vocabulary?.label_schema.dimensions ?? {});
  const active = Object.keys(labels).length + (filter.q ? 1 : 0) + (filter.author ? 1 : 0);

  function setLabel(dimension: string, value: string): void {
    const next: Record<string, string> = { ...labels };
    if (value === "") {
      delete next[dimension];
    } else {
      next[dimension] = value;
    }
    onChange({ ...filter, labels: next });
  }

  return (
    <div className="space-y-3 border-b border-slate-200 bg-slate-50/60 px-4 py-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-[11px] font-medium tracking-wide text-slate-500 uppercase">
            Search title and intent
          </span>
          <input
            data-testid="filter-q"
            type="search"
            value={filter.q ?? ""}
            placeholder="repeat operator"
            onChange={(event) => {
              onChange({ ...filter, q: event.target.value });
            }}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 placeholder:text-slate-300 focus:border-sky-400 focus:outline-none"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-[11px] font-medium tracking-wide text-slate-500 uppercase">
            Author handle
          </span>
          <input
            data-testid="filter-author"
            type="search"
            value={filter.author ?? ""}
            placeholder="pnair"
            onChange={(event) => {
              onChange({ ...filter, author: event.target.value });
            }}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1.5 font-mono text-xs text-slate-900 placeholder:text-slate-300 focus:border-sky-400 focus:outline-none"
          />
        </label>
      </div>

      {dimensions.length > 0 && (
        <div
          data-testid="filter-labels"
          className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
        >
          {dimensions.map(([dimension, values]) => (
            <label key={dimension} className="block">
              <span className="mb-1 block text-[11px] font-medium text-slate-500">{dimension}</span>
              <select
                data-testid={`filter-label-${dimension}`}
                value={labels[dimension] ?? ""}
                onChange={(event) => {
                  setLabel(dimension, event.target.value);
                }}
                className="w-full rounded border border-slate-300 bg-white px-1.5 py-1 text-xs text-slate-900 focus:border-sky-400 focus:outline-none"
              >
                <option value="">any</option>
                {values.map((value) => {
                  const count = vocabulary?.counts[dimension]?.[value] ?? 0;
                  return (
                    <option key={value} value={value}>
                      {value} ({count})
                    </option>
                  );
                })}
              </select>
            </label>
          ))}
        </div>
      )}

      {active > 0 && (
        <button
          type="button"
          data-testid="filter-clear"
          onClick={() => {
            onChange({ ...(filter.agent_id === undefined ? {} : { agent_id: filter.agent_id }) });
          }}
          className="text-[11px] text-sky-700 underline decoration-dotted hover:text-sky-900"
        >
          Clear {active} filter{active === 1 ? "" : "s"}
        </button>
      )}
    </div>
  );
}
