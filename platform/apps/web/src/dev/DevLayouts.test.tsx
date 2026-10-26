import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import DevLayouts from "./DevLayouts";

test("/dev/layouts draws the Sites list and MX-0001's case page from fixtures", async () => {
  render(<DevLayouts />);
  const sites = await screen.findByRole("table", { name: "Sites" });
  await within(sites).findByText("Indiranagar");
  expect(within(sites).getAllByRole("row")).toHaveLength(4); // the header and three sites
  const [nav] = screen.getAllByRole("button", { name: "MX-0001" });
  if (!nav) throw new Error("no MX-0001 nav item");
  await userEvent.setup().click(nav);
  expect(await screen.findByRole("heading", { level: 1, name: "Indiranagar" })).toBeDefined();
  expect(await screen.findByRole("group", { name: "Fill DD checklist" })).toBeDefined();
});
