import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createApiClient, type components } from "@zm/api-client";
import { expectNoAxeViolations, renderWithZm } from "@zm/ui/test";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { BlocksApi, PageRenderer, type UiPage } from "../../src";
import { sampleUi } from "../../src/test";

type TaskItem = components["schemas"]["TaskItem"];

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const problem = (status: number) =>
  json({ type: "about:blank", title: "x", status, detail: "x", request_id: "r", errors: null }, status);

const task = (over: Partial<TaskItem> = {}): TaskItem => ({
  id: "t1",
  name: "DD checklist",
  flow: "store_launch",
  case_code: "MX-0001",
  stage: "legal",
  created: "2026-10-20T09:00:00Z",
  assignee: null,
  form_key: "zm:dd_checklist",
  ...over,
});

/** Stubs `GET /app/tasks`; returns the query strings it was called with. */
function tasksApi(answer: () => Response | Promise<Response>): string[] {
  const queries: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (`${request.method} ${url.pathname}` !== "GET /app/tasks") return problem(404);
      queries.push(url.searchParams.toString());
      return answer();
    }),
  );
  return queries;
}

function Location() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{pathname + search}</output>;
}

/** Home with one task_inbox, drawn by the real registry, as the shell draws it. */
function draw(props: Record<string, unknown>, { scheme, api = true }: { scheme?: "light" | "dark"; api?: boolean } = {}) {
  const page: UiPage = { key: "home", layout: "standard", regions: { main: [{ block: "task_inbox", props }] } };
  const tree = (
    <MemoryRouter initialEntries={["/app"]}>
      <PageRenderer page={page} ui={sampleUi} />
      <Location />
    </MemoryRouter>
  );
  return renderWithZm(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {api ? <BlocksApi value={createApiClient("http://api.test")}>{tree}</BlocksApi> : tree}
    </QueryClientProvider>,
    { scheme },
  );
}

const cells = (table: HTMLElement) =>
  [...table.querySelectorAll("tbody tr")].map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent));
const headers = (table: HTMLElement) => within(table).getAllByRole("columnheader").map((h) => h.textContent);

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("Legal L1's Home lists MX-0001 › DD checklist under MX-0001, and a click opens it on the case page", async () => {
  const queries = tasksApi(() => json([task()]));
  draw({ scope: "mine" });
  expect(await screen.findByRole("heading", { name: "My work" })).toBeDefined();
  const site = await screen.findByRole("table", { name: /MX-0001/ });
  // the group header: the code and the stage pill, by name from the release
  expect(site.querySelector("caption")?.textContent).toBe("MX-0001Legal");
  expect(headers(site)).toEqual(["Task", "Stage", "Created"]);
  await userEvent.setup().click(within(site).getByText("DD checklist"));
  expect(screen.getByTestId("location").textContent).toBe("/app/cases/MX-0001?tab=tasks&task=t1");
  expect(queries).toEqual(["scope=mine"]);
});

test("Legal L2's team queue shows the new approval task under its site", async () => {
  const queries = tasksApi(() =>
    json([task({ assignee: "legal-l1" }), task({ id: "t2", name: "Legal approval", case_code: "MX-0002" })]),
  );
  draw({ scope: "team" });
  expect(await screen.findByRole("heading", { name: "Team queue" })).toBeDefined();
  const site = await screen.findByRole("table", { name: /MX-0002/ });
  expect(cells(site).map((row) => row[0])).toEqual(["Legal approval"]);
  expect(cells(screen.getByRole("table", { name: /MX-0001/ })).map((row) => row[0])).toEqual(["DD checklist"]);
  expect(queries).toEqual(["scope=team"]);
});

test('props {"scope": "everyone"} fail the manifest: the placeholder shows and the API is never called', () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const queries = tasksApi(() => json([]));
  draw({ scope: "everyone" });
  expect(screen.getByRole("alert").textContent).toContain("task_inbox: props/scope must be equal to one of the allowed values");
  expect(queries).toEqual([]);
});

test("a 500 shows an inline error with Retry, and Retry loads the tasks", async () => {
  let up = false;
  tasksApi(() => (up ? json([task()]) : problem(500)));
  draw({});
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Couldn't load your tasks.");
  up = true;
  await userEvent.setup().click(within(alert).getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("table", { name: /MX-0001/ })).toBeDefined();
});

test("while loading it says so; with no tasks it says Nothing waiting for you", async () => {
  let land: (r: Response) => void = () => {};
  tasksApi(() => new Promise<Response>((resolve) => (land = resolve)));
  draw({});
  expect(within(await screen.findByRole("table", { name: "My work" })).getByRole("status").textContent).toBe("Loading…");
  land(json([]));
  expect(await screen.findByText("Nothing waiting for you")).toBeDefined();
});

test("group_by none is one table with the columns asked for, and flow filters the query", async () => {
  const queries = tasksApi(() =>
    json([task(), task({ id: "t2", name: "Legal approval", case_code: "MX-0002", assignee: "legal-l2" })]),
  );
  draw({ group_by: "none", columns: ["case", "task", "assignee"], flow: "store_launch" });
  const table = await screen.findByRole("table", { name: "My work" });
  await within(table).findByText("Legal approval");
  expect(headers(table)).toEqual(["Case", "Task", "Assignee"]);
  expect(cells(table)).toEqual([
    ["MX-0001", "DD checklist", "Unassigned"],
    ["MX-0002", "Legal approval", "Assigned"],
  ]);
  expect(queries).toEqual(["scope=mine&flow=store_launch"]);
});

test("without the shell's API the block becomes a placeholder; the rest of the page keeps working", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  tasksApi(() => json([]));
  draw({}, { api: false });
  expect(screen.getByRole("alert").textContent).toContain("task_inbox:");
});

test.each(["light", "dark"] as const)("axe passes in %s", async (scheme) => {
  tasksApi(() => json([task(), task({ id: "t2", name: "Legal approval", case_code: "MX-0002", stage: null })]));
  draw({}, { scheme });
  await screen.findByRole("table", { name: /MX-0002/ });
  await expectNoAxeViolations();
});
