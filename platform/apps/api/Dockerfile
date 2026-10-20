# Build context: platform/ (docker compose sets it; by hand: docker build -f apps/api/Dockerfile .)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app
# The whole uv workspace is copied because --frozen checks every member against uv.lock.
COPY pyproject.toml uv.lock ./
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY packages/compiler packages/compiler
COPY packages/schema packages/schema
# Wheels only (--no-build): no setup/build-backend code runs. Our own code is not installed;
# it runs from source via PYTHONPATH.
RUN uv sync --frozen --no-dev --no-build --no-install-workspace --package zm-api
RUN useradd --system --no-create-home zm
USER zm
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app/apps/api/src"
EXPOSE 8000
# Railway injects $PORT; compose and local runs use 8000.
# --no-access-log: zm_api.logs writes the one JSON line per request (#21).
CMD ["sh", "-c", "exec uvicorn zm_api.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
