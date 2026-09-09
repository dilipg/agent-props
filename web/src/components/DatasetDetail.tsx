/**
 * The dataset detail view: **narrative and intent side by side**.
 *
 * PRD 5.7 is explicit that these are different fields answering different
 * questions, and that they "must not be filled with the same text":
 *
 * - the **narrative** describes the world — it is what the agent sees;
 * - the **intent** describes the purpose in the suite — what behaviour it pins
 *   down, what bug prompted it, what would go untested if it were deleted. It
 *   is what a reviewer needs.
 *
 * Putting them in two columns is not decoration. Side by side is what makes an
 * author who pasted the narrative into the intent see it immediately, which is
 * the mistake DS-027 warns about — and a reviewer comparing "what happens" with
 * "why we care" is doing the one thing this screen exists for.
 *
 * The columns stack on a narrow viewport, which loses the comparison. That is
 * the right trade for a two-column layout at 380px; the labels stay above so
 * the two fields are never confusable for one another.
 */

import { LabelChips } from "./DatasetList";
import type { DatasetView } from "@/mcp/types";

export function DatasetDetail({
  dataset,
  dimensions,
}: {
  readonly dataset: DatasetView;
  readonly dimensions?: readonly string[];
}): React.JSX.Element {
  const { provenance } = dataset;
  return (
    <article data-testid="dataset-detail" className="space-y-4">
      <header className="space-y-2">
        <h2 className="text-base font-semibold text-slate-900">{provenance.title}</h2>
        <p className="text-xs text-slate-500">
          <span data-testid="detail-author" data-handle={provenance.author.handle}>
            {provenance.author.name} (<span className="font-mono">@{provenance.author.handle}</span>
            )
          </span>
          <span className="text-slate-300"> · </span>
          <span className="font-mono">
            {dataset.blueprint.agent_id}@{dataset.blueprint.version}
          </span>
          <span className="text-slate-300"> · </span>
          <span className="font-mono">
            {dataset.id.slice(0, 8)} v{dataset.version}
          </span>
          {dataset.archived && (
            <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-[11px] text-slate-700">
              archived
            </span>
          )}
        </p>
        <LabelChips labels={dataset.labels} {...(dimensions === undefined ? {} : { dimensions })} />
      </header>

      <div data-testid="narrative-and-intent" className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <section data-testid="detail-narrative" className="rounded border border-slate-200 bg-white">
          <h3 className="border-b border-slate-100 px-3 py-2 text-[11px] font-semibold tracking-wide text-slate-500 uppercase">
            Narrative
            <span className="ml-2 font-normal tracking-normal normal-case text-slate-400">
              what happens in the world
            </span>
          </h3>
          <p className="px-3 py-3 text-xs leading-6 whitespace-pre-wrap text-slate-800">
            {dataset.narrative}
          </p>
        </section>

        <section data-testid="detail-intent" className="rounded border border-slate-200 bg-white">
          <h3 className="border-b border-slate-100 px-3 py-2 text-[11px] font-semibold tracking-wide text-slate-500 uppercase">
            Intent
            <span className="ml-2 font-normal tracking-normal normal-case text-slate-400">
              why this dataset exists in the suite
            </span>
          </h3>
          <p className="px-3 py-3 text-xs leading-6 whitespace-pre-wrap text-slate-800">
            {provenance.intent}
          </p>
        </section>
      </div>
    </article>
  );
}
