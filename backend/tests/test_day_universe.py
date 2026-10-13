"""The day universe: admission, its own ranking, held quotes, and Principle 1's golden test.

Principle 1: the real MOMO's inputs (the hot-35 and the regime) must not change.
The golden test feeds one fixed set of snapshots to a sweep without and with
day-universe names and held symbols, and requires the identical ranking group
and market context, pinned to a digest produced by the deployed sweep code.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import random
import sqlite3
import sys
from types import ModuleType
from typing import Any

import pytest

from tests.test_screener import (
    DAY,
    ET,
    NOW,
    FakeClock,
    FakeResolver,
    FakeScanSource,
    at,
    database,  # noqa: F401 - pytest fixture
    etf_answers,
    history_batch,
    merge,
    scan_row,
    snap,
    universe_row,
)
from trading.market import screener as screener_module
from trading.market.day_universe import (
    DAY_UNIVERSE_DAILY_FILENAME,
    DAY_UNIVERSE_MAX,
    DayUniverseDaily,
    DAY_UNIVERSE_PREMARKET_MAX,
    SCAN_ADDITIONS_PER_SWEEP,
    ScanSighting,
    admit_scan_names,
    load_day_universe,
    load_day_universe_members,
    reason_token,
    scan_sightings,
    size_limit,
)
from trading.market.ibkr_boundary import DailyBarBatch, IBKRContractDetails, IBKRDailyBar
from trading.market.screener import (
    ContextETFs,
    DayUniverseSection,
    ScreenerPayload,
    hotlist_rows,
    latest_day_universe,
    latest_hotlist,
    latest_snapshot,
    rank_day_universe,
    rank_observations,
    recheck_prior_closes,
    run_sweep,
    scan_additions,
    scan_candidates,
)
from trading.market.snapshots import MarketSnapshot
from trading.market.tws_actor import TWSConnectionError, TWSRequestError
from trading.market.universe import (
    DailyQualification,
    RequestPacer,
    load_contract_facts,
    load_daily,
    replace_universe_source,
    upsert_daily,
)
from trading.operations import screener_service as service

UTC = timezone.utc


# --- helpers --------------------------------------------------------------------------------


def qualified_row(symbol: str, con_id: int, *, avg: str = "8000000", prior: str = "100",
                  observed: datetime = NOW, qualified: bool = True) -> DailyQualification:
    return DailyQualification(
        session_date=DAY,
        con_id=con_id,
        symbol=symbol,
        prior_close=Decimal(prior),
        avg_daily_volume_shares=Decimal(avg),
        avg_dollar_volume=Decimal(avg) * Decimal(prior),
        daily_atr=None,
        qualified=qualified,
        reason=None if qualified else "price_out_of_range",
        observed_at=observed,
    )


def stock_details(con_id: int, symbol: str, **overrides: Any) -> IBKRContractDetails:
    values: dict[str, Any] = {
        "con_id": con_id,
        "symbol": symbol,
        "currency": "USD",
        "security_type": "STK",
        "stock_type": "COMMON",
        "primary_exchange": "NASDAQ",
        "min_tick": Decimal("0.01"),
        "industry": "Technology",
        "category": "Semiconductors",
    }
    values.update(overrides)
    return IBKRContractDetails(**values)


class FakeMarket(FakeScanSource):
    """Snapshots, scans, daily history and contract details, with an optional clock."""

    def __init__(
        self,
        answers: dict[int, MarketSnapshot | Exception],
        scans: dict[str, Any] | None = None,
        histories: dict[int, DailyBarBatch | Exception] | None = None,
        details: dict[int, IBKRContractDetails | Exception] | None = None,
        *,
        clock: FakeClock | None = None,
        request_seconds: float = 0.0,
    ) -> None:
        super().__init__(answers, scans or {}, histories or {})  # type: ignore[arg-type]
        self.details = details or {}
        self.detail_calls: list[int] = []
        self.clock = clock
        self.request_seconds = request_seconds

    def _spend(self) -> None:
        if self.clock is not None:
            self.clock.now += timedelta(seconds=self.request_seconds)

    async def contract_details(self, con_id: int) -> IBKRContractDetails:
        self.detail_calls.append(con_id)
        self._spend()
        answer = self.details.get(con_id, TWSRequestError("TWS contract-details request timed out"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        self.history_calls.append(con_id)
        self._spend()
        answer = self.histories[con_id]
        if isinstance(answer, Exception):
            raise answer
        return answer


def sighting(con_id: int, symbol: str, rank: int, *codes: str) -> ScanSighting:
    return ScanSighting(
        con_id=con_id,
        symbol=symbol,
        primary_exchange="NYSE",
        best_rank=rank,
        scan_codes=tuple(sorted(codes or ("HOT_BY_VOLUME",))),
    )


def admit(source: FakeMarket, sightings: list[ScanSighting], **overrides: Any) -> Any:
    options: dict[str, Any] = {
        "session_date": DAY,
        "sightings": sightings,
        "volume_multiplier": Decimal("100"),
        "history_pacer": RequestPacer(0),
        "clock": lambda: NOW,
    }
    options.update(overrides)
    return admit_scan_names(source, **options)


def day_rows(path: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as db:
        return db.execute(
            """SELECT con_id, symbol, primary_exchange, qualified, reason, best_rank, sources_json
               FROM autotrader_manager_day_universe ORDER BY con_id"""
        ).fetchall()


def insert_day_names(path: Path, names: list[tuple[str, int]], *, qualified: bool = True) -> None:
    stamp = NOW.isoformat(timespec="microseconds")
    with closing(sqlite3.connect(path)) as db:
        for rank, (symbol, con_id) in enumerate(names, start=1):
            db.execute(
                """INSERT INTO autotrader_manager_day_universe
                   (session_date, con_id, symbol, primary_exchange, first_seen, last_seen,
                    sources_json, best_rank, qualified, reason)
                   VALUES (?, ?, ?, 'NYSE', ?, ?, '["HOT_BY_VOLUME"]', ?, ?, ?)""",
                (DAY.isoformat(), con_id, symbol, stamp, stamp, rank, 1 if qualified else 0,
                 None if qualified else "stock_type_etf"),
            )
        db.commit()


def insert_accepted_decision(path: Path, *, con_id: int, symbol: str, strategy: str, suffix: str) -> None:
    """One shadow_accepted decision of ``strategy`` for the session (as the pipeline writes it)."""
    spec = json.dumps({"name": strategy})
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            """INSERT OR IGNORE INTO autotrader_strategy_versions
               (strategy_id, version, content_hash, spec_json, created_at, created_by)
               VALUES (?, 1, ?, ?, ?, 'owner')""",
            (f"strategy-{strategy}", "e" * 64, spec, NOW.isoformat()),
        )
        db.execute(
            """INSERT INTO autotrader_runs (id, owner_id, source_sha, strategy_id, strategy_version,
               session_date, mode, status, policy_fingerprints_json)
               VALUES (?, 'owner', 'abcdef1', ?, 1, ?, 'shadow', 'running', '{}')""",
            (f"run-{suffix}", f"strategy-{strategy}", DAY.isoformat()),
        )
        db.execute(
            """INSERT INTO autotrader_scanner_runs (id, run_id, parameters_json,
               parameters_fingerprint, status, started_at) VALUES (?, ?, '{}', 'f', 'completed', ?)""",
            (f"scan-{suffix}", f"run-{suffix}", NOW.isoformat()),
        )
        db.execute(
            """INSERT INTO autotrader_candidates (id, scanner_run_id, con_id, symbol, rank,
               contract_json, admission_status) VALUES (?, ?, ?, ?, 1, ?, 'admitted')""",
            (f"cand-{suffix}", f"scan-{suffix}", con_id, symbol, json.dumps({"primary_exchange": "nyse"})),
        )
        db.execute(
            """INSERT INTO autotrader_candidate_decisions (id, run_id, candidate_id, strategy_id,
               strategy_version, decision_key, outcome, payload_json, created_at)
               VALUES (?, ?, ?, ?, 1, ?, 'shadow_accepted', '{}', ?)""",
            (f"dec-{suffix}", f"run-{suffix}", f"cand-{suffix}", f"strategy-{strategy}",
             f"key-{suffix}", NOW.isoformat()),
        )
        db.commit()


# --- reasons, sightings, limits ---------------------------------------------------------


def test_reasons_become_the_tables_snake_case_tokens() -> None:
    assert reason_token("stock_type_ETF") == "stock_type_etf"
    assert reason_token("stock_type_CLOSED_END_FUND") == "stock_type_closed_end_fund"
    assert reason_token("price_out_of_range") == "price_out_of_range"
    assert reason_token("stale daily history cannot feed a candidate") == (
        "stale_daily_history_cannot_feed_a_candidate"
    )
    assert reason_token("20 bars") == "rejected_20_bars"
    assert reason_token("") == "rejected"
    assert len(reason_token("x" * 200)) == 64


def test_sightings_use_the_screeners_filters_and_order_and_keep_every_scan_code() -> None:
    hot = [scan_row(10, "TEN", 1), scan_row(1, "AAA", 2), scan_row(11, "ELEVEN", 3)]
    gainers = [scan_row(11, "ELEVEN", 1), scan_row(12, "TWELVE", 2), scan_row(0, "BAD", 3)]
    active = [
        scan_row(13, "", 1),
        scan_row(14, "CAD", 1, currency="CAD"),
        scan_row(15, "FUND", 1, security_type="ETF"),
        scan_row(16, "SIXTEEN", 4),
        scan_row(10, "TEN", 9),
    ]
    scans = {"HOT_BY_VOLUME": hot, "TOP_PERC_GAIN": gainers, "MOST_ACTIVE": active}

    sightings = scan_sightings(scans, exclude={1})

    assert [item.con_id for item in sightings] == [
        row.con_id for row in scan_candidates([hot, gainers, active], exclude={1})
    ]
    assert [(item.symbol, item.best_rank, item.scan_codes) for item in sightings] == [
        ("TEN", 1, ("HOT_BY_VOLUME", "MOST_ACTIVE")),
        ("ELEVEN", 1, ("HOT_BY_VOLUME", "TOP_PERC_GAIN")),
        ("TWELVE", 2, ("TOP_PERC_GAIN",)),
        ("SIXTEEN", 4, ("MOST_ACTIVE",)),
    ]


def test_the_pre_market_admits_at_most_sixty_names_and_the_session_one_hundred_fifty() -> None:
    assert (DAY_UNIVERSE_MAX, DAY_UNIVERSE_PREMARKET_MAX, SCAN_ADDITIONS_PER_SWEEP) == (150, 60, 40)
    assert size_limit(DAY, at(9, 29)) == DAY_UNIVERSE_PREMARKET_MAX
    assert size_limit(DAY, at(9, 30)) == DAY_UNIVERSE_MAX
    assert size_limit(DAY, at(15, 59)) == DAY_UNIVERSE_MAX


# --- admission ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admission_checks_the_stock_type_then_qualifies_once_and_keeps_contract_facts(database) -> None:
    await upsert_daily(qualified_row("CACHED", 52))
    source = FakeMarket(
        {},
        histories={50: history_batch(50), 51: history_batch(51, close="400")},
        details={
            50: stock_details(50, "NEW"),
            51: stock_details(51, "PRICEY"),
            52: stock_details(52, "CACHED"),
            53: stock_details(53, "ETFX", stock_type="ETF"),
            54: stock_details(54, "OTHER"),  # IBKR names another symbol
            55: TWSRequestError("TWS contract-details request timed out"),
        },
    )
    sightings = [
        sighting(50, "NEW", 1, "HOT_BY_VOLUME", "TOP_PERC_GAIN"),
        sighting(51, "PRICEY", 2),
        sighting(52, "CACHED", 3),
        sighting(53, "ETFX", 4),
        sighting(54, "WRONG", 5),
        sighting(55, "LATER", 6),
    ]

    report = await admit(source, sightings)

    assert (report.admitted, report.rejected, report.failed, report.size) == (2, 3, 1, 2)
    assert source.detail_calls == [50, 51, 52, 53, 54, 55]
    # 52 reuses today's qualification; the ETF never costs a history request.
    assert source.history_calls == [50, 51]
    assert day_rows(database) == [
        (50, "NEW", "NASDAQ", 1, None, 1, '["HOT_BY_VOLUME", "TOP_PERC_GAIN"]'),
        (51, "PRICEY", "NASDAQ", 0, "price_out_of_range", 2, '["HOT_BY_VOLUME"]'),
        (52, "CACHED", "NASDAQ", 1, None, 3, '["HOT_BY_VOLUME"]'),
        (53, "ETFX", "NASDAQ", 0, "stock_type_etf", 4, '["HOT_BY_VOLUME"]'),
        (54, "WRONG", "NYSE", 0, "contract_details_mismatch", 5, '["HOT_BY_VOLUME"]'),
    ]
    facts = await load_contract_facts()
    assert (facts[50].industry, facts[50].category, facts[53].stock_type) == (
        "Technology", "Semiconductors", "ETF",
    )
    # Principle 1: the hot-35's table holds only what its own path wrote.
    assert [row.con_id for row in await load_daily(DAY)] == [52]
    own = DayUniverseDaily().load(DAY)
    assert sorted(own) == [50, 51, 52]
    assert (own[50].qualified, own[51].reason) == (True, "price_out_of_range")
    members = await load_day_universe_members(DAY)
    assert [(member.con_id, member.avg_daily_volume_shares) for member in members] == [
        (50, Decimal("50000000.00")),
        (52, Decimal("8000000")),
    ]
    assert members[0].avg_dollar_volume == Decimal("2500000000.00")

    # Known names are only refreshed; the failed one is retried.
    later = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
    source.detail_calls.clear()
    source.details[55] = stock_details(55, "LATER")
    source.histories[55] = history_batch(55)
    again = await admit(
        source,
        [sighting(52, "CACHED", 1, "MOST_ACTIVE"), sighting(55, "LATER", 2)],
        clock=lambda: later,
    )
    assert (again.refreshed, again.admitted, again.size) == (1, 1, 3)
    assert source.detail_calls == [55]
    cached = next(name for name in await load_day_universe(DAY) if name.con_id == 52)
    assert (cached.best_rank, cached.sources, cached.last_seen, cached.first_seen) == (
        1, ("HOT_BY_VOLUME", "MOST_ACTIVE"), later, NOW,
    )


@pytest.mark.asyncio
async def test_the_day_universes_own_qualifications_live_beside_the_database_for_one_session(
    database, caplog
) -> None:
    store = DayUniverseDaily()
    assert store.path == database.parent / DAY_UNIVERSE_DAILY_FILENAME
    first = qualified_row("AAA", 50, avg="1000")
    store.record([first, replace(first, con_id=51, symbol="BBB", qualified=False, reason="error: pacing",
                                 prior_close=None, avg_daily_volume_shares=None, avg_dollar_volume=None)])

    assert store.load(DAY) == {50: first}  # transient outcomes are never kept
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.load(DAY + timedelta(days=1)) == {}
    # A new session replaces the old one: the file never outlives its session.
    later = replace(first, session_date=DAY + timedelta(days=1))
    store.record([later])
    assert store.load(DAY) == {} and store.load(DAY + timedelta(days=1)) == {50: later}
    # An unreadable file reads as empty: members stay, without a volume baseline.
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load(DAY + timedelta(days=1)) == {}
    assert "unreadable" in caplog.text


@pytest.mark.asyncio
async def test_a_stock_type_rejection_never_reaches_the_hot_35_scan_additions(database) -> None:
    """The legacy scan additions skip the stock-type check; the day universe must not change that."""
    source = FakeMarket(
        {},
        scans={"HOT_BY_VOLUME": [scan_row(53, "ADRX", 1)]},
        histories={53: history_batch(53)},
        details={53: stock_details(53, "ADRX", stock_type="ADR")},
    )

    report = await admit(source, [sighting(53, "ADRX", 1)])
    additions = await scan_additions(merge(source), session_date=DAY, exclude=frozenset(), clock=lambda: NOW)

    assert report.rejected == 1 and source.history_calls == [53]
    assert [row.con_id for row in await load_daily(DAY)] == [53]
    assert [(item.con_id, item.source) for item in additions] == [(53, "ibkr_scan")]


@pytest.mark.asyncio
async def test_admission_respects_the_per_sweep_cap_the_size_limit_and_the_deadline(database) -> None:
    names = [(f"N{index:02d}", 100 + index) for index in range(1, 61)]
    for symbol, con_id in names:
        await upsert_daily(qualified_row(symbol, con_id))
    clock = FakeClock(at(10, 0))
    source = FakeMarket(
        {},
        details={con_id: stock_details(con_id, symbol) for symbol, con_id in names},
        clock=clock,
        request_seconds=0.25,
    )
    sightings = [sighting(con_id, symbol, rank) for rank, (symbol, con_id) in enumerate(names, start=1)]

    capped = await admit(source, sightings, clock=clock, max_admissions=5)
    assert (capped.admitted, capped.deferred) == (5, 55)

    limited = await admit(source, sightings, clock=clock, max_size=8)
    assert (limited.admitted, limited.size, limited.deferred) == (3, 8, 52)

    # Each admission takes 0.25 s here; a name starts only with 1.5 s left.
    deadline = clock.monotonic() + 3.0
    timed = await admit(source, sightings, clock=clock, monotonic=clock.monotonic, deadline=deadline)
    assert timed.admitted == 7 and clock.monotonic() <= deadline
    assert len(await load_day_universe_members(DAY)) == 15


@pytest.mark.asyncio
async def test_transient_history_failures_are_retried_and_a_lost_connection_raises(database) -> None:
    source = FakeMarket(
        {},
        histories={60: DailyBarBatch(bars=(), pacing_failed=True, error="TWS error 162: pacing")},
        details={60: stock_details(60, "PACED"), 61: TWSConnectionError("TWS connection closed")},
    )

    report = await admit(source, [sighting(60, "PACED", 1)])
    assert (report.admitted, report.failed) == (0, 1)
    assert day_rows(database) == []

    with pytest.raises(TWSConnectionError):
        await admit(source, [sighting(61, "GONE", 1)])


# --- the sweep: the golden test (Principle 1) -------------------------------------------------

GOLDEN_SEED = 20261013
GOLDEN_WATCHLIST = [(f"W{index:02d}", index) for index in range(1, 61)]
GOLDEN_SCAN = [(f"S{index}", 500 + index) for index in range(1, 9)]
GOLDEN_DAY = [(f"D{index:02d}", 600 + index) for index in range(1, 41)]
GOLDEN_HELD = ("HELD", 700)
GOLDEN_OBSERVED = datetime(2026, 10, 12, 14, 48, tzinfo=UTC)
# sha256 of the ranking group's rows plus the market context (golden_digest)
# that the DEPLOYED sweep code (screener.py of 694cc1a, unchanged on
# autopilot/base 8467c4f) produces on these snapshots: computed by running that
# file itself, not this branch. Never update it to make a test pass: a new
# value means the real MOMO's inputs changed and needs its own review.
GOLDEN_RANKING_DIGEST = "fa5a721e5cd3047d3eb88c53d84bf2e674c6a7f0d67c3e723dc2be05a1c1d70c"


def golden_snapshots() -> dict[int, MarketSnapshot | Exception]:
    rng = random.Random(GOLDEN_SEED)
    observed = GOLDEN_OBSERVED.astimezone(ET)
    answers: dict[int, MarketSnapshot | Exception] = dict(etf_answers(observed))
    names = [*GOLDEN_WATCHLIST, *GOLDEN_SCAN, *GOLDEN_DAY, GOLDEN_HELD]
    for _symbol, con_id in names:
        prior = Decimal(rng.randint(1500, 25000)) / 100
        last = (prior * (1 + Decimal(rng.randint(-900, 1500)) / 10000)).quantize(Decimal("0.01"))
        high = max(last, prior) + Decimal(rng.randint(0, 300)) / 100
        low = min(last, prior) - Decimal(rng.randint(0, 300)) / 100
        answers[con_id] = MarketSnapshot(
            con_id=con_id,
            observed_at=observed,
            prior_close=prior,
            last=last,
            open=(prior * (1 + Decimal(rng.randint(-300, 600)) / 10000)).quantize(Decimal("0.01")),
            day_high=high,
            day_low=max(low, Decimal("1")),
            volume_raw=Decimal(rng.randint(100_000, 9_000_000)),
        )
    answers[7] = TWSRequestError("TWS market snapshot timed out without prior close")
    answers[611] = TWSRequestError("TWS market snapshot timed out without prior close")
    return answers


async def seed_golden_universe() -> None:
    rng = random.Random(GOLDEN_SEED + 1)
    await replace_universe_source(
        "watchlist", [universe_row(symbol, con_id) for symbol, con_id in GOLDEN_WATCHLIST]
    )
    for symbol, con_id in GOLDEN_WATCHLIST:
        await upsert_daily(qualified_row(symbol, con_id, avg=str(rng.randint(500_000, 20_000_000))))
    # Day-universe names were qualified earlier today (by their admission).
    for symbol, con_id in GOLDEN_DAY:
        await upsert_daily(qualified_row(symbol, con_id, avg=str(rng.randint(500_000, 20_000_000))))


def golden_source() -> FakeMarket:
    scans = {
        "HOT_BY_VOLUME": [scan_row(con_id, symbol, rank) for rank, (symbol, con_id) in enumerate(GOLDEN_SCAN, start=1)],
        "TOP_PERC_GAIN": [
            scan_row(con_id, symbol, rank) for rank, (symbol, con_id) in enumerate(GOLDEN_DAY, start=1)
        ],
    }
    histories = {con_id: history_batch(con_id) for _symbol, con_id in GOLDEN_SCAN}
    return FakeMarket(golden_snapshots(), scans, histories)  # type: ignore[arg-type]


def ranking_document(payload: ScreenerPayload, ranking_rows: int) -> str:
    document = {
        "rows": [row.model_dump(mode="json") for row in payload.rows[:ranking_rows]],
        "market": payload.market,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def golden_digest(payload: ScreenerPayload, ranking_rows: int) -> str:
    return sha256(ranking_document(payload, ranking_rows).encode()).hexdigest()


async def golden_sweep(source: FakeMarket, **options: Any) -> Any:
    return await run_sweep(
        source,
        session_date=DAY,
        clock=lambda: GOLDEN_OBSERVED,
        scan_merge=merge(source),
        context_etfs=ContextETFs(FakeResolver()),
        **options,
    )


@pytest.mark.asyncio
async def test_golden_the_hot_35_and_the_regime_ignore_day_universe_and_held_rows(database) -> None:
    await seed_golden_universe()
    without = await golden_sweep(golden_source(), day_universe=False, held=False)
    ranking_rows = len(without.payload.rows)

    insert_day_names(database, GOLDEN_DAY)
    insert_accepted_decision(database, con_id=GOLDEN_HELD[1], symbol=GOLDEN_HELD[0], strategy="MOMO_HOD_V2", suffix="held")
    insert_accepted_decision(database, con_id=3, symbol="W03", strategy="MOMO_HOD_V2", suffix="w03")
    insert_accepted_decision(database, con_id=701, symbol="ORBY", strategy="ORB_V1", suffix="orb")
    source = golden_source()
    with_extras = await golden_sweep(source, day_universe=True, held=True)

    before, after = without.payload, with_extras.payload
    # The ranking group: 59 quoted watchlist names (W07 failed) + 15 scan additions.
    assert ranking_rows == 74 and before.errors == 1
    assert sum(1 for row in before.rows if row.source == "ibkr_scan") == 15
    assert ranking_document(after, ranking_rows) == ranking_document(before, ranking_rows)
    assert hotlist_rows(after) == hotlist_rows(before)
    assert len(hotlist_rows(after)) == 35
    assert after.market == before.market and after.market is not None
    assert with_extras.market_context == without.market_context
    assert golden_digest(before, ranking_rows) == GOLDEN_RANKING_DIGEST
    assert golden_digest(after, ranking_rows) == GOLDEN_RANKING_DIGEST
    assert without.market_context is not None and without.market_context.regime == "risk_on"

    # Held: the MOMO decision outside the ranking group trails, unranked; W03
    # is already a ranked row; ORB's accepted decision is not sendable.
    trailing = after.rows[ranking_rows:]
    assert [(row.symbol, row.score, row.hot, row.source) for row in trailing] == [
        ("HELD", "0.0000", False, "ibkr_scan"),
    ]
    assert (after.qualified, after.quoted, after.errors) == (before.qualified + 1, before.quoted + 1, 1)

    # The day universe: its own list, every name quoted once (D11 failed).
    section = after.day_universe
    assert section is not None and before.day_universe is None
    assert (section.size, section.quoted, section.errors, section.complete) == (40, 39, 1, True)
    assert [row.rank for row in section.rows] == list(range(1, 40))
    assert {row.symbol for row in section.rows} == {symbol for symbol, _ in GOLDEN_DAY} - {"D11"}
    # D01..D07 were ranking-group scan additions too: one snapshot serves both lists.
    ranking_ids = {row.con_id for row in before.rows}
    assert {row.con_id for row in section.rows} & ranking_ids == {601, 602, 603, 604, 605, 606, 607}
    calls = [con_id for batch in source.batches for con_id in batch]
    assert len(calls) == len(set(calls))
    # After the whole ranking group: the held symbol, then the day universe
    # (D08..D40, best scan rank first, in chunks of 20).
    assert source.batches[-3:] == [
        [700],
        [600 + index for index in range(8, 28)],
        [600 + index for index in range(28, 41)],
    ]


class PersistWatch(FakeMarket):
    """Records what the database held when each quote-only batch was requested."""

    def __init__(self, *args: Any, watched: set[int], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.watched = watched
        self.seen: list[ScreenerPayload | None] = []

    async def market_snapshots(self, con_ids: Any, *, timeout_seconds: float = 3.0) -> Any:
        if set(con_ids) & self.watched:
            self.seen.append(await latest_snapshot(DAY))
        return await super().market_snapshots(con_ids, timeout_seconds=timeout_seconds)


@pytest.mark.asyncio
async def test_the_sweep_is_persisted_before_the_day_universe_is_quoted(database) -> None:
    """MOMO's readers see each sweep exactly when its ranking group is done."""
    await seed_golden_universe()
    insert_day_names(database, GOLDEN_DAY)
    template = golden_source()
    source = PersistWatch(
        template.answers, template.scans, template.histories, watched={608}
    )

    result = await golden_sweep(source, day_universe=True, held=False)

    (stored,) = source.seen
    assert stored is not None and stored.day_universe is None
    assert stored.rows == result.payload.rows and stored.market == result.payload.market
    # The list was attached to that same snapshot, leaving the rest byte-identical.
    with closing(sqlite3.connect(database)) as db:
        (text,) = db.execute(
            "SELECT payload_json FROM autotrader_screener_snapshots WHERE id = ?", (result.snapshot_id,)
        ).fetchone()
    assert text.startswith(stored.to_json()[:-1] + ',"day_universe":')
    assert ScreenerPayload.model_validate_json(text) == result.payload
    with pytest.raises(screener_module.ScreenerError):
        await screener_module.attach_day_universe(result.snapshot_id, result.payload, result.payload.day_universe)
    with pytest.raises(screener_module.ScreenerError, match="not this sweep"):
        await screener_module.attach_day_universe(result.snapshot_id, stored, result.payload.day_universe)


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
async def test_golden_holds_for_any_snapshots(database, monkeypatch, seed) -> None:
    monkeypatch.setattr(sys.modules[__name__], "GOLDEN_SEED", seed)
    await seed_golden_universe()
    without = await golden_sweep(golden_source(), day_universe=False, held=False)
    insert_day_names(database, GOLDEN_DAY)
    insert_accepted_decision(database, con_id=GOLDEN_HELD[1], symbol=GOLDEN_HELD[0], strategy="MOMO_HOD_V2", suffix="held")

    with_extras = await golden_sweep(golden_source(), day_universe=True, held=True)

    ranking_rows = len(without.payload.rows)
    assert ranking_document(with_extras.payload, ranking_rows) == ranking_document(without.payload, ranking_rows)
    assert hotlist_rows(with_extras.payload) == hotlist_rows(without.payload)
    assert with_extras.market_context == without.market_context


