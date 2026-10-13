"""Test harness for the execution engine: a ledger database, a fake TWS and a fake clock.

TWS is never contacted. The engine runs against ``FakeTws`` through the real
``TwsMirror`` callbacks and a real SQLite ledger (migrations applied), so the
outbox, the triggers and the decision query are exercised exactly as in
production.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from tests.fakes.decision_ledger import DecisionSeeder
from tests.fakes.fake_tws import ACCOUNT, CLIENT_ID, FakeTws


SESSION = date(2026, 10, 13)
NY = ZoneInfo("America/New_York")
EVENT_LINE = re.compile(r"^\d{2}:\d{2}:\d{2} ET  (?P<kind>[A-Z]+) (?P<text>\S.*)$")
STRATEGY = "MOMO_HOD_V2"
DEFAULT_QUOTE = {"bid": 50.10, "ask": 50.12, "last": 50.11}


def ny(hour: int, minute: int, second: int = 0, *, day: date = SESSION) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=NY)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def stepping(clock: Clock, seconds: float) -> Callable[[float], Awaitable[None]]:
    """A loop pause that moves the fake clock instead of sleeping."""

    async def pause(_: float) -> None:
        clock.advance(seconds)

    return pause


async def ledger_db(tmp_path: Path, monkeypatch: Any) -> Path:
    from config import cfg
    from db.core import init_db

    path = tmp_path / "execution.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


async def promote(strategy: str = STRATEGY, *, account: str = ACCOUNT, code_digest: str | None = None) -> None:
    from trading.execution.promotion import grant_promotion

    await grant_promotion(strategy_name=strategy, account=account, actor="owner", reason="test promotion",
                          code_digest=code_digest)


@dataclass
class Harness:
    engine: Any
    tws: FakeTws
    clock: Clock
    seeder: DecisionSeeder
    lines: list[str] = field(default_factory=list)
    session_date: date = SESSION

    @property
    def mirror(self) -> Any:
        return self.engine.mirror

    def events(self, *, clear: bool = True) -> list[tuple[str, str]]:
        """Every line must be a notifier event line with a known KIND."""
        from trading.execution.live_executor import EVENT_KINDS

        parsed: list[tuple[str, str]] = []
        for line in self.lines:
            match = EVENT_LINE.match(line)
            assert match is not None, f"not a notifier event line: {line!r}"
            assert match["kind"] in EVENT_KINDS, line
            parsed.append((match["kind"], match["text"]))
        if clear:
            self.lines.clear()
        return parsed

    def critical(self, *, clear: bool = True) -> list[str]:
        return [text for kind, text in self.events(clear=clear) if kind == "CRITICAL"]

    async def decide(self, symbol: str, *, con_id: int, quote: dict[str, float] | None = None,
                     created: datetime | None = None, **intent: Any) -> str:
        """An approved decision of this session, created now (or at ``created``)."""
        self.tws.quotes.setdefault(con_id, dict(quote or DEFAULT_QUOTE))
        self.tws.symbols.setdefault(con_id, symbol)
        return await self.seeder.accept(symbol, created=created or self.clock.value, con_id=con_id, **intent)

    def bracket(self, symbol: str) -> Any:
        found = [item for item in self.engine.brackets.values() if item.symbol == symbol]
        assert found, f"no bracket for {symbol}"
        return found[-1]

    def placed_ids(self) -> list[int]:
        return [order_id for order_id, _ in self.tws.placed]

    async def restart(self, *, start: bool = True, clock: Clock | None = None) -> "Harness":
        """The engine process restarts: a fresh mirror and engine on the same ledger and TWS."""
        engine = make_engine(self.tws, clock or self.clock, self.lines, session_date=self.session_date)
        self.tws.clock = clock or self.clock
        self.tws.attach(engine.mirror)
        self.tws.connect()
        restarted = Harness(engine=engine, tws=self.tws, clock=clock or self.clock, seeder=self.seeder,
                            lines=self.lines, session_date=self.session_date)
        if start:
            await engine.start()
        return restarted


def make_engine(tws: FakeTws, clock: Clock, lines: list[str], *, session_date: date = SESSION,
                account: str = ACCOUNT) -> Any:
    from trading.execution.live_executor import LiveExecutor
    from trading.execution.promotion import EngineAuthority
    from trading.execution.tws_state import TwsMirror
    from trading.storage.execution_ledger import ExecutionLedger

    mirror = TwsMirror(CLIENT_ID)
    engine = LiveExecutor(
        broker=tws, mirror=mirror, ledger=ExecutionLedger(clock=clock), authority=EngineAuthority(account=account),
        account=account, client_id=CLIENT_ID, session_date=session_date, clock=clock, sink=lines.append,
    )
    engine.quote_timeout = 0.01
    engine.sync_timeout = 0.05
    engine.loop_seconds = 0
    engine.poll_seconds = 0.001

    async def no_screener_quote(bracket: Any) -> Decimal | None:
        return None

    engine.fallback_quote = no_screener_quote
    # Never the real process table: the live bridge may be running on this machine.
    engine.bridge_processes = lambda: []
    return engine


async def build(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    clock: Clock | None = None,
    promoted: bool = True,
    start: bool = True,
    session_date: date = SESSION,
    **tws_fields: Any,
) -> Harness:
    await ledger_db(tmp_path, monkeypatch)
    if promoted:
        await promote()
    clock = clock or Clock(ny(10, 0, 5, day=session_date))
    lines: list[str] = []
    tws = FakeTws(wrapper=None, **tws_fields)
    tws.clock = clock
    engine = make_engine(tws, clock, lines, session_date=session_date)
    tws.attach(engine.mirror)
    tws.session_day = session_date.strftime("%Y%m%d")
    tws.connect()
    harness = Harness(engine=engine, tws=tws, clock=clock, seeder=DecisionSeeder(session_date), lines=lines,
                      session_date=session_date)
    if start:
        await engine.start()
    return harness


__all__ = [
    "ACCOUNT", "CLIENT_ID", "DEFAULT_QUOTE", "SESSION", "STRATEGY", "Clock", "Harness", "build", "ledger_db",
    "make_engine", "ny", "promote", "stepping",
]
