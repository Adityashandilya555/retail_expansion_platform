import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Inbox, MapPin } from "lucide-react";
import { beforeEach, expect, test, vi } from "vitest";
import { AppFrame, type NavItem } from "./AppFrame";
import { expectNoAxeViolations, renderWithZm } from "./test";

const nav: NavItem[] = [
  { key: "home", label: "My work", icon: Inbox, badge: 3 },
  { key: "sites", label: "Sites", icon: MapPin },
];

beforeEach(() => localStorage.clear());

function frame(onNavigate = vi.fn()) {
  return renderWithZm(
    <AppFrame clientName="Matrix Retail" nav={nav} activeKey="home" onNavigate={onNavigate} user={{ name: "Priya Rao" }}>
      <p>Page content</p>
    </AppFrame>,
  );
}

test("shows the client, the nav with counts, the active page and the content", () => {
  frame();
  expect(screen.getByText("Matrix Retail")).toBeDefined();
  const main = screen.getByRole("navigation", { name: "Main" });
  expect(main.textContent).toContain("My work");
  expect(main.textContent).toContain("3");
  expect(screen.getByRole("button", { name: /My work/ }).getAttribute("aria-current")).toBe("page");
  expect(screen.getByText("Page content")).toBeDefined();
});

test("navigates by mouse and keyboard", async () => {
  const user = userEvent.setup();
  const onNavigate = vi.fn();
  frame(onNavigate);
  await user.click(screen.getByRole("button", { name: /Sites/ }));
  expect(onNavigate).toHaveBeenLastCalledWith("sites");
  screen.getByRole("button", { name: /My work/ }).focus();
  await user.keyboard("{Enter}");
  expect(onNavigate).toHaveBeenLastCalledWith("home");
});

test("the sidebar collapses, keeps counts as an accessible label, and remembers it", async () => {
  const user = userEvent.setup();
  const first = frame();
  const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  await user.click(toggle);
  expect(screen.getByRole("button", { name: "Expand sidebar" }).getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByRole("button", { name: "My work, 3 open" })).toBeDefined();
  first.unmount();
  frame();
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeDefined();
});

test.each(["light", "dark"] as const)("passes axe in %s", async (scheme) => {
  renderWithZm(
    <AppFrame clientName="Matrix Retail" nav={nav} activeKey="home" user={{ name: "Priya Rao" }}>
      <p>Page content</p>
    </AppFrame>,
    { scheme },
  );
  await expectNoAxeViolations();
});
