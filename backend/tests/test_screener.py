"""Live screener: metrics, deterministic ranking, payload contract, sweep, service."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import random
import sqlite3
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.test_tws_actor import FakeTWSClient
from trading.contracts import ShadowRuntimeConfig
from trading.market import screener as screener_module
from trading.market.context import CONTEXT_SYMBOLS, build_market_context, latest_market_context
from trading.market.screener import (
    HOT_LIST_SIZE,
    TICK_VOLUME_MULTIPLIER,
    ContextETFs,
    Observation,
    ScanMerge,
    ScreenerError,
    ScreenerInstrument,
    ScreenerPayload,
    build_payload,
    compute_metrics,
    elapsed_fraction,
    latest_hotlist,
    latest_snapshot,
    percentile_ranks,
    persist_snapshot,
    price_text,
    rank_observations,
    run_sweep,
    scan_additions,
    scan_candidates,
    with_market_context,
)
from trading.market.snapshots import MarketSnapshot
from trading.market.tws_actor import (
    TWSActorConfig,
    TWSCompetingSessionError,
    TWSConnectionError,
    TWSPaperActor,
    TWSRequestError,
)
from trading.market.ibkr_boundary import (
    DailyBarBatch,
    IBKRContractDetails,
    IBKRDailyBar,
    IBKRScannerResult,
)
from trading.market.universe import (
    DailyQualification,
    RequestPacer,
    UniverseRow,
    load_daily,
    replace_universe_source,
    upsert_daily,
)
from trading.operations import screener_service as service
from trading.scheduler.sessions import market_session

ET = ZoneInfo("America/New_York")
DAY = date(2026, 10, 12)
SESSION = market_session(DAY)
PAYLOAD_KEYS = [
    "schema",
    "session_date",
    "observed_at",
    "sweep_seconds",
    "universe_size",
    "qualified",
    "quoted",
    "errors",
    "rows",
]
ROW_KEYS = [
    "symbol",
    "con_id",
    "primary_exchange",
    "source",
    "last",
    "bid",
    "ask",
    "prior_close",
    "open",
    "day_high",
    "day_low",
    "change_pct",
    "gap_pct",
    "volume_shares",
    "rvol",
    "dollar_volume",
    "pct_from_high",
    "score",
    "hot",
    "hot_rank",
]


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def snap(
    con_id: int = 1,
    *,
    last: str | None = "102",
    prior: str = "100",
    open_: str | None = "101",
    high: str | None = "103",
    low: str | None = "99",
    volume: str | None = "2000000",
    bid: str | None = None,
    ask: str | None = None,
    observed: datetime | None = None,
) -> MarketSnapshot:
    def dec(value: str | None) -> Decimal | None:
        return None if value is None else Decimal(value)

    return MarketSnapshot(
        con_id=con_id,
        observed_at=observed or at(12, 45),
        prior_close=Decimal(prior),
        last=dec(last),
        bid=dec(bid),
        ask=dec(ask),
        open=dec(open_),
        day_high=dec(high),
        day_low=dec(low),
        volume_raw=dec(volume),
    )


def instrument(symbol: str, con_id: int, avg: str | None = "8000000") -> ScreenerInstrument:
    return ScreenerInstrument(
        symbol=symbol,
        con_id=con_id,
        primary_exchange="NASDAQ",
        source="watchlist",
        avg_daily_volume_shares=None if avg is None else Decimal(avg),
    )


def observe(symbol: str, con_id: int, snapshot: MarketSnapshot, avg: str | None = "8000000") -> Observation:
    return Observation(
        instrument=instrument(symbol, con_id, avg),
        snapshot=snapshot,
        metrics=compute_metrics(
            snapshot,
            avg_daily_volume_shares=None if avg is None else Decimal(avg),
            session=SESSION,
        ),
    )


# --- metrics -------------------------------------------------------------------


def test_elapsed_fraction_floors_pre_open_and_caps_after_close() -> None:
    assert elapsed_fraction(at(8, 0), SESSION) == Decimal("0.05")
    assert elapsed_fraction(at(9, 31), SESSION) == Decimal("0.05")
    assert elapsed_fraction(at(12, 45), SESSION) == Decimal("0.5")
    assert elapsed_fraction(at(16, 30), SESSION) == Decimal("1")
    assert elapsed_fraction(at(12, 0), None) == Decimal("0.05")
    half_day = market_session(date(2026, 11, 27))
    assert half_day is not None and half_day.half_day
    after_close = datetime(2026, 11, 27, 15, 0, tzinfo=ET)
    assert elapsed_fraction(after_close, half_day) == Decimal("210") / Decimal("390")


def test_metrics_follow_the_contract_definitions() -> None:
    metrics = compute_metrics(
        snap(), avg_daily_volume_shares=Decimal("8000000"), session=SESSION
    )

    assert metrics.change_pct == Decimal("2")
    assert metrics.gap_pct == Decimal("1")
    # Streaming tick 8 is already in shares (live 2026-10-12): no x100.
    assert TICK_VOLUME_MULTIPLIER == Decimal("1")
    assert metrics.volume_shares == Decimal("2000000")
    # 2,000,000 / (8,000,000 x 195/390)
    assert metrics.rvol == Decimal("0.5")
    assert metrics.dollar_volume == Decimal("204000000")
    assert metrics.pct_from_high == (Decimal("-1") / Decimal("103")) * 100


def test_pre_open_has_no_gap_and_uses_the_volume_floor() -> None:
    metrics = compute_metrics(
        snap(observed=at(8, 30), volume="40000"),
        avg_daily_volume_shares=Decimal("8000000"),
        session=SESSION,
    )
    assert metrics.gap_pct is None
    assert metrics.rvol == Decimal("40000") / (Decimal("8000000") * Decimal("0.05"))


def test_missing_inputs_leave_metrics_unknown_and_mid_price_stands_in() -> None:
    metrics = compute_metrics(
        snap(last=None, bid="99", ask="101", high=None, volume=None),
        avg_daily_volume_shares=None,
        session=SESSION,
    )
    assert metrics.price == Decimal("100") and metrics.change_pct == Decimal("0")
    assert (metrics.volume_shares, metrics.rvol, metrics.dollar_volume, metrics.pct_from_high) == (
        None,
        None,
        None,
        None,
    )


def test_percentile_ranks_use_mid_ranks_and_zero_for_unknown() -> None:
    assert percentile_ranks([Decimal(1), Decimal(2), Decimal(3)]) == (Decimal(0), Decimal("0.5"), Decimal(1))
    assert percentile_ranks([Decimal(1), Decimal(1), Decimal(3)]) == (Decimal("0.25"), Decimal("0.25"), Decimal(1))
    assert percentile_ranks([None, Decimal(5)]) == (Decimal(0), Decimal("0.5"))
    assert percentile_ranks([]) == ()


# --- ranking ----------------------------------------------------------------------


def test_score_weights_and_hot_list_require_a_gain() -> None:
    # change ranks C<B<A, rvol ranks A<C<B, closeness ranks C<A<B.
    observations = [
        observe("AAA", 1, snap(1, last="103", high="104.03", volume="400000")),
        observe("BBB", 2, snap(2, last="102", high="102", volume="1200000")),
        observe("CCC", 3, snap(3, last="99", high="101.02", volume="800000")),
    ]

    ranked = rank_observations(observations)

    assert [(item.observation.instrument.symbol, item.score, item.hot_rank) for item in ranked] == [
        ("BBB", Decimal("0.75"), 1),
        ("AAA", Decimal("0.60"), 2),
        ("CCC", Decimal("0.15"), None),
    ]


def test_ranking_is_deterministic_and_caps_the_hot_list() -> None:
    generator = random.Random(7)
    observations = [
        observe(
            f"S{index:02d}",
            100 + index,
            snap(
                100 + index,
                last=str(Decimal(100) + Decimal(generator.randint(-100, 900)) / 100),
                high="107",
                volume=str(generator.randint(1, 50) * 100000),
            ),
        )
        for index in range(60)
    ]
    # Identical metrics: order falls back to the symbol.
    observations.append(observe("ZZZ", 999, snap(999, last="105", high="107", volume="900000")))
    observations.append(observe("YYY", 998, snap(998, last="105", high="107", volume="900000")))

    baseline = rank_observations(observations)
    for seed in range(5):
        shuffled = list(observations)
        random.Random(seed).shuffle(shuffled)
        assert rank_observations(shuffled) == baseline

    hot = [item for item in baseline if item.hot_rank is not None]
    assert [item.hot_rank for item in hot] == list(range(1, HOT_LIST_SIZE + 1))
    assert all(item.observation.metrics.change_pct > 0 for item in hot)
    order = [item.observation.instrument.symbol for item in baseline]
    assert order.index("YYY") + 1 == order.index("ZZZ")
    scores = [item.score for item in baseline]
    assert scores == sorted(scores, reverse=True)


# --- payload contract --------------------------------------------------------------


def live_aapl_payload(observed: datetime = at(10, 48)) -> ScreenerPayload:
    aapl = MarketSnapshot(
        con_id=265598,
        observed_at=observed,
        prior_close=Decimal("336.640015"),
        last=Decimal("340.995"),
        bid=Decimal("340.94"),
        ask=Decimal("341.04"),
        open=Decimal("337.83"),
        day_high=Decimal("342.63"),
        day_low=Decimal("337.6"),
        volume_raw=Decimal("7729883"),
    )
    loser = snap(13824, last="95", prior="100", observed=observed, volume=None, high=None)
    ranked = rank_observations(
        [
            observe("AAPL", 265598, aapl, avg="60000000"),
            observe("WMT", 13824, loser, avg=None),
        ]
    )
    return build_payload(
        session_date=DAY,
        observed_at=observed,
        sweep_seconds=12.34,
        universe_size=503,
        qualified=3,
        errors=1,
        ranked=ranked,
    )


def test_payload_json_matches_the_screener_v1_contract_exactly() -> None:
    document = json.loads(live_aapl_payload().to_json())

    assert list(document) == PAYLOAD_KEYS
    assert document["schema"] == "screener.v1"
    assert document["session_date"] == "2026-10-12"
    assert document["observed_at"] == "2026-10-12T14:48:00.000000+00:00"
    assert datetime.fromisoformat(document["observed_at"]).tzinfo is not None
    assert (document["sweep_seconds"], document["universe_size"]) == (12.3, 503)
    assert (document["qualified"], document["quoted"], document["errors"]) == (3, 2, 1)
    assert all(list(row) == ROW_KEYS for row in document["rows"])
    aapl, wmt = document["rows"]
    assert aapl == {
        "symbol": "AAPL",
        "con_id": 265598,
        "primary_exchange": "NASDAQ",
        "source": "watchlist",
        "last": "340.995",
        "bid": "340.94",
        "ask": "341.04",
        "prior_close": "336.64",
        "open": "337.83",
        "day_high": "342.63",
        "day_low": "337.60",
        "change_pct": "1.29",
        "gap_pct": "0.35",
        "volume_shares": "7729883",
        "rvol": "0.64",
        "dollar_volume": "2635851453.59",
        "pct_from_high": "-0.48",
        "score": "0.7500",
        "hot": True,
        "hot_rank": 1,
    }
    assert wmt["change_pct"] == "-5.00" and wmt["hot"] is False and wmt["hot_rank"] is None
    assert (wmt["volume_shares"], wmt["rvol"], wmt["dollar_volume"], wmt["pct_from_high"]) == (
        None,
        None,
        None,
        None,
    )


def test_pre_open_rows_null_the_stale_open_tick() -> None:
    payload = live_aapl_payload(observed=at(8, 30))
    row = payload.rows[0]
    assert row.gap_pct is None and row.open is None


def test_tiny_negative_values_render_without_a_sign() -> None:
    gap = snap(open_="99.9999", observed=at(10, 0))
    payload = build_payload(
        session_date=DAY,
        observed_at=at(10, 0),
        sweep_seconds=1.0,
        universe_size=1,
        qualified=1,
        errors=0,
        ranked=rank_observations([observe("AAA", 1, replace(gap, prior_close=Decimal("100")))]),
    )
    assert payload.rows[0].gap_pct == "0.00"


def test_price_text_keeps_cents_and_sub_penny_prints() -> None:
    assert price_text(Decimal("120")) == "120.00"
    assert price_text(Decimal("336.640015")) == "336.64"
    assert price_text(Decimal("340.995")) == "340.995"
    assert price_text(None) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update(extra=1),
        lambda doc: doc.pop("errors"),
        lambda doc: doc.update(quoted=5),
        lambda doc: doc.update(schema="screener.v2"),
        lambda doc: doc["rows"].reverse(),
        lambda doc: doc["rows"][0].pop("rvol"),
        lambda doc: doc["rows"][0].update(hot_rank=None),
        lambda doc: doc["rows"][1].update(change_pct="1.2.3"),
        lambda doc: doc.update(observed_at="2026-10-12T14:48:00"),
        lambda doc: doc.update(market=5),
        lambda doc: doc.update(market=["risk_on"]),
    ],
)
def test_payload_validation_rejects_contract_violations(mutate: Any) -> None:
    document = json.loads(live_aapl_payload().to_json())
    mutate(document)
    with pytest.raises(ValidationError):
        ScreenerPayload.model_validate(document)


def test_market_context_is_appended_without_touching_the_rest_of_the_payload() -> None:
    base = live_aapl_payload()
    context = build_market_context(
        session_date=DAY,
        observed_at=at(10, 48),
        etf_snapshots={"SPY": snap(9001, last="100.4", observed=at(10, 48))},
        watchlist_rows=[row.model_dump() for row in base.rows],
    )

    combined = with_market_context(base, context)

    market_json = json.dumps(context.to_payload(), separators=(",", ":"))
    assert combined.to_json() == base.to_json()[:-1] + ',"market":' + market_json + "}"
    document = json.loads(combined.to_json())
    assert list(document) == PAYLOAD_KEYS + ["market"]
    assert document["market"]["breadth_up_pct"] == "50.00"
    assert ScreenerPayload.model_validate_json(combined.to_json()) == combined
    for other in (
        replace(context, observed_at=at(10, 49)),
        replace(context, session_date=DAY + timedelta(days=1)),
    ):
        with pytest.raises(ScreenerError, match="different sweep"):
            with_market_context(base, other)


# --- persistence and hot list --------------------------------------------------------


@pytest.fixture
async def database(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    path = tmp_path / "screener.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


@pytest.mark.asyncio
async def test_latest_hotlist_reads_the_newest_snapshot(database) -> None:
    older = live_aapl_payload(observed=at(10, 47))
    newer = live_aapl_payload(observed=at(10, 48))
    await persist_snapshot(newer)
    await persist_snapshot(older)

    hot = await latest_hotlist(DAY)

    assert [(row.hot_rank, row.symbol, row.con_id) for row in hot] == [(1, "AAPL", 265598)]
    row = hot[0]
    assert row.observed_at == at(10, 48)
    assert (row.change_pct, row.rvol, row.score, row.last) == (
        Decimal("1.29"),
        Decimal("0.64"),
        Decimal("0.7500"),
        Decimal("340.995"),
    )
    assert await latest_hotlist(DAY + timedelta(days=1)) == ()
    with sqlite3.connect(database) as db:
        stored = db.execute(
            "SELECT session_date, observed_at, payload_json FROM autotrader_screener_snapshots"
            " ORDER BY observed_at"
        ).fetchall()
    assert [item[1] for item in stored] == [older.observed_at, newer.observed_at]
    assert json.loads(stored[1][2])["schema"] == "screener.v1"


@pytest.mark.asyncio
async def test_snapshot_filed_under_the_wrong_session_is_refused(database) -> None:
    payload = live_aapl_payload()
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            ("x", "2026-10-13", payload.observed_at, payload.to_json()),
        )
    with pytest.raises(ScreenerError):
        await latest_snapshot(date(2026, 10, 13))


# --- sweep ------------------------------------------------------------------------------

NOW = datetime(2026, 10, 12, 14, 48, tzinfo=timezone.utc)


def universe_row(symbol: str, con_id: int | None, status: str = "resolved") -> UniverseRow:
    return UniverseRow(
        source="watchlist",
        symbol=symbol,
        listing_exchange="NASDAQ",
        ibkr_symbol=symbol,
        con_id=con_id,
        primary_exchange="NASDAQ" if con_id else None,
        status=status,  # type: ignore[arg-type]
        last_error=None if con_id else "not found",
        updated_at=NOW,
    )


def daily(symbol: str, con_id: int, *, qualified: bool = True) -> DailyQualification:
    return DailyQualification(
        session_date=DAY,
        con_id=con_id,
        symbol=symbol,
        prior_close=Decimal("100"),
        avg_daily_volume_shares=Decimal("8000000"),
        avg_dollar_volume=Decimal("800000000"),
        daily_atr=None,
        qualified=qualified,
        reason=None if qualified else "price_out_of_range",
        observed_at=NOW,
    )


async def seed_screener_universe() -> None:
    await replace_universe_source(
        "watchlist",
        [
            universe_row("AAA", 1),
            universe_row("BBB", 2),
            universe_row("CCC", 3),
            universe_row("DDD", 4),
            universe_row("PENNY", 5),
            universe_row("NOPE", None, status="unresolved"),
        ],
    )
    for symbol, con_id in (("AAA", 1), ("BBB", 2), ("CCC", 3), ("DDD", 4)):
        await upsert_daily(daily(symbol, con_id))
    await upsert_daily(daily("PENNY", 5, qualified=False))


class FakeSnapshots:
    """Batch semantics of the actor: request errors in place, fatal ones raise."""

    def __init__(self, answers: dict[int, MarketSnapshot | Exception]) -> None:
        self.answers = answers
        self.calls: list[int] = []
        self.batches: list[list[int]] = []
        self.connected = True
        self.last_error: str | None = None

    async def market_snapshots(
        self, con_ids: Any, *, timeout_seconds: float = 3.0
    ) -> list[MarketSnapshot | TWSRequestError]:
        self.batches.append(list(con_ids))
        results: list[MarketSnapshot | TWSRequestError] = []
        for con_id in con_ids:
            self.calls.append(con_id)
            answer = self.answers[con_id]
            if isinstance(answer, (TWSCompetingSessionError, TWSConnectionError)):
                raise answer
            if isinstance(answer, TWSRequestError):
                results.append(answer)
            elif isinstance(answer, Exception):
                raise answer
            else:
                results.append(answer)
        return results


@pytest.mark.asyncio
async def test_sweep_snapshots_qualified_rows_counts_errors_and_persists(database) -> None:
    await seed_screener_universe()
    observed = NOW.astimezone(ET)
    source = FakeSnapshots(
        {
            1: snap(1, last="104", observed=observed),
            2: snap(2, last="101", observed=observed),
            3: TWSRequestError("TWS market snapshot timed out without prior close"),
            4: snap(99, last="101", observed=observed),
        }
    )

    result = await run_sweep(source, session_date=DAY, clock=lambda: NOW)

    assert source.calls == [1, 2, 3, 4]
    payload = result.payload
    assert (payload.universe_size, payload.qualified, payload.quoted, payload.errors) == (6, 4, 2, 2)
    assert [row.symbol for row in payload.rows] == ["AAA", "BBB"]
    assert payload.observed_at == "2026-10-12T14:48:00.000000+00:00"
    assert any("different contract" in sample for sample in result.error_samples)
    stored = await latest_snapshot(DAY)
    assert stored == payload


@pytest.mark.asyncio
async def test_competing_session_aborts_the_sweep_without_persisting(database) -> None:
    await seed_screener_universe()
    source = FakeSnapshots(
        {
            1: snap(1),
            2: TWSCompetingSessionError("TWS error 10197: No market data during competing live session"),
            3: snap(3),
            4: snap(4),
        }
    )

    with pytest.raises(TWSCompetingSessionError):
        await run_sweep(source, session_date=DAY, clock=lambda: NOW)
    assert source.calls == [1, 2]
    assert await latest_snapshot(DAY) is None


@pytest.mark.asyncio
async def test_consecutive_failures_stop_the_sweep(database, monkeypatch) -> None:
    await seed_screener_universe()
    monkeypatch.setattr(screener_module, "MAX_CONSECUTIVE_SNAPSHOT_ERRORS", 2)
    failure = TWSRequestError("TWS market snapshot timed out")
    source = FakeSnapshots({1: snap(1), 2: failure, 3: failure, 4: snap(4)})

    with pytest.raises(ScreenerError, match="consecutive"):
        await run_sweep(source, session_date=DAY, clock=lambda: NOW)
    assert await latest_snapshot(DAY) is None


@pytest.mark.asyncio
async def test_sweep_requests_snapshots_in_bounded_chunks(database, monkeypatch) -> None:
    await seed_screener_universe()
    monkeypatch.setattr(screener_module, "SNAPSHOT_CHUNK_SIZE", 3)
    source = FakeSnapshots({con_id: snap(con_id) for con_id in (1, 2, 3, 4)})

    result = await run_sweep(source, session_date=DAY, clock=lambda: NOW)

    assert source.batches == [[1, 2, 3], [4]]
    assert result.payload.quoted == 4


# --- IBKR scan merge ------------------------------------------------------------------


def scan_row(con_id: int, symbol: str, rank: int, **overrides: Any) -> IBKRScannerResult:
    values: dict[str, Any] = {
        "con_id": con_id,
        "symbol": symbol,
        "currency": "USD",
        "security_type": "STK",
        "stock_type": "",
        "primary_exchange": "NYSE",
        "rank": rank,
        "raw_rank": rank - 1,
    }
    values.update(overrides)
    return IBKRScannerResult(**values)


def test_scan_candidates_dedupe_exclude_and_order_by_best_rank() -> None:
    hot = [scan_row(10, "TEN", 1), scan_row(1, "AAA", 2), scan_row(11, "ELEVEN", 3)]
    gainers = [scan_row(11, "ELEVEN", 1), scan_row(12, "TWELVE", 2), scan_row(0, "BAD", 3)]
    active = [
        scan_row(13, "", 1),
        scan_row(14, "CAD", 1, currency="CAD"),
        scan_row(15, "FUND", 1, security_type="ETF"),
        scan_row(16, "SIXTEEN", 4),
    ]

    ordered = scan_candidates([hot, gainers, active], exclude={1})

    assert [row.con_id for row in ordered] == [10, 11, 12, 16]


def history_batch(con_id: int, *, close: str = "50", volume: str = "500000") -> DailyBarBatch:
    bars = tuple(
        IBKRDailyBar(
            con_id=con_id,
            session_date=DAY - timedelta(days=offset),
            close=Decimal(close),
            volume=Decimal(volume),
        )
        for offset in range(21, 0, -1)
    )
    return DailyBarBatch(bars=bars)


class FakeScanSource(FakeSnapshots):
    def __init__(
        self,
        answers: dict[int, MarketSnapshot | Exception],
        scans: dict[str, Any],
        histories: dict[int, DailyBarBatch],
    ) -> None:
        super().__init__(answers)
        self.scans = scans
        self.histories = histories
        self.scan_calls: list[str] = []
        self.history_calls: list[int] = []

    async def screener_scan(self, scan_code: str) -> Any:
        self.scan_calls.append(scan_code)
        answer = self.scans.get(scan_code, ())
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        self.history_calls.append(con_id)
        return self.histories[con_id]


def merge(source: FakeScanSource, **overrides: Any) -> ScanMerge:
    return ScanMerge(
        source=source,
        volume_multiplier=Decimal("100"),
        history_pacer=RequestPacer(0),
        **overrides,
    )


@pytest.mark.asyncio
async def test_scan_additions_qualify_once_and_respect_both_caps(database) -> None:
    await seed_screener_universe()
    await upsert_daily(daily("CACHED", 52))
    source = FakeScanSource(
        {},
        {
            "HOT_BY_VOLUME": [scan_row(1, "AAA", 1), scan_row(50, "NEW", 2), scan_row(51, "PRICEY", 3)],
            "TOP_PERC_GAIN": TWSRequestError("TWS screener scan timed out"),
            "MOST_ACTIVE": [scan_row(52, "CACHED", 4), scan_row(53, "LATE", 5), scan_row(54, "LAST", 6)],
        },
        {
            50: history_batch(50),
            51: history_batch(51, close="400"),
            53: history_batch(53),
            54: history_batch(54),
        },
    )

    additions = await scan_additions(
        merge(source, max_additions=3, max_qualifications=2),
        session_date=DAY,
        exclude=frozenset({1, 2, 3, 4, 5}),
        clock=lambda: NOW,
    )

    assert source.scan_calls == ["HOT_BY_VOLUME", "TOP_PERC_GAIN", "MOST_ACTIVE"]
    # 50 and 51 use the two qualification slots; 52 is reused from today's row.
    assert source.history_calls == [50, 51]
    assert [(item.symbol, item.con_id, item.source) for item in additions] == [
        ("NEW", 50, "ibkr_scan"),
        ("CACHED", 52, "ibkr_scan"),
    ]
    assert additions[0].avg_daily_volume_shares == Decimal("50000000.00")
    stored = {row.con_id: row for row in await load_daily(DAY)}
    assert stored[51].reason == "price_out_of_range"

    source.history_calls.clear()
    again = await scan_additions(
        merge(source, max_additions=3, max_qualifications=2),
        session_date=DAY,
        exclude=frozenset({1, 2, 3, 4, 5}),
        clock=lambda: NOW,
    )
    assert source.history_calls == [53]
    assert [item.con_id for item in again] == [50, 52, 53]


@pytest.mark.asyncio
async def test_competing_login_during_scans_aborts(database) -> None:
    await seed_screener_universe()
    blocked = TWSCompetingSessionError("TWS error 10197: competing live session")
    source = FakeScanSource({}, {"HOT_BY_VOLUME": blocked}, {})

    with pytest.raises(TWSCompetingSessionError):
        await scan_additions(merge(source), session_date=DAY, exclude=frozenset(), clock=lambda: NOW)


@pytest.mark.asyncio
async def test_sweep_ranks_scan_additions_beside_the_watchlist(database) -> None:
    await seed_screener_universe()
    observed = NOW.astimezone(ET)
    answers: dict[int, MarketSnapshot | Exception] = {
        con_id: snap(con_id, observed=observed) for con_id in (1, 2, 3, 4)
    }
    answers[50] = snap(50, last="110", observed=observed)
    source = FakeScanSource(
        answers,
        {"TOP_PERC_GAIN": [scan_row(50, "MOVER", 1), scan_row(1, "AAA", 2)]},
        {50: history_batch(50)},
    )

    result = await run_sweep(source, session_date=DAY, clock=lambda: NOW, scan_merge=merge(source))

    payload = result.payload
    assert (payload.universe_size, payload.qualified, payload.quoted) == (7, 5, 5)
    mover = next(row for row in payload.rows if row.con_id == 50)
    assert (mover.symbol, mover.source, mover.primary_exchange, mover.hot_rank) == (
        "MOVER",
        "ibkr_scan",
        "NYSE",
        1,
    )
    assert sum(1 for row in payload.rows if row.con_id == 1) == 1


# --- market context -------------------------------------------------------------------------

ETF_IDS = {symbol: 9000 + index for index, (symbol, _) in enumerate(CONTEXT_SYMBOLS)}
ETF_EXCHANGES = dict(CONTEXT_SYMBOLS)


def etf_details(symbol: str, **overrides: Any) -> IBKRContractDetails:
    values: dict[str, Any] = {
        "con_id": ETF_IDS[symbol],
        "symbol": symbol,
        "currency": "USD",
        "security_type": "STK",
        "stock_type": "ETF",
        "primary_exchange": ETF_EXCHANGES[symbol],
        "min_tick": Decimal("0.01"),
    }
    values.update(overrides)
    return IBKRContractDetails(**values)


class FakeResolver:
    """Resolves every context ETF unless an answer overrides it."""

    def __init__(self, answers: dict[str, IBKRContractDetails | Exception] | None = None) -> None:
        self.answers = dict(answers or {})
        self.calls: list[tuple[str, str | None]] = []

    async def contract_details_for_symbol(
        self, symbol: str, primary_exchange: str | None
    ) -> IBKRContractDetails:
        self.calls.append((symbol, primary_exchange))
        answer = self.answers.get(symbol) or etf_details(symbol)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.asyncio
async def test_context_etfs_resolve_once_and_retry_failures_after_the_wait(screener_logs) -> None:
    clock = FakeClock(at(9, 0))
    resolver = FakeResolver(
        {
            "QQQ": TWSRequestError("TWS contract-details request timed out"),
            "XLRE": etf_details("XLRE", currency="CAD"),
            "XLC": etf_details("XLC", con_id=ETF_IDS["XLK"]),
        }
    )
    etfs = ContextETFs(resolver, clock=clock, retry_after=timedelta(minutes=15))

    first = await etfs.resolve()

    assert resolver.calls == list(CONTEXT_SYMBOLS)
    assert [item.symbol for item in first] == [
        symbol for symbol, _ in CONTEXT_SYMBOLS if symbol not in {"QQQ", "XLRE", "XLC"}
    ]
    warnings = [record.getMessage() for record in screener_logs if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert all(f"{symbol}@" in warnings[0] for symbol in ("QQQ", "XLRE", "XLC"))

    resolver.calls.clear()
    clock.now += timedelta(minutes=14)
    assert await etfs.resolve() == first
    assert resolver.calls == []

    resolver.answers.clear()
    clock.now += timedelta(minutes=1)
    second = await etfs.resolve()
    assert [symbol for symbol, _ in resolver.calls] == ["QQQ", "XLRE", "XLC"]
    assert [item.symbol for item in second] == [symbol for symbol, _ in CONTEXT_SYMBOLS]
    assert etfs.con_ids == frozenset(ETF_IDS.values())


@pytest.mark.asyncio
async def test_context_resolution_propagates_a_competing_login() -> None:
    blocked = TWSCompetingSessionError("TWS error 10197: No market data during competing live session")
    with pytest.raises(TWSCompetingSessionError):
        await ContextETFs(FakeResolver({"SPY": blocked})).resolve()


# Last prices against a prior close of 100; IWM's snapshot fails.
ETF_LASTS = {
    "SPY": "100.50",
    "QQQ": "100.80",
    "DIA": "100.20",
    "XLK": "101.50",
    "XLV": "99.00",
    "XLF": "100.40",
    "XLE": "100.10",
    "XLY": "100.90",
    "XLP": "99.80",
    "XLI": "100.30",
    "XLU": "99.50",
    "XLB": "100.00",
    "XLRE": "99.70",
    "XLC": "101.10",
}


def etf_answers(observed: datetime) -> dict[int, MarketSnapshot | Exception]:
    answers: dict[int, MarketSnapshot | Exception] = {
        ETF_IDS[symbol]: snap(ETF_IDS[symbol], last=last, observed=observed)
        for symbol, last in ETF_LASTS.items()
    }
    answers[ETF_IDS["IWM"]] = TWSRequestError("TWS market snapshot timed out without prior close")
    return answers


async def seed_context_universe() -> None:
    """The owner's watchlist lists two context ETFs beside three stocks."""
    listed = (("AAA", 1), ("BBB", 2), ("CCC", 3), ("XLE", ETF_IDS["XLE"]), ("SPY", 777))
    await replace_universe_source("watchlist", [universe_row(symbol, con_id) for symbol, con_id in listed])
    for symbol, con_id in listed:
        await upsert_daily(daily(symbol, con_id))


