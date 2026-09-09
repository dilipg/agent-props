/**
 * The entry point: a query client, an error-free root, and nothing else.
 *
 * `retry: false` is set here as well as per-hook, because the default of three
 * retries is wrong for this service in a way worth stating once: a tool that
 * answers `ok: false` has answered, and the answer will not change on a second
 * ask. The service never gates and never raises for anything a user causes, so
 * every failure this app sees is either a structured rejection (do not retry)
 * or the service being unreachable (which the reviewer needs told, not hidden
 * behind three silent attempts).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./index.css";

const client = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

const root = document.getElementById("root");
if (root === null) {
  throw new Error("index.html has no #root element");
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
