/**
 * The blueprint screen: the read-only graph beside the JSON.
 *
 * Its own module for the same reason as the dataset screen — a test that wants
 * the graph should not have to mount the agent picker to get it.
 */

import { useState } from "react";

import { BlueprintGraph } from "@/components/BlueprintGraph";
import { DocumentEditor } from "@/components/DocumentEditor";
import { useBlueprint, useSaveBlueprint } from "@/queries";

export function BlueprintScreen({ agentId }: { readonly agentId: string | undefined }): React.JSX.Element {
  const blueprint = useBlueprint(agentId);
  const save = useSaveBlueprint();
  const [editing, setEditing] = useState(false);

  if (agentId === undefined) {
    return <p className="px-5 py-6 text-xs text-slate-500">Select an agent.</p>;
  }
  if (blueprint.error !== null) {
    return <p className="px-5 py-6 text-xs text-red-700">{blueprint.error.message}</p>;
  }
  if (blueprint.data === undefined) {
    return <p className="px-5 py-6 text-xs text-slate-400">Loading blueprint…</p>;
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-2">
      <section className="flex min-h-0 flex-col overflow-hidden border-r border-slate-200 bg-white">
        <div className="shrink-0 border-b border-slate-200 px-4 py-2">
          <h2 className="font-mono text-xs font-semibold">
            {blueprint.data.agent_id}@{blueprint.data.version}
            <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 font-sans text-[11px] font-normal text-slate-600">
              {blueprint.data.status}
            </span>
          </h2>
          <p className="mt-0.5 text-[11px] text-slate-500">{blueprint.data.description}</p>
        </div>
        <div className="min-h-0 flex-1">
          <BlueprintGraph blueprint={blueprint.data} />
        </div>
        <p className="shrink-0 border-t border-slate-200 px-4 py-1.5 text-[11px] text-slate-400">
          {String(blueprint.data.nodes.length)} nodes · {String(blueprint.data.edges.length)} edges ·
          read-only: it visualises, it does not author
        </p>
      </section>

      <section className="flex min-h-0 flex-col overflow-hidden bg-slate-50">
        <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
          <button
            type="button"
            data-testid="toggle-edit-blueprint"
            onClick={() => {
              setEditing((value) => !value);
            }}
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
          >
            {editing ? "Hide editor" : "Edit JSON"}
          </button>
          <span className="text-[11px] text-slate-400">
            saves as a draft; publishing a version makes it immutable (BP-016)
          </span>
        </div>
        {editing ? (
          <div className="min-h-0 flex-1">
            <DocumentEditor
              kind="blueprint"
              loaded={blueprint.data}
              onSave={(document) => {
                save.mutate(document);
              }}
              saveLabel="Save blueprint"
              saveState={save.status === "idle" ? "idle" : save.status}
              saveError={save.error}
              savedMessage={save.data === undefined ? null : `saved ${save.data.status}`}
            />
          </div>
        ) : (
          <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-[11px] leading-5 text-slate-700">
            {JSON.stringify(blueprint.data, null, 2)}
          </pre>
        )}
      </section>
    </div>
  );
}
