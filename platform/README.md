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
uv run pytest -m isolation  # the two local clients can't reach each other (#31)
docker compose down && supabase --workdir infra stop   # data volumes are kept; add -v to drop them
```

| Service | URL / port | What |
|---|---|---|
| Supabase CLI (`infra/supabase`, project `zm-local-pilot`) | Postgres `:54322`, API `:54321` | pilot client vault (`pilot-local`) |
| `api` | http://localhost:8000/healthz | FastAPI platform API |
| `worker` | — | worker loop (JSON logs) |
| `operaton-pilot` | http://localhost:8080/engine-rest | pilot engine, DB = Supabase CLI Postgres, schema `operaton`. Our image (`infra/operaton`): REST only, service account `zm-service` / `zm-local-only`, `/health` open |
| `operaton-iso` | http://localhost:8081/engine-rest | isolation client engine (`qsr-local`), DB = `pg-iso`. Its own service password, `zm-iso-local-only` (#31) |
| `pg-platform` | Postgres `:5433` | control plane stand-in |
| `pg-iso` | Postgres `:5434` | isolation client vault; roles `zm_api` / `zm_engine` with their own passwords (`ZM_ISO_*`, #31) |

A port already taken on your machine? Move it with `API_PORT`, `OPERATON_PILOT_PORT`, `OPERATON_ISO_PORT`, `PG_PLATFORM_PORT` or `PG_ISO_PORT` (for example `OPERATON_PILOT_PORT=8090 scripts/dev-up.sh`); the integration tests read the same variables.

## Checks (what CI runs)
```bash
uv sync --all-packages
uv run ruff check . && uv run ruff format --check .
uv run pytest                      # unit; integration tests are marked and skipped by default
uv run zm-schema export --check && uv run zm-api openapi --check   # committed generated files are current
uv run zm-compat --fixtures        # every template and synthetic release still compiles and draws (#31)

