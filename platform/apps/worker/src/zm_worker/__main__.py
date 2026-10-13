import logging
import os
import signal
import threading

from zm_worker import logs

log = logging.getLogger("zm_worker")


def tick() -> None:
    """One pass of the worker loop. Provisioning and reconciling arrive with S2-02a/b."""
    log.info("tick")


def run(stop: threading.Event, interval: float) -> None:
    log.info("worker started")
    while not stop.is_set():
        tick()
        stop.wait(interval)
    log.info("worker stopped")


def main() -> None:
    logs.configure(os.environ.get("LOG_LEVEL", "info"))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run(stop, float(os.environ.get("WORKER_TICK_SECONDS", "30")))


if __name__ == "__main__":
    main()
