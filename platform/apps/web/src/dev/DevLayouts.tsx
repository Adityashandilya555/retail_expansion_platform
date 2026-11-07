// /dev/layouts (dev server only, never in production builds): each layout with each block it takes
// (#37), drawn by the real blocks from the Sprint 1 demo's fixtures: the Sites list (`standard`) and
// MX-0001's case page (`case`). No API: requests are answered from fixtures, and submitting the DD
// checklist moves MX-0001 on (its tasks, history and activity), as the API would.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BlocksApi, PageRenderer, type UiPage } from "@zm/blocks";
import { fixtureApi, sampleCase, sampleCases, sampleTask, sampleUi } from "@zm/blocks/test";
import { AppFrame, PageHeader, ZmProvider } from "@zm/ui";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router";

const sites: UiPage = {
  key: "sites",
  layout: "standard",
  regions: {
    hero: [{ block: "kpi_strip", props: { tiles: [{ kind: "open_cases" }, { kind: "in_stage", stage: "legal" }, { kind: "awaiting_me" }] } }],
    main: [{ block: "case_table", props: { flow: "store_launch", columns: ["code", "site_name", "city", "stage", "status", "age", "open_tasks"] } }],
    aside: [{ block: "task_inbox", props: { scope: "mine" } }],
  },
};
const site: UiPage = {
  key: "site",
  layout: "case",
  flow: "store_launch",
  regions: {
    header: [{ block: "case_header", props: { title_field: "site_name", fields: ["city"] } }],
    overview: [{ block: "case_fields", props: {} }],
    tasks: [{ block: "case_tasks", props: {} }],
    activity: [{ block: "case_activity", props: {} }],
  },
};
const VIEWS = { sites: "/app/sites", site: "/app/cases/MX-0001?tab=tasks&task=t-dd" };

/** The fixture API; a submitted DD checklist leaves MX-0001's tasks and joins its history. */
function demoApi() {
  let mx1 = sampleCase();
  const others = Object.fromEntries(
    sampleCases.slice(1).map((c) => [`GET /app/cases/${c.code}`, () => sampleCase({ ...c, tasks: [], history: [] })]),
  );
  return fixtureApi({
    "GET /app/cases": () => sampleCases,
    "GET /app/tasks": () => (mx1.tasks.length > 0 ? [sampleTask.task] : []),
    "GET /app/cases/MX-0001": () => mx1,
    ...others,
    "GET /app/tasks/t-dd": () => sampleTask,
    "POST /app/tasks/t-dd/submit": () => {
      const at = new Date().toISOString();
      const done = { task: "legal_dd_checklist", task_name: "Fill DD checklist", by: "u-l1", by_name: "Legal Executive", outcome: null, comment: null, at };
      const sent = { kind: "submitted" as const, stage: "legal", task: done.task, task_name: done.task_name, by: done.by, by_name: done.by_name, at };
      mx1 = { ...mx1, tasks: [], open_tasks: 0, my_tasks: 0, history: [...mx1.history, done], events: [...mx1.events, sent] };
      return { submission_id: "s1", status: "committed", case_id: "c-1", release_version: 1 };
    },
  }).api;
}

export default function DevLayouts() {
  const [view, setView] = useState<keyof typeof VIEWS>("sites");
  const [api] = useState(demoApi);
  const [queries] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  return (
    <ZmProvider client={sampleUi.client}>
      <QueryClientProvider client={queries}>
        <BlocksApi value={api}>
          <AppFrame
            clientName="Matrix Retail (preview)"
            nav={[
              { key: "sites", label: "Sites" },
              { key: "site", label: "MX-0001" },
            ]}
            activeKey={view}
            onNavigate={(key) => setView(key === "site" ? "site" : "sites")}
            user={{ name: "Legal Executive", email: "legal.l1@matrix-retail.demo" }}
          >
            <PageHeader
              title="/dev/layouts"
              description="Each layout with each block it takes: the Sites list with its counts above and My work beside it, and MX-0001's case page, drawn by the real blocks from fixtures (no API). Switch scheme and accent in the top bar."
            />
            <MemoryRouter key={view} initialEntries={[VIEWS[view]]}>
              <Routes>
                <Route path="/app/cases/:code" element={<PageRenderer page={site} ui={sampleUi} />} />
                <Route path="*" element={<PageRenderer page={sites} ui={sampleUi} />} />
              </Routes>
            </MemoryRouter>
          </AppFrame>
        </BlocksApi>
      </QueryClientProvider>
    </ZmProvider>
  );
}