@pytest.mark.asyncio
async def test_sweep_records_market_context_and_never_ranks_context_etfs(database, screener_logs) -> None:
    await seed_context_universe()
    observed = NOW.astimezone(ET)
    answers = etf_answers(observed)
    answers.update(
        {
            1: snap(1, last="104", observed=observed),
            2: snap(2, last="101", observed=observed),
            3: snap(3, last="100", observed=observed),
            50: snap(50, last="110", observed=observed),
        }
    )
    source = FakeScanSource(
        answers,
        {"TOP_PERC_GAIN": [scan_row(ETF_IDS["XLF"], "XLF", 1), scan_row(50, "MOVER", 2)]},
        {50: history_batch(50)},
    )
    resolver = FakeResolver()

    result = await run_sweep(
        source,
        session_date=DAY,
        clock=lambda: NOW,
        scan_merge=merge(source),
        context_etfs=ContextETFs(resolver),
    )

    # The ETFs are snapshotted first, in their own batch, and never ranked.
    assert source.batches == [[ETF_IDS[symbol] for symbol, _ in CONTEXT_SYMBOLS], [1, 2, 3, 50]]
    assert source.history_calls == [50]
    payload = result.payload
    assert [row.symbol for row in payload.rows] == ["MOVER", "AAA", "BBB", "CCC"]
    assert not {row.con_id for row in payload.rows} & set(ETF_IDS.values())
    assert (payload.qualified, payload.quoted, payload.errors) == (4, 4, 0)

    context = result.market_context
    assert context is not None
    assert payload.market == context.to_payload()
    assert (context.session_date, context.observed_at) == (DAY, NOW)
    # Breadth counts the watchlist stocks only, not the scan mover.
    assert (context.advancers, context.decliners, context.breadth_up_pct) == (2, 0, Decimal("66.67"))
    assert (context.spy_change_pct, context.qqq_change_pct, context.iwm_change_pct) == (
        Decimal("0.50"),
        Decimal("0.80"),
        None,
    )
    assert context.regime == "risk_on"
    assert context.summary == (
        "Risk-on · S&P +0.5% · Nasdaq +0.8% · 2↑/0↓ · "
        "Leading: Technology +1.5% · Lagging: Health Care -1.0%"
    )
    infos = [record.getMessage() for record in screener_logs if record.levelno == logging.INFO]
    assert [message for message in infos if message.startswith("market ")] == [f"market {context.summary}"]
    warnings = [record.getMessage() for record in screener_logs if record.levelno == logging.WARNING]
    assert any(message.startswith("context ETF snapshot errors: IWM:") for message in warnings)

    stored = await latest_snapshot(DAY)
    assert stored == payload
    assert await latest_market_context(DAY, now=NOW + timedelta(seconds=30)) == context
    assert [row.symbol for row in await latest_hotlist(DAY)] == ["MOVER", "AAA", "BBB"]


