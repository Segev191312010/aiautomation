"""Read-only data tools for the Telegram assistant: supervisor status, market
clock, positions with bot marks, P&L, orders, hot list, market context,
quotes with a screener fallback, and the dedicated TWS data client."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.contracts import AccountSnapshot, RawAccountPosition
from trading.market.screener import ScreenerPayload
from trading.market.snapshots import MarketSnapshot
from trading.operations.paths import UserPaths
from trading.operations.telegram_tools import (
    TELEGRAM_TWS_CLIENT_ID,
    TOOL_DEFINITIONS,
    DataTools,
    TWSDataSession,
    ToolError,
    age_text,
    format_manual_row,
    format_supervisor,
    market_clock,
    normalize_symbol,
    supervisor_state,
    tws_problem,
)

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
DAY = date(2026, 10, 13)
ACCOUNT = "DU1234567"


def et(hour: int, minute: int, second: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute, second=second), tzinfo=ET).astimezone(UTC)


NOW = et(10, 30)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


def position(symbol: str, quantity: str, *, con_id: int, cost: str = "100", last: str = "101") -> RawAccountPosition:
    qty = Decimal(quantity)
    return RawAccountPosition(
        paper_account_id=ACCOUNT,
        con_id=con_id,
        symbol=symbol,
        quantity=qty,
        market_price=Decimal(last),
        market_value=qty * Decimal(last),
        average_cost=Decimal(cost),
        unrealized_pnl=qty * (Decimal(last) - Decimal(cost)),
        realized_pnl=Decimal("0"),
        observed_at=NOW,
    )


def account(*positions: RawAccountPosition) -> AccountSnapshot:
    return AccountSnapshot(
        paper_account_id=ACCOUNT,
        client_id=TELEGRAM_TWS_CLIENT_ID,
        observed_at=NOW - timedelta(seconds=2),
        net_liquidation=Decimal("1000000"),
        available_funds=Decimal("800000"),
        realized_day_pnl=Decimal("-325.00"),
        unrealized_pnl=Decimal("150.00"),
        positions=positions,
    )


def snapshot(con_id: int = 265598, **fields: str) -> MarketSnapshot:
    values = {"prior_close": "224.37", "last": "227.15", "bid": "227.14", "ask": "227.16",
              "open": "225.10", "day_high": "228.00", "day_low": "223.90"}
    values.update(fields)
    return MarketSnapshot(
        con_id=con_id,
        observed_at=NOW - timedelta(seconds=1),
        volume_raw=Decimal("41203117"),
        **{key: Decimal(value) for key, value in values.items()},
    )


class FakeActor:
    def __init__(self, *, snap: MarketSnapshot | Exception | None = None, acct: AccountSnapshot | Exception | None = None,
                 connect_error: Exception | None = None) -> None:
        self.connected = False
        self.managed_accounts = frozenset({ACCOUNT})
        self.snap = snap
        self.acct = acct
        self.connect_error = connect_error
        self.connects = 0
        self.disconnects = 0
        self.snapshot_requests: list[int] = []
        self.detail_requests: list[str] = []

    async def connect(self) -> None:
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False

    async def account_snapshot(self) -> AccountSnapshot:
        if isinstance(self.acct, Exception):
            raise self.acct
        assert self.acct is not None
        return self.acct

    async def market_snapshot(self, con_id: int, *, timeout_seconds: float = 3.0) -> MarketSnapshot:
        self.snapshot_requests.append(con_id)
        if isinstance(self.snap, Exception):
            raise self.snap
        assert self.snap is not None
        return self.snap

    async def contract_details_for_symbol(self, symbol: str, primary_exchange: str | None) -> Any:
        self.detail_requests.append(symbol)

        class Details:
            con_id = 4815747

        return Details()


class FakeLedger:
    def __init__(self, rows: Sequence[Mapping[str, Any]] = (), heartbeat: float | None = 3.0) -> None:
        self.rows = list(rows)
        self.heartbeat = heartbeat

    async def open_orders(self) -> Sequence[Mapping[str, Any]]:
        return self.rows

    async def heartbeat_age(self, now: datetime) -> float | None:
        return self.heartbeat


class FakeKill:
    def __init__(self, stopped: bool = False) -> None:
        self.stopped = stopped

    async def is_stopped(self) -> bool:
        return self.stopped


def row(symbol: str, rank: int | None, *, con_id: int, score: str, last: str = "17.94", change: str = "13.67") -> dict[str, Any]:
    return {
        "symbol": symbol, "con_id": con_id, "primary_exchange": "NYSE", "source": "watchlist",
        "last": last, "bid": "17.93", "ask": "17.95", "prior_close": "15.78", "open": "16.10",
        "day_high": "18.20", "day_low": "16.00", "change_pct": change, "gap_pct": "2.03",
        "volume_shares": "4100000", "rvol": "4.97", "dollar_volume": "73554000.00",
        "pct_from_high": "-1.43", "score": score, "hot": rank is not None, "hot_rank": rank,
    }


def payload(*, market: dict[str, Any] | None = None, observed: datetime = et(15, 53)) -> ScreenerPayload:
    document: dict[str, Any] = {
        "schema": "screener.v1",
        "session_date": observed.astimezone(ET).date().isoformat(),
        "observed_at": observed.isoformat(),
        "sweep_seconds": 60.6,
        "universe_size": 518,
        "qualified": 3,
        "quoted": 3,
        "errors": 0,
        "rows": [
            row("FIGS", 1, con_id=111, score="0.9000"),
            row("AAPL", 2, con_id=265598, score="0.8000", last="227.15", change="1.24"),
            row("KO", None, con_id=8894, score="0.1000", last="61.00", change="-0.20"),
        ],
    }
    if market is not None:
        document["market"] = market
    return ScreenerPayload.model_validate(document)


def market_document() -> dict[str, Any]:
    from trading.market.context import build_market_context

    context = build_market_context(
        session_date=DAY,
        observed_at=et(10, 29),
        etf_snapshots={},
        watchlist_rows=[row("A", None, con_id=1, score="0"), row("B", None, con_id=2, score="0", change="-1.00")],
    )
    return context.to_payload()


def tools_for(
    tmp_path: Path,
    *,
    actor: FakeActor | None = None,
    screener: ScreenerPayload | None | Exception = None,
    ledger: FakeLedger | None = None,
    kill: FakeKill | None = None,
    alive: bool = True,
) -> DataTools:
    session = None
    if actor is not None:
        session = TWSDataSession(account=ACCOUNT, factory=lambda config: actor)

    async def read_screener() -> ScreenerPayload | None:
        if isinstance(screener, Exception):
            raise screener
        return screener

    return DataTools(
        paths=UserPaths(home=tmp_path),
        tws=session,
        tws_problem_text=None if actor is not None else "shadow configuration is unavailable",
        clock=lambda: NOW,
        kill_switch=kill or FakeKill(),
        ledger=ledger or FakeLedger(),
        read_screener=read_screener,
        pid_alive=lambda pid: alive,
    )


def write_bridge_state(tmp_path: Path, entries: dict[str, dict[str, Any]]) -> None:
    path = UserPaths(home=tmp_path).data_directory / "bridge" / f"{DAY.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_date": DAY.isoformat(), "submitted": entries}))


def bracket(symbol: str, status: str, **fields: Any) -> dict[str, Any]:
    base = {
        "symbol": symbol, "strategy": "momo", "strategy_name": "MOMO_HOD_V2", "status": status,
        "qty": 100, "entry": "26.77", "stop": "26.69", "target": "26.93", "filled": 0,
        "exit_filled": 0, "avg_entry": "0", "avg_exit": "0", "realized_pnl": "0",
        "exit_reason": "", "skipped": False,
    }
    base.update(fields)
    return base


SUPERVISOR_LOG = """\
2026-10-13T11:50:00+00:00 SUPERVISOR_START pid=4242 mode=daily
2026-10-13T11:50:00+00:00 CHILD_START telegram pid=4300
2026-10-13T11:55:00+00:00 SESSION_START date=2026-10-13
2026-10-13T11:55:00+00:00 CHILD_START screener pid=4301
2026-10-13T13:25:00+00:00 CHILD_START bridge pid=4302
2026-10-13T13:40:00+00:00 CHILD_START orb pid=4303
2026-10-13T14:02:00+00:00 CHILD_EXIT orb code=1 restart_in=95
2026-10-13T14:03:35+00:00 LAUNCH_FAILED momo error=OSError
2026-10-13T14:03:35+00:00 CHILD_EXIT momo code=127 restart_in=none
"""


def write_supervisor_log(tmp_path: Path, text: str = SUPERVISOR_LOG) -> None:
    log = UserPaths(home=tmp_path).log_directory / "supervisor.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text)


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "symbol"),
    [("AAPL", "AAPL"), ("$aapl", "AAPL"), (" tsla ", "TSLA"), ("BRK.B", "BRK B"), ("bf-b", "BF B"), ("BF B", "BF B")],
)
def test_symbols_are_normalized_to_ibkr_form(raw, symbol):
    assert normalize_symbol(raw) == symbol


@pytest.mark.parametrize("raw", ["", "apple inc", "1ABC", "AAPL;DROP", "TOOLONGNAME", "$"])
def test_non_symbols_are_refused(raw):
    with pytest.raises(ToolError):
        normalize_symbol(raw)


def test_ages_read_naturally():
    assert [age_text(s) for s in (3, 89, 600, 3900, 7200, 200000)] == [
        "3 s", "89 s", "10 min", "65 min", "2 h", "2 d"
    ]


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (et(10, 30), "OPEN · closes 16:00 ET"),
        (et(9, 0), "CLOSED · opens Tue 13 Oct 09:30 ET"),
        (et(16, 0), "CLOSED · opens Wed 14 Oct 09:30 ET"),
        (et(12, 0, day=date(2026, 10, 17)), "CLOSED · opens Mon 19 Oct 09:30 ET"),
        (et(12, 0, day=date(2026, 11, 26)), "CLOSED · opens Fri 27 Nov 09:30 ET"),
    ],
)
def test_market_clock(moment, expected):
    assert market_clock(moment).text() == expected


def test_half_day_close_is_reported():
    assert market_clock(et(11, 0, day=date(2026, 11, 27))).text() == "OPEN · closes 13:00 ET (half day)"


def test_supervisor_log_tells_each_childs_state():
    state = supervisor_state(SUPERVISOR_LOG.splitlines(), pid_alive=lambda pid: pid == 4242)

    assert state.alive and state.pid == 4242
    assert state.session == "start date=2026-10-13"
    children = {child.name: child for child in state.children}
    assert children["telegram"].running and children["bridge"].running
    assert children["orb"].detail == "exited with code 1, restarting in 95 s"
    assert children["momo"].detail == "exited with code 127, not restarting"
    text = "\n".join(format_supervisor(state, NOW))
    assert "Supervisor: running (pid 4242)" in text
    assert "⚠️ orb: exited with code 1, restarting in 95 s" in text


def test_a_dead_or_stopped_supervisor_is_flagged():
    crashed = supervisor_state(SUPERVISOR_LOG.splitlines(), pid_alive=lambda pid: False)
    assert not crashed.alive
    assert "NOT RUNNING" in format_supervisor(crashed, NOW)[0]

    stopped = supervisor_state(
        [*SUPERVISOR_LOG.splitlines(), "2026-10-13T20:20:00+00:00 SUPERVISOR_STOP reason=SIGTERM"],
        pid_alive=lambda pid: True,
    )
    assert not stopped.alive
    assert format_supervisor(stopped, NOW)[0].startswith("Supervisor: STOPPED")
    assert "(SIGTERM)" in format_supervisor(stopped, NOW)[0]
    assert format_supervisor(supervisor_state([]), NOW) == [
        "Supervisor: no supervisor.log yet (autotraderctl run has not run here)"
    ]


def test_tws_problems_are_explained_without_tracebacks():
    from trading.market.tws_actor import TWSCompetingSessionError, TWSConnectionError

    assert "logged in elsewhere" not in tws_problem(TWSConnectionError("x"))
    assert tws_problem(TWSCompetingSessionError("10197")) == "IBKR login active elsewhere (phone/web) — log out there"
    assert "not reachable" in tws_problem(TWSConnectionError("handshake failed"))
    assert tws_problem(ValueError("bad thing")) == "ValueError: bad thing"


def ledger_row(**fields: Any) -> Any:
    from trading.execution.manual_orders import ManualOrderRow

    base: dict[str, Any] = {
        "id": "a1b2c3" + "0" * 26, "created_at": NOW, "updated_at": NOW, "chat_id": 4242,
        "message_id": 1, "update_id": 1, "source": "command", "raw_text": "/buy 100 AAPL",
        "action": "BUY", "symbol": "AAPL", "con_id": 265598, "primary_exchange": "NASDAQ",
        "quantity": 100, "order_type": "MKT", "limit_price": None, "stop_price": None,
        "target_order_ref": None, "status": "queued", "reject_code": None, "error": None,
        "ibkr_order_id": None, "perm_id": None, "filled_qty": 0, "avg_fill_price": None,
        "expires_at": NOW + timedelta(seconds=90), "cancel_requested": False, "audit": {},
    }
    base.update(fields)
    return ManualOrderRow(**base)


@pytest.mark.parametrize(
    ("fields", "text"),
    [
        ({"order_type": "LMT", "limit_price": Decimal("180.5"), "status": "sent", "filled_qty": 40},
         "#a1b2c3 BUY 100 AAPL LMT 180.50 · filled 40 · sent"),
        ({"action": "SELL_STOP", "symbol": "TSLA", "quantity": 50, "order_type": "STP",
          "stop_price": Decimal("240")}, "#a1b2c3 SELL 50 TSLA STP 240.00 · queued"),
        ({"action": "CLOSE", "quantity": None, "order_type": None, "status": "sending"},
         "#a1b2c3 CLOSE AAPL bot bracket · sending"),
        ({"action": "CLOSE_ALL", "symbol": None, "con_id": None, "quantity": None, "order_type": None},
         "#a1b2c3 CLOSE ALL · queued"),
        ({"action": "SELL", "status": "partially_filled", "filled_qty": 30, "cancel_requested": True},
         "#a1b2c3 SELL 100 AAPL MKT · filled 30 · cancel requested · partially_filled"),
    ],
)
def test_manual_rows_render_compactly(fields, text):
    assert format_manual_row(ledger_row(**fields)) == text


# --------------------------------------------------------------------------
# TWS data client 21
# --------------------------------------------------------------------------


def test_the_assistant_reads_on_its_own_tws_client():
    assert TELEGRAM_TWS_CLIENT_ID == 21
    for reserved in (17, 18, 19, 20, 0):
        with pytest.raises(ValueError):
            TWSDataSession(account=ACCOUNT, client_id=reserved)


async def test_the_data_session_connects_once_reuses_and_drops_when_idle():
    actor = FakeActor(acct=account())
    configs: list[Any] = []
    clock = [100.0]

    def factory(config: Any) -> FakeActor:
        configs.append(config)
        return actor

    session = TWSDataSession(account=ACCOUNT, factory=factory, idle_seconds=60, monotonic=lambda: clock[0])
    async with session.use() as first:
        await first.account_snapshot()
    async with session.use() as second:
        assert second is first

    assert actor.connects == 1
    assert configs[0].client_id == 21 and configs[0].account == ACCOUNT and configs[0].port == 7497
    clock[0] += 30
    await session.close_if_idle()
    assert actor.disconnects == 0
    clock[0] += 31
    await session.close_if_idle()
    assert actor.disconnects == 1


async def test_a_connection_failure_drops_the_client_and_is_reported(tmp_path):
    from trading.market.tws_actor import TWSConnectionError

    actor = FakeActor(acct=TWSConnectionError("lost"))
    tools = tools_for(tmp_path, actor=actor)

    text = await tools.positions()

    assert text.startswith("Positions unavailable: IBKR account data unavailable: TWS is not reachable")
    assert actor.disconnects == 1


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


async def test_status_covers_market_supervisor_bridge_regime_and_kill_switch(tmp_path):
    write_supervisor_log(tmp_path)
    tools = tools_for(tmp_path, screener=payload(market=market_document()), kill=FakeKill(stopped=True))

    text = await tools.status()

    assert text.splitlines()[0] == "🤖 Status · Tue 13 Oct 10:30:00 ET"
    assert "Market: OPEN · closes 16:00 ET" in text
    assert "Supervisor: running (pid 4242)" in text and "✅ telegram: running" in text
    assert "Bridge: alive (heartbeat 3 s ago)" in text
    assert "Regime: NEUTRAL as of Tue 10:29:00 ET (60 s ago)" in text
    assert "Kill switch: ON" in text


async def test_status_says_when_orders_would_be_refused(tmp_path):
    tools = tools_for(tmp_path, ledger=FakeLedger(heartbeat=None), screener=payload())
    text = await tools.status()

    assert "Bridge: no heartbeat (not running) — manual orders are refused" in text
    assert "Regime: unknown (the newest screener sweep has no market context)" in text
    assert "Kill switch: off" in text

    stale = await tools_for(tmp_path, ledger=FakeLedger(heartbeat=45.0), screener=payload()).status()
    assert "last heartbeat 45 s ago — manual orders are refused" in stale


async def test_positions_mark_what_the_bot_holds(tmp_path):
    write_bridge_state(tmp_path, {"d1": bracket("BF B", "open", filled=100), "d2": bracket("FIGS", "closed")})
    actor = FakeActor(acct=account(
        position("BF B", "100", con_id=1, cost="26.77", last="26.80"),
        position("TSLA", "50", con_id=2, cost="250", last="255"),
        position("FIGS", "0", con_id=3),
    ))
    text = await tools_for(tmp_path, actor=actor).positions()

    lines = text.splitlines()
    assert lines[0].startswith("📒 Positions · IBKR Tue 10:29:58 ET (2 s ago)")
    assert lines[1].startswith("BF B: 100 sh @ avg 26.77") and lines[1].endswith("🤖 bot bracket")
    assert lines[2].startswith("TSLA: 50 sh @ avg 250.00") and lines[2].endswith("👤 yours")
    assert "unrealized +$250.00" in lines[2]
    assert not any(line.startswith("FIGS") for line in lines)  # flat positions are not holdings
    assert lines[-1] == "Net liquidation $1,000,000.00 · unrealized +$150.00"


async def test_positions_without_ibkr_say_why(tmp_path):
    text = await tools_for(tmp_path).positions()

    assert text == "Positions unavailable: shadow configuration is unavailable"


async def test_pnl_combines_bridge_trades_and_the_account(tmp_path):
    write_bridge_state(tmp_path, {
        "d1": bracket("BF B", "closed", filled=3741, exit_filled=3741, avg_entry="26.77", avg_exit="26.68",
                      exit_reason="stop", realized_pnl="-325.00"),
        "d2": bracket("FIGS", "open", filled=200, avg_entry="17.90", realized_pnl="0"),
        "d3": bracket("KO", "skipped", skipped=True),
    })
    text = await tools_for(tmp_path, actor=FakeActor(acct=account())).pnl()

    assert "• BF B (momo): closed 3,741 sh 26.77 → 26.68 (stop) · realized -$325.00" in text
    assert "• FIGS (momo): OPEN 200 sh @ 17.90 · realized $0.00" in text
    assert "KO" not in text
    assert "Bot realized today: -$325.00" in text
    assert "realized -$325.00 · unrealized +$150.00 · net liq $1,000,000.00" in text


async def test_pnl_without_bridge_state_or_ibkr(tmp_path):
    text = await tools_for(tmp_path).pnl()

    assert "• no bot trades today" in text
    assert "Account P&L unavailable: shadow configuration is unavailable" in text


async def test_orders_list_manual_rows_and_active_brackets(tmp_path):
    write_bridge_state(tmp_path, {"d1": bracket("BF B", "sent"), "d2": bracket("FIGS", "closed")})
    ledger = FakeLedger(rows=[ledger_row(action="SELL", symbol="TSLA", quantity=50)])
    text = await tools_for(tmp_path, ledger=ledger).orders()

    assert "Manual #a1b2c3 SELL 50 TSLA MKT · queued" in text
    assert "Bot MOMO_HOD_V2 BF B: sent · entry 100 @ 26.77 · stop 26.69 · target 26.93" in text
    assert "FIGS" not in text


async def test_hotlist_and_market_read_the_newest_sweep(tmp_path):
    tools = tools_for(tmp_path, screener=payload(market=market_document(), observed=et(10, 29)))

    hot = await tools.hotlist()
    assert hot.splitlines()[0] == "🔥 Hot list · sweep Tue 10:29:00 ET (60 s ago) · 2 hot of 3 quoted"
    assert hot.splitlines()[1] == "1. FIGS 17.94 · +13.67% · gap +2.03% · rvol 4.97"
    assert "KO" not in hot

    market = await tools.market()
    assert market.startswith("📈 Market · 10:29:00 ET · NEUTRAL")
    assert "Indexes: SPY ?, QQQ ?, IWM ?, DIA ?" in market
    assert "Breadth: 50.00% of watchlist up (1 up / 1 down)" in market


async def test_market_without_context_or_sweeps_says_so(tmp_path):
    assert (await tools_for(tmp_path, screener=None).market()).startswith("No screener sweep has been recorded")
    assert "No market context in the newest screener sweep" in await tools_for(tmp_path, screener=payload()).market()
    assert await tools_for(tmp_path, screener=None).hotlist() == "No screener sweep has been recorded yet."
    assert (await tools_for(tmp_path, screener=OSError("db")).hotlist()) == "Hot list unavailable (OSError)"


async def test_quote_is_live_when_ibkr_has_real_time_data(tmp_path):
    actor = FakeActor(snap=snapshot())
    text = await tools_for(tmp_path, actor=actor, screener=payload()).quote("aapl")

    assert text.splitlines() == [
        "AAPL 227.15 · +1.24% vs close 224.37",
        "bid 227.14 / ask 227.16 (spread 0.01%)",
        "Day: open 225.10 · high 228.00 · low 223.90 · volume 41,203,117",
        "Real-time IBKR snapshot · Tue 10:29:59 ET (1 s ago)",
    ]
    assert actor.snapshot_requests == [265598]  # conId from the screener row


async def test_quote_falls_back_to_the_screener_and_says_it_is_not_live(tmp_path):
    from trading.market.tws_actor import TWSRequestError

    actor = FakeActor(snap=TWSRequestError("real-time market data is required; delayed/frozen data rejected"))
    text = await tools_for(tmp_path, actor=actor, screener=payload()).quote("AAPL")

    assert text.splitlines()[0] == (
        "AAPL: no live quote (TWSRequestError: real-time market data is required; delayed/frozen data rejected)."
    )
    assert "Last screener sweep Tue 15:53:00 ET" in text and "NOT live" in text
    assert "last 227.15 · +1.24% vs close 15.78" in text


async def test_quote_for_a_symbol_outside_the_universe_resolves_through_tws(tmp_path, monkeypatch):
    from trading.operations import telegram_tools

    async def no_universe(symbol: str) -> int | None:
        return None

    monkeypatch.setattr(telegram_tools, "universe_con_id", no_universe)
    actor = FakeActor(snap=snapshot(con_id=4815747))
    text = await tools_for(tmp_path, actor=actor, screener=payload()).quote("NVDA")

    assert actor.detail_requests == ["NVDA"] and actor.snapshot_requests == [4815747]
    assert text.startswith("NVDA 227.15")


async def test_quote_without_any_data_says_so(tmp_path):
    text = await tools_for(tmp_path, screener=None).quote("ZZZZ")

    assert text == "ZZZZ: no live quote (shadow configuration is unavailable), and ZZZZ is not in the newest screener sweep."
    assert await tools_for(tmp_path).quote("not a symbol") == "'not a symbol' does not look like a US stock symbol"


async def test_signals_and_report_read_todays_database(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "tools.db"))
    await init_db()
    tools = tools_for(tmp_path)

    assert await tools.signals() == "No strategy signals reached the AI review on Tue 13 Oct."
    report = await tools.report()
    assert report.startswith("📊 Daily report · Tue 13 Oct 2026")
    assert report.endswith("(so far, as of 10:30 ET)")


async def test_run_tool_dispatches_and_reports_unknown_or_bad_input(tmp_path):
    tools = tools_for(tmp_path, screener=payload())

    assert (await tools.run_tool("get_hotlist", {})).startswith("🔥 Hot list")
    assert (await tools.run_tool("get_quote", {"symbol": "KO"})).startswith("KO: no live quote")
    with pytest.raises(ToolError, match="unknown tool"):
        await tools.run_tool("place_order", {"symbol": "AAPL"})
    with pytest.raises(ToolError, match="needs a symbol"):
        await tools.run_tool("get_quote", {})
    assert {tool["name"] for tool in TOOL_DEFINITIONS} >= {"get_status", "get_quote"}
