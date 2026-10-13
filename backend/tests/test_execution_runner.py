"""The hand-started engine runner (autotraderctl execution run): every refusal comes before TWS.

TWS is never contacted: the runner is driven through a transport around the
fake TWS, as the engine tests are.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.decision_ledger import DecisionSeeder
from tests.fakes.engine_harness import ACCOUNT, CLIENT_ID, SESSION, Clock, ledger_db, ny, promote
from tests.fakes.fake_tws import FakeTws
from trading.execution.live_executor import ENGINE_VERSION, EXIT_OK, EXIT_REFUSED, EXIT_RESTART
from trading.execution.runner import (
    EngineWrapper,
    IbapiTransport,
    RunnerConfig,
    TwsUnavailable,
    pending_migrations,
    run_session,
)
from trading.execution.tws_state import TwsMirror


CONFIG = RunnerConfig(account=ACCOUNT, client_id=CLIENT_ID, host="127.0.0.1", port=7497)
HELD = 300


class FakeTransport:
    """The fake TWS behind the runner's transport interface; counts connects and disconnects."""

    def __init__(self, tws: FakeTws, *, reachable: bool = True) -> None:
        self.tws = tws
        self.broker = tws
        self.mirror = TwsMirror(CLIENT_ID)
        self.reachable = reachable
        self.connects = 0
        self.disconnects = 0

    async def connect(self) -> None:
        self.connects += 1
        if not self.reachable:
            raise TwsUnavailable("no API connection to 127.0.0.1:7497 as client 18 within 30s")
        self.tws.attach(self.mirror)
        self.tws.connect()

    def disconnect(self) -> None:
        self.disconnects += 1
        self.tws.disconnect()


def hermetic(clock: Clock, *, step: float = 0, bridge: list[tuple[int, str]] | None = None,
             on_pause: Any = None) -> Any:
    """Test settings: fast timeouts, a stepping clock, and never the real process table or bridge."""

    def tune(engine: Any) -> None:
        engine.quote_timeout = 0.01
        engine.sync_timeout = 0.05
        engine.loop_seconds = 0
        engine.poll_seconds = 0.001
        engine.bridge_processes = lambda: list(bridge or [])

        async def no_heartbeat() -> None:
            return None

        async def no_screener_quote(bracket: Any) -> Decimal | None:
            return None

        async def pause(_: float) -> None:
            clock.advance(step)
            if on_pause is not None:
                on_pause(engine)

        engine.bridge_heartbeat = no_heartbeat
        engine.fallback_quote = no_screener_quote
        engine.pause = pause

    return tune


async def test_the_runner_refuses_without_a_grant_before_contacting_tws(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)  # no promotion recorded
    clock = Clock(ny(10, 0))
    lines: list[str] = []
    transport = FakeTransport(FakeTws(wrapper=None))

    code = await run_session(SESSION, config=CONFIG, transport=transport, clock=clock, sink=lines.append,
                             tune=hermetic(clock))

    assert code == EXIT_REFUSED and transport.connects == 0 and transport.tws.placed == []
    assert lines[-1].endswith("— TWS not contacted; exit 78") and "refusing: promotion_missing" in lines[-1]


async def test_the_runner_refuses_while_the_bridge_runs_before_contacting_tws(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)
    await promote()
    clock = Clock(ny(10, 0))
    lines: list[str] = []
    transport = FakeTransport(FakeTws(wrapper=None))

    code = await run_session(SESSION, config=CONFIG, transport=transport, clock=clock, sink=lines.append,
                             tune=hermetic(clock, bridge=[(4242, "python scripts_local/paper_bridge.py")]))

    assert code == EXIT_REFUSED and transport.connects == 0
    assert "refusing: bridge_running: the PAPER bridge is running (pid 4242)" in lines[-1]


async def test_the_runner_refuses_a_day_without_a_session_before_contacting_tws(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)
    await promote()
    saturday = date(2026, 10, 17)
    clock = Clock(ny(10, 0, day=saturday))
    lines: list[str] = []
    transport = FakeTransport(FakeTws(wrapper=None))

    code = await run_session(saturday, config=CONFIG, transport=transport, clock=clock, sink=lines.append,
                             tune=hermetic(clock))

    assert code == EXIT_REFUSED and transport.connects == 0
    assert "refusing: market_closed: 2026-10-17 is not an XNYS session — TWS not contacted; exit 78" in lines[-1]


async def test_the_runner_exits_75_when_tws_is_unreachable(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)
    await promote()
    clock = Clock(ny(10, 0))
    lines: list[str] = []
    transport = FakeTransport(FakeTws(wrapper=None), reachable=False)

    code = await run_session(SESSION, config=CONFIG, transport=transport, clock=clock, sink=lines.append,
                             tune=hermetic(clock))

    assert code == EXIT_RESTART and transport.connects == 1
    assert "TWS not reachable: no API connection to 127.0.0.1:7497 as client 18 within 30s — exit 75" in lines[-1]


