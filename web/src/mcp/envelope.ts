/**
 * The **three** envelope shapes from `docs/contracts.md` section 1, and the
 * readers that discriminate between them.
 *
 * Every tool returns one of these and never raises for anything a user could
 * cause. There are three, not two, and ruling R-72 exists because this file
 * previously said two:
 *
 * | Shape | Fields | Who returns it |
 * |---|---|---|
 * | success | `{ok: true, data, warnings}` | every tool but the two validators |
 * | validate | `{ok, errors}` — **no `data`, no `warnings`** | `blueprint_validate`, `dataset_validate` |
 * | failure | `{ok: false, errors}` — **no `warnings`** | any tool, on a validation or resolution failure |
 *
 * The middle one is the trap. `blueprint_validate` and `dataset_validate`
 * return an `ErrorEnvelope` **whatever the outcome**: `{ok: true, errors: []}`
 * for a clean document, and — per ruling R-13 — `{ok: true, errors: [...]}`
 * with **warning**-severity items for a document that trips only BP-019,
 * DS-007, DS-027 or DS-032. So `ok: true` does *not* imply `data` exists, and
 * routing a validate reply through {@link payload} calls `Object.keys` on
 * `undefined`. That threw on every clean document's debounce tick, the throw
 * was caught, and the editor announced "clean" while discarding every warning
 * the catalogue had reported.
 *
 * `src/agentprops/models/errors.py::ErrorEnvelope` documented this all along.
 * The TypeScript side did not, which is the whole of R-72.
 *
 * Read `rule` and `pointer`, never `message`: `CLAUDE.md` says messages get
 * reworded, so nothing in this app branches on message text.
 */

/** A structured finding: a catalogue rule id plus where it points. */
export interface RuleError {
  /** Catalogue id (`BP-005`, `DS-026`) or an `AP-*` boundary code. */
  readonly rule: string;
  /**
   * `error` blocks a write; `warning` never does (ground rule 3). A warned
   * document is storable, so the editor renders warnings without blocking save.
   */
  readonly severity: "error" | "warning";
  /** RFC 6901 JSON pointer into the submitted document. */
  readonly pointer: string;
  readonly message: string;
  /** The skeleton section, or null outside a skeleton context. */
  readonly section: string | null;
  readonly context?: Readonly<Record<string, unknown>>;
}

/**
 * A warning attached to a successful response. Never blocks anything.
 *
 * `{code, detail}` — matching `models/errors.py::Warning`. It is **not**
 * `{code, message}`: this interface claimed a `message` and a `context` until
 * ruling R-72's fix round, and nothing failed, because nothing rendered a
 * warning. A field name no response carries is invisible until something reads
 * it, which is the same lesson in miniature.
 */
export interface ToolWarning {
  readonly code: string;
  readonly detail: Readonly<Record<string, unknown>>;
}

/** `{ok: true, data, warnings}` — every tool but the two validators. */
export interface SuccessEnvelope {
  readonly ok: true;
  readonly data: Readonly<Record<string, unknown>>;
  readonly warnings: readonly ToolWarning[];
}

/**
 * `{ok, errors}` — what `blueprint_validate` and `dataset_validate` return,
 * whatever the outcome, and what any tool returns on a failure.
 *
 * One interface for both because they are one shape on the wire: `ok`
 * distinguishes "clean, possibly with warnings" from "rejected", and a reader
 * that wants the findings wants them either way.
 */
export interface FindingsEnvelope {
  readonly ok: boolean;
  readonly errors: readonly RuleError[];
}

export type Envelope = SuccessEnvelope | FindingsEnvelope;

/**
 * Thrown when a tool answers `ok: false`.
 *
 * The findings travel on the error rather than in its message, so a caller
 * that wants to render them inline — the editor does — reads `errors` and a
 * caller that only wants to know it failed reads nothing.
 */
export class ToolError extends Error {
  readonly errors: readonly RuleError[];
  readonly tool: string;

  constructor(tool: string, errors: readonly RuleError[]) {
    const first = errors[0];
    super(
      first === undefined
        ? `${tool} failed with no findings`
        : `${tool}: ${first.rule} at ${first.pointer || "/"}`,
    );
    this.name = "ToolError";
    this.tool = tool;
    this.errors = errors;
  }
}

/** True for the shape that carries a payload under one named key. */
export function carriesData(envelope: Envelope): envelope is SuccessEnvelope {
  return "data" in envelope;
}

/** True for the shape that carries findings — validate-clean or failure. */
export function carriesFindings(envelope: Envelope): envelope is FindingsEnvelope {
  return "errors" in envelope;
}

/**
 * The value under `data`'s single named key.
 *
 * Throws {@link ToolError} on a failure envelope, so a TanStack Query hook
 * needs no `ok` check of its own. The named key is not passed in: R-43(b)
 * guarantees there is exactly one, so reading whichever one is there means the
 * app hard-codes no key name anywhere and a rename is a runtime failure that
 * names both the tool and the keys it found — rather than a silent `undefined`
 * three components away. It cannot be a *compile*-time failure, because the
 * return is an unchecked cast: the wire is untyped and this is the boundary.
 *
 * A **validate** reply reaching here is a programming error, not a user error,
 * and says so — the caller wanted {@link findingsIn}.
 */
export function payload<T>(tool: string, envelope: Envelope): T {
  if (!envelope.ok && carriesFindings(envelope)) {
    throw new ToolError(tool, envelope.errors);
  }
  if (!carriesData(envelope)) {
    throw new Error(
      `${tool} returned a findings envelope with no data; read errors with findingsIn() ` +
        "instead — contracts section 1's third shape (ruling R-72)",
    );
  }
  const keys = Object.keys(envelope.data);
  if (keys.length !== 1) {
    throw new Error(
      `${tool} returned ${String(keys.length)} keys in data (${keys.join(", ")}); ` +
        "contracts section 1 promises exactly one named key",
    );
  }
  return envelope.data[keys[0] as string] as T;
}

/**
 * The findings on a validate reply — clean, warned or rejected alike.
 *
 * This is the reader R-72 requires: `errors` is read **directly** off the
 * reply, never routed through {@link payload}, so a warned-clean document's
 * warnings arrive instead of being lost to a swallowed `TypeError`.
 *
 * A success envelope reaching here means a validate tool started wrapping its
 * report under a named key, which is a contract change and throws rather than
 * returning an empty list — an empty list is exactly what the bug looked like.
 */
export function findingsIn(tool: string, envelope: Envelope): readonly RuleError[] {
  if (carriesFindings(envelope)) {
    return envelope.errors;
  }
  throw new Error(
    `${tool} returned a data envelope; a validate tool returns {ok, errors} ` +
      "(contracts section 1, ruling R-72)",
  );
}

/** The warnings on a successful response, or an empty list for the other shapes. */
export function warningsIn(envelope: Envelope): readonly ToolWarning[] {
  return carriesData(envelope) ? envelope.warnings : [];
}

/** True when any finding blocks a write. Warnings never do — ground rule 3. */
export function blocking(findings: readonly RuleError[]): readonly RuleError[] {
  return findings.filter((finding) => finding.severity === "error");
}

/**
 * The findings on a failed call, or an empty list — for the callers that want
 * to render a rejection rather than treat it as an exception.
 */
export function findings(error: unknown): readonly RuleError[] {
  return error instanceof ToolError ? error.errors : [];
}
