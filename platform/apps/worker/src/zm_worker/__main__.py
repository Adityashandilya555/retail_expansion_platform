import argparse
import logging
import os
import signal
import sys
import threading
import time

from zm_worker import logs, vault_migrate

log = logging.getLogger("zm_worker")

RECONCILE_EVERY = 60.0  # seconds between reconciler passes (#26)
RECONCILE_ENV = ("PLATFORM_DATABASE_URL", "SECRETS_ENCRYPTION_KEY")
# all set (and PLATFORM_DATABASE_URL, SECRETS_ENCRYPTION_KEY), or no provisioning, e.g. in compose
PROVISION_ENV = ("SUPABASE_ACCESS_TOKEN", "SUPABASE_ORG_ID", "SUPABASE_PROJECT_PREFIX")
_last_reconcile: float | None = None
_warned = False


def start_provisioner(stop: threading.Event, interval: float) -> threading.Thread | None:
    """Provisioning (S2-02a, #24) in its own thread: a step can wait 10 minutes for a new project,
    which must hold up neither the reconciler nor shutdown. None while it isn't configured."""
    if not all(os.environ.get(v) for v in (*RECONCILE_ENV, *PROVISION_ENV)):
        return None
    try:
        # imports zm_api, like the reconciler: inside the try so a broken image logs
        from zm_worker import provision
        from zm_worker.provision.runner import Settings

        settings = Settings.from_env()
        assert settings is not None  # PROVISION_ENV is set
    except Exception:
        log.exception("provisioning off: it failed to start")
        return None
    thread = threading.Thread(
        target=provision.loop,
        args=(os.environ["PLATFORM_DATABASE_URL"], settings, stop, interval),
        name="provisioner",
        daemon=True,
    )
    thread.start()
    return thread


def tick() -> None:
    """One pass of the worker loop: the submission reconciler (#26) at most every minute, once
    PLATFORM_DATABASE_URL and SECRETS_ENCRYPTION_KEY are set. Provisioning has its own thread."""
    global _last_reconcile, _warned
    log.info("tick")
    now = time.monotonic()
    missing = [v for v in RECONCILE_ENV if not os.environ.get(v)]
    if missing:
        if not _warned:  # stuck submissions then block their tasks: say why, once
            log.warning("reconciler off: set %s", " and ".join(missing))
            _warned = True
        return
    if _last_reconcile is not None and now - _last_reconcile < RECONCILE_EVERY:
        return
    _last_reconcile = now
    try:
        # imports zm_api: only once there is something to reconcile, and inside the try so a
        # broken image logs instead of crash-looping
        from zm_worker import reconcile

        reconcile.run_once()
    except Exception:  # the registry is down: try again next minute, keep the loop alive
        log.exception("reconcile pass failed")


def run(stop: threading.Event, interval: float) -> None:
    log.info("worker started")
    provisioner = start_provisioner(stop, interval)
    while not stop.is_set():
        tick()
        stop.wait(interval)
    if provisioner is not None:  # its step hands itself back at the next wait or HTTP call
        provisioner.join(timeout=30)
    log.info("worker stopped")


def migrate(argv: list[str]) -> None:
    """`zm-worker vault-migrate --dsn …`: apply the client vault migrations by hand (local dev)."""
    parser = argparse.ArgumentParser(prog="zm-worker vault-migrate")
    parser.add_argument("--dsn", required=True, help="client database URL")
    args = parser.parse_args(argv)
    env = {role: f"{role.upper()}_DB_PASSWORD" for role in vault_migrate.ROLES}
    missing = [var for var in env.values() if not os.environ.get(var)]
    if missing:
        parser.error(f"set {', '.join(missing)}")
    applied = vault_migrate.apply(args.dsn, {role: os.environ[var] for role, var in env.items()})
    log.info("vault migrated: %s", ", ".join(applied) or "already up to date")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    logs.configure(os.environ.get("LOG_LEVEL", "info"))
    if args[:1] == ["vault-migrate"]:
        migrate(args[1:])
        return
    if args[:1] == ["reconcile"]:  # one pass by hand: `zm-worker reconcile`
        from zm_worker import reconcile

        reconcile.run_once()
        return
    if args:
        sys.exit(
            f"unknown command {args[0]!r}; usage: zm-worker [vault-migrate --dsn URL | reconcile]"
        )
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run(stop, float(os.environ.get("WORKER_TICK_SECONDS", "30")))


if __name__ == "__main__":  # pragma: no cover
    main()
