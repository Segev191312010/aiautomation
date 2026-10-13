"""Golden: the size multiplier's default leaves MOMO_HOD_V2's sizing unchanged.

``_v2_reference`` is the sizing function exactly as MOMO_HOD_V2 ran it
before the AI day manager existed (commit 8467c4f, verbatim apart from its
name and two helper names). Over a seeded grid of portfolios, setups and
reviews, the current function with its default, and with an explicit 1,
returns the same RiskApproval byte for byte. Any multiplier in (0, 1] only
narrows: it approves nothing the policy blocks and never sizes above it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
import itertools
import random
import secrets

import pytest

from trading.contracts import (
    AIReview,
    PortfolioSnapshot,
    PositionExposure,
    RiskApproval,
    RiskPolicy,
    RuleSetup,
    TradeIntent,
    fingerprint_payload,
)
import trading.risk.engine as risk_engine
from trading.risk.engine import (
    SIZE_SCALED_TO_ZERO,
    policy_quantity,
    require_size_multiplier,
    scaled_quantity,
    size_and_evaluate_trade,
)

NOW = datetime(2026, 10, 13, 15, 50, 5, tzinfo=timezone.utc)
STEPS = (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1"))
MULTIPLIERS = sorted({day * setup for day in STEPS for setup in STEPS})


def _pct(value: Decimal, percentage: Decimal) -> Decimal:
    return value * percentage / Decimal("100")


def _units(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_DOWN))


# fmt: off
def _v2_reference(
    *,
    policy: RiskPolicy,
    portfolio: PortfolioSnapshot,
    setup: RuleSetup,
    review: AIReview,
    admitted_symbols: frozenset[str],
    now: datetime | None = None,
) -> RiskApproval:
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at = observed_now + timedelta(seconds=15)
    setup_fingerprint = setup.fingerprint()
    review_fingerprint = review.fingerprint()
    codes: list[str] = []

    if review.setup_fingerprint != setup_fingerprint:
        codes.append("ai_setup_mismatch")
    if review.decision != "PROPOSE" or review.failure_reason:
        codes.append("ai_not_proposed")
    if setup.symbol not in admitted_symbols:
        codes.append("symbol_not_admitted")
    if any(position.symbol == setup.symbol for position in portfolio.positions):
        codes.append("pyramiding_blocked")
    if setup.symbol in portfolio.pending_symbols:
        codes.append("pending_symbol_blocked")
    if len(portfolio.positions) + len(portfolio.pending_symbols) >= policy.max_open_positions:
        codes.append("max_open_positions_reached")

    daily_loss = portfolio.daily_loss
    if daily_loss >= _pct(portfolio.net_liquidation, policy.max_daily_loss_pct):
        codes.append("daily_loss_limit_reached")

    unit_risk = setup.entry_limit - setup.stop_price
    risk_budget = _pct(portfolio.net_liquidation, policy.max_risk_per_trade_pct)
    position_notional_budget = _pct(
        portfolio.net_liquidation, policy.max_position_notional_pct
    )
    current_notional = sum(position.market_value for position in portfolio.positions)
    total_notional_budget = _pct(portfolio.net_liquidation, policy.max_total_notional_pct)
    remaining_notional = total_notional_budget - current_notional - portfolio.reserved_notional

    quantity = min(
        _units(risk_budget / unit_risk),
        _units(position_notional_budget / setup.entry_limit),
        _units(max(Decimal("0"), remaining_notional) / setup.entry_limit),
    )
    if quantity < 1:
        codes.append("risk_budget_too_small")

    actual_risk = unit_risk * max(quantity, 0)
    if actual_risk + portfolio.reserved_risk > risk_budget:
        codes.append("risk_reservation_limit_exceeded")

    reward_to_risk = (setup.target_price - setup.entry_limit) / unit_risk
    if reward_to_risk < policy.min_reward_to_risk:
        codes.append("reward_to_risk_below_minimum")

    intent = None
    if not codes:
        execution_key = fingerprint_payload(
            {
                "setup": setup_fingerprint,
                "review": review_fingerprint,
                "portfolio": portfolio.fingerprint,
                "policy": policy.fingerprint(),
            }
        )
        intent = TradeIntent(
            execution_key=execution_key,
            setup_fingerprint=setup_fingerprint,
            approval_token=secrets.token_urlsafe(32),
            expires_at=expires_at,
            strategy_id=setup.strategy_id,
            strategy_version=setup.strategy_version,
            symbol=setup.symbol,
            quantity=quantity,
            entry_limit=setup.entry_limit,
            stop_price=setup.stop_price,
            target_price=setup.target_price,
        )

    return RiskApproval(
        approved=not codes,
        codes=tuple(codes),
        intent=intent,
        setup_fingerprint=setup_fingerprint,
        ai_review_fingerprint=review_fingerprint,
        policy_fingerprint=policy.fingerprint(),
        portfolio_fingerprint=portfolio.fingerprint,
        expires_at=expires_at,
    )
# fmt: on


@pytest.fixture
def tokens(monkeypatch):
    """Deterministic approval tokens, so two sizings compare byte for byte."""
    counter = itertools.count()

    def token(_bytes: int) -> str:
        return f"token-{next(counter):040d}"

    monkeypatch.setattr(secrets, "token_urlsafe", token)
    return counter


def _setup(rng: random.Random, symbol: str = "BF B") -> RuleSetup:
    entry = Decimal(rng.randint(1000, 30000)) / 100
    stop_distance = max(Decimal("0.01"), (entry * Decimal(rng.randint(30, 150)) / 10000).quantize(Decimal("0.01")))
    stop = entry - stop_distance
    target = entry + stop_distance * Decimal(rng.choice(("2", "2", "2.5", "1.5")))
    return RuleSetup(
        run_id="run-1",
        candidate_id="cand-1",
        strategy_id="strategy-momo",
        strategy_version=1,
        strategy_kind="momo_hod",
        symbol=symbol,
        atr=Decimal("0.08"),
        entry_limit=entry,
        stop_price=stop,
        target_price=target,
        bar_fingerprints=("a" * 64,),
        quote_fingerprint="b" * 64,
        detected_at=NOW,
    )


def _review(setup: RuleSetup, rng: random.Random) -> AIReview:
    decision = rng.choice(("PROPOSE", "PROPOSE", "PROPOSE", "ABSTAIN"))
    return AIReview(
        setup_fingerprint=setup.fingerprint() if rng.random() > 0.05 else "c" * 64,
        decision=decision,
        confidence=Decimal("0.7"),
        rationale="Golden grid.",
        model_id="claude-opus-5-5",
        prompt_version="momo-review-v2",
        response_hash="d" * 64,
        latency_ms=10,
        cost_usd=Decimal("0.05"),
    )


def _portfolio(rng: random.Random) -> PortfolioSnapshot:
    held = rng.choice((0, 0, 0, 1, 1, 2, 3))
    positions = tuple(
        PositionExposure(symbol=symbol, quantity=rng.randint(1, 500), market_value=Decimal(rng.randint(0, 120000)))
        for symbol in rng.sample(("AAPL", "MSFT", "NVDA", "BF B"), held)
    )
    return PortfolioSnapshot(
        observed_at=NOW,
        net_liquidation=Decimal(rng.choice((665000, 665000, 100000, 25000, 3000, 900))),
        realized_day_pnl=Decimal(rng.choice((0, 0, 0, -500, -15000, 2000))),
        unrealized_pnl=Decimal(rng.choice((0, -300, 400))),
        positions=positions,
        pending_symbols=tuple(rng.sample(("TSLA", "AMD", "BF B"), rng.choice((0, 0, 0, 1, 2)))),
        reserved_risk=Decimal(rng.choice((0, 0, 0, 500, 5000))),
        reserved_notional=Decimal(rng.choice((0, 0, 0, 20000, 250000))),
    )


def _cases(count: int = 400):
    rng = random.Random(20261013)
    for _ in range(count):
        setup = _setup(rng)
        yield (
            RiskPolicy(),
            _portfolio(rng),
            setup,
            _review(setup, rng),
            frozenset({"BF B"} if rng.random() < 0.9 else {"FIGS"}),
        )


def _bytes(approval: RiskApproval) -> str:
    return approval.model_dump_json()


def test_the_default_multiplier_is_v2_byte_for_byte(monkeypatch):
    approved = 0
    for policy, portfolio, setup, review, admitted in _cases():
        results = []
        for call in (
            lambda: _v2_reference(policy=policy, portfolio=portfolio, setup=setup, review=review,
                                  admitted_symbols=admitted, now=NOW),
            lambda: size_and_evaluate_trade(policy=policy, portfolio=portfolio, setup=setup,
                                            review=review, admitted_symbols=admitted, now=NOW),
            lambda: size_and_evaluate_trade(policy=policy, portfolio=portfolio, setup=setup,
                                            review=review, admitted_symbols=admitted, now=NOW,
                                            size_multiplier=Decimal("1")),
        ):
            # The same token for each of the three calls of one case.
            monkeypatch.setattr(secrets, "token_urlsafe", lambda _bytes: "t" * 43)
            results.append(_bytes(call()))
        assert results[0] == results[1] == results[2]
        approved += '"approved":true' in results[0]
    # The grid exercises both approvals and every kind of block.
    assert 30 < approved < 370


def test_bf_b_keeps_v2s_known_size_by_default():
    """The 2026-10-12 BF B setup: 3726 shares at 26.77 with the 26.51 stop."""
    setup = RuleSetup(
        run_id="run-1", candidate_id="cand-1", strategy_id="strategy-momo", strategy_version=1,
        strategy_kind="momo_hod", symbol="BF B", atr=Decimal("0.0792"), entry_limit=Decimal("26.77"),
        stop_price=Decimal("26.51"), target_price=Decimal("27.29"), bar_fingerprints=("a" * 64,),
        quote_fingerprint="b" * 64, detected_at=NOW,
    )
    portfolio = PortfolioSnapshot(observed_at=NOW, net_liquidation=Decimal("665000"))
    review = AIReview(
        setup_fingerprint=setup.fingerprint(), decision="PROPOSE", confidence=Decimal("0.7"),
        rationale="BF B.", model_id="m", prompt_version="momo-review-v2", response_hash="d" * 64,
        latency_ms=1, cost_usd=Decimal("0"),
    )
    kwargs = dict(policy=RiskPolicy(), portfolio=portfolio, setup=setup, review=review,
                  admitted_symbols=frozenset({"BF B"}), now=NOW)

    assert policy_quantity(policy=RiskPolicy(), portfolio=portfolio, setup=setup) == 3726
    assert size_and_evaluate_trade(**kwargs).intent.quantity == 3726
    assert size_and_evaluate_trade(**kwargs, size_multiplier=Decimal("0.75")).intent.quantity == 2794
    assert size_and_evaluate_trade(**kwargs, size_multiplier=Decimal("0.0625")).intent.quantity == 232


def test_any_multiplier_only_narrows_the_v2_approval(tokens):
    for (policy, portfolio, setup, review, admitted), multiplier in zip(
        _cases(), itertools.cycle(MULTIPLIERS)
    ):
        reference = _v2_reference(policy=policy, portfolio=portfolio, setup=setup, review=review,
                                  admitted_symbols=admitted, now=NOW)
        managed = size_and_evaluate_trade(policy=policy, portfolio=portfolio, setup=setup, review=review,
                                          admitted_symbols=admitted, now=NOW, size_multiplier=multiplier)
        policy_qty = policy_quantity(policy=policy, portfolio=portfolio, setup=setup)
        if managed.approved:
            # Approved only where V2 approved, and never larger.
            assert reference.approved
            assert managed.intent.quantity == _units(Decimal(policy_qty) * multiplier)
            assert 1 <= managed.intent.quantity <= reference.intent.quantity == policy_qty
        elif reference.approved:
            # The policy approved; only the steps left less than one share.
            assert managed.codes == (SIZE_SCALED_TO_ZERO,)
            assert _units(Decimal(policy_qty) * multiplier) == 0
        else:
            # A blocked V2 setup stays blocked for the same reasons.
            assert managed.codes == reference.codes
        assert managed.setup_fingerprint == reference.setup_fingerprint
        assert managed.policy_fingerprint == reference.policy_fingerprint


@pytest.mark.parametrize(
    "value",
    [Decimal("0"), Decimal("-0.25"), Decimal("1.01"), Decimal("2"), Decimal("NaN"), Decimal("Infinity"),
     0.5, "0.5", True, None, 2],
)
def test_a_multiplier_outside_zero_to_one_is_refused_before_sizing(value):
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        require_size_multiplier(value)
    with pytest.raises(ValueError):
        scaled_quantity(100, value)  # type: ignore[arg-type]


def test_scaling_floors_and_never_exceeds_the_quantity():
    for quantity in (0, 1, 3, 4, 7, 931, 3726):
        for multiplier in MULTIPLIERS:
            scaled = scaled_quantity(quantity, multiplier)
            assert 0 <= scaled <= quantity
            assert scaled == int(quantity * multiplier)  # exact: the steps are binary fractions
    assert scaled_quantity(3, Decimal("0.25")) == 0
    assert require_size_multiplier(1) == Decimal("1")
