import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expectNoAxeViolations } from "@zm/ui/test";
import { afterEach, expect, test, vi } from "vitest";
import type { AppUi, UiPage } from "../../src";
import { problem, sampleCases, sampleUi } from "../../src/test";
import { drawPage, location } from "../../src/test/render";

/** Legal's page: the strip over a case table (`stages` scopes the table, a picked tile too). */
const legalPage = (tiles: unknown[], table: Record<string, unknown> = {}): UiPage => ({
  key: "legal",
  layout: "standard",
  department: "legal",
  regions: { hero: [{ block: "kpi_strip", props: { tiles } }], main: [{ block: "case_table", props: table }] },
});
const stripOnly = (tiles: unknown[]): UiPage => ({ key: "legal", layout: "standard", regions: { hero: [{ block: "kpi_strip", props: { tiles } }] } });
const LEGAL = [{ kind: "in_stage", stage: "legal" }, { kind: "awaiting_me" }];
const routes = { "GET /app/cases": () => sampleCases };

const tile = (name: RegExp) => screen.getByRole("button", { name });
const codes = () =>
  [...screen.getByRole("table", { name: "Sites" }).querySelectorAll("tbody tr")].map((tr) => tr.querySelector("td")?.textContent);
const shows = async (count: string, name: RegExp) => waitFor(() => expect(tile(name).textContent).toContain(count));

afterEach(() => vi.useRealTimers());

test("Legal's page: the counts match the lists, and a tile filters the table and the URL", async () => {
  const calls = drawPage(legalPage(LEGAL), { path: "/app/legal", routes });
  // MX-0001 is the one case at Legal, and the one with a task for the user (`my_tasks`: the
  // server's count of their GET /app/tasks per case)
  await shows("1", /^In Legal/);
  await shows("1", /^Awaiting me/);
  expect(codes()).toEqual(["MX-0001", "MX-0002", "MX-0003"]);
  expect(calls).toHaveLength(1); // the strip and the table share one GET /app/cases

  const user = userEvent.setup();
  await user.click(tile(/^In Legal/));
  expect(location()).toBe("/app/legal?kpi=in_stage.legal");
  expect(tile(/^In Legal/).getAttribute("aria-pressed")).toBe("true");
  expect(codes()).toEqual(["MX-0001"]);

  await user.click(tile(/^Awaiting me/));
  expect(location()).toBe("/app/legal?kpi=awaiting_me");
  expect(tile(/^In Legal/).getAttribute("aria-pressed")).toBe("false");
  expect(codes()).toEqual(["MX-0001"]);

  await user.click(tile(/^Awaiting me/)); // again: back to the whole table
  expect(location()).toBe("/app/legal");
  expect(codes()).toEqual(["MX-0001", "MX-0002", "MX-0003"]);
});

test("each kind counts its cases; a tile in the URL filters the table on arrival", async () => {
  const [, , third] = sampleCases;
  if (!third) throw new Error("the fixtures have three sites");
  const done = { ...third, code: "MX-0004", status: "completed" as const, stages: third.stages.map((s) => ({ ...s, state: "done" as const })) };
  const tiles = [{ kind: "open_cases" }, { kind: "done" }, { kind: "done", stage: "legal", label: "Through Legal" }, { kind: "in_stage", stage: "bd_qualification" }];
  drawPage(legalPage(tiles), { path: "/app/legal?kpi=done.legal", routes: { "GET /app/cases": () => [...sampleCases, done] } });
  await shows("3", /^Open sites/);
  await shows("1", /^Completed/);
  await shows("2", /^Through Legal/);
  await shows("1", /^In BD qualification/);
  expect(tile(/^Through Legal/).getAttribute("aria-pressed")).toBe("true");
  expect(codes()).toEqual(["MX-0003", "MX-0004"]); // past Legal
});

test("a picked tile keeps the table's own `stages`, and the strip counts within them", async () => {
  drawPage(legalPage([{ kind: "open_cases" }, { kind: "awaiting_me" }], { stages: ["legal"] }), { path: "/app/legal?kpi=open_cases", routes });
  await shows("1", /^Open sites/); // 3 open in the flow, 1 of them at Legal
  expect(tile(/^Open sites/).getAttribute("aria-pressed")).toBe("true");
  expect(codes()).toEqual(["MX-0001"]);
});

/** Every kind over sites in every state: one completed, one with Legal late beside a reopened BD. */
function everyKindCases() {
  const [first, , third] = sampleCases;
  if (!first || !third) throw new Error("the fixtures have three sites");
  const done = { ...third, code: "MX-0004", status: "completed" as const, stages: third.stages.map((s) => ({ ...s, state: "done" as const })) };
  const parallel = {
    ...first,
    code: "MX-0005",
    my_tasks: 0,
    stages: first.stages.map((s) => (s.key === "bd_qualification" ? { ...s, state: "open" as const, started_at: "2026-10-25T09:00:00Z", ended_at: null } : s)),
  };
  return [...sampleCases, done, parallel];
}
const legalSla5: AppUi = {
  ...sampleUi,
  flows: sampleUi.flows.map((f) => ({ ...f, stages: f.stages.map((s) => (s.key === "legal" ? { ...s, sla_days: 5 } : s)) })),
};
const KIND_GROUPS = [
  [{ kind: "open_cases" }, { kind: "awaiting_me" }, { kind: "overdue" }, { kind: "done" }],
  [{ kind: "in_stage", stage: "legal" }, { kind: "overdue", stage: "legal" }, { kind: "done", stage: "legal" }, { kind: "awaiting_me", stage: "legal" }],
];

