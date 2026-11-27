"""A client's private bucket (#51, S2-10) through Supabase's Storage REST API: a signed upload URL,
the object streamed back, a delete, and a signed download URL.

One instance per client (`zm_api.context`), with that project's service key. Only keys go in our
database; signed URLs are made per request and never stored. Each call logs `op`, `status` and
`ms`, never a key or a URL (a signed URL carries its token). An error answer, or none, is a 502.

Routes as in supabase/storage `src/http/routes/object/` (read on GitHub, 2026-11-27):
`POST /object/upload/sign/{bucket}/{key}` → `{url}`, `GET /object/authenticated/{bucket}/{key}`,
`DELETE /object/{bucket}/{key}`, and `POST /object/sign/{bucket}/{key} {expiresIn}` →
`{signedURL}`. Both URLs are relative to `/storage/v1`.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from zm_api.errors import ApiError
from zm_api.logs import request_id

DOWNLOAD_TTL = 60  # seconds a signed download URL works (S2-10)
UPLOAD_TTL = 60
"""Seconds a signed upload URL surely works: Storage sets its lifetime itself
(`UPLOAD_SIGNED_URL_EXPIRATION_TIME`, 60 by default, 2 h on hosted projects); the browser PUTs
at once."""
_log = logging.getLogger("zm_api.storage")


class StorageError(ApiError):
    status = 502

    def __init__(self) -> None:
        super().__init__("File storage could not handle the request; try again")


def auth_headers(key: str) -> dict[str, str]:
    """A project's service key as Storage takes it: a legacy JWT also as the bearer token, a new
    `sb_secret_` key only in `apikey`."""
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def answer_status(response: httpx.Response) -> int:
    """The status Storage means: older versions answer some errors (a missing object, a duplicate
    bucket, a limit above the project's) with 400 and the real code as `statusCode` in the body."""
    if response.status_code == 400:
        try:
            return int(response.json()["statusCode"])
        except (ValueError, TypeError, KeyError):
            pass
    return response.status_code


class Storage:
    def __init__(
        self,
        url: str,
        key: str,
        bucket: str,
        *,
        client: str,
        public_url: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`url` is where the API reaches the project; `public_url`, where the browser does (the
        local stack's differ when the API runs in its container), by default the same."""
        self.bucket, self.client = bucket, client
        self.http = httpx.AsyncClient(
            base_url=f"{url.rstrip('/')}/storage/v1/",
            headers=auth_headers(key),
            timeout=timeout,
            transport=transport,
        )
        self._public = f"{(public_url or url).rstrip('/')}/storage/v1"

    async def aclose(self) -> None:
        await self.http.aclose()

    def _object(self, route: str, key: str) -> str:
        return f"object/{route}{quote(self.bucket, safe='')}/{quote(key)}"

    @asynccontextmanager
    async def _logged(self, op: str) -> AsyncIterator[dict[str, Any]]:
        """One log line per call; no answer (timeout, unreachable) is a StorageError."""
        start, seen = time.perf_counter(), {}
        try:
            yield seen
        except httpx.HTTPError as exc:
            seen["error"] = type(exc).__name__
            raise StorageError() from None
        finally:
            status = seen.get("status")
            _log.log(
                logging.INFO if status and status < 400 else logging.WARNING,
                "storage",
                extra={
                    "client": self.client,
                    "request_id": request_id.get(),
                    "op": op,
                    "status": status,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                    "error": seen.get("error"),
                },
            )

    async def _call(
        self, op: str, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any
    ) -> httpx.Response:
        async with self._logged(op) as seen:
            response = await self.http.request(method, path, **kwargs)
            seen["status"] = status = answer_status(response)
            if response.is_error and not (missing_ok and status == 404):
                raise StorageError()
            return response

    async def signed_upload(self, key: str) -> tuple[str, datetime]:
        """A URL the browser PUTs the file to, and a time before which it works."""
        expires = datetime.now(UTC) + timedelta(seconds=UPLOAD_TTL)  # counted from before signing
        response = await self._call("signed_upload", "POST", self._object("upload/sign/", key))
        return self._public + response.json()["url"], expires

    async def signed_download(self, key: str, filename: str | None = None) -> tuple[str, datetime]:
        """A URL serving the object for DOWNLOAD_TTL seconds; with `filename`, as an attachment
        of that name (Storage's `download` parameter), else inline (a preview)."""
        expires = datetime.now(UTC) + timedelta(seconds=DOWNLOAD_TTL)
        response = await self._call(
            "signed_download", "POST", self._object("sign/", key), json={"expiresIn": DOWNLOAD_TTL}
        )
        url = self._public + response.json()["signedURL"]
        if filename is not None:
            url += "&" + urlencode({"download": filename})
        return url, expires

    async def delete(self, key: str) -> None:
        """Remove an object; one already gone is fine."""
        await self._call("delete", "DELETE", self._object("", key), missing_ok=True)

    @asynccontextmanager
    async def read(self, key: str) -> AsyncIterator[AsyncIterator[bytes] | None]:
        """The object's bytes a chunk at a time, never the whole object in memory; None when
        there is no such object (nothing was uploaded)."""
        async with (
            self._logged("read") as seen,
            self.http.stream("GET", self._object("authenticated/", key)) as response,
        ):
            if response.is_error:
                await response.aread()  # an error answer is a short JSON body
            seen["status"] = status = answer_status(response)
            if status == 404:
                yield None
            elif response.is_error:
                raise StorageError()
            else:
                yield response.aiter_bytes()
