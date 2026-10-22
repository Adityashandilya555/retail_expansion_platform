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
uv run zm-schema export --check && uv run zm-api openapi --check   # committed generated files are current

pnpm install
pnpm typecheck && pnpm lint && pnpm test
pnpm --filter @zm/web dev       # component preview at http://localhost:5173/dev/ui
```

## Platform API (`apps/api`)
- **Which client:** only the JWT claim `client` (a registry slug) decides it, never the path or body. `ClientCtx` gives a route that client's database (`zm_api` role, pool 5), its Operaton (`OperatonClient`, as the `zm-service` account) and its bucket. Registry rows are cached for 60 s; the per-client engines and clients are created lazily and closed when evicted. `OwnerCtx` (owner console) has no client resources. Put `/app/*` routes on `client_router()` and `/owner/*` routes on `owner_router()`, so each needs its token even without a Ctx parameter.
- **Errors:** everything is RFC 9457 `application/problem+json` `{type, title, status, detail, request_id, errors}` (`errors`: per-field `{path, message}` by JSON Pointer on 422, else null). `Problem` is every route's `default` response in the OpenAPI document, so the TS client types errors from it. Another client's ids are 404, never 403. From Operaton: 404 or "Cannot find … with id" → 404, 400 → 400, anything else → 502; Operaton's own message goes to the log only.
- **Logs:** one JSON line per request that reaches the app (CORS, the outermost middleware, answers preflights itself) (`client`, `request_id`, `method`, `route`, `status`, `ms`) plus one per Operaton call (`op`, `status`, `ms`). `X-Request-Id` is echoed or generated. Level from `LOG_LEVEL`.
- **TS client:** after an API change, run `uv run zm-api openapi` (writes `packages/api-client/openapi.json`), then `pnpm --filter @zm/api-client gen`. CI fails if either is stale. Apps use `createApiClient(import.meta.env.VITE_API_BASE_URL, getToken)`.

## Sign-in (#22)
- **Client app:** `/` asks for the workspace code (`POST /auth/workspace` → the client's name and look; 10/min/IP), `/login` for email and password (`POST /auth/login`), then `/app`. **Owner console:** `/login` (`POST /owner/auth/login`).
- **Tokens:**
  - The access token (JWT, `JWT_ACCESS_TTL_MIN`) lives in memory only.
  - The refresh token is an httpOnly, Secure, `SameSite=Strict` cookie on `/auth` (or `/owner/auth`), `JWT_REFRESH_TTL_DAYS`. Each `POST …/refresh` rotates it; presenting a spent one again revokes the whole session. `POST …/logout` revokes it.
  - Every failed sign-in gets the same 401. A valid token on the other surface (owner on `/app`, client user on `/owner`) gets 403.
- **Same site required.** Browsers only send a `SameSite=Strict` cookie when the app and the API are on the same site. Locally, `localhost:5173` and `localhost:8000` are. Deployed, `*.vercel.app` and Railway's generated domain are not. Either give both a domain under one site (e.g. `app.example.com` + `api.example.com`), or rewrite `/api/*` on Vercel to the Railway API and set `VITE_API_BASE_URL=/api`.
- **People:**
  ```bash
  uv run zm-api owners create --email you@example.com --name "You"   # asks for the password; deployed: `railway run`
  uv run zm-api users seed --client pilot-local packages/templates/matrix-retail/demo-users.json
  ```
  `users seed` reads a file under the current directory only, and prints each new user's generated password once (existing users keep theirs). It then mirrors every user into the client's Operaton: same uuid, an unusable password, and exactly the `<dept>__L<n>` groups of their memberships (`zm_api.users.sync_user`, D6). Client users live in the client's own database (`app.users`, `app.memberships`, `app.refresh_tokens`, migration `0002_users.sql`); owners in `platform.owners`.

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

## `workflow.json` schema
One `workflow.json` per client: org, flows, fields, forms, apps, KPIs. The Pydantic models in `packages/schema/src/zm_schema/models.py` are the source of truth (#16); cross-reference rules live in `semantics.py`.
```bash
uv run zm-schema validate path/to/workflow.json   # path: message per problem, exit 1 if any (files under the current dir only)
uv run zm-schema export                           # after a model change: regenerate workflow.schema.json
pnpm --filter @zm/schema gen                      # …then the TS types in packages/schema/ts/
```
CI fails if either generated file is stale. In Python, use `zm_schema.validate(doc)` to get every error as `{path, message}`, or `zm_schema.migrate(doc)` to get a `WorkflowV1` model. A breaking change bumps `CURRENT_VERSION` and registers a step in `zm_schema.versioning.MIGRATIONS`. Broken examples live in `packages/schema/tests/broken/`; regenerate them with `uv run python packages/schema/tests/make_broken.py`.

## Compiler
`zm_compiler.compile(WorkflowV1, client_slug) -> Release` (#19) is pure and deterministic: the same document gives byte-identical output. A `Release` holds:
- the BPMN: one main process `zm_<slug>__<flow>` per flow and one process per stage, all for a single Operaton deployment;
- per form key, a JSON Schema 2020-12 form with `x-zm-type`, `x-zm-options` and `x-zm-layout`;
- the `apps` UI config, unchanged;
- the candidate groups `<dept>__L<n>`;
- the routing variables per task (D5);
- the process keys.

Golden files live in `packages/compiler/tests/golden/`, and the shared form fixtures in `packages/schema/tests/fixtures/*.schema.json`. After an intended change, regenerate both with `ZM_UPDATE_GOLDEN=1 uv run pytest packages/compiler`. `pytest -m integration packages/compiler` deploys both templates to the local `operaton-pilot` and runs cases to `closed:legal` and `completed`.

## Forms
`@zm/forms` (#28) renders a compiled form: `<ZmForm schema layout values? readOnly? caseLabel? caseData? onSubmit>`.
- Sections become fieldsets; each row is a Mantine `Grid` with `span={{ base: 12, sm: n }}`, so phones get one column.
- Fields by `x-zm-type`: `text`, `textarea`, `number`, `currency` (₹, lakh grouping), `date` (native), `select`, `yes_no` (Yes/No radios). `document` shows "Uploads arrive with Files"; any other type shows "unsupported in v1".
- The outcome becomes buttons (the first is primary, `reject` is red) with the comment field above them. `caseData` fills the read-only "<case_label> data" panel for `x-zm-show`.
- Ajv 2020 (`ajv`, `formErrors`, both exported) checks the values first. `onSubmit` gets them without unanswered fields and can resolve with the API's 422 errors (`[{path, message}]`) to show them under their fields.

The API checks the same schema with `zm_schema.submission_errors(schema, values)`, which returns the same JSON Pointer paths. `packages/schema/tests/fixtures/*.cases.json` hold the shared cases, run by both vitest and pytest. Preview: `pnpm --filter @zm/web dev`, then open `/dev/ui`.

## Starter templates
`packages/templates/<key>/workflow.json` holds the starter `workflow.json` documents. It's a data folder, not a code package:
- `matrix-retail`: the pilot client, converted from the prototype (#18);
- `qsr`: a small template for the local isolation client.

A template is only a starting point (D14). Provisioning copies it into a new client's draft once, and later template changes never reach existing clients.
```bash
uv run zm-schema validate packages/templates/matrix-retail/workflow.json
PLATFORM_DATABASE_URL=… uv run zm-api templates sync   # validate + upsert into platform.templates (key, version)
```
`matrix-retail/demo-users.json` lists the demo accounts (emails only; passwords are generated at seed time).

## Layout
```
apps/api            FastAPI platform API              (Python, uv workspace)
apps/worker         provisioning + reconciler loop    (Python)
apps/web            client app                        (Vite + React)
apps/console        owner console                     (Vite + React)
packages/compiler   workflow.json → BPMN + specs      (Python)
packages/schema     workflow.json schema v1           (Python)
packages/templates  starter workflow.json documents (data)
packages/zm-ui, zm-forms, zm-blocks, api-client   (TypeScript; zm-ui = Mantine theme + extras)
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
- **api only:** healthcheck `/healthz`. The api listens on `$PORT`, injected by Railway. Pre-deploy runs the control-plane migrations (`alembic -c apps/api/alembic.ini -x db=platform upgrade head`, S1-04); a failed migration stops the deploy.
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
