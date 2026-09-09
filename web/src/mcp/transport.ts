/**
 * The one place in this app that touches the network.
 *
 * Finding F-16: the web app had no specified interface to the service. This
 * module is the answer, and the answer is "the MCP tool surface, nothing
 * else". There is no REST API beside the tools, because ruling R-17 says the
 * web app writes *through the tool surface* and the M9 gate makes that
 * mechanical.
 *
 * Why same-origin, measured rather than assumed
 * ---------------------------------------------
 *
 * A browser cannot drive `mcp` 2.2.0's streamable-HTTP transport
 * cross-origin. Measured against the running service:
 *
 * - `OPTIONS /mcp` answers **405 Method Not Allowed**, `allow: GET, POST,
 *   DELETE`. There is no CORS preflight handler, and a POST carrying
 *   `content-type: application/json` plus `mcp-session-id` is not a simple
 *   request, so the preflight is mandatory.
 * - No response from `/mcp` carries `access-control-allow-origin`. The SDK
 *   wires `CORSMiddleware` only into its OAuth routes
 *   (`mcp/server/auth/routes.py`), never into the MCP endpoint, and
 *   `streamable_http_app()` takes no CORS parameter.
 * - Nothing sets `access-control-expose-headers`, so even a permitted
 *   cross-origin response would hide `mcp-session-id` — the header every
 *   request after `initialize` must echo — from JavaScript.
 *
 * So the app is served **same-origin** with the service: Vite's dev and
 * preview servers proxy `/mcp`, and a deployment serves the built assets
 * behind the same origin as the service. CORS is removed from the picture
 * rather than worked around, and the service needs no change.
 *
 * What is deliberately not implemented
 * ------------------------------------
 *
 * The client-to-server half of streamable HTTP and nothing else: `initialize`,
 * the `notifications/initialized` acknowledgement, and `tools/call`. The
 * app makes no use of the server-to-client `GET` stream because nothing here
 * receives server-initiated requests, and it never opens a second session.
 * That is why this is ~150 lines rather than a client library — and it is not
 * one: ruling R-68 descoped the TypeScript *client package*, and this is app
 * code behind one function, which the guard test can point at.
 */

import type { Envelope } from "./envelope";

/** Where the tool surface lives, relative to this app's own origin. */
export const MCP_PATH = "/mcp";

/**
 * The handshake version this app declares. The server answers with the version
 * it settled on, and every later request echoes **that** rather than this, so
 * a server that negotiates down keeps working.
 */
const CLIENT_PROTOCOL_VERSION = "2025-11-25";

const SESSION_HEADER = "mcp-session-id";
const PROTOCOL_HEADER = "mcp-protocol-version";

interface JsonRpcSuccess {
  readonly jsonrpc: "2.0";
  readonly id: number;
  readonly result: Readonly<Record<string, unknown>>;
}

interface JsonRpcFailure {
  readonly jsonrpc: "2.0";
  readonly id: number | null;
  readonly error: { readonly code: number; readonly message: string; readonly data?: unknown };
}

interface CallToolResult {
  readonly content?: readonly { readonly type: string; readonly text?: string }[];
  readonly structuredContent?: Envelope;
  readonly isError?: boolean;
}

/** A transport-level failure: HTTP, JSON-RPC, or a malformed body. */
export class TransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TransportError";
  }
}

/**
 * Parse one streamable-HTTP response body into its JSON-RPC message.
 *
 * The transport answers `text/event-stream` by default and
 * `application/json` when the service is started with `json_response=True`,
 * so both are handled: whichever it is, exactly one JSON-RPC message comes
 * back for a request. Exported for the unit test, which is the only way to
 * assert the SSE framing without a live server.
 */
export function parseRpcBody(contentType: string, body: string): unknown {
  if (contentType.includes("text/event-stream")) {
    const datas = body
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice("data:".length).trim());
    const last = datas[datas.length - 1];
    if (last === undefined) {
      throw new TransportError("event stream carried no data line");
    }
    return JSON.parse(last);
  }
  return JSON.parse(body);
}

/** Session state. One per page load; the service allocates the id. */
interface Session {
  readonly id: string;
  readonly protocolVersion: string;
}

