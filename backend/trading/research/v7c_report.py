"""The v7c report: refused orders, stop slippage and P&L by source (plan P3-B3).

Bridge v7c (owner decisions D1 and D5 of 2026-10-18) refuses a risk-increasing
order, a bot entry or an owner buy, when the account's 3 % day risk budget or
its -2 % daily loss stop says no. Every refusal starts with its token (the
bridge: "the same string starts the bridge.log line, the bracket's
skip_reason, the manual row's reject_code and the page"). Nightly, read-only,
this module reports per source (bot or manual):

* blocked trades: how many, and what they would have done in R. A bot entry
  is joined decision id -> (run, setup fingerprint) to the signal tracker's
  outcome with the promoter's own join (``promotion_proposals.
  read_shadow_outcomes``); a setup the tracker says would not have filled
  would have done nothing (0 R, counted apart as well). A row without an
  outcome is counted as such and never as 0 R: an owner buy has no setup, so
  it never has one;
* stop slippage, per identity: for each closed bracket that exited at its
  stop, -realized / (filled x (average entry - intent stop)), the multiple of
  its planned risk the stop cost. n, median, p90 (nearest rank: always an
  observed exit, so a small sample is judged by its own worst ones) and max,
  and the token
  ``stop_slippage_above_factor`` when the p90 exceeds the bridge's
  STOP_SLIPPAGE_FACTOR. A bracket whose planned risk is zero or unknown is
  excluded and counted;
* P&L by source: the bot's closed brackets from the live R ledger.

The source of an order is its orderRef prefix (``order_source``): the bridge
tags every bot order ``autotrader:<IDENTITY>:<decision>`` and every owner
order ``manual:<row id>``.

What the bridge does not record reads "unknown" (``GAPS``), never a guess:
the owner's realized P&L, the stop price a bracket actually exited at, how a
bracket's exit shares split between its target and stop legs, and which
bridge version wrote a state file.

Inputs, each opened read-only: the bridge's state files (``live_ledger.
read_submitted``), the manual-order ledger and the decision/outcome tables
(``?mode=ro``). Nothing here writes a file, a row or a fence. The bridge's
tokens and constants below are copies: this module never imports the bridge,
so a test loads it and pins each copy equal.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from trading.research.live_ledger import STATE_FILE, LiveLedger, LiveTrade, read_submitted
from trading.storage.migrations import SCHEMA_READ_TIMEOUT_SECONDS

logger = logging.getLogger("autotrader.shadow.research")

REPORT_VERSION = "v7c-report-v1"
NEW_YORK = ZoneInfo("America/New_York")

# Sources, from the orderRef prefix the bridge puts on every order
# (paper_bridge.py ORDER_REF_PREFIX, MANUAL_REF_PREFIX; manual_orders.py
# ORDER_REF_PREFIX).
BOT = "bot"
MANUAL = "manual"
UNKNOWN_SOURCE = "unknown"
SOURCES = (BOT, MANUAL)
BOT_REF_PREFIX = "autotrader:"
MANUAL_REF_PREFIX = "manual:"
OWNER = "owner"  # what an owner order is reported under in place of an identity

# Bridge v7c's tokens and constants (paper_bridge.py, pinned by a test).
V7C_DAY_RISK_BUDGET = "v7c_day_risk_budget"
V7C_UNPROTECTED_POSITION = "v7c_unprotected_position"
V7C_WRONG_THREAD = "v7c_wrong_thread"
DAILY_LOSS_LIMIT = "daily_loss_limit"
V7C_BUDGET_OK = "v7c_budget_ok"
V7C_BUDGET_OVER = "v7c_budget_over"
V7C_STOP_WIDENED = "v7c_stop_widened"
V7C_BUDGET_INVARIANT_BROKEN = "v7c_budget_invariant_broken"
DAY_RISK_BUDGET_PCT = Decimal("3")
STOP_SLIPPAGE_FACTOR = Decimal("1.0")
# The refusals of a risk-increasing order (budget_check and the account's
# daily loss stop): a bracket's skip_reason starts "<token>:", a manual row's
# reject_code is the token itself.
BLOCK_TOKENS = (V7C_DAY_RISK_BUDGET, V7C_UNPROTECTED_POSITION, DAILY_LOSS_LIMIT, V7C_WRONG_THREAD)

# Report tokens.
STOP_SLIPPAGE_ABOVE_FACTOR = "stop_slippage_above_factor"
NO_V7C_DATA = "no v7c data"
NOT_COLLECTED = "not_collected"
HEADER = "V7C day risk budget (refused risk-increasing orders; would-have R from the signal tracker)"
FILLED = "filled"
NOT_FILLED = "not_filled"
NO_OUTCOME = "no_outcome"
MANUAL_LEDGER_READ = "read"
MANUAL_LEDGER_ABSENT = "absent"
MANUAL_LEDGER_TABLE = "autotrader_manual_orders"
STOP_EXIT_REASON = "stop"

# What the bridge does not record today, so the report says "unknown" for it.
GAPS = (
    "owner realized P&L: not recorded (the bridge computes it in memory from execDetails and the"
    " position's average cost; no state field, ledger column or log line carries it)",
    "the stop price a bracket exited at: not recorded (the state keeps the intent stop only), so stop"
    " slippage is measured against the intent stop and an owner-raised stop reads as less slippage",
    "a bracket's exit split between its target and stop legs: not recorded (exit_reason names the last"
    " leg that filled), so a mixed exit reads as a stop exit",
    "the bridge version per session: not in the state file (its schema paper-bridge-state.v5 names none),"
    " so a session without a refusal reads \"no v7c data\" whichever bridge ran it",
)

_ZERO = Decimal("0")
_RATIO_QUANTUM = Decimal("0.0001")
_TEXT_QUANTUM = Decimal("0.01")
_MANUAL_BLOCKS_SQL = f"""
    SELECT id, created_at, symbol, reject_code
    FROM {MANUAL_LEDGER_TABLE}
    WHERE status = 'rejected' AND reject_code IN ({", ".join("?" for _ in BLOCK_TOKENS)})
    ORDER BY created_at, id
