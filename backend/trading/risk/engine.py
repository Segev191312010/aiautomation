"""Deterministic sizing and risk approval for long-only Autotrader V1 setups.

``size_multiplier`` lets the AI day manager scale a managed setup down by its
fixed steps (Principle 2): every check runs on the policy quantity, exactly
as without it, and only an approved intent is then scaled to
floor(policy quantity x multiplier). So a multiplier can only shrink an
approved order or turn it into a skip, never approve what the policy blocks
or size above it. The default (1) is byte-for-byte the unmanaged sizing.
"""
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


NO_SCALING = Decimal("1")
# The policy approved the setup, but the manager's steps leave less than one
# share: the setup is skipped (narrowing), never rounded up.
SIZE_SCALED_TO_ZERO = "size_scaled_to_zero"


def require_size_multiplier(value: object) -> Decimal:
    """A finite Decimal in (0, 1]; anything else is refused before sizing."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError("size_multiplier must be a Decimal in (0, 1]")
    multiplier = Decimal(value)
    if not multiplier.is_finite() or not Decimal("0") < multiplier <= NO_SCALING:
        raise ValueError("size_multiplier must be a Decimal in (0, 1]")
    return multiplier


def policy_quantity(*, policy: RiskPolicy, portfolio: PortfolioSnapshot, setup: RuleSetup) -> int:
    """The whole-share quantity the policy allows for ``setup`` (before any scaling)."""
    unit_risk = setup.entry_limit - setup.stop_price
    risk_budget = _percent_amount(portfolio.net_liquidation, policy.max_risk_per_trade_pct)
    position_notional_budget = _percent_amount(
        portfolio.net_liquidation, policy.max_position_notional_pct
    )
    current_notional = sum(position.market_value for position in portfolio.positions)
    total_notional_budget = _percent_amount(portfolio.net_liquidation, policy.max_total_notional_pct)
    remaining_notional = total_notional_budget - current_notional - portfolio.reserved_notional
    return min(
        _whole_units(risk_budget / unit_risk),
        _whole_units(position_notional_budget / setup.entry_limit),
        _whole_units(max(Decimal("0"), remaining_notional) / setup.entry_limit),
    )


def scaled_quantity(quantity: int, multiplier: Decimal) -> int:
    """floor(quantity x multiplier); never above ``quantity`` for a multiplier <= 1."""
    scale = require_size_multiplier(multiplier)
    if scale == NO_SCALING:
        return quantity
    return _whole_units(Decimal(quantity) * scale)


def size_and_evaluate_trade(
    *,
    policy: RiskPolicy,
    portfolio: PortfolioSnapshot,
    setup: RuleSetup,
    review: AIReview,
    admitted_symbols: frozenset[str],
    now: datetime | None = None,
    size_multiplier: Decimal = NO_SCALING,
) -> RiskApproval:
    multiplier = require_size_multiplier(size_multiplier)
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
    quantity = policy_quantity(policy=policy, portfolio=portfolio, setup=setup)
    if quantity < 1:
        codes.append("risk_budget_too_small")

    actual_risk = unit_risk * max(quantity, 0)
    if actual_risk + portfolio.reserved_risk > risk_budget:
        codes.append("risk_reservation_limit_exceeded")

    reward_to_risk = (setup.target_price - setup.entry_limit) / unit_risk
    if reward_to_risk < policy.min_reward_to_risk:
        codes.append("reward_to_risk_below_minimum")

    # Scaling follows every policy check, so it can only shrink an approval.
    final_quantity = quantity if multiplier == NO_SCALING else scaled_quantity(quantity, multiplier)
    if not codes and final_quantity < 1:
        codes.append(SIZE_SCALED_TO_ZERO)

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
            quantity=final_quantity,
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
