# DESIGN.md — @zm/ui (stub)

Impeccable's design context for the one Z-Matrix look (D19). It stays a stub until someone runs `/impeccable document` (scan mode) on this package with the plugin at the tag in `platform/AGENTS.md` → "Design tooling". At that tag, `/impeccable init` writes product context to a PRODUCT.md and never fills this file.

- **Tokens:** `src/theme.ts` is the source (accents, presets, tone colours, `createZmTheme`) and the only file that may hold colour literals. A token block written here mirrors it and never replaces it.
- **Presets:** each client gets one preset from `PRESETS` in `src/theme.ts` (`quartz` is the default) plus a default accent. A preset changes only what `PresetOverrides` allows, never components or layouts.
- **Product rules:** `platform/docs/PRODUCT.md` → "UI" (D19, ADR-001). Impeccable finds that file as this package's PRODUCT.md (through `platform/docs/`). It may read it but must not write it, so decline any edit it proposes there. If this file and PRODUCT.md disagree, PRODUCT.md wins.
