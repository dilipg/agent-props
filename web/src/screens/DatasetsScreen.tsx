/**
 * The dataset screen: the review surface on the left, the detail or the editor
 * on the right.
 *
 * A module of its own rather than a closure inside `App.tsx`, so
 * `DatasetList.test.tsx` can mount it with an agent id and assert the call
 * count without also mounting the header, the agent picker and the store
 * status — three more queries whose requests would be counted alongside the
 * ones under test.
 */

import { useEffect, useMemo, useState } from "react";

import { DatasetDetail } from "@/components/DatasetDetail";
import { DatasetFilters } from "@/components/DatasetFilters";
import { DatasetList } from "@/components/DatasetList";
import { DatasetRuns } from "@/components/DatasetRuns";
import { DocumentEditor } from "@/components/DocumentEditor";
import { Warnings } from "@/components/Findings";
import type { DatasetFilter } from "@/mcp/types";
import { useDataset, useDatasets, useLabelVocabulary, useSaveDataset } from "@/queries";

/**
 * The page size, sent explicitly rather than left to the server default.
 *
 * Ruling R-42(b) ratified 50 as `dataset_find`'s default "because it suits the
 * review surface M9 builds on it". Sending it means the app *knows* the
 * boundary it is displaying, which is what lets the footer say "the page is
 * full" instead of printing a page count as though it were a total.
 */
const PAGE_SIZE = 50;

/** A dataset another screen has asked this one to open, at a given version. */
export interface DatasetFocus {
  readonly datasetId: string;
  /** The version to open. A run pins one, and that is the one worth showing. */
  readonly version: number;
}

