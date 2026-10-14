"""The shared PAPER risk book (trading/risk/book.py): the engine's own limits, pure."""
from __future__ import annotations

from decimal import Decimal

import pytest

from trading.risk.book import (
    DAILY_RISK_PCT,
    MAX_BOT_POSITIONS,
    MAX_ORDER_NOTIONAL_PCT,
    NEW_TRADE_RISK_PCT,
    OPEN_RISK_PCT,
    BookPosition,
    NewTrade,
    RiskBook,
    evaluate_new_trade,
    position_risk,
)


NET_LIQ = Decimal("100000")
HASH = "a" * 64


def book(*positions: BookPosition, realized: str = "0", net_liq: Decimal | None = NET_LIQ,
         unmanaged: frozenset[str] = frozenset()) -> RiskBook:
    return RiskBook(net_liquidation=net_liq, realized_pnl=Decimal(realized), positions=positions,
                    unmanaged_symbols=unmanaged)


def held(symbol: str, risk: str, *, slot: bool = True) -> BookPosition:
    return BookPosition(symbol=symbol, open_risk=Decimal(risk), holds_slot=slot)


def trade(symbol: str = "NEWX", quantity: int = 100, entry: str = "50", stop: str = "49") -> NewTrade:
    return NewTrade(symbol=symbol, quantity=quantity, entry=Decimal(entry), stop=Decimal(stop))


def test_the_book_limits_are_the_owner_approved_numbers() -> None:
    assert (NEW_TRADE_RISK_PCT, OPEN_RISK_PCT, DAILY_RISK_PCT) == (Decimal("0.5"), Decimal("1.5"), Decimal("2"))
    assert (MAX_BOT_POSITIONS, MAX_ORDER_NOTIONAL_PCT) == (3, Decimal("25"))


def test_a_trade_inside_every_limit_is_approved() -> None:
    check = evaluate_new_trade(book(held("AAA", "400")), trade(quantity=400))  # 400 risk, 20000 notional
    assert check.approved and check.code is None
    assert (check.new_risk, check.open_risk, check.realized_loss) == (Decimal("400"), Decimal("400"), Decimal("0"))


@pytest.mark.parametrize(("current", "new_trade", "code"), [
    # 0.5% of 100000 is 500: 501 risk is one share-dollar too much (notional only 10020).
    (book(), trade(quantity=501, entry="20", stop="19"), "new_trade_risk_exceeded"),
    # 25% notional: 501 x 50 = 25050 > 25000 (risk 501 x 0.01 stays tiny).
    (book(), trade(quantity=501, stop="49.99"), "order_notional_exceeded"),
    # 1.5% open risk: 1100 open + 450 new = 1550 > 1500.
    (book(held("AAA", "600"), held("BBB", "500")), trade(quantity=450), "open_risk_exceeded"),
    # 2% daily: 700 realized loss + 900 open + 450 new = 2050 > 2000.
    (book(held("AAA", "900"), realized="-700"), trade(quantity=450), "daily_risk_exceeded"),
    (book(held("AAA", "1"), held("BBB", "1"), held("CCC", "1")), trade(), "max_positions_reached"),
    (book(held("AAA", "1"), held("BBB", "1"), unmanaged=frozenset({"ZZZ"})), trade(), "max_positions_reached"),
    (book(held("NEWX", "1")), trade(), "symbol_already_held"),
    (book(unmanaged=frozenset({"NEWX"})), trade(), "symbol_already_held"),
    (book(net_liq=None), trade(), "net_liquidation_unknown"),
    (book(), trade(stop="50"), "invalid_trade"),
    (book(), trade(quantity=0), "invalid_trade"),
])
def test_each_limit_refuses_with_its_named_code(current: RiskBook, new_trade: NewTrade, code: str) -> None:
    check = evaluate_new_trade(current, new_trade)
    assert not check.approved
    assert check.code == code


def test_open_risk_exactly_at_the_limit_is_allowed() -> None:
    # 500 + 500 open + 500 new = 1500: equal to the 1.5% budget, not above it.
    assert evaluate_new_trade(book(held("AAA", "500"), held("BBB", "500")), trade(quantity=500)).approved


def test_a_winning_day_never_adds_capacity() -> None:
    winning = book(held("AAA", "600"), held("BBB", "500"), realized="5000")
    assert evaluate_new_trade(winning, trade(quantity=450)).code == "open_risk_exceeded"
    assert winning.realized_loss == 0


def test_positions_without_a_slot_still_carry_risk() -> None:
    # A cancelled entry holds no slot and no risk; a needs-attention bracket keeps both.
    current = book(held("AAA", "0", slot=False), held("BBB", "1200"))
    assert current.slot_symbols() == frozenset({"BBB"})
    assert evaluate_new_trade(current, trade(quantity=400)).code == "open_risk_exceeded"


def test_position_risk_values_held_shares_at_their_fill_and_pending_at_the_limit() -> None:
    assert position_risk(held=60, average_entry=Decimal("50.20"), pending=40, entry=Decimal("50"),
                         stop=Decimal("49")) == Decimal("60") * Decimal("1.20") + Decimal("40")
    assert position_risk(held=0, average_entry=None, pending=100, entry=Decimal("50"), stop=Decimal("49")) == 100
    # A fill below the stop has no further downside to the stop (never negative).
    assert position_risk(held=10, average_entry=Decimal("48"), pending=0, entry=Decimal("50"),
                         stop=Decimal("49")) == 0
    with pytest.raises(ValueError):
        position_risk(held=-1, average_entry=None, pending=0, entry=Decimal("50"), stop=Decimal("49"))
