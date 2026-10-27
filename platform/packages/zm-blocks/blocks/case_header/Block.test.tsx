import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expectNoAxeViolations } from "@zm/ui/test";
import { afterEach, expect, test, vi } from "vitest";
import { problem, sampleCase } from "../../src/test";
import { casePage, drawPage } from "../../src/test/render";

const PATH = "/app/cases/MX-0001";
const header = (props: Record<string, unknown> = { title_field: "site_name", fields: ["city"] }) =>
  casePage({ header: [{ block: "case_header", props }] });

afterEach(() => vi.restoreAllMocks());

test("MX-0001 with BD done and Legal open: the tracker says so, with a text label per stage", async () => {
  drawPage(header(), { path: PATH, routes: { "GET /app/cases/MX-0001": () => sampleCase() } });
  const tracker = await screen.findByRole("list", { name: "Site stages" });
  expect(within(tracker).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "BD qualification: done",
    "Legal: open",
    "Design: queued",
  ]);
  expect(screen.getByRole("heading", { level: 1, name: "Indiranagar" })).toBeDefined();
  expect(screen.getByText("MX-0001")).toBeDefined();
  expect(screen.getByText("Open")).toBeDefined(); // the status pill
  expect(screen.getByText("City").nextElementSibling?.textContent).toBe("Bengaluru");
});

test("without title_field the case's own title heads the page", async () => {
  drawPage(header({}), { path: PATH, routes: { "GET /app/cases/MX-0001": () => sampleCase({ title: "Indiranagar 12th Main" }) } });
  expect(await screen.findByRole("heading", { level: 1, name: "Indiranagar 12th Main" })).toBeDefined();
});

test("every case block on the page shares one GET /app/cases/{code}", async () => {
  const calls = drawPage(
    casePage({
      header: [{ block: "case_header", props: {} }],
      overview: [{ block: "case_fields", props: {} }],
      tasks: [{ block: "case_tasks", props: {} }],
    }),
    { path: PATH, routes: { "GET /app/cases/MX-0001": () => sampleCase() } },
  );
  await screen.findByRole("heading", { level: 1 });
  await screen.findByText("Site name");
  expect(calls.map((c) => `${c.method} ${new URL(c.url).pathname}`)).toEqual(["GET /app/cases/MX-0001"]);
});

test("an error shows Retry, and Retry loads the case", async () => {
  let up = false;
  drawPage(header(), { path: PATH, routes: { "GET /app/cases/MX-0001": () => (up ? sampleCase() : problem(500)) } });
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Couldn't load this site.");
  up = true;
  await userEvent.setup().click(within(alert).getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Indiranagar" })).toBeDefined();
});

test.each(["light", "dark"] as const)("axe passes in %s", async (scheme) => {
  drawPage(header(), { path: PATH, routes: { "GET /app/cases/MX-0001": () => sampleCase() }, scheme });
  await screen.findByRole("list", { name: "Site stages" });
  await expectNoAxeViolations();
});
