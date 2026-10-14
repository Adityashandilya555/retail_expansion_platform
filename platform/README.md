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
Project `zm-platform`, environment `production`, region `asia-southeast1-eqsg3a` (D2). Build and deploy settings live in each service's `railway.json` (config as code). Two settings can't live there, so set them once per service:

| Service | Root directory | Config file path | Public domain |
|---|---|---|---|
| `api` | `platform` | `/platform/apps/api/railway.json` | yes (`railway domain`) |
| `worker` | `platform` | `/platform/apps/worker/railway.json` | no |

- **Root directory `platform`:** the Dockerfiles use `platform/` as their build context, the same as compose. `dockerfilePath` in `railway.json` is relative to it.
- **Config file path is absolute:** it doesn't follow the root directory. `watchPatterns` are repo-absolute too, so Operaton-only merges don't redeploy either service.
- **`api` port:** the api listens on `$PORT` (injected by Railway), and Railway health-checks `/healthz`. Once S1-04 lands, add `"preDeployCommand": "alembic upgrade head"` to `apps/api/railway.json`.
- **Private networking** is on by default, so the worker reaches `operaton-<slug>.railway.internal:8080`.

Setup, run once after `railway login`:
1. HUMAN: pick a plan (Hobby or higher) under railway.com → Settings → Plans.
2. `railway init --name zm-platform`.
3. Add services `api` and `worker` from GitHub repo `Adityashandilya555/retail_expansion_platform`, branch `main`.
4. Set each service's root directory and config path (table above), then run `railway domain --service api`.
5. Set variables with `railway variables --service <svc> --set NAME=value`:
   - `api`: `CORS_ALLOWED_ORIGINS`, `LOG_LEVEL`;
   - `worker`: `LOG_LEVEL`, `WORKER_TICK_SECONDS`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID`, `RAILWAY_REGION`, `RAILWAY_API_TOKEN`.

   Further `[Railway]` variables in `.env.example` are added by the issues whose code first reads them.
6. HUMAN: Project → Settings → Tokens → create token `zm-worker`, then set it as `RAILWAY_API_TOKEN` on `worker`. S2-02b checks whether `serviceCreate` needs an account token instead.
