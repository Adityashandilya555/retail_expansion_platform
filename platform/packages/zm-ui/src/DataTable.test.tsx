import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { DataTable, type Column } from "./DataTable";
import { expectNoAxeViolations, renderWithZm } from "./test";

type Site = { code: string; city: string; days: number };
const rows: Site[] = [
  { code: "MX-0002", city: "Pune", days: 9 },
  { code: "MX-0001", city: "Bengaluru", days: 3 },
  { code: "MX-0003", city: "Chennai", days: 14 },
];
const columns: Column<Site>[] = [
  { key: "code", header: "Code", sortValue: (r) => r.code },
  { key: "city", header: "City" },
  { key: "days", header: "Days in stage", sortValue: (r) => r.days, align: "right", render: (r) => `${r.days}d` },
];

const codes = () =>
  within(screen.getByRole("table")).getAllByRole("row").slice(1).map((tr) => tr.querySelector("td")?.textContent);

test("renders rows in the given order with a caption", () => {
  renderWithZm(<DataTable caption="Sites" columns={columns} rows={rows} rowKey={(r) => r.code} />);
  expect(screen.getByRole("table", { name: "Sites" })).toBeDefined();
  expect(codes()).toEqual(["MX-0002", "MX-0001", "MX-0003"]);
  expect(screen.getByText("14d")).toBeDefined();
});

test("sortable headers sort ascending then descending and expose aria-sort", async () => {
  const user = userEvent.setup();
  renderWithZm(<DataTable caption="Sites" columns={columns} rows={rows} rowKey={(r) => r.code} />);
  expect(screen.queryByRole("button", { name: /City/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: /Days in stage/ }));
  expect(codes()).toEqual(["MX-0001", "MX-0002", "MX-0003"]);
  expect(screen.getByRole("columnheader", { name: /Days in stage/ }).getAttribute("aria-sort")).toBe("ascending");
  await user.click(screen.getByRole("button", { name: /Days in stage/ }));
  expect(codes()).toEqual(["MX-0003", "MX-0002", "MX-0001"]);
  expect(screen.getByRole("columnheader", { name: /Days in stage/ }).getAttribute("aria-sort")).toBe("descending");
});

test("rows open by click and by Enter", async () => {
  const user = userEvent.setup();
  const onRowClick = vi.fn();
  renderWithZm(<DataTable caption="Sites" columns={columns} rows={rows} rowKey={(r) => r.code} onRowClick={onRowClick} />);
  await user.click(screen.getByText("Pune"));
  expect(onRowClick).toHaveBeenLastCalledWith(rows[0]);
  const second = within(screen.getByRole("table")).getAllByRole("row")[2] as HTMLElement;
  second.focus();
  await user.keyboard("{Enter}");
  expect(onRowClick).toHaveBeenLastCalledWith(rows[1]);
});

test("loading, empty and error-with-retry states", async () => {
  const user = userEvent.setup();
  const onRetry = vi.fn();
  const { rerender } = renderWithZm(<DataTable caption="Sites" columns={columns} rows={[]} rowKey={(r) => r.code} loading />);
  expect(screen.getByRole("status").textContent).toContain("Loading");
  rerender(<DataTable caption="Sites" columns={columns} rows={[]} rowKey={(r) => r.code} emptyText="No sites yet" />);
  expect(screen.getByText("No sites yet")).toBeDefined();
  rerender(<DataTable caption="Sites" columns={columns} rows={[]} rowKey={(r) => r.code} error="Couldn't load sites" onRetry={onRetry} />);
  expect(screen.getByRole("alert").textContent).toContain("Couldn't load sites");
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledOnce();
});

test.each(["light", "dark"] as const)("passes axe in %s", async (scheme) => {
  renderWithZm(
    <DataTable caption="Sites" columns={columns} rows={rows} rowKey={(r) => r.code} onRowClick={() => {}} />,
    { scheme },
  );
  await expectNoAxeViolations();
});
