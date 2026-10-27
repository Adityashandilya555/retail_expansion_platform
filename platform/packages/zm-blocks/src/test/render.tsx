// Draws a page as the shell does, for the blocks' tests: the real registry, an API answering from
// fixtures, and the client app's routes (`/app/cases/:code` gives the case blocks their code).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderWithZm } from "@zm/ui/test";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { BlocksApi } from "../api";
import { PageRenderer } from "../PageRenderer";
import type { UiPage } from "../registry";
import { fixtureApi, sampleUi } from "./index";

function Location() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{pathname + search}</div>;
}

export function drawPage(
  page: UiPage,
  { path = "/app", routes = {}, scheme }: { path?: string; routes?: Parameters<typeof fixtureApi>[0]; scheme?: "light" | "dark" } = {},
): Request[] {
  const { api, calls } = fixtureApi(routes);
  const drawn = <PageRenderer page={page} ui={sampleUi} />;
  renderWithZm(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BlocksApi value={api}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/app/cases/:code" element={drawn} />
            <Route path="*" element={drawn} />
          </Routes>
          <Location />
        </MemoryRouter>
      </BlocksApi>
    </QueryClientProvider>,
    { scheme },
  );
  return calls;
}

export const location = () => document.querySelector("[data-testid=location]")?.textContent;

/** The case page (`case` layout) with the given blocks in their regions. */
export function casePage(regions: UiPage["regions"]): UiPage {
  return { key: "site", layout: "case", flow: "store_launch", regions };
}
