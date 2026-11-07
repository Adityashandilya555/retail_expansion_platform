import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import DevLayouts from "./DevLayouts";

test("/dev/layouts draws each layout with each block it takes, from fixtures", async () => {
  render(<DevLayouts />);
  // standard: kpi_strip in hero, case_table in main, task_inbox in aside
  expect(await screen.findByRole("button", { name: /^In Legal/ })).toBeDefined();
  const sites = await screen.findByRole("table", { name: "Sites" });
  await within(sites).findByText("Indiranagar");
  expect(within(sites).getAllByRole("row")).toHaveLength(4); // the header and three sites
  expect(screen.getByRole("heading", { name: "My work" })).toBeDefined();
  await within(await screen.findByRole("table", { name: /MX-0001/ })).findByText("Fill DD checklist");
  // case: case_header, then case_fields, case_tasks and case_activity tabs
  const [nav] = screen.getAllByRole("button", { name: "MX-0001" });
  if (!nav) throw new Error("no MX-0001 nav item");
  await userEvent.setup().click(nav);
  expect(await screen.findByRole("heading", { level: 1, name: "Indiranagar" })).toBeDefined();
  expect(await screen.findByRole("group", { name: "Fill DD checklist" })).toBeDefined();
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Tasks", "Activity"]);
});
