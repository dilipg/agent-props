/**
 * The dataset list. A **review surface, not a picker**.
 *
 * PRD 5.7 is the design constraint and the M9 brief states it as an acceptance
 * clause: "the list shows enough to judge relevance without a detail fetch".
 * So every row carries the four things provenance exists to answer —
 *
 * | Field | Answers |
 * |---|---|
 * | `title` | What is this, in one line? |
 * | `author` | Who do I ask about it? |
 * | `labels` | How do I find it? (**every** dimension, no gaps) |
 * | `intent` | Why does this dataset exist in the suite? |
 *
 * — and `intent` is rendered **in full**, not truncated. That is the whole
 * point: PRD 5.7 says twenty datasets identified by a UUID and a label tuple
 * are twenty things nobody will reuse, and a list that elides the intent to
 * one line is that failure with extra steps. The narrative excerpt is here
 * too, greyed, because it answers a different question and a reviewer scanning
 * for relevance wants the purpose first.
 *
 * All of it comes from `dataset_find`'s `DatasetSummary` (contracts 2.2.1), so
 * the list makes **one** call for the whole page — no per-row `dataset_get`.
 * `src/components/DatasetList.test.tsx` asserts that count, because "without a
 * detail fetch" is a claim about how many requests happen, and that is the part
 * of clause 3 a test can actually pin down.
 */

import type { DatasetSummary } from "@/mcp/types";

/** The dimensions to show, in the blueprint's declared order where known. */
function orderedLabels(
  labels: Readonly<Record<string, string>>,
  dimensions: readonly string[] | undefined,
): readonly [string, string][] {
  const declared = dimensions ?? [];
  const known = declared.filter((dimension) => dimension in labels);
  const extra = Object.keys(labels)
    .filter((key) => !declared.includes(key))
    .sort();
  return [...known, ...extra].map((key) => [key, labels[key] as string]);
}

export function LabelChips({
  labels,
  dimensions,
}: {
  readonly labels: Readonly<Record<string, string>>;
  readonly dimensions?: readonly string[];
}): React.JSX.Element {
  const entries = orderedLabels(labels, dimensions);
  return (
    <ul data-testid="labels" className="flex flex-wrap gap-1.5">
      {entries.map(([dimension, value]) => (
        <li
          key={dimension}
          data-dimension={dimension}
          data-value={value}
          className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] leading-4"
        >
          <span className="text-slate-400">{dimension}</span>
          <span className="text-slate-300"> · </span>
          <span className="font-medium text-slate-700">{value}</span>
        </li>
      ))}
    </ul>
  );
}

export function DatasetRow({
  summary,
  dimensions,
  selected,
  onSelect,
}: {
  readonly summary: DatasetSummary;
  readonly dimensions?: readonly string[];
  readonly selected: boolean;
  readonly onSelect: (id: string) => void;
}): React.JSX.Element {
  return (
    <li data-testid="dataset-row" data-dataset-id={summary.id}>
      <button
        type="button"
        onClick={() => {
          onSelect(summary.id);
        }}
        aria-current={selected ? "true" : undefined}
        className={`block w-full border-l-2 px-4 py-3 text-left transition-colors ${
          selected
            ? "border-l-sky-500 bg-sky-50/60"
            : "border-l-transparent hover:bg-slate-50 focus-visible:bg-slate-50"
        }`}
      >
        <div className="flex items-baseline justify-between gap-3">
          <h3 data-testid="row-title" className="text-sm font-semibold text-slate-900">
            {summary.title}
          </h3>
          <span className="shrink-0 font-mono text-[11px] text-slate-400">
            v{summary.version}
          </span>
        </div>

        <p className="mt-0.5 text-[11px] text-slate-500">
          <span data-testid="row-author" data-handle={summary.author.handle}>
            {summary.author.name} (<span className="font-mono">@{summary.author.handle}</span>)
            {summary.author.agent === "human" ? "" : ` · via ${summary.author.agent}`}
          </span>
          <span className="text-slate-300"> · </span>
          <span className="font-mono">
            {summary.blueprint.agent_id}@{summary.blueprint.version}
          </span>
        </p>

        <div className="mt-2">
          <LabelChips labels={summary.labels} {...(dimensions === undefined ? {} : { dimensions })} />
        </div>

        {/* The intent, in full. Truncating it is the failure this list exists to avoid. */}
        <p data-testid="row-intent" className="mt-2 text-xs leading-5 text-slate-700">
          {summary.intent}
        </p>

        <p data-testid="row-narrative-excerpt" className="mt-1.5 text-[11px] leading-5 text-slate-400">
          {summary.narrative_excerpt}
        </p>
      </button>
    </li>
  );
}

export function DatasetList({
  datasets,
  dimensions,
  selectedId,
  onSelect,
  isLoading,
  error,
}: {
  readonly datasets: readonly DatasetSummary[];
  readonly dimensions?: readonly string[];
  readonly selectedId: string | undefined;
  readonly onSelect: (id: string) => void;
  readonly isLoading: boolean;
  readonly error: Error | null;
}): React.JSX.Element {
  if (error !== null) {
    return (
      <p data-testid="dataset-list-error" className="px-4 py-6 text-xs text-red-700">
        {error.message}
      </p>
    );
  }
  if (isLoading) {
    return (
      <p data-testid="dataset-list-loading" className="px-4 py-6 text-xs text-slate-400">
        Loading datasets…
      </p>
    );
  }
  if (datasets.length === 0) {
    return (
      <p data-testid="dataset-list-empty" className="px-4 py-6 text-xs text-slate-500">
        No dataset matches this filter.
      </p>
    );
  }
  return (
    <ul data-testid="dataset-list" className="divide-y divide-slate-100">
      {datasets.map((summary) => (
        <DatasetRow
          key={summary.id}
          summary={summary}
          {...(dimensions === undefined ? {} : { dimensions })}
          selected={summary.id === selectedId}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}
