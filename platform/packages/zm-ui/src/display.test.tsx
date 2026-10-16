// PageHeader, StatusPill, StageTracker and Num: rendering, interaction and axe in light and dark.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { Num, formatNum } from "./Num";
import { PageHeader } from "./PageHeader";
import { StageTracker, type Stage } from "./StageTracker";
import { StatusPill } from "./StatusPill";
import { expectNoAxeViolations, renderWithZm } from "./test";

const stages: Stage[] = [
  { key: "bd", label: "BD", state: "done" },
  { key: "legal", label: "Legal", state: "open" },
  { key: "design", label: "Design", state: "queued" },
];

test("PageHeader: heading, description, actions and a back button", async () => {
  const user = userEvent.setup();
  const onBack = vi.fn();
  renderWithZm(
    <PageHeader title="MX-0001 · Koramangala" description="Store launch" actions={<button type="button">Export</button>} onBack={onBack} backLabel="Back to sites" />,
  );
  expect(screen.getByRole("heading", { level: 1, name: "MX-0001 · Koramangala" })).toBeDefined();
  expect(screen.getByText("Store launch")).toBeDefined();
  expect(screen.getByRole("button", { name: "Export" })).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Back to sites" }));
  expect(onBack).toHaveBeenCalledOnce();
});

test("StatusPill: every tone has a text label, and children override it", () => {
  renderWithZm(
    <>
      <StatusPill tone="done" />
      <StatusPill tone="open" />
      <StatusPill tone="queued" />
      <StatusPill tone="closed" />
      <StatusPill tone="warning" />
      <StatusPill tone="open">In Legal</StatusPill>
    </>,
  );
  for (const label of ["Done", "Open", "Queued", "Closed", "Attention", "In Legal"]) {
    expect(screen.getByText(label)).toBeDefined();
  }
});

test("StageTracker: an ordered list whose states are readable as text, also in the compact variant", () => {
  renderWithZm(
    <>
      <StageTracker stages={stages} label="Stages of MX-0001" />
      <StageTracker stages={stages} label="Stages compact" compact />
    </>,
  );
  for (const name of ["Stages of MX-0001", "Stages compact"]) {
    const list = screen.getByRole("list", { name });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(["BD: done", "Legal: open", "Design: queued"]);
  }
  expect(screen.getByRole("list", { name: "Stages of MX-0001" }).querySelector("[aria-current='step']")?.textContent).toBe("Legal: open");
});

test("Num and formatNum: mono figures, Indian grouping, rupees, empty values", () => {
  expect(formatNum(1234567)).toBe("12,34,567");
  expect(formatNum(1234567.891, { decimals: 2 })).toBe("12,34,567.89");
  expect(formatNum(250000, { currency: true })).toBe("₹2,50,000");
  expect(formatNum(null)).toBe("—");
  renderWithZm(<Num value={1234567} currency />);
  const el = screen.getByText("₹12,34,567");
  expect(el.getAttribute("data-num")).toBe("");
});

test.each(["light", "dark"] as const)("PageHeader, StatusPill, StageTracker and Num pass axe in %s", async (scheme) => {
  renderWithZm(
    <>
      <PageHeader title="Sites" onBack={() => {}} actions={<button type="button">New</button>} />
      <StatusPill tone="open" />
      <StageTracker stages={stages} />
      <StageTracker stages={stages} compact label="Compact stages" />
      <Num value={42} />
    </>,
    { scheme },
  );
  await expectNoAxeViolations();
});
