"""The isolation checks (#31), each returning the leaks it found (empty = isolated), so the same
check proves both that the platform is isolated and, against a broken resolver, that the check
would notice if it weren't."""

import uuid
from typing import Any

from fastapi.testclient import TestClient

Leaks = list[str]


def cross_client_ids(http: TestClient, auth: dict[str, str], other_task: str) -> Leaks:
    """Check 1: another client's task id is 404 to this client's user, read or submit. Any
    other answer (401 included) says the id resolved somewhere: a leak."""
    leaks = []
    read = http.get(f"/app/tasks/{other_task}", headers=auth)
    if read.status_code != 404:
        leaks.append(f"GET /app/tasks/{{other client's id}} answered {read.status_code}")
    headers = {**auth, "Idempotency-Key": f"isolation-{uuid.uuid4()}"}
    sent = http.post(f"/app/tasks/{other_task}/submit", json={"values": {}}, headers=headers)
    if sent.status_code != 404:
        leaks.append(f"POST /app/tasks/{{other client's id}}/submit answered {sent.status_code}")
    return leaks


def cross_client_case(http: TestClient, auth: dict[str, str], other_case: str) -> Leaks:
    """Check 1 on the cases API (#91): another client's case is 404."""
    found = http.get(f"/app/cases/{other_case}", headers=auth)
    return [] if found.status_code == 404 else [f"GET /app/cases/{{other}} {found.status_code}"]


def own_ui_only(
    http: TestClient,
    auth: dict[str, str],
    foreign_user_auth: dict[str, str],
    own_pages: set[str],
    other_pages: set[str],
) -> Leaks:
    """Check 1 on /app/ui: the user gets their own client's pages and none of the other's; a
    token naming the other client's user is refused."""
    leaks = []
    mine = http.get("/app/ui", headers=auth)
    pages = {p["key"] for p in mine.json().get("pages", [])} if mine.status_code == 200 else set()
    if mine.status_code != 200 or not pages or not pages <= own_pages:
        leaks.append(f"/app/ui answered {mine.status_code} with pages {sorted(pages)}")
    if pages & (other_pages - own_pages):
        leaks.append(f"/app/ui shows the other client's pages {sorted(pages & other_pages)}")
    foreign = http.get("/app/ui", headers=foreign_user_auth)
    if foreign.status_code != 401:
        leaks.append(f"/app/ui for the other client's user answered {foreign.status_code}")
    return leaks


def owner_surface(http: TestClient, auth: dict[str, str]) -> Leaks:
    """Check 6: a client token on the owner console is 403."""
    found = http.get("/owner/clients", headers=auth)
    return [] if found.status_code == 403 else [f"/owner/clients answered {found.status_code}"]


def run_all(http: TestClient, a: Any, b: Any) -> Leaks:
    """Check 1 both ways between two `Client`s of conftest, with two kinds of caller:
    - the client's own user;
    - a token for this client carrying the other client's user id (someone who learned a
      uuid). The platform still answers 404, because the client comes from the token's
      `client` claim; a resolver that let anything else pick the client would hand this
      caller the other client's task (200), which the negative control shows."""
    leaks: Leaks = []
    for me, other in ((a, b), (b, a)):
        leaks += cross_client_ids(http, me.auth(), other.task_id)
        leaks += cross_client_ids(http, me.foreign_auth(other), other.task_id)
    return leaks