"""


# --- pure helpers ---------------------------------------------------------------------


def order_source(order_ref: object) -> str | None:
    """``bot`` for the bridge's own orderRef, ``manual`` for an owner order's, else None."""
    if not isinstance(order_ref, str):
        return None
    if order_ref.startswith(BOT_REF_PREFIX) and len(order_ref) > len(BOT_REF_PREFIX):
        return BOT
    if order_ref.startswith(MANUAL_REF_PREFIX) and len(order_ref) > len(MANUAL_REF_PREFIX):
        return MANUAL
    return None


def bot_order_ref(identity: str, decision_id: str) -> str:
    """The orderRef the bridge puts on a bracket's three orders (``Bridge.ref``)."""
    return f"{BOT_REF_PREFIX}{identity.strip()}:{decision_id[:12]}"


def manual_order_ref(row_id: str) -> str:
    """The orderRef of every order of one manual-order row (``manual_orders.order_ref``)."""
    return f"{MANUAL_REF_PREFIX}{row_id}"


def block_token(reason: object) -> str | None:
    """The v7c token a refusal starts with: ``<token>: ...`` (a skip_reason) or the
    token alone (a reject_code); None for anything else, a pre-v7c reason included."""
    if not isinstance(reason, str):
        return None
    head = reason.split(":", 1)[0].strip()
    return head if head in BLOCK_TOKENS else None


def nearest_rank(ordered: Sequence[Decimal], share: Decimal) -> Decimal | None:
    """Nearest-rank percentile of sorted samples (``share`` in (0, 1]): an observed value."""
    if not ordered:
        return None
    rank = math.ceil(share * len(ordered))  # exact: Decimal implements __ceil__
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


def median(ordered: Sequence[Decimal]) -> Decimal | None:
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def stop_ratio(*, filled: int, exit_filled: int, avg_entry: Decimal, avg_exit: Decimal,
               stop: Decimal) -> Decimal | None:
    """-realized / (filled x (avg_entry - stop)) of a closed stop exit; None when its risk is
    zero or unknown. Realized is the bridge's own formula, (avg_exit - avg_entry) x exit shares,
    before its rounding to the cent."""
    if filled <= 0 or exit_filled != filled or avg_entry <= 0 or avg_exit <= 0 or stop <= 0:
        return None
    risk = Decimal(filled) * (avg_entry - stop)
    if risk <= 0:
        return None
    realized = (avg_exit - avg_entry) * exit_filled
    return -realized / risk


