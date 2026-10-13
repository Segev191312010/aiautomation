"""Persistence for the reviewed execution engine (the execution-ledger migration's tables).

The repository records what the engine decided and what IBKR reported; it
never talks to IBKR. Every write that must precede broker I/O commits before
the engine sends anything:

* :meth:`ExecutionLedger.stage_bracket` claims the decision in the outbox,
  re-checks the shared risk book inside the same IMMEDIATE transaction and
  persists the bracket, its three legs with their order ids, and the risk
  reservation; only then may the engine call ``placeOrder``;
* :meth:`ExecutionLedger.add_flatten_leg` persists the fallback market sell
  (and the one-way flatten state) before it is sent.

A crash can therefore leave an order unconfirmed, never unrecorded; restart
reconciliation turns an unconfirmed row into ``sent`` or ``unknown`` and never
sends it again.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re
from typing import Literal
from uuid import uuid4

import aiosqlite

from db.core import get_db, transaction
from trading.risk.book import (
    BookPosition,
    NewTrade,
    RiskBook,
    RiskBookCheck,
    evaluate_new_trade,
    position_risk,
)


ORDER_REF_PREFIX = "at1:"
LegCode = Literal["P", "T", "S", "F"]
LEG_CODES: tuple[LegCode, ...] = ("P", "T", "S", "F")
BRACKET_LEGS: tuple[LegCode, ...] = ("P", "T", "S")
EXIT_LEGS: tuple[LegCode, ...] = ("T", "S", "F")
BracketState = Literal["sending", "working", "open", "closed", "cancelled", "unknown", "needs_attention"]
FlattenState = Literal["none", "repricing", "repriced", "cancelling", "selling", "skipped", "failed"]
LegState = Literal["staged", "sent", "unknown"]
# IBKR statuses after which a parent order can no longer fill.
PARENT_DEAD_STATUSES: frozenset[str] = frozenset({"Cancelled", "ApiCancelled", "Inactive"})
_BRACKET_ID_RE = re.compile(r"[0-9a-f]{12}")
_PRICE_PLACES = Decimal("0.0001")
UTC = timezone.utc


class LedgerError(RuntimeError):
    """The execution ledger refused a write; nothing was sent."""


class DecisionAlreadyClaimed(LedgerError):
    """The decision already has an outbox row: it is never processed twice."""


class RiskBookRefusal(LedgerError):
    """The shared risk book, re-read inside the staging transaction, refused the trade."""

    def __init__(self, check: RiskBookCheck) -> None:
        self.check = check
        super().__init__(f"{check.code}: {check.detail}")


def book_id(account: str, session_date: date) -> str:
    """The shared risk book: one per account and New York session date."""
    return f"{account}:{session_date.isoformat()}"


def new_bracket_id() -> str:
    return uuid4().hex[:12]


def order_ref(bracket_id: str, leg: LegCode) -> str:
    """The IBKR orderRef of one engine leg: ``at1:<bracket>:<P|T|S|F>``."""
    if not _BRACKET_ID_RE.fullmatch(bracket_id) or leg not in LEG_CODES:
        raise ValueError("invalid bracket id or leg code")
    return f"{ORDER_REF_PREFIX}{bracket_id}:{leg}"


def parse_order_ref(ref: object) -> tuple[str, LegCode] | None:
    """``(bracket id, leg)`` of an engine orderRef, else ``None`` (manual:, owner TWS orders, ...)."""
    if not isinstance(ref, str) or not ref.startswith(ORDER_REF_PREFIX):
        return None
    bracket_id, separator, leg = ref[len(ORDER_REF_PREFIX):].rpartition(":")
    if not separator or leg not in LEG_CODES or not _BRACKET_ID_RE.fullmatch(bracket_id):
        return None
    return bracket_id, leg  # type: ignore[return-value]


def _ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ledger timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _from_ts(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _dec(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass(slots=True)
class LegRecord:
    id: str
    bracket_id: str
    leg: LegCode
    client_id: int
    order_id: int
    order_ref: str
    action: Literal["BUY", "SELL"]
    order_type: Literal["LMT", "STP", "MKT"]
    tif: Literal["DAY", "GTC"]
    quantity: int
    price: Decimal | None
    parent_order_id: int | None
    transmit: bool
    state: LegState
    created_at: datetime
    broker_status: str | None = None
    perm_id: int | None = None
    filled: int = 0
    working_quantity: int | None = None
    working_price: Decimal | None = None


@dataclass(slots=True)
class BracketRecord:
    """One native IBKR bracket: immutable intent plus the engine's observations."""

    id: str
    decision_id: str
    account: str
    client_id: int
    session_date: date
    strategy_name: str
    symbol: str
    con_id: int
    primary_exchange: str
    quantity: int
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    setup_fingerprint: str | None
    state: BracketState
    created_at: datetime
    legs: dict[str, LegRecord] = field(default_factory=dict)
    entry_filled: int = 0
    avg_entry: Decimal | None = None
    exit_filled: int = 0
    avg_exit: Decimal | None = None
    exit_reason: str | None = None
    realized_pnl: Decimal = Decimal("0")
    commissions: Decimal = Decimal("0")
    parent_cancel_requested: bool = False
    resized_qty: int = 0
    flatten_attempted: bool = False
    flatten_state: FlattenState = "none"
    flatten_price: Decimal | None = None
    attention_reason: str | None = None
    attention_cleared_at: datetime | None = None

    @property
    def book_id(self) -> str:
        return book_id(self.account, self.session_date)

    @property
    def net_held(self) -> int:
        """Shares held per the engine's fills; negative means exits exceeded entries."""
        return self.entry_filled - self.exit_filled

    @property
    def remaining(self) -> int:
        return max(0, self.net_held)

    @property
    def risk_amount(self) -> Decimal:
        return Decimal(self.quantity) * (self.entry_price - self.stop_price)

    def leg(self, code: LegCode) -> LegRecord | None:
        return self.legs.get(code)

    def order_id(self, code: LegCode) -> int | None:
        leg = self.legs.get(code)
        return None if leg is None else leg.order_id

    def parent_dead(self) -> bool:
        parent = self.legs.get("P")
        return parent is not None and parent.broker_status in PARENT_DEAD_STATUSES

    def book_position(self) -> BookPosition:
        """What this bracket claims from the shared risk book right now."""
        if self.state in ("closed", "cancelled", "unknown") or self.attention_cleared_at is not None:
            return BookPosition(symbol=self.symbol, open_risk=Decimal("0"), holds_slot=False)
        pending = 0 if self.parent_dead() else max(0, self.quantity - self.entry_filled)
        risk = position_risk(
            held=self.remaining, average_entry=self.avg_entry, pending=pending,
            entry=self.entry_price, stop=self.stop_price,
        )
        # needs_attention keeps its slot until reconciliation sees it flat (bridge lesson M4).
        return BookPosition(symbol=self.symbol, open_risk=risk, holds_slot=True)


