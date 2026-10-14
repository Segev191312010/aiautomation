"""Streaming market snapshots, symbol resolution, and competing-session errors."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_tws_actor import FakeTWSClient, config
from trading.market.snapshots import MarketSnapshot, MarketSnapshotError
from trading.market.tws_actor import (
    MARKET_SNAPSHOT_SETTLE_SECONDS,
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSPaperActor,
    TWSRequestError,
    _CallbackSink,
    _official_client_factory,
    _official_symbol_contract_factory,
)
from trading.market.ibkr_boundary import is_competing_session_error, is_competing_session_report

Script = Callable[[Any, int], None]


def live_burst(callbacks: Any, request_id: int) -> None:
    """The AAPL tick order TWS sent on 2026-10-12, including the 2176 warning."""
    callbacks.marketDataType(request_id, 1)
    callbacks.tickPrice(request_id, 1, 340.94, None)
    callbacks.tickSize(request_id, 0, 1)
    callbacks.tickPrice(request_id, 2, 341.04, None)
    callbacks.tickSize(request_id, 3, 1)
    callbacks.tickPrice(request_id, 4, 340.995, None)
    callbacks.tickSize(request_id, 5, 2)
    callbacks.error(
        request_id,
        2176,
        "Warning: Your API version does not support fractional share size rules. "
        "Trimmed value 7729883.797717 to 7729883",
    )
    callbacks.tickSize(request_id, 8, 7729883)
    callbacks.tickPrice(request_id, 6, 342.63, None)
    callbacks.tickPrice(request_id, 7, 337.6, None)
    callbacks.tickPrice(request_id, 9, 336.640015, None)
    callbacks.tickPrice(request_id, 14, 337.83, None)


def ticks(*events: tuple[str, int, float]) -> Script:
    def script(callbacks: Any, request_id: int) -> None:
        for kind, tick_type, value in events:
            if kind == "type":
                callbacks.marketDataType(request_id, int(value))
            elif kind == "price":
                callbacks.tickPrice(request_id, tick_type, value, None)
            else:
                callbacks.tickSize(request_id, tick_type, value)

    return script


class SnapshotClient(FakeTWSClient):
    def __init__(self, callbacks: Any, script: Script = live_burst) -> None:
        super().__init__(callbacks)
        self.script = script
        self.details_requests: list[tuple[int, Any]] = []
        self.details_script: Callable[[Any, int, Any], None] | None = None

    def reqMktData(self, *args: object) -> None:
        self.quote_calls.append(args)
        self.script(self.callbacks, int(args[0]))  # type: ignore[arg-type]

    def reqContractDetails(self, request_id: int, contract: object) -> None:
        self.details_requests.append((request_id, contract))
        if self.details_script is None:
            super().reqContractDetails(request_id, contract)
        else:
            self.details_script(self.callbacks, request_id, contract)


async def connected(script: Script = live_burst, **actor_kwargs: Any) -> tuple[TWSPaperActor, SnapshotClient]:
    holder: list[SnapshotClient] = []

    def factory(callbacks: Any) -> SnapshotClient:
        client = SnapshotClient(callbacks, script)
        holder.append(client)
        return client

    actor = TWSPaperActor(config(), client_factory=factory, **actor_kwargs)
    await actor.connect()
    return actor, holder[0]


@pytest.mark.asyncio
async def test_full_live_burst_resolves_without_settle_wait_and_releases_the_line() -> None:
    actor, client = await connected()
    started = time.monotonic()

    snapshot = await actor.market_snapshot(265598)

    assert time.monotonic() - started < MARKET_SNAPSHOT_SETTLE_SECONDS
    assert snapshot == MarketSnapshot(
        con_id=265598,
        observed_at=snapshot.observed_at,
        prior_close=Decimal("336.640015"),
        last=Decimal("340.995"),
        bid=Decimal("340.94"),
        ask=Decimal("341.04"),
        open=Decimal("337.83"),
        day_high=Decimal("342.63"),
        day_low=Decimal("337.6"),
        volume_raw=Decimal("7729883"),
    )
    assert snapshot.observed_at.tzinfo is not None
    request_id, _contract, generic_ticks, snapshot_flag, regulatory, _options = client.quote_calls[0]
    # Streaming subscription with default ticks; released on success.
    assert (generic_ticks, snapshot_flag, regulatory) == ("", False, False)
    assert client.cancelled_quotes == [request_id]


@pytest.mark.asyncio
async def test_missing_session_statistics_wait_only_for_the_settle_window() -> None:
    script = ticks(("type", 0, 1), ("price", 4, 25.5), ("size", 8, 1200), ("price", 9, 25.0))
    actor, client = await connected(script)
    started = time.monotonic()

    snapshot = await actor.market_snapshot(7, timeout_seconds=2.0)

    elapsed = time.monotonic() - started
    assert MARKET_SNAPSHOT_SETTLE_SECONDS * 0.8 <= elapsed < 1.0
    assert (snapshot.last, snapshot.prior_close, snapshot.volume_raw) == (
        Decimal("25.5"),
        Decimal("25.0"),
        Decimal("1200"),
    )
    assert snapshot.open is None and snapshot.day_high is None and snapshot.bid is None
    assert client.cancelled_quotes == [client.quote_calls[0][0]]


@pytest.mark.asyncio
async def test_timeout_returns_price_and_prior_close_without_volume() -> None:
    script = ticks(("type", 0, 1), ("price", 1, 50.0), ("price", 2, 50.1), ("price", 9, 49.0))
    actor, client = await connected(script)

    snapshot = await actor.market_snapshot(7, timeout_seconds=0.1)

    assert snapshot.last is None and snapshot.volume_raw is None
    assert snapshot.reference_price == Decimal("50.05")
    assert client.cancelled_quotes == [client.quote_calls[0][0]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("script", "missing"),
    [
        (ticks(("type", 0, 1), ("price", 4, 50.0), ("size", 8, 10)), "prior close"),
        (ticks(("type", 0, 1), ("price", 1, 50.0), ("price", 9, 49.0)), "a last trade or bid/ask"),
        (ticks(("price", 4, 50.0), ("price", 9, 49.0), ("size", 8, 10)), "real-time confirmation"),
    ],
)
async def test_timeout_without_minimum_data_raises_and_releases(script: Script, missing: str) -> None:
    actor, client = await connected(script)

    with pytest.raises(TWSRequestError, match=missing):
        await actor.market_snapshot(7, timeout_seconds=0.1)
    assert client.cancelled_quotes == [client.quote_calls[0][0]]


@pytest.mark.asyncio
@pytest.mark.parametrize("data_type", [2, 3, 4])
async def test_delayed_or_frozen_data_is_rejected(data_type: int) -> None:
    script = ticks(("type", 0, data_type), ("price", 4, 50.0), ("price", 9, 49.0), ("size", 8, 1))
    actor, client = await connected(script)

    with pytest.raises(TWSRequestError, match="real-time"):
        await actor.market_snapshot(7)
    assert client.cancelled_quotes == [client.quote_calls[0][0]]


@pytest.mark.asyncio
async def test_unknown_values_are_absent_not_zero() -> None:
    script = ticks(
        ("type", 0, 1),
        ("price", 1, -1.0),
        ("price", 2, 0.0),
        ("price", 4, 12.0),
        ("price", 9, 11.0),
        ("size", 8, -1),
    )
    actor, _client = await connected(script)

    snapshot = await actor.market_snapshot(7, timeout_seconds=0.1)

    assert snapshot.bid is None and snapshot.ask is None and snapshot.volume_raw is None


@pytest.mark.asyncio
async def test_competing_session_code_10197_is_a_distinct_error() -> None:
    def blocked(callbacks: Any, request_id: int) -> None:
        callbacks.marketDataType(request_id, 1)
        callbacks.error(request_id, 10197, "No market data during competing live session")

    actor, client = await connected(blocked)

    with pytest.raises(TWSCompetingSessionError, match="10197"):
        await actor.market_snapshot(7)
    assert client.cancelled_quotes == [client.quote_calls[0][0]]
    assert actor.last_error is not None and "10197" in actor.last_error


@pytest.mark.asyncio
async def test_connection_level_competing_session_fails_a_pending_snapshot_fast() -> None:
    def blocked(callbacks: Any, request_id: int) -> None:
        callbacks.error(
            -1,
            165,
            "Historical Market Data Service query message:"
            "Trading TWS session is connected from a different IP address",
        )

    actor, _client = await connected(blocked)
    started = time.monotonic()

    with pytest.raises(TWSCompetingSessionError):
        await actor.market_snapshot(7, timeout_seconds=2.0)
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_error_after_minimum_data_fails_closed_during_settle() -> None:
    def late_error(callbacks: Any, request_id: int) -> None:
        ticks(("type", 0, 1), ("price", 4, 20.0), ("price", 9, 19.0), ("size", 8, 5))(
            callbacks, request_id
        )
        asyncio.get_running_loop().call_later(
            0.05, callbacks.error, request_id, 10197, "No market data during competing live session"
        )

    actor, _client = await connected(late_error)

    with pytest.raises(TWSCompetingSessionError):
        await actor.market_snapshot(7, timeout_seconds=2.0)


@pytest.mark.asyncio
async def test_ordinary_request_errors_stay_plain_request_errors() -> None:
    def rejected(callbacks: Any, request_id: int) -> None:
        callbacks.error(request_id, 354, "Requested market data is not subscribed")

    actor, _client = await connected(rejected)

    with pytest.raises(TWSRequestError) as raised:
        await actor.market_snapshot(7)
    assert not isinstance(raised.value, TWSCompetingSessionError)


@pytest.mark.asyncio
async def test_disconnect_and_connection_closed_fail_pending_snapshots() -> None:
    actor, _client = await connected(ticks())
    pending = asyncio.create_task(actor.market_snapshot(7, timeout_seconds=2.0))
    await asyncio.sleep(0.02)
    await actor.disconnect()
    with pytest.raises(TWSConnectionError):
        await pending

    actor, client = await connected(ticks())
    pending = asyncio.create_task(actor.market_snapshot(7, timeout_seconds=2.0))
    await asyncio.sleep(0.02)
    client.callbacks.connectionClosed()
    with pytest.raises(TWSConnectionError):
        await pending


@pytest.mark.asyncio
async def test_snapshot_rejects_bad_arguments_and_requires_connection() -> None:
    actor = TWSPaperActor(config(), client_factory=lambda callbacks: SnapshotClient(callbacks))
    with pytest.raises(TWSConnectionError):
        await actor.market_snapshot(7)
    await actor.connect()
    with pytest.raises(TWSRequestError, match="con_id"):
        await actor.market_snapshot(0)
    with pytest.raises(TWSRequestError, match="timeout"):
        await actor.market_snapshot(7, timeout_seconds=0)


@pytest.mark.asyncio
async def test_late_ticks_after_release_are_ignored_and_quotes_ignore_sizes() -> None:
    actor, client = await connected()
    await actor.market_snapshot(7)
    request_id = int(client.quote_calls[0][0])  # type: ignore[arg-type]
    client.callbacks.tickPrice(request_id, 4, 1.0, None)
    client.callbacks.tickSize(request_id, 8, 1)

    def quote(callbacks: Any, quote_id: int) -> None:
        callbacks.marketDataType(quote_id, 1)
        callbacks.tickSize(quote_id, 0, 3)
        callbacks.tickPrice(quote_id, 1, 100.0, None)
        callbacks.tickPrice(quote_id, 2, 100.25, None)

    client.script = quote
    result = await actor.quote_snapshot(7, min_tick=Decimal("0.01"))
    assert (result.bid, result.ask) == (Decimal("100.0"), Decimal("100.25"))


def test_tick_size_is_forwarded_by_sink_and_official_client() -> None:
    received: list[tuple[int, int, Any]] = []
    actor = SimpleNamespace(_on_tick_size=lambda *args: received.append(args))
    sink = _CallbackSink(actor)  # type: ignore[arg-type]
    sink.tickSize(5, 8, 123)
    sink.tick_size(6, 8, 7)
    assert received == [(5, 8, 123), (6, 8, 7)]

    forwarded: list[tuple[Any, ...]] = []
    client = _official_client_factory(SimpleNamespace(tickSize=lambda *args: forwarded.append(args)))  # type: ignore[arg-type]
    client.tickSize(9, 8, 4567)  # type: ignore[attr-defined]
    assert forwarded == [(9, 8, 4567)]


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        (10197, "No market data during competing live session", True),
        (165, "HMDS query message:Trading TWS session is connected from a different IP address", True),
        (162, "HMDS error message:Trading TWS session is connected from a different IP address", True),
        (165, "Historical Market Data Service query message:HMDS query returned no data", False),
        (162, "Historical data request pacing violation", False),
        (200, "connected from a different IP address", False),
    ],
)
def test_competing_session_recognition(code: int, message: str, expected: bool) -> None:
    assert is_competing_session_error(code, message) is expected
    assert is_competing_session_report(f"TWS error {code}: {message}") is expected


def test_competing_session_report_rejects_unstructured_text() -> None:
    assert is_competing_session_report(None) is False
    assert is_competing_session_report("") is False
    assert is_competing_session_report("No market data during competing live session") is False


ELSEWHERE_162 = (
    "Historical Market Data Service error message:"
    "Trading TWS session is connected from a different IP address"
)


def _history_error(code: int, message: str) -> Callable[[Any], FakeTWSClient]:
    def factory(callbacks: Any) -> FakeTWSClient:
        client = FakeTWSClient(callbacks)

        def history(*args: object) -> None:
            callbacks.error(int(args[0]), code, message)  # type: ignore[arg-type]

        client.reqHistoricalData = history  # type: ignore[method-assign]
        return client

    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (162, ELSEWHERE_162),
        (165, "HMDS query message:Trading TWS session is connected from a different IP address"),
        (10197, "No market data during competing live session"),
    ],
)
async def test_a_login_elsewhere_on_history_is_a_competing_login_never_pacing(code, message) -> None:
    """R5 (2026-10-13 13:05-14:15 ET): 162 "different IP address" was counted as pacing."""
    from trading.market.ibkr_boundary import (
        FAULT_COMPETING_LOGIN,
        MarketDataQualityError,
        liquidity_profile,
        validate_completed_trade_bars,
    )

    heard: list[str] = []
    actor = TWSPaperActor(config(), client_factory=_history_error(code, message))
    actor.set_competing_session_listener(heard.append)
    await actor.connect()

    daily = await actor.daily_trade_bars(101)
    intraday = await actor.completed_trade_bars(101)

    for batch in (daily, intraday):
        assert batch.bars == () and batch.pacing_failed is False and batch.competing_session is True
        assert is_competing_session_report(batch.error)
    assert heard == [f"TWS error {code}: {message}"] * 2
    with pytest.raises(MarketDataQualityError) as bars:
        validate_completed_trade_bars(intraday, now=datetime.now(timezone.utc))
    with pytest.raises(MarketDataQualityError) as history:
        liquidity_profile(daily, session_date=date(2026, 10, 13), volume_multiplier=Decimal("100"))
    for refused in (bars.value, history.value):
        assert refused.fault == FAULT_COMPETING_LOGIN
        assert str(refused).startswith("IBKR login active elsewhere (phone/web)")


@pytest.mark.asyncio
async def test_a_real_pacing_violation_stays_pacing_and_reaches_no_listener() -> None:
    from trading.market.ibkr_boundary import FAULT_PACING, MarketDataQualityError, validate_completed_trade_bars

    heard: list[str] = []
    violation = "Historical Market Data Service error message:Historical data request pacing violation"
    actor = TWSPaperActor(config(), client_factory=_history_error(162, violation))
    actor.set_competing_session_listener(heard.append)
    await actor.connect()

    batch = await actor.completed_trade_bars(101)

    assert (batch.pacing_failed, batch.competing_session, heard) == (True, False, [])
    with pytest.raises(MarketDataQualityError) as refused:
        validate_completed_trade_bars(batch, now=datetime.now(timezone.utc))
    assert refused.value.fault == FAULT_PACING


@pytest.mark.asyncio
async def test_a_global_competing_notice_reaches_the_listener_and_a_failing_one_is_ignored() -> None:
    heard: list[str] = []

    def broken(report: str) -> None:
        heard.append(report)
        raise RuntimeError("listener bug")

    actor = TWSPaperActor(config(), client_factory=lambda callbacks: FakeTWSClient(callbacks))
    actor.set_competing_session_listener(broken)
    await actor.connect()

    actor._on_error(-1, 10197, "No market data during competing live session")

    assert heard == ["TWS error 10197: No market data during competing live session"]
    assert actor.last_error == heard[0]


@pytest.mark.asyncio
async def test_contract_details_for_symbol_uses_hint_and_requires_one_match() -> None:
    built: list[tuple[str, str | None]] = []

    def symbol_factory(symbol: str, primary_exchange: str | None) -> Any:
        built.append((symbol, primary_exchange))
        return SimpleNamespace(symbol=symbol, primaryExchange=primary_exchange or "")

    def details(callbacks: Any, request_id: int, contract: Any) -> None:
        match = SimpleNamespace(
            contract=SimpleNamespace(
                conId=72063691,
                symbol=contract.symbol,
                currency="USD",
                secType="STK",
                primaryExchange="NYSE",
            ),
            minTick=0.01,
            stockType="COMMON",
        )
        callbacks.contractDetails(request_id, match)
        if contract.symbol == "DUAL":
            callbacks.contractDetails(request_id, match)
        callbacks.contractDetailsEnd(request_id)

    actor, client = await connected(symbol_contract_factory=symbol_factory)
    client.details_script = details

    resolved = await actor.contract_details_for_symbol("BRK B", "NYSE")
    assert (resolved.con_id, resolved.symbol, resolved.primary_exchange) == (
        72063691,
        "BRK B",
        "NYSE",
    )
    with pytest.raises(TWSRequestError, match="missing or ambiguous"):
        await actor.contract_details_for_symbol("DUAL", None)
    with pytest.raises(TWSRequestError, match="symbol"):
        await actor.contract_details_for_symbol("  ", None)
    assert built == [("BRK B", "NYSE"), ("DUAL", None)]


@pytest.mark.asyncio
async def test_symbol_lookup_reports_not_found_as_request_error() -> None:
    def not_found(callbacks: Any, request_id: int, contract: Any) -> None:
        callbacks.error(request_id, 200, "No security definition has been found for the request")

    actor, client = await connected(symbol_contract_factory=lambda symbol, hint: SimpleNamespace())
    client.details_script = not_found

    with pytest.raises(TWSRequestError, match="200") as raised:
        await actor.contract_details_for_symbol("NOPE", "NYSE")
    assert not isinstance(raised.value, TWSCompetingSessionError)


def test_official_symbol_contract_is_a_smart_routed_us_stock() -> None:
    hinted = _official_symbol_contract_factory("BRK B", "NYSE")
    assert (hinted.symbol, hinted.secType, hinted.exchange, hinted.currency, hinted.primaryExchange) == (
        "BRK B",
        "STK",
        "SMART",
        "USD",
        "NYSE",
    )
    assert _official_symbol_contract_factory("CBOE", None).primaryExchange == ""


def test_market_snapshot_invariants() -> None:
    now = datetime.now(timezone.utc)
    mid = MarketSnapshot(
        con_id=1, observed_at=now, prior_close=Decimal("10"), bid=Decimal("9.9"), ask=Decimal("10.1")
    )
    assert mid.reference_price == Decimal("10.0")
    with pytest.raises(MarketSnapshotError, match="timezone"):
        MarketSnapshot(con_id=1, observed_at=now.replace(tzinfo=None), prior_close=Decimal("1"), last=Decimal("1"))
    with pytest.raises(MarketSnapshotError, match="last trade or both"):
        MarketSnapshot(con_id=1, observed_at=now, prior_close=Decimal("10"), bid=Decimal("9.9"))
    with pytest.raises(MarketSnapshotError, match="positive"):
        MarketSnapshot(con_id=1, observed_at=now, prior_close=Decimal("10"), last=Decimal("0"))
    with pytest.raises(MarketSnapshotError, match="volume_raw"):
        MarketSnapshot(
            con_id=1, observed_at=now, prior_close=Decimal("10"), last=Decimal("1"), volume_raw=Decimal("-1")
        )
    with pytest.raises(MarketSnapshotError, match="con_id"):
        MarketSnapshot(con_id=0, observed_at=now, prior_close=Decimal("10"), last=Decimal("1"))
    with pytest.raises(MarketSnapshotError, match="prior_close"):
        MarketSnapshot(
            con_id=1,
            observed_at=now - timedelta(seconds=1),
            prior_close=None,  # type: ignore[arg-type]
            last=Decimal("1"),
        )


# --- batched snapshots ---------------------------------------------------------


class DelayedClient(SnapshotClient):
    """Delivers each stream's ticks later, so several lines overlap."""

    def __init__(self, callbacks: Any, script: Script = live_burst, delay: float = 0.05) -> None:
        super().__init__(callbacks, script)
        self.delay = delay
        self.active: set[int] = set()
        self.max_active = 0
        self.started_at: list[float] = []

    def reqMktData(self, *args: object) -> None:
        request_id = int(args[0])  # type: ignore[arg-type]
        self.quote_calls.append(args)
        self.active.add(request_id)
        self.max_active = max(self.max_active, len(self.active))
        loop = asyncio.get_running_loop()
        self.started_at.append(loop.time())
        loop.call_later(self.delay, self.script, self.callbacks, request_id)

    def cancelMktData(self, request_id: int) -> None:
        self.cancelled_quotes.append(request_id)
        self.active.discard(request_id)


