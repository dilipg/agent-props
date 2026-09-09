/**
 * The transport: SSE framing, one session per page load, and the envelope.
 */

import { afterEach, describe, expect, it } from "vitest";

import { TransportError, callTool, parseRpcBody, resetSession } from "./transport";
import { ToolError, payload } from "./envelope";
import { installServer, ok } from "@/test/server";
import type { Recorder } from "@/test/server";

let server: Recorder | undefined;

afterEach(() => {
  server?.restore();
  server = undefined;
  resetSession();
});

describe("parseRpcBody", () => {
  it("reads the data line out of an event stream", () => {
    expect(parseRpcBody("text/event-stream", 'event: message\ndata: {"a":1}\n\n')).toEqual({ a: 1 });
  });

  it("reads a plain JSON body, which is what json_response=True answers", () => {
    expect(parseRpcBody("application/json", '{"a":2}')).toEqual({ a: 2 });
  });

  it("tolerates CRLF, which is what a proxy may deliver", () => {
    expect(parseRpcBody("text/event-stream", 'event: message\r\ndata: {"a":3}\r\n\r\n')).toEqual({
      a: 3,
    });
  });

  it("refuses an event stream with no data line rather than guessing", () => {
    expect(() => parseRpcBody("text/event-stream", ": keepalive\n\n")).toThrow(TransportError);
  });
});

describe("callTool", () => {
  it("opens one session however many tools are called", async () => {
    server = installServer({
      store_status: () => ok("status", { backend: "sqlite", healthy: true }),
      agent_list: () => ok("agents", []),
    });
    await callTool("store_status", {});
    await callTool("agent_list", {});
    await callTool("store_status", {});
    expect(server.handshakes()).toBe(1);
    expect(server.toolNames()).toEqual(["store_status", "agent_list", "store_status"]);
  });

  it("opens one session for concurrent first calls rather than racing", async () => {
    // The memo is on the promise, not the resolved value. Memoising the value
    // would let ten components mounting together allocate ten sessions, and
    // each of those is server-side state.
    server = installServer({ agent_list: () => ok("agents", []) });
    await Promise.all([callTool("agent_list", {}), callTool("agent_list", {})]);
    expect(server.handshakes()).toBe(1);
  });

  it("returns the envelope from structuredContent", async () => {
    server = installServer({ agent_list: () => ok("agents", [{ agent_id: "x" }]) });
    const envelope = await callTool("agent_list", {});
    expect(envelope.ok).toBe(true);
    expect(payload("agent_list", envelope)).toEqual([{ agent_id: "x" }]);
  });

  it("turns an error envelope into a ToolError carrying the rule ids", async () => {
    server = installServer({});
    const envelope = await callTool("nonexistent_tool", {});
    expect(() => payload("nonexistent_tool", envelope)).toThrow(ToolError);
    try {
      payload("nonexistent_tool", envelope);
    } catch (error) {
      expect(error).toBeInstanceOf(ToolError);
      expect((error as ToolError).errors.map((finding) => finding.rule)).toEqual(["AP-004"]);
    }
  });

  it("refuses a success payload that does not carry exactly one named key", () => {
    // Ruling R-43(b) is a contract. A response with two keys means the server
    // changed and the app should say so rather than pick one.
    expect(() => payload("t", { ok: true, data: { a: 1, b: 2 }, warnings: [] })).toThrow(
      /exactly one named key/,
    );
  });
});
