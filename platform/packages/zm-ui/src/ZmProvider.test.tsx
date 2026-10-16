import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { AppearanceMenu } from "./AppearanceMenu";
import { expectNoAxeViolations } from "./test";
import { ZmProvider, useAppearance, type ZmClientLook } from "./ZmProvider";

function Probe() {
  const { accent, preset } = useAppearance();
  return <output data-testid="probe">{`${preset.key}:${accent}`}</output>;
}

function renderApp(client?: ZmClientLook) {
  return render(
    <ZmProvider env="test" client={client}>
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
