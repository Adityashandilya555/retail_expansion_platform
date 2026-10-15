import os


def cors_allowed_origins() -> list[str]:
    """`CORS_ALLOWED_ORIGINS`: comma-separated origins (client app, owner console); empty = none."""
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
