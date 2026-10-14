"""MOMO_HOD_V2: wider stops, an extension guard, and retryable skips.

The replays use real IBKR PAPER bars for 2026-10-12, the first live PAPER day
(tests/fixtures/momo, captured read-only from TWS). On that day V1 bought BF B
with an 8-cent (1x ATR) stop that the next bar took out for about -$325, and
Claude abstained on FIGS because its entry sat 6.6 ATR above VWAP.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.fakes.legacy_strategies import insert_legacy_shadow_strategy
from trading.contracts import (
    AIReview,
    Candidate,
    CompletedBar,
    DecisionOutcome,
    EngineMode,
    PortfolioSnapshot,
    QuoteSnapshot,
    RiskPolicy,
    RuleSetup,
    Run,
    ScannerRun,
    StrategyState,
    fingerprint_payload,
)
from trading.intelligence.prompts import (
    MOMO_REVIEW_V1,
    MOMO_REVIEW_V2,
    REVIEW_PROMPT_STRATEGY_KINDS,
    review_prompt_for,
)
from trading.market.ibkr_boundary import DailyBarBatch, IBKRDailyBar, liquidity_profile
from trading.orchestration import CandidateInput, RepositoryBackedShadowRunner, ShadowPipeline
from trading.orchestration.session_runner import (
    MOMO_STRATEGY_NAME,
    ensure_strategy,
    momo_strategy_spec,
)
from trading.risk.engine import size_and_evaluate_trade
from trading.rules.momo import (
    EXTENDED_ABOVE_VWAP,
    MOMO_RULES_VERSION,
    MOMO_STRATEGY_KIND,
    STOP_TOO_WIDE,
    MomoConfig,
    MomoResult,
    MomoStrategy,
    evaluate_momo,
)
from trading.rules.orb import NO_SIGNAL_YET, QUOTE_REQUIRED
from trading.rules.strategy import is_data_rejection

NEW_YORK = ZoneInfo("America/New_York")
FIXTURES = Path(__file__).parent / "fixtures" / "momo"
SESSION = date(2026, 10, 12)
PRIOR = date(2026, 10, 9)
BAR = timedelta(minutes=5)
TICK = Decimal("0.01")
# ibapi reports bar and daily volume in round lots of 100 shares.
VOLUME_MULTIPLIER = Decimal("100")
HASH = "e" * 64


def _et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=NEW_YORK).astimezone(
        timezone.utc
    )


@dataclass(frozen=True)
class _Replay:
    """One real fixture: bars, the production liquidity profile, the live quote."""

    symbol: str
    con_id: int
    bars: tuple[CompletedBar, ...]
    reference_price: Decimal
    average_dollar_volume: Decimal
    signal_end: datetime
    live: dict[str, Any]

    def candidate(self, scanner_run_id: str = "scanner-replay", rank: int = 1) -> Candidate:
        return Candidate(
            id=f"{scanner_run_id}-{self.con_id}",
            scanner_run_id=scanner_run_id,
            con_id=self.con_id,
            symbol=self.symbol,
            primary_exchange="NYSE",
            rank=rank,
            admission_status="admitted",
        )

    def bars_until(self, now: datetime) -> tuple[CompletedBar, ...]:
        return tuple(bar for bar in self.bars if bar.interval_end_utc <= now)

    def bar_ending(self, end: datetime) -> CompletedBar:
        return next(bar for bar in self.bars if bar.interval_end_utc == end)

    def quote(self, now: datetime) -> QuoteSnapshot:
        """The live entry quote at the live signal; elsewhere one tick over the close."""
        latest = self.bars_until(now)[-1]
        if latest.interval_end_utc == self.signal_end:
            bid, ask = (Decimal(self.live["replay_quote"][side]) for side in ("bid", "ask"))
        else:
            bid, ask = latest.close, latest.close + TICK
        return QuoteSnapshot(con_id=self.con_id, bid=bid, ask=ask, observed_at=now, min_tick=TICK)


def _load(name: str) -> _Replay:
    raw = json.loads((FIXTURES / f"{name}_2026-10-12.json").read_text())
    con_id = raw["con_id"]
    bars = tuple(
        CompletedBar(
            con_id=con_id,
            interval_start_utc=datetime.fromisoformat(item["interval_start_utc"]),
            interval_end_utc=datetime.fromisoformat(item["interval_end_utc"]),
            open=item["open"],
            high=item["high"],
            low=item["low"],
            close=item["close"],
            volume=item["volume"],
            rth=True,
            source="ibkr",
            data_mode="historical",
        )
        for item in raw["bars"]
    )
    daily = DailyBarBatch(
        bars=tuple(
            IBKRDailyBar(
                con_id=con_id,
                session_date=date.fromisoformat(item["session_date"]),
                close=Decimal(item["close"]),
                volume=Decimal(item["volume"]),
            )
            for item in raw["daily_bars"]
        )
    )
    # The same 20-session profile the live run seeded from IBKR daily bars.
    profile = liquidity_profile(daily, session_date=SESSION, volume_multiplier=VOLUME_MULTIPLIER)
    hour, minute = (int(part) for part in raw["live_context"]["signal_bar_end_et"].split(":"))
    return _Replay(
        symbol=raw["symbol"],
        con_id=con_id,
        bars=bars,
        reference_price=profile.reference_price,
        average_dollar_volume=profile.average_dollar_volume,
        signal_end=_et(SESSION, hour, minute),
        live=raw["live_context"],
    )


BF_B = _load("bf_b")
FIGS = _load("figs")


def _evaluate(
    replay: _Replay,
    now: datetime,
    *,
    quote: QuoteSnapshot | None = None,
    config: MomoConfig = MomoConfig(),
) -> MomoResult:
    return evaluate_momo(
        candidate=replay.candidate(),
        bars=replay.bars_until(now),
        quote=quote,
        session_date=SESSION,
        run_id="run-replay",
        strategy_id="strategy-replay",
        strategy_version=1,
        reference_price=replay.reference_price,
        average_dollar_volume=replay.average_dollar_volume,
        now=now,
        config=config,
    )


def _proposal(setup: RuleSetup) -> AIReview:
    return AIReview(
        setup_fingerprint=setup.fingerprint(),
        decision="PROPOSE",
        confidence="0.7",
        rationale="Replay review.",
        model_id="test-model",
        prompt_version="momo-review-v2",
        response_hash=HASH,
        latency_ms=1,
        cost_usd="0",
    )


def test_fixtures_are_complete_real_sessions_with_the_live_liquidity_inputs():
    for replay in (BF_B, FIGS):
        days = [bar.interval_start_utc.astimezone(NEW_YORK).date() for bar in replay.bars]
        # Friday's full session, then Monday through the 15:15-15:20 ET bar.
        assert days.count(PRIOR) == 78 and days.count(SESSION) == 70
        assert all(
            later.interval_start_utc - earlier.interval_start_utc == BAR
            for earlier, later in zip(replay.bars, replay.bars[1:])
            if later.interval_start_utc.date() == earlier.interval_start_utc.date()
        )
    assert (BF_B.symbol, BF_B.con_id, BF_B.reference_price) == ("BF B", 4931, Decimal("26.39"))
    assert (FIGS.symbol, FIGS.con_id, FIGS.reference_price) == ("FIGS", 493581450, Decimal("15.78"))


def test_bf_b_v2_stop_sits_below_the_signal_bar_and_survives_the_real_session():
    now = BF_B.signal_end + timedelta(seconds=5)  # the 11:50:05 ET poll
    unquoted = _evaluate(BF_B, now)
    assert unquoted.rejection_reason == QUOTE_REQUIRED and unquoted.retryable is True
    signal = unquoted.evaluated_bar
    assert signal is not None and signal == BF_B.bar_ending(BF_B.signal_end)
    assert (signal.low, signal.close) == (Decimal("26.52"), Decimal("26.76"))

    result = _evaluate(BF_B, now, quote=BF_B.quote(now))
    setup = result.setup
    assert setup is not None and setup.strategy_kind == MOMO_STRATEGY_KIND
    # The same inputs the live run logged at 11:50 ET.
    assert result.vwap is not None and result.atr is not None
    assert result.vwap.quantize(Decimal("0.0001")) == Decimal(BF_B.live["vwap"])
    assert result.atr.quantize(Decimal("0.0001")) == Decimal(BF_B.live["atr"])
    assert (result.vwap.quantize(Decimal("0.0001")), result.atr.quantize(Decimal("0.0001"))) == (
        Decimal("26.5586"),
        Decimal("0.0792"),
    )
    assert setup.entry_limit == Decimal(BF_B.live["entry_limit"]) == Decimal("26.77")

    # Stop distance = max(1.5 x 0.0792 = 0.119, 0.6% x 26.77 = 0.161,
    # 26.77 - 26.52 + 0.01 = 0.26): the signal bar's low binds.
    assert setup.stop_price == Decimal("26.51")
    assert setup.target_price == Decimal("27.29")
    assert setup.stop_price <= setup.entry_limit * (Decimal("1") - Decimal("0.006"))
    assert setup.stop_price < signal.low
    assert setup.entry_limit - setup.stop_price <= setup.entry_limit * Decimal("0.015")
    assert setup.levels["stop_distance"] == Decimal("0.26")
    assert setup.levels["vwap_extension_atr"] == Decimal("2.67")
    assert setup.levels["vwap"] == Decimal("26.5586")
    assert setup.levels["rvol"] == Decimal("7.21")
    assert setup.levels["prior_day_high"] == Decimal("26.58")

    # What actually happened next: the very next bar traded through the V1
    # stop, while nothing through 15:20 ET came near the V2 stop.
    after = [bar for bar in BF_B.bars if bar.interval_start_utc >= BF_B.signal_end]
    assert after[0].low == Decimal("26.64") < Decimal(BF_B.live["v1_stop"])
    assert min(bar.low for bar in after) == Decimal("26.59") > setup.stop_price
    assert max(bar.high for bar in after) < setup.target_price


@pytest.mark.parametrize(
    ("net_liquidation", "quantity", "risk"),
    [
        # The requested $665k book: 15% notional cap = $99,750 -> 3,726 shares.
        ("665000", 3726, Decimal("968.76")),
        # The live fill of 3,741 shares implies a book of about $667.7k.
        ("667700", 3741, Decimal("972.66")),
    ],
)
def test_bf_b_size_is_bound_by_the_notional_cap_so_v2_raises_dollar_risk(
    net_liquidation, quantity, risk
):
    now = BF_B.signal_end + timedelta(seconds=5)
    setup = _evaluate(BF_B, now, quote=BF_B.quote(now)).setup
    assert setup is not None
    policy = RiskPolicy()
    book = Decimal(net_liquidation)
    approval = size_and_evaluate_trade(
        policy=policy,
        portfolio=PortfolioSnapshot(observed_at=now, net_liquidation=book),
        setup=setup,
        review=_proposal(setup),
        admitted_symbols=frozenset({setup.symbol}),
        now=now,
    )
    assert approval.approved is True and approval.codes == ()
    intent = approval.intent
    assert intent is not None and intent.quantity == quantity

    unit_risk = setup.entry_limit - setup.stop_price
    notional_budget = book * policy.max_position_notional_pct / 100
    risk_budget = book * policy.max_risk_per_trade_pct / 100
    notional_cap = (notional_budget / setup.entry_limit).to_integral_value(rounding=ROUND_DOWN)
    risk_cap = (risk_budget / unit_risk).to_integral_value(rounding=ROUND_DOWN)
    # The 15% notional cap binds; the 0.5% risk budget would allow ~12.8k shares.
    assert intent.quantity == notional_cap < risk_cap
    assert intent.risk_amount == risk == quantity * Decimal("0.26")
    assert intent.risk_amount < risk_budget
    assert intent.reward_to_risk == Decimal("2")


def test_figs_signal_is_skipped_as_extended_above_vwap_and_stays_retryable():
    now = FIGS.signal_end + timedelta(seconds=5)  # the 14:30:05 ET poll
    quote = FIGS.quote(now)
    assert quote.ask == Decimal(FIGS.live["entry_limit"]) == Decimal("17.83")
    result = _evaluate(FIGS, now, quote=quote)
    assert result.setup is None
    assert result.rejection_reason == EXTENDED_ABOVE_VWAP
    assert result.retryable is True and not is_data_rejection(EXTENDED_ABOVE_VWAP)
    assert result.vwap is not None and result.atr is not None
    assert result.vwap.quantize(Decimal("0.0001")) == Decimal(FIGS.live["vwap"])
    assert result.atr.quantize(Decimal("0.001")) == Decimal(FIGS.live["atr"])
    extension = (quote.ask - result.vwap) / result.atr
    assert extension.quantize(Decimal("0.01")) == Decimal("6.57")

    strategy_result = MomoStrategy().evaluate(
        candidate=FIGS.candidate(),
        bars=FIGS.bars_until(now),
        quote=quote,
        session_date=SESSION,
        run_id="run-replay",
        strategy_id="strategy-replay",
        strategy_version=1,
        reference_price=FIGS.reference_price,
        average_dollar_volume=FIGS.average_dollar_volume,
        candidate_input_fingerprint=None,
        now=now,
    )
    assert strategy_result.setup is None
    assert strategy_result.rejection_reason == EXTENDED_ABOVE_VWAP
    assert strategy_result.retryable is True

    # The guard compares the unrounded extension, and it alone blocks FIGS:
    # with a looser guard the V2 stop (one tick under the 17.69 low) prices.
    def guarded_at(limit: str) -> MomoResult:
        config = MomoConfig(max_vwap_extension_atr=Decimal(limit))
        return _evaluate(FIGS, now, quote=quote, config=config)

    assert guarded_at("6.56").rejection_reason == EXTENDED_ABOVE_VWAP
    loose = guarded_at("6.57").setup
    assert loose is not None
    assert (loose.stop_price, loose.target_price) == (Decimal("17.68"), Decimal("18.13"))

    # FIGS' earlier 13:25 ET breakout, priced one tick over its 17.59 close,
    # was already 4.77 ATR above VWAP.
    earlier = _et(SESSION, 13, 25, 5)
    early_quote = FIGS.quote(earlier)
    early = _evaluate(FIGS, earlier, quote=early_quote)
    assert early.rejection_reason == EXTENDED_ABOVE_VWAP and early.retryable is True
    assert early.vwap is not None and early.atr is not None
    early_extension = (early_quote.ask - early.vwap) / early.atr
    assert early_extension.quantize(Decimal("0.01")) == Decimal("4.77")


def _synthetic_bars(*, quiet_half_range: str, signal: dict[str, str]) -> list[CompletedBar]:
    """A quiet prior session and twelve quiet bars, then a 10:30-10:35 ET breakout."""
    half = Decimal(quiet_half_range)
    quiet = {
        "open": "100",
        "high": str(Decimal("100") + half),
        "low": str(Decimal("100") - half),
        "close": "100",
        "volume": "1000",
    }

    def bar(start: datetime, shape: dict[str, str]) -> CompletedBar:
        return CompletedBar(
            con_id=4242,
            interval_start_utc=start,
            interval_end_utc=start + BAR,
            rth=True,
            source="ibkr",
            data_mode="historical",
            **shape,
        )

    prior = [bar(_et(PRIOR, 9, 30) + BAR * index, quiet) for index in range(78)]
    today = [bar(_et(SESSION, 9, 30) + BAR * index, quiet) for index in range(12)]
    breakout = {"open": "100.2", "high": "101", "close": "101", "volume": "2000", **signal}
    return [*prior, *today, bar(_et(SESSION, 10, 30), breakout)]


def _evaluate_synthetic(bars: list[CompletedBar], **overrides: Any) -> MomoResult:
    now = bars[-1].interval_end_utc + timedelta(seconds=5)
    params: dict[str, Any] = {
        "candidate": Candidate(
            id="candidate-synthetic",
            scanner_run_id="scanner-synthetic",
            con_id=4242,
            symbol="SYN",
            primary_exchange="NYSE",
            rank=1,
            admission_status="admitted",
        ),
        "bars": bars,
        "quote": QuoteSnapshot(
            con_id=4242, bid="100.99", ask="101.00", observed_at=now, min_tick=TICK
        ),
        "session_date": SESSION,
        "run_id": "run-synthetic",
        "strategy_id": "strategy-synthetic",
        "strategy_version": 1,
        "reference_price": "100",
        "average_dollar_volume": "25000000",
        "now": now,
    }
    params.update(overrides)
    return evaluate_momo(**params)


def test_a_stop_wider_than_one_and_a_half_percent_is_a_retryable_skip():
    # ATR(14) = 0.57, so the signal bar's low sets the distance: entry 101
    # minus the low plus one tick, against a cap of 1.5% x 101 = 1.515.
    def low_at(low: str) -> list[CompletedBar]:
        return _synthetic_bars(quiet_half_range="0.25", signal={"low": low})

    setup = _evaluate_synthetic(low_at("99.50")).setup
    assert setup is not None and setup.levels["stop_distance"] == Decimal("1.51")
    assert (setup.stop_price, setup.target_price) == (Decimal("99.49"), Decimal("104.02"))

    too_wide = _evaluate_synthetic(low_at("99.49"))
    assert too_wide.setup is None
    assert too_wide.rejection_reason == STOP_TOO_WIDE
    assert too_wide.retryable is True and not is_data_rejection(STOP_TOO_WIDE)
    assert too_wide.atr is not None and too_wide.evaluated_bar is not None

    tighter_cap = _evaluate_synthetic(
        low_at("99.50"), config=MomoConfig(max_stop_pct=Decimal("1.49"))
    )
    assert tighter_cap.rejection_reason == STOP_TOO_WIDE and tighter_cap.retryable is True


def test_the_percentage_floor_binds_when_atr_and_the_signal_bar_are_tight():
    # ATR(14) = 0.257 (1.5x = 0.386) and the signal low is 0.21 away, so the
    # 0.6% floor (0.606) sets the stop: 101 - 0.606 rounds down to 100.39.
    bars = _synthetic_bars(quiet_half_range="0.1", signal={"open": "100.85", "low": "100.8"})
    result = _evaluate_synthetic(bars)
    setup = result.setup
    assert setup is not None and result.atr is not None
    assert result.atr * Decimal("1.5") < Decimal("101") * Decimal("0.006")
    assert setup.stop_price == Decimal("100.39")
    assert setup.target_price == Decimal("102.22")
    assert setup.levels["stop_distance"] == Decimal("0.61")


@pytest.mark.anyio
async def test_retryable_skips_stay_pending_in_the_leased_session_until_the_cutoff(
    tmp_path, monkeypatch
):
    """Replay the real day bar by bar, the way the session runner polls."""
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "momo-v2.db"))
    await init_db()
    # Yesterday's catalogue still holds MOMO_HOD_V1 (written before the name
    # guard, which now refuses it); V2 bootstraps its own record.
    v1 = await insert_legacy_shadow_strategy(
        owner_id="owner-a", spec=momo_strategy_spec().model_copy(update={"name": "MOMO_HOD_V1"})
    )
    strategy = await ensure_strategy(owner_id="owner-a", spec=momo_strategy_spec())
    assert strategy.spec.name == MOMO_STRATEGY_NAME == "MOMO_HOD_V2"
    assert strategy.state == StrategyState.SHADOW and strategy.strategy_id != v1.strategy_id

    run = Run(
        id="run-momo-v2-replay",
        owner_id="owner-a",
        source_sha="abcdef1",
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        policy_fingerprints={
            "risk": RiskPolicy().fingerprint(),
            "momo_hod": MomoConfig().fingerprint(),
        },
        session_date=SESSION,
        mode=EngineMode.SHADOW,
    )
    parameters = {"source": "screener_hotlist", "top": 35}
    scanner = ScannerRun(
        id=f"{run.id}-scanner",
        run_id=run.id,
        parameters=parameters,
        parameters_fingerprint=fingerprint_payload(parameters),
        status="completed",
        started_at=_et(SESSION, 9, 55),
    )
    replays = {replay.symbol: replay for replay in (BF_B, FIGS)}
    candidates = {
        symbol: replay.candidate(scanner.id, rank)
        for rank, (symbol, replay) in enumerate(replays.items(), 1)
    }

    class Provider:
        def __init__(self) -> None:
            self.setups: list[RuleSetup] = []

        async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
            self.setups.append(setup)
            return _proposal(setup)

    provider = Provider()
    clock = {"now": _et(SESSION, 10, 0, 5)}
    runner = RepositoryBackedShadowRunner(
        pipeline=ShadowPipeline(proposal_provider=provider, strategy=MomoStrategy()),
        clock=lambda: clock["now"],
        lease_ttl=timedelta(hours=6),
        lease_renew_interval=timedelta(hours=1),
    )
    portfolio = PortfolioSnapshot(observed_at=clock["now"], net_liquidation="665000")
    pending: dict[str, list[tuple[str, str | None]]] = {symbol: [] for symbol in replays}

    def item(symbol: str, now: datetime, *, quoted: bool = False) -> CandidateInput:
        replay = replays[symbol]
        return CandidateInput(
            candidate=candidates[symbol],
            bars=replay.bars_until(now),
            quote=replay.quote(now) if quoted else None,
            reference_price=replay.reference_price,
            average_dollar_volume=replay.average_dollar_volume,
        )

    async with runner.open_session(
        run=run,
        scanner_run=scanner,
        strategy=strategy,
        candidates=tuple(candidates.values()),
        policy=RiskPolicy(),
        holder_id="replay-momo-v2",
    ) as session:
        assert session.cutoff == _et(SESSION, 15, 0)
        poll = _et(SESSION, 10, 0, 5)
        latest: dict[str, CandidateInput] = {}
        while poll < session.cutoff:
            clock["now"] = poll
            for candidate in session.pending():
                symbol = candidate.symbol
                latest[symbol] = item(symbol, poll)
                assessment = await session.assess(latest[symbol], portfolio=portfolio, now=poll)
                if assessment.needs_quote:
                    assessment = await session.assess(
                        item(symbol, poll, quoted=True), portfolio=portfolio, now=poll
                    )
                if assessment.decision is None:
                    stamp = poll.astimezone(NEW_YORK).strftime("%H:%M")
                    pending[symbol].append((stamp, assessment.pending_reason))
            poll += BAR

        # Every FIGS skip was retryable, so it was still pending at the cutoff.
        assert [candidate.symbol for candidate in session.pending()] == ["FIGS"]
        clock["now"] = session.cutoff
        final = await session.finalize(
            [latest["FIGS"]], portfolio=portfolio, now=session.cutoff
        )

    figs_skips = [entry for entry in pending["FIGS"] if entry[1] != NO_SIGNAL_YET]
    assert figs_skips == [("13:25", EXTENDED_ABOVE_VWAP), ("14:30", EXTENDED_ABOVE_VWAP)]
    assert final[candidates["FIGS"].id].outcome == DecisionOutcome.RULE_REJECTED
    assert final[candidates["FIGS"].id].ai_review is None

    bf_b = final[candidates["BF B"].id]
    assert bf_b.outcome == DecisionOutcome.SHADOW_ACCEPTED
    assert {reason for _, reason in pending["BF B"]} == {NO_SIGNAL_YET}
    assert pending["BF B"][-1][0] == "11:45"
    assert [setup.symbol for setup in provider.setups] == ["BF B"]
    assert bf_b.risk is not None and bf_b.risk.intent is not None
    assert (bf_b.risk.intent.quantity, bf_b.risk.intent.stop_price) == (3726, Decimal("26.51"))


class _ReplayMarket:
    """The session runner's data for BF B and FIGS: their real bars and quotes."""

    def __init__(self, clock: dict[str, datetime]) -> None:
        self.clock = clock
        self.bar_requests: list[tuple[str, datetime]] = []
        self.by_con_id = {replay.con_id: replay for replay in (BF_B, FIGS)}

    async def completed_bars(self, *, con_id: int, now: datetime) -> tuple[CompletedBar, ...]:
        replay = self.by_con_id[con_id]
        self.bar_requests.append((replay.symbol, self.clock["now"]))
        return replay.bars_until(self.clock["now"])

    async def quote(self, *, con_id: int) -> QuoteSnapshot:
        return self.by_con_id[con_id].quote(self.clock["now"])

    async def liquidity(self, *, con_id: int, session_date: date) -> Any:
        raise AssertionError("the hot list seeds liquidity")

    async def scanner_snapshot(self, *, scanner_run_id: str) -> Any:
        raise AssertionError("MOMO takes the hot list, not a scan")


