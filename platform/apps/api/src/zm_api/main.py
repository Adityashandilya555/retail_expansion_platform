from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from zm_api import auth, clients, errors, logs, publish, settings, tasks_routes, ui
from zm_api.context import PlatformRegistry, Resolver


def create_app(resolver: Resolver | None = None) -> FastAPI:
    """`resolver` defaults to the registry in PLATFORM_DATABASE_URL; tests pass fake rows."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.resolver.aclose()
        await app.state.platform.aclose()

    app = FastAPI(title="Z-Matrix Platform API", lifespan=lifespan, responses=errors.RESPONSES)
    app.state.platform = PlatformRegistry()  # client registry + owner tables
    app.state.resolver = resolver or Resolver(app.state.platform)
    app.state.workspace_limit = auth.RateLimit(10, 60.0)  # POST /auth/workspace: 10/min/IP
    errors.install(app)
    for router in (
        auth.router,
        auth.app_routes,
        auth.owner_auth,
        auth.owner_routes,
        clients.routes,
        publish.routes,
        tasks_routes.routes,
        ui.routes,
    ):
        app.include_router(router)
    # Outermost last: CORS → RequestLog → CrashGuard → routes. CORS headers reach every response,
    # a crash's 500 too, so the browser can read it. CORS answers preflights itself, so those
    # aren't logged; every request that reaches the app gets one log line and an X-Request-Id.
    app.add_middleware(errors.CrashGuard)
    app.add_middleware(logs.RequestLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


logs.configure(settings.log_level())
app = create_app()