@pytest.mark.asyncio
async def test_unresolved_context_etfs_still_stay_out_of_the_rows(database) -> None:
    await seed_context_universe()
    observed = NOW.astimezone(ET)
    source = FakeSnapshots({con_id: snap(con_id, observed=observed) for con_id in (1, 2, 3)})
    failing = FakeResolver(
        {symbol: TWSRequestError("No security definition has been found") for symbol, _ in CONTEXT_SYMBOLS}
    )

    result = await run_sweep(source, session_date=DAY, clock=lambda: NOW, context_etfs=ContextETFs(failing))

    assert source.batches == [[1, 2, 3]]
    assert [row.symbol for row in result.payload.rows] == ["AAA", "BBB", "CCC"]
    context = result.market_context
    assert context is not None and context.regime == "neutral"
    assert context.summary.startswith("Neutral · S&P n/a · Nasdaq n/a · 3↑/0↓ · ")
    assert result.payload.market == context.to_payload()


@pytest.mark.asyncio
async def test_sweep_without_context_etfs_omits_the_market_key(database) -> None:
    await seed_screener_universe()
    source = FakeSnapshots({con_id: snap(con_id) for con_id in (1, 2, 3, 4)})

    result = await run_sweep(source, session_date=DAY, clock=lambda: NOW)

    assert result.market_context is None and result.payload.market is None
    assert "market" not in json.loads(result.payload.to_json())
    assert await latest_market_context(DAY, now=NOW) is None


