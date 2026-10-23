"""RFC 9457 problem details (#21): every error is `application/problem+json`
`{type, title, status, detail, request_id}`; validation errors add `errors: [{path, message}]`
with JSON Pointer paths (the shape @zm/forms shows under fields, #28).

`Problem` is every route's `default` response in the OpenAPI document, so the generated TS
client types errors from it (and FastAPI's own 422 schema is never emitted). Unknown or other
clients' ids are 404, never 403.
"""

from collections.abc import Callable
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zm_api.logs import request_id

PROBLEM = "application/problem+json"


class FieldError(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    path: str
    """JSON Pointer to the field, e.g. `/ca_amount`."""
    message: str


class Problem(BaseModel):
    """Problem details (RFC 9457), the body of every error response."""

    # every key is always sent, so the generated TS type has no optional fields
    model_config = ConfigDict(
        use_attribute_docstrings=True, json_schema_serialization_defaults_required=True
    )

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    request_id: str | None = None
    errors: list[FieldError] | None = None
    """One entry per invalid field (422, or a 409 on a taken value); otherwise null."""


RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {"model": Problem, "description": "Problem details (RFC 9457)"}
}


class ApiError(Exception):
    status = 500

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        errors: list[FieldError] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.headers = headers
        self.errors = errors  # e.g. a 409 on one field of a form


class BadRequest(ApiError):
    status = 400


class NotFound(ApiError):
    status = 404


class Unauthorized(ApiError):
    status = 401

    def __init__(self, detail: str = "Sign in again") -> None:
        super().__init__(detail, headers={"WWW-Authenticate": "Bearer"})


class Forbidden(ApiError):
    """A valid sign-in for the other surface: an owner on `/app`, a client user on `/owner`."""

    status = 403


class Conflict(ApiError):
    status = 409


class UnprocessableContent(ApiError):
    """A well-formed request about something that can't be processed, e.g. an invalid draft."""

    status = 422


class FormInvalid(UnprocessableContent):
    """Submitted values that fail their form's schema: one entry per field, as @zm/forms shows."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("Some values are not valid", errors=errors)


class TooManyRequests(ApiError):
    status = 429

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "Too many attempts, try again shortly", headers={"Retry-After": str(retry_after)}
        )


class ServiceUnavailable(ApiError):
    status = 503


def problem(
    status: int,
    detail: str | None = None,
    headers: dict[str, str] | None = None,
    errors: list[FieldError] | None = None,
) -> JSONResponse:
    body = Problem(
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        request_id=request_id.get(),
        errors=errors,
    ).model_dump()
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM)


def _pointer(loc: tuple[int | str, ...]) -> str:
    parts = loc[1:] if loc and loc[0] in {"body", "query", "path", "header", "cookie"} else loc
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return problem(exc.status, exc.detail, exc.headers, exc.errors)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    return problem(exc.status_code, exc.detail, exc.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [FieldError(path=_pointer(tuple(e["loc"])), message=e["msg"]) for e in exc.errors()]
    return problem(422, "Some values are not valid", errors=errors)


class CrashGuard:
    """Turns an unhandled exception into a 500 problem. Sits inside CORSMiddleware, so the
    browser can read the error and its request_id; the exception goes to the request log line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def track(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, track)
        except Exception as exc:
            scope.setdefault("state", {})["error"] = exc
            if not started:
                await problem(500, "Something went wrong")(scope, receive, send)


def _problem_media_type(app: FastAPI) -> Callable[[], dict[str, Any]]:
    """FastAPI files declared responses under the route's media type; ours is problem+json."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            for item in FastAPI.openapi(app).get("paths", {}).values():
                for operation in item.values():
                    content = operation.get("responses", {}).get("default", {}).get("content", {})
                    if "application/json" in content:
                        content[PROBLEM] = content.pop("application/json")
        assert app.openapi_schema is not None
        return app.openapi_schema

    return openapi


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.openapi = _problem_media_type(app)  # type: ignore[method-assign]
