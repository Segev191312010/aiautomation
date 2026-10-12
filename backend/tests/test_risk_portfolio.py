"""Broker account truth feeds risk; open losses count toward the breaker."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading.contracts import AccountSnapshot, PortfolioSnapshot, RawAccountPosition
from trading.risk import PortfolioDerivationError, portfolio_from_account

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)


def _position(quantity: str, *, symbol: str = "AAPL") -> RawAccountPosition:
    return RawAccountPosition(
        paper_account_id="DU1",
        con_id=101,
        symbol=symbol,
        quantity=Decimal(quantity),
        market_price=Decimal("150"),
        market_value=Decimal("150") * Decimal(quantity),
        average_cost=Decimal("140"),
        unrealized_pnl=Decimal("10"),
        realized_pnl=Decimal("0"),
        observed_at=NOW,
    )


def _account(*positions: RawAccountPosition, realized: str = "0", unrealized: str = "0") -> AccountSnapshot:
    return AccountSnapshot(
        paper_account_id="DU1",
        client_id=7,
        observed_at=NOW,
        net_liquidation=Decimal("100000"),
        available_funds=Decimal("90000"),
        realized_day_pnl=Decimal(realized),
        unrealized_pnl=Decimal(unrealized),
        positions=positions,
    )


def test_account_becomes_long_only_portfolio_with_day_pnl():
    portfolio = portfolio_from_account(
        _account(_position("10"), realized="-300", unrealized="-50"),
        pending_symbols=("MSFT",),
    )

    assert portfolio.net_liquidation == Decimal("100000")
    assert [(item.symbol, item.quantity) for item in portfolio.positions] == [("AAPL", 10)]
    assert portfolio.pending_symbols == ("MSFT",)
    assert portfolio.daily_loss == Decimal("350")


@pytest.mark.parametrize("quantity", ["-5", "2.5"])
def test_short_or_fractional_positions_block_derivation(quantity):
    with pytest.raises(PortfolioDerivationError):
        portfolio_from_account(_account(_position(quantity)))


@pytest.mark.parametrize(
    ("realized", "unrealized", "loss"),
    [("-300", "0", "300"), ("0", "-200", "200"), ("-300", "500", "300"), ("200", "-500", "300")],
)
def test_unrealized_gains_never_offset_realized_losses(realized, unrealized, loss):
    portfolio = PortfolioSnapshot(
        observed_at=NOW,
        net_liquidation=Decimal("100000"),
        realized_day_pnl=Decimal(realized),
        unrealized_pnl=Decimal(unrealized),
    )
    assert portfolio.daily_loss == Decimal(loss)


def test_positions_the_engine_did_not_open_are_ignored_but_net_liq_is_whole_account():
    foreign = _position("100", symbol="TSLA")
    account = _account(foreign, realized="-9000", unrealized="-8000")

    portfolio = portfolio_from_account(account, owned_con_ids=frozenset())

    assert portfolio.positions == ()
    assert portfolio.daily_loss == Decimal("0")
    assert portfolio.net_liquidation == Decimal("100000")

    owned = portfolio_from_account(account, owned_con_ids=frozenset({101}))
    assert [item.symbol for item in owned.positions] == ["TSLA"]
