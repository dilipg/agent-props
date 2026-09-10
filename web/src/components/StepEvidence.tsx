/**
 * One step's evidence: the fixture that went in, the output that came back, and
 * what the dataset author expected.
 *
 * Both run views open onto this component — the graph opens it by clicking a
 * node, the sequence view stacks one per step — so the three payloads are
 * described in exactly one place and the two options cannot drift into
 * disagreeing about what `served` means.
 *
 * Why there is no pass/fail badge
 * -------------------------------
 *
 * The bundle carries `comparison`, the mode the dataset declared, and under
 * `subset` an `actual` that differs from `expected` is *correct* as long as it
 * contains it. A red "differs" marker would therefore be wrong on exactly the
 * runs it looked most useful on. Grading is the client's three pure functions
 * in `agentprops_client.compare`, run in the caller's test — `run_evidence`
 * hands out the inputs and computes no verdict, and neither does this.
 *
 * What it does state are facts off the bundle: whether the agent ever reported
 * this step (`recorded`), and whether the author injected a fault here.
 */

import { useState } from "react";

import type { EvidenceNode } from "@/mcp/types";

/** The three payloads, in the order data actually moves through a step. */
const PANELS = [
  {
    key: "served" as const,
    label: "fixture served",
    hint: "the fake data agent-props handed the agent",
    tone: "border-sky-200 bg-sky-50/60",
  },
  {
    key: "actual" as const,
    label: "output recorded",
    hint: "what the agent reported for this step",
    tone: "border-emerald-200 bg-emerald-50/60",
  },
  {
    key: "expected" as const,
    label: "expected",
    hint: "what the dataset author said this step produces",
    tone: "border-amber-200 bg-amber-50/60",
  },
];

function Json({ value }: { readonly value: unknown }): React.JSX.Element {
  if (value === null || value === undefined) {
    return <p className="px-2 py-1.5 text-[11px] italic text-slate-400">none</p>;
  }
  return (
    <pre className="max-h-72 overflow-auto px-2 py-1.5 font-mono text-[11px] leading-relaxed text-slate-700">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function StepEvidence({
  node,
  defaultOpen = true,
}: {
  readonly node: EvidenceNode;
  readonly defaultOpen?: boolean;
}): React.JSX.Element {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <article
      data-testid="step-evidence"
      data-node-id={node.node_id}
      data-iteration={node.iteration}
      className="rounded border border-slate-200 bg-white"
    >
      <button
        type="button"
        data-testid="step-evidence-header"
        onClick={() => {
          setOpen((was) => !was);
        }}
        className="flex w-full items-baseline gap-2 px-2.5 py-2 text-left hover:bg-slate-50"
      >
        <span className="font-mono text-[11px] text-slate-400">{node.seq}</span>
        <span className="font-mono text-[12px] font-semibold text-slate-900">{node.node_id}</span>
        {node.iteration > 0 && (
          <span
            data-testid="step-iteration"
            className="rounded bg-rose-100 px-1 text-[10px] text-rose-800"
          >
            iteration {node.iteration}
          </span>
        )}
        <span className="text-[10px] text-slate-500">{node.kind}</span>
        {node.tool_name !== null && (
          <span className="font-mono text-[10px] text-slate-400">{node.tool_name}</span>
        )}
        {!node.recorded && (
          <span
            data-testid="step-not-recorded"
            className="rounded bg-slate-200 px-1 text-[10px] text-slate-700"
          >
            fetched, never recorded
          </span>
        )}
        {node.fault !== null && (
          <span
            data-testid="step-fault"
            className="rounded bg-orange-100 px-1 text-[10px] text-orange-800"
          >
            fault injected
          </span>
        )}
        <span className="ml-auto text-[10px] text-slate-400">{open ? "collapse" : "expand"}</span>
      </button>

      {open && (
        <div
          data-testid="step-panels"
          className="grid gap-2 border-t border-slate-100 p-2 lg:grid-cols-3"
        >
          {PANELS.map((panel) => (
            <section
              key={panel.key}
              data-testid={`step-${panel.key}`}
              className={`min-w-0 rounded border ${panel.tone}`}
            >
              <header className="border-b border-white/70 px-2 py-1">
                <h4 className="text-[11px] font-semibold text-slate-800">{panel.label}</h4>
                <p className="text-[10px] text-slate-500">{panel.hint}</p>
              </header>
              <Json value={node[panel.key]} />
            </section>
          ))}
          {node.fault !== null && (
            <section
              data-testid="step-fault-detail"
              className="min-w-0 rounded border border-orange-200 bg-orange-50/60 lg:col-span-3"
            >
              <header className="border-b border-white/70 px-2 py-1">
                <h4 className="text-[11px] font-semibold text-slate-800">fault</h4>
                <p className="text-[10px] text-slate-500">
                  the failure the dataset author wrote into this step on purpose
                </p>
              </header>
              <Json value={node.fault} />
            </section>
          )}
        </div>
      )}
    </article>
  );
}