# --- the golden timeline: admission between sweeps never moves the hot-35 ------------------
#
# Five sweeps a minute apart. The scans list 45 non-watchlist names every
# sweep, rotated by 15 ranks each time, so the hot-35's own path (at most 15
# qualifications and 15 additions a sweep) keeps meeting names it has not
# qualified yet, while admission (up to 40 a sweep) has. Had admission's
# outcomes reached autotrader_universe_daily, the additions would differ from
# sweep 1 on (test_..._would_catch_a_coupled_admission shows it).

TIMELINE_SWEEPS = 5
TIMELINE_WATCHLIST = [(f"T{index:02d}", 300 + index) for index in range(1, 61)]
TIMELINE_SCAN = [(f"N{index:02d}", 800 + index) for index in range(1, 46)]
TIMELINE_START = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)  # 10:00 ET
# sha256 of every sweep's ranking-group rows and market context
# (timeline_digest) that the DEPLOYED sweep code (screener.py of 694cc1a)
# produces on this timeline, computed by running that file itself. Never
# update it to make a test pass.
TIMELINE_DIGEST = "c81c82c6248353511d76dc705fba881b836be815ad1f29b7e03b120352cc7c1e"


def timeline_observed(sweep: int) -> datetime:
    return TIMELINE_START + timedelta(minutes=sweep)


