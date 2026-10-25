"""DSNs for psycopg: settings hold SQLAlchemy URLs (postgresql+asyncpg://…); libpq wants plain."""

from sqlalchemy.engine import make_url


def libpq(dsn: str) -> str:
    """Any Postgres URL, whatever its driver suffix, as libpq takes it."""
    return make_url(dsn).set(drivername="postgresql").render_as_string(hide_password=False)
