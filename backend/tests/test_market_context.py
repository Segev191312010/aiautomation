"""Market context: regime thresholds, breadth, sector ranking, payload, reader."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import random
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.market.context import (
    CONTEXT_SYMBOLS,
    MARKET_CONTEXT_SCHEMA,
    SECTOR_NAMES,
    MarketContext,
    MarketContextError,
    build_market_context,
    classify_regime,
    latest_market_context,
    rank_sectors,
)
from trading.market.snapshots import MarketSnapshot

D = Decimal
ET = ZoneInfo("America/New_York")
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, 14, 48, tzinfo=timezone.utc)
PAYLOAD_KEYS = [
    "schema",
    "session_date",
    "observed_at",
    "regime",
    "spy_change_pct",
    "qqq_change_pct",
    "iwm_change_pct",
    "dia_change_pct",
    "breadth_up_pct",
    "advancers",
    "decliners",
    "median_change_pct",
    "sector_leaders",
    "sector_laggards",
    "summary",
]


def etf(
    last: str | None,
    *,
    prior: str = "100",
    bid: str | None = None,
    ask: str | None = None,
    con_id: int = 1,
) -> MarketSnapshot:
    def dec(value: str | None) -> Decimal | None:
        return None if value is None else Decimal(value)

    return MarketSnapshot(
        con_id=con_id,
        observed_at=NOW,
        prior_close=Decimal(prior),
        last=dec(last),
        bid=dec(bid),
        ask=dec(ask),
    )


def rows(up: int, down: int, flat: int = 0, *, source: str = "watchlist") -> list[dict[str, object]]:
    def row(change: str) -> dict[str, object]:
        return {"symbol": "X", "source": source, "change_pct": change}

    return [row("1.00")] * up + [row("-1.00")] * down + [row("0.00")] * flat


def context(
    etfs: dict[str, MarketSnapshot] | None = None,
    watchlist: list[dict[str, object]] | None = None,
    *,
    observed_at: datetime = NOW,
) -> MarketContext:
    return build_market_context(
        session_date=DAY,
        observed_at=observed_at,
        etf_snapshots=etfs or {},
        watchlist_rows=watchlist or [],
    )


# One last price per context ETF against a prior close of 100.
CONTRACT_EXAMPLE = {
    "SPY": "99.90",
    "QQQ": "99.30",
    "IWM": "100.25",
    "DIA": "99.95",
    "XLV": "101.20",
    "XLE": "100.80",
    "XLU": "100.60",
    "XLP": "100.30",
    "XLRE": "100.10",
    "XLB": "100.00",
    "XLI": "99.80",
    "XLF": "99.60",
    "XLY": "99.40",
    "XLC": "99.10",
    "XLK": "98.90",
}


def example_etfs(**overrides: str | None) -> dict[str, MarketSnapshot]:
    lasts = {**CONTRACT_EXAMPLE, **overrides}
    return {symbol: etf(last) for symbol, last in lasts.items() if last is not None}


# --- regime ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spy", "qqq", "breadth", "expected"),
    [
        ("-0.75", "0.00", "39.99", "risk_off"),  # SPY exactly at its threshold
        ("-0.74", "-0.99", "39.99", "neutral"),  # both just above their thresholds
        ("0.00", "-1.00", "39.99", "risk_off"),  # QQQ exactly at its threshold
        ("0.00", "-1.0", "39.99", "risk_off"),
        ("-2.00", "-3.00", "40.00", "neutral"),  # breadth must be strictly below 40
        ("1.00", "-1.50", "10.00", "risk_off"),  # a Nasdaq sell-off with weak breadth
        ("0.30", "0.00", "55.01", "risk_on"),  # SPY exactly at its threshold
        ("0.29", "0.00", "99.00", "neutral"),
        ("0.30", "0.00", "55.00", "neutral"),  # breadth must be strictly above 55
        ("5.00", "-5.00", "55.01", "risk_on"),  # QQQ weakness needs weak breadth too
        ("0.00", "0.00", "50.00", "neutral"),
    ],
)
def test_regime_thresholds_at_their_boundaries(spy: str, qqq: str, breadth: str, expected: str) -> None:
    assert classify_regime(spy_change_pct=D(spy), qqq_change_pct=D(qqq), breadth_up_pct=D(breadth)) == expected


@pytest.mark.parametrize("missing", ["spy_change_pct", "qqq_change_pct", "breadth_up_pct"])
@pytest.mark.parametrize(
    "inputs",
    [
        {"spy_change_pct": D("-5"), "qqq_change_pct": D("-5"), "breadth_up_pct": D("5")},
        {"spy_change_pct": D("5"), "qqq_change_pct": D("5"), "breadth_up_pct": D("95")},
    ],
    ids=["risk_off_inputs", "risk_on_inputs"],
)
def test_any_missing_input_is_neutral(inputs: dict[str, Decimal], missing: str) -> None:
    assert classify_regime(**inputs) != "neutral"
    assert classify_regime(**{**inputs, missing: None}) == "neutral"
    assert classify_regime(**{**inputs, missing: D("NaN")}) == "neutral"


def test_regime_inputs_must_be_decimals() -> None:
    with pytest.raises(TypeError):
        classify_regime(
            spy_change_pct=-1.0,  # type: ignore[arg-type]
            qqq_change_pct=D("0"),
            breadth_up_pct=D("10"),
        )


# --- build ----------------------------------------------------------------------------------


def test_contract_example_builds_the_documented_summary_line() -> None:
    built = context(example_etfs(), rows(247, 140, 13))

    assert built.summary == (
        "Neutral · S&P -0.1% · Nasdaq -0.7% · 247↑/140↓ · "
        "Leading: Health Care +1.2% · Lagging: Technology -1.1%"
    )
    assert built.regime == "neutral"
    assert (built.spy_change_pct, built.qqq_change_pct, built.iwm_change_pct, built.dia_change_pct) == (
        D("-0.10"),
        D("-0.70"),
        D("0.25"),
        D("-0.05"),
    )
    # 247 of 400 quoted watchlist rows are up; unchanged rows count as quoted.
    assert (built.advancers, built.decliners, built.breadth_up_pct) == (247, 140, D("61.75"))
    assert built.median_change_pct == D("1.00")
    assert built.sector_leaders == (
        ("Health Care", D("1.20")),
        ("Energy", D("0.80")),
        ("Utilities", D("0.60")),
    )
    assert built.sector_laggards == (
        ("Technology", D("-1.10")),
        ("Communication", D("-0.90")),
        ("Consumer Discretionary", D("-0.60")),
    )
    assert built.session_date == DAY and built.observed_at == NOW


def test_risk_off_and_risk_on_examples() -> None:
    risk_off = context(example_etfs(SPY="99.00", QQQ="98.50"), rows(100, 200))
    assert risk_off.regime == "risk_off"
    assert risk_off.breadth_up_pct == D("33.33")
    assert risk_off.summary.startswith("Risk-off · S&P -1.0% · Nasdaq -1.5% · 100↑/200↓ · ")

    risk_on = context(example_etfs(SPY="100.30", QQQ="100.50"), rows(60, 40))
    assert risk_on.regime == "risk_on"
    assert risk_on.summary.startswith("Risk-on · S&P +0.3% · Nasdaq +0.5% · 60↑/40↓ · ")


def test_rounded_values_decide_the_regime() -> None:
    # -0.745% rounds half-up to the -0.75% the owner sees, which is risk_off.
    built = context(example_etfs(SPY="99.255"), rows(30, 70))
    assert built.spy_change_pct == D("-0.75")
    assert built.regime == "risk_off"
    # A tiny loss rounds to an unsigned zero.
    flat = context(example_etfs(SPY="99.999"), rows(1, 0))
    assert str(flat.spy_change_pct) == "0.00"
    assert "S&P 0.0%" in flat.summary


def test_pre_market_change_uses_the_bid_ask_midpoint() -> None:
    built = context({"SPY": etf(None, bid="100.10", ask="100.30"), "QQQ": etf("101")}, rows(1, 0))
    assert built.spy_change_pct == D("0.20")
    assert built.qqq_change_pct == D("1.00")


def test_breadth_counts_only_quoted_watchlist_rows() -> None:
    watchlist = [
        {"source": "watchlist", "change_pct": "2.50"},
        {"source": "watchlist", "change_pct": D("-0.40")},
        {"source": "watchlist", "change_pct": D("0.004")},  # displays as 0.00: unchanged
        {"source": "watchlist", "change_pct": None},  # not quoted
        {"source": "ibkr_scan", "change_pct": "9.00"},  # scan additions are not the universe
        {"change_pct": "3.00"},
    ]
    built = context({}, watchlist)
    assert (built.advancers, built.decliners) == (1, 1)
    assert built.breadth_up_pct == D("33.33")
    assert built.median_change_pct == D("0.00")


@pytest.mark.parametrize(
    ("changes", "median"),
    [
        (["0.50", "-0.25", "3.00"], "0.50"),
        (["0.01", "0.02"], "0.02"),  # 0.015 rounds half-up
        (["-0.01", "-0.02"], "-0.02"),
        (["4.00"], "4.00"),
    ],
)
def test_median_change_handles_odd_and_even_counts(changes: list[str], median: str) -> None:
    built = context({}, [{"source": "watchlist", "change_pct": change} for change in changes])
    assert built.median_change_pct == D(median)


def test_malformed_row_change_is_rejected() -> None:
    with pytest.raises(MarketContextError):
        context({}, [{"source": "watchlist", "change_pct": "1.2.3"}])
    with pytest.raises(MarketContextError):
        context({}, [{"source": "watchlist", "change_pct": 1.5}])


def test_missing_etfs_and_rows_leave_values_unknown_and_the_regime_neutral() -> None:
    built = context({"AAPL": etf("150")}, [])
    assert built.regime == "neutral"
    assert built.summary == (
        "Neutral · S&P n/a · Nasdaq n/a · breadth n/a · Leading: n/a · Lagging: n/a"
    )
    assert (built.spy_change_pct, built.breadth_up_pct, built.median_change_pct) == (None, None, None)
    assert (built.advancers, built.decliners) == (0, 0)
    assert built.sector_leaders == () and built.sector_laggards == ()


def test_missing_qqq_alone_keeps_a_sell_off_neutral() -> None:
    built = context(example_etfs(SPY="98.00", QQQ=None), rows(10, 90))
    assert built.qqq_change_pct is None
    assert built.regime == "neutral"
    assert "Nasdaq n/a" in built.summary


def test_sector_ties_order_by_name() -> None:
    changes = {symbol: D("0.00") for symbol in SECTOR_NAMES}
    changes.update(XLK=D("1.00"), XLV=D("1.00"), XLF=D("1.00"), XLC=D("1.00"))
    changes.update(XLE=D("-0.50"), XLB=D("-0.50"), XLU=D("-0.50"), XLRE=D("-0.50"))

    leaders, laggards = rank_sectors(changes)

    assert [name for name, _ in leaders] == ["Communication", "Financials", "Health Care"]
    assert [name for name, _ in laggards] == ["Energy", "Materials", "Real Estate"]
    shuffled = list(changes.items())
    random.Random(3).shuffle(shuffled)
    assert rank_sectors(dict(shuffled)) == (leaders, laggards)


@pytest.mark.parametrize(
    ("quoted", "lead", "lag"),
    [(0, 0, 0), (1, 1, 0), (2, 1, 1), (3, 2, 1), (4, 2, 2), (5, 3, 2), (6, 3, 3), (11, 3, 3)],
)
def test_few_sectors_split_without_overlap(quoted: int, lead: int, lag: int) -> None:
    symbols = list(SECTOR_NAMES)[:quoted]
    changes = {symbol: D(index) for index, symbol in enumerate(symbols)}

    leaders, laggards = rank_sectors(changes)

    assert (len(leaders), len(laggards)) == (lead, lag)
    assert not {name for name, _ in leaders} & {name for name, _ in laggards}
    if laggards:
        assert laggards[0][1] == min(changes.values())
    if leaders:
        assert leaders[0][1] == max(changes.values())


def test_context_is_deterministic_and_its_regime_rederives_from_stored_values() -> None:
    generator = random.Random(11)
    for _ in range(200):
        etfs = {
            symbol: etf(str(D(10000 + generator.randint(-300, 300)) / 100), con_id=index + 1)
            for index, (symbol, _) in enumerate(CONTEXT_SYMBOLS)
            if generator.random() > 0.1
        }
        watchlist = [
            {"source": generator.choice(["watchlist", "watchlist", "ibkr_scan"]),
             "change_pct": str(D(generator.randint(-500, 500)) / 100)}
            for _ in range(generator.randint(0, 60))
        ]
        built = context(etfs, watchlist)
        shuffled_rows = list(watchlist)
        generator.shuffle(shuffled_rows)
        shuffled_etfs = list(etfs.items())
        generator.shuffle(shuffled_etfs)
        assert context(dict(shuffled_etfs), shuffled_rows) == built
        assert built.regime == classify_regime(
            spy_change_pct=built.spy_change_pct,
            qqq_change_pct=built.qqq_change_pct,
            breadth_up_pct=built.breadth_up_pct,
        )
        assert MarketContext.from_payload(json.loads(json.dumps(built.to_payload()))) == built


def test_observed_at_is_normalized_to_utc_and_must_be_aware() -> None:
    built = context(observed_at=NOW.astimezone(ET))
    assert built.observed_at == NOW and built.observed_at.utcoffset() == timedelta(0)
    with pytest.raises(MarketContextError):
        context(observed_at=NOW.replace(tzinfo=None))


def test_construction_rejects_shape_violations() -> None:
    valid = context(example_etfs(), rows(3, 1))
    for changes in (
        {"regime": "panic"},
        {"spy_change_pct": -0.1},
        {"breadth_up_pct": D("100.01")},
        {"advancers": -1},
        {"decliners": True},
        {"sector_leaders": (("Tech", D("1")),)},
        {"sector_laggards": (("Energy", D("1")),) * 4},
        {"summary": "two\nlines"},
        {"summary": "trailing break\n"},
        {"summary": " "},
        {"session_date": NOW},
    ):
        with pytest.raises(MarketContextError):
            replace(valid, **changes)
    # Sector pairs given as lists are stored as tuples.
    listed = replace(valid, sector_leaders=[["Energy", D("1.00")]])
    assert listed.sector_leaders == (("Energy", D("1.00")),)


# --- payload ----------------------------------------------------------------------------------


def test_payload_is_json_native_and_round_trips() -> None:
    built = context(example_etfs(IWM=None), rows(247, 140, 13))

    document = json.loads(json.dumps(built.to_payload()))

    assert list(document) == PAYLOAD_KEYS
    assert document["schema"] == MARKET_CONTEXT_SCHEMA == "market_context.v1"
    assert document["observed_at"] == "2026-10-12T14:48:00.000000+00:00"
    assert (document["spy_change_pct"], document["iwm_change_pct"]) == ("-0.10", None)
    assert (document["breadth_up_pct"], document["advancers"], document["decliners"]) == ("61.75", 247, 140)
    assert document["sector_leaders"][0] == ["Health Care", "1.20"]
    assert document["summary"] == built.summary
    assert MarketContext.from_payload(document) == built


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update(schema="market_context.v2"),
        lambda doc: doc.update(schema_id=doc.pop("schema")),
        lambda doc: doc.update(extra=1),
        lambda doc: doc.pop("summary"),
        lambda doc: doc.update(spy_change_pct="-0.1.0"),
        lambda doc: doc.update(spy_change_pct=-0.1),
        lambda doc: doc.update(observed_at="2026-10-12T14:48:00"),
        lambda doc: doc.update(session_date="2026-13-40"),
        lambda doc: doc.update(regime="panic"),
        lambda doc: doc.update(advancers=-1),
        lambda doc: doc.update(advancers=True),
        lambda doc: doc.update(advancers="247"),
        lambda doc: doc.update(breadth_up_pct="101.00"),
        lambda doc: doc["sector_leaders"].append(["Energy", "0.10"]),
        lambda doc: doc["sector_laggards"][0].__setitem__(0, "Tech"),
        lambda doc: doc.update(summary="two\nlines"),
        # The stored numbers say risk_off; a document claiming otherwise is corrupt.
        lambda doc: doc.update(regime="neutral"),
    ],
)
def test_from_payload_rejects_contract_violations(mutate: Any) -> None:
    built = context(example_etfs(SPY="99.00", QQQ="98.50"), rows(100, 200))
    document = json.loads(json.dumps(built.to_payload()))
    mutate(document)
    with pytest.raises(MarketContextError):
        MarketContext.from_payload(document)


def test_from_payload_requires_an_object() -> None:
    with pytest.raises(MarketContextError):
        MarketContext.from_payload([("schema", MARKET_CONTEXT_SCHEMA)])  # type: ignore[arg-type]


# --- latest_market_context ----------------------------------------------------------------------


@pytest.fixture
async def database(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    path = tmp_path / "context.db"
    monkeypatch.setattr(cfg, "DB_PATH", str(path))
    await init_db()
    return path


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def context_logs():
    handler = ListHandler()
    logger = logging.getLogger("autotrader.shadow.screener")
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


_ABSENT = object()


def store(
    path: Any,
    snapshot_id: str,
    observed_at: datetime,
    market: object = _ABSENT,
    *,
    session: date = DAY,
) -> None:
    observed = observed_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    payload: dict[str, object] = {
        "schema": "screener.v1",
        "session_date": session.isoformat(),
        "observed_at": observed,
        "rows": [],
    }
    if market is not _ABSENT:
        payload["market"] = market
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO autotrader_screener_snapshots VALUES (?,?,?,?)",
            (snapshot_id, session.isoformat(), observed, json.dumps(payload)),
        )


def sweep_context(observed_at: datetime, *, spy: str = "99.90") -> MarketContext:
    return context(example_etfs(SPY=spy), rows(247, 140, 13), observed_at=observed_at)


@pytest.mark.asyncio
async def test_latest_context_reads_the_newest_sweep_of_the_session(database) -> None:
    older = sweep_context(NOW - timedelta(minutes=2), spy="99.00")
    newer = sweep_context(NOW - timedelta(minutes=1))
    store(database, "b", newer.observed_at, newer.to_payload())
    store(database, "a", older.observed_at, older.to_payload())

    assert await latest_market_context(DAY, now=NOW) == newer
    assert await latest_market_context(DAY + timedelta(days=1), now=NOW) is None


@pytest.mark.asyncio
async def test_latest_context_is_none_without_any_sweep(database) -> None:
    assert await latest_market_context(DAY, now=NOW) is None


@pytest.mark.asyncio
async def test_latest_context_staleness_boundary(database) -> None:
    observed = NOW - timedelta(minutes=5)
    stored = sweep_context(observed)
    store(database, "a", observed, stored.to_payload())

    assert await latest_market_context(DAY, now=NOW) == stored
    assert await latest_market_context(DAY, now=NOW + timedelta(microseconds=1)) is None
    later = NOW + timedelta(minutes=5)
    assert await latest_market_context(DAY, now=later, max_age=timedelta(minutes=10)) == stored


@pytest.mark.asyncio
async def test_latest_context_needs_the_newest_sweep_to_carry_market(database) -> None:
    older = sweep_context(NOW - timedelta(minutes=2))
    store(database, "a", older.observed_at, older.to_payload())
    store(database, "b", NOW - timedelta(minutes=1))
    assert await latest_market_context(DAY, now=NOW) is None
    store(database, "c", NOW - timedelta(seconds=30), None)
    assert await latest_market_context(DAY, now=NOW) is None


@pytest.mark.asyncio
async def test_latest_context_never_reads_a_sweep_after_now(database) -> None:
    current = sweep_context(NOW - timedelta(minutes=1))
    future = sweep_context(NOW + timedelta(seconds=1), spy="99.00")
    store(database, "a", current.observed_at, current.to_payload())
    store(database, "b", future.observed_at, future.to_payload())

    assert await latest_market_context(DAY, now=NOW) == current


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "market",
    [
        "Neutral · S&P -0.1%",
        5,
        json.dumps({"schema": MARKET_CONTEXT_SCHEMA}),
        {"schema": MARKET_CONTEXT_SCHEMA},
        {**sweep_context(NOW).to_payload(), "regime": "risk_on"},
        {**sweep_context(NOW).to_payload(), "session_date": "2026-10-13"},
    ],
    ids=["text", "number", "stringified", "partial", "contradicted_regime", "other_session"],
)
async def test_unusable_stored_context_is_reported_and_reads_as_unknown(
    database, context_logs, market: object
) -> None:
    store(database, "bad", NOW - timedelta(seconds=10), market)

    assert await latest_market_context(DAY, now=NOW) is None
    errors = [record for record in context_logs if record.levelno == logging.ERROR]
    assert len(errors) == 1 and "bad" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_latest_context_requires_an_aware_now_and_positive_age(database) -> None:
    with pytest.raises(ValueError):
        await latest_market_context(DAY, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        await latest_market_context(DAY, now=NOW, max_age=timedelta(0))