def timeline_scans(sweep: int) -> dict[str, list[Any]]:
    shift = (15 * sweep) % len(TIMELINE_SCAN)
    rotated = TIMELINE_SCAN[shift:] + TIMELINE_SCAN[:shift]
    return {
        "HOT_BY_VOLUME": [scan_row(con_id, symbol, rank) for rank, (symbol, con_id) in enumerate(rotated, start=1)],
        "TOP_PERC_GAIN": [
            scan_row(con_id, symbol, rank) for rank, (symbol, con_id) in enumerate(rotated[::-1][:12], start=1)
        ],
    }


def timeline_histories() -> dict[int, DailyBarBatch]:
    # Every fourth scan name trades above $300 and never qualifies.
    return {
        con_id: history_batch(con_id, close="400" if index % 4 == 0 else "50")
        for index, (_symbol, con_id) in enumerate(TIMELINE_SCAN, start=1)
    }


def timeline_snapshots(sweep: int) -> dict[int, MarketSnapshot | Exception]:
    rng = random.Random(GOLDEN_SEED * 100 + sweep)
    observed = timeline_observed(sweep).astimezone(ET)
    answers: dict[int, MarketSnapshot | Exception] = dict(etf_answers(observed))
    for _symbol, con_id in [*TIMELINE_WATCHLIST, *TIMELINE_SCAN]:
        prior = Decimal(rng.randint(1500, 25000)) / 100
        last = (prior * (1 + Decimal(rng.randint(-900, 1500)) / 10000)).quantize(Decimal("0.01"))
        answers[con_id] = MarketSnapshot(
            con_id=con_id,
            observed_at=observed,
            prior_close=prior,
            last=last,
            open=prior,
            day_high=max(last, prior) + Decimal(rng.randint(0, 300)) / 100,
            day_low=max(min(last, prior) - Decimal(rng.randint(0, 300)) / 100, Decimal("1")),
            volume_raw=Decimal(rng.randint(100_000, 9_000_000)),
        )
    return answers