def _r(value: Decimal) -> str:
    return f"{value.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN):+}R"


def _times(value: Decimal) -> str:
    return f"{value.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN)}x"


def _money(value: Decimal) -> str:
    return f"{value.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN):+}"


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_EVEN))


# --- the report -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BlockedOrder:
    """One refused risk-increasing order and what it would have done."""

    session_date: date | None  # None: a manual row whose created_at is unreadable
    source: str  # bot | manual (from its orderRef); unknown never happens by construction
    token: str
    order_ref: str
    identity: str  # the bot's identity; OWNER for a manual row
    symbol: str
    decision_id: str | None  # bot only: the decision the join starts from
    outcome: str  # filled | not_filled | no_outcome
    would_have_r: Decimal | None  # the tracker's R when filled, 0 when not, None without an outcome

    def payload(self) -> dict[str, object]:
        return {
            "date": None if self.session_date is None else self.session_date.isoformat(),
            "source": self.source,
            "token": self.token,
            "order_ref": self.order_ref,
            "identity": self.identity,
            "symbol": self.symbol,
            "decision_id": self.decision_id,
            "outcome": self.outcome,
            "would_have_r": _text(self.would_have_r),
        }


@dataclass(frozen=True, slots=True)
class SourceTally:
    """Blocked orders of one source: the R of those with an outcome, the rest counted."""

    source: str
    blocked: int = 0
    with_outcome: int = 0
    not_filled: int = 0
    no_outcome: int = 0
    would_have_r: Decimal | None = None  # None while no row has an outcome

    def payload(self) -> dict[str, object]:
        return {
            "blocked": self.blocked,
            "with_outcome": self.with_outcome,
            "not_filled": self.not_filled,
            "no_outcome": self.no_outcome,
            "would_have_r": _text(self.would_have_r),
        }


def tally(rows: Iterable[BlockedOrder], source: str) -> SourceTally:
    mine = [row for row in rows if row.source == source]
    known = [row.would_have_r for row in mine if row.outcome != NO_OUTCOME and row.would_have_r is not None]
    return SourceTally(
        source=source,
        blocked=len(mine),
        with_outcome=len(known),
        not_filled=sum(1 for row in mine if row.outcome == NOT_FILLED),
        no_outcome=len(mine) - len(known),
        would_have_r=sum(known, _ZERO) if known else None,
    )


@dataclass(frozen=True, slots=True)
class StopExit:
    session_date: date
    decision_id: str
    identity: str
    symbol: str
    ratio: Decimal | None  # None: excluded (zero or unknown risk)
    why: str = ""

    def payload(self) -> dict[str, object]:
        return {
            "date": self.session_date.isoformat(),
            "decision_id": self.decision_id,
            "identity": self.identity,
            "symbol": self.symbol,
            "ratio": _text(self.ratio),
            "excluded": self.why or None,
        }


@dataclass(frozen=True, slots=True)
class IdentitySlippage:
    """One identity's stop exits as multiples of their planned risk."""

    identity: str
    ratios: tuple[Decimal, ...]  # sorted
    excluded: int
    factor: Decimal = STOP_SLIPPAGE_FACTOR

    @property
    def n(self) -> int:
        return len(self.ratios)

    @property
    def median(self) -> Decimal | None:
        return median(self.ratios)

    @property
    def p90(self) -> Decimal | None:
        return nearest_rank(self.ratios, Decimal("0.9"))

    @property
    def max(self) -> Decimal | None:
        return self.ratios[-1] if self.ratios else None

    @property
    def above_factor(self) -> bool:
        p90 = self.p90
        return p90 is not None and p90 > self.factor

    def payload(self) -> dict[str, object]:
        return {
            "n": self.n,
            "median": _text(self.median),
            "p90": _text(self.p90),
            "max": _text(self.max),
            "excluded": self.excluded,
            "flag": STOP_SLIPPAGE_ABOVE_FACTOR if self.above_factor else None,
        }

    def line(self) -> str | None:
        p90 = self.p90
        if p90 is None:
            return None
        compare = ">" if self.above_factor else "<="
        return f"STOP SLIPPAGE {self.identity} p90 {_times(p90)} (n={self.n}) {compare} {self.factor}"


