"""Thin typed client for the Operaton REST endpoints the platform uses (#21).

One instance per client (`zm_api.context`), on that client's `/engine-rest` base URL as its
service account. Every call logs `op`, `status` and `ms`, with Operaton's message in the log only.

How Operaton's answers map (verified at v2.1.5: exception/ExceptionHandlerHelper.java:114-127, read
from GitHub at the tag, and probed against the operaton/operaton:2.1.5 image):
- 404, and a 500 saying the task/user doesn't exist → our 404. Operaton answers an unknown task
  on `claim`/`complete` (and an unknown user on `add_member`) with 500 `NullValueException` /
  `RestException` "Cannot find task with id …" / "No user found with id …", not 404.
- 400 (`BadUserRequestException`, e.g. a variable of the wrong type) → our 400.
- anything else (other 4xx such as 401, 5xx, timeout, connection, a non-JSON answer) → 502.
  `OperatonError.upstream_type` keeps the engine's exception type, e.g.
  `TaskAlreadyClaimedException` (also a 500), for callers that handle it.
"""

import logging
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from zm_api.errors import ApiError, NotFound
from zm_api.logs import request_id

Json = dict[str, Any]
_log = logging.getLogger("zm_api.operaton")
_NOT_FOUND = re.compile(r"Cannot find \w+ with id|No \w+ found with id")


