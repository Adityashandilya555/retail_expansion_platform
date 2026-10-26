"""Sprint 1 demo driver (#31): put Matrix Retail cases at three different stages, through the API.

    uv run python scripts/demo_s1.py --api https://<api> --client <workspace code> \\
        [--users .demo-users.json] [--app-url https://<client app>]

The users file holds the demo users with their passwords (gitignored; never commit it):
`{"users": [{"email", "password", "department", "level"}, ...]}` with at least BD L1, BD L2,
Legal L1 and Legal L2. `zm-api users seed` prints the passwords of users it creates.

Only the public API is used, as the client app would: sign in, start cases, open each task and
submit it with a fresh Idempotency-Key. Required fields are filled from each form's own schema,
and every yes/no field is answered yes. At the end:
- case 1 waits at `legal_dd_checklist` for Legal L1;
- case 2 is still in its first stage;
- case 3 has finished Legal.
Exits non-zero if any of that doesn't hold. The run-book is platform/docs/demo-sprint-1.md.
"""

import argparse
import datetime
import json
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

FLOW = "store_launch"
START = {
    "site_name": "Indiranagar 100ft Road",
    "city": "Bengaluru",
    "address": "100 Feet Road, Indiranagar",
    "store_model": "btc_cafe",
}
# the decision each step takes, by task id; other steps only fill their form
OUTCOMES = {
    "bd_review_draft": "shortlist",
    "bd_approve_details": "approve",
    "bd_send_legal": "send",
    "legal_dd_verdict": "positive",
}
TO_LEGAL = [  # (who, task) from start to `legal_dd_checklist`
    ("bd2", "bd_review_draft"),
    ("bd1", "bd_site_details"),
    ("bd2", "bd_approve_details"),
    ("bd1", "bd_upload_loi"),
    ("bd2", "bd_send_legal"),
]
THROUGH_LEGAL = [
    ("legal1", "legal_dd_checklist"),
    ("legal2", "legal_dd_verdict"),
    ("legal2", "legal_agreement"),
    ("legal2", "legal_licensing"),
]
ROLES = {"bd1": ("bd", 1), "bd2": ("bd", 2), "legal1": ("legal", 1), "legal2": ("legal", 2)}


class DemoError(RuntimeError):
    pass