@pytest.mark.asyncio
async def test_competing_login_during_the_context_batch_aborts_the_sweep(database) -> None:
    await seed_screener_universe()
    answers = etf_answers(NOW)
    answers[ETF_IDS["QQQ"]] = TWSCompetingSessionError(
        "TWS error 10197: No market data during competing live session"
    )
    source = FakeSnapshots(answers)

    with pytest.raises(TWSCompetingSessionError):
        await run_sweep(source, session_date=DAY, clock=lambda: NOW, context_etfs=ContextETFs(FakeResolver()))
    assert len(source.batches) == 1
    assert await latest_snapshot(DAY) is None


# --- service --------------------------------------------------------------------------------


def runtime_config(**overrides: Any) -> ShadowRuntimeConfig:
    values: dict[str, Any] = {
        "paper_account_id": "DU123456",
        "data_client_id": 17,
        "order_client_id": 18,
        "model_id": "claude-opus-5-5",
        "prompt_id": "orb-review-v1",
        "calendar_id": "XNYS",
        "calendar_version": "2026.1",
        "commission_policy_id": "ibkr-fixed",
        "commission_policy_version": 1,
    }
    values.update(overrides)
    return ShadowRuntimeConfig(**values)


def test_screener_uses_its_own_client_id() -> None:
    assert service.screener_actor_config(runtime_config()).client_id == 19
    with pytest.raises(service.ScreenerConfigurationError):
        service.screener_actor_config(runtime_config(order_client_id=19))


