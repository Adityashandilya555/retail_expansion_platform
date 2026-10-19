import os


def cors_allowed_origins() -> list[str]:
    """`CORS_ALLOWED_ORIGINS`: comma-separated origins (client app, owner console); empty = none."""
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"set {name}")
    return value


def jwt_secret() -> str:
    """`JWT_SECRET`: HS256 key for access tokens (#22 issues them, #21 checks them)."""
    return _required("JWT_SECRET")


def platform_database_url() -> str:
    """`PLATFORM_DATABASE_URL`: the control-plane database holding the client registry."""
    return _required("PLATFORM_DATABASE_URL")


def log_level() -> str:
    return os.environ.get("LOG_LEVEL", "info")
