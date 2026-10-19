import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { AppearanceMenu } from "./AppearanceMenu";
import { expectNoAxeViolations } from "./test";
import { APPEARANCE_USER_KEY, ZmProvider, useAppearance, type ZmClientLook } from "./ZmProvider";

function Probe() {
  const { accent, preset } = useAppearance();
  return <output data-testid="probe">{`${preset.key}:${accent}`}</output>;
}

function renderApp(client?: ZmClientLook, userId?: string) {
  return render(
    <ZmProvider env="test" client={client} userId={userId}>
      <AppearanceMenu />
      <Probe />
    </ZmProvider>,
  );
}

const scheme = () => document.documentElement.getAttribute("data-mantine-color-scheme");

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-mantine-color-scheme");
});

test("defaults to the quartz preset with the slate accent", () => {
  renderApp();
  expect(screen.getByTestId("probe").textContent).toBe("quartz:slate");
});

test("the client's default accent applies until the user picks one", () => {
  renderApp({ theme: "quartz", accent: "teal" });
  expect(screen.getByTestId("probe").textContent).toBe("quartz:teal");
});

test("the user's scheme and accent survive a reload and beat the client's default", async () => {
  const user = userEvent.setup();
  const first = renderApp({ accent: "teal" });
  await user.click(screen.getByRole("button", { name: "Appearance" }));
  await user.click(screen.getByRole("radio", { name: "Dark" }));
  await user.click(screen.getByRole("button", { name: "Accent: Copper" }));
  expect(scheme()).toBe("dark");
  expect(screen.getByTestId("probe").textContent).toBe("quartz:copper");
  expect(screen.getByRole("button", { name: "Accent: Copper" }).getAttribute("aria-pressed")).toBe("true");

  first.unmount();
  renderApp({ accent: "teal" });
  expect(screen.getByTestId("probe").textContent).toBe("quartz:copper");
  expect(scheme()).toBe("dark");
});

test("unknown preset and accent from the server fall back with a warning", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  renderApp({ theme: "neon", accent: "chartreuse" });
  expect(screen.getByTestId("probe").textContent).toBe("quartz:slate");
  expect(warn).toHaveBeenCalled();
  warn.mockRestore();
});

test("the appearance menu works from the keyboard and passes axe", async () => {
  const user = userEvent.setup();
  renderApp();
  await user.tab();
  expect(screen.getByRole("button", { name: "Appearance" })).toBe(document.activeElement);
  await user.keyboard("{Enter}");
  expect(screen.getByRole("radiogroup", { name: "Colour scheme" })).toBeDefined();
  await expectNoAxeViolations();
});

test("people sharing a browser keep their own appearance", async () => {
  const user = userEvent.setup();
  const asA = renderApp(undefined, "user-a");
  await user.click(screen.getByRole("button", { name: "Appearance" }));
  await user.click(screen.getByRole("radio", { name: "Dark" }));
  await user.click(screen.getByRole("button", { name: "Accent: Plum" }));
  expect(localStorage.getItem(APPEARANCE_USER_KEY)).toBe("user-a");
  asA.unmount();

  // B signs in on the same browser: system scheme and the default accent, not A's choices.
  const asB = renderApp(undefined, "user-b");
  expect(screen.getByTestId("probe").textContent).toBe("quartz:slate");
  expect(localStorage.getItem("zm:color-scheme:user-b")).toBeNull();
  expect(localStorage.getItem(APPEARANCE_USER_KEY)).toBe("user-b");
  asB.unmount();

  // A comes back: their choices return.
  renderApp(undefined, "user-a");
  expect(screen.getByTestId("probe").textContent).toBe("quartz:plum");
  expect(scheme()).toBe("dark");
});

test("switching user in place reloads that user's choices", async () => {
  const user = userEvent.setup();
  const view = renderApp(undefined, "user-a");
  await user.click(screen.getByRole("button", { name: "Appearance" }));
  await user.click(screen.getByRole("button", { name: "Accent: Teal" }));
  view.rerender(
    <ZmProvider env="test" userId="user-b">
      <Probe />
    </ZmProvider>,
  );
  expect(screen.getByTestId("probe").textContent).toBe("quartz:slate");
});

test("signing out clears the pointer the pre-paint script reads", () => {
  localStorage.setItem(APPEARANCE_USER_KEY, "user-a");
  renderApp();
  expect(localStorage.getItem(APPEARANCE_USER_KEY)).toBeNull();
});
