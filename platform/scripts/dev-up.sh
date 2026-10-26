#!/usr/bin/env bash
# Start the local Z-Matrix stack: the Supabase CLI vault (pilot-local) first, then docker compose.
set -euo pipefail
cd "$(dirname "$0")/.."

for tool in docker uv; do
  command -v "$tool" >/dev/null || { echo "missing: $tool (see platform/README.md)" >&2; exit 1; }
done
if command -v supabase >/dev/null; then supabase=(supabase); else supabase=(npx -y supabase); fi

export ZM_ENGINE_DB_PASSWORD="${ZM_ENGINE_DB_PASSWORD:-zm-engine-local}"
export ZM_API_DB_PASSWORD="${ZM_API_DB_PASSWORD:-zm-api-local}"
# qsr-local (pg-iso, operaton-iso) has its own credentials (#31)
ZM_ISO_ENGINE_DB_PASSWORD="${ZM_ISO_ENGINE_DB_PASSWORD:-zm-engine-iso-local}"
ZM_ISO_API_DB_PASSWORD="${ZM_ISO_API_DB_PASSWORD:-zm-api-iso-local}"
export ZM_ISO_ENGINE_DB_PASSWORD ZM_ISO_API_DB_PASSWORD

"${supabase[@]}" --workdir infra start
docker compose up -d --wait pg-iso
uv run --no-build zm-worker vault-migrate --dsn postgresql://postgres:postgres@localhost:54322/postgres
ZM_ENGINE_DB_PASSWORD="$ZM_ISO_ENGINE_DB_PASSWORD" ZM_API_DB_PASSWORD="$ZM_ISO_API_DB_PASSWORD" \
  uv run --no-build zm-worker vault-migrate --dsn "postgresql://postgres:postgres@localhost:${PG_ISO_PORT:-5434}/postgres"
docker compose up -d --build --wait
# an engine created before #31 still has the shared service password: rotate it (idempotent)
uv run --no-build python scripts/local_iso_password.py \
  || echo "warning: operaton-iso keeps its old service password; pytest -m isolation will say so" >&2

cat <<'URLS'

Z-Matrix local stack is up:
  api             http://localhost:8000/healthz
  operaton-pilot  http://localhost:8080/engine-rest/engine   (DB: Supabase CLI, :54322)
  operaton-iso    http://localhost:8081/engine-rest/engine   (DB: pg-iso, :5434)
  pg-platform     postgresql://postgres:postgres@localhost:5433/postgres
Smoke test: uv run pytest -m integration
URLS
