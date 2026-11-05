import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PRESETS, presetCssVariables } from "@zm/ui";
import { expect, test } from "vitest";
import DevUi from "./DevUi";

const rootVar = (name: string) =>
  [...document.querySelectorAll("style")]
    .map((s) => s.textContent)
    .join("")
    .match(new RegExp(`${name}: ([^;]+);`))?.[1];

const matrix = Object.values(PRESETS).flatMap((preset) => ["Light", "Dark"].map((scheme) => ({ preset, scheme })));

test.each(matrix)("/dev/ui draws every component in $preset.key · $scheme (#34)", async ({ preset, scheme }) => {
  render(<DevUi />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("radio", { name: new RegExp(`^${preset.label}`) }));
  await user.click(screen.getByRole("button", { name: "Appearance" })); // one pick per render: jsdom has no layout
  await user.click(await screen.findByRole("radio", { name: scheme }));
  expect(document.documentElement.getAttribute("data-mantine-color-scheme")).toBe(scheme.toLowerCase());
  const own = presetCssVariables(preset, preset.defaultAccent, "light");
  for (const name of ["--mantine-heading-font-weight", "--mantine-radius-default"]) expect(rootVar(name)).toBe(own[name]);
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
    "Presets (the owner console's picker)",
    "Status pills",
    "Notices",
    "Stage tracker",
    "Data table",
    "Sign-in form (AuthCard frames it on /, /login and the console's /login)",
    "Numbers",
    "Forms (@zm/forms): Legal DD checklist, QSR licences, a Legal L3 approval",
    "Mantine inputs (used directly, styled by the theme)",
  ]);
  expect(screen.getByRole("table", { name: "Sites" })).toBeDefined();
  expect(screen.getByRole("list", { name: "Stages of MX-0001" })).toBeDefined();
  expect(screen.getByRole("textbox", { name: "Site name" })).toBeDefined();
});
