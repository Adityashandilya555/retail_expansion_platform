"""The worker's reconciler wiring (#26): the tick runs it at most once a minute and survives it
failing; one client's failure doesn't stop the others. What a pass does to a stuck submission
runs against the stack in apps/api/tests/test_tasks.py (`-m integration`)."""

import asyncio
import logging
from collections import Counter
from types import SimpleNamespace

import pytest

import zm_worker.__main__ as worker_main
from zm_worker import reconcile


@pytest.fixture
def ticks(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql://unused/postgres")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", "unused")
    monkeypatch.setattr(worker_main, "_last_reconcile", None)
    monkeypatch.setattr(reconcile, "run_once", lambda older_than=120.0: calls.append(older_than))
    return calls


def test_tick_reconciles_at_most_once_a_minute(
    ticks: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(worker_main.time, "monotonic", lambda: now[0])
    worker_main.tick()
    now[0] += 30
    worker_main.tick()
    assert len(ticks) == 1
    now[0] += 31
    worker_main.tick()
    assert len(ticks) == 2


@pytest.mark.parametrize("missing", ["PLATFORM_DATABASE_URL", "SECRETS_ENCRYPTION_KEY"])
def test_tick_skips_until_configured(
    ticks: list[float], monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    worker_main.tick()
    assert ticks == []


def test_a_failing_pass_is_logged_and_the_loop_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql://unused/postgres")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", "unused")
    monkeypatch.setattr(worker_main, "_last_reconcile", None)

    def boom(older_than: float = 120.0) -> None:
        raise OSError("registry down")

    monkeypatch.setattr(reconcile, "run_once", boom)
    with caplog.at_level(logging.ERROR, logger="zm_worker"):
        worker_main.tick()
    assert caplog.records[-1].getMessage() == "reconcile pass failed"


def test_reconcile_command_runs_one_pass(ticks: list[float]) -> None:
    worker_main.main(["reconcile"])
    assert ticks == [120.0]


class FakeEngine:
    def connect(self) -> "FakeEngine":
        return self

    async def __aenter__(self) -> "FakeEngine":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeResolver:
    closed = False

    def __init__(self, _registry: object) -> None:
        pass

    async def context(self, row: SimpleNamespace) -> SimpleNamespace:
        if row.slug == "broken":
            raise OSError("vault down")
        return SimpleNamespace(slug=row.slug)

    async def aclose(self) -> None:
        FakeResolver.closed = True


def test_one_clients_failure_doesnt_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        SimpleNamespace(slug="broken"),
        SimpleNamespace(slug="pilot"),
        SimpleNamespace(slug="qsr"),
    ]
    seen: list[tuple[str, float]] = []

    async def fake_ready(conn: object) -> list[SimpleNamespace]:
        return rows

    async def fake_reconcile(ctx: SimpleNamespace, older_than: float) -> Counter[str]:
        seen.append((ctx.slug, older_than))
        return Counter(committed=1) if ctx.slug == "pilot" else Counter()

    monkeypatch.setattr(reconcile, "PlatformRegistry", lambda: SimpleNamespace(engine=FakeEngine))
    monkeypatch.setattr(reconcile, "Resolver", FakeResolver)
    monkeypatch.setattr(reconcile, "ready_clients", fake_ready)
    monkeypatch.setattr(reconcile.tasks, "reconcile", fake_reconcile)

    out = asyncio.run(reconcile.reconcile_all(older_than=0))
    assert out == {"broken": Counter(error=1), "pilot": Counter(committed=1), "qsr": Counter()}
    assert seen == [("pilot", 0), ("qsr", 0)]
    assert FakeResolver.closed


def test_an_unconfigured_reconciler_is_reported_once(
    ticks: list[float], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("SECRETS_ENCRYPTION_KEY")
    monkeypatch.setattr(worker_main, "_warned", False)
    with caplog.at_level(logging.WARNING, logger="zm_worker"):
        worker_main.tick()
        worker_main.tick()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["reconciler off: set SECRETS_ENCRYPTION_KEY"]
    assert ticks == []