@dataclass(frozen=True, slots=True)
class FillRecord:
    """One IBKR execution of an engine leg."""

    exec_id: str
    exec_base: str
    exec_version: int
    leg_id: str
    order_id: int
    side: str | None
    shares: int
    price: Decimal
    account: str | None
    exec_time: str | None
    commission: Decimal | None = None
    commission_currency: str | None = None


@dataclass(frozen=True, slots=True)
class FillWrite:
    inserted: tuple[str, ...] = ()
    commissions_added: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FillTotals:
    """A leg's effective fills: the newest correction of each execution only."""

    shares: int
    value: Decimal
    commission: Decimal

    @property
    def average(self) -> Decimal | None:
        return None if self.shares <= 0 else (self.value / self.shares).quantize(_PRICE_PLACES)


@dataclass(frozen=True, slots=True)
class OutboxRow:
    decision_id: str
    book_id: str
    strategy_name: str
    symbol: str
    state: str
    reason_code: str | None
    reason: str | None
    bracket_id: str | None


@dataclass(frozen=True, slots=True)
class EventRow:
    seq: int
    book_id: str
    bracket_id: str | None
    leg_id: str | None
    kind: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReservationRow:
    id: str
    book_id: str
    decision_id: str
    bracket_id: str
    symbol: str
    risk_amount: Decimal
    notional_amount: Decimal
    state: str


