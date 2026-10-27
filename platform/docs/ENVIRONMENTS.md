# Environments (D16, #61)

Every change is tried on **test** before it reaches **production**:
- `main` deploys to test automatically;
- production deploys from `release`, which only moves by a reviewed PR from `main`.

Both environments run the same images and configuration, with different data and secrets.

| | Local | Test | Production |
|---|---|---|---|
| Branch | your working tree | `main` | `release` |
| API | http://localhost:8000 | TBD: `railway domain --service api` in Railway `test` | the production `api` domain (#13) |
| Client app (`zm-web`) | http://localhost:5173 | `https://zm-web-git-main-<scope>.vercel.app` (Vercel Preview of `main`) | the `zm-web` production domain (#14) |
| Owner console (`zm-console`) | http://localhost:5174 | `https://zm-console-git-main-<scope>.vercel.app` | the `zm-console` production domain (#14) |
| Supabase control plane | `pg-platform` (compose) | `zm-platform-test` | `zm-platform` (#10) |
| Client vaults | Supabase CLI (`pilot-local`), `pg-iso` (`qsr-local`) | `zm-test-<slug>` (`SUPABASE_PROJECT_PREFIX=zm-test-`) | `zm-<slug>` (`SUPABASE_PROJECT_PREFIX=zm-`) |
| Railway environment | — (docker compose) | `test` in project `zm-platform` | `production` in project `zm-platform` |
| Client engines | `operaton-pilot`, `operaton-iso` | `operaton-<slug>` in Railway `test` only | `operaton-<slug>` in Railway `production` only |
| `ENVIRONMENT` | `local` | `test` | `production` |

Replace each TBD with the real URL when it is created (checklist step 6). URLs are not secrets; tokens and passwords never go here.

## Rules
- **Nothing is shared between test and production:** not `JWT_SECRET`, not `SECRETS_ENCRYPTION_KEY`, and not a Supabase project. A token from one is refused by the other (401). A sealed secret from one can't be opened by the other.
- **Same names, different values:** the variables are the same in both Railway environments. In `.railway/railway.ts` they are `preserve()`, so each environment keeps its own values.
- **Branch tracking comes from the file:** `.railway/railway.ts` sets each environment's branch (`ctx.isEnvironment("production") ? "release" : "main"`). Apply it once per environment.
- **Each worker provisions only its own environment:** Railway provides `RAILWAY_ENVIRONMENT_ID`, so a test client's `operaton-<slug>` is created only in `test`.
- **Demo rehearsals run on test first** (S1-16, S2-20).

## Promotion: test → production
1. Merge to `main` as usual. Test redeploys: Railway `api`/`worker` for their watch paths, and the Vercel Preview of `main`.
2. Check the change on test (the test URLs above).
3. Open a PR **`main` → `release`** titled `release: <date>`, listing the PRs it carries.
4. It needs 1 approving review (ruleset `release`). Merge it with a **merge commit**, never squash or rebase, so `release` keeps `main`'s history.
5. Production redeploys from `release`: Railway `api` and `worker`, and the Vercel production builds of `zm-web` and `zm-console`.
6. Check production's `/healthz` and the client app.

**Hotfix:** fix on `main` (PR), check on test, then promote as above. Nothing is pushed to `release` directly.

## Setting it up (checklist for #61)
These are the cloud steps; the repo side is in PR #61. Secrets are typed into the dashboards by a person, never into git, issues, PRs or chat.

1. **Billing (HUMAN):** approve the extra Supabase compute for the test projects (supabase.com → Organization → Billing). The org is on Pro (D18).
2. **Supabase test control plane** (supabase MCP):
   1. Create `zm-platform-test` in the same org and region, as for `zm-platform` (S1-03a, #10).
   2. Data API off, no anon grants, schema `platform`.
   3. Run `alembic -c apps/api/alembic.ini -x db=platform upgrade head` with `PLATFORM_DATABASE_URL` pointing at it (session pooler, port 5432).
   4. Run `get_advisors` and fix anything it reports.
3. **The `release` branch and its ruleset** (do this before production tracks it):
   ```bash
   git push origin origin/main:refs/heads/release
   gh api -X POST repos/Adityashandilya555/retail_expansion_platform/rulesets --input platform/infra/github/ruleset-release.json
   ```
4. **Railway `test` environment** (railway MCP or dashboard):
   1. Duplicate `production` as `test`.
   2. In `test`, set new values for:
      - `JWT_SECRET` and `SECRETS_ENCRYPTION_KEY`: new ones, never production's;
      - `PLATFORM_DATABASE_URL`: `zm-platform-test`'s session pooler;
      - `SUPABASE_PLATFORM_*`;
      - `SUPABASE_PROJECT_PREFIX=zm-test-`;
      - `ENVIRONMENT=test`;
      - `CORS_ALLOWED_ORIGINS`: the two test frontend URLs only;
      - `API_BASE_URL`: the test API domain;
      - `SUPABASE_ACCESS_TOKEN`, `SUPABASE_ORG_ID` and `RAILWAY_API_TOKEN`: a project token for the same project.
   3. Run `railway environment test`, then `npx @railway/cli config plan`. The plan should show the services tracking `main`. Then `config apply`.
   4. Run `railway domain --service api` in `test`, and put the domain in the table above.
5. **Railway `production`:** run `railway environment production`, then `npx @railway/cli config plan`. The only change should be the source branch, `main` → `release`. Then `config apply`. Do this after step 3, so the branch exists.
6. **Vercel** (vercel MCP or dashboard), for both `zm-web` and `zm-console`:
   1. Settings → Git → Production Branch = `release`.
   2. Settings → Environment Variables → Preview: `VITE_API_BASE_URL` = the test API, `ENVIRONMENT=test`.
   3. Redeploy the `main` Preview, then fill in the table.
7. **Verify, and record the results in #61:**
   - **Branch tracking:** a merge to `main` touching `platform/apps/api/**` deploys test's `api` only (`list-deployments` per environment). The first `main` → `release` PR redeploys production's `api` and `worker`, and rebuilds the Vercel production frontends.
   - **Health:** `curl https://<test api>/healthz` and `curl https://<production api>/healthz` both answer `{"ok": true}`.
   - **Token swap:** a production access token sent to `GET <test api>/app/me` gets 401.
   - **CORS:** a request from a production frontend origin gets no `Access-Control-Allow-Origin` from the test API; a test origin does.
   - **Provisioning:** a client created in the test console gets the Supabase project `zm-test-<slug>`, and `operaton-<slug>` exists only in Railway `test`.