async def delayed(script: Script = live_burst, delay: float = 0.05) -> tuple[TWSPaperActor, DelayedClient]:
    holder: list[DelayedClient] = []

    def factory(callbacks: Any) -> DelayedClient:
        client = DelayedClient(callbacks, script, delay)
        holder.append(client)
        return client

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()
    return actor, holder[0]


def contract_script(failing: set[int]) -> Callable[[DelayedClient], Script]:
    def bind(client: DelayedClient) -> Script:
        def script(callbacks: Any, request_id: int) -> None:
            contract = next(call[1] for call in client.quote_calls if call[0] == request_id)
            if contract.conId in failing:  # type: ignore[attr-defined]
                callbacks.error(request_id, 354, "Requested market data is not subscribed")
            else:
                live_burst(callbacks, request_id)

        return script

    return bind


@pytest.mark.asyncio
async def test_batch_keeps_input_order_bounds_lines_and_releases_every_line() -> None:
    actor, client = await delayed(delay=0.2)
    client.script = contract_script({3, 6})(client)

    results = await actor.market_snapshots(list(range(1, 9)), max_in_flight=3)

    assert [type(result).__name__ for result in results] == [
        "MarketSnapshot",
        "MarketSnapshot",
        "TWSRequestError",
        "MarketSnapshot",
        "MarketSnapshot",
        "TWSRequestError",
        "MarketSnapshot",
        "MarketSnapshot",
    ]
    assert [result.con_id for result in results if isinstance(result, MarketSnapshot)] == [1, 2, 4, 5, 7, 8]
    assert client.max_active == 3
    assert sorted(client.cancelled_quotes) == sorted(int(call[0]) for call in client.quote_calls)  # type: ignore[arg-type]
    gaps = [later - earlier for earlier, later in zip(client.started_at, client.started_at[1:])]
    assert min(gaps) >= 0.045


