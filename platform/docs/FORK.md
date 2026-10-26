# Our fork of Operaton — deltas, CI, upstream sync

`Adityashandilya555/retail_expansion_platform` is a fork of `operaton/operaton`. Operaton is the base project; Z-Matrix lives in `platform/`. Every change outside `platform/` is listed here so upstream merges stay predictable.

## Fork deltas (outside `platform/`)
| File | Change | Why |
|---|---|---|
| `.github/workflows/build.yml` | `paths-ignore` += `platform/**`, `.mcp.json`; Sonar job gated on `vars.SONAR_ENABLED == 'true'` | Platform-only commits shouldn't trigger the 15–20 min Operaton build; no SonarCloud project on the fork |
| `.github/workflows/pr-build.yml` | same as above | same |
| `.github/workflows/migration-test.yml` | `paths` += `!platform/**` | Our Supabase migrations aren't Operaton's schema; without this every platform migration PR would run the 5-database rolling-update matrix (S1-01) |
| `.github/workflows/zm-operaton-image.yml` | new | Builds `platform/infra/operaton` (official 2.1.5 + our `default.yml`, no engine code change) for amd64 + arm64 on pushes to `main` touching it, and pushes `ghcr.io/adityashandilya555/zm-operaton:2.1.5-zm.<run>` + `sha-<short>` with `GITHUB_TOKEN` (`packages: write`). The package is public so Railway pulls without credentials (S1-02) |
| `.github/workflows/zm-platform.yml` | new | Z-Matrix CI: `python` (uv workspace: ruff, pytest + coverage), `web` (pnpm workspace: tsc, eslint, vitest + coverage, client-app build check: no `/dev/ui`, no Google Fonts, JS ≤ 300 kB gzip), `zm-integration` (Supabase CLI db + client vault migrations via `zm-worker vault-migrate`, S1-03b + `docker compose up --wait` + `pytest -m integration` with coverage, S1-01); `zm-isolation` (#31: `zm-compat --fixtures`, then the two local clients with their own credentials + `pytest -m isolation` with coverage; not a required check, as no check is: see the ruleset row); freshness checks that the committed `workflow.schema.json` and `packages/schema/ts` match the Pydantic models (S1-07), and that `packages/api-client` (`openapi.json`, generated `src/schema.d.ts`) matches the API (S1-05) + SonarQube Cloud scan reading the `coverage-*` artifacts of `platform/` (project `Adityashandilya555_retail_expansion_platform`, secret `SONAR_TOKEN`), only for `platform/**`. Operaton's own Sonar jobs in `build.yml`/`pr-build.yml` stay off (`SONAR_ENABLED` unset) until the first `area:engine` change |
| `.github/ISSUE_TEMPLATE/zm_spec.yml` | new | spec-driven issue template |
| `CLAUDE.md`, `.mcp.json`, `.claude/commands/*`, `.claude/agents/*` | new | agent setup (upstream `.claude/skills` untouched): MCP servers supabase, vercel, railway, sourcegraph, rea; commands `/refine-issue`, `/implement-issue`, `/infra-check`; subagent `operaton-researcher` |
| Repo settings: ruleset `main` | require a PR (0 approvals), block force pushes and deletion, no bypass; **no required status checks**. Source: `platform/infra/github/ruleset-main.json`, applied with `gh api -X POST repos/Adityashandilya555/retail_expansion_platform/rulesets --input platform/infra/github/ruleset-main.json` (to change it: `-X PUT .../rulesets/<id>`) | Adi merges his own PRs once CI is green; collaborators' PRs get his review. CI is path-filtered (`zm-platform` only on `platform/**`, Operaton builds skip `platform/**`), so a required check would never report on the other kind of PR and would block it forever (S1-01) |
| Engine modules | none yet | each future engine change gets a row here + an `area:engine` issue |

## Operaton workflows on the fork
**Kept — build and test the base project:**
| Workflow | Runs on | What it guarantees |
|---|---|---|
| `pr-build.yml` | PRs to `main` touching Operaton | Maven build + unit tests; integration, Spring Boot starter, Quarkus and webapps-neo tests when relevant |
| `build.yml` | push to `main` | full build + tests, uploads Operaton Run/Tomcat distro artifacts (used for our engine image when we change the engine) |
| `migration-test.yml` | PRs touching `**/*.sql` outside `platform/`, weekly, manual | DB rolling-update/migration safety (we run Operaton on Postgres/Supabase). GitHub had it as `disabled_fork`; enabled in S1-01 |
| `unit-tests-with-databases.yaml` | manual | engine tests against **PostgreSQL** etc. — run before pinning a new engine version |
| `zizmor-check.yml` | workflow changes | security lint of all workflows incl. ours |
| `lint.yml` | PRs touching ADR markdown | markdown lint (non-blocking) |
| `experimental.yml`, `maintenance.yml` | manual only | harmless; available if needed |

**Disabled on the fork** (upstream project maintenance, need upstream-only secrets, or harmful here):
`sync-labels.yml` (deletes every label not in Operaton's list — would wipe ours), `label-pr.yml` (adds upstream labels), `stale.yml`, `auto-merge.yml`, `check-for-new-contributor.yml`, `good-first-issue.yml`, `update-changelog-noteworthy.yml`, `update-dependabot-milestone.yml`, `update-nodejs.yml`, `update-sbom.yml`, `code-cleanup.yml`, `reports-and-docs.yml`, `slack-link-check.yml`, `nightly-trigger.yml` (costs minutes; run `pr-build`/`integration-build` manually instead), `release.yml` and `integration-build.yml` (publish to Maven Central/Docker with Operaton's GPG/OSSRH secrets).
`scripts/setup-fork.sh` in the Retailexpansion workspace applies this list; re-enable any with `gh workflow enable <file> --repo Adityashandilya555/retail_expansion_platform`.

Optional: create a free SonarCloud project for the fork, add secret `SONAR_TOKEN`, update `sonar.organization` in a fork delta, then set repo variable `SONAR_ENABLED=true`.

## Syncing upstream
```bash
git fetch upstream
git checkout -b chore/sync-upstream-$(date +%F) main
git merge upstream/main          # never rebase main
# resolve conflicts using the delta table above; re-run pr-build on the PR
gh pr create --repo Adityashandilya555/retail_expansion_platform --base main
```
After a sync, re-check that the disabled workflows are still disabled (new upstream workflows arrive enabled).

## PRs and issues target the fork
GitHub defaults a fork's PRs to the parent. Always `gh repo set-default Adityashandilya555/retail_expansion_platform` and `--repo Adityashandilya555/retail_expansion_platform`; in the web UI check the base repository before clicking "Create pull request".
