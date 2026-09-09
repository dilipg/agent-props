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
          className="flex gap-3 px-3 py-2 text-xs"
        >
          <code
            className={
              finding.rule === SHAPE_RULE
                ? "shrink-0 rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-900"
                : "shrink-0 rounded bg-red-100 px-1.5 py-0.5 font-semibold text-red-900"
            }
          >
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

/** Warnings on a successful response. They never block anything. */
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
          data-warning={warning.code}
          className="flex gap-3 px-3 py-2 text-xs"
        >
          <code className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-amber-900">
            {warning.code}
          </code>
          <span className="text-slate-700">{warning.message}</span>
        </li>
      ))}
    </ul>
  );
}