def slippage_by_identity(exits: Iterable[StopExit], factor: Decimal = STOP_SLIPPAGE_FACTOR
                         ) -> tuple[IdentitySlippage, ...]:
    grouped: dict[str, tuple[list[Decimal], list[StopExit]]] = {}
    for item in exits:
        ratios, excluded = grouped.setdefault(item.identity, ([], []))
        if item.ratio is None:
            excluded.append(item)
        else:
            ratios.append(item.ratio)
    return tuple(
        IdentitySlippage(identity=name, ratios=tuple(sorted(ratios)), excluded=len(excluded), factor=factor)
        for name, (ratios, excluded) in sorted(grouped.items())
    )


@dataclass(frozen=True, slots=True)
class BotPnl:
    """The bot's closed brackets in a window (the live R ledger: gross, IBKR Pro fixed commissions)."""

    trades: int
    gross: Decimal
    commissions: Decimal

    @property
    def net(self) -> Decimal:
        return self.gross - self.commissions

    def payload(self) -> dict[str, object]:
        return {
            "trades": self.trades,
            "gross": str(self.gross.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN)),
            "commissions": str(self.commissions.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN)),
            "net": str(self.net.quantize(_TEXT_QUANTUM, rounding=ROUND_HALF_EVEN)),
        }


def bot_pnl(trades: Iterable[LiveTrade]) -> BotPnl:
    chosen = list(trades)
    return BotPnl(
        trades=len(chosen),
        gross=sum((trade.gross_pnl for trade in chosen), _ZERO),
        commissions=sum((trade.commissions for trade in chosen), _ZERO),
    )


