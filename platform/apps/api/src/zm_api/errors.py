"""RFC 9457 problem details (#21): every error is `application/problem+json`
`{type, title, status, detail, request_id}`; validation errors add `errors: [{path, message}]`
with JSON Pointer paths (the shape @zm/forms shows under fields, #28).

Unknown or other clients' ids are 404, never 403. Unhandled exceptions become a 500 problem in
`zm_api.logs.RequestLogMiddleware`, so they are logged and carry `X-Request-Id` too.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from zm_api.logs import request_id

PROBLEM = "application/problem+json"


class ApiError(Exception):
    status = 500

    def __init__(self, detail: str | None = None, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.headers = headers


class NotFound(ApiError):
    status = 404


class Unauthorized(ApiError):
    status = 401

    def __init__(self, detail: str = "Sign in again") -> None:
        super().__init__(detail, headers={"WWW-Authenticate": "Bearer"})


class BadGateway(ApiError):
    status = 502


class ServiceUnavailable(ApiError):
    status = 503


def problem(
    status: int, detail: str | None = None, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "request_id": request_id.get(),
        **extra,
    }
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM)


def _pointer(loc: tuple[int | str, ...]) -> str:
    parts = loc[1:] if loc and loc[0] in {"body", "query", "path", "header", "cookie"} else loc
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return problem(exc.status, exc.detail, exc.headers)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    return problem(exc.status_code, exc.detail, exc.headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [{"path": _pointer(tuple(e["loc"])), "message": e["msg"]} for e in exc.errors()]
    return problem(422, "Some values are not valid", errors=errors)


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
