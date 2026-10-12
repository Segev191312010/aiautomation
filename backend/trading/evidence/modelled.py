"""Deterministic shadow-fill modelling from persisted IBKR market data.

This module never submits orders and never represents modelled outcomes as
broker fills.  Every result is explicitly labelled ``MODELLED`` by the domain
contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Literal

from trading.contracts import (
    CommissionPolicy,
    CompletedBar,
    ModelledFill,
    QuoteSnapshot,
    RuleSetup,
    require_aware,
)


SHADOW_SLIPPAGE_BPS = Decimal("5")
_BASIS_POINTS = Decimal("10000")


class ShadowFillModelError(ValueError):
    """Raised when real-data inputs cannot support a deterministic fill."""


@dataclass(frozen=True, slots=True)
class ModelledEntryResult:
    fill: ModelledFill | None
    blocked_reason: Literal["modelled_entry_exceeds_limit"] | None = None

    def __post_init__(self) -> None:
        if (self.fill is None) == (self.blocked_reason is None):
            raise ValueError("entry modelling must produce either a fill or a block reason")


def _positive_finite(value: Decimal, *, name: str) -> Decimal:
    normalized = Decimal(value)
    if not normalized.is_finite() or normalized <= 0:
        raise ShadowFillModelError(f"{name} must be positive and finite")
    return normalized


def round_up_to_tick(value: Decimal, min_tick: Decimal) -> Decimal:
    """Round a buy price upward so the model never improves the fill."""
    price = _positive_finite(value, name="price")
    tick = _positive_finite(min_tick, name="min_tick")
    return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def round_down_to_tick(value: Decimal, min_tick: Decimal) -> Decimal:
    """Round a sell price downward so the model never improves the fill."""
    price = _positive_finite(value, name="price")
    tick = _positive_finite(min_tick, name="min_tick")
    rounded = (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    return _positive_finite(rounded, name="rounded price")


def _apply_adverse_slippage(
    *,
    price: Decimal,
    side: Literal["BUY", "SELL"],
    min_tick: Decimal,
) -> Decimal:
    multiplier = SHADOW_SLIPPAGE_BPS / _BASIS_POINTS
    if side == "BUY":
        return round_up_to_tick(price * (Decimal("1") + multiplier), min_tick)
    return round_down_to_tick(price * (Decimal("1") - multiplier), min_tick)


def model_entry_fill(
    *,
    setup: RuleSetup,
    quote: QuoteSnapshot,
    quantity: int,
    commission_policy: CommissionPolicy,
    filled_at: datetime,
    rule_only_baseline: bool = False,
) -> ModelledEntryResult:
    """Model entry at ask plus five bps, bounded by the rule-owned limit."""
    observed_at = require_aware(filled_at)
    if quantity < 1:
        raise ShadowFillModelError("quantity must be positive")
    quote.require_fresh(now=observed_at)
    modelled_price = _apply_adverse_slippage(
        price=quote.ask,
        side="BUY",
        min_tick=quote.min_tick,
    )
    if modelled_price > setup.entry_limit:
        return ModelledEntryResult(fill=None, blocked_reason="modelled_entry_exceeds_limit")
    fill = ModelledFill(
        run_id=setup.run_id,
        candidate_id=setup.candidate_id,
        setup_fingerprint=setup.fingerprint(),
        quote_fingerprint=quote.fingerprint(),
        commission_policy_fingerprint=commission_policy.fingerprint(),
        side="BUY",
        reason="entry",
        quantity=quantity,
        reference_price=quote.ask,
        modelled_price=modelled_price,
        commission_usd=commission_policy.fee_for(
            side="BUY",
            quantity=quantity,
            price=modelled_price,
        ),
        filled_at=observed_at,
        rule_only_baseline=rule_only_baseline,
    )
    return ModelledEntryResult(fill=fill)


def triggered_exit_reason(
    *,
    setup: RuleSetup,
    bar: CompletedBar,
) -> Literal["stop", "target"] | None:
    """Choose the conservative stop when both levels occur in one bar."""
    if bar.low <= setup.stop_price:
        return "stop"
    if bar.high >= setup.target_price:
        return "target"
    return None


def model_triggered_exit_fill(
    *,
    setup: RuleSetup,
    bar: CompletedBar,
    quote: QuoteSnapshot,
    quantity: int,
    commission_policy: CommissionPolicy,
    filled_at: datetime,
    rule_only_baseline: bool = False,
) -> ModelledFill | None:
    """Model a later stop/target exit at bid minus five bps."""
    reason = triggered_exit_reason(setup=setup, bar=bar)
    if reason is None:
        return None
    observed_at = require_aware(filled_at)
    if quantity < 1:
        raise ShadowFillModelError("quantity must be positive")
    if quote.con_id != bar.con_id:
        raise ShadowFillModelError("exit quote and bar must identify the same contract")
    quote.require_fresh(now=observed_at)
    modelled_price = _apply_adverse_slippage(
        price=quote.bid,
        side="SELL",
        min_tick=quote.min_tick,
    )
    return ModelledFill(
        run_id=setup.run_id,
        candidate_id=setup.candidate_id,
        setup_fingerprint=setup.fingerprint(),
        quote_fingerprint=quote.fingerprint(),
        commission_policy_fingerprint=commission_policy.fingerprint(),
        side="SELL",
        reason=reason,
        quantity=quantity,
        reference_price=quote.bid,
        modelled_price=modelled_price,
        commission_usd=commission_policy.fee_for(
            side="SELL",
            quantity=quantity,
            price=modelled_price,
        ),
        filled_at=observed_at,
        rule_only_baseline=rule_only_baseline,
    )


def model_session_close_fill(
    *,
    setup: RuleSetup,
    quote: QuoteSnapshot,
    quantity: int,
    commission_policy: CommissionPolicy,
    closed_at: datetime,
    rule_only_baseline: bool = False,
) -> ModelledFill:
    """Close an outstanding modelled position from a fresh pre-close bid."""
    observed_at = require_aware(closed_at)
    if quantity < 1:
        raise ShadowFillModelError("quantity must be positive")
    quote.require_fresh(now=observed_at)
    modelled_price = _apply_adverse_slippage(
        price=quote.bid,
        side="SELL",
        min_tick=quote.min_tick,
    )
    return ModelledFill(
        run_id=setup.run_id,
        candidate_id=setup.candidate_id,
        setup_fingerprint=setup.fingerprint(),
        quote_fingerprint=quote.fingerprint(),
        commission_policy_fingerprint=commission_policy.fingerprint(),
        side="SELL",
        reason="session_close",
        quantity=quantity,
        reference_price=quote.bid,
        modelled_price=modelled_price,
        commission_usd=commission_policy.fee_for(
            side="SELL",
            quantity=quantity,
            price=modelled_price,
        ),
        filled_at=observed_at,
        rule_only_baseline=rule_only_baseline,
    )


def net_pnl(entry: ModelledFill, exit_fill: ModelledFill) -> Decimal:
    """Return net modelled P&L after both immutable commission records."""
    if entry.side != "BUY" or exit_fill.side != "SELL":
        raise ShadowFillModelError("net P&L requires one entry and one exit fill")
    identity = (
        entry.run_id,
        entry.candidate_id,
        entry.setup_fingerprint,
        entry.quantity,
        entry.commission_policy_fingerprint,
        entry.rule_only_baseline,
    )
    exit_identity = (
        exit_fill.run_id,
        exit_fill.candidate_id,
        exit_fill.setup_fingerprint,
        exit_fill.quantity,
        exit_fill.commission_policy_fingerprint,
        exit_fill.rule_only_baseline,
    )
    if identity != exit_identity:
        raise ShadowFillModelError("modelled fills do not belong to one position")
    gross = (exit_fill.modelled_price - entry.modelled_price) * Decimal(entry.quantity)
    return gross - entry.commission_usd - exit_fill.commission_usd


__all__ = [
    "ModelledEntryResult",
    "SHADOW_SLIPPAGE_BPS",
    "ShadowFillModelError",
    "model_entry_fill",
    "model_session_close_fill",
    "model_triggered_exit_fill",
    "net_pnl",
    "round_down_to_tick",
    "round_up_to_tick",
    "triggered_exit_reason",
]
