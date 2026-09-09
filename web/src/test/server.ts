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

export function ok(key: string, value: unknown, warnings: readonly ToolWarning[] = []): Envelope {
  return { ok: true, data: { [key]: value }, warnings };
}

export function failed(errors: readonly RuleError[]): Envelope {
  return { ok: false, errors };
}

export function rule(id: string, pointer: string, section: string | null = null): RuleError {
  return { rule: id, severity: "error", pointer, message: `${id} at ${pointer}`, section };
}

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