def test_screener_window_and_session_dates() -> None:
    assert service.window_session_date(at(7, 59)) is None
    assert service.window_session_date(at(8, 0)) == DAY
    assert service.window_session_date(at(15, 59)) == DAY
    assert service.window_session_date(at(16, 0)) is None
    saturday = at(12, 0, day=date(2026, 10, 10))
    assert service.window_session_date(saturday) is None
    assert service.screener_session_date(saturday) == DAY
    assert service.next_window_open(at(7, 0)) == at(8, 0)
    assert service.next_window_open(at(17, 0)) == at(8, 0, day=date(2026, 10, 13))
    friday_evening = at(17, 0, day=date(2026, 10, 16))
    assert service.next_window_open(friday_evening) == at(8, 0, day=date(2026, 10, 19))


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start.astimezone(timezone.utc)
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.now.timestamp()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class FakeActor:
    """The actor surface the service drives, with scripted failures."""

    def __init__(self, *, connect_failures: int = 0, snapshot_failures: list[Exception] | None = None) -> None:
        self.connect_failures = connect_failures
        self.snapshot_failures = list(snapshot_failures or [])
        self.connected = False
        self.last_error: str | None = None
        self.connects = 0
        self.disconnects = 0
        self.history_calls: list[int] = []
        self.detail_calls: list[str] = []

    async def connect(self) -> None:
        self.connects += 1
        if self.connect_failures:
            self.connect_failures -= 1
            self.last_error = "TWS error 502: Couldn't connect to TWS"
            raise TWSConnectionError("TWS PAPER handshake failed")
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False

    async def contract_details_for_symbol(self, symbol: str, primary_exchange: str | None) -> Any:
        self.detail_calls.append(symbol)
        return etf_details(symbol)

    async def daily_trade_bars(self, con_id: int) -> Any:
        self.history_calls.append(con_id)
        raise TWSRequestError("history unavailable in this test")

    async def market_snapshots(self, con_ids: Any, *, timeout_seconds: float = 3.0) -> list[MarketSnapshot]:
        if self.snapshot_failures:
            raise self.snapshot_failures.pop(0)
        return [snap(con_id) for con_id in con_ids]

    async def screener_scan(self, scan_code: str) -> tuple[Any, ...]:
        return ()


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def screener_logs():
    handler = ListHandler()
    logger = logging.getLogger("autotrader.shadow.screener")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    yield handler.records
    logger.removeHandler(handler)
    logger.setLevel(previous)