_BRACKET_COLUMNS = (
    "id", "decision_id", "account", "client_id", "session_date", "strategy_name", "symbol",
    "con_id", "primary_exchange", "quantity", "entry_price", "stop_price", "target_price",
    "setup_fingerprint", "state", "created_at", "entry_filled", "avg_entry", "exit_filled",
    "avg_exit", "exit_reason", "realized_pnl", "commissions", "parent_cancel_requested",
    "resized_qty", "flatten_attempted", "flatten_state", "flatten_price", "attention_reason",
    "attention_cleared_at",
)
_LEG_COLUMNS = (
    "id", "bracket_id", "leg", "client_id", "order_id", "order_ref", "action", "order_type", "tif",
    "quantity", "price", "parent_order_id", "transmit", "state", "created_at", "broker_status",
    "perm_id", "filled", "working_quantity", "working_price",
)
# Brackets the engine must keep in memory: everything of today's book, plus any
# earlier bracket that may still hold shares or orders at IBKR.
_LOAD_FILTER = (
    "account=? AND (session_date=? OR state IN ('sending','working','open')"
    " OR (state='needs_attention' AND attention_cleared_at IS NULL))"
)


def _bracket(row: Sequence[object]) -> BracketRecord:
    values = dict(zip(_BRACKET_COLUMNS, row, strict=True))
    return BracketRecord(
        id=str(values["id"]),
        decision_id=str(values["decision_id"]),
        account=str(values["account"]),
        client_id=int(values["client_id"]),
        session_date=date.fromisoformat(str(values["session_date"])),
        strategy_name=str(values["strategy_name"]),
        symbol=str(values["symbol"]),
        con_id=int(values["con_id"]),
        primary_exchange=str(values["primary_exchange"]),
        quantity=int(values["quantity"]),
        entry_price=Decimal(str(values["entry_price"])),
        stop_price=Decimal(str(values["stop_price"])),
        target_price=Decimal(str(values["target_price"])),
        setup_fingerprint=None if values["setup_fingerprint"] is None else str(values["setup_fingerprint"]),
        state=str(values["state"]),  # type: ignore[arg-type]
        created_at=_from_ts(values["created_at"]),
        entry_filled=int(values["entry_filled"]),
        avg_entry=_dec(values["avg_entry"]),
        exit_filled=int(values["exit_filled"]),
        avg_exit=_dec(values["avg_exit"]),
        exit_reason=None if values["exit_reason"] is None else str(values["exit_reason"]),
        realized_pnl=Decimal(str(values["realized_pnl"])),
        commissions=Decimal(str(values["commissions"])),
        parent_cancel_requested=bool(values["parent_cancel_requested"]),
        resized_qty=int(values["resized_qty"]),
        flatten_attempted=bool(values["flatten_attempted"]),
        flatten_state=str(values["flatten_state"]),  # type: ignore[arg-type]
        flatten_price=_dec(values["flatten_price"]),
        attention_reason=None if values["attention_reason"] is None else str(values["attention_reason"]),
        attention_cleared_at=(
            None if values["attention_cleared_at"] is None else _from_ts(values["attention_cleared_at"])
        ),
    )


def _leg(row: Sequence[object]) -> LegRecord:
    values = dict(zip(_LEG_COLUMNS, row, strict=True))
    return LegRecord(
        id=str(values["id"]),
        bracket_id=str(values["bracket_id"]),
        leg=str(values["leg"]),  # type: ignore[arg-type]
        client_id=int(values["client_id"]),
        order_id=int(values["order_id"]),
        order_ref=str(values["order_ref"]),
        action=str(values["action"]),  # type: ignore[arg-type]
        order_type=str(values["order_type"]),  # type: ignore[arg-type]
        tif=str(values["tif"]),  # type: ignore[arg-type]
        quantity=int(values["quantity"]),
        price=_dec(values["price"]),
        parent_order_id=None if values["parent_order_id"] is None else int(values["parent_order_id"]),
        transmit=bool(values["transmit"]),
        state=str(values["state"]),  # type: ignore[arg-type]
        created_at=_from_ts(values["created_at"]),
        broker_status=None if values["broker_status"] is None else str(values["broker_status"]),
        perm_id=None if values["perm_id"] is None else int(values["perm_id"]),
        filled=int(values["filled"]),
        working_quantity=None if values["working_quantity"] is None else int(values["working_quantity"]),
        working_price=_dec(values["working_price"]),
    )