@pytest.mark.asyncio
async def test_batch_aborts_on_a_competing_login_and_releases_open_lines() -> None:
    def blocked(callbacks: Any, request_id: int) -> None:
        callbacks.error(request_id, 10197, "No market data during competing live session")

    actor, client = await delayed(blocked)

    with pytest.raises(TWSCompetingSessionError):
        await actor.market_snapshots(list(range(1, 21)), max_in_flight=5)
    requested = sorted(int(call[0]) for call in client.quote_calls)  # type: ignore[arg-type]
    assert requested and sorted(client.cancelled_quotes) == requested
    assert client.active == set()
    assert len(requested) < 20


@pytest.mark.asyncio
async def test_batch_holds_the_request_gate() -> None:
    actor, client = await delayed(delay=0.1)
    batch = asyncio.create_task(actor.market_snapshots([1, 2], max_in_flight=2))
    await asyncio.sleep(0.02)
    details = asyncio.create_task(actor.contract_details(101))
    await asyncio.sleep(0.02)
    assert not details.done()
    await batch
    assert (await details).con_id == 101


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("con_ids", "kwargs", "message"),
    [
        ([1, 0], {}, "con_id"),
        ([1], {"max_in_flight": 0}, "max_in_flight"),
        ([1], {"max_in_flight": 101}, "max_in_flight"),
        ([1], {"timeout_seconds": -1}, "timeout"),
    ],
)
async def test_batch_rejects_bad_arguments(con_ids: list[int], kwargs: dict[str, Any], message: str) -> None:
    actor, client = await delayed()
    with pytest.raises(TWSRequestError, match=message):
        await actor.market_snapshots(con_ids, **kwargs)
    assert client.quote_calls == []