export function DatasetsScreen({
  agentId,
  focus,
  onOpenRun,
}: {
  readonly agentId: string | undefined;
  readonly focus?: DatasetFocus | undefined;
  readonly onOpenRun?: ((runId: string) => void) | undefined;
}): React.JSX.Element {
  const [filter, setFilter] = useState<DatasetFilter>({});
  const [selected, setSelected] = useState<string | undefined>(undefined);
  /**
   * The version to request, when one was asked for.
   *
   * Undefined for a dataset opened from the list, which is what makes
   * `dataset_get` serve the lineage's latest - the meaning every other part of
   * this screen already assumes. Set only by a `focus`, and cleared the moment
   * the reviewer picks a different row, because the pin belongs to the run they
   * arrived from and not to whatever they browse to next.
   */
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [editing, setEditing] = useState(false);

  // A focus is a request from another screen, so it wins over local selection.
  useEffect(() => {
    if (focus === undefined) return;
    setSelected(focus.datasetId);
    setVersion(focus.version);
    setEditing(false);
  }, [focus]);

  const scoped = useMemo<DatasetFilter>(
    () => ({
      ...filter,
      ...(agentId === undefined ? {} : { agent_id: agentId }),
      limit: PAGE_SIZE,
    }),
    [filter, agentId],
  );
  const datasets = useDatasets(scoped);
  const vocabulary = useLabelVocabulary(agentId);
  const detail = useDataset(selected, version);
  const dataset = detail.data?.value;
  /**
   * The agent id comes from the **dataset's own blueprint reference**, not from
   * the screen's selection.
   *
   * `agentId ?? ""` sent an empty `agent_id` in the bundle whenever no agent
   * was selected, which the service rejects — and the dataset already knows
   * which agent it belongs to. A bundle whose `agent_id` disagreed with its
   * dataset's blueprint would be wrong even when the selection was set.
   */
  const save = useSaveDataset(dataset?.blueprint.agent_id ?? "");
  const dimensions = useMemo(
    () => Object.keys(vocabulary.data?.label_schema.dimensions ?? {}),
    [vocabulary.data],
  );
  const rows = datasets.data ?? [];
  /**
   * The latest version of the selected lineage, read off the list rather than
   * fetched: `dataset_find` returns one row per lineage **at its latest
   * version**, so the number is already here. Undefined when the dataset is not
   * in the current page, in which case no claim is made rather than a wrong one.
   */
  const latestVersion = useMemo(
    () => datasets.data?.find((row) => row.id === selected)?.version,
    [datasets.data, selected],
  );

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(360px,1fr)_1.4fr]">
      <section className="flex min-h-0 flex-col overflow-hidden border-r border-slate-200 bg-white">
        <DatasetFilters filter={filter} vocabulary={vocabulary.data} onChange={setFilter} />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <DatasetList
            datasets={datasets.data ?? []}
            dimensions={dimensions}
            selectedId={selected}
            onSelect={(id) => {
              setSelected(id);
              // Back to the lineage's latest: the pinned version belonged to the
              // run the reviewer arrived from, not to this row.
              setVersion(undefined);
              setEditing(false);
            }}
            isLoading={datasets.isLoading}
            error={datasets.error}
          />
        </div>
        {/*
          A count that says what it counts. The number of rows is a **page**,
          not a total: `dataset_find` takes a limit and this app has no paging,
          so printing the row count as "50 datasets" told a reviewer they had
          seen everything when they had seen the first fifty. The agent's true
          total comes from `label_vocabulary`, which is already fetched.
        */}
        <p
          data-testid="dataset-count"
          className="shrink-0 border-t border-slate-200 px-4 py-1.5 text-[11px] text-slate-400"
        >
          {String(rows.length)} shown
          {vocabulary.data === undefined
            ? ""
            : ` · ${String(vocabulary.data.dataset_count)} in this agent`}
          {rows.length >= PAGE_SIZE
            ? ` · page limit of ${String(PAGE_SIZE)} reached, refine the filter to see more`
            : ""}
          {" · one row per lineage at its latest version"}
        </p>
      </section>

      <section className="flex min-h-0 flex-col overflow-hidden bg-slate-50">
        {selected === undefined ? (
          <p className="px-5 py-6 text-xs text-slate-500">
            Select a dataset. Every row above already carries its title, author, complete label set
            and intent, so this pane is for the narrative and the JSON rather than for deciding
            which one to open.
          </p>
        ) : (
          <>
            <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
              <button
                type="button"
                data-testid="toggle-edit"
                onClick={() => {
                  setEditing((value) => !value);
                }}
                className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
              >
                {editing ? "Back to review" : "Edit JSON"}
              </button>
              <span className="text-[11px] text-slate-400">
                {editing
                  ? "an edit saves as a new version of this lineage, through dataset_import"
                  : "narrative and intent answer different questions"}
              </span>
            </div>
            {editing ? (
              <div className="min-h-0 flex-1">
                <DocumentEditor
                  kind="dataset"
                  loaded={dataset}
                  onSave={(document) => {
                    save.mutate(document);
                  }}
                  onDirty={() => {
                    // Clears `mutation.error`, so a rejection does not outlive
                    // the document it was about. Without it the finding list
                    // reports rules against text the reviewer has since fixed.
                    save.reset();
                  }}
                  saveLabel="Save as new version"
                  saveState={save.status === "idle" ? "idle" : save.status}
                  saveError={save.error}
                  saveWarnings={save.data?.warnings ?? []}
                  savedMessage={
                    save.data === undefined
                      ? null
                      : `saved as v${String(save.data.value.datasets[0]?.version ?? "?")}`
                  }
                />
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
                {detail.error !== null ? (
                  <p className="text-xs text-red-700">{detail.error.message}</p>
                ) : dataset === undefined ? (
                  <p className="text-xs text-slate-400">Loading…</p>
                ) : (
                  <>
                    {/*
                      `dataset_get` warns `dataset_archived` for a dataset
                      reached by explicit id. An archived dataset is hidden
                      from `dataset_find` and still readable, so a reviewer
                      following a link needs telling it has been withdrawn.
                    */}
                    <Warnings warnings={detail.data?.warnings ?? []} />
                    {latestVersion !== undefined && dataset.version !== latestVersion && (
                      <p
                        data-testid="dataset-version-note"
                        className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-900"
                      >
                        Viewing v{dataset.version}; the latest is v{latestVersion}. This is the
                        document a run pinned, not the current one — edits here save as a new
                        version on top of the latest.
                      </p>
                    )}
                    <DatasetDetail dataset={dataset} dimensions={dimensions} />
                    <section data-testid="dataset-runs-section" className="mt-5">
                      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                        runs against this dataset
                      </h3>
                      <div className="rounded border border-slate-200 bg-white">
                        <DatasetRuns
                          agentId={agentId}
                          datasetId={dataset.id}
                          onOpenRun={onOpenRun ?? (() => {})}
                        />
                      </div>
                    </section>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
