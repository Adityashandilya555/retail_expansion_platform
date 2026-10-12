# platform/AGENTS.md — Z-Matrix platform on our Operaton fork

Read by every coding agent working on Z-Matrix. Three collaborators work here, each with Claude; this file is the single source of truth for platform conventions. For work on Operaton itself (Java modules at the repo root) also read the root `AGENTS.md` (Operaton's own contributor guide).

## Repository
- **This repo:** `Adityashandilya555/retail_expansion_platform`, a GitHub **fork of `operaton/operaton`** (Apache 2.0). Issues, PRs and milestones live here.
- **Our code lives only under `platform/`.** Everything else at the root is Operaton, kept identical to upstream unless an `area:engine` issue explicitly changes it.
- Remotes: `origin` = our fork, `upstream` = `operaton/operaton`. Sync upstream on a branch `chore/sync-upstream-<date>` with `git fetch upstream && git merge upstream/main`; never rebase `main`. Procedure and the list of fork deltas: `platform/docs/FORK.md`.
- **PRs and issues always open on our fork.** GitHub defaults a fork's PRs to the parent repo. Always target our fork: `gh repo set-default Adityashandilya555/retail_expansion_platform` once, and pass `--repo Adityashandilya555/retail_expansion_platform` to `gh pr create` / `gh issue create`. A PR opened against `operaton/operaton` by mistake must be closed immediately.
- Reference copies for mapping: a pristine upstream checkout (`../operaton` in the Retailexpansion workspace, indexed in Sourcegraph as `github.com/operaton/operaton`) and the prototype `github.com/Shrey2149/outpost`.

## What we are building
**Z-Matrix**: a multi-client platform where each client's store-launch flow runs on **Operaton**, looks like Z-Matrix, and is configured by the client's admin through an agent.
Blueprint (with diagrams): https://claude.ai/artifact/5wzWQ8tFq6bTyjENpmLJe3. Text copy for agents: `platform/docs/BLUEPRINT.md`. `§01–§18` and `S1-xx`/`S2-xx` refer to it.

| Part | What | Runs on |
|---|---|---|
| Engine | Operaton (`operaton/operaton` image pinned to the release this fork is based on, or an image built from this fork's `distro/run` when an engine change is needed), REST only, webapps off | Railway (private network) |
| Platform API + worker | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, httpx | Railway |
| Z-Matrix shell | React 18, TypeScript, Vite, React Router, TanStack Query, Z-Matrix tokens as CSS vars (no Tailwind), Lucide | Vercel |
| Admin agent | Anthropic Python SDK, `claude-opus-5-5`, strict tool schemas, edits the draft only | inside the API |
| Data + files | Supabase **platform project** (`platform` schema: registry, requests, admins, templates) + **one project per client** (`app`, `operaton` schemas, private bucket; keys not URLs) | Supabase |

## The product flow (what every issue ultimately serves)
**Product decisions that override the blueprint are in `platform/docs/PRODUCT.md`** (owner console vs client app, workspace-code login, who uses the agent, change requests with human review, field versioning, theme packs on fixed layouts). Read it before planning.
In one line: **client input → agent writes that client's `workflow.json` (flow + forms + picked UI packages) → compile → deploy to that client's own instance (its Operaton + Supabase project).**

1. **We create the client** in the owner console (no public sign-up) and issue its workspace code.
2. **Provisioning worker builds the client's vault automatically**, no manual steps, every step idempotent and re-runnable:
   - creates the client's **own Supabase project** via the Supabase Management API (platform project = control plane; one project per client), applies `app` + `operaton` schemas and roles, turns the Data API off, creates the private bucket;
   - starts the client's **own Operaton service on Railway** via the Railway API (pinned image, env from the registry, private networking, service account), waits for health;
   - writes the registry row (client → Operaton URL, DB DSN, bucket, encrypted secrets), seeds the business admin and the chosen template.
3. **Agent writes the client's `workflow.json` from the client's input.** We (platform owners, in the owner console) describe the client's process; the agent turns it into the client's **draft `workflow.json`** (the prototype calls it `workspace.json`). One file per client holds everything: `org` (departments, levels), `flow` (stages, tasks, approvals, document slots, SLAs), `forms`, `apps` (pages → layout template → regions → **UI packages picked for this client** with their props, nav, accent) and `kpis`. The agent edits it only through typed, strict-schema tools; every change is a logged JSON patch with undo. The **UI packages are pre-built by us** (`@zm/blocks` blocks and layout templates on `@zm/ui` + `@zm/tokens`); the agent chooses and configures them by reading their **manifests** (name, description, allowed props) — it never writes code or CSS.
4. **Validate → preview → publish → deploy to that client's instance.** The draft `workflow.json` is validated against its JSON Schema, previewed in the shell, and a **person** clicks Publish: the compiler emits BPMN (+ DMN) and form/UI specs, deploys them to that client's Operaton as an immutable release vN, and stores the UI config in that client's database. Running sites stay on their version unless the admin migrates them.
5. **Run.** Users open the client landing page, enter the workspace code, log in with email + password (never Operaton); the API resolves the client from the JWT, filters the release by department/level/scope, and the one Z-Matrix shell draws that client's screens from its config; tasks come from that client's Operaton, answers/files from its database and bucket.

Who builds what: **we** (developers with Claude) build the packages — tokens, components, blocks with manifests, layout templates, the compiler, the agent tools, the provisioning worker. **The agent** only arranges those packages per client through configuration. A new capability one client needs = we build the block once; every client's agent can then place it.

## POC scope (current) — read before planning anything
- **One pilot client, fully provisioned by the worker**: Supabase **platform project** (control plane: `platform` schema — clients, requests, admins, templates, releases index) + **one client project** created by the provisioning worker (schemas `app`, `operaton`, private bucket) = 2 projects (fits the Supabase free tier). One Operaton service on Railway for the pilot, also started by the worker.
- The platform project is created once by hand via the Supabase MCP (`needs:human` for org/billing); everything per-client is created **by code**, never by hand, so client #2 is just another approval.
- Isolation tests that need a second client run **locally** (Supabase CLI + Operaton in Docker Compose) until we decide to provision a second cloud client.
- Blueprint items outside the POC go to the `Backlog` milestone.

## Operaton: bring it in, don't rebuild it
- Operaton is used as an engine, not rewritten. **Default: run it unmodified** (official image or this fork's `distro/run` build) configured by env vars; talk to it over REST (`/engine-rest`).
- **Engine changes are the exception**: only in an issue labelled `area:engine` that explains why REST/config/plugins can't do it. Keep them small, in isolated commits under the upstream module they touch, with a test, so upstream merges stay easy. Never reformat or refactor upstream code.
- Disable Operaton's webapps for clients (`run.sh --rest`; check `distro/run/assembly/resources/default.yml` for the exact properties). Built-in auth stays on; only the API and worker hold the service account.
- Operaton connects to Supabase through the **session pooler on port 5432** (prepared statements), schema `operaton`.
- Pin versions: the fork's `pom.xml` is `2.2.0-SNAPSHOT`; deployed images use a released tag, never `latest`.
- Before designing anything engine-related, **map it to Operaton source** (Sourcegraph MCP `repo:^github.com/operaton/operaton$` or `repo:^github.com/Adityashandilya555/retail_expansion_platform$ fork:yes` — **`fork:yes` is required**, Sourcegraph hides forks otherwise; the `operaton-researcher` subagent, or `../operaton`) and cite paths/endpoints in the issue or PR. Follow "Code search: Sourcegraph rules" below.

### Blueprint → Operaton map (starting points; the full verified map with line numbers is in plan issue #7)
| Blueprint concept | Operaton mechanism | Where to look |
|---|---|---|
| Release vN (§03, §11) | Deployment, immutable, versioned per process key | `engine-rest/.../rest/DeploymentRestService.java:45-49` (`POST /deployment/create`) → `rest/impl/DeploymentRestServiceImpl.java`; engine side `engine/.../impl/repository/DeploymentBuilderImpl.java` |
| Steps, parallel departments | BPMN user tasks, parallel gateways, call activities | `engine/.../bpmn/parser/BpmnParse.java`, `model-api/bpmn-model` |
| Levels, approval chains (§08) | `candidateGroups` per level (`legal__L1`), task outcomes as variables + gateways — see *Engine constraints* below | `BpmnParse.java` (candidateGroups parsing), `TaskRestService.java` (task queries), `SaveGroupCmd.java` |
| Forms (§09) | `operaton:formKey="zm:<task_id>"` (not `embedded:deployment:forms/…`); the form is a JSON Schema in the client's release, rendered by our shell — UI config never goes into the BPMN | `engine/.../impl/form/`, `GET /task/{id}/form` |
| Users, levels, scope | Identity service + authorizations | `IdentityRestService`, `AuthorizationRestService`, `engine/.../identity/` |
| Live changes (§11) | Process instance migration | `MigrationRestService.java`: `POST /migration/generate` (35-39), `/validate` (41-45), `/execute` (47-50), **`/executeAsync` (52-56, batch — use for many sites)**; `engine/.../impl/migration/` |
| KPIs (§12) | History tables (`ACT_HI_*`), history level full | `HistoryRestService`, `engine/.../history/` |
| Isolation (§07) | One engine per client (tenant ids only if engines are shared later) | `TenantIdProvider` (engine/src/main/java/org/operaton/bpm/engine/impl/cfg/multitenancy/TenantIdProvider.java) |
| Notifications, integrations | External tasks | `ExternalTaskRestService` |
| Headless engine | Operaton Run, REST only | `distro/run/` (`assembly/resources/default.yml`, `production.yml`, `run.sh --rest`) |

### Engine constraints found while mapping (verified in source)
- **Resource id whitelist.** By default Operaton accepts only `[a-zA-Z0-9]+|operaton-admin` for user, group and tenant ids (`generalResourceWhitelistPattern`, `ProcessEngineConfiguration.java:331` on `main`, `:323` at `v2.1.5`; checked in `EnsureUtil.java:416-456`, called from `SaveUserCmd`, `SaveGroupCmd`, `SaveTenantCmd`). BPMN parsing accepts `legal__L1`, but **creating that group over REST fails**, and so would UUID user ids. Decision: our Operaton image ships a config override setting `operaton.bpm.generic-properties.properties.group-resource-whitelist-pattern` and `user-resource-whitelist-pattern` to `[a-zA-Z0-9_-]+|operaton-admin` (the `generic-properties` block that `distro/run/assembly/resources/production.yml` uses for other keys; `production.yml` itself does not set these). S1-02 must prove it with a test that creates group `legal__L2` and a UUID user via REST. Fallback if that fails: alphanumeric ids (`legalL2`).
- **Candidate groups** come from the client's department levels and approval chains in `workflow.json` (`<dept>__L<n>`), not copied from a free-text role as the prototype does today.

## The prototype (to be imported)
The working prototype is `github.com/Shrey2149/outpost` (a fork of `Adityashandilya555/operaton-plat`, the repo the blueprint names): `matrix.py` (validator + BPMN/form compiler + deploy + configurator ops), `mcp_server.py` (matrix-configurator MCP), `workspace.json`, `catalogue.json`, `start.sh`. A Sprint 1 issue imports it into `platform/prototype/` unchanged; S1-08 then ports the compiler into `platform/packages/compiler`: `compile_main` (matrix.py:408), `compile_module`/`add_task` (:352/:379), `build` (:525), `deploy` (:563). Changes for `workflow.json`: `zm:` formKeys with JSON Schema forms instead of `form_html`; candidate groups from levels/approval chains; the `apps` section validated against block manifests and stored in the client's database (never compiled into BPMN); deploy target = the client's own Operaton from the registry.

## Target layout (created by Sprint 1 issues)
```
platform/apps/web/            client app: landing (workspace code) → login → shell drawn from the client's release (Vercel)
platform/apps/console/        owner console: clients, provisioning, agent, publish, change-request review, account counts (Vercel, separate project/domain)
platform/apps/api/            FastAPI platform API incl. agent loop + typed draft tools (Railway)
platform/apps/worker/         provisioning (Supabase Management API + Railway API), reconciler, notifications (Railway)
platform/packages/compiler/   workflow.json → BPMN/DMN + form/UI specs → release on the client's Operaton (from matrix.py)
platform/packages/schema/     workflow.json JSON Schema v1 + Pydantic; TS types generated
platform/packages/zm-tokens|zm-ui|zm-forms/   TypeScript design system (installed once)
platform/packages/zm-blocks/  blocks + manifest.json each (name, description, allowed props) — what the agent can place
platform/packages/zm-themes/  theme packs (tokens + styled components), each with DESIGN.md; built/audited with Impeccable
platform/packages/zm-layouts/ fixed layouts (screen types) with named regions
platform/packages/templates/  starter workflow.json per template (QSR, Café, Gated retail) + department catalogue
platform/infra/operaton/      Dockerfile (official image or fork build), railway.json, health check
platform/infra/supabase/      supabase CLI: platform/ migrations (control plane) and client/ migrations (app + operaton roles) applied by the worker
platform/prototype/           imported outpost prototype, still runnable
platform/docker-compose.yml   local stack: api, worker, operaton ×2 (pilot + isolation test), supabase
```

## Infrastructure work goes through MCP (or CLI)
| Platform | Tool | Use for |
|---|---|---|
| Supabase | `supabase` MCP (OAuth) + `supabase` CLI for dev work; **Management API** (`SUPABASE_ACCESS_TOKEN`) from the provisioning worker for client projects | platform project, migrations (`apply_migration`), `execute_sql` checks, `get_advisors` after every schema change, storage |
| Railway | `railway` MCP (`railway mcp`, CLI ≥ 5.44) for dev work; **Railway public API** (`RAILWAY_API_TOKEN`) from the provisioning worker for client Operaton services | services `api`, `worker`, `operaton-<client>`; variables; private networking; deploys; logs |
| Vercel | `vercel` MCP (OAuth) + CLI | project for `platform/apps/web`, env vars, deployments |
| GitHub | `github` MCP or `gh --repo Adityashandilya555/retail_expansion_platform` | issues, labels, milestones, PRs, Actions |
Secrets never go into git, issues, PRs or chat — set them as platform env vars via MCP/CLI and refer to them by name. Human-only steps (billing, OAuth consent, plan upgrades) get `needs:human` and an exact click path.

## Agent tools and rules
Tools agents can call besides the platform MCPs above: `sourcegraph` (code search), `rea` (reverse engineering), the `operaton-researcher` subagent, and the commands `/refine-issue`, `/implement-issue`, `/infra-check`. All MCP servers are registered in the root `.mcp.json`; Claude Code asks each person to approve them on first use. Check your setup with `python3 platform/scripts/check_agent_tools.py` (Node, GitHub, REA, Sourcegraph incl. `rev:` and `fork:yes` search).

### Code search: Sourcegraph rules
Sourcegraph is the first stop for any question about Operaton, the prototype or code in this fork. It returns exact paths and lines without loading the 17k-file tree into context.

**Access**
- MCP server `sourcegraph`: `${SRC_ENDPOINT:-http://localhost:7080}/.api/mcp`, authorised with `SRC_ACCESS_TOKEN` (export it in your shell; never commit it). Adi runs the instance locally; it indexes `github.com/operaton/operaton`, `github.com/Adityashandilya555/retail_expansion_platform` and `github.com/Shrey2149/outpost`.
- No instance? Fall back, in this order, and say in the issue or PR which source you used:
  1. a local clone of `operaton/operaton`: `git grep`, and `git show v2.1.5:<path>` for the pinned release;
  2. GitHub code search for upstream (`gh search code --repo operaton/operaton <terms>`);
  3. this checkout (`git grep` outside `platform/` is Operaton as forked).

**Query rules**
1. **Scope the repo with an anchored regex.** Upstream: `repo:^github.com/operaton/operaton$`. This fork: `repo:^github.com/Adityashandilya555/retail_expansion_platform$ fork:yes`. Without `fork:yes` Sourcegraph silently returns nothing for the fork.
2. **Choose the revision deliberately.** The default branch is `main` (2.2.0-SNAPSHOT), but we **run 2.1.5** (plan decision D1). For runtime behaviour add `rev:v2.1.5`; `rev:` always needs a `repo:` filter. Line numbers differ between the two (`generalResourceWhitelistPattern` is line 331 on `main` and 323 at `v2.1.5`), so give every line number with its revision.
3. **Narrow by path:**
   - `file:^engine/src/main/` for the engine, `file:^engine-rest/` for REST, `file:^distro/run/` for Operaton Run, `file:^spring-boot-starter/` for the starter properties;
   - `file:^platform/` for our code;
   - add `-file:/src/test/` unless you want test examples.
4. **Pick the tool by question:**
   - an exact identifier, property key or endpoint path → `keyword_search` (1–3 terms, `lang:java`, quote phrases);
   - a concept ("where is history time-to-live enforced") → `nls_search`, then confirm with `keyword_search`;
   - a symbol's definition or its callers → `go_to_definition`, `find_references`;
   - reading code → `read_file` with a line range, never a whole large file;
   - layout → `list_files`;
   - history → `commit_search`, `diff_search`;
   - what changed between two points → `compare_revisions`. Run it on this fork between the last synced upstream commit and `main` to list our deltas, or on upstream between `v2.1.5` and a new tag before an engine upgrade.
5. **Verify before you cite.** Every path:line in an issue, PR, ADR or vault note comes from a search or read in the current session, with its revision. Mark anything you couldn't confirm `UNVERIFIED`.
6. **Mind index lag.** The instance can trail GitHub. For code merged in the last hour, read the git tree or the PR instead.
7. **Keep context small.** Return findings (path, lines, one line on why), not file dumps. For more than about five lookups, delegate to the `operaton-researcher` subagent.
8. **Stay in scope.** Sourcegraph holds only the three public repos above: never paste secrets or client data into a query.

### Reverse engineering: REA
`rea` (github.com/morluto/rea, MIT, pinned `rea-agents@6.4.0`, Node ≥ 22.19) inspects compiled things whose source we don't have: JavaScript/Electron bundles, .NET assemblies, native binaries (with an existing Ghidra, Hopper or IDA install), and websites. Analysis runs locally, and results come with evidence and stated limits.
- **Use it for:**
  - the Z-Matrix design kit, which ships only a compiled `_ds_bundle.js` and an empty component manifest: recover its component inventory, props and states for `@zm/tokens`, `@zm/ui` and the theme packs;
  - a built artifact when its source can't answer the question, e.g. what our production web bundle actually contains;
  - studying a reference product's feature for product research.
- **Don't use it for:**
  - Operaton (we have its source: use Sourcegraph);
  - our own code or the prototype;
  - anything holding client data.
- **Third-party software:** check its licence or terms allow analysis first. Never copy its code into this repo; re-implement from the understanding.
- Findings go to `vault/40-Research/<topic>.md` with REA's evidence and limitations.
- It exposes about 170 tools, so enable it in `/mcp` only for sessions that need it, and keep it off otherwise.
- Node: supported versions are 22.19+, 24.11+ and 26+; we use **Node 26** (`brew upgrade node`). Odd versions such as 25 are unsupported: REA starts with an npm `EBADENGINE` warning (checked 2026-10-12).
- Bump the pinned version deliberately in a PR (REA releases often); don't run `rea setup` here, because `.mcp.json` already registers the server.

### Validating an issue: `/refine-issue`
Run `/refine-issue <n>` before `/implement-issue <n>`, and again when a dependency merges with a different shape than its spec described.
- It checks labels and size, product and convention fit, dependency drift, every Operaton reference (at `main` and `rev:v2.1.5`), repo facts and env vars, testable acceptance criteria, overlap with other issues, and time-sensitive vendor facts.
- It reports a verdict (Ready / Needs refinement / Invalid) with evidence and a proposed rewrite.
- It edits the issue only with `--apply` and your confirmation.

## Environment variables
Canonical list: `platform/.env.example` (each var tagged with the platform that holds it). Local: copy to `platform/.env` (gitignored). New var → add to `.env.example`, set it via MCP, name it in the PR.

## CI
Operaton is the base project, so its build and test workflows stay on: `pr-build.yml` (build, unit, integration, Spring Boot, Quarkus, webapps-neo tests), `build.yml`, `migration-test.yml`, `unit-tests-with-databases.yaml` (manual, run against PostgreSQL before pinning an engine version), `zizmor-check.yml`, `lint.yml`. They skip `platform/**`-only changes. Upstream-maintenance workflows are disabled on the fork. Full list, reasons and every fork delta: `platform/docs/FORK.md` — update it whenever you change anything outside `platform/`.
Our CI: `.github/workflows/zm-platform.yml` (ruff + pytest, tsc + eslint + vitest; Playwright joins when e2e exists), triggered only by `platform/**`. An engine change must pass `pr-build.yml`; run `./mvnw -pl <module> test` locally first (root `AGENTS.md`).

## Issues are specs
Template: `.github/ISSUE_TEMPLATE/zm_spec.yml` — Context (§ref) · Spec · How (MCP per step, `HUMAN:` steps) · Operaton references · Acceptance criteria (Given/When/Then) · Test plan · Out of scope · Depends on · Environment variables.
Implementing: restate the spec → failing tests from acceptance criteria → implement → green → PR. If the spec is wrong, comment and stop.

### Labels (`platform/labels.yml`, synced by `platform/scripts/sync-labels.sh`)
`sprint:1` `sprint:2` · `area:infra` `area:engine` `area:backend` `area:frontend` `area:agent` `area:qa` `area:design` · `type:feature` `type:chore` `type:spike` `type:test` `type:docs` · `priority:P0|P1|P2` · `size:S` (≤½ day) `size:M` (1–2 days) `size:L` (~3 days; split bigger) · `via:supabase-mcp` `via:railway-mcp` `via:vercel-mcp` `via:github-mcp` · `needs:human` `blocked` `good-first-agent-task`
Milestones: `Sprint 1 — Foundation`, `Sprint 2 — Configure and run`, `Backlog`.

## Git and PRs
Branch `<issue#>-<slug>` from `main`; one issue per PR; title `#<n> <title>`; body `Closes #<n>`, summary, test evidence, env vars. Conventional commits. No direct pushes to `main`, no force-push on shared branches. Adi (repo owner) merges: his own PRs need no second approval once CI is green, and other collaborators' PRs need his review.

## Code conventions
- Python 3.12, typed, Pydantic v2 at boundaries, async SQLAlchemy, `httpx.AsyncClient` for Operaton, JSON logs with `client` + `request_id`.
- TypeScript strict, no `any`, API types generated from OpenAPI, components with light/dark previews and visible focus.
- Client id comes only from the JWT; cross-client reads return 404.
- Files: keys `sites/<site>/<task>/<slot>/<uuid>-<name>` + sha256, never URLs.
