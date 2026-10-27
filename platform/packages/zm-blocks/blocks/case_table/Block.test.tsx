import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expectNoAxeViolations } from "@zm/ui/test";
import { afterEach, expect, test, vi } from "vitest";
import type { UiPage } from "../../src";
import { problem, sampleCases } from "../../src/test";
import { drawPage, location } from "../../src/test/render";

const sitesPage = (props: Record<string, unknown>): UiPage => ({
  key: "sites",
  layout: "standard",
  regions: { main: [{ block: "case_table", props }] },
});
const SITES = { flow: "store_launch", columns: ["code", "site_name", "city", "stage", "status"] };
const routes = { "GET /app/cases": () => sampleCases };

const cells = (table: HTMLElement) =>
  [...table.querySelectorAll("tbody tr")].map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent));
const codes = (table: HTMLElement) => cells(table).map((row) => row[0]);
const headers = (table: HTMLElement) => within(table).getAllByRole("columnheader").map((h) => h.textContent);

afterEach(() => vi.restoreAllMocks());

test("Sites lists the 3 demo sites: code, site name, city, current stage and status", async () => {
  const calls = drawPage(sitesPage(SITES), { path: "/app/sites", routes });
  const table = await screen.findByRole("table", { name: "Sites" });
  await within(table).findByText("Indiranagar");
  expect(headers(table)).toEqual(["Code", "Site name", "City", "Stage", "Status"]);
  // the stage cell is the current stage's name, then a mini tracker (its labels are for screen readers)
  expect(cells(table)).toEqual([
    ["MX-0001", "Indiranagar", "Bengaluru", expect.stringMatching(/^Legal/), "Open"],
    ["MX-0002", "Koramangala", "Bengaluru", expect.stringMatching(/^BD qualification/), "Open"],
    ["MX-0003", "Bandra West", "Mumbai", expect.stringMatching(/^Design/), "Open"],
  ]);
  expect(new URL(calls[0]?.url ?? "").search).toBe("?flow=store_launch");
});

test("filtering by stage updates the URL and the rows", async () => {
  drawPage(sitesPage(SITES), { path: "/app/sites", routes });
  const table = await screen.findByRole("table", { name: "Sites" });
  await within(table).findByText("Indiranagar");
  const user = userEvent.setup();
  await user.click(screen.getByRole("checkbox", { name: "Legal" }));
  expect(location()).toBe("/app/sites?stage=legal");
  expect(codes(table)).toEqual(["MX-0001"]);
  await user.click(screen.getByRole("checkbox", { name: "Design" }));
  expect(location()).toBe("/app/sites?stage=legal&stage=design");
  expect(codes(table)).toEqual(["MX-0001", "MX-0003"]);
});

test("a stage filter in the URL applies on arrival; search narrows by code or name", async () => {
  drawPage(sitesPage(SITES), { path: "/app/sites?stage=bd_qualification&stage=design", routes });
  const table = await screen.findByRole("table", { name: "Sites" });
  await within(table).findByText("Koramangala");
  expect(codes(table)).toEqual(["MX-0002", "MX-0003"]);
  await userEvent.setup().type(screen.getByRole("searchbox", { name: "Search sites" }), "bandra");
  expect(codes(table)).toEqual(["MX-0003"]);
});

test("a row opens the case page", async () => {
  drawPage(sitesPage(SITES), { path: "/app/sites", routes });
  const table = await screen.findByRole("table", { name: "Sites" });
  await userEvent.setup().click(await within(table).findByText("Bandra West"));
  expect(location()).toBe("/app/cases/MX-0003");
});

test("on a department page `stages` keeps the cases in those stages; defaults fill flow and columns", async () => {
  drawPage(sitesPage({ stages: ["legal"] }), { path: "/app/legal", routes });
  const table = await screen.findByRole("table", { name: "Sites" });
  await within(table).findByText("MX-0001");
  expect(headers(table)).toEqual(["Code", "Site", "Stage", "Status", "Open tasks"]);
  expect(codes(table)).toEqual(["MX-0001"]);
  expect(screen.queryByRole("checkbox")).toBeNull(); // one stage: nothing to filter
});

test("days in stage, my tasks and a closed case", async () => {
  vi.useFakeTimers({ now: new Date("2026-10-25T09:00:00Z"), toFake: ["Date"] });
  const [first, , third] = sampleCases;
  if (!first || !third) throw new Error("the fixtures have three sites");
  drawPage(sitesPage({ columns: ["code", "age", "my_tasks", "status"] }), {
    routes: { "GET /app/cases": () => [first, { ...third, status: "closed" }] },
  });
  const table = await screen.findByRole("table", { name: "Sites" });
  await within(table).findByText("MX-0001");
  expect(headers(table)).toEqual(["Code", "Days in stage", "My tasks", "Status"]);
  expect(cells(table)).toEqual([
    ["MX-0001", "5", "1", "Open"],
    ["MX-0003", "5", "0", "Closed"],
  ]);
  vi.useRealTimers();
});

test("no cases says so; an error shows Retry, and Retry loads them", async () => {
  let up = false;
  drawPage(sitesPage(SITES), { routes: { "GET /app/cases": () => (up ? [] : problem(500)) } });
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Couldn't load the sites.");
  up = true;
  await userEvent.setup().click(within(alert).getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No sites yet")).toBeDefined();
});

test.each(["light", "dark"] as const)("axe passes in %s", async (scheme) => {
  drawPage(sitesPage(SITES), { path: "/app/sites", routes, scheme });
  await within(await screen.findByRole("table", { name: "Sites" })).findByText("Indiranagar");
  await expectNoAxeViolations();
});
