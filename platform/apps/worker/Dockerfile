# Build context: platform/ (docker compose sets it; by hand: docker build -f apps/worker/Dockerfile .)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app
# The whole uv workspace is copied because --frozen checks every member against uv.lock.
COPY pyproject.toml uv.lock ./
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY packages/compiler packages/compiler
COPY packages/schema packages/schema
RUN uv sync --frozen --no-dev --package zm-worker
RUN useradd --system --no-create-home zm
USER zm
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "zm_worker"]
