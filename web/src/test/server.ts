/**
 * A fake `/mcp` endpoint: a real streamable-HTTP conversation over a stubbed
 * `fetch`, and a record of every request it received.
 *
 * Why a fake transport rather than a mocked `callTool`
 * ---------------------------------------------------
 *
 * Because the thing worth asserting is the **request count and the tool names**,
 * and mocking `callTool` would assert against a stub of the very layer the
 * claims are about. Clause 3 of the M9 gate — "the list shows enough to judge
 * relevance without a detail fetch" — is a claim about how many calls the list
 * makes; {@link Recorder.calls} is that number, measured at the wire.
 *
 * It speaks the real framing: an `initialize` whose response carries an
 * `mcp-session-id` header, `text/event-stream` bodies, and `tools/call` results
 * under `structuredContent`. So the parsing in `src/mcp/transport.ts` is
 * exercised rather than bypassed.
 *
 * Ruling R-72: a harness must be able to emit every shape the surface emits
 * ------------------------------------------------------------------------
 *
 * This file previously had **one** envelope constructor, {@link ok}, which can
 * only build a success-with-`data` envelope. So every clause-1 test stubbed the
 * validate tools as `ok("report", {ok, errors})` — **a wrapper no validate tool
 * produces**. Six tests agreed with each other about a shape the service never
 * sends, and the bug they were meant to catch was in the reader for the shape
 * they never constructed.
 *
 * So there are now three constructors, one per documented shape, named after
 * the shapes in `docs/contracts.md` section 1:
 *
 * | Constructor | Shape | Who returns it |
 * |---|---|---|
 * | {@link ok} | `{ok: true, data, warnings}` | every tool but the two validators |
 * | {@link validated} | `{ok, errors}` | `blueprint_validate`, `dataset_validate` |
 * | {@link failed} | `{ok: false, errors}` | any tool, on a rejection |
 *
 * {@link ENVELOPE_SHAPES} pins each to a structural predicate, and
 * `server.test.ts` asserts that every shape has a constructor, that each
 * constructor produces exactly one shape, and — the assertion that makes it
 * more than bookkeeping — that
 * `tests/unit/test_web_envelope_shapes.py` observes the same three shapes
 * coming out of the **real** tool surface.
 */

import { vi } from "vitest";

import type { Envelope, RuleError, ToolWarning } from "@/mcp/envelope";

export interface RecordedCall {
  readonly tool: string;
  readonly args: Readonly<Record<string, unknown>>;
}

export interface Recorder {
  /** Every `tools/call` this server received, in order. */
  readonly calls: RecordedCall[];
  /** How many `initialize` requests were made. One per page load is correct. */
  readonly handshakes: () => number;
  /** Tool names called, in order. */
  readonly toolNames: () => string[];
  readonly restore: () => void;
}

/** A tool handler: arguments in, envelope out. */
export type Handler = (args: Readonly<Record<string, unknown>>) => Envelope;

// ------------------------------------------------------- the three shapes

/** `{ok: true, data: {<key>: value}, warnings}`. Every tool but the validators. */
export function ok(key: string, value: unknown, warnings: readonly ToolWarning[] = []): Envelope {
  return { ok: true, data: { [key]: value }, warnings };
}

/**
 * `{ok, errors}` — what a validate tool returns, whatever the outcome.
 *
 * `ok` is **computed** from the findings rather than passed in: per ruling
 * R-13 a validate reply is `ok: true` exactly when nothing is error-severity,
 * so a test cannot construct the incoherent `{ok: true, errors: [an error]}` or
 * `{ok: false, errors: [only warnings]}`. The harness having been able to
 * construct an impossible reply is how R-72's bug survived; it should not be
 * able to construct a *contradictory* one either.
 *
 * Note there is no `warnings` key. The service does not send one on this shape
 * — warning-severity items travel in `errors`, which is the whole subtlety.
 */
export function validated(findings: readonly RuleError[] = []): Envelope {
  return { ok: !findings.some((finding) => finding.severity === "error"), errors: findings };
}

/** `{ok: false, errors}` — a rejection from any tool. */
export function failed(errors: readonly RuleError[]): Envelope {
  return { ok: false, errors };
}

/** An error-severity finding. */
export function rule(id: string, pointer: string, section: string | null = null): RuleError {
  return { rule: id, severity: "error", pointer, message: `${id} at ${pointer}`, section };
}