async def seed_timeline_universe() -> None:
    rng = random.Random(GOLDEN_SEED + 2)
    await replace_universe_source(
        "watchlist", [universe_row(symbol, con_id) for symbol, con_id in TIMELINE_WATCHLIST]
    )
    for symbol, con_id in TIMELINE_WATCHLIST:
        await upsert_daily(qualified_row(symbol, con_id, avg=str(rng.randint(500_000, 20_000_000))))


def timeline_source(sweep: int) -> FakeMarket:
    details = {con_id: stock_details(con_id, symbol) for symbol, con_id in TIMELINE_SCAN}
    details[803] = stock_details(803, "N03", stock_type="ADR")  # the day universe refuses it
    return FakeMarket(timeline_snapshots(sweep), timeline_scans(sweep), timeline_histories(), details)  # type: ignore[arg-type]


def timeline_record(payload: Any, history_calls: list[int]) -> dict[str, Any]:
    """One sweep as the real MOMO sees it: ranking rows, hot-35, market, requests.

    The timeline has no held symbols, so every row is a ranking-group row.
    """
    rows = list(payload.rows)
    return {
        "additions": [row.con_id for row in rows if row.source == "ibkr_scan"],
        "hot": [(row.hot_rank, row.symbol, row.score) for row in rows if row.hot],
        "rows": [row.model_dump(mode="json") for row in rows],
        "market": payload.market,
        "history_requests": list(history_calls),
    }


