from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from zm_api import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Z-Matrix Platform API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


app = create_app()
