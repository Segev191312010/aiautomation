"""Derive the risk engine's portfolio view from broker account truth."""
from __future__ import annotations

from decimal import Decimal

from trading.contracts import AccountSnapshot, PortfolioSnapshot, PositionExposure


class PortfolioDerivationError(ValueError):
    """The broker account cannot be represented as a long-only portfolio."""


def portfolio_from_account(
    account: AccountSnapshot,
    *,
    owned_con_ids: frozenset[int] | None = None,
    pending_symbols: tuple[str, ...] = (),
    reserved_risk: Decimal = Decimal("0"),
    reserved_notional: Decimal = Decimal("0"),
) -> PortfolioSnapshot:
    """Long-only view of the positions the engine is responsible for.

    ``owned_con_ids=None`` treats every broker position as the engine's. Given a
    set (owner decision 2026-10-12), positions the engine did not open are left
    out of exposure and day P&L; net liquidation still sizes from the whole
    account.
    """
    exposures: list[PositionExposure] = []
    positions = tuple(
        position
        for position in account.positions
        if owned_con_ids is None or position.con_id in owned_con_ids
    )
    for position in positions:
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
        realized_day_pnl=(
            account.realized_day_pnl
            if owned_con_ids is None
            else sum((position.realized_pnl for position in positions), Decimal("0"))
        ),
        unrealized_pnl=(
            account.unrealized_pnl
            if owned_con_ids is None
            else sum((position.unrealized_pnl for position in positions), Decimal("0"))
        ),
        positions=tuple(exposures),
        pending_symbols=pending_symbols,
        reserved_risk=reserved_risk,
        reserved_notional=reserved_notional,
    )


__all__ = ["PortfolioDerivationError", "portfolio_from_account"]
