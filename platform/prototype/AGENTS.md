# platform/prototype/AGENTS.md: REFERENCE ONLY

Everything in this directory is a **frozen copy of `github.com/Shrey2149/outpost` at commit `ce6d141`** (2026-10-08). It is here so everyone ports from the same code. It is **not part of Z-Matrix** and nothing runs it in CI or in production.

Rules for every agent:
- **Don't edit it.** `AGENTS.md`, `CLAUDE.md` and `README.md` are ours; every other file is upstream. A bug you find here gets fixed in the port, not here.
- **Don't import, call or extend it** from platform code (`import matrix`, subprocess, loading its JSON at runtime). Port the logic into `platform/packages/` with tests: the compiler in #19 (S1-08a), deploy and groups in #23 (S1-08b), the Matrix Retail template in #18 (S1-09), `matrix.py check` in #57 (S2-16).
- **It's a demo, not the spec.** It mimics part of the Matrix-bd store-launch flow and is incomplete. `workspace.json` is a starting draft for the Matrix Retail template; the real step list is the one Adi signs off in S1-09. Where it disagrees with `platform/AGENTS.md`, `platform/docs/PRODUCT.md` or the blueprint, they win: candidate groups come from levels, not free-text roles; forms use `zm:` formKeys with JSON Schema, not `form_html`; Operaton is pinned, not `latest`.
- **Line numbers match upstream.** The only change to upstream files is a "REFERENCE ONLY" marker written into an existing line at the top of `matrix.py`, `mcp_server.py` and `start.sh`, so `matrix.py:408` here is `matrix.py:408` at `ce6d141`, as cited in the issues.
- **Don't run it in place.** `matrix.py` rewrites `workspace.json` and writes `build/` next to itself; use a scratch copy (see `README.md`).
- Sonar (`platform/sonar-project.properties`) and pytest (no `pyproject.toml` here) skip this directory. Ruff must too: `extend-exclude = ["prototype"]` in `platform/pyproject.toml` (#9).
