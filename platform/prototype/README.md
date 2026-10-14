# platform/prototype: outpost prototype (reference only)

Frozen copy of [`Shrey2149/outpost`](https://github.com/Shrey2149/outpost) at commit [`ce6d141`](https://github.com/Shrey2149/outpost/commit/ce6d1415b6a65caf01f6b078a1bb0dd2515e7f23) (2026-10-08), imported by #8 so everyone ports from the same code.

**Frozen reference: don't edit; port into `platform/packages/`.** Rules for agents: [AGENTS.md](AGENTS.md).

| File | What it is | Ported by |
|---|---|---|
| `matrix.py` | validator, BPMN/form compiler, deploy, identity sync, configurator ops, `check` | #19 (compile), #23 (deploy, groups), #57 (`check`) |
| `workspace.json` | demo Matrix BD store-launch config | #18 (Matrix Retail template, as a draft) |
| `catalogue.json` | module presets that `reset` copies into `workspace.json` | — |
| `mcp_server.py` | the `matrix-configurator` MCP over the same ops | — (agent tools are rebuilt in `apps/api`) |
| `start.sh` | local Postgres + Operaton in Docker | — |
| `docs/` | `PLATFORM.md`, `HOW-OPERATON-WORKS.md`, `prompt-to-process.html` | — |

Not copied: `build/`, `__pycache__/`, `.serena/`, `.mcp.json`, `.gitignore`, `workflows/`.

## Run it
`matrix.py` rewrites `workspace.json` and writes `build/` next to itself, so run a scratch copy, never this directory:
```sh
cp -R platform/prototype /tmp/outpost-ref && cd /tmp/outpost-ref
./start.sh      # Postgres + Operaton on :8080
python3 matrix.py reset && python3 matrix.py publish && python3 matrix.py check
```
Python 3 stdlib only. Reads `OPERATON_URL` and `OPERATON_ADMIN_PASSWORD` (`platform/.env.example`).

`start.sh` is unchanged and pulls `operaton/operaton:latest`. For platform work use the pinned Operaton (2.1.5, plan decision D1).
