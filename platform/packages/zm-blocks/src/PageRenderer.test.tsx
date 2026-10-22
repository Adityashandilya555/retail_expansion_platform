import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expectNoAxeViolations, renderWithZm } from "@zm/ui/test";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { PageRenderer, defaultCasePage, layouts, type UiPage } from "./index";
import { sampleUi, testBlocks } from "./test";

const pageOf = (key: string) => sampleUi.pages.find((p) => p.key === key) as UiPage;

function Search() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function draw(page: UiPage, { path = "/", scheme }: { path?: string; scheme?: "light" | "dark" } = {}) {
  return renderWithZm(
    <MemoryRouter initialEntries={[path]}>
      <PageRenderer page={page} ui={sampleUi} registry={testBlocks} />
      <Search />
    </MemoryRouter>,
    { scheme },
  );
}

const blockNames = () => screen.queryAllByRole("region").map((r) => r.getAttribute("aria-label"));

afterEach(() => vi.restoreAllMocks());

test("a standard page draws its blocks in the release's order", async () => {
  draw(pageOf("legal"));
  expect(blockNames()).toEqual(["task_inbox", "case_table"]);
  expect(screen.getByRole("region", { name: "task_inbox" }).textContent).toContain('"scope":"team"');
  await expectNoAxeViolations();
});

test("the same page with another block order draws the new order", () => {
  const legal = pageOf("legal");
  draw({ ...legal, regions: { main: [...(legal.regions.main ?? [])].reverse() } });
  expect(blockNames()).toEqual(["case_table", "task_inbox"]);
});

test("an unknown block or invalid props show a placeholder; the rest of the page works", () => {
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  draw({
    key: "legal",
    layout: "standard",
    regions: {
      main: [
        { block: "no_such_block", props: {} },
        { block: "task_inbox", props: { scope: "everyone" } },
        { block: "case_table", props: {} },
      ],
    },
  });
  expect(blockNames()).toEqual(["case_table"]);
  const placeholders = screen.getAllByRole("alert");
  expect(placeholders.map((p) => p.textContent)).toEqual([
    expect.stringContaining("no_such_block: unknown block"),
    expect.stringContaining("task_inbox: props/scope must be equal to one of the allowed values"),
  ]);
  expect(error.mock.calls.map((c) => String(c[0]))).toEqual(
    expect.arrayContaining([expect.stringContaining("no_such_block"), expect.stringContaining("props/scope")]),
  );
});

test("a block outside its regions, past a region's max, or crashing becomes a placeholder", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const crashing = {
    ...testBlocks,
    boom: {
      1: {
        manifest: { name: "boom", version: 1, description: "", regions: ["aside"], props: { type: "object" } },
        Block: () => {
          throw new Error("kaput");
        },
      },
    },
  };
  renderWithZm(
    <MemoryRouter>
      <PageRenderer
        ui={sampleUi}
        registry={crashing}
        page={{
          key: "p",
          layout: "standard",
          regions: {
            hero: [
              { block: "kpi_tile", props: {} },
              { block: "kpi_tile", props: {} },
            ],
            main: [{ block: "case_header", props: {} }, { block: "case_table", props: {} }],
            aside: [{ block: "boom", props: {} }],
          },
        }}
      />
    </MemoryRouter>,
  );
  expect(blockNames()).toEqual(["kpi_tile", "case_table"]);
  expect(screen.getAllByRole("alert").map((p) => p.textContent)).toEqual([
    expect.stringContaining('region "hero" takes at most 1'),
    expect.stringContaining('case_header: not allowed in region "main"'),
    expect.stringContaining("boom: kaput"),
  ]);
});

test("an unknown layout is a placeholder", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  draw({ key: "p", layout: "dashboard", regions: {} });
  expect(screen.getByRole("alert").textContent).toContain('unknown layout "dashboard"');
});

test("standard: main takes 12 columns, or 8 with a 4-column aside when the aside has content", () => {
  const spans = () => [...document.querySelectorAll("[data-region]")].map((c) => `${c.getAttribute("data-region")}:${c.getAttribute("data-span")}`);
  const legal = pageOf("legal");
  const { unmount } = draw(legal);
  expect(spans()).toEqual(["main:12"]);
  unmount();
  draw({ ...legal, regions: { ...legal.regions, aside: [{ block: "task_inbox", props: {} }] } });
  expect(spans()).toEqual(["main:8", "aside:4"]);
});

test.each(["light", "dark"] as const)("case (%s): header, then one tab per non-empty region; ?tab= picks the tab", async (scheme) => {
  draw(pageOf("site"), { path: "/app/cases/MX-0001?tab=tasks", scheme });
  expect(screen.getByRole("region", { name: "case_header" })).toBeDefined();
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Tasks"]);
  expect(screen.getByRole("tab", { name: "Tasks" }).getAttribute("aria-selected")).toBe("true");
  expect(within(screen.getByRole("tabpanel")).getByRole("region", { name: "case_tasks" })).toBeDefined();
  expect(screen.queryByRole("region", { name: "case_fields" })).toBeNull(); // hidden tabs aren't mounted
  await expectNoAxeViolations();
});

test("case: without ?tab= the first tab opens, and choosing a tab puts it in the URL", async () => {
  draw(pageOf("site"), { path: "/app/cases/MX-0001?task=t1" });
  expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");
  await userEvent.setup().click(screen.getByRole("tab", { name: "Tasks" }));
  expect(screen.getByTestId("search").textContent).toBe("?task=t1&tab=tasks");
  expect(screen.getByRole("region", { name: "case_tasks" })).toBeDefined();
});

test("the built-in case page: case_header, then case_fields and case_tasks tabs", () => {
  draw(defaultCasePage("store_launch"), { path: "/app/cases/MX-0001?tab=overview" });
  expect(blockNames()).toEqual(["case_header", "case_fields"]);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Tasks"]);
});

test("layout manifests: names match their keys, standard has hero (max 1), main, aside; case needs a flow", () => {
  for (const [key, { manifest }] of Object.entries(layouts)) expect(manifest.name).toBe(key);
  expect(layouts.standard?.manifest.regions.map((r) => [r.key, r.max])).toEqual([
    ["hero", 1],
    ["main", undefined],
    ["aside", undefined],
  ]);
  expect(layouts.case?.manifest.requires).toEqual(["flow"]);
  expect(layouts.case?.manifest.regions.map((r) => r.key)).toEqual(["header", "overview", "tasks", "activity", "documents"]);
});
