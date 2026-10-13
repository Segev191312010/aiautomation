"""The shared PAPER risk book: one budget per account and New York session.

Every strategy's bot entries draw on the same book (``<account>:<session
date>``), so three strategies can never each spend a full budget. The limits
are code-owned constants (owner-approved C2 plan); configuration can only make
the engine stricter by disabling entries, never loosen these numbers.

All amounts are percentages of the account's net liquidation:

* a new trade risks at most 0.5% (entry - stop, times quantity);
* open risk, the new trade included, stays at or below 1.5%;
* realized loss today + open risk + new risk stays at or below 2%;
* at most three bot positions, one per symbol;
* one order's notional is at most 25%.

The kill switch is not part of the book: it blocks entries before the book is
consulted and never blocks an exit.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


NEW_TRADE_RISK_PCT = Decimal("0.5")
OPEN_RISK_PCT = Decimal("1.5")
DAILY_RISK_PCT = Decimal("2")
MAX_BOT_POSITIONS = 3
MAX_ORDER_NOTIONAL_PCT = Decimal("25")
_HUNDRED = Decimal("100")

RiskBookCode = Literal[
    "invalid_trade",
    "net_liquidation_unknown",
    "symbol_already_held",
    "max_positions_reached",
    "order_notional_exceeded",
    "new_trade_risk_exceeded",
    "open_risk_exceeded",
    "daily_risk_exceeded",
]


def percent_of(amount: Decimal, percentage: Decimal) -> Decimal:
    return amount * percentage / _HUNDRED


def position_risk(
    *,
    held: int,
    average_entry: Decimal | None,
    pending: int,
    entry: Decimal,
    stop: Decimal,
) -> Decimal:
    """Loss to the protective stop of what a bracket holds plus what may still fill.

    Shares already held are valued at their average fill (never below zero: a
    stop above the fill price has no remaining downside); the part of the entry
    that may still fill is valued at the limit price.
    """
    if held < 0 or pending < 0:
        raise ValueError("held and pending shares cannot be negative")
    fill_price = average_entry if average_entry is not None and average_entry > 0 else entry
    held_risk = Decimal(held) * max(Decimal("0"), fill_price - stop)
    return held_risk + Decimal(pending) * max(Decimal("0"), entry - stop)


@dataclass(frozen=True, slots=True)
class BookPosition:
    """One bot bracket as the book sees it."""

    symbol: str
    open_risk: Decimal
    holds_slot: bool


@dataclass(frozen=True, slots=True)
class RiskBook:
    """Everything the book needs, read at one moment."""

    net_liquidation: Decimal | None
    realized_pnl: Decimal
    positions: tuple[BookPosition, ...] = ()
    # Symbols that take a bot slot without a bot bracket: live orders of the
    # engine's own client that the ledger does not know (bridge lesson L4).
    unmanaged_symbols: frozenset[str] = frozenset()

    @property
    def open_risk(self) -> Decimal:
        return sum((position.open_risk for position in self.positions), Decimal("0"))

    @property
    def realized_loss(self) -> Decimal:
        """Today's net realized loss; a winning day never adds capacity."""
        return max(Decimal("0"), -self.realized_pnl)

    def slot_symbols(self) -> frozenset[str]:
        held = {position.symbol for position in self.positions if position.holds_slot}
        return frozenset(held | set(self.unmanaged_symbols))


@dataclass(frozen=True, slots=True)
class NewTrade:
    symbol: str
    quantity: int
    entry: Decimal
    stop: Decimal

    @property
    def risk(self) -> Decimal:
        return Decimal(self.quantity) * (self.entry - self.stop)

    @property
    def notional(self) -> Decimal:
        return Decimal(self.quantity) * self.entry


@dataclass(frozen=True, slots=True)
class RiskBookCheck:
    approved: bool
    code: RiskBookCode | None
    detail: str
    new_risk: Decimal = Decimal("0")
    open_risk: Decimal = Decimal("0")
    realized_loss: Decimal = Decimal("0")


def _refuse(code: RiskBookCode, detail: str, book: RiskBook, trade_risk: Decimal) -> RiskBookCheck:
    return RiskBookCheck(
        approved=False, code=code, detail=detail, new_risk=trade_risk,
        open_risk=book.open_risk, realized_loss=book.realized_loss,
    )


def evaluate_new_trade(book: RiskBook, trade: NewTrade) -> RiskBookCheck:
    """Approve a new bot entry against the shared book, or name the first limit it breaks."""
    if (
        trade.quantity <= 0
        or not trade.entry.is_finite()
        or not trade.stop.is_finite()
        or not Decimal("0") < trade.stop < trade.entry
    ):
        return _refuse("invalid_trade", "trade must buy a positive quantity with stop < entry", book, Decimal("0"))
    risk = trade.risk
    net_liq = book.net_liquidation
    if net_liq is None or not net_liq.is_finite() or net_liq <= 0:
        return _refuse("net_liquidation_unknown", "net liquidation unknown", book, risk)
    slots = book.slot_symbols()
    if trade.symbol in slots:
        return _refuse("symbol_already_held", f"{trade.symbol} already has a bot position or order", book, risk)
    if len(slots) >= MAX_BOT_POSITIONS:
        return _refuse("max_positions_reached", f"max {MAX_BOT_POSITIONS} positions/entries already active",
                       book, risk)
    notional_cap = percent_of(net_liq, MAX_ORDER_NOTIONAL_PCT)
    if trade.notional > notional_cap:
        return _refuse(
            "order_notional_exceeded",
            f"order notional {trade.notional:.2f} exceeds {MAX_ORDER_NOTIONAL_PCT}% of net liquidation"
            f" {net_liq:.2f}",
            book, risk,
        )
    if risk > percent_of(net_liq, NEW_TRADE_RISK_PCT):
        return _refuse(
            "new_trade_risk_exceeded",
            f"order risk {risk:.2f} exceeds {NEW_TRADE_RISK_PCT}% of net liquidation {net_liq:.2f}",
            book, risk,
        )
    open_risk = book.open_risk
    if open_risk + risk > percent_of(net_liq, OPEN_RISK_PCT):
        return _refuse(
            "open_risk_exceeded",
            f"open risk {open_risk:.2f} + {risk:.2f} exceeds {OPEN_RISK_PCT}% of net liquidation {net_liq:.2f}",
            book, risk,
        )
    loss = book.realized_loss
    if loss + open_risk + risk > percent_of(net_liq, DAILY_RISK_PCT):
        return _refuse(
            "daily_risk_exceeded",
            f"realized loss {loss:.2f} + open risk {open_risk:.2f} + {risk:.2f} exceeds {DAILY_RISK_PCT}%"
            f" of net liquidation {net_liq:.2f}",
            book, risk,
        )
    return RiskBookCheck(
        approved=True, code=None, detail="within the shared risk book", new_risk=risk,
        open_risk=open_risk, realized_loss=loss,
    )


__all__ = [
    "DAILY_RISK_PCT",
    "MAX_BOT_POSITIONS",
    "MAX_ORDER_NOTIONAL_PCT",
    "NEW_TRADE_RISK_PCT",
    "OPEN_RISK_PCT",
    "BookPosition",
    "NewTrade",
    "RiskBook",
    "RiskBookCheck",
    "RiskBookCode",
    "evaluate_new_trade",
    "percent_of",
    "position_risk",
]
