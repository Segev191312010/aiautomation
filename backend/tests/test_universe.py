"""Watchlist universe: parsing, IBKR resolution, persistence, daily qualification."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from trading.market.ibkr_boundary import DailyBarBatch, IBKRContractDetails, IBKRDailyBar
from trading.market.tws_actor import (
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSRequestError,
)
from trading.market.universe import (
    ERROR_REASON_PREFIX,
    EXCHANGE_HINTS,
    DailyQualification,
    RequestPacer,
    WatchlistEntry,
    WatchlistFormatError,
    entry_rejection,
    evaluate_daily_history,
    import_watchlist,
    load_daily,
    load_universe,
    parse_watchlist,
    qualify_universe,
    resolve_entry,
    to_ibkr_symbol,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
SESSION = date(2026, 10, 12)
NOT_FOUND = TWSRequestError("TWS error 200: No security definition has been found for the request")


@pytest.fixture
async def database(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    path = tmp_path / "universe.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


def details(symbol: str, con_id: int, exchange: str, **overrides: object) -> IBKRContractDetails:
    values: dict[str, object] = {
        "con_id": con_id,
        "symbol": symbol,
        "currency": "USD",
        "security_type": "STK",
        "stock_type": "COMMON",
        "primary_exchange": exchange,
        "min_tick": Decimal("0.01"),
    }
    values.update(overrides)
    return IBKRContractDetails(**values)  # type: ignore[arg-type]


class FakeResolver:
    def __init__(self, answers: dict[tuple[str, str | None], IBKRContractDetails | Exception]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str | None]] = []

    async def contract_details_for_symbol(
        self, symbol: str, primary_exchange: str | None
    ) -> IBKRContractDetails:
        self.calls.append((symbol, primary_exchange))
        answer = self.answers.get((symbol, primary_exchange), NOT_FOUND)
        if isinstance(answer, Exception):
            raise answer
        return answer


class CountingPacer(RequestPacer):
    def __init__(self) -> None:
        super().__init__(0)
        self.waits = 0
        self.deferred: list[float] = []

    async def wait(self) -> None:
        self.waits += 1

    def defer(self, seconds: float) -> None:
        self.deferred.append(seconds)


# --- parsing and mapping -------------------------------------------------------


def test_parse_accepts_commas_newlines_blanks_and_skips_duplicates() -> None:
    parsed = parse_watchlist(
        "NASDAQ:AAPL,NYSE:BRK.B\n\n nyse:bf.b , ,NASDAQ:AAPL\r\nCBOE:CBOE,\nNYSEARCA:SPY"
    )

    assert [entry.label for entry in parsed.entries] == [
        "NASDAQ:AAPL",
        "NYSE:BRK.B",
        "NYSE:BF.B",
        "CBOE:CBOE",
        "NYSEARCA:SPY",
    ]
    assert parsed.duplicates == ("NASDAQ:AAPL",)


def test_tradingview_symbols_and_exchanges_map_to_ibkr() -> None:
    assert to_ibkr_symbol("BRK.B") == "BRK B"
    assert to_ibkr_symbol("bf.b") == "BF B"
    assert {key: EXCHANGE_HINTS[key] for key in ("NASDAQ", "NYSE", "AMEX", "NYSEARCA", "CBOE")} == {
        "NASDAQ": "NASDAQ",
        "NYSE": "NYSE",
        "AMEX": "AMEX",
        "NYSEARCA": "ARCA",
        "CBOE": "BATS",
    }
    entry = WatchlistEntry(listing_exchange="NYSE", symbol="BRK.B")
    assert (entry.ibkr_symbol, entry.primary_exchange_hint) == ("BRK B", "NYSE")
    assert WatchlistEntry(listing_exchange="", symbol="AAPL").primary_exchange_hint is None


def test_unsupported_entries_are_rejected_before_any_lookup() -> None:
    assert "exchange" in str(entry_rejection(WatchlistEntry("OTC", "ABCD")))
    assert "exchange" in str(entry_rejection(WatchlistEntry("TSX", "SHOP")))
    assert "symbol" in str(entry_rejection(WatchlistEntry("NYSE", "BRK B")))
    assert entry_rejection(WatchlistEntry("", "AAPL")) is None
    assert entry_rejection(WatchlistEntry("NYSE", "BF.B")) is None


@pytest.mark.parametrize("text", ["", " , \n ,", "NYSE:"])
def test_unreadable_exports_fail(text: str) -> None:
    with pytest.raises(WatchlistFormatError):
        parse_watchlist(text)


# --- resolution -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hinted_lookup_resolves_in_one_request() -> None:
    resolver = FakeResolver({("BRK B", "NYSE"): details("BRK B", 72063691, "NYSE")})
    pacer = CountingPacer()

    resolution = await resolve_entry(resolver, WatchlistEntry("NYSE", "BRK.B"), now=NOW, pacer=pacer)

    row = resolution.row
    assert (row.status, row.con_id, row.primary_exchange, row.ibkr_symbol) == (
        "resolved",
        72063691,
        "NYSE",
        "BRK B",
    )
    assert (row.symbol, row.listing_exchange, row.source) == ("BRK.B", "NYSE", "watchlist")
    assert resolution.used_fallback is False
    assert resolver.calls == [("BRK B", "NYSE")]
    assert pacer.waits == 1


@pytest.mark.asyncio
async def test_failed_hint_retries_once_without_it() -> None:
    resolver = FakeResolver({("CBOE", None): details("CBOE", 76227954, "BATS")})

    resolution = await resolve_entry(resolver, WatchlistEntry("CBOE", "CBOE"), now=NOW)

    assert resolver.calls == [("CBOE", "BATS"), ("CBOE", None)]
    assert resolution.row.status == "resolved" and resolution.used_fallback is True


@pytest.mark.asyncio
async def test_both_attempts_failing_records_unresolved_with_both_errors() -> None:
    resolver = FakeResolver({("ZZZ", None): details("OTHER", 5, "NYSE")})

    resolution = await resolve_entry(resolver, WatchlistEntry("NYSE", "ZZZ"), now=NOW)

    row = resolution.row
    assert row.status == "unresolved" and row.con_id is None and row.primary_exchange is None
    assert "NYSE: TWS error 200" in str(row.last_error)
    assert "no hint: IBKR returned symbol 'OTHER'" in str(row.last_error)
    assert len(resolver.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"currency": "CAD"},
        {"security_type": "ETF"},
        {"con_id": 0},
    ],
)
async def test_non_us_stock_answers_are_not_accepted(bad: dict[str, object]) -> None:
    resolver = FakeResolver({("AAPL", "NASDAQ"): replace(details("AAPL", 265598, "NASDAQ"), **bad)})

    resolution = await resolve_entry(resolver, WatchlistEntry("NASDAQ", "AAPL"), now=NOW)

    assert resolution.row.status == "unresolved"


@pytest.mark.asyncio
async def test_unsupported_exchange_never_queries_ibkr() -> None:
    resolver = FakeResolver({})

    resolution = await resolve_entry(resolver, WatchlistEntry("OTC", "ABCD"), now=NOW)

    assert resolution.row.status == "unresolved" and resolver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [TWSConnectionError("lost"), TWSCompetingSessionError("TWS error 10197: competing")],
)
async def test_connection_and_competing_session_abort_resolution(failure: Exception) -> None:
    resolver = FakeResolver({("AAPL", "NASDAQ"): failure})

    with pytest.raises(type(failure)):
        await resolve_entry(resolver, WatchlistEntry("NASDAQ", "AAPL"), now=NOW)


# --- import persistence ---------------------------------------------------------

ANSWERS: dict[tuple[str, str | None], IBKRContractDetails | Exception] = {
    ("AAPL", "NASDAQ"): details("AAPL", 265598, "NASDAQ"),
    ("BRK B", "NYSE"): details("BRK B", 72063691, "NYSE"),
    ("CBOE", None): details("CBOE", 76227954, "BATS"),
}


def universe_rows(path) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT source, symbol, listing_exchange, ibkr_symbol, con_id, primary_exchange,"
            " status, last_error FROM autotrader_universe ORDER BY symbol"
        ).fetchall()


@pytest.mark.asyncio
async def test_import_persists_idempotently_and_prunes_removed_symbols(database) -> None:
    export = "NASDAQ:AAPL,NYSE:BRK.B,CBOE:CBOE,NYSE:NOPE,NASDAQ:AAPL"

    report = await import_watchlist(FakeResolver(ANSWERS), export, clock=lambda: NOW)

    assert (report.parsed, report.resolved) == (4, 3)
    assert report.duplicates == ("NASDAQ:AAPL",)
    assert report.used_fallback == ("CBOE",)
    assert [row.symbol for row in report.unresolved] == ["NOPE"]
    first = universe_rows(database)
    assert first[:2] == [
        ("watchlist", "AAPL", "NASDAQ", "AAPL", 265598, "NASDAQ", "resolved", None),
        ("watchlist", "BRK.B", "NYSE", "BRK B", 72063691, "NYSE", "resolved", None),
    ]
    assert first[3][:7] == ("watchlist", "NOPE", "NYSE", "NOPE", None, None, "unresolved")

    again = await import_watchlist(FakeResolver(ANSWERS), export, clock=lambda: NOW)
    assert again.removed == ()
    assert universe_rows(database) == first

    trimmed = await import_watchlist(FakeResolver(ANSWERS), "NASDAQ:AAPL\nNYSE:BRK.B", clock=lambda: NOW)
    assert trimmed.removed == ("CBOE", "NOPE")
    assert [row.symbol for row in await load_universe("watchlist")] == ["AAPL", "BRK.B"]


@pytest.mark.asyncio
async def test_failed_import_keeps_the_previous_universe(database) -> None:
    await import_watchlist(FakeResolver(ANSWERS), "NASDAQ:AAPL", clock=lambda: NOW)
    lost = dict(ANSWERS)
    lost[("BRK B", "NYSE")] = TWSConnectionError("lost")

    with pytest.raises(TWSConnectionError):
        await import_watchlist(FakeResolver(lost), "NYSE:BRK.B", clock=lambda: NOW)
    assert [row.symbol for row in await load_universe()] == ["AAPL"]


# --- daily qualification ----------------------------------------------------------


def history(
    con_id: int,
    *,
    sessions: int = 21,
    close: str = "100",
    volume: str = "500000",
    include_today: bool = True,
) -> DailyBarBatch:
    days = [SESSION - timedelta(days=offset) for offset in range(sessions, 0, -1)]
    bars = [
        IBKRDailyBar(con_id=con_id, session_date=day, close=Decimal(close), volume=Decimal(volume))
        for day in days
    ]
    if include_today:
        # The forming bar for the session itself must never count.
        bars.append(
            IBKRDailyBar(con_id=con_id, session_date=SESSION, close=Decimal("999"), volume=Decimal("1"))
        )
    return DailyBarBatch(bars=tuple(bars))


def test_qualification_uses_prior_sessions_and_round_lot_multiplier() -> None:
    batch = history(7, close="100", volume="500000")
    first = batch.bars[0]
    # Make the window boundary observable: the oldest of 21 prior bars is excluded.
    bars = (IBKRDailyBar(con_id=7, session_date=first.session_date, close=Decimal("1"), volume=Decimal("1")),) + batch.bars[1:]

    result = evaluate_daily_history(
        DailyBarBatch(bars=bars),
        con_id=7,
        symbol="AAPL",
        session_date=SESSION,
        volume_multiplier=Decimal("100"),
        observed_at=NOW,
    )

    assert result.qualified is True and result.reason is None
    assert result.prior_close == Decimal("100")
    # 500,000 round lots x 100 = 50,000,000 shares a day.
    assert result.avg_daily_volume_shares == Decimal("50000000.00")
    assert result.avg_dollar_volume == Decimal("5000000000.00")
    assert result.daily_atr is None


@pytest.mark.parametrize(
    ("close", "volume", "reason"),
    [
        ("9.99", "10000000", "price_out_of_range"),
        ("300.01", "10000000", "price_out_of_range"),
        ("10", "19999", "dollar_volume_too_low"),
        # $10 x 20,000 round lots x 100 = exactly $20M: the floor is inclusive.
        ("10", "20000", None),
        ("300", "10000", None),
    ],
)
def test_qualification_thresholds(close: str, volume: str, reason: str | None) -> None:
    result = evaluate_daily_history(
        history(7, close=close, volume=volume),
        con_id=7,
        symbol="X",
        session_date=SESSION,
        volume_multiplier=Decimal("100"),
        observed_at=NOW,
    )
    assert result.reason == reason
    assert result.qualified is (reason is None)
    assert result.transient is False


def test_history_problems_are_final_and_request_failures_are_transient() -> None:
    short = evaluate_daily_history(
        history(7, sessions=19),
        con_id=7,
        symbol="X",
        session_date=SESSION,
        volume_multiplier=Decimal("100"),
        observed_at=NOW,
    )
    assert short.reason == "insufficient_daily_history" and short.transient is False

    for batch in (
        DailyBarBatch(bars=(), pacing_failed=True, error="TWS error 162: pacing violation"),
        DailyBarBatch(bars=(), error="TWS error 366: no data"),
        DailyBarBatch(bars=(), connected=False),
        history(8),
    ):
        failed = evaluate_daily_history(
            batch,
            con_id=7,
            symbol="X",
            session_date=SESSION,
            volume_multiplier=Decimal("100"),
            observed_at=NOW,
        )
        assert failed.qualified is False and failed.transient is True
        assert str(failed.reason).startswith(ERROR_REASON_PREFIX)


class FakeHistory:
    def __init__(self, batches: dict[int, DailyBarBatch | Exception]) -> None:
        self.batches = batches
        self.calls: list[int] = []

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        self.calls.append(con_id)
        batch = self.batches[con_id]
        if isinstance(batch, Exception):
            raise batch
        return batch


async def seed_universe() -> None:
    await import_watchlist(
        FakeResolver(ANSWERS), "NASDAQ:AAPL,NYSE:BRK.B,CBOE:CBOE,NYSE:NOPE", clock=lambda: NOW
    )


@pytest.mark.asyncio
async def test_qualify_universe_is_paced_resumable_and_retries_transient_rows(database) -> None:
    await seed_universe()
    source = FakeHistory(
        {
            265598: history(265598),
            72063691: history(72063691, close="5"),
            76227954: DailyBarBatch(bars=(), pacing_failed=True, error="TWS error 162: pacing"),
        }
    )
    pacer = CountingPacer()

    report = await qualify_universe(
        source, session_date=SESSION, volume_multiplier=Decimal("100"), clock=lambda: NOW, pacer=pacer
    )

    # Unresolved symbols are never requested.
    assert sorted(source.calls) == [265598, 72063691, 76227954]
    assert pacer.waits == 3 and pacer.deferred == [15.0]
    assert (report.instruments, report.requested, report.skipped, report.qualified) == (3, 3, 0, 1)
    assert report.rejected == (("price_out_of_range", 1),)
    assert report.errors == (("CBOE", "error: TWS error 162: pacing"),)
    with sqlite3.connect(database) as db:
        stored = db.execute(
            "SELECT symbol, prior_close, avg_daily_volume_shares, avg_dollar_volume, qualified, reason"
            " FROM autotrader_universe_daily WHERE session_date = ? ORDER BY symbol",
            (SESSION.isoformat(),),
        ).fetchall()
    assert stored[0] == ("AAPL", "100", "50000000.00", "5000000000.00", 1, None)

    source.batches[76227954] = history(76227954)
    source.calls.clear()
    resumed = await qualify_universe(
        source, session_date=SESSION, volume_multiplier=Decimal("100"), clock=lambda: NOW, pacer=pacer
    )
    assert source.calls == [76227954]
    assert (resumed.requested, resumed.skipped, resumed.qualified, resumed.errors) == (1, 2, 2, ())


@pytest.mark.asyncio
async def test_competing_session_aborts_qualification_but_keeps_progress(database) -> None:
    await seed_universe()
    blocked = DailyBarBatch(
        bars=(),
        pacing_failed=True,
        error="TWS error 162: HMDS error message:Trading TWS session is connected from a different IP address",
    )
    source = FakeHistory({265598: history(265598), 72063691: blocked, 76227954: blocked})

    with pytest.raises(TWSCompetingSessionError):
        await qualify_universe(
            source,
            session_date=SESSION,
            volume_multiplier=Decimal("100"),
            clock=lambda: NOW,
            pacer=CountingPacer(),
        )
    stored = await load_daily(SESSION)
    assert [(row.symbol, row.qualified) for row in stored] == [("AAPL", True)]


@pytest.mark.asyncio
async def test_request_errors_are_recorded_and_connection_loss_aborts(database) -> None:
    await seed_universe()
    source = FakeHistory(
        {
            265598: TWSRequestError("TWS daily-history request timed out"),
            72063691: history(72063691),
            76227954: TWSConnectionError("TWS connection closed"),
        }
    )

    with pytest.raises(TWSConnectionError):
        await qualify_universe(
            source,
            session_date=SESSION,
            volume_multiplier=Decimal("100"),
            clock=lambda: NOW,
            pacer=CountingPacer(),
        )
    stored = {row.symbol: row for row in await load_daily(SESSION)}
    assert stored["AAPL"].transient and "timed out" in str(stored["AAPL"].reason)
    assert stored["BRK.B"].qualified is True


def test_daily_qualification_invariants() -> None:
    with pytest.raises(ValueError):
        DailyQualification(
            session_date=SESSION,
            con_id=1,
            symbol="X",
            prior_close=None,
            avg_daily_volume_shares=None,
            avg_dollar_volume=None,
            daily_atr=None,
            qualified=True,
            reason=None,
            observed_at=NOW,
        )
    with pytest.raises(ValueError):
        DailyQualification(
            session_date=SESSION,
            con_id=1,
            symbol="X",
            prior_close=None,
            avg_daily_volume_shares=None,
            avg_dollar_volume=None,
            daily_atr=None,
            qualified=False,
            reason=None,
            observed_at=NOW,
        )


@pytest.mark.asyncio
async def test_request_pacer_spaces_starts_and_defers() -> None:
    now = [100.0]
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pacer = RequestPacer(1.0, clock=lambda: now[0], sleep=sleep)
    await pacer.wait()
    now[0] += 0.25
    await pacer.wait()
    now[0] += 2.0
    await pacer.wait()
    pacer.defer(15.0)
    await pacer.wait()

    assert sleeps == [pytest.approx(0.75), pytest.approx(16.0)]
