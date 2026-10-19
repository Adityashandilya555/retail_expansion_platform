// The inline script in each app's index.html sets the colour scheme before React loads (no flash).
import { beforeEach, describe, expect, test } from "vitest";
import consoleHtml from "../../console/index.html?raw";
import webHtml from "../index.html?raw";

function runInlineScript(html: string, stored: string | null, prefersDark: boolean, scope = "anon") {
  const script = /<script>([\s\S]*?)<\/script>/.exec(html)?.[1];
  if (!script) throw new Error("index.html has no inline script");
  if (stored !== null) localStorage.setItem(`zm:color-scheme:${scope}`, stored);
  window.matchMedia = ((query: string) => ({ matches: prefersDark && query.includes("dark"), media: query })) as unknown as typeof window.matchMedia;
  new Function(script)();
  return document.documentElement.getAttribute("data-mantine-color-scheme");
}

describe.each([
  ["web", webHtml],
  ["console", consoleHtml],
])("%s index.html", (_name, html) => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-mantine-color-scheme");
  });

  test.each([
    ["dark", false, "dark"],
    ["light", true, "light"],
    ["auto", true, "dark"],
    ["auto", false, "light"],
    [null, true, "dark"],
    [null, false, "light"],
    ["sepia", true, "dark"],
  ] as const)("stored %s, system dark %s → %s", (stored, prefersDark, expected) => {
    expect(runInlineScript(html, stored, prefersDark)).toBe(expected);
  });

  test("a signed-in person's scheme is used, not the anonymous one", () => {
    localStorage.setItem("zm:appearance-user", "u-1");
    localStorage.setItem("zm:color-scheme:anon", "light");
    expect(runInlineScript(html, "dark", false, "u-1")).toBe("dark");
  });

  test("after sign-out (no pointer) another person's choice is not used", () => {
    expect(runInlineScript(html, "dark", false, "u-1")).toBe("light");
  });

  test("the inline script runs before the app bundle", () => {
    expect(html.indexOf("<script>")).toBeGreaterThan(-1);
    expect(html.indexOf("<script>")).toBeLessThan(html.indexOf('<script type="module"'));
  });
});