@pytest.mark.asyncio
async def test_loop_backs_off_on_connection_failures_then_sweeps(database) -> None:
    clock = FakeClock(at(10, 0))
    actor = FakeActor(connect_failures=2)

    sweeps = await service.run_screener(
        runtime_config=runtime_config(),
        actor_factory=lambda config: actor,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_sweeps=1,
    )

    assert sweeps == 1
    assert clock.sleeps == [5.0, 10.0]
    assert actor.connects == 3 and actor.connected is False
    assert await latest_snapshot(DAY) is not None


@pytest.mark.asyncio
async def test_competing_login_is_reported_and_retried_every_minute(database, screener_logs) -> None:
    # Qualification is already final for the day; only the sweep runs.
    await seed_screener_universe()
    clock = FakeClock(at(10, 0))
    blocked = TWSCompetingSessionError("TWS error 10197: No market data during competing live session")
    actor = FakeActor(snapshot_failures=[blocked, blocked])

    sweeps = await service.run_screener(
        runtime_config=runtime_config(),
        actor_factory=lambda config: actor,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_sweeps=1,
    )

    assert sweeps == 1
    assert clock.sleeps == [60.0, 60.0]
    errors = [record for record in screener_logs if record.levelno == logging.ERROR]
    assert len(errors) == 2
    assert all(
        "IBKR login active elsewhere (phone/web) — log out there" in record.getMessage()
        for record in errors
    )


