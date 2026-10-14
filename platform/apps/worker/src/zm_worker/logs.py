import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """One JSON object per line, tagged with client and request_id (AGENTS.md conventions)."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "msg": record.getMessage(),
                "client": getattr(record, "client", None),
                "request_id": getattr(record, "request_id", None),
            }
        )


def configure(level: str = "info") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
