"""Thin typed client for the Operaton REST endpoints the platform uses (#21).

One instance per client (`zm_api.context`), on that client's `/engine-rest` base URL as its
service account. Every call logs `op`, `status` and `ms`. Operaton's 404 becomes our 404; any
other failure (4xx, 5xx, timeout, connection) becomes a 502 problem, with Operaton's message in
the log only.

Verified at Operaton v2.1.5 (engine-rest/.../rest/, read from GitHub at the tag; Sourcegraph was
down): most engine errors are HTTP 500 with body `{type, message, code}`
(exception/ExceptionHandlerHelper.java:114-127). A missing or already-claimed task on `claim` is
a 500 too, so callers check `OperatonError.upstream_type` (e.g. `TaskAlreadyClaimedException`),
not the status.
"""

import logging
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from zm_api.errors import BadGateway, NotFound
from zm_api.logs import request_id

Json = dict[str, Any]
_log = logging.getLogger("zm_api.operaton")


class OperatonError(BadGateway):
    """Operaton answered with an error or not at all. `upstream_status` is None when unreachable."""

    def __init__(self, op: str, upstream_status: int | None, upstream_type: str | None) -> None:
        super().__init__("The process engine could not handle the request")
        self.op, self.upstream_status, self.upstream_type = op, upstream_status, upstream_type


def variables(values: Mapping[str, Any]) -> dict[str, Json]:
    """Plain values → typed Operaton variables. Only routing values go to the engine (D5): an
    untyped object or list would become a Java-serialized Map, which Operaton Run refuses
    (dto/VariableValueDto.java:82-86 at v2.1.5)."""
    out: dict[str, Json] = {}
    for name, value in values.items():
        if value is None:
            out[name] = {"value": None, "type": "Null"}
        elif isinstance(value, bool):
            out[name] = {"value": value, "type": "Boolean"}
        elif isinstance(value, int):
            out[name] = {"value": value, "type": "Long"}
        elif isinstance(value, float):
            out[name] = {"value": value, "type": "Double"}
        elif isinstance(value, str):
            out[name] = {"value": value, "type": "String"}
        else:
            raise TypeError(f"{name}: only strings, numbers, booleans and null go to Operaton (D5)")
    return out


def _id(value: str) -> str:
    return quote(value, safe="")


class OperatonClient:
    def __init__(
        self,
        base_url: str,
        *,
        auth: tuple[str, str],
        client: str,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client = client
        self.http = httpx.AsyncClient(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json"},  # GET /task/{id} answers 406 to other types
            transport=transport,
        )

    @property
    def base_url(self) -> httpx.URL:
        return self.http.base_url

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _call(self, op: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
        start, status, error = time.perf_counter(), None, None
        try:
            response = await self.http.request(method, path, **kwargs)
            status = response.status_code
            if status >= 400:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                error = f"{body.get('type')}: {body.get('message')}"
                if status == 404:
                    raise NotFound("Not found")
                raise OperatonError(op, status, body.get("type"))
            return response
        except httpx.HTTPError as exc:
            error = repr(exc)
            raise OperatonError(op, None, None) from exc
        finally:
            _log.log(
                logging.WARNING if error else logging.INFO,
                "operaton",
                extra={
                    "client": self.client,
                    "request_id": request_id.get(),
                    "op": op,
                    "status": status,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                    "error": error,
                },
            )

    # Repository
    async def deploy(
        self, name: str, files: Mapping[str, bytes], *, source: str = "zm-platform"
    ) -> Json:
        """`POST /deployment/create`; unchanged resources don't make a new version."""
        data = {
            "deployment-name": name,
            "deployment-source": source,
            "enable-duplicate-filtering": "true",
        }
        # one part per resource, named after it: the engine keys parts by name
        multipart = [(filename, (filename, content)) for filename, content in files.items()]
        return (
            await self._call("deploy", "POST", "deployment/create", data=data, files=multipart)
        ).json()

    async def process_definition(self, key: str) -> Json:
        """The latest version of a process definition."""
        return (
            await self._call("process_definition", "GET", f"process-definition/key/{_id(key)}")
        ).json()

    # Runtime
    async def start_by_key(
        self, key: str, *, business_key: str | None = None, values: Mapping[str, Any] | None = None
    ) -> Json:
        body: Json = {"variables": variables(values or {})}
        if business_key is not None:
            body["businessKey"] = business_key
        path = f"process-definition/key/{_id(key)}/start"
        return (await self._call("start_by_key", "POST", path, json=body)).json()

    async def list_tasks(
        self, query: Mapping[str, Any], *, first: int = 0, max_results: int = 200
    ) -> list[Json]:
        """`POST /task` with a TaskQueryDto body, e.g. `candidateGroups`, `assignee`."""
        params = {"firstResult": first, "maxResults": max_results}
        return (
            await self._call("list_tasks", "POST", "task", params=params, json=dict(query))
        ).json()

    async def get_task(self, task_id: str) -> Json:
        return (await self._call("get_task", "GET", f"task/{_id(task_id)}")).json()

    async def claim(self, task_id: str, user_id: str) -> None:
        await self._call("claim", "POST", f"task/{_id(task_id)}/claim", json={"userId": user_id})

    async def complete(self, task_id: str, values: Mapping[str, Any]) -> None:
        body = {"variables": variables(values)}
        await self._call("complete", "POST", f"task/{_id(task_id)}/complete", json=body)

    # History
    async def history_task(self, task_id: str) -> list[Json]:
        return (
            await self._call("history_task", "GET", "history/task", params={"taskId": task_id})
        ).json()

    # Identity
    async def create_group(self, group_id: str, name: str, *, type: str = "zm") -> None:  # noqa: A002
        await self._call(
            "create_group",
            "POST",
            "group/create",
            json={"id": group_id, "name": name, "type": type},
        )

    async def create_user(
        self, user_id: str, *, first_name: str, last_name: str, email: str, password: str
    ) -> None:
        profile = {"id": user_id, "firstName": first_name, "lastName": last_name, "email": email}
        body = {"profile": profile, "credentials": {"password": password}}
        await self._call("create_user", "POST", "user/create", json=body)

    async def add_member(self, group_id: str, user_id: str) -> None:
        await self._call("add_member", "PUT", f"group/{_id(group_id)}/members/{_id(user_id)}")

    async def remove_member(self, group_id: str, user_id: str) -> None:
        await self._call("remove_member", "DELETE", f"group/{_id(group_id)}/members/{_id(user_id)}")
