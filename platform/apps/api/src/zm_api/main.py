from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from zm_api import errors, logs, settings
from zm_api.context import Resolver, platform_lookup


def create_app(resolver: Resolver | None = None) -> FastAPI:
    """`resolver` defaults to the registry in PLATFORM_DATABASE_URL; tests pass fake rows."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.resolver.aclose()

    app = FastAPI(title="Z-Matrix Platform API", lifespan=lifespan)
    app.state.resolver = resolver or Resolver(platform_lookup())
    errors.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(logs.RequestLogMiddleware)  # added last = outermost

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


logs.configure(settings.log_level())
app = create_app()