@dataclass(frozen=True, slots=True)
class V7CReport:
    as_of: date
    week_start: date | None = None
    blocked: tuple[BlockedOrder, ...] = ()
    stop_exits: tuple[StopExit, ...] = ()
    ledger: LiveLedger = field(default_factory=LiveLedger)
    sessions: tuple[date, ...] = ()
    manual_ledger: str = MANUAL_LEDGER_ABSENT
    unreadable: tuple[str, ...] = ()
    factor: Decimal = STOP_SLIPPAGE_FACTOR
    readable: bool = True
    note: str = ""

    def window(self, name: str) -> tuple[BlockedOrder, ...]:
        if name == "tonight":
            return tuple(row for row in self.blocked if row.session_date == self.as_of)
        if name == "week":
            start = self.week_start or self.as_of
            return tuple(row for row in self.blocked
                         if row.session_date is not None and start <= row.session_date <= self.as_of)
        return self.blocked

    def trades(self, name: str) -> tuple[LiveTrade, ...]:
        if name == "tonight":
            return tuple(trade for trade in self.ledger.trades if trade.session_date == self.as_of)
        start = self.week_start or self.as_of
        return tuple(trade for trade in self.ledger.trades if start <= trade.session_date <= self.as_of)

    @property
    def slippage(self) -> tuple[IdentitySlippage, ...]:
        return slippage_by_identity(self.stop_exits, self.factor)

    def payload(self) -> dict[str, object]:
        if not self.readable:
            return {"report": REPORT_VERSION, "readable": False, "note": self.note, "as_of": self.as_of.isoformat(),
                    "lines": self.lines()}
        slippage = self.slippage
        return {
            "report": REPORT_VERSION,
            "readable": True,
            "note": self.note,
            "as_of": self.as_of.isoformat(),
            "week_start": None if self.week_start is None else self.week_start.isoformat(),
            "tokens": list(BLOCK_TOKENS),
            "would_have_basis": (
                "the signal tracker's outcome of the decision's own setup (gross r_multiple); a setup it says"
                " would not have filled counts 0 R; a row without an outcome is counted, never as 0 R"
            ),
            "blocked": {
                name: {source: tally(self.window(name), source).payload() for source in SOURCES}
                for name in ("tonight", "week", "all")
            },
            "blocked_orders": [row.payload() for row in self.blocked],
            "stop_slippage": {
                "factor": str(self.factor),
                "measure": "-realized / (filled x (average entry - intent stop)), closed stop exits",
                "percentile": "nearest rank",
                "identities": {item.identity: item.payload() for item in slippage},
                "flagged": [item.identity for item in slippage if item.above_factor],
                "excluded": [item.payload() for item in self.stop_exits if item.ratio is None],
            },
            "pnl_by_source": {
                name: {BOT: bot_pnl(self.trades(name)).payload(), MANUAL: None}
                for name in ("tonight", "week")
            },
            "unknown": list(GAPS),
            "sessions": [day.isoformat() for day in self.sessions],
            "manual_ledger": self.manual_ledger,
            "unreadable": list(self.unreadable),
            "lines": self.lines(),
        }

    def lines(self) -> list[str]:
        """The nightly (tonight) and weekly (this week) lines, then stop slippage and P&L by source."""
        lines = [HEADER]
        if not self.readable:
            lines.append(f"  V7C {NO_V7C_DATA}: report unreadable ({self.note or 'unknown'})")
            return lines
        lines.append(f"  {self.as_of.isoformat()}: {blocked_text(self.window('tonight'))}")
        lines.append(f"  this week: {blocked_text(self.window('week'))}")
        slippage = self.slippage
        measured = [text for item in slippage if (text := item.line()) is not None]
        lines += [f"  {text}" for text in measured]
        if not measured:
            lines.append(f"  STOP SLIPPAGE {NO_V7C_DATA} (no stop exit with a known risk)")
        excluded = sum(item.excluded for item in slippage)
        if excluded:
            lines.append(f"  STOP SLIPPAGE excluded {excluded} stop exit{'s' if excluded != 1 else ''}:"
                         " zero or unknown risk")
        for name, label in (("tonight", self.as_of.isoformat()), ("week", "this week")):
            pnl = bot_pnl(self.trades(name))
            lines.append(f"  P&L by source {label}: bot {_money(pnl.net)} net ({pnl.trades} closed,"
                         f" gross {_money(pnl.gross)}) · manual unknown (not recorded)")
        if self.manual_ledger != MANUAL_LEDGER_READ:
            lines.append(f"  manual-order ledger {self.manual_ledger}: owner refusals unknown")
        if self.unreadable:
            lines.append("  not read: " + ", ".join(self.unreadable[:5]))
        return lines


def blocked_text(rows: Sequence[BlockedOrder]) -> str:
    """``V7C blocked 3 bot / 1 manual (would-have +1.40R)``; rows without an outcome are named."""
    if not rows:
        return f"V7C {NO_V7C_DATA} (no refused order)"
    tallies = [tally(rows, source) for source in SOURCES]
    counts = " / ".join(f"{item.blocked} {item.source}" for item in tallies)
    unknown = sum(1 for row in rows if row.source not in SOURCES)
    if unknown:
        counts += f" / {unknown} {UNKNOWN_SOURCE}"
    known = [item.would_have_r for item in tallies if item.would_have_r is not None]
    missing = sum(item.no_outcome for item in tallies) + unknown
    with_outcome = sum(item.with_outcome for item in tallies)
    if not known:
        return f"V7C blocked {counts} (would-have unknown: {missing} without an outcome)"
    total = _r(sum(known, _ZERO))
    if missing:
        return f"V7C blocked {counts} (would-have {total} from {with_outcome}; {missing} without an outcome)"
    return f"V7C blocked {counts} (would-have {total})"


# --- reading (read-only) ----------------------------------------------------------------


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _identity(item: Mapping[str, Any]) -> str:
    return str(item.get("strategy_name") or item.get("strategy") or "unknown").strip()


