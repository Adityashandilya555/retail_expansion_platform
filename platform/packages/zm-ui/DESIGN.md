# DESIGN.md — @zm/ui (stub)

Impeccable's design context for the one Z-Matrix look (D19). Generate it with `/impeccable document` (scan mode) using the plugin pinned in the repo's `.claude/settings.json`. Each run replaces this file, so nothing here binds the code: the rules live in `platform/AGENTS.md` ("Code conventions", "Design tooling: Impeccable") and `platform/docs/PRODUCT.md` → "UI". **Never run `/impeccable init` in this repo:** it writes product context into `platform/docs/PRODUCT.md`.

Until it is generated: the tokens are in `src/theme.ts` (accents, `PRESETS`, tone colours, `createZmTheme`), the only file with colour literals; each client gets one preset (`quartz` by default, or `warm`) and a default accent. Each preset's intent, palette and do/don't are in `presets/<key>/DESIGN.md`.
