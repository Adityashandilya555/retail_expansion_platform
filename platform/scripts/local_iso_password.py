"""Give operaton-iso its own service password (#31), on a local stack created before it had one.

Operaton Run creates its admin user only once, so an existing engine keeps the password all
local engines shared (`zm-local-only`). If that still works, rotate it to ZM_ISO_ADMIN_USER_PASSWORD
through the REST API (nothing is deleted). Tries the new password first: a wrong password costs
Operaton's 3 s login delay, so a stack that is already rotated answers at once.
"""

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

DOTENV = Path(__file__).resolve().parents[1] / ".env"


def dotenv(path: Path = DOTENV) -> dict[str, str]:
    """platform/.env, which docker compose also reads: a password set there is the engine's."""
    if not path.exists():
        return {}
    pairs = (line.split("#", 1)[0].strip().partition("=") for line in path.read_text().splitlines())
    return {k.strip(): v.strip() for k, sep, v in pairs if sep and k.strip()}


ENV = {**dotenv(), **os.environ}
BASE = f"http://localhost:{ENV.get('OPERATON_ISO_PORT', '8081')}/engine-rest"
USER = ENV.get("ZM_ADMIN_USER_ID", "zm-service")
OLD = ENV.get("ZM_ADMIN_USER_PASSWORD", "zm-local-only")
NEW = ENV.get("ZM_ISO_ADMIN_USER_PASSWORD", "zm-iso-local-only")


def main(http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep) -> int:
    with http or httpx.Client(base_url=BASE, timeout=10) as http:
        if http.get("user/count", auth=(USER, NEW)).status_code == 200:
            print("operaton-iso: service password already its own")
            return 0
        sleep(4)  # the failed try above locks the user for 3 s
        body = {"password": NEW, "authenticatedUserPassword": OLD}
        done = http.put(f"user/{USER}/credentials", json=body, auth=(USER, OLD))
    if done.status_code != 204:
        print(f"operaton-iso: couldn't rotate the service password ({done.status_code})")
        return 1
    print("operaton-iso: service password rotated to ZM_ISO_ADMIN_USER_PASSWORD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