async def _load_brackets(db: aiosqlite.Connection, account: str, session_date: date) -> list[BracketRecord]:
    async with db.execute(
        f"SELECT {', '.join(_BRACKET_COLUMNS)} FROM autotrader_exec_brackets WHERE {_LOAD_FILTER}"
        " ORDER BY created_at, id",
        (account, session_date.isoformat()),
    ) as cursor:
        brackets = [_bracket(row) for row in await cursor.fetchall()]
    if not brackets:
        return []
    by_id = {bracket.id: bracket for bracket in brackets}
    placeholders = ", ".join("?" for _ in by_id)
    async with db.execute(
        f"SELECT {', '.join(_LEG_COLUMNS)} FROM autotrader_exec_legs WHERE bracket_id IN ({placeholders})",
        tuple(by_id),
    ) as cursor:
        for row in await cursor.fetchall():
            leg = _leg(row)
            by_id[leg.bracket_id].legs[leg.leg] = leg
    return brackets


def risk_book_from(
    brackets: Iterable[BracketRecord],
    *,
    session_date: date,
    net_liquidation: Decimal | None,
    unmanaged_symbols: frozenset[str] = frozenset(),
) -> RiskBook:
    """The shared book: today's realized P&L plus every bracket's open claim."""
    loaded = list(brackets)
    return RiskBook(
        net_liquidation=net_liquidation,
        realized_pnl=sum(
            (bracket.realized_pnl for bracket in loaded if bracket.session_date == session_date),
            Decimal("0"),
        ),
        positions=tuple(bracket.book_position() for bracket in loaded),
        unmanaged_symbols=unmanaged_symbols,
    )


