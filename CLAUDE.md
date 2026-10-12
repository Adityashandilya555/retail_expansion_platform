# CLAUDE.md — retail_expansion_platform

This repo is **our fork of Operaton** plus the **Z-Matrix platform** we build on it.
- Platform work (almost everything): conventions, scope, stack, labels, Operaton map → @platform/AGENTS.md
- Changes to Operaton's own Java modules (`area:engine` issues only): also read `AGENTS.md` at the root (Operaton's contributor guide: Maven build, module layout, tests).

## Claude Code specifics
- Issues and PRs: always `--repo Adityashandilya555/retail_expansion_platform` (a fork defaults to the parent `operaton/operaton`).
- Before coding an issue, check it with `/refine-issue <n>`, then build it with `/implement-issue <n>`. For Operaton behaviour delegate to the `operaton-researcher` subagent so the 17k-file tree doesn't flood context.
- MCP servers (`.mcp.json`): `supabase`, `vercel` (OAuth via `/mcp`), `railway` (`railway login`), `sourcegraph` (needs `SRC_ACCESS_TOKEN`, and `SRC_ENDPOINT` if not `http://localhost:7080`), `rea` (reverse engineering, pinned `rea-agents@6.4.0`, needs Node 26 or 22.19+/24.11+). Rules for using them: `platform/AGENTS.md` → "Agent tools and rules". Add GitHub locally: `claude mcp add --transport http github https://api.githubcopilot.com/mcp/ -H "Authorization: Bearer $(gh auth token)"`. Optional on Adi's machine: `serena`, `matrix-configurator`.
- Commands: `/refine-issue <n> [--apply]`, `/implement-issue <n>`, `/infra-check`.
- Don't: edit Operaton outside an `area:engine` issue, hand-edit generated BPMN/forms, commit `.env`, run destructive SQL or delete cloud resources without explicit confirmation, open PRs against `operaton/operaton`.