def _stop_exit(session_date: date, decision_id: str, item: Mapping[str, Any]) -> StopExit:
    filled, exit_filled = _count(item.get("filled")), _count(item.get("exit_filled"))
    avg_entry, avg_exit, stop = (_decimal(item.get(name)) for name in ("avg_entry", "avg_exit", "stop"))
    if filled is None or exit_filled is None or avg_entry is None or avg_exit is None or stop is None:
        ratio = None
    else:
        ratio = stop_ratio(filled=filled, exit_filled=exit_filled, avg_entry=avg_entry, avg_exit=avg_exit, stop=stop)
    return StopExit(
        session_date=session_date, decision_id=decision_id, identity=_identity(item),
        symbol=str(item.get("symbol") or "?"), ratio=ratio,
        why="" if ratio is not None else "zero or unknown risk",
    )


@dataclass(slots=True)
class _BridgeRead:
    blocked: list[tuple[date, str, Mapping[str, Any], str]] = field(default_factory=list)
    stop_exits: list[StopExit] = field(default_factory=list)
    sessions: list[date] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def read_bridge_states(directory: Path) -> _BridgeRead:
    """Refused entries and stop exits from every session file (a missing directory reads empty)."""
    read = _BridgeRead()
    if directory.is_symlink() or not directory.is_dir():
        return read
    for path in sorted(directory.iterdir()):
        if STATE_FILE.match(path.name) is None:
            continue
        try:
            session_date, submitted = read_submitted(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            logger.warning("bridge state %s not read for the v7c report: %s", path.name, exc)
            read.unreadable.append(path.name)
            continue
        read.sessions.append(session_date)
        for decision_id, item in sorted(submitted.items()):
            if not isinstance(item, dict):
                continue
            if item.get("skipped"):
                token = block_token(item.get("skip_reason"))
                if token is not None:
                    read.blocked.append((session_date, str(decision_id), item, token))
                continue
            if str(item.get("status") or "") == "closed" and str(item.get("exit_reason") or "") == STOP_EXIT_REASON:
                read.stop_exits.append(_stop_exit(session_date, str(decision_id), item))
    return read


def _read_only(database: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(database.resolve()))}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SCHEMA_READ_TIMEOUT_SECONDS)


def _created_session(value: object) -> date | None:
    try:
        created = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if created.tzinfo is None:
        return None
    return created.astimezone(NEW_YORK).date()


def read_manual_blocks(database: Path) -> tuple[list[BlockedOrder], str]:
    """Owner orders the bridge refused with a v7c token, and whether the ledger was there.

    No database or no manual-order table: nothing is read and the report says
    so; a read error raises (the caller names it).
    """
    path = Path(database).expanduser()
    if not path.is_file():
        return [], MANUAL_LEDGER_ABSENT
    with closing(_read_only(path)) as db:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if MANUAL_LEDGER_TABLE not in tables:
            return [], MANUAL_LEDGER_ABSENT
        rows = db.execute(_MANUAL_BLOCKS_SQL, BLOCK_TOKENS).fetchall()
    blocked = []
    for row_id, created_at, symbol, code in rows:
        ref = manual_order_ref(str(row_id))
        blocked.append(BlockedOrder(
            session_date=_created_session(created_at), source=order_source(ref) or UNKNOWN_SOURCE,
            token=str(code), order_ref=ref, identity=OWNER, symbol=str(symbol or "?"), decision_id=None,
            outcome=NO_OUTCOME, would_have_r=None,
        ))
    return blocked, MANUAL_LEDGER_READ


def _bot_row(session_date: date, decision_id: str, item: Mapping[str, Any], token: str, *,
             outcome: str = NO_OUTCOME, would_have_r: Decimal | None = None) -> BlockedOrder:
    identity = _identity(item)
    ref = bot_order_ref(identity, decision_id)
    return BlockedOrder(
        session_date=session_date, source=order_source(ref) or UNKNOWN_SOURCE, token=token, order_ref=ref,
        identity=identity, symbol=str(item.get("symbol") or "?"), decision_id=decision_id, outcome=outcome,
        would_have_r=would_have_r,
    )


