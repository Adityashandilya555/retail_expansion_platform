from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from zm_api import errors, logs, settings
from zm_api.context import PlatformRegistry, Resolver


def create_app(resolver: Resolver | None = None) -> FastAPI:
    """`resolver` defaults to the registry in PLATFORM_DATABASE_URL; tests pass fake rows."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.resolver.aclose()

    app = FastAPI(title="Z-Matrix Platform API", lifespan=lifespan, responses=errors.RESPONSES)
    app.state.resolver = resolver or Resolver(PlatformRegistry())
    errors.install(app)
    # Outermost last: RequestLog → CORS → CrashGuard → routes. Every request is logged with an
    # X-Request-Id, and even a crash's 500 passes through CORS, so the browser can read it.
    app.add_middleware(errors.CrashGuard)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(logs.RequestLogMiddleware)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


logs.configure(settings.log_level())
app = create_app()
