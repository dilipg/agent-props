/**
 * A finding list, rendered inline. One component for both halves of validation.
 *
 * Every finding — Ajv's shape errors and the catalogue's rule errors — arrives
 * as a `RuleError` with a rule id and an RFC 6901 pointer, so there is one
 * renderer rather than two. The rule id is the loud part because that is what
 * `CLAUDE.md` says to read; the message is prose that will be reworded.
 */

import type { RuleError, ToolWarning } from "@/mcp/envelope";
import { SHAPE_RULE } from "@/lib/validation";

export function Findings({
  findings,
  emptyLabel,
}: {
  readonly findings: readonly RuleError[];
  readonly emptyLabel?: string;
}): React.JSX.Element | null {
  if (findings.length === 0) {
    return emptyLabel === undefined ? null : (
      <p data-testid="findings-clean" className="px-3 py-2 text-xs text-emerald-700">
        {emptyLabel}
      </p>
    );
  }
  return (
    <ul data-testid="findings" className="divide-y divide-red-100">
      {findings.map((finding, index) => (
        <li
          key={`${finding.rule}:${finding.pointer}:${String(index)}`}
          data-testid="finding"
          data-rule={finding.rule}
          data-pointer={finding.pointer}
          data-severity={finding.severity}
          className="flex gap-3 px-3 py-2 text-xs"
        >
          <code className={`shrink-0 rounded px-1.5 py-0.5 font-semibold ${chip(finding)}`}>
            {finding.rule}
          </code>
          <span className="min-w-0 flex-1">
            <span className="text-slate-700">{finding.message}</span>
            <span className="ml-2 font-mono text-[11px] text-slate-400">
              {finding.pointer || "/"}
              {finding.section === null ? "" : ` · ${finding.section}`}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Three chip colours, because severity is the thing a reviewer must not
 * misread: a `warning` is savable and an `error` is not.
 *
 * `SHAPE` is distinguished from a catalogue id as well, so a reviewer can tell
 * "this does not match the emitted schema" from "a rule fired" at a glance.
 */
function chip(finding: RuleError): string {
  if (finding.severity === "warning") {
    return "bg-amber-100 text-amber-900";
  }
  return finding.rule === SHAPE_RULE ? "bg-orange-100 text-orange-900" : "bg-red-100 text-red-900";
}

/**
 * Warnings on a successful response. They never block anything.
 *
 * The shape is `{code, detail}`, matching `models/errors.py::Warning`. It is
 * not `{code, message}`: this component read a `message` that no response
 * carries until the R-72 fix round, and nothing failed — because nothing
 * imported it. Ground rule 3 makes warnings the only channel for a policy
 * problem, so an unreachable renderer for them was the mechanism going
 * unrendered, not a spare part.
 *
 * `detail` is rendered as JSON rather than prose because it has no fixed keys —
 * `dataset_archived` carries `{dataset_id, version}`, and a future code will
 * carry something else.
 */
export function Warnings({
  warnings,
}: {
  readonly warnings: readonly ToolWarning[];
}): React.JSX.Element | null {
  if (warnings.length === 0) {
    return null;
  }
  return (
    <ul data-testid="warnings" className="divide-y divide-amber-100">
      {warnings.map((warning, index) => (
        <li
          key={`${warning.code}:${String(index)}`}
          data-testid="warning"
          data-warning={warning.code}
          className="flex gap-3 px-3 py-2 text-xs"
        >
          <code className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-amber-900">
            {warning.code}
          </code>
          <span className="font-mono text-[11px] text-slate-500">
            {JSON.stringify(warning.detail)}
          </span>
        </li>
      ))}
    </ul>
  );
}