def _bot_blocks(read: _BridgeRead, database: Path) -> list[BlockedOrder]:
    """Each refused bot entry with its setup's tracked outcome (the promoter's join)."""
    # Lazy: session_check and the manager's context import this module for its vocabulary alone, and
    # the promoter's module loads the replay and strategy stack.
    from trading.research.promotion_proposals import read_shadow_outcomes

    outcomes = read_shadow_outcomes(database, [decision_id for _, decision_id, _, _ in read.blocked])
    rows = []
    for session_date, decision_id, item, token in read.blocked:
        found = outcomes.get(decision_id)
        if found is None or (found.filled and found.gross_r is None):
            rows.append(_bot_row(session_date, decision_id, item, token))
        elif not found.filled:  # the tracker says it would not have filled: it would have done nothing
            rows.append(_bot_row(session_date, decision_id, item, token, outcome=NOT_FILLED, would_have_r=_ZERO))
        else:
            rows.append(_bot_row(session_date, decision_id, item, token, outcome=FILLED,
                                 would_have_r=found.gross_r))
    return rows


def collect_v7c_report(
    *,
    database: str | Path,
    bridge_directory: Path,
    ledger: LiveLedger,
    as_of: date,
    week_start: date | None,
) -> V7CReport:
    """The nightly call (``trading.research.nightly``): everything read-only, nothing written."""
    path = Path(database).expanduser()
    read = read_bridge_states(bridge_directory)
    unreadable = list(read.unreadable)
    try:
        bot = _bot_blocks(read, path)
    except sqlite3.Error as exc:
        logger.warning("v7c report: setup outcomes not read: %s", type(exc).__name__)
        unreadable.append(f"signal outcomes ({type(exc).__name__})")
        bot = [_bot_row(*entry) for entry in read.blocked]
    try:
        manual, ledger_state = read_manual_blocks(path)
    except sqlite3.Error as exc:
        logger.warning("v7c report: manual-order ledger not read: %s", type(exc).__name__)
        manual, ledger_state = [], f"unreadable ({type(exc).__name__})"
    blocked = sorted(bot + manual, key=lambda row: (row.session_date or date.min, row.source, row.order_ref))
    return V7CReport(
        as_of=as_of,
        week_start=week_start,
        blocked=tuple(blocked),
        stop_exits=tuple(read.stop_exits),
        ledger=ledger,
        sessions=tuple(read.sessions),
        manual_ledger=ledger_state,
        unreadable=tuple(unreadable),
    )


def failed_report(as_of: date, note: str) -> V7CReport:
    """A report that could not be built: its lines and payload say so (never omitted)."""
    return V7CReport(as_of=as_of, readable=False, note=note)


def report_lines(report: V7CReport | None) -> list[str]:
    """The section's lines; a night without the report says so, never an empty section."""
    if report is None:
        return [HEADER, f"  V7C {NO_V7C_DATA}: report {NOT_COLLECTED}"]
    return report.lines()


__all__ = [
    "BLOCK_TOKENS",
    "BOT",
    "BOT_REF_PREFIX",
    "DAILY_LOSS_LIMIT",
    "DAY_RISK_BUDGET_PCT",
    "GAPS",
    "HEADER",
    "MANUAL",
    "MANUAL_REF_PREFIX",
    "NOT_COLLECTED",
    "NO_V7C_DATA",
    "REPORT_VERSION",
    "SOURCES",
    "STOP_SLIPPAGE_ABOVE_FACTOR",
    "STOP_SLIPPAGE_FACTOR",
    "V7C_BUDGET_INVARIANT_BROKEN",
    "V7C_BUDGET_OK",
    "V7C_BUDGET_OVER",
    "V7C_DAY_RISK_BUDGET",
    "V7C_STOP_WIDENED",
    "V7C_UNPROTECTED_POSITION",
    "V7C_WRONG_THREAD",
    "BlockedOrder",
    "BotPnl",
    "IdentitySlippage",
    "SourceTally",
    "StopExit",
    "V7CReport",
    "block_token",
    "blocked_text",
    "bot_order_ref",
    "bot_pnl",
    "collect_v7c_report",
    "failed_report",
    "manual_order_ref",
    "median",
    "nearest_rank",
    "order_source",
    "read_bridge_states",
    "read_manual_blocks",
    "report_lines",
    "slippage_by_identity",
    "stop_ratio",
    "tally",
]
