import { screen } from "@testing-library/react";
import { expectNoAxeViolations } from "@zm/ui/test";
import { afterEach, expect, test, vi } from "vitest";
import { sampleCase } from "../../src/test";
import { casePage, drawPage } from "../../src/test/render";

const PATH = "/app/cases/MX-0001";
const overview = (props: Record<string, unknown> = {}) => casePage({ overview: [{ block: "case_fields", props }] });

// Legal L1's view: the API already left out the start form's section hidden below Legal L2.
const withDocuments = () =>
  sampleCase({
    sections: [
      ...sampleCase().sections,
      {
        key: "documents",
        title: "Documents",
        fields: [
          { key: "loi_file", label: "Signed LOI", type: "file", value: { name: "loi-mx-0001.pdf" } },
          { key: "dd_title_doc", label: "Title document clear", type: "yes_no", value: true },
          { key: "frontage_ft", label: "Frontage (ft)", type: "number", value: null },
        ],
      },
    ],
  });

const value = (label: string) => screen.getByText(label).nextElementSibling?.textContent;

afterEach(() => vi.restoreAllMocks());

test("Legal L1 sees the sections the API sent, read-only; the one hidden below L2 isn't there", async () => {
  drawPage(overview(), { path: PATH, routes: { "GET /app/cases/MX-0001": withDocuments } });
  await screen.findByText("Site name");
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Site", "Documents"]);
  expect(screen.queryByText("Partner review")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull(); // values, not inputs
  expect([value("Site name"), value("City")]).toEqual(["Indiranagar", "Bengaluru"]);
  expect([value("Signed LOI"), value("Title document clear"), value("Frontage (ft)")]).toEqual(["loi-mx-0001.pdf", "Yes", "—"]);
});

test("`sections` keeps only those sections; no values says so", async () => {
  drawPage(overview({ sections: ["documents"] }), { path: PATH, routes: { "GET /app/cases/MX-0001": withDocuments } });
  await screen.findByText("Signed LOI");
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Documents"]);
});

test("a case without start values says so", async () => {
  drawPage(overview(), { path: PATH, routes: { "GET /app/cases/MX-0001": () => sampleCase({ sections: [] }) } });
  expect(await screen.findByText("No details yet")).toBeDefined();
});

test.each(["light", "dark"] as const)("axe passes in %s", async (scheme) => {
  drawPage(overview(), { path: PATH, routes: { "GET /app/cases/MX-0001": withDocuments }, scheme });
  await screen.findByText("Signed LOI");
  await expectNoAxeViolations();
});
