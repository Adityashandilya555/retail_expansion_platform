import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { App } from "./App";

test("renders the title", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "Z-Matrix — owner console" })).toBeDefined();
});