# --- screener scans -------------------------------------------------------------


@pytest.mark.asyncio
async def test_screener_scan_is_allowlisted_and_leaves_the_orb_scan_frozen() -> None:
    from trading.market.ibkr_boundary import ScannerRequest

    built: list[tuple[str, int]] = []

    def scan_factory(scan_code: str, rows: int) -> Any:
        built.append((scan_code, rows))
        return SimpleNamespace(scanCode=scan_code)

    actor, client = await connected(screener_scan_factory=scan_factory)

    results = await actor.screener_scan("TOP_PERC_GAIN")
    assert [(row.rank, row.symbol) for row in results] == [(1, "AAPL"), (2, "")]
    assert built == [("TOP_PERC_GAIN", 50)]
    assert client.cancelled_scanners == [client.scanner_calls[0][0]]
    with pytest.raises(TWSRequestError, match="allowlisted"):
        await actor.screener_scan("TOP_PERC_LOSE")
    assert len(client.scanner_calls) == 1
    with pytest.raises(TWSRequestError, match="frozen"):
        await actor.scan(ScannerRequest(scan_code="TOP_PERC_GAIN"))  # type: ignore[arg-type]


def test_official_screener_scan_requests_common_stock_in_the_price_band() -> None:
    from trading.market.tws_actor import _official_screener_scan_factory

    subscription = _official_screener_scan_factory("MOST_ACTIVE", 50)
    assert (
        subscription.instrument,
        subscription.locationCode,
        subscription.scanCode,
        subscription.stockTypeFilter,
        subscription.numberOfRows,
        subscription.abovePrice,
        subscription.belowPrice,
    ) == ("STK", "STK.US.MAJOR", "MOST_ACTIVE", "CORP", 50, 10.0, 300.0)
