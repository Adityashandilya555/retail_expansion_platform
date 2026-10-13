import json
import logging
import signal
import threading

import zm_worker.__main__ as worker_main
from zm_worker import logs
from zm_worker.__main__ import run


def test_json_formatter_tags_client_and_request_id() -> None:
    record = logging.LogRecord("zm_worker", logging.INFO, __file__, 1, "hello", None, None)
    record.client = "pilot-local"
    line = json.loads(logs.JsonFormatter().format(record))
    assert line["msg"] == "hello"
    assert line["level"] == "info"
    assert line["client"] == "pilot-local"
    assert line["request_id"] is None


def test_run_stops_when_event_is_set(caplog) -> None:
    stop = threading.Event()
    stop.set()
    with caplog.at_level(logging.INFO, logger="zm_worker"):
        run(stop, interval=0)
    assert [r.getMessage() for r in caplog.records] == ["worker started", "worker stopped"]


def test_configure_installs_json_handler() -> None:
    logs.configure("debug")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert isinstance(root.handlers[0].formatter, logs.JsonFormatter)


def test_tick_logs(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="zm_worker"):
        worker_main.tick()
    assert caplog.records[-1].getMessage() == "tick"


def test_main_reads_env_and_stops_on_signal(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_run(stop: threading.Event, interval: float) -> None:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        calls["stopped"] = stop.is_set()
        calls["interval"] = interval

    monkeypatch.setenv("WORKER_TICK_SECONDS", "5")
    monkeypatch.setattr(worker_main, "run", fake_run)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        worker_main.main()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    assert calls == {"stopped": True, "interval": 5.0}


def test_run_ticks_until_stopped(monkeypatch) -> None:
    stop = threading.Event()
    ticks: list[int] = []

    def fake_tick() -> None:
        ticks.append(1)
        if len(ticks) == 2:
            stop.set()

    monkeypatch.setattr(worker_main, "tick", fake_tick)
    run(stop, interval=0)
    assert len(ticks) == 2