@pytest.mark.asyncio
async def test_loop_idles_outside_the_window_in_bounded_chunks(database) -> None:
    clock = FakeClock(at(12, 0, day=date(2026, 10, 10)))
    actor = FakeActor()

    sweeps = await service.run_screener(
        runtime_config=runtime_config(),
        actor_factory=lambda config: actor,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_sweeps=1,
    )

    assert sweeps == 1
    assert max(clock.sleeps) <= service.MAX_SLEEP_CHUNK_SECONDS
    assert sum(clock.sleeps) == pytest.approx(44 * 3600)
    assert actor.connects == 1


@pytest.mark.asyncio
async def test_sweeps_repeat_on_the_interval(database) -> None:
    clock = FakeClock(at(10, 0))
    actor = FakeActor(
        connect_failures=1,
        snapshot_failures=[TWSConnectionError("TWS connection closed")],
    )

    sweeps = await service.run_screener(
        runtime_config=runtime_config(),
        interval_seconds=60,
        actor_factory=lambda config: actor,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_sweeps=3,
    )

    assert sweeps == 3
    assert clock.sleeps == [5.0, 5.0, 60.0, 60.0]
    # Resolved once for the process, across reconnects and sweeps.
    assert actor.detail_calls == [symbol for symbol, _ in CONTEXT_SYMBOLS]
    context = await latest_market_context(DAY, now=clock.now)
    assert context is not None and context.observed_at == at(10, 2) + timedelta(seconds=10)


