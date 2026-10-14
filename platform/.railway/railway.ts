// Railway Infrastructure as Code for the Z-Matrix platform services (#13, S1-18a).
// Config as code (railway.json) is deprecated and new services cannot opt in, so this file is the
// source of truth for the `api` and `worker` services. Run from platform/ after `railway link`:
//   npx @railway/cli config plan    # read-only diff against the linked environment
//   npx @railway/cli config apply   # applies after confirmation
// Per-client `operaton-<slug>` services are created by the worker through the Railway API (S2-02b),
// not here. Generated *.up.railway.app domains are not managed by IaC: `railway domain --service api`.
import { defineRailway, github, preserve, project, service } from "railway/iac";

const REPO = "Adityashandilya555/retail_expansion_platform";
const REGION = "asia-southeast1-eqsg3a"; // D2

// The Dockerfiles use platform/ as build context (as docker-compose.yml does), so platform/ is the
// root directory and dockerfilePath is relative to it. Watch patterns are repo-absolute: Operaton-only
// commits redeploy nothing.
const source = github(REPO, { branch: "main", rootDirectory: "platform" });
const shared = ["/platform/packages/**", "/platform/pyproject.toml", "/platform/uv.lock"];

export default defineRailway(() => {
  const api = service("api", {
    source,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "apps/api/Dockerfile",
      watchPatterns: ["/platform/apps/api/**", ...shared],
    },
    healthcheck: "/healthz",
    replicas: { [REGION]: 1 },
    deploy: { restartPolicyType: "ON_FAILURE" },
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
    deploy: { restartPolicyType: "ON_FAILURE" },
    env: {
      LOG_LEVEL: "info",
      WORKER_TICK_SECONDS: "30",
      RAILWAY_REGION: REGION,
      // Project token `zm-worker` (HUMAN step); RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID are
      // provided by Railway at runtime.
      RAILWAY_API_TOKEN: preserve(),
    },
  });

  return project("zm-platform", { resources: [api, worker] });
});
