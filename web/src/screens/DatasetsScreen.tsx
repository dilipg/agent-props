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

import { useMemo, useState } from "react";

import { DatasetDetail } from "@/components/DatasetDetail";
import { DatasetFilters } from "@/components/DatasetFilters";
import { DatasetList } from "@/components/DatasetList";
import { DocumentEditor } from "@/components/DocumentEditor";
import type { DatasetFilter } from "@/mcp/types";
import { useDataset, useDatasets, useLabelVocabulary, useSaveDataset } from "@/queries";

export function DatasetsScreen({ agentId }: { readonly agentId: string | undefined }): React.JSX.Element {
  const [filter, setFilter] = useState<DatasetFilter>({});
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [editing, setEditing] = useState(false);

  const scoped = useMemo<DatasetFilter>(
    () => ({ ...filter, ...(agentId === undefined ? {} : { agent_id: agentId }) }),
    [filter, agentId],
  );
  const datasets = useDatasets(scoped);
  const vocabulary = useLabelVocabulary(agentId);
  const detail = useDataset(selected);
  const save = useSaveDataset(agentId ?? "");
  const dimensions = useMemo(
    () => Object.keys(vocabulary.data?.label_schema.dimensions ?? {}),
    [vocabulary.data],
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
              setEditing(false);
            }}
            isLoading={datasets.isLoading}
            error={datasets.error}
          />
        </div>
        <p className="shrink-0 border-t border-slate-200 px-4 py-1.5 text-[11px] text-slate-400">
          {String((datasets.data ?? []).length)} dataset
          {(datasets.data ?? []).length === 1 ? "" : "s"} · one row per lineage at its latest
          version
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
                  loaded={detail.data}
                  onSave={(document) => {
                    save.mutate(document);
                  }}
                  saveLabel="Save as new version"
                  saveState={save.status === "idle" ? "idle" : save.status}
                  saveError={save.error}
                  savedMessage={
                    save.data === undefined
                      ? null
                      : `saved as v${String(save.data.datasets[0]?.version ?? "?")}`
                  }
                />
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
                {detail.error !== null ? (
                  <p className="text-xs text-red-700">{detail.error.message}</p>
                ) : detail.data === undefined ? (
                  <p className="text-xs text-slate-400">Loading…</p>
                ) : (
                  <DatasetDetail dataset={detail.data} dimensions={dimensions} />
                )}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
