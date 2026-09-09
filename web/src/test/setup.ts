/**
 * Test environment setup: jsdom, the DOM matchers, and the two browser APIs
 * React Flow needs that jsdom does not implement.
 *
 * `resetSession()` before each test matters more than it looks: the transport
 * memoises one MCP session per page load, and a test that reused the previous
 * test's session would pass while proving the handshake works only once.
 *
 * `ResizeObserver` and `DOMMatrixReadOnly` are stubs, not implementations, and
 * that limit is why `BlueprintGraph.test.tsx` asserts only that the nodes
 * mount. Every element measures 0×0 under jsdom, so React Flow computes no
 * edge geometry — the topology assertion belongs in the pure test over
 * `src/graph/topology.ts`, where there is nothing to measure.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";

import { resetSession } from "@/mcp/transport";

class ResizeObserverStub {
  observe(): void {
    // No layout in jsdom, so nothing to report.
  }
  unobserve(): void {
    /* no-op */
  }
  disconnect(): void {
    /* no-op */
  }
}

class DOMMatrixReadOnlyStub {
  readonly m22 = 1;
  constructor(_transform?: string) {
    // React Flow reads m22 off the computed transform; the identity is right
    // for an unzoomed viewport, which is what a 0×0 container has.
  }
}

globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
globalThis.DOMMatrixReadOnly ??= DOMMatrixReadOnlyStub as unknown as typeof DOMMatrixReadOnly;
// `getBoundingClientRect` needs no stub: jsdom implements it and returns all
// zeroes, which is the honest answer for an environment with no layout and is
// exactly why the edge-geometry assertions live in `graph/topology.test.ts`.

beforeEach(() => {
  resetSession();
});

afterEach(() => {
  cleanup();
});
