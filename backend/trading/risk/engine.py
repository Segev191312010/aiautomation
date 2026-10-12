"""Deterministic sizing and risk approval for long-only Autotrader V1 setups."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
import secrets

from trading.contracts import (
    AIReview,
    PortfolioSnapshot,
    RiskApproval,
    RiskPolicy,
    RuleSetup,
    TradeIntent,
    fingerprint_payload,
)


def _percent_amount(value: Decimal, percentage: Decimal) -> Decimal:
    return value * percentage / Decimal("100")


def _whole_units(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_DOWN))


def size_and_evaluate_trade(
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
    if daily_loss >= _percent_amount(portfolio.net_liquidation, policy.max_daily_loss_pct):
        codes.append("daily_loss_limit_reached")

    unit_risk = setup.entry_limit - setup.stop_price
    risk_budget = _percent_amount(portfolio.net_liquidation, policy.max_risk_per_trade_pct)
    position_notional_budget = _percent_amount(
        portfolio.net_liquidation, policy.max_position_notional_pct
    )
    current_notional = sum(position.market_value for position in portfolio.positions)
    total_notional_budget = _percent_amount(portfolio.net_liquidation, policy.max_total_notional_pct)
    remaining_notional = total_notional_budget - current_notional - portfolio.reserved_notional

    quantity = min(
        _whole_units(risk_budget / unit_risk),
        _whole_units(position_notional_budget / setup.entry_limit),
        _whole_units(max(Decimal("0"), remaining_notional) / setup.entry_limit),
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