class ExecutionLedger:
    """Async repository over the execution-ledger tables (``db.core`` connection)."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> str:
        return _ts(self._clock())

    # ---- intake -----------------------------------------------------------
    async def processed_decision_ids(self, account: str, session_date: date) -> frozenset[str]:
        async with get_db() as db:
            async with db.execute(
                "SELECT decision_id FROM autotrader_exec_outbox WHERE book_id=?",
                (book_id(account, session_date),),
            ) as cursor:
                return frozenset(str(row[0]) for row in await cursor.fetchall())

    async def record_skip(
        self, *, decision_id: str, account: str, session_date: date, strategy_name: str, symbol: str,
        code: str, reason: str,
    ) -> bool:
        """Close a decision as skipped; False when it already has an outbox row."""
        now = self._now()
        book = book_id(account, session_date)
        try:
            async with transaction() as db:
                await db.execute(
                    """INSERT INTO autotrader_exec_outbox
                    (decision_id, book_id, account, session_date, strategy_name, symbol, state,
                     reason_code, reason, bracket_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'skipped', ?, ?, NULL, ?, ?)""",
                    (decision_id, book, account, session_date.isoformat(), strategy_name, symbol, code,
                     reason[:500], now, now),
                )
                await self._event(db, book=book, kind="skip", now=now,
                                  payload={"decision_id": decision_id, "code": code, "reason": reason[:500]})
        except aiosqlite.IntegrityError:
            if await self.outbox_row(decision_id) is not None:
                return False
            raise
        return True

    async def stage_bracket(
        self,
        record: BracketRecord,
        *,
        net_liquidation: Decimal,
        unmanaged_symbols: frozenset[str] = frozenset(),
        payload: Mapping[str, object] | None = None,
    ) -> str:
        """Claim the decision and persist the whole bracket before any order leaves.

        The shared risk book is re-read and re-checked inside the same
        IMMEDIATE transaction, so two writers can never both spend it.
        Returns the reservation id.
        """
        if record.state != "sending" or tuple(sorted(record.legs)) != ("P", "S", "T"):
            raise LedgerError("a staged bracket is sending and has exactly P, T and S legs")
        if any(leg.state != "staged" for leg in record.legs.values()):
            raise LedgerError("every leg of a staged bracket must be staged")
        now = self._now()
        reservation_id = uuid4().hex
        book = record.book_id
        trade = NewTrade(symbol=record.symbol, quantity=record.quantity, entry=record.entry_price,
                         stop=record.stop_price)
        async with transaction() as db:
            async with db.execute(
                "SELECT 1 FROM autotrader_exec_outbox WHERE decision_id=?", (record.decision_id,)
            ) as cursor:
                if await cursor.fetchone() is not None:
                    raise DecisionAlreadyClaimed(f"decision {record.decision_id} is already in the outbox")
            current = await _load_brackets(db, record.account, record.session_date)
            check = evaluate_new_trade(
                risk_book_from(current, session_date=record.session_date, net_liquidation=net_liquidation,
                               unmanaged_symbols=unmanaged_symbols),
                trade,
            )
            if not check.approved:
                raise RiskBookRefusal(check)
            await db.execute(
                """INSERT INTO autotrader_exec_outbox
                (decision_id, book_id, account, session_date, strategy_name, symbol, state,
                 reason_code, reason, bracket_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'sending', NULL, NULL, ?, ?, ?)""",
                (record.decision_id, book, record.account, record.session_date.isoformat(),
                 record.strategy_name, record.symbol, record.id, now, now),
            )
            await db.execute(
                """INSERT INTO autotrader_exec_brackets
                (id, decision_id, book_id, account, client_id, session_date, strategy_name, symbol,
                 con_id, primary_exchange, quantity, entry_price, stop_price, target_price,
                 setup_fingerprint, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sending', ?, ?)""",
                (record.id, record.decision_id, book, record.account, record.client_id,
                 record.session_date.isoformat(), record.strategy_name, record.symbol, record.con_id,
                 record.primary_exchange, record.quantity, str(record.entry_price), str(record.stop_price),
                 str(record.target_price), record.setup_fingerprint, _ts(record.created_at), now),
            )
            for code in BRACKET_LEGS:
                await self._insert_leg(db, record.legs[code], now)
            await db.execute(
                """INSERT INTO autotrader_exec_risk_reservations
                (id, book_id, account, session_date, decision_id, bracket_id, symbol, risk_amount,
                 notional_amount, net_liquidation, state, created_at, settled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, NULL)""",
                (reservation_id, book, record.account, record.session_date.isoformat(), record.decision_id,
                 record.id, record.symbol, str(trade.risk), str(trade.notional), str(net_liquidation), now),
            )
            await self._event(
                db, book=book, bracket_id=record.id, kind="claimed", now=now,
                payload={
                    "decision_id": record.decision_id, "reservation_id": reservation_id,
                    "order_ids": {code: record.legs[code].order_id for code in BRACKET_LEGS},
                    "risk": str(trade.risk), "open_risk": str(check.open_risk),
                    "realized_loss": str(check.realized_loss), **dict(payload or {}),
                },
            )
        return reservation_id

    async def mark_sent(self, record: BracketRecord, *, payload: Mapping[str, object] | None = None) -> None:
        """Every leg reached placeOrder (or IBKR reported it after a restart): consume the reservation."""
        now = self._now()
        async with transaction() as db:
            await self._settle_sending(db, record, outbox="sent", bracket="working", leg_state="sent",
                                       reservation="consumed", reason_code=None, reason=None, now=now)
            await self._event(db, book=record.book_id, bracket_id=record.id, kind="placed", now=now,
                              payload=dict(payload or {}))
        record.state = "working"
        for code in BRACKET_LEGS:
            record.legs[code].state = "sent"

    async def mark_unknown(self, record: BracketRecord, *, reason: str) -> None:
        """An in-flight bracket IBKR does not know after a restart: never resent."""
        now = self._now()
        async with transaction() as db:
            await self._settle_sending(db, record, outbox="unknown", bracket="unknown", leg_state="unknown",
                                       reservation="released", reason_code="not_at_ibkr", reason=reason, now=now)
            await self._event(db, book=record.book_id, bracket_id=record.id, kind="unknown", now=now,
                              payload={"reason": reason})
        record.state = "unknown"
        for code in BRACKET_LEGS:
            record.legs[code].state = "unknown"

    async def _settle_sending(
        self, db: aiosqlite.Connection, record: BracketRecord, *, outbox: str, bracket: str, leg_state: str,
        reservation: str, reason_code: str | None, reason: str | None, now: str,
    ) -> None:
        cursor = await db.execute(
            "UPDATE autotrader_exec_outbox SET state=?, reason_code=?, reason=?, updated_at=?"
            " WHERE decision_id=? AND bracket_id=? AND state='sending'",
            (outbox, reason_code, reason, now, record.decision_id, record.id),
        )
        if cursor.rowcount != 1:
            raise LedgerError(f"bracket {record.id} is not sending")
        await db.execute(
            "UPDATE autotrader_exec_brackets SET state=?, updated_at=? WHERE id=? AND state='sending'",
            (bracket, now, record.id),
        )
        await db.execute(
            "UPDATE autotrader_exec_legs SET state=?, updated_at=?"
            " WHERE bracket_id=? AND leg IN ('P','T','S') AND state='staged'",
            (leg_state, now, record.id),
        )
        await db.execute(
            "UPDATE autotrader_exec_risk_reservations SET state=?, settled_at=?"
            " WHERE bracket_id=? AND state='reserved'",
            (reservation, now, record.id),
        )

    # ---- bracket state ----------------------------------------------------
    async def load_brackets(self, account: str, session_date: date) -> list[BracketRecord]:
        async with get_db() as db:
            return await _load_brackets(db, account, session_date)

    async def save_bracket(self, record: BracketRecord) -> None:
        """Persist the engine's observations; the triggers refuse any unsafe change."""
        now = self._now()
        async with transaction() as db:
            await db.execute(
                """UPDATE autotrader_exec_brackets SET
                    state=?, entry_filled=?, avg_entry=?, exit_filled=?, avg_exit=?, exit_reason=?,
                    realized_pnl=?, commissions=?, parent_cancel_requested=?, resized_qty=?,
                    flatten_attempted=?, flatten_state=?, flatten_price=?, attention_reason=?,
                    attention_cleared_at=?, updated_at=?
                WHERE id=?""",
                (record.state, record.entry_filled, _text(record.avg_entry), record.exit_filled,
                 _text(record.avg_exit), record.exit_reason, str(record.realized_pnl), str(record.commissions),
                 int(record.parent_cancel_requested), record.resized_qty, int(record.flatten_attempted),
                 record.flatten_state, _text(record.flatten_price), record.attention_reason,
                 None if record.attention_cleared_at is None else _ts(record.attention_cleared_at), now,
                 record.id),
            )
            for leg in record.legs.values():
                await db.execute(
                    """UPDATE autotrader_exec_legs SET broker_status=?, perm_id=?, filled=?,
                        working_quantity=?, working_price=?, updated_at=? WHERE id=?""",
                    (leg.broker_status, leg.perm_id, leg.filled, leg.working_quantity,
                     _text(leg.working_price), now, leg.id),
                )

    async def add_flatten_leg(self, record: BracketRecord, leg: LegRecord, *, payload: Mapping[str, object]) -> None:
        """Persist the fallback market sell and the one-way 'selling' state before it is sent."""
        if leg.leg != "F" or leg.state != "staged" or leg.bracket_id != record.id:
            raise LedgerError("only a staged F leg of this bracket can be added")
        now = self._now()
        async with transaction() as db:
            cursor = await db.execute(
                "UPDATE autotrader_exec_brackets SET flatten_attempted=1, flatten_state='selling', updated_at=?"
                " WHERE id=? AND flatten_state='cancelling'",
                (now, record.id),
            )
            if cursor.rowcount != 1:
                raise LedgerError(f"bracket {record.id} is not waiting for a fallback sell")
            await self._insert_leg(db, leg, now)
            await self._event(db, book=record.book_id, bracket_id=record.id, leg_id=leg.id,
                              kind="flatten_sell_staged", now=now, payload=dict(payload))
        record.flatten_state = "selling"
        record.legs["F"] = leg

    async def settle_leg(self, leg: LegRecord, *, state: Literal["sent", "unknown"], book: str) -> None:
        now = self._now()
        async with transaction() as db:
            cursor = await db.execute(
                "UPDATE autotrader_exec_legs SET state=?, updated_at=? WHERE id=? AND state='staged'",
                (state, now, leg.id),
            )
            if cursor.rowcount != 1:
                raise LedgerError(f"leg {leg.id} is not staged")
            await self._event(db, book=book, bracket_id=leg.bracket_id, leg_id=leg.id,
                              kind=f"leg_{state}", now=now, payload={"order_id": leg.order_id})
        leg.state = state

    async def _insert_leg(self, db: aiosqlite.Connection, leg: LegRecord, now: str) -> None:
        await db.execute(
            """INSERT INTO autotrader_exec_legs
            (id, bracket_id, leg, client_id, order_id, order_ref, action, order_type, tif, quantity,
             price, parent_order_id, transmit, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?)""",
            (leg.id, leg.bracket_id, leg.leg, leg.client_id, leg.order_id, leg.order_ref, leg.action,
             leg.order_type, leg.tif, leg.quantity, _text(leg.price), leg.parent_order_id,
             int(leg.transmit), _ts(leg.created_at), now),
        )

    async def max_order_id(self, client_id: int) -> int:
        async with get_db() as db:
            async with db.execute(
                "SELECT COALESCE(MAX(order_id), 0) FROM autotrader_exec_legs WHERE client_id=?", (client_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def find_refs(self, refs: Iterable[str]) -> dict[str, tuple[str, str, int]]:
        """``orderRef -> (bracket id, bracket state, order id)`` for refs the ledger knows."""
        wanted = sorted({ref for ref in refs if parse_order_ref(ref) is not None})
        if not wanted:
            return {}
        placeholders = ", ".join("?" for _ in wanted)
        async with get_db() as db:
            async with db.execute(
                f"""SELECT leg.order_ref, leg.bracket_id, bracket.state, leg.order_id
                FROM autotrader_exec_legs leg JOIN autotrader_exec_brackets bracket ON bracket.id=leg.bracket_id
                WHERE leg.order_ref IN ({placeholders})""",
                tuple(wanted),
            ) as cursor:
                return {str(row[0]): (str(row[1]), str(row[2]), int(row[3])) for row in await cursor.fetchall()}

    # ---- fills ------------------------------------------------------------
    async def record_fills(self, fills: Sequence[FillRecord]) -> FillWrite:
        """Insert new executions (deduplicated by execId) and late commission reports."""
        if not fills:
            return FillWrite()
        now = self._now()
        inserted: list[str] = []
        commissions: list[str] = []
        conflicts: list[str] = []
        async with transaction() as db:
            for fill in fills:
                async with db.execute(
                    "SELECT leg_id, shares, price, commission FROM autotrader_exec_fills WHERE exec_id=?",
                    (fill.exec_id,),
                ) as cursor:
                    existing = await cursor.fetchone()
                if existing is None:
                    await db.execute(
                        """INSERT INTO autotrader_exec_fills
                        (exec_id, exec_base, exec_version, leg_id, order_id, side, shares, price, account,
                         exec_time, commission, commission_currency, received_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (fill.exec_id, fill.exec_base, fill.exec_version, fill.leg_id, fill.order_id, fill.side,
                         fill.shares, str(fill.price), fill.account, fill.exec_time, _text(fill.commission),
                         fill.commission_currency, now),
                    )
                    inserted.append(fill.exec_id)
                    continue
                if (str(existing[0]), int(existing[1]), Decimal(str(existing[2]))) != (
                    fill.leg_id, fill.shares, fill.price
                ):
                    conflicts.append(fill.exec_id)  # the same execId reported differently
                    continue
                if existing[3] is None and fill.commission is not None:
                    await db.execute(
                        "UPDATE autotrader_exec_fills SET commission=?, commission_currency=?"
                        " WHERE exec_id=? AND commission IS NULL",
                        (str(fill.commission), fill.commission_currency, fill.exec_id),
                    )
                    commissions.append(fill.exec_id)
        return FillWrite(tuple(inserted), tuple(commissions), tuple(conflicts))

    async def load_fills(self, leg_ids: Iterable[str]) -> list[FillRecord]:
        """Every stored execution (all corrections) of these legs."""
        wanted = sorted(set(leg_ids))
        if not wanted:
            return []
        placeholders = ", ".join("?" for _ in wanted)
        async with get_db() as db:
            async with db.execute(
                f"""SELECT exec_id, exec_base, exec_version, leg_id, order_id, side, shares, price, account,
                          exec_time, commission, commission_currency
                FROM autotrader_exec_fills WHERE leg_id IN ({placeholders}) ORDER BY exec_base, exec_version""",
                tuple(wanted),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            FillRecord(
                exec_id=str(row[0]), exec_base=str(row[1]), exec_version=int(row[2]), leg_id=str(row[3]),
                order_id=int(row[4]), side=None if row[5] is None else str(row[5]), shares=int(row[6]),
                price=Decimal(str(row[7])), account=None if row[8] is None else str(row[8]),
                exec_time=None if row[9] is None else str(row[9]), commission=_dec(row[10]),
                commission_currency=None if row[11] is None else str(row[11]),
            )
            for row in rows
        ]

    async def fill_totals(self, leg_ids: Iterable[str]) -> dict[str, FillTotals]:
        wanted = sorted(set(leg_ids))
        if not wanted:
            return {}
        placeholders = ", ".join("?" for _ in wanted)
        async with get_db() as db:
            async with db.execute(
                f"""SELECT leg_id, exec_base, exec_version, shares, price, commission
                FROM autotrader_exec_fills WHERE leg_id IN ({placeholders})""",
                tuple(wanted),
            ) as cursor:
                rows = await cursor.fetchall()
        newest: dict[tuple[str, str], tuple[int, int, Decimal, Decimal]] = {}
        for leg_id, base, version, shares, price, commission in rows:
            key = (str(leg_id), str(base))
            current = newest.get(key)
            if current is None or int(version) > current[0]:
                newest[key] = (int(version), int(shares), Decimal(str(price)),
                               Decimal(str(commission)) if commission is not None else Decimal("0"))
        totals: dict[str, FillTotals] = {}
        for (leg_id, _), (_, shares, price, commission) in newest.items():
            previous = totals.get(leg_id, FillTotals(0, Decimal("0"), Decimal("0")))
            totals[leg_id] = FillTotals(
                shares=previous.shares + shares,
                value=previous.value + Decimal(shares) * price,
                commission=previous.commission + commission,
            )
        return totals

    # ---- events and reports -------------------------------------------------
    async def append_event(
        self, *, book: str, kind: str, payload: Mapping[str, object], bracket_id: str | None = None,
        leg_id: str | None = None,
    ) -> None:
        now = self._now()
        async with transaction() as db:
            await self._event(db, book=book, kind=kind, payload=dict(payload), bracket_id=bracket_id,
                              leg_id=leg_id, now=now)

    @staticmethod
    async def _event(
        db: aiosqlite.Connection, *, book: str, kind: str, payload: Mapping[str, object], now: str,
        bracket_id: str | None = None, leg_id: str | None = None,
    ) -> None:
        await db.execute(
            "INSERT INTO autotrader_exec_events (book_id, bracket_id, leg_id, kind, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (book, bracket_id, leg_id, kind, json.dumps(payload, sort_keys=True, default=str), now),
        )

    async def events(self, *, book: str | None = None, bracket_id: str | None = None) -> list[EventRow]:
        clauses, params = [], []
        if book is not None:
            clauses.append("book_id=?")
            params.append(book)
        if bracket_id is not None:
            clauses.append("bracket_id=?")
            params.append(bracket_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with get_db() as db:
            async with db.execute(
                "SELECT seq, book_id, bracket_id, leg_id, kind, payload_json, created_at"
                f" FROM autotrader_exec_events{where} ORDER BY seq",
                tuple(params),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            EventRow(seq=int(row[0]), book_id=str(row[1]), bracket_id=None if row[2] is None else str(row[2]),
                     leg_id=None if row[3] is None else str(row[3]), kind=str(row[4]),
                     payload=json.loads(str(row[5])), created_at=_from_ts(row[6]))
            for row in rows
        ]

    async def outbox_row(self, decision_id: str) -> OutboxRow | None:
        async with get_db() as db:
            async with db.execute(
                "SELECT decision_id, book_id, strategy_name, symbol, state, reason_code, reason, bracket_id"
                " FROM autotrader_exec_outbox WHERE decision_id=?",
                (decision_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return OutboxRow(*(None if value is None else str(value) for value in row))  # type: ignore[arg-type]

    async def reservations(self, book: str) -> list[ReservationRow]:
        async with get_db() as db:
            async with db.execute(
                "SELECT id, book_id, decision_id, bracket_id, symbol, risk_amount, notional_amount, state"
                " FROM autotrader_exec_risk_reservations WHERE book_id=? ORDER BY created_at, id",
                (book,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            ReservationRow(id=str(row[0]), book_id=str(row[1]), decision_id=str(row[2]), bracket_id=str(row[3]),
                           symbol=str(row[4]), risk_amount=Decimal(str(row[5])),
                           notional_amount=Decimal(str(row[6])), state=str(row[7]))
            for row in rows
        ]


__all__ = [
    "BRACKET_LEGS",
    "EXIT_LEGS",
    "LEG_CODES",
    "ORDER_REF_PREFIX",
    "PARENT_DEAD_STATUSES",
    "BracketRecord",
    "BracketState",
    "DecisionAlreadyClaimed",
    "EventRow",
    "ExecutionLedger",
    "FillRecord",
    "FillTotals",
    "FillWrite",
    "FlattenState",
    "LedgerError",
    "LegCode",
    "LegRecord",
    "OutboxRow",
    "ReservationRow",
    "RiskBookRefusal",
    "book_id",
    "new_bracket_id",
    "order_ref",
    "parse_order_ref",
    "risk_book_from",
]
