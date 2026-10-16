// Test helpers for anything that renders @zm/ui: wrap in ZmProvider and run axe.
import { render, type RenderResult } from "@testing-library/react";
import axe from "axe-core";
import type { ReactElement, ReactNode } from "react";
import { expect } from "vitest";
import { ZmProvider, type ZmClientLook } from "../ZmProvider";

/** Renders inside ZmProvider; as a `wrapper`, so `rerender` keeps the provider. */
export function renderWithZm(
  ui: ReactElement,
  options: { scheme?: "light" | "dark"; client?: ZmClientLook } = {},
): RenderResult {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ZmProvider env="test" forceColorScheme={options.scheme} client={options.client}>
      {children}
    </ZmProvider>
  );
  return render(ui, { wrapper });
}

/** axe in jsdom; colour contrast can't be computed here, so theme.test.ts checks it numerically. */
export async function expectNoAxeViolations(root: Element = document.body) {
  const results = await axe.run(root, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  expect(
    results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
}