@dataclass
class Session:
    http: httpx.Client
    tokens: dict[str, str]

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.tokens[who]}", **kwargs.pop("headers", {})}
        return self.http.request(method, path, headers=headers, **kwargs)

    def ok(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        response = self.call(who, method, path, **kwargs)
        if response.status_code >= 400:
            raise DemoError(f"{who}: {method} {path} → {response.status_code} {response.text}")
        return response.json()


def pick_users(users: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """BD L1/L2 and Legal L1/L2 from the users file."""
    found = {}
    for role, (department, level) in ROLES.items():
        match = next(
            (u for u in users if u["department"] == department and int(u["level"]) == level), None
        )
        if match is None:
            raise DemoError(f"the users file has no {department} L{level} user")
        found[role] = match
    return found


def sign_in(http: httpx.Client, code: str, users: list[Mapping[str, Any]]) -> Session:
    tokens = {}
    for role, user in pick_users(users).items():
        body = {"workspace_code": code, "email": user["email"], "password": user["password"]}
        response = http.post("/auth/login", json=body)
        if response.status_code != 200:
            raise DemoError(f"{user['email']} couldn't sign in to {code}: {response.status_code}")
        tokens[role] = response.json()["access_token"]
    return Session(http, tokens)


def fill(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Values for a form: its required fields plus every yes/no field (answered yes)."""
    out: dict[str, Any] = {}
    props = schema.get("properties", {})
    for name, prop in props.items():
        kind = prop.get("x-zm-type")
        if kind in {"outcome"} or name.endswith("_comment"):
            continue
        if name in schema.get("required", []) or kind == "yes_no":
            out[name] = _value(name, prop)
    return out


def _value(name: str, prop: Mapping[str, Any]) -> Any:
    if "enum" in prop:
        return prop["enum"][0]
    kind = prop.get("type")
    if kind == "boolean":
        return True
    if kind in {"number", "integer"}:
        return max(prop.get("minimum", 1), 1)
    if kind == "array":
        return []
    if kind == "object":
        if prop.get("x-zm-type") == "document":
            return {"key": f"demo/{name}.pdf", "sha256": "0" * 64, "name": f"{name}.pdf"}
        if prop.get("x-zm-type") == "date_range":
            today = datetime.date.today().isoformat()
            return {"from": today, "to": today}
        return {"text": "Demo"} if prop.get("x-zm-type") == "address" else {}
    if prop.get("format") == "date":
        return datetime.date.today().isoformat()
    return "Demo"


def task(s: Session, who: str, task_id: str, code: str) -> dict[str, Any]:
    found = [
        t
        for t in s.ok(who, "GET", "/app/tasks", params={"case": code})
        if t["form_key"] == f"zm:{task_id}"
    ]
    if not found:
        raise DemoError(f"{who} doesn't see {task_id} of {code}")
    return found[0]


def submit(s: Session, who: str, task_id: str, code: str) -> None:
    item = task(s, who, task_id, code)
    detail = s.ok(who, "GET", f"/app/tasks/{item['id']}")
    body: dict[str, Any] = {"values": fill(detail["form"]["schema"])}
    if task_id in OUTCOMES:
        body["outcome"] = OUTCOMES[task_id]
    s.ok(
        who,
        "POST",
        f"/app/tasks/{item['id']}/submit",
        json=body,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


def start(s: Session, site: str) -> str:
    case = s.ok(
        "bd1", "POST", "/app/cases", json={"flow": FLOW, "values": {**START, "site_name": site}}
    )
    return case["code"]


def open_stages(s: Session, code: str) -> set[str]:
    """Stage keys with an open task for the case, as the demo users can see them."""
    stages: set[str] = set()
    for who, scope in (("bd1", "mine"), ("bd2", "team"), ("legal2", "team"), ("legal1", "mine")):
        for t in s.ok(who, "GET", "/app/tasks", params={"scope": scope, "case": code}):
            if t["stage"]:
                stages.add(t["stage"])
    return stages


def drive(http: httpx.Client, code: str, users: list[Mapping[str, Any]]) -> list[str]:
    """Create the three demo cases and check where they ended up; returns their codes."""
    s = sign_in(http, code, users)
    first, second, third = (start(s, site) for site in ("Indiranagar", "Koramangala", "HSR Layout"))
    for case in (first, third):
        for who, step in TO_LEGAL:
            submit(s, who, step, case)
    for who, step in THROUGH_LEGAL:
        submit(s, who, step, third)

    if not any(
        t["case_code"] == first and t["form_key"] == "zm:legal_dd_checklist"
        for t in s.ok("legal1", "GET", "/app/tasks")
    ):
        raise DemoError(f"Legal L1's My work doesn't show legal_dd_checklist under {first}")
    stages = {c: open_stages(s, c) for c in (first, second, third)}
    if "legal" not in stages[first] or stages[second] != {"bd_qualification"}:
        raise DemoError(f"cases aren't where the demo needs them: {stages}")
    if "legal" in stages[third] or not stages[third]:
        raise DemoError(f"{third} should have finished Legal: {stages[third]}")
    cases = http.get("/app/cases", headers={"Authorization": f"Bearer {s.tokens['bd2']}"})
    if cases.status_code == 200:  # the cases API (#91), once it exists
        listed = {c.get("code") for c in cases.json()}
        if not {first, second, third} <= listed:
            raise DemoError(f"GET /app/cases doesn't list {first}, {second}, {third}")
    return [first, second, third]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo_s1", description=__doc__.split("\n")[0])
    parser.add_argument("--api", required=True, help="the platform API's base URL")
    parser.add_argument("--client", required=True, help="the client's workspace code")
    parser.add_argument("--users", default=".demo-users.json", help="demo users with passwords")
    parser.add_argument("--app-url", default=os.environ.get("ZM_WEB_URL", ""), help="client app")
    args = parser.parse_args(argv)
    users = json.loads(Path(args.users).read_text())["users"]
    with httpx.Client(base_url=args.api, timeout=60) as http:
        try:
            codes = drive(http, args.client, users)
        except DemoError as e:
            print(f"demo_s1: {e}", file=sys.stderr)
            return 1
    print(f"cases: {codes[0]} (at Legal), {codes[1]} (first stage), {codes[2]} (Legal done)")
    print(f"client app: {args.app_url or '(set --app-url)'}   workspace code: {args.client}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
