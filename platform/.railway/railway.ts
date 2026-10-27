// Railway Infrastructure as Code for the Z-Matrix platform services (#13, S1-18a).
// Config as code (railway.json) is deprecated and new services cannot opt in, so this file is the
// source of truth for the `api` and `worker` services in both environments (D16, #61): `test` tracks
// `main`, `production` tracks `release` (promoted from main by PR; docs/ENVIRONMENTS.md). Variables
// marked preserve() keep each environment's own values. Run from platform/ after `railway link`,
// once per environment, from the branch that environment tracks: production only from a clean
// checkout of origin/release (applying from main would push unpromoted config), test from main:
//   npx @railway/cli environment test   # or production
//   npx @railway/cli config plan    # read-only diff against the linked environment
//   npx @railway/cli config apply   # applies after confirmation
// Per-client `operaton-<slug>` services are created by the worker through the Railway API (S2-02b),
// not here. Generated *.up.railway.app domains are not managed by IaC: `railway domain --service api`.
import { defineRailway, github, preserve, project, service } from "railway/iac";

// A named partial owns only what this file declares. As a whole-project file, `config apply` would
// delete every service missing from it, i.e. the worker's operaton-<slug> services (S2-02b, #25).
export const partial = "platform";

const REPO = "Adityashandilya555/retail_expansion_platform";
const REGION = "asia-southeast1-eqsg3a"; // D2

// The Dockerfiles use platform/ as build context (as docker-compose.yml does), so platform/ is the
// root directory and dockerfilePath is relative to it. Watch patterns are repo-absolute: Operaton-only
// commits redeploy nothing. Restart policy is Railway's default (on failure), so it isn't set: an
// explicit ON_FAILURE is stored as null and would show as permanent drift in `config plan`.
const shared = ["/platform/packages/**", "/platform/pyproject.toml", "/platform/uv.lock"];

export default defineRailway((ctx) => {
  // what each environment deploys: test is every merge to main; production only what was promoted.
  // Names are compared exactly; an unknown or missing one (a renamed environment, `config pull`
  // without a link) fails rather than quietly pointing production back at main.
  const branch = ctx.isEnvironment("production")
    ? "release"
    : ctx.isEnvironment("test")
      ? "main"
      : undefined;
  if (branch === undefined) {
    throw new Error(
      `railway.ts: unknown environment ${ctx.environment ?? "(none)"}; expected test or production`,
    );
  }
  const source = github(REPO, { branch, rootDirectory: "platform" });

  const api = service("api", {
    source,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "apps/api/Dockerfile",
      watchPatterns: ["/platform/apps/api/**", ...shared],
    },
    healthcheck: "/healthz",
    // Control-plane migrations (S1-04) on PLATFORM_DATABASE_URL before each deploy; WORKDIR is /app.
    preDeploy: "alembic -c apps/api/alembic.ini -x db=platform upgrade head",
    replicas: { [REGION]: 1 },
    env: {
      LOG_LEVEL: "info",
      // Comma-separated Vercel domains of zm-web and zm-console, set with #14.
      CORS_ALLOWED_ORIGINS: preserve(),
    },
  });

  const worker = service("worker", {
    source,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "apps/worker/Dockerfile",
      watchPatterns: ["/platform/apps/worker/**", "/platform/infra/supabase/**", ...shared],
    },
    replicas: { [REGION]: 1 },
    env: {
      LOG_LEVEL: "info",
      WORKER_TICK_SECONDS: "30",
      RAILWAY_REGION: REGION,
      // Project token `zm-worker` (HUMAN step); RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID are
      // provided by Railway at runtime. A project token goes in the Project-Access-Token header;
      // set RAILWAY_API_TOKEN_TYPE=bearer for an account or workspace token instead.
      RAILWAY_API_TOKEN: preserve(),
      RAILWAY_API_TOKEN_TYPE: preserve(), // the worker defaults to project when unset
      OPERATON_IMAGE: "ghcr.io/adityashandilya555/zm-operaton:2.1.5-zm.1",
      // Supabase Management API token for provisioning client projects (S2-02a); set in the dashboard.
      SUPABASE_ACCESS_TOKEN: preserve(),
      // Provisioning (S2-02a/b, #24/#25). The prefix differs per environment (zm-test- in test, D16).
      SUPABASE_ORG_ID: preserve(),
      SUPABASE_PROJECT_PREFIX: preserve(),
      SUPABASE_REGION: "ap-southeast-1",
      SUPABASE_CLIENT_INSTANCE_SIZE: "micro",
      PLATFORM_DATABASE_URL: preserve(),
      SECRETS_ENCRYPTION_KEY: preserve(),
    },
  });

  return project("zm-platform", { resources: [api, worker] });
});