def _decision_projection(decision: dict[str, Any], symbols: dict[str, str]) -> tuple[Any, ...]:
    """Everything a decision decides; timestamps, ids and quote-time fingerprints aside."""
    review = decision["ai_review"] or {}
    risk = decision["risk"] or {}
    intent = risk.get("intent") or {}
    return (
        symbols[decision["candidate_id"]],
        decision["outcome"],
        review.get("decision"),
        risk.get("approved"),
        tuple(risk.get("codes") or ()),
        tuple(intent.get(key) for key in ("quantity", "entry_limit", "stop_price", "target_price")),
    )


def _setup_projection(setup: RuleSetup) -> tuple[Any, ...]:
    provenance = setup.causal_provenance
    return (
        setup.symbol,
        setup.entry_limit,
        setup.stop_price,
        setup.target_price,
        setup.atr,
        tuple(sorted(setup.levels.items())),
        setup.detected_at,
        provenance.breakout_bar_fingerprint if provenance else None,
        provenance.atr_bar_fingerprints if provenance else None,
    )


@pytest.mark.anyio
async def test_v2_decides_the_same_at_the_old_and_the_new_poll_slot(tmp_path, monkeypatch):
    """Golden: MOMO's slot moved from 5 s to 15 s after each close; identical
    bars still give identical setups, reviews, sizes and outcomes."""
    from config import cfg
    from db.core import init_db
    from trading.contracts import AccountSnapshot
    from trading.market.ibkr_boundary import LiquidityProfile
    from trading.operations.shadow_service import (
        WATCHLIST_BAR_MAX_AGE,
        poll_schedule,
        strategy_entry_window,
    )
    from trading.orchestration.session_runner import PollSchedule, ShadowSessionRunner
    from trading.scheduler.sessions import market_session
    from trading.storage.repository import list_decisions

    window_start = strategy_entry_window("momo", SESSION)[0]

    async def day(label: str, schedule: PollSchedule):
        # One ledger per day: the shadow lease admits one run per strategy and date.
        monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / f"momo-golden-{label}.db"))
        await init_db()
        strategy = await ensure_strategy(owner_id="owner-a", spec=momo_strategy_spec())
        clock = {"now": _et(SESSION, 9, 50)}
        market = _ReplayMarket(clock)
        setups: list[RuleSetup] = []

        class Provider:
            async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
                setups.append(setup)
                return _proposal(setup)

        async def sleep(seconds: float) -> None:
            clock["now"] += timedelta(seconds=seconds)

        async def read_account() -> AccountSnapshot:
            return AccountSnapshot(
                paper_account_id="DU1234567",
                client_id=20,
                observed_at=clock["now"],
                net_liquidation=Decimal("665000"),
                available_funds=Decimal("665000"),
                realized_day_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
            )

        async def hot_list(run: Run):
            parameters: dict[str, object] = {"source": "screener_hotlist", "top": 35}
            scanner = ScannerRun(
                id=f"{run.id}-hotlist",
                run_id=run.id,
                parameters=parameters,
                parameters_fingerprint=fingerprint_payload(parameters),
                status="completed",
                started_at=clock["now"],
            )
            candidates = tuple(
                replay.candidate(scanner.id, rank) for rank, replay in enumerate((BF_B, FIGS), 1)
            )
            liquidity = {
                replay.con_id: LiquidityProfile(
                    con_id=replay.con_id,
                    reference_price=replay.reference_price,
                    average_dollar_volume=replay.average_dollar_volume,
                    sessions_used=20,
                    last_session_date=PRIOR,
                )
                for replay in (BF_B, FIGS)
            }
            return scanner, candidates, liquidity

        runner = ShadowSessionRunner(
            market=market,
            read_account=read_account,
            runner=RepositoryBackedShadowRunner(
                pipeline=ShadowPipeline(proposal_provider=Provider(), strategy=MomoStrategy()),
                clock=lambda: clock["now"],
                lease_ttl=timedelta(hours=6),
                lease_renew_interval=timedelta(hours=1),
            ),
            clock=lambda: clock["now"],
            poll_schedule=schedule,
            sleep=sleep,
        )
        report = await runner.run(
            session=market_session(SESSION),
            strategy=strategy,
            policy=RiskPolicy(),
            source_sha="abcdef1",
            policy_fingerprints={
                "risk": RiskPolicy().fingerprint(),
                "momo_hod": MomoConfig().fingerprint(),
            },
            run_id=f"momo-golden-{label}",
            candidate_source=hot_list,
            window_start=window_start,
        )
        symbols = {replay.candidate(f"momo-golden-{label}-hotlist").id: replay.symbol for replay in (BF_B, FIGS)}
        stored = await list_decisions(owner_id="owner-a", run_id=report.run_id)
        decisions = sorted(_decision_projection(item, symbols) for item in stored)
        return report, market, decisions, [_setup_projection(setup) for setup in setups]

    # The slot V2 ran with until this change, under the same freshness limit.
    old = await day("5s", PollSchedule(offset=timedelta(seconds=5), bar_max_age=WATCHLIST_BAR_MAX_AGE))
    new = await day("15s", poll_schedule("momo", "watchlist"))

    old_report, old_market, old_decisions, old_setups = old
    new_report, new_market, new_decisions, new_setups = new
    assert new_decisions == old_decisions
    assert new_setups == old_setups
    assert (new_report.polls, new_report.data_errors) == (old_report.polls, old_report.data_errors) == (60, 0)
    assert dict(new_report.outcomes) == dict(old_report.outcomes)
    # Only the moment moved: every request comes 10 s later in the same bar.
    assert [(symbol, at - timedelta(seconds=10)) for symbol, at in new_market.bar_requests] == (
        old_market.bar_requests
    )
    assert old_market.bar_requests[0] == ("BF B", _et(SESSION, 10, 0, 5))
    # And it is V2's known day: BF B bought at 11:50 with the wide stop, FIGS skipped.
    assert new_decisions == [
        ("BF B", "shadow_accepted", "PROPOSE", True, (), (3726, "26.77", "26.51", "27.29")),
        ("FIGS", "rule_rejected", None, None, (), (None, None, None, None)),
    ]
    assert [setup[0] for setup in new_setups] == ["BF B"]
    assert new_setups[0][6] == BF_B.signal_end


