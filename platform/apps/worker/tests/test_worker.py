import json
import logging
import threading

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
