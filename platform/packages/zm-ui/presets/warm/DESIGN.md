# Warm (preset notes)

Design context for the `warm` preset (#34), read with Impeccable (#32). It describes `PRESETS.warm` in `src/theme.ts` and binds nothing: the rules are in `platform/AGENTS.md` ("Code conventions", "Design tooling: Impeccable") and `platform/docs/PRODUCT.md` → "UI". Colours are named by their token in `theme.ts`, never copied here.

## Intent
Softer and more hospitable than `quartz`, for clients whose brand is warm (food and QSR, coffee, hospitality, lifestyle retail) and who find cool greys clinical. Same components, layouts and density: only the neutrals, the corners and the heading weight change.

## Palette (tokens in `src/theme.ts`)
- **Neutrals:** `PRESETS.warm.overrides.gray` (light scheme) and `.dark` (dark scheme): Mantine's own ramps re-hued to a warm stone (OKLCH hue 70°, low chroma) at the same lightness per shade. Text, dimmed text and borders keep quartz's contrast in both schemes.
- **Page:** white in light and `dark.7` in dark, as in quartz; the warmth shows in borders, hovers, dimmed text and surfaces.
- **Default accent:** `copper`, seeded from the Z-Matrix kit.
- **Radius:** `PRESETS.warm.overrides.radius` (14 px) for the `md` step, so cards, inputs, buttons and the sign-in card all round together; badges keep `sm`.
- **Headings:** Plus Jakarta Sans at weight 600, a touch lighter than quartz's 650.
- **States:** `TONE_COLORS`, the same in every preset.

## Do
- Pair it with `copper` (its default), `plum`, `pink` or `green`.
- Look at a dense screen (a long sites table, the DD checklist) in both schemes before choosing it: the rounder corners show most on inputs.

## Don't
- Pair it with `blue` or `indigo` unless the brand asks for it: blue against warm greys reads muddy.
- Choose it to get another layout or component: those are the same in every preset.
- Rely on dimmed text for something people must read: like quartz's, it is below AA (`gray.6` on white about 3.3:1, `dark.2` on `dark.7` about 4.0:1), which Impeccable reports.
