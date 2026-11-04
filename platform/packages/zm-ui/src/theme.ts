// The one Z-Matrix look (D19 / ADR-001): Mantine theme, curated accents and client presets.
// This is the only file in packages/*/src allowed to hold colour literals (no-hex.test.ts).
import {
  DEFAULT_THEME,
  createTheme,
  defaultCssVariablesResolver,
  mergeMantineTheme,
  type MantineColorsTuple,
  type MantineThemeOverride,
} from "@mantine/core";

/** Meaning → palette for states that aren't the accent. Components take these through @zm/ui
 * (StatusPill, StageTracker, Notice, …); colour never carries meaning alone. */
export const TONE_COLORS = { success: "green", error: "red", warning: "orange", neutral: "gray" } as const;

/** Accents a user (or a client default) can pick. The first one is the default. */
export const ACCENTS = ["slate", "blue", "indigo", "teal", "green", "copper", "plum", "pink"] as const;
export type Accent = (typeof ACCENTS)[number];

// 10-shade palettes generated once as OKLCH ramps around a seed (shade 6 = seed) and committed as
// literals. slate, copper and plum are seeded from the Z-Matrix kit (#496580, #9A6321, #6B4789).
// Each keeps WCAG AA 4.5:1 for filled buttons and links in light and dark (theme.test.ts).
const PALETTES: Record<Accent, MantineColorsTuple> = {
  slate: ["#f2f5f9", "#e2e9f0", "#c9d6e3", "#adc0d3", "#96aec6", "#6e89a3", "#496580", "#3a546d", "#2c445a", "#1f3447"],
  blue: ["#f0f5ff", "#dce9fe", "#bed5ff", "#9bbefc", "#7faaf9", "#5383da", "#2b5cb8", "#1e4ba0", "#123b87", "#072b6f"],
  indigo: ["#f3f5fe", "#e2e6ff", "#c9d1ff", "#aeb9fa", "#97a3f7", "#6e78d5", "#4a4fb0", "#3c3f99", "#2e2f80", "#212068"],
  teal: ["#eff7f6", "#dbeceb", "#bcdcda", "#99c8c6", "#7ab8b5", "#49938f", "#0f6e6b", "#045c59", "#024947", "#013836"],
  green: ["#f0f7f2", "#dcede1", "#beddc7", "#9dcbab", "#7fbb93", "#569c70", "#2e7d4f", "#1e6b40", "#105832", "#034725"],
  copper: ["#faf4ee", "#f3e5d8", "#e9cfb6", "#dbb691", "#d0a070", "#b58149", "#9a6321", "#855311", "#714303", "#5b3501"],
  plum: ["#f7f3fb", "#ece4f5", "#dccdec", "#cab3e0", "#ba9cd6", "#9270b0", "#6b4789", "#593875", "#482960", "#371c4c"],
  pink: ["#fff1f5", "#fddfe8", "#fac2d5", "#f2a3bf", "#ea87ac", "#cf608c", "#b03a6e", "#992b5d", "#821d4c", "#6a103c"],
};

/** What a client preset may change: never components or layout, only these (D19). The neutrals
 * are Mantine's `gray` (light scheme) and `dark` (dark scheme) palettes; theme.test.ts enforces it. */
export type PresetOverrides = {
  gray?: MantineColorsTuple;
  dark?: MantineColorsTuple;
  /** The `md` radius: every component's default, and what `radius="md"` asks for. */
  radius?: string;
  headingsFontWeight?: string;
};

export type Preset = {
  key: string;
  label: string;
  description: string;
  defaultAccent: Accent;
  overrides: PresetOverrides;
};

export const DEFAULT_PRESET = "quartz";

/** Pre-built looks a client can choose in the owner console. Additive only (D14): a released key is
 * never removed or renamed. Each has its intent in packages/zm-ui/presets/<key>/DESIGN.md. */
export const PRESETS: Record<string, Preset> = {
  quartz: {
    key: "quartz",
    label: "Quartz",
    description: "Cool slate neutrals and 10 px corners: the Z-Matrix default.",
    defaultAccent: "slate",
    overrides: {},
  },
  warm: {
    key: "warm",
    label: "Warm",
    description: "Stone neutrals, a copper accent and softer 14 px corners.",
    defaultAccent: "copper",
    overrides: {
      // Mantine's gray and dark ramps re-hued to stone (OKLCH hue 70°, chroma ≤ 0.014) at the same
      // lightness per shade, so text and borders keep quartz's contrast.
      gray: ["#fbf8f6", "#f5f2ef", "#efebe6", "#e6e1db", "#d9d2cb", "#bab3ab", "#938c84", "#544e47", "#3d3833", "#28241f"],
      dark: ["#cdc8c2", "#bcb7b1", "#86817c", "#6d6863", "#45413e", "#3e3a37", "#312d2a", "#272320", "#221e1b", "#161411"],
      radius: "14px",
      headingsFontWeight: "600",
    },
  },
};

export function isAccent(value: unknown): value is Accent {
  return typeof value === "string" && (ACCENTS as readonly string[]).includes(value);
}

/** The client's preset, or the default; an unknown key (e.g. a newer release) warns and falls back. */
export function resolvePreset(key: string | null | undefined): Preset {
  const preset = key ? PRESETS[key] : undefined;
  if (preset) return preset;
  if (key) console.warn(`[zm-ui] unknown theme preset "${key}", using "${DEFAULT_PRESET}"`);
  return PRESETS[DEFAULT_PRESET] as Preset;
}

/** User choice beats the client default, which beats the preset default; unknown ids are skipped. */
export function resolveAccent(options: { user?: string | null; client?: string | null; preset: Preset }): Accent {
  if (isAccent(options.user)) return options.user;
  if (isAccent(options.client)) return options.client;
  return options.preset.defaultAccent;
}

export function createZmTheme({ preset, accent }: { preset: Preset; accent: Accent }): MantineThemeOverride {
  const { gray, dark, radius, headingsFontWeight } = preset.overrides;
  return createTheme({
    primaryColor: accent,
    primaryShade: { light: 6, dark: 8 },
    autoContrast: true,
    colors: { ...PALETTES, ...(gray && { gray }), ...(dark && { dark }) },
    fontFamily: "'Inter Variable', system-ui, sans-serif",
    fontFamilyMonospace: "'JetBrains Mono Variable', ui-monospace, monospace",
    headings: {
      fontFamily: "'Plus Jakarta Sans Variable', 'Inter Variable', system-ui, sans-serif",
      fontWeight: headingsFontWeight ?? "650",
    },
    radius: { md: radius ?? "10px" },
    defaultRadius: "md",
    cursorType: "pointer",
    components: {
      Table: { defaultProps: { verticalSpacing: "xs", highlightOnHover: true } },
      Badge: { defaultProps: { radius: "sm" } },
    },
  });
}

/** Mantine's CSS variables for a preset × accent × scheme, as ZmProvider would set them on :root.
 * Scoped to an element, they draw Mantine components in that look (PresetPicker's live previews). */
export function presetCssVariables(preset: Preset, accent: Accent, scheme: "light" | "dark"): Record<string, string> {
  const { variables, light, dark } = defaultCssVariablesResolver(mergeMantineTheme(DEFAULT_THEME, createZmTheme({ preset, accent })));
  return { ...variables, ...(scheme === "light" ? light : dark) };
}