class OperatonError(ApiError):
    """Operaton refused or failed: 400 when it rejected the values, else 502.
    `upstream_status` is None when it was unreachable or didn't answer JSON."""

    def __init__(self, op: str, upstream_status: int | None, upstream_type: str | None) -> None:
        rejected = upstream_status == 400
        super().__init__(
            "The process engine rejected the values"
            if rejected
            else "The process engine could not handle the request"
        )
        self.status = 400 if rejected else 502
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


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


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

    async def _call(self, op: str, method: str, path: str, **kwargs: Any) -> Any:
        """The parsed JSON answer, or None for an empty one (204)."""
        start, status, error = time.perf_counter(), None, None
        try:
            response = await self.http.request(method, path, **kwargs)
            status = response.status_code
            if status >= 400:
                body = _json(response)
                upstream = body if isinstance(body, dict) else {}
                kind, message = upstream.get("type"), str(upstream.get("message") or "")
                error = f"{kind}: {message}"
                if status == 404 or (status == 500 and _NOT_FOUND.search(message)):
                    raise NotFound("Not found")
                raise OperatonError(op, status, kind)
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                error = "answer is not JSON"
                raise OperatonError(op, None, None) from None
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
        self,
        name: str,
        files: Mapping[str, bytes],
        *,
        source: str = "zm-platform",
        changed_only: bool = False,
    ) -> Json:
        """`POST /deployment/create` with duplicate filtering against the latest deployment of the
        same name and source. If nothing changed, Operaton returns that existing deployment; if
        anything changed, `changed_only=False` redeploys every resource, which call activities
        bound with `calledElementBinding="deployment"` need (DeployCmd.java:207-263)."""
        data = {
            "deployment-name": name,
            "deployment-source": source,
            "enable-duplicate-filtering": "true",
            "deploy-changed-only": "true" if changed_only else "false",
        }
        # one part per resource, named after it: the engine keys parts by name
        multipart = [(filename, (filename, content)) for filename, content in files.items()]
        return await self._call("deploy", "POST", "deployment/create", data=data, files=multipart)

    async def process_definition(self, key: str) -> Json:
        """The latest version of a process definition."""
        return await self._call("process_definition", "GET", f"process-definition/key/{_id(key)}")

    async def process_definition_by_id(self, definition_id: str) -> Json:
        """One process definition, e.g. a task's `processDefinitionId`; has `deploymentId`."""
        path = f"process-definition/{_id(definition_id)}"
        return await self._call("process_definition_by_id", "GET", path)

    async def definition_in(self, deployment_id: str, key: str) -> Json:
        """The definition of `key` deployed by one deployment (a release), whatever is latest."""
        params = {"deploymentId": deployment_id, "key": key}
        found = await self._call("definition_in", "GET", "process-definition", params=params)
        if not found:
            raise NotFound("No such process in this release")
        return found[0]

    # Runtime
    async def start(
        self,
        definition_id: str,
        *,
        business_key: str | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> Json:
        """Start one exact process definition (`POST /process-definition/{id}/start`)."""
        body: Json = {"variables": variables(values or {})}
        if business_key is not None:
            body["businessKey"] = business_key
        path = f"process-definition/{_id(definition_id)}/start"
        return await self._call("start", "POST", path, json=body)

    async def start_by_key(
        self, key: str, *, business_key: str | None = None, values: Mapping[str, Any] | None = None
    ) -> Json:
        body: Json = {"variables": variables(values or {})}
        if business_key is not None:
            body["businessKey"] = business_key
        path = f"process-definition/key/{_id(key)}/start"
        return await self._call("start_by_key", "POST", path, json=body)

    async def find_instances(self, query: Mapping[str, Any]) -> list[Json]:
        """`POST /process-instance` with a ProcessInstanceQueryDto body; each instance has
        `businessKey`. A body, not a query string: hundreds of ids would pass Tomcat's 8 KB
        request-line limit."""
        return await self._call("find_instances", "POST", "process-instance", json=dict(query))

    async def process_instances(self, ids: list[str]) -> list[Json]:
        """Running process instances by id, in one call (ProcessInstanceQueryDto.java:75). Stage
        processes get the case's business key from their call activity."""
        if not ids:
            return []
        return await self.find_instances({"processInstanceIds": ids})

    async def delete_process_instance(self, instance_id: str) -> None:
        path = f"process-instance/{_id(instance_id)}"
        params = {"skipCustomListeners": "true"}
        await self._call("delete_process_instance", "DELETE", path, params=params)

    async def list_tasks(
        self, query: Mapping[str, Any], *, first: int = 0, max_results: int = 200
    ) -> list[Json]:
        """`POST /task` with a TaskQueryDto body, e.g. `candidateGroups`, `assignee`."""
        params = {"firstResult": first, "maxResults": max_results}
        return await self._call("list_tasks", "POST", "task", params=params, json=dict(query))

    async def all_tasks(self, query: Mapping[str, Any], *, page: int = 200) -> list[Json]:
        """Every task `query` matches, `page` at a time: an inbox shows the whole queue (#30)."""
        out: list[Json] = []
        while True:
            found = await self.list_tasks(query, first=len(out), max_results=page)
            out += found
            if len(found) < page:
                return out

    async def get_task(self, task_id: str) -> Json:
        return await self._call("get_task", "GET", f"task/{_id(task_id)}")

    async def identity_links(self, task_id: str) -> list[Json]:
        """The task's candidate users and groups: `[{userId, groupId, type: "candidate"}]`."""
        path = f"task/{_id(task_id)}/identity-links"
        return await self._call("identity_links", "GET", path, params={"type": "candidate"})

    async def claim(self, task_id: str, user_id: str) -> None:
        await self._call("claim", "POST", f"task/{_id(task_id)}/claim", json={"userId": user_id})

    async def unclaim(self, task_id: str) -> None:
        await self._call("unclaim", "POST", f"task/{_id(task_id)}/unclaim")

    async def complete(self, task_id: str, values: Mapping[str, Any]) -> None:
        body = {"variables": variables(values)}
        await self._call("complete", "POST", f"task/{_id(task_id)}/complete", json=body)

    # History
    async def history_task(self, task_id: str) -> list[Json]:
        return await self._call("history_task", "GET", "history/task", params={"taskId": task_id})

    async def history_variables(
        self, instance_ids: list[str], names: list[str], *, max_results: int = 20000
    ) -> list[Json]:
        """`POST /history/variable-instance` for many process instances and variable names in one
        call (HistoricVariableInstanceQueryDto.java:67,71); a body, not a query string."""
        if not instance_ids or not names:
            return []
        body = {"processInstanceIdIn": instance_ids, "variableNameIn": names}
        params = {"maxResults": max_results, "deserializeValues": "false"}
        return await self._call(
            "history_variables", "POST", "history/variable-instance", params=params, json=body
        )

    async def history_activities(
        self, instance_id: str, activity_type: str = "callActivity"
    ) -> list[Json]:
        """`GET /history/activity-instance` of one process instance: start/end times and
        `canceled` per activity (HistoricActivityInstanceQueryDto.java:70,76)."""
        params = {
            "processInstanceId": instance_id,
            "activityType": activity_type,
            "sortBy": "startTime",
            "sortOrder": "asc",
        }
        return await self._call(
            "history_activities", "GET", "history/activity-instance", params=params
        )

    # Identity (reads first: creating a user or group twice is a 400, a membership twice a 500)
    async def user_profile(self, user_id: str) -> Json:
        return await self._call("user_profile", "GET", f"user/{_id(user_id)}/profile")

    async def group(self, group_id: str) -> Json:
        return await self._call("group", "GET", f"group/{_id(group_id)}")

    async def groups_of(self, user_id: str) -> list[Json]:
        params = {"member": user_id, "maxResults": 500}
        return await self._call("groups_of", "GET", "group", params=params)

    async def create_group(self, group_id: str, name: str, *, type: str = "zm") -> None:  # noqa: A002
        await self._call(
            "create_group",
            "POST",
            "group/create",
            json={"id": group_id, "name": name, "type": type},
        )

    async def update_group(self, group_id: str, name: str, *, type: str = "zm") -> None:  # noqa: A002
        await self._call(
            "update_group",
            "PUT",
            f"group/{_id(group_id)}",
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