async def test_the_runner_runs_one_session_on_the_fake_tws(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)
    await promote()
    clock = Clock(ny(10, 0))
    tws = FakeTws(wrapper=None)
    tws.clock = clock
    tws.quotes[HELD] = {"bid": 50.10, "ask": 50.12, "last": 50.11}
    tws.symbols[HELD] = "HELD"
    await DecisionSeeder(SESSION).accept("HELD", created=clock.value, con_id=HELD)
    transport = FakeTransport(tws)
    lines: list[str] = []

    def fill_the_entry_once_placed(engine: Any) -> None:
        parent = tws.orders.get((CLIENT_ID, 100))
        if parent is not None and parent.filled == 0:
            tws.fill(100, 100, "50.00")

    code = await run_session(SESSION, config=CONFIG, transport=transport, clock=clock, sink=lines.append,
                             tune=hermetic(clock, step=300, on_pause=fill_the_entry_once_placed))

    assert code == EXIT_OK and (transport.connects, transport.disconnects) == (1, 1)
    placed = [(order_id, sent.orderType, int(sent.totalQuantity)) for order_id, sent in tws.placed]
    assert placed[:3] == [(100, "LMT", 100), (101, "LMT", 100), (102, "STP", 100)]
    assert (101, "LMT", 100) in placed[3:]  # the 15:45 flatten reprices the target
    assert clock.value >= ny(15, 55)
    assert any(f"execution engine {ENGINE_VERSION} started for 2026-10-13" in line for line in lines)
    assert any(line.endswith("INFO session end: realized P&L 0") for line in lines)


def test_the_engine_wrapper_is_an_ibapi_wrapper_with_the_mirrors_callbacks() -> None:
    from ibapi.wrapper import EWrapper

    assert issubclass(EngineWrapper, EWrapper) and issubclass(EngineWrapper, TwsMirror)
    for callback in ("orderStatus", "openOrder", "completedOrder", "execDetails", "position", "error",
                     "nextValidId", "managedAccounts"):
        assert getattr(EngineWrapper, callback) is getattr(TwsMirror, callback), callback
    wrapper = EngineWrapper(CLIENT_ID)
    wrapper.tickSize(1, 0, 100)  # a callback the engine ignores: ibapi's default, no error
    wrapper.connectAck()


def test_the_ibapi_transport_gives_up_on_a_tws_that_never_answers() -> None:
    silent = IbapiTransport(CONFIG, timeout=0.1)
    silent.client.connect = lambda host, port, client_id: time.sleep(2)  # type: ignore[method-assign]
    started = time.monotonic()
    with pytest.raises(TwsUnavailable, match="within 0s"):
        asyncio.run(silent.connect())
    assert time.monotonic() - started < 1.5

    refused = IbapiTransport(CONFIG, timeout=0.5)
    refused.client.connect = lambda host, port, client_id: None  # type: ignore[method-assign]  # never connected
    with pytest.raises(TwsUnavailable):
        asyncio.run(refused.connect())


async def test_pending_migrations_are_read_only_and_refuse_anything_but_this_codes(tmp_path, monkeypatch) -> None:
    from trading.storage.migrations import MIGRATIONS

    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert pending_migrations(empty) == (f"migrations {[item.version for item in MIGRATIONS]} are not applied:"
                                         " apply them with a backup first")
    with sqlite3.connect(empty) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []  # nothing created

    current = await ledger_db(tmp_path, monkeypatch)
    assert pending_migrations(current) is None
    with sqlite3.connect(current) as connection:
        connection.execute("UPDATE autotrader_schema_migrations SET checksum='x' WHERE version=1")
    assert pending_migrations(current) == "applied migrations [1] differ from this code's (checksum mismatch)"
    with sqlite3.connect(current) as connection:
        connection.execute("INSERT INTO autotrader_schema_migrations VALUES (99, 'future', 'x', 'now')")
    assert pending_migrations(current) == "the database has migrations this code does not know: [99]"
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a database " * 100)
    assert (pending_migrations(corrupt) or "").startswith("the database is unreadable")


def test_the_runner_is_never_scheduled() -> None:
    from trading.operations import launchd, supervisor
    from trading.scheduler.sessions import market_session

    session = market_session(date(2026, 10, 13))
    assert session is not None
    commands = [plan.argv for plan in supervisor.plan_session(session)]
    commands += [plan.argv for plan in supervisor.always_on_plans()]
    assert commands and not [argv for argv in commands if "execution" in argv or any("runner" in a for a in argv)]
    assert launchd.SUPERVISOR_COMMAND == ("run",)  # the LaunchAgent only starts the supervisor
    backend = Path(__file__).resolve().parents[1]
    for module in ("trading/operations/supervisor.py", "trading/operations/launchd.py"):
        source = (backend / module).read_text()
        assert "execution run" not in source and "execution.runner" not in source, module
