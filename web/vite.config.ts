/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * The service URL the dev and preview servers proxy `/mcp` to.
 *
 * Why a proxy at all: the browser cannot reach the MCP streamable-HTTP
 * endpoint cross-origin. `mcp` 2.2.0 applies CORS only to its OAuth routes,
 * so `OPTIONS /mcp` answers 405, no response carries
 * `access-control-allow-origin`, and nothing exposes `mcp-session-id` — the
 * header every request after `initialize` has to echo. Proxying makes the
 * whole conversation same-origin, which removes CORS from the picture rather
 * than working around it. See DECISIONS.md [M9] for the full finding (F-16).
 */
const SERVICE_URL = process.env["AGENTPROPS_SERVICE_URL"] ?? "http://127.0.0.1:8000";

const proxy = {
  "/mcp": {
    target: SERVICE_URL,
    changeOrigin: true,
    // Server-sent events: the transport answers `text/event-stream`, so the
    // proxy must not buffer whole responses.
    ws: false,
  },
} as const;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The bundle is ~1.6 MB raw / ~480 kB gzipped, and almost all of it is
  // CodeMirror (inside `vanilla-jsoneditor`) plus React Flow. Both are locked
  // stack choices for an internal review-and-edit tool served from the same
  // origin as the service, so the default 500 kB warning is noise here rather
  // than a finding. The named remedy if it ever matters: `React.lazy` around
  // `JsonEditorPane` and `BlueprintGraph`, which are the only two consumers and
  // are both mounted behind a click. Recorded rather than built - see
  // DECISIONS.md [M9].
  build: { chunkSizeWarningLimit: 1800 },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@schemas": fileURLToPath(new URL("../schemas", import.meta.url)),
    },
  },
  // `../schemas` is outside the project root, so Vite has to be told the
  // schemas the editor validates against are allowed to be served.
  server: { proxy, fs: { allow: [".", "../schemas"] } },
  preview: { proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
