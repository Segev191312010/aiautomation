"""Derive the risk engine's portfolio view from broker account truth."""
from __future__ import annotations

from decimal import Decimal

from trading.contracts import AccountSnapshot, PortfolioSnapshot, PositionExposure


class PortfolioDerivationError(ValueError):
    """The broker account cannot be represented as a long-only portfolio."""


def portfolio_from_account(
    account: AccountSnapshot,
    *,
    pending_symbols: tuple[str, ...] = (),
    reserved_risk: Decimal = Decimal("0"),
    reserved_notional: Decimal = Decimal("0"),
) -> PortfolioSnapshot:
    """Long-only view; a short or fractional position means state we did not create."""
    exposures: list[PositionExposure] = []
    for position in account.positions:
        if position.quantity < 0:
            raise PortfolioDerivationError("PAPER account holds a short position")
        if position.quantity != position.quantity.to_integral_value():
            raise PortfolioDerivationError("PAPER account holds a fractional position")
        exposures.append(
            PositionExposure(
                symbol=position.symbol,
                quantity=int(position.quantity),
                market_value=abs(position.market_value),
            )
        )
    return PortfolioSnapshot(
        observed_at=account.observed_at,
        net_liquidation=account.net_liquidation,
        realized_day_pnl=account.realized_day_pnl,
        unrealized_pnl=account.unrealized_pnl,
        positions=tuple(exposures),
        pending_symbols=pending_symbols,
        reserved_risk=reserved_risk,
        reserved_notional=reserved_notional,
    )


__all__ = ["PortfolioDerivationError", "portfolio_from_account"]
