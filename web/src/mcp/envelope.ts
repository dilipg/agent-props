/**
 * The two envelopes from `docs/contracts.md` section 1, and the one function
 * that unwraps a success payload.
 *
 * Every tool returns one of these and never raises for anything a user could
 * cause. A success payload always sits under **one named key** in `data`
 * (ruling R-43(b)) — `{"blueprint": {...}}`, `{"datasets": [...]}` — which is
 * what lets {@link payload} be one function rather than a switch per tool.
 *
 * Read `rule` and `pointer`, never `message`: `CLAUDE.md` says messages get
 * reworded, so nothing in this app branches on message text.
 */

/** A structured finding: a catalogue rule id plus where it points. */
export interface RuleError {
  /** Catalogue id (`BP-005`, `DS-026`) or an `AP-*` boundary code. */
  readonly rule: string;
  readonly severity: "error" | "warning";
  /** RFC 6901 JSON pointer into the submitted document. */
  readonly pointer: string;
  readonly message: string;
  /** The skeleton section, or null outside a skeleton context. */
  readonly section: string | null;
  readonly context?: Readonly<Record<string, unknown>>;
}

/** A warning attached to a successful response. Never blocks anything. */
export interface ToolWarning {
  readonly code: string;
  readonly message: string;
  readonly context?: Readonly<Record<string, unknown>>;
}

export interface SuccessEnvelope {
  readonly ok: true;
  readonly data: Readonly<Record<string, unknown>>;
  readonly warnings: readonly ToolWarning[];
}

export interface ErrorEnvelope {
  readonly ok: false;
  readonly errors: readonly RuleError[];
}

export type Envelope = SuccessEnvelope | ErrorEnvelope;

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

export function isSuccess(envelope: Envelope): envelope is SuccessEnvelope {
  return envelope.ok;
}

/**
 * The value under `data`'s single named key.
 *
 * Throws {@link ToolError} on an error envelope, so a TanStack Query hook
 * needs no `ok` check of its own. The named key is not passed in: R-43(b)
 * guarantees there is exactly one, and reading whichever one is there means a
 * key rename shows up as a type error at the call site rather than as an
 * `undefined` three components away.
 */
export function payload<T>(tool: string, envelope: Envelope): T {
  if (!envelope.ok) {
    throw new ToolError(tool, envelope.errors);
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
 * The findings on a failed call, or an empty list — for the callers that want
 * to render a rejection rather than treat it as an exception.
 */
export function findings(error: unknown): readonly RuleError[] {
  return error instanceof ToolError ? error.errors : [];
}
