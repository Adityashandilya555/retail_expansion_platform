import { screen, within } from "@testing-library/react";
import { expectNoAxeViolations } from "@zm/ui/test";
import { expect, test } from "vitest";
import { sampleCase } from "../../src/test";
import { casePage, drawPage } from "../../src/test/render";

const PATH = "/app/cases/MX-0001?tab=activity";
const activityPage = casePage({ activity: [{ block: "case_activity", props: {} }] });
const routes = { "GET /app/cases/MX-0001": () => sampleCase() };

const items = async () => within(await screen.findByRole("list", { name: "Activity" })).getAllByRole("listitem");
const titles = (found: HTMLElement[]) => found.map((li) => li.querySelector(".mantine-Timeline-itemTitle")?.textContent);

test("MX-0001's Activity tab lists created, each submission, BD done and Legal started, in order", async () => {
  drawPage(activityPage, { path: PATH, routes });
  const found = await items();
  expect(titles(found)).toEqual([
    "Site created",
    "BD qualification started",
    "Send to Legal submitted",
    "BD qualification done",
    "Legal started",
  ]);
  // who, as the case page shows people, and when; never a form value
  expect(found[0]?.textContent).toContain("BD Manager");
  expect(found[2]?.textContent).toContain("BD Manager");
  expect(found[4]?.querySelector("time")?.getAttribute("datetime")).toBe("2026-10-20T09:00:00Z");
});

test("a stage the close cut short reads closed", async () => {
  const closed = sampleCase({
    events: [
      { kind: "created", at: "2026-10-18T09:00:00Z", by: "u-bd2", by_name: null },
      { kind: "stage_closed", at: "2026-10-19T09:00:00Z", stage: "legal" },
    ],
  });
  drawPage(activityPage, { path: PATH, routes: { "GET /app/cases/MX-0001": () => closed } });
  expect(titles(await items())).toEqual(["Site created", "Legal closed"]);
});

test.each(["light", "dark"] as const)("axe passes in %s", async (scheme) => {
  drawPage(activityPage, { path: PATH, routes, scheme });
  await items();
  await expectNoAxeViolations();
});