def test_v2_identity_is_consistent_across_rules_catalogue_and_prompt():
    config = MomoConfig()
    assert (
        config.stop_atr_multiple,
        config.min_stop_pct,
        config.max_stop_pct,
        config.max_vwap_extension_atr,
    ) == (Decimal("1.5"), Decimal("0.6"), Decimal("1.5"), Decimal("4.5"))
    assert MOMO_RULES_VERSION == MOMO_STRATEGY_NAME == momo_strategy_spec().name == "MOMO_HOD_V2"
    rationale = momo_strategy_spec().rationale
    assert all(term in rationale for term in ("1.5x ATR(14)", "0.6%", "1.5%", "4.5 ATR"))

    # Each V2 parameter is part of the run's rule fingerprint.
    baseline = config.fingerprint()
    for name, value in (
        ("stop_atr_multiple", "1.6"),
        ("min_stop_pct", "0.5"),
        ("max_stop_pct", "1.4"),
        ("max_vwap_extension_atr", "5"),
    ):
        assert MomoConfig(**{name: Decimal(value)}).fingerprint() != baseline

    assert REVIEW_PROMPT_STRATEGY_KINDS["momo-review-v2"] == MOMO_STRATEGY_KIND
    assert review_prompt_for("momo-review-v2", strategy_kind=MOMO_STRATEGY_KIND) == MOMO_REVIEW_V2
    for term in (
        "1.5 times ATR(14)",
        "0.6% of the entry",
        "one tick below the signal bar's low",
        "1.5% of the entry",
        "4.5 ATR above vwap",
        "stop_distance",
        "vwap_extension_atr",
        "PROPOSE",
        "ABSTAIN",
        "When the evidence is ambiguous, ABSTAIN.",
        "You cannot change any price, quantity, symbol, or order field.",
        # The review sees the market, and leaves the risk-off block to code.
        "market_context",
        "SPY, QQQ, IWM and DIA",
        "a regime of risk_on, neutral or risk_off",
        "when the setup is marginal, ABSTAIN.",
        "Deterministic code already blocks new entries while the regime is risk_off.",
        'When market_context is {"available": false}, judge the setup on its own.',
    ):
        assert term in MOMO_REVIEW_V2
    assert "market_context" not in MOMO_REVIEW_V1
    # Recorded reviews name momo-review-v1; its text must never change.
    assert (
        sha256(MOMO_REVIEW_V1.encode("utf-8")).hexdigest()
        == "0169bbdf90d5d2c4c577c8c8ca82505a93b7f0c5abdf416a5cc4394dc811feca"
    )
    # momo-review-v2 (with market context) goes live on 2026-10-13; from then
    # on new wording needs a new prompt id, never an edit of this text.
    assert (
        sha256(MOMO_REVIEW_V2.encode("utf-8")).hexdigest()
        == "265260540822fd6f749162af9118fb0d6f978a7883fcb3dcedd9b5e7f7516f59"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_stop_pct": Decimal("1.5")},
        {"min_stop_pct": Decimal("2")},
        {"min_stop_pct": Decimal("-0.1")},
        {"max_stop_pct": Decimal("100")},
        {"max_vwap_extension_atr": Decimal("0")},
        {"stop_atr_multiple": Decimal("0")},
    ],
)
def test_invalid_v2_stop_parameters_are_rejected(overrides):
    with pytest.raises(ValueError):
        MomoConfig(**overrides)
