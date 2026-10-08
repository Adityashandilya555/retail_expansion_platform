---
description: Read-only health check of the pilot client's infrastructure
---
Read-only. Do not change anything.
- Supabase MCP: project status, schemas `platform`/`app`/`operaton` exist, Data API exposure, `get_advisors` (security + performance), bucket privacy.
- Railway MCP: services `api`, `worker`, `operaton-pilot` — status, last deploy, pinned Operaton image tag, private networking, recent errors in logs.
- Operaton: `GET $OPERATON_URL/engine` and `/deployment?sortBy=deploymentTime&sortOrder=desc&maxResults=3` through the API health endpoint if direct access isn't available.
- Vercel MCP: latest production deployment of `platform/apps/web`, build status, env var names present (not values).
Report a table: component · status · issue found · suggested fix (as a draft issue title).
