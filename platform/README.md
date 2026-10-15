# Z-Matrix platform

Our code on top of the Operaton fork. Conventions: [AGENTS.md](AGENTS.md) · product rules: [docs/PRODUCT.md](docs/PRODUCT.md) · blueprint: [docs/BLUEPRINT.md](docs/BLUEPRINT.md) · fork deltas: [docs/FORK.md](docs/FORK.md).

## Prerequisites
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is picked from `.python-version`)
- Node ≥ 22.12 with corepack (`corepack enable`; pnpm's version comes from `package.json`)
- Docker (Docker Desktop on macOS)
- [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started) — optional: the scripts fall back to `npx supabase`

## Local stack
```bash
cp .env.example .env        # optional for now; never commit .env
scripts/dev-up.sh           # Supabase CLI vault, then docker compose
uv run pytest -m integration
docker compose down && supabase --workdir infra stop   # data volumes are kept; add -v to drop them
```

| Service | URL / port | What |
|---|---|---|
| Supabase CLI (`infra/supabase`, project `zm-local-pilot`) | Postgres `:54322`, API `:54321` | pilot client vault (`pilot-local`) |
| `api` | http://localhost:8000/healthz | FastAPI platform API |
| `worker` | — | worker loop (JSON logs) |
| `operaton-pilot` | http://localhost:8080/engine-rest | pilot engine, DB = Supabase CLI Postgres, schema `operaton`. Our image (`infra/operaton`): REST only, service account `zm-service` / `zm-local-only`, `/health` open |
| `operaton-iso` | http://localhost:8081/engine-rest | isolation client engine (`qsr-local`), DB = `pg-iso` |
| `pg-platform` | Postgres `:5433` | control plane stand-in |
| `pg-iso` | Postgres `:5434` | isolation client vault |

A port already taken on your machine? Move it with `API_PORT`, `OPERATON_PILOT_PORT`, `OPERATON_ISO_PORT`, `PG_PLATFORM_PORT` or `PG_ISO_PORT` (for example `OPERATON_PILOT_PORT=8090 scripts/dev-up.sh`); the integration tests read the same variables.

## Checks (what CI runs)
```bash
uv sync --all-packages
uv run ruff check . && uv run ruff format --check .
uv run pytest                      # unit; integration tests are marked and skipped by default

pnpm install
pnpm typecheck && pnpm lint && pnpm test
```

## Deploy: Vercel (`zm-web`, `zm-console`)
There are two projects in the Vercel **Pro** team (D18); Hobby is non-commercial only. They are static Vite builds, so a later move to AWS is S3 + CloudFront. Build settings live in each app's `vercel.json`:
- **Install and build** run at the pnpm workspace root `platform/` (`cd ../.. && corepack pnpm …`), because the lockfile lives there. `corepack` (bundled with Node 22 and 24, Vercel's build runtimes) runs the pnpm pinned in `platform/package.json` (`packageManager`). Vercel's own Corepack switch only reads `packageManager` from the project root (`apps/<app>`), so it isn't used.
- **`ignoreCommand`** skips the build unless `platform/` changed, so Operaton-only commits don't build.
- **SPA fallback:** every path rewrites to `/index.html`, so deep links like `/app/legal` load the app.

| Project | Root directory | Env vars (Production + Preview) |
|---|---|---|
| `zm-web` | `platform/apps/web` | `VITE_API_BASE_URL=https://<railway-api-domain>` |
| `zm-console` | `platform/apps/console` | same |

Setup:
1. HUMAN: create or upgrade the Vercel team to **Pro**: Dashboard → Settings → Billing, starting with the Pro trial. Use one Developer seat; collaborators join as free Viewer seats.
2. HUMAN: install the Vercel GitHub app on the fork: vercel.com → Add New → Project → Import `retail_expansion_platform`. Then run `vercel login`.
3. Create both projects from the repo with the root directories above (keep "Include files outside the root directory" on), and set the env vars. Preview deployments on PRs are on by default.
   - Vercel's "Skip deployments when there are no changes to the root directory or its dependencies" detects workspaces from a lockfile at the repo root. Ours is in `platform/`, so after the first deploys, check that a change in `platform/packages/**` still builds both apps. If it doesn't, turn that setting off; `ignoreCommand` already skips non-`platform/` commits.
4. Add both production domains to `CORS_ALLOWED_ORIGINS` on the Railway `api` (comma-separated; see "Deploy: Railway" in #13).
5. Check:
   - `curl -X OPTIONS https://<api>/healthz -H "Origin: https://<zm-web domain>" -H "Access-Control-Request-Method: GET"` returns `access-control-allow-origin`;
   - `https://<zm-web domain>/app/legal` serves the app.

## Layout
```
apps/api            FastAPI platform API              (Python, uv workspace)
apps/worker         provisioning + reconciler loop    (Python)
apps/web            client app                        (Vite + React)
apps/console        owner console                     (Vite + React)
packages/compiler   workflow.json → BPMN + specs      (Python)
packages/schema     workflow.json schema v1           (Python)
packages/templates, zm-tokens, zm-ui, zm-forms, zm-blocks, api-client   (TypeScript)
infra/supabase      Supabase CLI project (local pilot vault)
infra/compose       init scripts for docker-compose.yml
tests/integration   smoke tests for the local stack
```

## Deploy: Railway (`api`, `worker`)
Project `zm-platform`, environment `production`, region `asia-southeast1-eqsg3a` (D2). The two services are defined in **`.railway/railway.ts`**, using Railway Infrastructure as Code. Config as code (`railway.json`) is deprecated, and new services can't opt in to it.

What the file sets for each service:
- **Source:** the GitHub repo, branch `main`, with root directory `platform`. The Dockerfiles use `platform/` as their build context, the same as compose, and `dockerfilePath` is relative to it.
- **Watch patterns:** repo-absolute, so only `platform/apps/<svc>/**`, `platform/packages/**`, `pyproject.toml` and `uv.lock` (plus `infra/supabase/**` for the worker) trigger a deploy. Operaton-only merges deploy nothing.
- **Deploy:** one replica in `asia-southeast1-eqsg3a`; restart on failure is Railway's default, so the file doesn't set it.
- **api only:** healthcheck `/healthz`. The api listens on `$PORT`, injected by Railway. Once S1-04 lands, add `preDeploy: "alembic upgrade head"` to `api`.
- **Variables:** `LOG_LEVEL`, plus `WORKER_TICK_SECONDS` and `RAILWAY_REGION` for the worker.
  - Secrets, and values that come from other systems, are `preserve()`: Railway keeps the value set in the dashboard. That covers `RAILWAY_API_TOKEN` and `SUPABASE_ACCESS_TOKEN` (worker), and `CORS_ALLOWED_ORIGINS` (api, set with #14). Paste secrets into the Railway dashboard only, never into git, issues or chat.
  - Railway provides `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID` and `PORT` at runtime.

Private networking is on by default, so the worker reaches `operaton-<slug>.railway.internal:8080`. The per-client Operaton services are created by the worker through the Railway API (S2-02b), not by this file.

Setup, run from `platform/`:
1. HUMAN: pick a plan (Hobby or higher) under railway.com → Settings → Plans, then run `npx @railway/cli login`.
2. `corepack pnpm install`, because the CLI evaluates `.railway/railway.ts`, which imports `railway/iac` from `node_modules`. Then run `npx @railway/cli link` and choose project `zm-platform`, environment `production`.
3. `npx @railway/cli config plan`. This is read-only; review the diff.
   - Railway matches services **by name**. A service it auto-created when the repo was connected (for example `retail_expansion_platform`) shows up as **destroy**, with `api` as **create**. Rename that service to `api` first, or delete it, so the plan has no destroy line.
   - Check that the `preserve()` variables plan cleanly before their values exist.
4. `npx @railway/cli config apply`. It confirms before changing anything.
5. `npx @railway/cli domain --service api`. Generated domains aren't part of IaC.
6. HUMAN: Project → Settings → Tokens → create token `zm-worker`, then set it as `RAILWAY_API_TOKEN` on `worker` in the dashboard. S2-02b checks whether `serviceCreate` needs an account token instead.

Later changes: edit `.railway/railway.ts` in a PR, then run `config plan` and `config apply` after merge. Run `config plan --detailed-exit-code` to check for drift.
