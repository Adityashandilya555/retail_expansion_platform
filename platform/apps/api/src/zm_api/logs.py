"""JSON logs (#21): one line per request with `client`, `request_id`, `route`, `status`, `ms`.

The request id comes from `X-Request-Id` when it is safe to log, else a new one; every response
echoes it. Run uvicorn with `--no-access-log` so the request line stays the only one.
"""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
client: ContextVar[str | None] = ContextVar("client", default=None)

_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_EXTRA = ("method", "route", "status", "ms", "op", "error")
_log = logging.getLogger("zm_api.request")


class JsonFormatter(logging.Formatter):
    """One JSON object per line; `client` and `request_id` default to the current request's."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "client": getattr(record, "client", client.get()),
            "request_id": getattr(record, "request_id", request_id.get()),
        }
        out.update({k: getattr(record, k) for k in _EXTRA if hasattr(record, k)})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: str = "info") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)


class RequestLogMiddleware:
    """Outermost middleware: sets the request id, logs the request, turns crashes into a 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "")
        rid = incoming if _SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex
        reset = request_id.set(rid)
        state = scope.setdefault(
            "state", {}
        )  # request.state: ClientContext records the client here
        status, started, failure = 500, False, None
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                started, status = True, message["status"]
                MutableHeaders(scope=message).append("X-Request-Id", rid)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:  # anything the exception handlers didn't turn into a response
            failure = exc
            if not started:
                from zm_api.errors import problem  # errors imports this module

                await problem(500, "Something went wrong")(scope, receive, send_with_id)
        finally:
            _log.info(
                "request",
                exc_info=failure,
                extra={
                    "client": state.get("client"),
                    "request_id": rid,
                    "method": scope["method"],
                    "route": getattr(scope.get("route"), "path", None),
                    "status": status,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            request_id.reset(reset)