pnpm install
pnpm typecheck && pnpm lint && pnpm test
pnpm --filter @zm/web dev       # previews: components at http://localhost:5173/dev/ui, blocks at /dev/layouts
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
- **Invites (S2-23):** the owner invites a client's admin (`POST /owner/clients/{id}/client-admin`); the admin manages people at `/app/admin/people` (`/app/admin/users`: invite, department + level from the active release's org, deactivate). Each change is mirrored into Operaton at once (if that fails, the reconciler mirrors the person again: `app.users.unsynced_since`, `0008_users_unsynced.sql`), and requests read memberships from the database, so a new level applies on the next request. The client's last active client admin can't be deactivated (409). The worker's reconciler pass emails each invite through Resend: `CLIENT_APP_BASE_URL/invite/<slug>.<token>`, one-time, 7 days, and `POST /auth/invite/accept` sets the password and signs the person in. Inviting someone who never accepted again (the people page's Resend invite) replaces their invite with a new link; the old one stops working. Memberships left out of the request (always, on the owner's route) stay as they were. Only the token's hash stays in `app.invites` (`0006_people.sql`): the sealed token is dropped once emailed or expired. Until `RESEND_API_KEY`, `EMAIL_FROM` and `CLIENT_APP_BASE_URL` are set, invites wait unsent.
- **Change requests (S2-24a):** the client admin asks for a change at `/app/admin/change-requests`: add fields to a form, add a document type, rename a field, make a field required or optional, or add a choice to a select field. `POST /app/admin/change-requests` takes a typed payload per `kind` (`zm_api.change_requests.Payload`). Its forms and fields must exist in the active release (else 422), new field types come from `zm_schema`'s field catalogue, only select and multi_select fields take options (and need them), and a payload is at most 16,000 characters of JSON. A repeat with the same `Idempotency-Key` header (per client) returns the first request; the same key with a different request is 409. `GET …/targets` lists both for the form, and `GET /app/admin/change-requests[/{id}]` gives the client's own requests with their status and review note (another client's are 404), newest first, `limit` (default 100, at most 500) and `offset` at a time. Each is read as stored, so a request sent under older rules still lists. Requests live in the control plane, `platform.change_requests` (Alembic `0002`), as `submitted` until we review them in the owner console (S2-24b).

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
`matrix-retail/demo-users.json` lists the demo accounts (emails only; passwords are generated at seed time). `matrix-retail/optional-fields.json` is its optional catalogue (S2-25): fields and document types (`state`, `district`, `gst_no`, `landlord_pan`, `gst_certificate`) that a draft can take as they are when a client asks for them.

## Draft and change log (#33)
Every edit of a client's draft goes through `zm_api.drafts.apply_change(ctx, author, tool, patch, summary, conversation_id?, change_request_id?, draft_sha256?)`. It applies an RFC 6902 patch to a copy, checks the result with `zm_schema.validate`, and writes the draft and an `app.draft_changes` row (patch, inverse, author, `after_sha256` of the draft it left) in one transaction. A change that brings in problems the draft didn't have writes nothing and comes back as a 422 listing them with `workflow.json` paths; problems the draft already had (say, restored by an undo) don't block it. With `draft_sha256` (the sha256 of the draft the caller read, which `drafts.read` gives), a draft changed since is a 409. `app.draft_changes` is insert-only: a trigger refuses updates and deletes, and `zm_api` has no grant for them. Undo applies the inverse of the newest change not yet undone, as a new row, and restores that exact earlier state without validating it again. It answers 409 when the draft was written outside the log since that change (`zm-api seed-draft`, SQL), because the inverse was made for another document.

**Field versioning (#35, S2-25):** once a release is live, every change is also checked against the fields of every published release, by key (`zm_api.versioning`), so making an older release active again doesn't free the fields a newer one added. A field, document type or select option may be added, a label may change, and a field may be hidden (`remove_field` sets `hidden: true`) or made required. A select option may be removed and a document field's rules changed: a stored value keeps its raw option id, a site keeps its own release's forms, and document rules apply to new uploads. A published field can't be deleted, and a type change is refused as needing a migration decision (checked against the newest release that has the field). `GET /validate` and publish report the same problems, and an undo that would bring one in is a 409. Publish stamps the new release's workflow: `since: vN` on the fields it adds and `required_from_version: vN` on those it makes required; release 1 is the baseline and carries neither. A site keeps its own release's forms, so a field made required in vN binds only sites started on vN or later.

Owner endpoints under `/owner/clients/{id}/draft`: `GET` (the draft), `GET /changes` (newest first, without the inverses), `POST /undo`, `GET /diff` (stages, tasks, fields, blocks, theme and accent against the active release), `GET /validate`, and `PATCH /theme {theme?, accent?}`.

## Agent tools (#36, #39)
`zm_api.agent.tools` holds the agent's tools v1 (blueprint §10). Read: `get_overview` (also the pages without their blocks, and the menu, listed first so a cut overview still names them), `get_task`, `get_form`, `list_field_types` (zm_schema's types, each flagged with whether @zm/forms draws it), `list_templates`, `load_template` (only while the draft is still an unchanged template). Org: `add_department`, `update_department`, `set_levels`. Flow: `add_stage`, `set_after`, `add_task`, `update_task`, `move_task`, `remove_task`, `set_outcomes`, `set_approval_chain`, `add_document_slot`. Forms: `add_field`, `update_field` (never the key or type), `remove_field` (sets the field's `hidden`), `arrange_form`. UI (#39), from `packages/zm-blocks/catalogue.json`: `list_layouts`, `list_blocks` (names, versions, description, and `goes_in`: each layout's regions that take the block, as validation reads them), `get_block_manifest` (the newest version, with its props schema and `goes_in`), `get_page` (one page whole: each region's blocks in order, with their props), `set_page` (adds a page or sets its layout, department and flow; its blocks stay), `place_block` (at an index or the region's end), `update_block` (all its props), `remove_block`, `set_nav` (the whole menu). The draft's validation checks them against the manifests: the region exists in the layout and takes the block (`allowed_blocks`, `max`), the props fit the block's schema, a `case` page has a flow and no other `case` page has that flow, the menu names existing pages and no `case` page, and a draft with pages has a menu (the shell opens on its first page). No tool sets the theme preset or accent (D19). Checks: `validate_draft`, `diff_vs_live`.

`definitions()` gives them as Messages API tools: no extra properties, every property required (null only where it means "none", such as a task with no form), and no client parameter. The 22 edits are `strict: true` but for `place_block` and `update_block`, whose free-form `props` a strict schema can't hold, and the reads and checks aren't, which keeps a request inside Anthropic's [schema complexity limits](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) (at most 20 strict tools, 16 union and 24 optional parameters in total; a test counts them, and today it is 20, 13 and 0). `update_task` and `update_field` set every value, so a form, shown fields or options can be cleared. `call(ctx, author, name, args, conversation_id?)` runs one for the console's client: an edit builds its RFC 6902 patch from the locked draft (`apply_change` also takes a function of the draft) and is logged under the tool's name, with undo. It answers `{ok, summary, errors?}`, plus `data` for a read; `summary` is the change chip, and a done edit adds its log row's `change_id`. `get_overview`, `get_task`, `get_form`, `get_page` and every done edit also answer the draft's `draft_sha256`; each edit takes the one it last got and is refused while the draft has changed since, so it never overwrites a person's edit it hasn't read. A result's `data` is cut at 40,000 characters of JSON and its `errors` at 50, flagged `truncated`. A cut read returns no `draft_sha256`. There is no HTTP route: the agent loop (#38) calls them.

**For the loop and the prompt:**
- **Edits must run one at a time.** Two edits sent in one turn carry the same `draft_sha256`, so every one after the first would be refused as stale. The agent loop runs a reply's edits in order, each on the sha the edit before it answered.
- **A problem the draft already had doesn't block an edit, even one that moves stages or tasks.** Problems are counted by kind, with list positions stripped from their paths and messages. An edit is refused only if it raises the count of some kind.
- **A truncated read returns no `draft_sha256`.** Read the parts with `get_task`, `get_form` or `get_page` before editing.
- **The strict budget is full (20 of 20 tools).** A new strict tool means making another one non-strict.

## Agent loop (#38)
`zm_api.agent.loop` runs the owner console's conversation with the client's agent (`AGENT_MODEL`, default `claude-opus-5-5`, Anthropic Python SDK). `POST /owner/clients/{id}/agent/conversations` `{mode}` starts one: `configure` (effort `high`) or `edit` (effort `medium`). `POST …/conversations/{cid}/messages` `{text}` runs one turn as server-sent events: `text_delta {text}`, `retry {}` (drop the text this call has streamed so far: its reply couldn't be read and is asked again), `tool_call {name}`, `change {change_id, summary}` (the change log row and its chip), `refusal {category}`, `error {message}`, then always `done {usage}`. An API error the request itself caused (a 4xx but 429) says `The request was refused: <reason>`; other API failures say to try again, and any other failure still ends with `error` and `done`. A conversation answers only the owner who started it (anyone else gets 404). One turn runs at a time per conversation: a message sent while one runs is a 409 and calls nothing (a session advisory lock in the vault, taken before the conversation is read and held until the stream ends). Each API process runs at most 4 turns at once per client (its vault pool has 5 connections and each turn holds one); past that a message is a 409 too.
- **A turn** appends the owner's message and a `{"role": "system"}` message with a short overview of the draft and whether it changed since the model's last read, then calls the model until it stops calling tools. The message never holds the draft's `draft_sha256`: an edit names the one the model's own latest read or edit returned, so it can't overwrite a person's edit it hasn't read. Each reply's tool calls run through `tools.call` in order, and all their results go back in one user message (a failure, an exception included, as `is_error`). Thinking is adaptive (never off), `tool_choice` is `auto`, each tool has `eager_input_streaming` (`tools.call` validates every input), and a refused call falls back server-side (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). A refusal the whole chain makes, or a reply cut at `max_tokens`, applies nothing.
- **Caching:** the tools (sorted, `cache_control` on the last) and the system block (rules, field types, the layout manifests, the block manifests without their region keys since each layout's `allowed_blocks` says where a block goes, templates; no ids or times) only change with a release, and top-level `cache_control` caches the history. Each call's usage (input, cache writes and reads, output, whether a fallback ran) is logged as `agent call`.
- **History** is in the client's vault, `app.agent_conversations` and `app.agent_messages` (`0009_agent_conversations.sql`), insert-only (triggers refuse update, delete and truncate, and zm_api has no update or delete grant). Replies are stored and sent back unchanged, thinking blocks included, in a `json` column (jsonb would reorder a tool call's input); only the `fallback` marker blocks are left out, which the API ignores. A reply is stored together with the messages it answers and its tool results, so the stored history always replays. A refused or cut call, and one that didn't finish (an unreadable reply asked again, an API error, the console going away mid-reply or while its tools run), keeps only its usage (a stopped one also why: `stop_reason`, and a refusal's `category`). The owner's message (and its draft state) is stored when the turn starts, so it reads back however the turn ends; a message no stored reply answers is left out of every later call, so a refused request isn't sent again. The read-back flags such a message `unanswered` (but for a refused one, and the running turn's), and the dock offers "Send again" for it.
- **Budget:** `AGENT_MAX_TOKENS_PER_CONVERSATION` (default 200,000) caps the tokens a conversation's calls read fresh or wrote (input, cache writes, output; cache reads don't count). `count_tokens` sizes each call first; a call that could pass the budget if nothing were cached and its reply ran to `max_tokens` (64,000) isn't made, and the turn ends with an `error`.
- **Tests** use a fake client with recorded response shapes (`apps/api/tests/agent/test_loop.py`). `uv run pytest -m live` runs one smoke test against the real API with `ANTHROPIC_API_KEY`, by hand only; CI never calls it.
- **The dock (#41)** is the client page's Configure tab in the owner console (`apps/console/src/agent/`). It reads the stream with `fetch` (EventSource can't POST) and types the events by hand (`stream.ts`), since server-sent events aren't in the OpenAPI document. `GET …/agent/conversations` lists the calling owner's conversations, newest first (at most 100); `GET …/conversations/{cid}` reads one back as `{entries, usage, running}`: the owner's and the agent's texts, each done edit's chip `{change_id, summary}` from the stored tool results, refusals, the conversation's usage sums, and whether a turn is running in it (its advisory lock as `pg_locks` shows it, read without taking it; another owner's is a 404). What wasn't stored isn't there: a call's text that didn't finish, and errors. A dropped stream can't be resumed: the turn stops between its tool runs, before the reply and their results are stored (the reply keeps only its usage, which the budget counts), but keeps its done edits, so their chips come from the change log (`conversation_id`), placed before the first row stored after them. The dock keeps the turn locked (Send, Publish) and reads the conversation back, 1 s, 2 s, 4 s… up to 30 s apart and also while offline, until `running` is false, then shows that (a 4xx but 408 and 429, such as an expired session or a 404, stops it: the dock unlocks and shows the error); chips are keyed by `change_id`, so none repeats. A message whose POST fails before a stream starts says it wasn't sent and stays in the box. A chip opens its change's patch from `GET …/draft/changes`; Undo is `POST …/draft/undo` and adds an "Undone" chip (its patch is the revert), or shows the 409. Publish is disabled while `GET …/draft/validate` runs, fails (it says so) or reports a problem (it shows the first), and while a turn runs, as is the Overview tab's Publish (the dock stays mounted there, so the turn goes on; a turn in another tab or another owner's isn't seen); otherwise it confirms and calls `POST …/publish` (the full publish dialog is #43). Preview waits for #42.

## Publish
A client's draft `workflow.json` (`app.drafts` in its vault) becomes a release on its own Operaton (#23):
```bash
uv run zm-api drafts seed --client <slug> --template matrix-retail   # until provisioning seeds drafts (S2-02b)
curl -X POST -H "Authorization: Bearer <owner token>" $API_BASE_URL/owner/clients/<client id>/publish
```
`zm_api.publish.publish()` does the following:
1. Compiles the draft.
2. Creates any missing `<dept>__L<n>` groups.
3. Deploys every BPMN resource plus `zm-release.json` as `<slug>-release`, with duplicate filtering and `deploy-changed-only=false`. An unchanged draft gives back the same deployment and no new version. Any change, even a form label, redeploys every resource as the next version.
4. Records `app.releases` vN (forms, ui, routing vars, process keys) and the `platform.releases` index row.

Running cases keep their version: `release_for_task()` resolves a task's forms through its process definition's deployment.

## Owner dashboard (#47)
`GET /owner/clients/overview` gives, per client, counts and status only, never a name, an email or a form value (a test walks the response schema's keys):
- the provisioning status;
- once the client is ready: its users by status (`app.users`), cases in progress per flow of the active release (Operaton `GET /process-instance/count?processDefinitionKey=<main process>`), the active release's version and time, and its health: Operaton Run's `GET /health` beside `/engine-rest` and `select 1` on the vault, each with its time in ms.

Every client is probed at once, the vault and then the engine each within 3 s, with its own short-lived vault connection and Operaton client, so the resolver's cached ones are never evicted under other requests. Any error while opening or probing, a client that is down, or a ready client without its secrets or Operaton URL turns only its own card red and is logged as a warning under `zm_api.clients`. The answer is kept 60 s per API process, so the probes cost about one extra connection per client a minute, and the console's cards on the clients list refetch it every 15 s.

## Cases and tasks (#26)
The client app works cases and tasks through `/app` (a client user's token; `zm_api.tasks`):

| Endpoint | What it does |
|---|---|
| `POST /app/cases {flow, values}` | Starts a case of any flow on the active release. The user must hold one of the flow's start groups. The values are checked against `start:<flow>`. The code (`MX-0001`) is the Operaton business key. |
| `GET /app/tasks?scope=mine\|team&flow=&case=` | `mine` is the user's tasks plus their groups' unclaimed ones. `team` (level 2 and up) is every task of their departments. |
| `GET /app/tasks/{id}` | The task, its case, its form `{schema, layout}` minus the sections hidden below the user's level, and its `show` values. |
| `POST /app/tasks/{id}/submit {values, outcome?, comment?}` | Needs an `Idempotency-Key` header. Repeating a key returns the first result. |

**Where values go:** only routing variables reach Operaton (D5). A case starts with every routing variable of its flow set to `null`, so a rule always has a value to read. Each submission then sends only the routing values it contains, which leaves earlier values in place. A list or object, or a string over 4000 characters, is sent as `null`. Every value a person enters is stored in `app.submissions` and `app.case_values`.

A form that shows a `user` field assigning a later step must fill it. Fields hidden from a user's level are also kept out of their `show`.

**Submission lifecycle:** a submission is `pending`, then `completed` (Operaton took it), then `committed` (its values are written). Each task has at most one live submission. A submission is `failed` when Operaton answered with its own error, which it sends after rolling back:
- rejected values or a failing rule: the task is unclaimed again;
- the task is gone;
- someone else claimed it, which gives 409.

A timeout, an unreachable engine or a non-JSON answer gives 503, and the submission stays `pending` for the reconciler. A start with no clear answer looks the case up by its code before giving up.

**Reconciler:** the worker (`zm-worker`, or by hand with `zm-worker reconcile`) runs it every minute on every ready client. It settles submissions left `pending`/`completed` for more than 2 minutes:
- a `completed` one gets its values written;
- a `pending` one whose task completed is committed from Operaton's history, without completing it again;
- a `pending` one whose task is still open, and isn't held by someone else, gets one more completion attempt; if the task is still open after that, it is `failed`.

One bad row doesn't stop the pass. The worker needs `PLATFORM_DATABASE_URL` and `SECRETS_ENCRYPTION_KEY`, like the API.

**Provisioning (S2-02a/b):** the worker runs the client steps that the owner console queued in `platform.provisioning_steps` (`zm_worker/provision/`), in `seq` order. It does this in its own thread, every `WORKER_TICK_SECONDS`, so a step that waits minutes never holds up the reconciler. It claims each step with `FOR UPDATE SKIP LOCKED`. Steps 1–5 build the client's Supabase project `<SUPABASE_PROJECT_PREFIX><slug>`:
1. `supabase_project`: create the project, or find the one this client created before, then wait for `ACTIVE_HEALTHY`. A same-named project the client didn't create is never adopted; the step goes `needs_human` instead;
2. `vault_migrations`: apply `infra/supabase/client` through the session pooler;
3. `data_api_off`: expose no schema, then probe with the anon key;
4. `bucket`: create the private bucket `files`;
5. `registry`: write `supabase_ref` and `bucket`.

Steps 6–8 start its Operaton on Railway:

6. `operaton_service`: find or create `operaton-<slug>` from `OPERATON_IMAGE`, with no public domain. It sets `DB_*` (session pooler, TLS), `ZM_ADMIN_USER_*` and `PORT`, and redeploys only when the image or a variable changed, or when an earlier deploy didn't go through.
7. `operaton_health`: wait for `/health` on `operaton-<slug>.railway.internal:8080`, then check that the service account's task query gets 200 and an unauthenticated one 401. It writes `operaton_url`. On a 10-minute timeout, `last_error` holds the newest deployment log lines.
8. `seed_draft`: copy the client's template from `platform.templates` into `app.drafts`, unless a draft exists; the client is then `ready`.

Each step is idempotent, and every generated secret is sealed into `clients.secrets` before it is used. A failing step is retried with backoff (0 s, 10 s, 30 s, 70 s, 150 s), and after 5 failures it is `failed` with `last_error`. A step left `running` by a killed worker is picked up again after a 2-minute lease. A worker whose lease ran out can't overwrite the new owner's result. On shutdown, a waiting step is handed back without using an attempt. Secret values are redacted from `last_error` and the logs. `needs_human` keeps the click path in `last_error`, and the step re-checks every minute. Provisioning stays off until `SUPABASE_ACCESS_TOKEN`, `SUPABASE_ORG_ID` (the org slug) and `SUPABASE_PROJECT_PREFIX` are set too. The prefix has no default, so a test or dev worker can't reach production's `zm-` projects. Steps 6–8 also need `RAILWAY_API_TOKEN` and `OPERATON_IMAGE`; Railway provides the project and environment ids.

For test clients only, `zm-worker deprovision <slug> --yes` deletes `operaton-<slug>` and the Supabase project, marks the client `suspended`, and takes its unfinished steps out of the queue; the worker never claims a suspended client's steps. Nothing calls it automatically.

### Cases read API (#91)
| Endpoint | What it returns |
|---|---|
| `GET /app/cases?flow=&status=open\|completed\|closed` | Every case (newest first, up to 500; when more match, the response has `X-Truncated: true`): `{code, flow, title, status, stages[{key, name, department, state, started_at, ended_at}], fields, open_tasks, my_tasks, created_at, release_version}`; a started stage has `started_at` (when its `m_<stage>` was first set), and only closed cases have `ended_at` (the detail always has both). |
| `GET /app/cases/{code}` | The same, plus stage times, the start form's `sections` as the user sees them, the open `tasks` they can see (`can_act`), the `history` of committed decisions, and `events`, its activity oldest first (#94): `created`, each stage's `stage_started` and `stage_done` or `stage_closed`, and each `submitted` task, with `stage`, `task`, `by` and `at` but never a value. Another client's code is 404. The sections follow the start form of the newer of the active release and the case's own `release_version` (after a rollback, the case's own), filled with the values the case recorded on its release. A renamed field shows its new label, and a removed field still shows a value the case has; a value whose field that form no longer places comes last, in the section `_other`. A field the case's release didn't have has no value and `since: N`, which the shell shows as "added in vN". |

**Stage states** come from Operaton's history of the main process: `case_status` and one `m_<stage>` per stage, set by the compiler. The list makes one variable request for all cases. A closed case also reads its call activities' history, so a stage the close cancelled shows `closed`, not `done`. Field values follow level masking; everyone in the client sees every case (scope rules: #56).

**Department overview (#94):** a `standard` page's `hero` takes `kpi_strip`: 2 to 4 tiles (`open_cases`, `in_stage`, `awaiting_me`, `overdue`, `done`, each optionally narrowed to a `stage`) counted in the browser from `GET /app/cases`. Past its 500 cases, the tiles show "N+" (at least N) and `case_table` says it shows the newest 500; exact counts come with #55. Picking a tile puts `?kpi=` in the URL, and the page's `case_table` then shows exactly the cases it counts. An optional `stages[].sla_days` in `workflow.json` makes a case `overdue` once it has spent more whole days in that stage, shown as a pill in `case_table`. The sidebar shows the user's open tasks per department page (`GET /app/tasks?scope=mine`, one request; a dot when collapsed), and the `case` page's Activity tab is `case_activity`, drawn from `events`.

## Isolation and compatibility (#31)
**Two local clients**, each with its own vault, engine and credentials (`ZM_ISO_*` for qsr-local):
- `pilot-local`: Matrix Retail, on the Supabase CLI vault and operaton-pilot;
- `qsr-local`: QSR, on pg-iso and operaton-iso.

`zm-worker provision-local <slug> --dsn <vault superuser DSN> --operaton-url <engine>/engine-rest` registers a client on local resources. It runs the provisioning steps minus the cloud calls: migrations, the registry row, seed-draft. `pytest -m isolation` (`tests/isolation/`, CI job `zm-isolation`) does this for both clients, publishes v1, and checks:
1. the other client's task ids, cases and UI are out of reach;
2. a forged token is refused;
3. each engine holds only its own client's processes;
4. database credentials don't work across vaults;
5. a user can't sign in with the other client's workspace code;
6. a client token can't use the owner console;
7. a service account can't use the other client's engine.

A broken resolver must trip check 1 (the negative control).

**`zm-compat --fixtures`** validates and compiles every template and every synthetic release in `tests/compat/`. Validation checks each page, and the theme preset and accent, against `packages/zm-blocks/catalogue.json`, so a template can place only blocks that are built and pick only a preset and accent the shell has. That file is generated from the shell's registry and `@zm/ui`'s theme: when a manifest, preset or accent changes, `pnpm vitest run -u packages/zm-blocks/src/catalogue.test.ts` rewrites it, and `pnpm test` fails while it is stale.

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
Project `zm-platform`, environments `test` (tracks `main`) and `production` (tracks `release`), region `asia-southeast1-eqsg3a` (D2, D16; `docs/ENVIRONMENTS.md`). The two services are defined in **`.railway/railway.ts`**, using Railway Infrastructure as Code. Config as code (`railway.json`) is deprecated, and new services can't opt in to it.

What the file sets for each service:
- **Source:** the GitHub repo, branch `main` in `test` and `release` in `production` (`ctx.isEnvironment("production")`), with root directory `platform`. Apply the file once per environment (`npx @railway/cli environment <name>`, then `config plan` / `config apply`; production only from a clean `origin/release` checkout). The Dockerfiles use `platform/` as their build context, the same as compose, and `dockerfilePath` is relative to it.
- **Watch patterns:** repo-absolute, so only `platform/apps/<svc>/**`, `platform/packages/**`, `pyproject.toml` and `uv.lock` (plus `infra/supabase/**` for the worker and `infra/supabase/client/**` for the api) trigger a deploy. Operaton-only merges deploy nothing.
- **Deploy:** one replica in `asia-southeast1-eqsg3a`; restart on failure is Railway's default, so the file doesn't set it.
- **api only:** healthcheck `/healthz`. The api listens on `$PORT`, injected by Railway. Pre-deploy runs the control-plane migrations (`alembic -c apps/api/alembic.ini -x db=platform upgrade head`, S1-04), then every ready cloud client's vault migrations (`python -m zm_worker vault-migrate --ready`), so a new version never serves a vault without the columns it reads. A failed migration stops the deploy and the running version stays. Clients registered by `provision-local` are skipped: `scripts/dev-up.sh` migrates local vaults. Like provisioning, it also sets each vault's `zm_engine` and `zm_api` passwords to the registry's, so every api deploy reverts a role password changed only in the vault: rotate them through the registry.
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
6. HUMAN: Project → Settings → Tokens → create token `zm-worker`, then set it as `RAILWAY_API_TOKEN` on `worker` in the dashboard. The worker sends a project token as `Project-Access-Token`, and it only works in its own environment. If `serviceCreate` refuses it, use a workspace token instead and set `RAILWAY_API_TOKEN_TYPE=bearer`.

Later changes: edit `.railway/railway.ts` in a PR, then run `config plan` and `config apply` after merge. Run `config plan --detailed-exit-code` to check for drift.

The file is the named partial `platform` (#25): it owns only `api` and `worker`, so `config apply` leaves the worker's `operaton-<slug>` services alone. A whole-project file deletes every service it doesn't declare. `railway config partials list` shows who owns what.
