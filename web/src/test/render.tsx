/**
 * Rendering the app under a fresh `QueryClient`, so no cache crosses a test.
 *
 * A shared client would make the second test's "one fetch" assertion pass
 * because the first test already filled the cache — a test passing while
 * proving nothing, which is the failure mode this build has caught five times.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";

export function renderWithQuery(element: ReactElement): RenderResult & { client: QueryClient } {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  const result = render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
  return { ...result, client };
}