test.each([
  [0, "an unscoped table", undefined],
  [1, "an unscoped table", undefined],
  [0, "a table scoped to Legal", ["legal"]],
  [1, "a table scoped to Legal", ["legal"]],
] as const)("tiles %i over %s: each tile's count is the rows it shows once picked", async (group, _, stages) => {
  vi.useFakeTimers({ now: new Date("2026-10-26T09:00:01Z"), toFake: ["Date"] }); // Legal runs 6 days, SLA 5
  const tiles = KIND_GROUPS[group] ?? [];
  drawPage(legalPage(tiles, stages ? { stages } : {}), { path: "/app/legal", routes: { "GET /app/cases": everyKindCases }, ui: legalSla5 });
  await waitFor(() => expect(codes().length).toBeGreaterThan(0));
  const user = userEvent.setup();
  for (const button of screen.getAllByRole("button", { pressed: false })) {
    await waitFor(() => expect(button.textContent).toMatch(/\d/));
    const count = Number((button.textContent ?? "").replace(/\D/g, "")); // labels have no digits
    await user.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    const shown = codes().filter((c) => c?.startsWith("MX-")).length; // not the empty-state row
    expect([button.textContent, shown]).toEqual([button.textContent, count]);
    await user.click(button); // clear it again
  }
});

test("a tile and the table's stage chips replace each other in the URL", async () => {
  drawPage(legalPage(LEGAL), { path: "/app/legal?stage=design", routes });
  await shows("1", /^In Legal/);
  expect(codes()).toEqual(["MX-0003"]);
  const user = userEvent.setup();
  await user.click(tile(/^In Legal/));
  expect(location()).toBe("/app/legal?kpi=in_stage.legal");
  await user.click(screen.getByRole("checkbox", { name: "BD qualification" }));
  expect(location()).toBe("/app/legal?stage=bd_qualification");
  expect(tile(/^In Legal/).getAttribute("aria-pressed")).toBe("false");
  expect(codes()).toEqual(["MX-0002"]);
});

test("sla_days 5 on Legal: a case 6 days in Legal counts as overdue", async () => {
  vi.useFakeTimers({ now: new Date("2026-10-26T09:00:01Z"), toFake: ["Date"] }); // 6 days after the fixtures' stage start
  const ui: AppUi = {
    ...sampleUi,
    flows: sampleUi.flows.map((f) => ({ ...f, stages: f.stages.map((s) => (s.key === "legal" ? { ...s, sla_days: 5 } : s)) })),
  };
  drawPage(legalPage([{ kind: "overdue" }, { kind: "overdue", stage: "design" }]), { path: "/app/legal", routes, ui });
  await shows("1", /^Overdue(?! in)/);
  expect(tile(/^Overdue in Design/).textContent).toContain("0"); // Design has no SLA
  await userEvent.setup().click(tile(/^Overdue(?! in)/));
  expect(codes()).toEqual(["MX-0001"]);
});

test("past the API's cap the tiles say at least N, and the table says it shows the newest", async () => {
  const capped = () => Response.json(sampleCases, { headers: { "X-Truncated": "true" } });
  drawPage(legalPage(LEGAL), { path: "/app/legal", routes: { "GET /app/cases": capped } });
  await shows("1+", /^In Legal/);
  expect(tile(/^Awaiting me/).textContent).toContain("1+");
  expect(screen.getByText("Showing the newest 3 sites.")).toBeTruthy();
});

test("in_stage needs its stage, and a strip has 2 to 4 tiles", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  drawPage(stripOnly([{ kind: "in_stage" }]));
  const why = screen.getByRole("alert").textContent;
  expect(why).toContain("must NOT have fewer than 2 items");
  expect(why).toContain("must have required property 'stage'");
  vi.mocked(console.error).mockRestore();
});

test("a failed load says so with Retry, and Retry loads the counts", async () => {
  let up = false;
  drawPage(stripOnly(LEGAL), { routes: { "GET /app/cases": () => (up ? sampleCases : problem(500)) } });
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Couldn't load the counts.");
  up = true;
  await userEvent.setup().click(within(alert).getByRole("button", { name: "Retry" }));
  await shows("1", /^In Legal/);
});

test.each(["light", "dark"] as const)("axe passes in %s with a tile picked", async (scheme) => {
  drawPage(legalPage(LEGAL), { path: "/app/legal?kpi=awaiting_me", routes, scheme });
  await shows("1", /^In Legal/);
  await expectNoAxeViolations();
});
