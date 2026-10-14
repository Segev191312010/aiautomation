"""Review R1: a finished non-dynamic run must not restart all day.

A non-dynamic run (orb-v2, gap-v2) returns as soon as every candidate is
terminal, or at once with none. The supervisor restarts any clean exit
before a child's planned finish, so an early return used to restart about
every 95 s to 5 minutes until the window end: some 72 restarts, reconnects
and phone lines a day. Now the run idles (TWS closed) until its cutoff and
exits there, its planned finish.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import itertools
import signal
from types import SimpleNamespace

import pytest

from tests.test_shadow_service import TUESDAY, et, runtime_config
from trading.operations import shadow_service
from trading.operations import supervisor as sup
from trading.operations.notify_service import supervisor_event_text
from trading.orchestration.session_runner import SessionReport
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session


class _Child:
    """Exits 0 after ``lifetime`` (or at its cutoff), or when signalled."""

    def __init__(self, pid: int, lifetime: timedelta | None, clock) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._lifetime = lifetime
        self._clock = clock
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        if self._lifetime is not None:
            await self._clock.sleep(self._lifetime.total_seconds())
            if self.returncode is None:
                self.returncode = 0
                self._exited.set()
        await self._exited.wait()
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, signum: int) -> None:
        if self.returncode is None:
            self.returncode = 0 if signum == signal.SIGTERM else -signum
            self._exited.set()


async def _day(lifetime_for) -> list[tuple[datetime, str]]:
    session = market_session(TUESDAY)
    plans = [plan for plan in sup.plan_session(session) if plan.name in {"gap-v2", "orb-v2", "momo"}]
    clock = sup._SimulatedClock(datetime.combine(TUESDAY, time(7, 0), tzinfo=MARKET_TIMEZONE).astimezone(timezone.utc))
    events: list[tuple[datetime, str]] = []
    pids = itertools.count(1)

    class Launcher:
        async def start(self, plan):
            return _Child(next(pids), lifetime_for(plan, clock.now), clock)

    supervisor = sup.Supervisor(
        launcher=Launcher(),
        emit=lambda line: events.append((clock.now, line)),
        planner=lambda _session: plans,
        clock=clock,
        sleep=clock.sleep,
        sleep_chunk_seconds=1.0,
    )
    await supervisor.run(session_date=TUESDAY)
    return events


def _starts(events, name: str) -> int:
    return sum(1 for _at, line in events if f"CHILD_START {name} " in line)


@pytest.mark.full_schedule
@pytest.mark.anyio
async def test_an_early_exit_restarts_all_day_and_idling_to_the_cutoff_does_not():
    early = await _day(lambda plan, _now: timedelta(seconds=2) if plan.name in {"gap-v2", "orb-v2"} else None)
    # The probe's finding: the loop the supervisor's restart policy makes of an early exit.
    assert _starts(early, "gap-v2") > 60 and _starts(early, "orb-v2") > 60
    phone = [supervisor_event_text(line) for _at, line in early if "CHILD_EXIT gap-v2" in line]
    # Each restart was a phone line (now held to one per half hour by the notifier).
    assert sum(text is not None for text in phone) > 60

    # The fix: a finished run idles to its cutoff instead of exiting early; it never
    # exits before its planned finish, so the supervisor starts it once.
    fixed = await _day(lambda _plan, _now: None)
    assert _starts(fixed, "gap-v2") == _starts(fixed, "orb-v2") == _starts(fixed, "momo") == 1
    exits = [line for _at, line in fixed if "CHILD_EXIT gap-v2" in line or "CHILD_EXIT orb-v2" in line]
    assert len(exits) == 2 and all(line.endswith("code=0 restart_in=none") for line in exits)


def _fakes(monkeypatch, order: list[str]):
    class FakeActor:
        connected = True

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def connect(self) -> None:
            order.append("connect")

        async def disconnect(self) -> None:
            order.append("disconnect")

        async def preflight(self, *, required_data_lines: int):
            return SimpleNamespace(ready=True, reasons=())

        async def account_snapshot(self):
            raise AssertionError("unused")

    class FakeMarket:
        def __init__(self, **_kwargs) -> None:
            pass

        async def load_contract(self, con_id: int) -> None:
            return None

    class FakeRunner:
        def __init__(self, **_kwargs) -> None:
            pass

        async def run(self, **kwargs):
            order.append("run")
            return SessionReport(run_id=str(kwargs["run_id"]), session_date=TUESDAY)

    async def strategy(**_kwargs):
        return SimpleNamespace(strategy_id="f" * 32)

    usage = SimpleNamespace(usage=SimpleNamespace(calls=0, cost_usd=Decimal("0")))
    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    monkeypatch.setattr(shadow_service, "read_anthropic_api_key", lambda: "sk-test")
    monkeypatch.setattr(shadow_service, "build_anthropic_client", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "AnthropicMessagesTransport", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "StructuredAIReviewProvider", lambda **_kwargs: usage)
    monkeypatch.setattr(shadow_service, "TWSPaperActor", FakeActor)
    monkeypatch.setattr(shadow_service, "PaperIBKRMarketData", FakeMarket)
    monkeypatch.setattr(shadow_service, "ShadowPipeline", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "RepositoryBackedShadowRunner", lambda **_kwargs: object())
    monkeypatch.setattr(shadow_service, "ShadowSessionRunner", FakeRunner)
    monkeypatch.setattr(shadow_service, "source_sha", lambda: "abc1234")


@pytest.mark.parametrize(
    ("strategy_name", "universe", "idles"),
    [("gap-v2", "all-gappers", True), ("orb-v2", "scanner", True), ("momo", "watchlist", False)],
)
@pytest.mark.anyio
async def test_a_finished_run_idles_until_its_cutoff_with_tws_closed(monkeypatch, strategy_name, universe, idles):
    order: list[str] = []
    _fakes(monkeypatch, order)
    clock = {"now": et(TUESDAY, 11, 0)}

    async def sleep(seconds: float) -> None:
        order.append(f"sleep:{seconds:.0f}")
        clock["now"] += timedelta(seconds=seconds)

    await shadow_service.run_shadow_session(
        config=runtime_config(), owner_id="demo", session=market_session(TUESDAY),
        clock=lambda: clock["now"], sleep=sleep, strategy_name=strategy_name, universe=universe,
    )

    cutoff = shadow_service.strategy_entry_window(strategy_name, TUESDAY)[1]
    assert order[:2] == ["connect", "run"] and order[2] == "disconnect"
    if idles:
        assert order[3:] == [f"sleep:{(cutoff - et(TUESDAY, 11, 0)).total_seconds():.0f}"]
        assert clock["now"] == cutoff
    else:
        assert order[3:] == []


@pytest.mark.anyio
async def test_a_run_at_or_after_its_cutoff_never_waits():
    calls: list[float] = []

    async def sleep(seconds: float) -> None:
        calls.append(seconds)

    cutoff = et(TUESDAY, 15, 30)
    await shadow_service.idle_until_cutoff("gap-v2", cutoff, dynamic=False, clock=lambda: cutoff, sleep=sleep)
    await shadow_service.idle_until_cutoff("momo", cutoff, dynamic=True, clock=lambda: et(TUESDAY, 10, 0), sleep=sleep)
    assert calls == []


@pytest.mark.anyio
async def test_a_restart_at_the_window_end_creates_no_run(monkeypatch):
    """Live 2026-10-13: gap.log showed a fresh run (r0936 ... r1027) per restart.

    Before R1 each early exit was restarted and re-selected a new snapshot (a
    conflict by construction), then the mid-session deploy changed the source
    sha (`stored run differs from immutable request`): a fresh labelled run
    every 5 minutes. Now the idling child exits 0 at its cutoff, its planned
    finish (no restart); and a child started again at the window end is
    refused by the closed window before any database write: no run id, no
    fresh labelled run, no retry (SessionAborted is final).
    """
    from trading.orchestration.session_runner import SessionAborted

    order: list[str] = []
    _fakes(monkeypatch, order)
    strategies: list[str] = []

    async def strategy(**_kwargs):
        strategies.append("ensure_strategy")
        return SimpleNamespace(strategy_id="f" * 32)

    monkeypatch.setattr(shadow_service, "ensure_strategy", strategy)
    cutoff = shadow_service.strategy_entry_window("gap-v2", TUESDAY)[1]

    with pytest.raises(SessionAborted, match="gap-v2 entry window for 2026-10-13 closed at"):
        await shadow_service.run_shadow_day(
            config=runtime_config(), owner_id="demo", session=market_session(TUESDAY),
            strategy_name="gap-v2", universe="all-gappers", clock=lambda: cutoff,
        )

    assert order == [] and strategies == []
    # The supervisor's side of it: a clean exit at the cutoff is the planned finish.
    plan = next(p for p in sup.plan_session(market_session(TUESDAY)) if p.name == "gap-v2")
    assert plan.finishes_at == cutoff
