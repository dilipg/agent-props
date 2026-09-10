/**
 * The app shell: agents, blueprints, datasets, runs. Four screens, one selection.
 *
 * Routing is deliberately absent. Phase 1's scope is "browse and edit"
 * (PRD 10.4) and a router would be a fifth library in a locked five-library
 * stack for four screens with one selected agent between them. The selected
 * agent, blueprint, dataset and run live in this component's state; a URL
 * scheme is the obvious next addition and the shape here does not block it.
 *
 * The runs screen is not M9's. That milestone scoped the app to browsing what
 * had been *authored*; a run is what an agent did with it, and the surface for
 * reading one was the tool layer alone until an owner asked for it here.
 */

import { useEffect, useState } from "react";

import { BlueprintScreen } from "./screens/BlueprintScreen";
import { DatasetsScreen } from "./screens/DatasetsScreen";
import { RunsScreen } from "./screens/RunsScreen";
import { useAgents, useStoreStatus } from "./queries";

type Screen = "datasets" | "blueprint" | "runs";

export function App(): React.JSX.Element {
  const status = useStoreStatus();
  const agents = useAgents();
  const [agentId, setAgentId] = useState<string | undefined>(undefined);
  const [screen, setScreen] = useState<Screen>("datasets");

  // The first agent, once the list arrives. A store with one agent — the
  // authoring case — then needs no click to be useful.
  useEffect(() => {
    if (agentId === undefined && agents.data !== undefined && agents.data.length > 0) {
      setAgentId(agents.data[0]?.agent_id);
    }
  }, [agentId, agents.data]);

  return (
    <div className="flex h-screen flex-col bg-slate-100 text-slate-900">
      <header className="flex shrink-0 items-center gap-4 border-b border-slate-200 bg-white px-4 py-2">
        <h1 className="text-sm font-semibold tracking-tight">agent-props</h1>

        <label className="flex items-center gap-1.5 text-xs">
          <span className="text-slate-500">agent</span>
          <select
            data-testid="agent-select"
            value={agentId ?? ""}
            onChange={(event) => {
              setAgentId(event.target.value === "" ? undefined : event.target.value);
            }}
            className="rounded border border-slate-300 bg-white px-1.5 py-1 font-mono text-xs"
          >
            <option value="">—</option>
            {(agents.data ?? []).map((agent) => (
              <option key={agent.agent_id} value={agent.agent_id}>
                {agent.agent_id} ({agent.dataset_count})
              </option>
            ))}
          </select>
        </label>

        <nav className="flex gap-1 text-xs">
          {(["datasets", "blueprint", "runs"] as const).map((option) => (
            <button
              key={option}
              type="button"
              data-testid={`screen-${option}`}
              onClick={() => {
                setScreen(option);
              }}
              className={`rounded px-2 py-1 ${
                screen === option ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {option}
            </button>
          ))}
        </nav>

        <span className="ml-auto font-mono text-[11px] text-slate-400">
          {status.data === undefined
            ? status.error === null
              ? "connecting…"
              : `unreachable: ${status.error.message}`
            : `${status.data.backend} · ${String(status.data.counts.blueprints)} blueprints · ${String(
                status.data.counts.datasets,
              )} datasets`}
        </span>
      </header>

      <main className="min-h-0 flex-1">
        {screen === "datasets" && <DatasetsScreen agentId={agentId} />}
        {screen === "blueprint" && <BlueprintScreen agentId={agentId} />}
        {screen === "runs" && <RunsScreen agentId={agentId} />}
      </main>
    </div>
  );
}
