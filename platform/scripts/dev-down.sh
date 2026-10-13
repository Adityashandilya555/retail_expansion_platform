#!/usr/bin/env bash
# Stop the local Z-Matrix stack. Data volumes are kept; add `-v` to docker compose down to drop them.
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v supabase >/dev/null; then supabase=(supabase); else supabase=(npx -y supabase); fi
docker compose down
"${supabase[@]}" --workdir infra stop