let nextRequestId = 1;
let handshake: Promise<Session> | undefined;

function headers(session: Session | undefined): HeadersInit {
  const base: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
  };
  if (session !== undefined) {
    base[SESSION_HEADER] = session.id;
    base[PROTOCOL_HEADER] = session.protocolVersion;
  }
  return base;
}

async function post(body: unknown, session: Session | undefined): Promise<Response> {
  const response = await fetch(MCP_PATH, {
    method: "POST",
    headers: headers(session),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new TransportError(
      `POST ${MCP_PATH} answered ${String(response.status)} ${response.statusText}`,
    );
  }
  return response;
}

function resultOf(message: unknown): Readonly<Record<string, unknown>> {
  if (typeof message !== "object" || message === null) {
    throw new TransportError("response body was not a JSON-RPC message");
  }
  if ("error" in message) {
    const { error } = message as JsonRpcFailure;
    throw new TransportError(`JSON-RPC error ${String(error.code)}: ${error.message}`);
  }
  if (!("result" in message)) {
    throw new TransportError("JSON-RPC message carried neither result nor error");
  }
  return (message as JsonRpcSuccess).result;
}

async function rpc(
  method: string,
  params: Readonly<Record<string, unknown>>,
  session: Session | undefined,
): Promise<{ result: Readonly<Record<string, unknown>>; response: Response }> {
  const response = await post(
    { jsonrpc: "2.0", id: nextRequestId++, method, params },
    session,
  );
  const contentType = response.headers.get("content-type") ?? "";
  const message = parseRpcBody(contentType, await response.text());
  return { result: resultOf(message), response };
}

/**
 * Open the session, once per page load.
 *
 * Memoised on the promise rather than on the resolved value, so ten
 * components mounting at once share one `initialize` instead of racing to
 * allocate ten sessions. A failed handshake clears the memo so the next call
 * retries — one decision site, not a loop.
 */
function connect(): Promise<Session> {
  handshake ??= (async () => {
    const { result, response } = await rpc(
      "initialize",
      {
        protocolVersion: CLIENT_PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: "agent-props-web", version: "0.1.0" },
      },
      undefined,
    );
    const id = response.headers.get(SESSION_HEADER);
    if (id === null) {
      throw new TransportError(
        `initialize returned no ${SESSION_HEADER} header. If this app is not served ` +
          "same-origin with the service, the browser cannot read it — see " +
          "src/mcp/transport.ts.",
      );
    }
    const negotiated = result["protocolVersion"];
    const session: Session = {
      id,
      protocolVersion: typeof negotiated === "string" ? negotiated : CLIENT_PROTOCOL_VERSION,
    };
    // Fire-and-forget: the spec's acknowledgement, which this server does not
    // require before serving. Failing to send it must not fail the handshake.
    void post({ jsonrpc: "2.0", method: "notifications/initialized" }, session).catch(
      () => undefined,
    );
    return session;
  })().catch((error: unknown) => {
    handshake = undefined;
    throw error;
  });
  return handshake;
}

/**
 * Call one tool and return its envelope.
 *
 * The **only** function in this app that reaches the network, and the reason
 * clause 5 of the M9 gate can be checked mechanically: every mutation the app
 * can perform is a tool named at a `callTool` call site.
 *
 * `structuredContent` carries the envelope directly, which is why nothing here
 * re-parses `content[0].text` unless the server omitted it.
 */
export async function callTool(name: string, args: Readonly<Record<string, unknown>>): Promise<Envelope> {
  const session = await connect();
  const { result } = await rpc("tools/call", { name, arguments: args }, session);
  const call = result as CallToolResult;
  if (call.structuredContent !== undefined) {
    return call.structuredContent;
  }
  const text = call.content?.find((part) => part.type === "text")?.text;
  if (text === undefined) {
    throw new TransportError(`tool ${name} returned no structured content and no text`);
  }
  return JSON.parse(text) as Envelope;
}

/** Drop the memoised session. Exported for tests, which run many sessions. */
export function resetSession(): void {
  handshake = undefined;
  nextRequestId = 1;
}
