# Quartz (preset notes)

Design context for the `quartz` preset (#34), read with Impeccable (#32). It describes `PRESETS.quartz` in `src/theme.ts` and binds nothing: the rules are in `platform/AGENTS.md` ("Code conventions", "Design tooling: Impeccable") and `platform/docs/PRODUCT.md` → "UI". Colours are named by their token in `theme.ts`, never copied here.

## Intent
The Z-Matrix default: a calm, cool workspace for long days of approvals, checklists and tables. The neutrals stay out of the way so the accent, the status pills and the stage tracker carry the meaning.

## Palette (tokens in `src/theme.ts`)
- **Neutrals:** Mantine's own `gray` (light scheme) and `dark` (dark scheme) palettes, unchanged: cool greys with a hint of blue.
- **Default accent:** `slate`, seeded from the Z-Matrix kit.
- **Radius:** `md` (10 px, the base in `createZmTheme`) everywhere; badges use `sm`.
- **Headings:** Plus Jakarta Sans at weight 650.
- **States:** `TONE_COLORS`, the same in every preset.

## Do
- Use it for a new client until a preset is chosen, and for clients with a cool or blue brand.
- Pair it with `slate`, `blue`, `indigo` or `teal`.

## Don't
- Expect the preset to carry a brand on its own: the brand shows through the default accent.
- Pair it with `copper` or `pink` for a warm brand; `warm` suits that better.
- Rely on dimmed text for something people must read: Mantine's `gray.6` on white is about 3.3:1 and `dark.2` on `dark.7` about 4.0:1, below AA, which Impeccable reports.
