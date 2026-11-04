// #20 and #34 acceptance: every extra renders in light and dark for every preset × accent.
// Contrast is checked numerically in theme.test.ts (jsdom can't compute it); this checks that each
// extra renders with the preset, accent and scheme applied, keeps its text, and passes axe.
import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { AccentSwatches } from "./AppearanceMenu";
import { AppFrame } from "./AppFrame";
import { DataTable } from "./DataTable";
import { Num } from "./Num";
import { PageHeader } from "./PageHeader";
import { PresetPicker } from "./PresetPicker";
import { sampleColumns, sampleNav, sampleSites, sampleStages } from "./samples";
import { StageTracker } from "./StageTracker";
import { StatusPill, type StatusTone } from "./StatusPill";
import { expectNoAxeViolations, renderWithZm } from "./test";
import { ACCENTS, PRESETS } from "./theme";
import { useAppearance } from "./ZmProvider";

const TONES: StatusTone[] = ["done", "open", "queued", "closed", "warning"];

function Probe() {
  const { accent, preset } = useAppearance();
  return <output data-testid="probe">{`${preset.key}:${accent}`}</output>;
}

function Extras() {
  return (
    <AppFrame clientName="Matrix Retail" nav={sampleNav} activeKey="sites" user={{ name: "Priya Rao" }}>
      <PageHeader title="Sites" description="Store launch" onBack={() => {}} backLabel="Back" />
      <Probe />
      {TONES.map((tone) => (
        <StatusPill key={tone} tone={tone} />
      ))}
      <StageTracker stages={sampleStages} label="Stages" />
      <StageTracker stages={sampleStages} compact label="Compact stages" />
      <DataTable caption="Sites" columns={sampleColumns} rows={sampleSites} rowKey={(s) => s.code} onRowClick={() => {}} />
      <Num value={4250000} currency />
      <PresetPicker label="Theme preset" value="warm" onChange={() => {}} />
      <AccentSwatches value="teal" onChange={() => {}} />
    </AppFrame>
  );
}

describe.each(Object.keys(PRESETS).flatMap((preset) => ACCENTS.map((accent) => [preset, accent])))("%s · %s", (preset, accent) => {
  test.each(["light", "dark"] as const)("every extra renders in %s and passes axe", async (scheme) => {
    renderWithZm(<Extras />, { scheme, client: { theme: preset, accent } });
    expect(screen.getByTestId("probe").textContent).toBe(`${preset}:${accent}`);
    expect(document.documentElement.getAttribute("data-mantine-color-scheme")).toBe(scheme);
    expect(screen.getByText("Matrix Retail")).toBeDefined();
    expect(screen.getByRole("heading", { level: 1, name: "Sites" })).toBeDefined();
    for (const label of ["Done", "Open", "Queued", "Closed", "Attention"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByRole("list", { name: "Stages" })).toBeDefined();
    expect(screen.getByRole("list", { name: "Compact stages" })).toBeDefined();
    const table = screen.getByRole("table", { name: "Sites" });
    expect(within(table).getByText("Koramangala 5th Block")).toBeDefined();
    expect(screen.getAllByText("₹42,50,000").length).toBe(2); // the Num sample and the table row
    const presets = within(screen.getByRole("radiogroup", { name: "Theme preset" }));
    expect(presets.getAllByRole("radio").map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true"]);
    expect(screen.getByRole("button", { name: "Accent: Teal" }).getAttribute("aria-pressed")).toBe("true");
    await expectNoAxeViolations();
  });
});
