#!/usr/bin/env bash
# Start the local Z-Matrix stack: the Supabase CLI vault (pilot-local) first, then docker compose.
set -euo pipefail
cd "$(dirname "$0")/.."

for tool in docker uv; do
  command -v "$tool" >/dev/null || { echo "missing: $tool (see platform/README.md)" >&2; exit 1; }
done
if command -v supabase >/dev/null; then supabase=(supabase); else supabase=(npx -y supabase); fi

"${supabase[@]}" --workdir infra start
docker compose up -d --build --wait

cat <<'URLS'

Z-Matrix local stack is up:
  api             http://localhost:8000/healthz
  operaton-pilot  http://localhost:8080/engine-rest/engine   (DB: Supabase CLI, :54322)
  operaton-iso    http://localhost:8081/engine-rest/engine   (DB: pg-iso, :5434)
  pg-platform     postgresql://postgres:postgres@localhost:5433/postgres
Smoke test: uv run pytest -m integration
URLS
