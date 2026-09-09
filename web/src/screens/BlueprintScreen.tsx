/**
 * The blueprint screen: the read-only graph beside the JSON, one version at a time.
 *
 * Its own module for the same reason as the dataset screen — a test that wants
 * the graph should not have to mount the agent picker to get it.
 *
 * **Versions are selectable**, and that was a gap rather than a decision:
 * `blueprint_list` was wired at the query layer and consumed by nothing, so
 * only the latest published version of the selected agent was reachable, while
 * `agent_list`'s `versions[]` was fetched and never shown. The brief says
 * "browse agents, blueprints and datasets", and a blueprint with three versions
 * of which one is visible is not being browsed. The picker reads
 * `blueprint_list` rather than `agent_list.versions` because it carries each
 * version's `status` — a reviewer needs to know whether what they are reading
 * is a draft or an immutable published version before they edit it.
 */

import { useMemo, useState } from "react";

import { BlueprintGraph } from "@/components/BlueprintGraph";
import { DocumentEditor } from "@/components/DocumentEditor";
import { useBlueprint, useBlueprints, useSaveBlueprint } from "@/queries";

export function BlueprintScreen({
  agentId,
}: {
  readonly agentId: string | undefined;
}): React.JSX.Element {
  const [version, setVersion] = useState<string | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  const listed = useBlueprints();
  const blueprint = useBlueprint(agentId, version);
  const save = useSaveBlueprint();

  /**
   * This agent's versions, in `blueprint_list`'s order — `(agent_id, version)`
   * with version compared as semver, so `1.10.0` sorts after `1.9.0`
   * (ruling R-35). Reversed for display so the newest is first, which is what a
   * reviewer reaches for; the ordering the service guarantees is still what the
   * list is built from.
   */
  const versions = useMemo(
    () => (listed.data ?? []).filter((row) => row.agent_id === agentId).reverse(),
    [listed.data, agentId],
  );

  if (agentId === undefined) {
    return <p className="px-5 py-6 text-xs text-slate-500">Select an agent.</p>;
  }
  if (blueprint.error !== null) {
    return <p className="px-5 py-6 text-xs text-red-700">{blueprint.error.message}</p>;
  }
  if (blueprint.data === undefined) {
    return <p className="px-5 py-6 text-xs text-slate-400">Loading blueprint…</p>;
  }

  const document = blueprint.data;

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-2">
      <section className="flex min-h-0 flex-col overflow-hidden border-r border-slate-200 bg-white">
        <div className="shrink-0 border-b border-slate-200 px-4 py-2">
          <div className="flex items-baseline gap-2">
            <h2 className="font-mono text-xs font-semibold">{document.agent_id}</h2>
            <label className="flex items-center gap-1 text-[11px] text-slate-500">
              <span>version</span>
              <select
                data-testid="blueprint-version"
                value={version ?? ""}
                onChange={(event) => {
                  setVersion(event.target.value === "" ? undefined : event.target.value);
                  setEditing(false);
                }}
                className="rounded border border-slate-300 bg-white px-1.5 py-0.5 font-mono text-[11px]"
              >
                <option value="">latest published</option>
                {versions.map((row) => (
                  <option key={row.version} value={row.version}>
                    {row.version} ({row.status})
                  </option>
                ))}
              </select>
            </label>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
              {document.status}
            </span>
            <span className="font-mono text-[11px] text-slate-400">@{document.version}</span>
          </div>
          <p className="mt-0.5 text-[11px] text-slate-500">{document.description}</p>
        </div>
        <div className="min-h-0 flex-1">
          <BlueprintGraph blueprint={document} />
        </div>
        <p className="shrink-0 border-t border-slate-200 px-4 py-1.5 text-[11px] text-slate-400">
          {String(document.nodes.length)} nodes · {String(document.edges.length)} edges ·{" "}
          {String(versions.length)} version{versions.length === 1 ? "" : "s"} · read-only: it
          visualises, it does not author
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
            {document.status === "published"
              ? "this version is published and immutable (BP-016); save the edit as a new version"
              : "saves as a draft; publishing a version makes it immutable (BP-016)"}
          </span>
        </div>
        {editing ? (
          <div className="min-h-0 flex-1">
            <DocumentEditor
              kind="blueprint"
              loaded={document}
              onSave={(edited) => {
                save.mutate(edited);
              }}
              onDirty={() => {
                save.reset();
              }}
              saveLabel="Save blueprint"
              saveState={save.status === "idle" ? "idle" : save.status}
              saveError={save.error}
              saveWarnings={save.data?.warnings ?? []}
              savedMessage={
                save.data === undefined
                  ? null
                  : `saved ${save.data.value.agent_id}@${save.data.value.version} (${save.data.value.status})`
              }
            />
          </div>
        ) : (
          <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-[11px] leading-5 text-slate-700">
            {JSON.stringify(document, null, 2)}
          </pre>
        )}
      </section>
    </div>
  );
}
