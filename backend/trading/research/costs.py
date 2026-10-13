"""Trading costs in R, one model for simulated setups and real PAPER brackets.

IBKR Pro "Fixed" pricing for US stocks: $0.005 a share, at least $1.00 and at
most 1% of the trade value per order. A simulated setup has no quantity (the
replay and the tracker do not size), so its costs are per share: the
commission on both sides plus one tick of slippage on exits that execute as
marketable orders (a triggered stop, the 15:45 flatten, a mark at the last
close). A limit target fills at its price. A real bracket knows its shares
and its fill prices, so only commissions (with the per-order minimum and cap)
are added; its slippage is already in the fills.

Costs are always charged, never credited: expectancy "after costs" is gross
R minus ``cost_r``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

COST_QUANTUM = Decimal("0.0001")
MONEY_QUANTUM = Decimal("0.01")
_PERCENT = Decimal("100")
# Exits that trade as marketable orders and so pay the spread.
MARKET_EXIT_REASONS = frozenset({"stop", "time", "data_end", "flatten"})


@dataclass(frozen=True, slots=True)
class CostModel:
    model_id: str = "ibkr-pro-fixed-v1"
    commission_per_share: Decimal = Decimal("0.005")
    min_commission_per_order: Decimal = Decimal("1.00")
    max_commission_pct_of_value: Decimal = Decimal("1")
    tick: Decimal = Decimal("0.01")
    market_exit_slippage_ticks: int = 1

    def __post_init__(self) -> None:
        for name in (
            "commission_per_share",
            "min_commission_per_order",
            "max_commission_pct_of_value",
            "tick",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative Decimal")
        if self.market_exit_slippage_ticks < 0:
            raise ValueError("market_exit_slippage_ticks must not be negative")

    def per_share_cost(self, exit_reason: str) -> Decimal:
        """Commission on entry and exit, plus exit slippage when the exit is marketable."""
        slippage = (
            self.tick * self.market_exit_slippage_ticks
            if exit_reason in MARKET_EXIT_REASONS
            else Decimal("0")
        )
        return self.commission_per_share * 2 + slippage

    def cost_r(self, *, entry: Decimal, stop: Decimal, exit_reason: str) -> Decimal:
        """A simulated trade's costs in R, rounded up so costs are never understated."""
        risk = entry - stop
        if risk <= 0:
            raise ValueError("a long trade needs its stop below its entry")
        return (self.per_share_cost(exit_reason) / risk).quantize(
            COST_QUANTUM, rounding=ROUND_CEILING
        )

    def order_commission(self, *, shares: int, price: Decimal) -> Decimal:
        """One order's commission: per share, with the per-order minimum and cap."""
        if shares <= 0:
            return Decimal("0")
        commission = max(self.min_commission_per_order, self.commission_per_share * shares)
        cap = price * shares * self.max_commission_pct_of_value / _PERCENT
        if cap > 0:
            commission = min(commission, cap)
        return commission.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)

    def payload(self) -> dict[str, str]:
        return {
            "model_id": self.model_id,
            "commission_per_share": str(self.commission_per_share),
            "min_commission_per_order": str(self.min_commission_per_order),
            "max_commission_pct_of_value": str(self.max_commission_pct_of_value),
            "tick": str(self.tick),
            "market_exit_slippage_ticks": str(self.market_exit_slippage_ticks),
        }


DEFAULT_COST_MODEL = CostModel()


__all__ = [
    "COST_QUANTUM",
    "DEFAULT_COST_MODEL",
    "MARKET_EXIT_REASONS",
    "CostModel",
]