def timeline_digest(records: list[dict[str, Any]]) -> str:
    document = [{"rows": record["rows"], "market": record["market"]} for record in records]
    return sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def run_timeline(
    *, admission: bool, coupled: bool = False, sweep_function: Any = None, context_class: Any = None
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    """Five sweeps; with ``admission`` the day universe admits names between them.

    ``coupled`` copies admission's outcomes into autotrader_universe_daily,
    as an admission writing that table would (the negative control).
    ``sweep_function``/``context_class`` let the deployed screener.py run the
    same timeline (its run_sweep has no day-universe options).
    """
    deployed = sweep_function is not None
    sweep_function = sweep_function or run_sweep
    etfs = (context_class or ContextETFs)(FakeResolver())
    # The hot-35's own pacer, kept across sweeps as the screener keeps it.
    legacy_pacer = RequestPacer(0)
    records: list[dict[str, Any]] = []
    admitted: list[list[int]] = []
    for sweep in range(TIMELINE_SWEEPS):
        source = timeline_source(sweep)
        options: dict[str, Any] = {} if deployed else {"day_universe": admission, "held": admission}
        result = await sweep_function(
            source,
            session_date=DAY,
            clock=lambda sweep=sweep: timeline_observed(sweep),
            scan_merge=production_scan_merge(source, legacy_pacer),
            context_etfs=etfs,
            **options,
        )
        records.append(timeline_record(result.payload, source.history_calls))
        if not admission:
            continue
        before = len(source.history_calls)
        report = await service.admit_day_universe(
            source,
            result,
            session_date=DAY,
            context_etfs=etfs,
            history_pacer=RequestPacer(0),
            details_pacer=RequestPacer(0),
            clock=lambda sweep=sweep: timeline_observed(sweep) + timedelta(seconds=40),
            monotonic=lambda: 0.0,
            deadline=None,
        )
        admitted.append(source.history_calls[before:])
        assert report.failed == 0
        if coupled:
            for row in DayUniverseDaily().load(DAY).values():
                await upsert_daily(row)
    return records, admitted


def production_scan_merge(source: FakeMarket, pacer: RequestPacer) -> Any:
    """The production scan merge (15 additions, 15 qualifications a sweep)."""
    from trading.market.screener import ScanMerge

    return ScanMerge(source=source, volume_multiplier=Decimal("100"), history_pacer=pacer)


async def _fresh_database(path: Path) -> None:
    from config import cfg
    from db.core import init_db

    path.parent.mkdir(parents=True, exist_ok=True)
    cfg.DB_PATH = str(path)
    await init_db()


@pytest.mark.asyncio
async def test_golden_timeline_legacy_additions_and_hot_35_ignore_admission_sweep_by_sweep(
    database, tmp_path
) -> None:
    """Principle 1, timing included: admission runs first, yet every sweep is the deployed one."""
    await seed_timeline_universe()
    with_admission, admission_requests = await run_timeline(admission=True)
    day_names = await load_day_universe(DAY)
    daily_with = {row.con_id: (row.qualified, row.reason) for row in await load_daily(DAY)}

    await _fresh_database(tmp_path / "baseline" / "screener.db")
    await seed_timeline_universe()
    baseline, _ = await run_timeline(admission=False)
    daily_without = {row.con_id: (row.qualified, row.reason) for row in await load_daily(DAY)}

    for sweep, (got, expected) in enumerate(zip(with_admission, baseline)):
        # The same additions, from the same legacy requests, at the same sweep.
        assert got["history_requests"] == expected["history_requests"], sweep
        assert got["additions"] == expected["additions"], sweep
        assert got["hot"] == expected["hot"], sweep
        assert got["rows"] == expected["rows"], sweep
        assert got["market"] == expected["market"], sweep
    assert all(len(record["hot"]) == 35 for record in baseline)
    # The hot-35's table holds exactly what its own path wrote.
    assert daily_with == daily_without
    assert timeline_digest(baseline) == TIMELINE_DIGEST
    assert timeline_digest(with_admission) == TIMELINE_DIGEST
    # Admission really ran ahead of the legacy path: after sweep 0 it qualified
    # 25 names the legacy path had not reached, with its own requests.
    assert [len(requests) for requests in admission_requests] == [25, 5, 0, 0, 0]
    assert not set(admission_requests[0]) & set(baseline[0]["history_requests"])
    assert len([name for name in day_names if name.qualified]) >= 30
    # N03 is an ADR: refused by the day universe, still a hot-35 scan addition.
    assert any(name.con_id == 803 and name.reason == "stock_type_adr" for name in day_names)
    assert 803 in with_admission[0]["additions"]


@pytest.mark.asyncio
async def test_golden_timeline_would_catch_a_coupled_admission(database, tmp_path) -> None:
    """Negative control: admission outcomes in the hot-35's table would move its additions."""
    await seed_timeline_universe()
    coupled, _ = await run_timeline(admission=True, coupled=True)

    await _fresh_database(tmp_path / "baseline" / "screener.db")
    await seed_timeline_universe()
    baseline, _ = await run_timeline(admission=False)

    assert coupled[0]["additions"] == baseline[0]["additions"]  # admission runs after sweep 0
    assert any(got["additions"] != expected["additions"] for got, expected in zip(coupled, baseline))
    assert timeline_digest(coupled) != timeline_digest(baseline)


def test_the_day_universe_ranks_on_its_own_percentiles() -> None:
    from tests.test_screener import observe

    watchlist = [observe(f"W{index}", index, snap(index, last=str(100 + index))) for index in range(1, 6)]
    names = [
        observe("UP", 61, snap(61, last="103")),
        observe("FLAT", 62, snap(62, last="100")),
        observe("DOWN", 63, snap(63, last="95")),
    ]

    alone = rank_day_universe(names)
    beside = rank_observations([*watchlist, *names])

    assert [(item.observation.instrument.symbol, item.hot_rank) for item in alone] == [
        ("UP", 1), ("FLAT", 2), ("DOWN", 3),
    ]
    # Its scores come from its own names only, never from the watchlist's.
    assert [item.score for item in alone] != [
        item.score for item in beside if item.observation.instrument.con_id > 60
    ]
    assert rank_day_universe(list(reversed(names))) == alone


# --- the sweep: held quotes, failures, compatibility -------------------------------------------


def _load_bridge() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts_local" / "paper_bridge.py"
    spec = importlib.util.spec_from_file_location("paper_bridge_screener_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_the_pinned_bridges_flatten_fallback_reads_held_rows(database) -> None:
    await seed_golden_universe()
    insert_accepted_decision(database, con_id=GOLDEN_HELD[1], symbol=GOLDEN_HELD[0], strategy="MOMO_HOD_V2", suffix="held")
    insert_day_names(database, GOLDEN_DAY)
    source = golden_source()

    result = await golden_sweep(source, day_universe=True)

    bridge = _load_bridge()
    held_answer = source.answers[700]
    assert isinstance(held_answer, MarketSnapshot)
    quote = bridge.screener_quote(database, DAY, "HELD", 700)
    assert quote is not None and quote.price == held_answer.last
    # A day-universe name outside rows is invisible to the bridge (it is never held).
    assert bridge.screener_quote(database, DAY, "D30", 630) is None
    # Ranked rows keep working as before.
    w01 = next(row for row in result.payload.rows if row.symbol == "W01")
    assert w01.last is not None and w01.bid is None
    assert bridge.screener_quote(database, DAY, "W01", 1).price == Decimal(w01.last)


@pytest.mark.asyncio
async def test_failing_extras_never_abort_or_change_the_ranking_group(database, monkeypatch) -> None:
    await seed_golden_universe()
    reference = await golden_sweep(golden_source(), day_universe=False, held=False)
    insert_day_names(database, GOLDEN_DAY)
    monkeypatch.setattr(screener_module, "MAX_CONSECUTIVE_SNAPSHOT_ERRORS", 3)
    source = golden_source()
    for _symbol, con_id in GOLDEN_DAY[10:]:
        source.answers[con_id] = TWSRequestError("TWS market snapshot timed out")

    result = await golden_sweep(source, day_universe=True, held=False)

    section = result.payload.day_universe
    assert section is not None and section.complete is False
    assert section.quoted + section.errors == section.size == 40
    rows = len(reference.payload.rows)
    assert ranking_document(result.payload, rows) == ranking_document(reference.payload, rows)
    with closing(sqlite3.connect(database)) as db:
        (stored,) = db.execute(
            "SELECT payload_json FROM autotrader_screener_snapshots WHERE id = ?", (result.snapshot_id,)
        ).fetchone()
    assert ScreenerPayload.model_validate_json(stored) == result.payload


class TimedMarket(FakeMarket):
    """Every snapshot batch takes ``batch_seconds`` on a manual monotonic clock."""

    def __init__(self, *args: Any, batch_seconds: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.batch_seconds = batch_seconds
        self.elapsed = 0.0

    def monotonic(self) -> float:
        return self.elapsed

    async def market_snapshots(self, con_ids: Any, *, timeout_seconds: float = 3.0) -> Any:
        self.elapsed += self.batch_seconds
        return await super().market_snapshots(con_ids, timeout_seconds=timeout_seconds)


@pytest.mark.asyncio
async def test_the_day_universe_pass_stops_at_the_next_sweeps_deadline_best_ranks_first(database) -> None:
    await seed_golden_universe()
    insert_day_names(database, GOLDEN_DAY)
    template = golden_source()
    source = TimedMarket(template.answers, template.scans, template.histories, batch_seconds=10.0)

    # ETFs + two ranking chunks take 30 s; a day-universe chunk may start until 35 s.
    result = await run_sweep(
        source,
        session_date=DAY,
        clock=lambda: GOLDEN_OBSERVED,
        monotonic=source.monotonic,
        scan_merge=merge(source),
        context_etfs=ContextETFs(FakeResolver()),
        day_universe=True,
        held=False,
        day_universe_deadline=35.0,
    )

    section = result.payload.day_universe
    assert section is not None and section.complete is False
    # D01..D07 came with the ranking group; one chunk of 20 more fit (D08..D27).
    assert {row.symbol for row in section.rows} == {f"D{index:02d}" for index in range(1, 28)} - {"D11"}
    assert section.quoted + section.errors == section.size == 40
    assert result.payload.sweep_seconds == 30.0 and section.seconds == 10.0
    assert len(hotlist_rows(result.payload)) == 35


@pytest.mark.asyncio
async def test_a_lost_connection_in_the_extras_pass_persists_the_ranking_group_then_raises(database) -> None:
    await seed_golden_universe()
    insert_day_names(database, GOLDEN_DAY)
    source = golden_source()
    source.answers[620] = TWSConnectionError("TWS connection closed")

    with pytest.raises(TWSConnectionError):
        await golden_sweep(source, day_universe=True)

    stored = await latest_snapshot(DAY)
    assert stored is not None and len(await latest_hotlist(DAY)) == 35
    assert stored.day_universe is not None and stored.day_universe.complete is False
    assert stored.day_universe.quoted + stored.day_universe.errors == 40


@pytest.mark.asyncio
async def test_a_broken_day_universe_list_is_left_out_and_the_ranking_group_still_persists(
    database, monkeypatch
) -> None:
    await seed_golden_universe()
    reference = await golden_sweep(golden_source(), day_universe=False, held=False)
    insert_day_names(database, [*GOLDEN_DAY[:3], ("SEVENTEENCHARSLONG", 690)])
    await upsert_daily(qualified_row("SEVENTEENCHARSLONG", 690))

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("day universe rows must be sorted by score, highest first")

    monkeypatch.setattr(screener_module, "rank_day_universe", broken)
    result = await golden_sweep(golden_source(), day_universe=True)

    assert result.payload.day_universe is None
    rows = len(reference.payload.rows)
    assert ranking_document(result.payload, rows) == ranking_document(reference.payload, rows)
    # A symbol over the row contract's 16 characters is never quoted at all.
    members = await screener_module.day_universe_instruments(DAY, frozenset())
    assert [item.con_id for item in members] == [601, 602, 603]


@pytest.mark.asyncio
async def test_a_sweep_without_the_day_universe_serializes_exactly_as_before(database) -> None:
    await seed_golden_universe()
    result = await golden_sweep(golden_source(), day_universe=False)

    document = json.loads(result.payload.to_json())
    assert "day_universe" not in document
    assert list(document) == [
        "schema", "session_date", "observed_at", "sweep_seconds", "universe_size",
        "qualified", "quoted", "errors", "rows", "market",
    ]


@pytest.mark.asyncio
async def test_latest_day_universe_reads_the_newest_sweeps_list(database) -> None:
    await seed_golden_universe()
    assert await latest_day_universe(DAY) is None
    insert_day_names(database, GOLDEN_DAY[:5])

    result = await golden_sweep(golden_source(), day_universe=True)

    found = await latest_day_universe(DAY)
    assert found is not None
    observed_at, section = found
    assert observed_at == GOLDEN_OBSERVED and section == result.payload.day_universe
    assert isinstance(section, DayUniverseSection) and section.size == 5


def test_the_section_contract_refuses_inconsistent_lists() -> None:
    row = {
        "rank": 1, "symbol": "D01", "con_id": 601, "primary_exchange": None, "last": "10.00",
        "bid": None, "ask": None, "prior_close": "9.00", "open": None, "day_high": None,
        "day_low": None, "change_pct": "11.11", "gap_pct": None, "volume_shares": None,
        "rvol": None, "dollar_volume": None, "pct_from_high": None, "score": "0.5000",
    }
    good = {"size": 2, "quoted": 1, "errors": 1, "complete": True, "seconds": 1.0, "rows": [row]}
    DayUniverseSection.model_validate(good)
    for broken in (
        {**good, "size": 3},
        {**good, "quoted": 2},
        {**good, "rows": [{**row, "rank": 2}]},
        {**good, "size": 151, "errors": 150},
        {**good, "rows": [row, {**row, "rank": 2, "score": "0.6000"}], "quoted": 2, "errors": 0},
    ):
        with pytest.raises(ValueError):
            DayUniverseSection.model_validate(broken)


@pytest.mark.asyncio
async def test_held_symbols_follow_the_bridges_sendable_prefix(database) -> None:
    insert_accepted_decision(database, con_id=700, symbol="HELD", strategy=" MOMO_HOD_V2 ", suffix="a")
    insert_accepted_decision(database, con_id=701, symbol="ORBY", strategy="ORB_V1", suffix="b")
    insert_accepted_decision(database, con_id=702, symbol="APMO", strategy="AP_MOMO_HOD_V1", suffix="c")

    held = await screener_module.held_instruments(DAY)

    assert [(item.symbol, item.con_id, item.primary_exchange, item.source) for item in held] == [
        ("HELD", 700, "NYSE", "ibkr_scan"),
    ]
    assert await screener_module.held_instruments(DAY + timedelta(days=1)) == ()


# --- the screener service: admission in the idle time, the prior-close re-check ---------------


class LoopActor(FakeMarket):
    """The service's actor surface, on a fake clock that each request advances."""

    def __init__(self, clock: FakeClock, **kwargs: Any) -> None:
        super().__init__(clock=clock, **kwargs)
        self.connected = False
        self.last_error: str | None = None
        self.sweep_starts: list[datetime] = []

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def contract_details_for_symbol(self, symbol: str, primary_exchange: str | None) -> Any:
        raise TWSRequestError("no context ETFs in this test")

    async def screener_scan(self, scan_code: str) -> Any:
        if scan_code == "HOT_BY_VOLUME":
            assert self.clock is not None
            self.sweep_starts.append(self.clock.now)
        return await super().screener_scan(scan_code)

    async def market_snapshots(self, con_ids: Any, *, timeout_seconds: float = 3.0) -> Any:
        assert self.clock is not None
        self.clock.now += timedelta(seconds=10)
        observed = self.clock.now
        return [snap(con_id, observed=observed) for con_id in con_ids]


@pytest.mark.asyncio
async def test_the_loop_admits_in_the_idle_time_and_never_delays_the_next_sweep(database, monkeypatch) -> None:
    # Pacing is simulated on the fake clock (each request takes 1 s there).
    monkeypatch.setattr(service, "DAILY_HISTORY_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(service, "CONTRACT_DETAILS_MIN_INTERVAL_SECONDS", 0.0)
    await replace_universe_source("watchlist", [universe_row("AAA", 1)])
    await upsert_daily(qualified_row("AAA", 1))
    clock = FakeClock(at(10, 0))
    names = [(f"N{index:02d}", 100 + index) for index in range(1, 61)]
    actor = LoopActor(
        clock,
        answers={},
        scans={"HOT_BY_VOLUME": [scan_row(con_id, symbol, rank) for rank, (symbol, con_id) in enumerate(names, start=1)]},
        histories={con_id: history_batch(con_id) for _symbol, con_id in names},
        details={con_id: stock_details(con_id, symbol) for symbol, con_id in names},
        request_seconds=1.0,
    )

    sweeps = await service._screener_loop(
        actor,
        interval_seconds=60,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_sweeps=3,
        include_scans=True,
        retention=False,
    )

    assert sweeps == 3
    # Sweeps start exactly one interval apart: admission filled the idle time only.
    starts = actor.sweep_starts
    assert [later - earlier for earlier, later in zip(starts, starts[1:])] == [timedelta(seconds=60)] * 2
    stored = await load_day_universe(DAY)
    assert 0 < len(stored) <= 2 * SCAN_ADDITIONS_PER_SWEEP
    assert all(name.qualified for name in stored)
    snapshot = await latest_snapshot(DAY)
    assert snapshot is not None and snapshot.day_universe is not None
    assert snapshot.day_universe.size > 0
    # The hot-35 group stays the watchlist plus at most 15 scan additions.
    assert len(snapshot.rows) <= 1 + 15


@pytest.mark.asyncio
async def test_the_first_sweep_requalifies_an_evening_row_whose_prior_close_changed(database) -> None:
    evening = datetime(2026, 10, 9, 21, 30, tzinfo=UTC)  # the session before, 17:30 ET
    await replace_universe_source("watchlist", [universe_row("SPLT", 1), universe_row("SAME", 2), universe_row("BIG", 3)])
    # SPLT and SAME are quoted by the sweep; BIG ($350, rejected) only by the re-check.
    await upsert_daily(qualified_row("SPLT", 1, prior="200", observed=evening))
    await upsert_daily(qualified_row("SAME", 2, prior="100", observed=evening))
    await upsert_daily(qualified_row("BIG", 3, prior="350", observed=evening, qualified=False))
    observed = at(8, 0)
    split_history = DailyBarBatch(
        bars=tuple(
            IBKRDailyBar(con_id=1, session_date=DAY - timedelta(days=offset), close=Decimal("100"), volume=Decimal("500000"))
            for offset in range(21, 0, -1)
        )
    )
    big_history = DailyBarBatch(
        bars=tuple(
            IBKRDailyBar(con_id=3, session_date=DAY - timedelta(days=offset), close=Decimal("175"), volume=Decimal("500000"))
            for offset in range(21, 0, -1)
        )
    )
    source = FakeMarket(
        {
            1: snap(1, prior="100", last="101", observed=observed),
            2: snap(2, prior="100.50", last="101", observed=observed),
            3: snap(3, prior="175", last="176", observed=observed),
        },
        histories={1: split_history, 3: big_history},
    )
    sweep = await run_sweep(source, session_date=DAY, clock=lambda: observed)

    report = await recheck_prior_closes(
        source,
        session_date=DAY,
        payload=sweep.payload,
        volume_multiplier=Decimal("100"),
        pacer=RequestPacer(0),
        clock=lambda: observed,
    )

    assert report.checked == 3
    assert report.requalified == ("BIG", "SPLT") and report.still_different == ()
    assert source.history_calls == [3, 1]
    # BIG was not a sweep row: one extra snapshot batch quoted it.
    assert source.batches == [[2, 1], [3]]
    stored = {row.con_id: row for row in await load_daily(DAY)}
    assert stored[1].prior_close == Decimal("100") and stored[1].observed_at == observed
    assert stored[3].qualified is True and stored[3].prior_close == Decimal("175")
    assert stored[2].observed_at == evening  # within 2%: untouched

    again = await recheck_prior_closes(
        source, session_date=DAY, payload=sweep.payload, volume_multiplier=Decimal("100"),
        pacer=RequestPacer(0), clock=lambda: observed,
    )
    assert again.checked == 1 and again.requalified == ()


@pytest.mark.asyncio
async def test_the_loop_rechecks_once_per_session(database, monkeypatch) -> None:
    calls: list[date] = []

    async def recheck(actor: Any, sweep: Any, *, session_date: date, **kwargs: Any) -> Any:
        calls.append(session_date)
        return screener_module.PriorCloseRecheck(checked=0)

    monkeypatch.setattr(service, "recheck_session_prior_closes", recheck)
    clock = FakeClock(at(10, 0))
    actor = LoopActor(clock, answers={})

    await service._screener_loop(
        actor, interval_seconds=60, clock=clock, sleep=clock.sleep, monotonic=clock.monotonic,
        max_sweeps=3, include_scans=True, retention=False,
    )

    assert calls == [DAY]


@pytest.mark.asyncio
async def test_a_proven_gap_scan_feeds_the_day_universe_only(database, monkeypatch) -> None:
    from trading.market import tws_actor

    monkeypatch.setattr(tws_actor, "PROVEN_GAP_SCAN_CODES", frozenset({"HIGH_OPEN_GAP"}))
    await replace_universe_source("watchlist", [universe_row("AAA", 1)])
    await upsert_daily(qualified_row("AAA", 1))
    await upsert_daily(qualified_row("GAPR", 90))
    source = FakeMarket(
        {1: snap(1), 90: snap(90)},
        scans={"HIGH_OPEN_GAP": [scan_row(90, "GAPR", 1)]},
        details={90: stock_details(90, "GAPR")},
    )
    etfs = ContextETFs(FakeResolver())

    sweep = await run_sweep(source, session_date=DAY, clock=lambda: NOW, scan_merge=merge(source), day_universe=True)
    report = await service.admit_day_universe(
        source, sweep, session_date=DAY, context_etfs=etfs, history_pacer=RequestPacer(0),
        details_pacer=RequestPacer(0), clock=lambda: NOW, monotonic=lambda: 0.0, deadline=None,
    )

    # The sweep ran only the three ranking-group scans; the gap scan ran after it.
    assert source.scan_calls == ["HOT_BY_VOLUME", "TOP_PERC_GAIN", "MOST_ACTIVE", "HIGH_OPEN_GAP"]
    assert [row.con_id for row in sweep.payload.rows] == [1]
    assert report.admitted == 1
    assert [name.sources for name in await load_day_universe(DAY)] == [("HIGH_OPEN_GAP",)]
