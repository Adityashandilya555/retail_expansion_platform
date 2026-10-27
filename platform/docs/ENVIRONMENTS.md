# Environments (D16, #61)

Every change is tried on **test** before it reaches **production**:
- `main` deploys to test automatically;
- production deploys from `release`, which only moves by a reviewed PR from `main`.

Both environments run the same images and configuration, with different data and secrets.

| | Local | Test | Production |
|---|---|---|---|
| Branch | your working tree | `main` | `release` |
| API | http://localhost:8000 | TBD: `npx @railway/cli domain --service api` in Railway `test` | the production `api` domain (#13) |
| Client app (`zm-web`) | http://localhost:5173 | `https://zm-web-git-main-<scope>.vercel.app` (Vercel Preview of `main`) | the `zm-web` production domain (#14) |
| Owner console (`zm-console`) | http://localhost:5174 | `https://zm-console-git-main-<scope>.vercel.app` | the `zm-console` production domain (#14) |
| Supabase control plane | `pg-platform` (compose) | `zm-platform-test` | `zm-platform` (#10) |
| Client vaults | Supabase CLI (`pilot-local`), `pg-iso` (`qsr-local`) | `zm-test-<slug>` (`SUPABASE_PROJECT_PREFIX=zm-test-`) | `zm-<slug>` (`SUPABASE_PROJECT_PREFIX=zm-`) |
| Railway environment | — (docker compose) | `test` in project `zm-platform` | `production` in project `zm-platform` |
| Client engines | `operaton-pilot`, `operaton-iso` | `operaton-<slug>` in Railway `test` only | `operaton-<slug>` in Railway `production` only |
| `ENVIRONMENT` (Railway; informational, nothing reads it yet) | `local` | `test` | `production` |

Replace each TBD with the real URL when it is created (checklist steps 4.4 and 6). URLs are not secrets; tokens and passwords never go here.

## Rules
- **Nothing is shared between test and production:** not `JWT_SECRET`, not `SECRETS_ENCRYPTION_KEY`, and not a Supabase project. A token from one is refused by the other (401). A sealed secret from one can't be opened by the other.
- **Same names, different values:** the variables are the same in both Railway environments. In `.railway/railway.ts` they are `preserve()`, so each environment keeps its own values.
- **Branch tracking comes from the file:** `.railway/railway.ts` sets each environment's branch (`ctx.isEnvironment("production") ? "release" : "main"`; it compares the environment **name**, case-sensitive). Apply it once per environment, **from the branch that environment tracks**:
  - production: only from a clean checkout of `origin/release`. Applying from `main` would push unpromoted config, such as `OPERATON_IMAGE`, `preDeploy` or watch patterns;
  - test: from `main`.
- **Promotion needs a second person.** `release` requires 1 approving review, and GitHub doesn't count a PR author's own approval. Nobody can bypass it, not even the repo admin. So every promotion and hotfix needs another collaborator's review. That's intended: it's the "checked on test by someone else" step.
- **Each worker provisions only its own environment:** Railway provides `RAILWAY_ENVIRONMENT_ID`, and the worker looks a client's engine up in its own environment only. A test client's `operaton-<slug>` is created only in `test`.
- **A client slug belongs to one environment.** Railway services belong to the project, and only their instances are per environment. So a slug already used in the other environment stops provisioning with a "needs human" message instead of sharing the service. Rehearse demos on test under their own slugs, and don't give production clients slugs starting with `test-`: production's `zm-` + `test-x` is the same Supabase name as test's `zm-test-` + `x`.
- **Demo rehearsals run on test first** (S1-16, S2-20).

## Promotion: test → production
1. Merge to `main` as usual. Test redeploys: Railway `api`/`worker` for their watch paths, and the Vercel Preview of `main`.
2. Check the change on test (the test URLs above).
3. Open a PR **`main` → `release`** titled `release: <date>`, listing the PRs it carries.
4. It needs 1 approving review from someone other than the author (ruleset `release`). The ruleset allows only a **merge commit**, so `release` keeps `main`'s history. An approval stays valid if more PRs merge to `main` meanwhile; the promotion then carries them too, so re-check the PR's commit list before merging.
5. Production redeploys from `release`: Railway `api` and `worker`, and the Vercel production builds of `zm-web` and `zm-console`.
6. Check production's `/healthz` and the client app.

**Hotfix:** fix on `main` (PR), check on test, then promote as above. Nothing is pushed to `release` directly.

## Setting it up (checklist for #61)
These are the cloud steps; the repo side is in PR #131. Secrets are typed into the dashboards by a person, never into git, issues, PRs or chat.

1. **Billing (HUMAN):** approve the extra Supabase compute for the test projects (supabase.com → Organization → Billing). The org is on Pro (D18).
2. **Supabase test control plane** (supabase MCP):
   1. Create `zm-platform-test` in the same org and region, as for `zm-platform` (S1-03a, #10).
   2. Data API off, no anon grants, schema `platform`.
   3. Run `alembic -c apps/api/alembic.ini -x db=platform upgrade head` with `PLATFORM_DATABASE_URL` pointing at it (session pooler, port 5432).
   4. Run `get_advisors` and fix anything it reports.
3. **The `release` branch and its ruleset.** Do this after PR #131 is merged, so `release`'s `railway.ts` already picks branches per environment, and **right before step 5**. Production keeps deploying `main` until step 5, so a merge to `main` in between would put production ahead of `release`.
   ```bash
   git fetch origin
   git push origin origin/main:refs/heads/release
   gh api -X POST repos/Adityashandilya555/retail_expansion_platform/rulesets --input platform/infra/github/ruleset-release.json
   ```
4. **Railway `test` environment** (railway MCP or dashboard). A duplicate starts with **production's** variables: its database, its keys, `SUPABASE_PROJECT_PREFIX=zm-`. A test deploy before they are replaced would run `main`'s migrations against the production control plane, and a test worker would take production's provisioning jobs. So:
   1. Duplicate `production` as `test` with the changes **staged, not deployed**. If a deploy starts anyway, remove the `api` and `worker` services from `test` until step 4.2 is done.
   2. Before deploying anything in `test`, **delete every `operaton-*` service from `test`** (Railway → zm-platform → `test` → the service → Settings → Delete service, for this environment only). The duplicate copies each client engine with that client's production database credentials. Two engines on one database would run the same timers and jobs. Test engines are created only by the test worker.
   3. In `test`, replace every value before the first deploy:
      - `JWT_SECRET` and `SECRETS_ENCRYPTION_KEY`: new ones, never production's;
      - `PLATFORM_DATABASE_URL` (session pooler) and `SUPABASE_PLATFORM_*`: `zm-platform-test`'s;
      - `SUPABASE_PROJECT_PREFIX=zm-test-`;
      - `CORS_ALLOWED_ORIGINS`: the two test frontend URLs only;
      - `ENVIRONMENT=test`;
      - `RAILWAY_API_TOKEN`: a **new project token created for the `test` environment**. A project token is scoped to one environment, so production's can't create services in `test`. `RAILWAY_API_TOKEN_TYPE` is unchanged.
      - `SUPABASE_ACCESS_TOKEN` and `SUPABASE_ORG_ID`: the same org values as production (one Pro org, D18). The prefix keeps the projects apart.
      - Third-party keys (`ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `SENTRY_*`), once used: separate test keys are recommended but optional.
   4. From `main`, run `npx @railway/cli environment test`, then `npx @railway/cli config plan`. The plan should show the services tracking `main`, and **no service created or deleted**. If it shows creates, the duplicated environment doesn't carry the `platform` partial's ownership: transfer it with `npx @railway/cli config partials` first. Then `npx @railway/cli config apply`.
   5. Run `npx @railway/cli domain --service api` in `test`. Put the domain in the table above, and set `API_BASE_URL` to it in `test`.
   6. Seed the test control plane, which otherwise has no templates (`seed_draft` fails) and no owner (nobody can sign in to the test console):
      ```bash
      npx @railway/cli run --environment test --service api uv run zm-api templates sync
      npx @railway/cli run --environment test --service api uv run zm-api owners create --email <you> --name "<You>"
      ```
      `owners create` asks for the password in the terminal, so it never goes on the command line.
5. **Railway `production`:** do this right after step 3, so the branch exists, and **from a clean checkout of `origin/release`**.
   1. Check that `git rev-parse origin/release` is the commit of production's active `api` and `worker` deployments. If production has moved on since step 3, promote `main` → `release` first, so the switch doesn't redeploy older code against a newer schema.
   2. Run `npx @railway/cli environment production`, then `npx @railway/cli config plan`. The only change should be the source branch, `main` → `release`. Then `npx @railway/cli config apply`.
6. **Vercel** (vercel MCP or dashboard), for both `zm-web` and `zm-console`:
   1. Settings → Git → Production Branch = `release`.
   2. Settings → Environment Variables: `VITE_API_BASE_URL` = the test API, scoped to **Preview, branch `main`**. Scoped to all of Preview, every PR preview would call the test API and be refused by its CORS (which lists only the `git-main` URLs). Other PR previews then fall back to `http://localhost:8000`, which is intended: they're for looking at the UI. Don't widen the scope to "fix" that.
   3. Settings → Deployment Protection: Pro protects preview URLs with Vercel Authentication by default. Allow the `main` branch URL (or give test a custom domain), so rehearsals and testers without a Vercel seat can open it.
   4. Redeploy the `main` Preview, then fill in the table.
7. **Verify, and record the results in #61:**
   - **Branch tracking:** a merge to `main` touching `platform/apps/api/**` deploys test's `api` only (`list-deployments` per environment). The first `main` → `release` PR redeploys production's `api` and `worker`, and rebuilds the Vercel production frontends.
   - **Health:** `curl https://<test api>/healthz` and `curl https://<production api>/healthz` both answer `{"ok": true}`.
   - **Token swap:** a production access token sent to the test API gets 401: a client token to `GET <test api>/app/me`, or a production owner token to `GET <test api>/owner/me`.
   - **CORS:** a request from a production frontend origin gets no `Access-Control-Allow-Origin` from the test API; a test origin does.
   - **Provisioning:** a client created in the test console gets the Supabase project `zm-test-<slug>`, and `operaton-<slug>` exists only in Railway `test`.
