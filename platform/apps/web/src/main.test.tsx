import { act } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  document.body.innerHTML = "";
});

test("mounts the app into #root", async () => {
  document.body.innerHTML = '<div id="root"></div>';
  await act(async () => {
    await import("./main");
  });
  expect(document.querySelector("h1")?.textContent).toBe("Z-Matrix — client app");
});

test("fails loudly without #root", async () => {
  await expect(import("./main")).rejects.toThrow("#root missing");
});