/**
 * A **warning**-severity finding — DS-027, BP-019, DS-007, DS-032.
 *
 * Its own constructor rather than a `severity` parameter on {@link rule},
 * because the severity is the thing the tests are about and a default argument
 * is how it went untested: every existing case used `rule()` and therefore
 * error severity, so the warning path had no caller.
 */
export function warns(id: string, pointer: string, section: string | null = null): RuleError {
  return { rule: id, severity: "warning", pointer, message: `${id} at ${pointer}`, section };
}

/** A `{code, detail}` warning, as `models/errors.py::Warning` defines it. */
export function warning(code: string, detail: Record<string, unknown> = {}): ToolWarning {
  return { code, detail };
}

/**
 * Each documented shape, with the structural predicate that identifies it and
 * the constructor that builds it.
 *
 * The predicates are written against the *wire* object, deliberately not
 * against the TypeScript types — types vanish at runtime, and the bug R-72
 * names was a runtime shape mismatch that the types had blessed.
 */
export const ENVELOPE_SHAPES = {
  "success-with-data": {
    matches: (envelope: Envelope): boolean =>
      envelope.ok === true && "data" in envelope && "warnings" in envelope,
    build: (): Envelope => ok("thing", { a: 1 }),
  },
  "validate-clean": {
    matches: (envelope: Envelope): boolean =>
      envelope.ok === true && !("data" in envelope) && "errors" in envelope,
    build: (): Envelope => validated([warns("DS-027", "/provenance/intent", "provenance")]),
  },
  "failure-with-errors": {
    matches: (envelope: Envelope): boolean =>
      envelope.ok === false && !("data" in envelope) && "errors" in envelope,
    build: (): Envelope => failed([rule("DS-026", "/provenance/intent", "provenance")]),
  },
} as const;

export type EnvelopeShape = keyof typeof ENVELOPE_SHAPES;

/** Which documented shape an envelope is, or `null` for none of them. */
export function shapeOf(envelope: Envelope): EnvelopeShape | null {
  const matched = (Object.keys(ENVELOPE_SHAPES) as EnvelopeShape[]).filter((name) =>
    ENVELOPE_SHAPES[name].matches(envelope),
  );
  return matched.length === 1 ? (matched[0] as EnvelopeShape) : null;
}

// ----------------------------------------------------------- the transport

const SESSION_ID = "test-session-0001";

/**
 * Install the fake server. Returns a recorder; call `restore()` when done.
 *
 * An unhandled tool answers `AP-004` rather than throwing, because that is what
 * the real surface does for a name it cannot resolve — a test that reached an
 * unexpected tool should see the app's own error path, not a stack trace from
 * the harness.
 */
export function installServer(handlers: Readonly<Record<string, Handler>>): Recorder {
  const calls: RecordedCall[] = [];
  let handshakes = 0;
  const original = globalThis.fetch;

  const stub = vi.fn((input: unknown, init?: RequestInit): Promise<Response> => {
    const path = String(input);
    if (path !== "/mcp") {
      throw new Error(`unexpected request to ${path}; the app must only reach /mcp`);
    }
    const raw = typeof init?.body === "string" ? init.body : "{}";
    const body = JSON.parse(raw) as {
      id?: number;
      method?: string;
      params?: { name?: string; arguments?: Record<string, unknown> };
    };

    if (body.method === "initialize") {
      handshakes += 1;
      return Promise.resolve(
        sse(body.id ?? 0, { protocolVersion: "2025-11-25" }, { "mcp-session-id": SESSION_ID }),
      );
    }
    if (body.method === "notifications/initialized") {
      return Promise.resolve(new Response(null, { status: 202 }));
    }
    if (body.method === "tools/call") {
      const tool = body.params?.name ?? "";
      const args = body.params?.arguments ?? {};
      calls.push({ tool, args });
      const handler = handlers[tool];
      const envelope: Envelope =
        handler === undefined ? failed([rule("AP-004", "", null)]) : handler(args);
      return Promise.resolve(sse(body.id ?? 0, { structuredContent: envelope, isError: false }));
    }
    throw new Error(`unexpected JSON-RPC method ${String(body.method)}`);
  });

  globalThis.fetch = stub;

  return {
    calls,
    handshakes: () => handshakes,
    toolNames: () => calls.map((call) => call.tool),
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

function sse(
  id: number,
  result: Readonly<Record<string, unknown>>,
  extra: Readonly<Record<string, string>> = {},
): Response {
  const payload = JSON.stringify({ jsonrpc: "2.0", id, result });
  return new Response(`event: message\ndata: ${payload}\n\n`, {
    status: 200,
    headers: { "content-type": "text/event-stream", ...extra },
  });
}
