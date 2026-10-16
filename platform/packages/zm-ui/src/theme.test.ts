import { DEFAULT_THEME, defaultCssVariablesResolver, mergeMantineTheme } from "@mantine/core";
import { describe, expect, test, vi } from "vitest";
import { ACCENTS, PRESETS, type Accent, createZmTheme, isAccent, resolveAccent, resolvePreset } from "./theme";

type Scheme = "light" | "dark";

/** Mantine's own CSS variables for a preset × accent × scheme, with var() references resolved. */
function cssVariables(presetKey: string, accent: Accent, scheme: Scheme) {
  const theme = mergeMantineTheme(DEFAULT_THEME, createZmTheme({ preset: resolvePreset(presetKey), accent }));
  const { variables, light, dark } = defaultCssVariablesResolver(theme);
  const vars: Record<string, string> = { ...variables, ...(scheme === "light" ? light : dark) };
  const resolve = (value: string, depth = 0): string => {
    const ref = /^var\((--[\w-]+)\)$/.exec(value.trim());
    if (!ref) return value.trim();
    const next = vars[ref[1] as string];
    if (next === undefined) throw new Error(`unknown CSS variable ${ref[1]}`);
    if (depth > 10) throw new Error(`var() loop at ${value}`);
    return resolve(next, depth + 1);
  };
  return (name: string) => {
    const value = vars[name];
    if (value === undefined) throw new Error(`missing CSS variable ${name}`);
    return resolve(value);
  };
}

function relativeLuminance(colour: string): number {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(colour);
  if (!m) throw new Error(`expected a hex colour, got ${colour}`);
  const hex = (m[1] as string).length === 3 ? [...(m[1] as string)].map((c) => c + c).join("") : (m[1] as string);
  const [r, g, b] = [0, 2, 4].map((i) => {
    const s = parseInt(hex.slice(i, i + 2), 16) / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const matrix = Object.keys(PRESETS).flatMap((preset) =>
  ACCENTS.flatMap((accent) => (["light", "dark"] as const).map((scheme) => ({ preset, accent, scheme }))),
);

describe("contrast (WCAG AA 4.5:1) for every preset × accent × scheme", () => {
  test.each(matrix)("$preset · $accent · $scheme: filled button text", ({ preset, accent, scheme }) => {
    const v = cssVariables(preset, accent, scheme);
    expect(contrast(v("--mantine-primary-color-contrast"), v("--mantine-primary-color-filled"))).toBeGreaterThanOrEqual(4.5);
  });

  test.each(matrix)("$preset · $accent · $scheme: link on page", ({ preset, accent, scheme }) => {
    const v = cssVariables(preset, accent, scheme);
    expect(contrast(v("--mantine-color-anchor"), v("--mantine-color-body"))).toBeGreaterThanOrEqual(4.5);
  });
});

describe("palettes", () => {
  test.each([...ACCENTS])("%s has 10 shades in the theme", (accent) => {
    const theme = createZmTheme({ preset: resolvePreset("quartz"), accent });
    expect(theme.colors?.[accent]).toHaveLength(10);
    expect(theme.primaryColor).toBe(accent);
  });

  test("the slate accent is the default and quartz is a registered preset", () => {
    expect(ACCENTS[0]).toBe("slate");
    expect(PRESETS.quartz?.defaultAccent).toBe("slate");
  });
});

describe("resolution order", () => {
  const quartz = resolvePreset("quartz");

  test("user beats client beats preset default", () => {
    expect(resolveAccent({ user: "copper", client: "teal", preset: quartz })).toBe("copper");
    expect(resolveAccent({ user: null, client: "teal", preset: quartz })).toBe("teal");
    expect(resolveAccent({ preset: quartz })).toBe("slate");
  });

  test("unknown accents are ignored", () => {
    expect(isAccent("teal")).toBe(true);
    expect(isAccent("neon")).toBe(false);
    expect(resolveAccent({ user: "neon", client: "chartreuse", preset: quartz })).toBe("slate");
  });

  test("an unknown preset falls back to quartz with a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(resolvePreset("neon").key).toBe("quartz");
    expect(warn).toHaveBeenCalledOnce();
    expect(resolvePreset(undefined).key).toBe("quartz");
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });
});