# --- CLI ---------------------------------------------------------------------------------------


class SymbolClient(FakeTWSClient):
    KNOWN = {("AAPL", "NASDAQ"): 265598, ("BRK B", "NYSE"): 72063691}

    def reqContractDetails(self, request_id: int, contract: object) -> None:
        key = (getattr(contract, "symbol", ""), getattr(contract, "primaryExchange", ""))
        con_id = self.KNOWN.get(key)
        if con_id is None:
            self.callbacks.error(request_id, 200, "No security definition has been found for the request")
            return
        details = SimpleNamespace(
            contract=SimpleNamespace(
                conId=con_id, symbol=key[0], currency="USD", secType="STK", primaryExchange=key[1]
            ),
            minTick=0.01,
            stockType="COMMON",
        )
        self.callbacks.contractDetails(request_id, details)
        self.callbacks.contractDetailsEnd(request_id)


@pytest.fixture
def cli_environment(tmp_path, monkeypatch):
    import trading.cli as cli
    import trading.operations.settings as settings

    monkeypatch.setattr(cli, "_interactive_screener_logger", lambda: logging.getLogger("autotrader.shadow.screener"))
    monkeypatch.setattr(settings, "load_shadow_runtime_config", lambda: runtime_config())
    monkeypatch.setattr(service, "DAILY_HISTORY_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(service, "CONTRACT_DETAILS_MIN_INTERVAL_SECONDS", 0.0)

    def actor_factory(config: TWSActorConfig) -> TWSPaperActor:
        fast = replace(config, handshake_timeout_seconds=0.2, request_timeout_seconds=0.5)
        return TWSPaperActor(fast, client_factory=lambda callbacks: SymbolClient(callbacks))

    monkeypatch.setattr(service, "default_actor_factory", actor_factory)
    return tmp_path


def test_cli_imports_qualifies_and_sweeps_once(cli_environment, capsys) -> None:
    from trading.cli import main

    export = cli_environment / "watchlist.txt"
    export.write_text("NASDAQ:AAPL,NYSE:BRK.B\nOTC:ABCD\n", encoding="utf-8")
    database = cli_environment / "cli.db"

    assert main(["--db-path", str(database), "universe", "import", str(export)]) == 0
    imported = capsys.readouterr().out
    assert "resolved:          2" in imported
    assert "OTC:ABCD: unsupported listing exchange 'OTC'" in imported

    assert main(["--db-path", str(database), "universe", "qualify", "--date", "2026-10-12"]) == 0
    qualified = capsys.readouterr().out
    assert "2 resolved instruments" in qualified
    assert "insufficient_daily_history" in qualified

    assert main(["--db-path", str(database), "screener", "run", "--once"]) == 0
    assert "qualified 0, quoted 0, errors 0" in capsys.readouterr().out
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM autotrader_screener_snapshots").fetchone()[0] == 1
        stored = json.loads(db.execute("SELECT payload_json FROM autotrader_screener_snapshots").fetchone()[0])
    # This TWS double knows no ETFs: the sweep continues without them.
    assert stored["market"]["regime"] == "neutral"
    assert stored["market"]["summary"].startswith("Neutral · S&P n/a · Nasdaq n/a")


def test_cli_reports_a_competing_login_clearly(cli_environment, capsys, monkeypatch) -> None:
    from trading.cli import main

    async def blocked(**kwargs: Any) -> Any:
        raise TWSCompetingSessionError("TWS error 10197: No market data during competing live session")

    monkeypatch.setattr(service, "run_screener_once", blocked)

    assert main(["--db-path", str(cli_environment / "cli.db"), "screener", "run", "--once"]) == 1
    assert "IBKR login active elsewhere (phone/web) — log out there" in capsys.readouterr().err
