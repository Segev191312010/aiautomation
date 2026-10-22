"""TEMPORARY supervised PAPER bridge v7c (owner request 2026-10-12; v5 = C1.1 hardening + GO fixes;
v7a = per-identity authority: exact-match allowlist, identity fence, caps, orderRef identity;
v7b = the four identities MOMO_HOD_V2, ORB_V2, VWAP_PB_V2, GAP_GO_V2, data rows only;
v7c = the account-level 3 % day risk budget and owner buys that carry their stop, owner decisions
D1 and D5 of 2026-10-18).

Consumes accepted decisions of ONE New York session from the shadow ledger and
manages each as an IBKR native bracket on the PAPER account until the session
ends:

  parent BUY LMT (DAY, transmit=False) -> target SELL LMT (GTC) -> stop SELL STP
  (GTC, transmit=True). Bracket children are OCA-linked by IBKR.

Session times below are for a 16:00 close; every checkpoint moves with the
real XNYS close (13:00 on a half day: entries stop 12:30, flatten 12:45,
alerts 12:46/12:50/12:58, stop 12:55).

Lifecycle per bracket: sent -> (open | cancelled) -> closed by target, stop, or
the 15:45 flatten; needs_attention when startup reconciliation cannot find it
at IBKR or its exit fills exceed its entry fills (never re-sent, never managed,
counted toward the position caps while it may hold shares). Fills come from execDetails
of this session, account and order-ref prefix (deduplicated by execId; a
corrected execution replaces the one it corrects) and never decrease, so a
restart can rebuild them with reqExecutions.

Usage (backend venv; the supervisor always passes the session date):

  backend/.venv/bin/python backend/scripts_local/paper_bridge.py --date YYYY-MM-DD
  backend/.venv/bin/python backend/scripts_local/paper_bridge.py --preflight

--preflight never connects to TWS: it checks the config, prints the sendable
identities, their caps rows and the fenced identities from this file's own
code, and exits 78 when a sendable identity has no caps row or the fence is
unreadable. The supervisor's dry run relays its lines.

State: ~/.local/share/autotrader-v1/bridge/<date>.json (directory 0700, file
0600, atomic replace), mirrored to ~/.local/share/autotrader-v1/
paper_bridge_state.json for the dashboard. Another date's state is never read.

Exit codes: 0 normal session end (15:55 ET when flat, otherwise right after
the 15:58 alert), started after 15:55 ET, or SIGTERM/SIGINT (state saved,
TWS disconnected); 75 TWS unreachable, silent (no handshake within 30 s, no
managed accounts) or disconnected, or any unexpected error after connect (the
supervisor restarts the bridge); 78 refused (non-paper account, unusable
config or state, future --date). A stop or disconnect after 15:45 first
repeats the CRITICAL alert for anything not flat.

Every stdout line is "HH:MM:SS ET  <KIND> <text>" with KIND in EVENT_KINDS;
the notifier parses that format. Tracebacks go to stderr only.

Safety rules (all enforced here because the shadow pipeline sizes per run):
- Refuses to start unless every managed account starts with "DU" and the
  configured paper account is among them; order client ID only.
- Only a strategy whose ledger name is exactly one of SENDABLE_STRATEGY_NAMES
  may send orders (no prefix: a prefix once gave ORB_V2 ORB_V1's cutoff). The
  name comes from the strategy version of the decision's run, never the run id.
  A sendable name without an IDENTITY_CAPS row is skipped (fail closed).
  Caps rows are validated at import and the table is read-only at runtime.
- The PAPER kill fence (name 'paper') and the identity's own fence (name
  'paper:<IDENTITY>') are checked before every entry; unreadable means stopped.
  An identity fence blocks only that identity's new entries; exits, the
  flatten and owner orders are unaffected.
- Per identity: at most max_positions brackets working, open, or unreconciled
  with shares, at most max_entries_per_day entries sent (whatever became of
  them), and no entry after the session close minus last_entry_before_close.
  Across ALL identities at most sum(max_positions) brackets plus one slot per
  symbol with a live client order the bridge does not manage; one bracket per
  symbol. A single order may not exceed 25% of net liq in notional or 1% of net
  liq in risk (entry - stop).
- Day risk budget (v7c, D1/D5), across the whole account: a risk-increasing
  order (a bot entry or an owner buy) is sent only if today's realized loss
  (floored at zero: gains never fund risk) + the open risk to CURRENT stops +
  its own risk, risks scaled by STOP_SLIPPAGE_FACTOR, stays within
  DAY_RISK_BUDGET_PCT of net liq; none once the account's realized loss
  reaches MAX_DAILY_LOSS_PCT (daily_loss_limit). Open risk = held x (avg -
  stop) + unfilled x (limit - stop) for every bot bracket, every owner buy and
  every other long, whose stop is the lowest working SELL STP on the contract
  (any client, read from reqAllOpenOrders on a fixed cadence). Shares without
  a covering stop, a short, or a bot position whose stop leg is dead refuse
  every risk-increasing order (v7c_unprotected_position). The check runs on
  the bridge loop right before placeOrder, after the quote wait and the
  fences, and the order is reserved (stored) before it leaves, so two
  decisions of one tick cannot both spend the same budget. Exits, closes,
  cancels, the flatten and stop changes are never checked, and nothing is
  ever flattened because of the budget: an over-budget account or a widened
  stop pages (CRITICAL) and only blocks new risk. An open-orders request TWS
  has not answered for ORDER_BOOK_UNANSWERED_ALERT pages CRITICAL once: no
  book completes until it is answered, and it is never written off.
- Every order carries orderRef autotrader:<IDENTITY>:<decision id prefix>, so
  IBKR reports attribute fills per identity; parse_ref reads it back.
- A new entry needs an account position in its contract that is known and
  zero: the exit caps read the whole account position, so the bot only trades
  symbols it alone holds and a bracket can never sell the owner's shares.
- A decision is sent only before its risk approval expires (the intent's
  expires_at, else 15 s after the decision) and a setup fingerprint is sent
  at most once.
- Unfilled entries are cancelled after ENTRY_TTL or at their strategy cutoff;
  a cancel that never reached IBKR is re-sent after a restart.
- Every exit modification goes through Bridge.modify_exit: the quantity comes
  from ONE locked snapshot taken right before placeOrder (target total = entry
  fills - stop fills, stop total = entry fills - target fills), is capped by
  the account position from reqPositions, and is never sent while exit fills
  exceed entry fills. It is encoded by the same order() factory as the
  placement TWS accepted, copying only the OCA link (ocaGroup, ocaType,
  parentId) from the order TWS last reported, and it counts as done only once
  TWS echoes it through openOrder: re-sent once per tick at most 3 times, then
  CRITICAL. Any TWS error on one of the bridge's own order ids is CRITICAL
  unless it is a known notice (ORDER_INFO_CODES).
- A dead, partially filled entry has its exits resized to its fresh fill
  count, again whenever that count changes. After a restart a parent whose
  cancel was requested and that TWS no longer reports counts as dead.
- Every tick, CRITICAL once: an open position without a live stop, an exit
  still live on a closed or cancelled bracket, or an account holding fewer
  shares than a bracket expects (the last two after a 15 s grace, because an
  OCA cancel or a position update may trail its fill). No entry is sent once a
  stop was requested.
- 15:45 ET flatten: each open bracket's target is repriced ONCE to a
  marketable limit on the same order ID (live bid, else the newest screener
  snapshot less a further 1%); the OCA link cancels the stop, so there is never
  a second sell order. The attempt is persisted before anything is sent, so a
  failed or interrupted flatten is never retried after a restart. 15:46, 15:50
  and 15:58 only alert (CRITICAL) about anything not flat: a second automatic
  sell could race the owner's manual close in TWS.

Owner orders (ManualDesk, a second isolated intake that always runs after the
flatten): every tick it writes the bridge heartbeat, follows sent rows of the
manual-order ledger (trading/execution/manual_orders.py) to their IBKR outcome,
cancels what the owner asked to cancel, then claims at most one queued row
(queued -> sending before anything leaves), re-validates it against live facts
(account position less bot shares and less filled sells the position does not
show yet, open manual sells, every working sell on the contract from any client
via reqAllOpenOrders — stops placed by hand in TWS and the bot's own legs, a
past session's included — and a fresh real-time quote) and sends a plain DAY
order with orderRef manual:<id>. A buy (v7c) must pass the day risk budget and
goes out as a DAY parent plus its attached SELL STP child (parentId, GTC, the
same orderRef): the child's fills never count as the buy's, a /cancel of the
buy never cancels it, and once the buy is dead it is fitted down to the
shares still held. A /close, /sell all or /closeall first cancels the owner's
own attached stops on what it sells (a /closeall decides every holding before
it cancels anything, and keeps the stop of each holding it cannot sell) and
sends the sell only after IBKR reports them cancelled, so two sells never work
on the same shares; a holding whose stop was cancelled and that is then not
sold is paged by name. A symbol with a live bot order the bridge does
not manage today counts as a bot symbol: owner sells are refused there. A
/close of a bot symbol reprices that bracket's target instead; /closeall does
that for every bracket and sells each unreserved US-stock long at market. A
row still sending at a restart that IBKR does not know becomes unknown and is
never resent. Manual orders never count toward the position caps, are never flattened at
the close, keep bot entries off their symbol, and log KIND MANUAL only.

Stage C2 replaces this script with the reviewed execution engine.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Coroutine, Mapping
from contextlib import closing
import copy
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time as clock_time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time
import tomllib
import traceback
from types import FrameType, MappingProxyType, ModuleType
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.execution import ExecutionFilter
from ibapi.order import Order
from ibapi.wrapper import EWrapper

BRIDGE_VERSION = "v7c"
STATE_SCHEMA = "paper-bridge-state.v5"
NY = ZoneInfo("America/New_York")
DATA_DIR = Path.home() / ".local/share/autotrader-v1"
DB = DATA_DIR / "autotrader.db"
CONFIG = Path.home() / ".config/autotrader-v1/config.toml"
STATE_DIR = DATA_DIR / "bridge"
LEGACY_STATE = DATA_DIR / "paper_bridge_state.json"  # read by the dashboard only
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497

EXIT_OK = 0
EXIT_RESTART = 75  # EX_TEMPFAIL: the supervisor restarts the bridge
EXIT_REFUSED = 78  # EX_CONFIG: unsafe or invalid setup; a restart cannot fix it

EVENT_KINDS = frozenset({
    "SENT", "FILL", "EXIT", "CLOSED", "CANCEL", "CANCELLED", "SKIP",
    "FLATTEN", "FLAT", "RESIZE", "CRITICAL", "INFO",
    "MANUAL",  # owner orders from Telegram: the assistant reports them, the notifier skips them
})

DEFAULT_DECISION_TTL_S = 15  # trading/risk/engine.py: a risk approval lives 15 s
MAX_DECISION_AGE_S = 60  # backstop even when an intent claims a longer expiry
MAX_DAILY_LOSS_PCT = Decimal("2")  # v7c: the ACCOUNT's realized loss today, bot and owner alike (O14)
MAX_ORDER_NOTIONAL_PCT = Decimal("25")
MAX_ORDER_RISK_PCT = Decimal("1")
# v7c day risk budget (owner decision D1, 2026-10-18): realized loss + open risk
# to current stops + new risk <= this share of net liquidation, account level.
# Raising it, or lowering the factor, is a reviewed change; nothing waives it for a day (D5).
DAY_RISK_BUDGET_PCT = Decimal("3")
# Every risk to a stop is multiplied by this before it is compared: a stop can
# fill below its price. Measured nightly (P3-B3); never below 1.
STOP_SLIPPAGE_FACTOR = Decimal("1.0")
BUDGET_PAGE_STEP_PCT = Decimal("0.5")  # an over-budget account pages once per step of this size
PNL_FRESH = timedelta(seconds=120)  # an IBKR reqPnL value older than this is not used (O15)
PNL_MISMATCH_MIN_USD = Decimal("25")  # reqPnL vs computed realized: WARN above the larger of these two
PNL_MISMATCH_NLV_PCT = Decimal("0.05")
# The open-orders book of every client (reqAllOpenOrders) is re-read on this
# fixed cadence: it is the only source of stops the bridge did not send (TWS,
# client 0) and of TWS edits of its own stops, since TWS need not push an
# owner's edit to client 18 (spike V-1). A book older than the maximum age
# covers nothing: a position it would have to cover blocks new risk.
ORDER_BOOK_REFRESH = timedelta(seconds=30)
ORDER_BOOK_MAX_AGE = timedelta(seconds=90)
ORDER_BOOK_TIMEOUT_S = 3.0  # one refresh never holds the loop longer than this
UNPROTECTED_RECHECK = timedelta(seconds=5)  # a book older than this is re-read before an unprotected page
# An open-orders request TWS has not answered for this long pages CRITICAL once (N18b-LOW-2): until it
# is answered no book completes, and it is never written off (its late end would complete a later one).
ORDER_BOOK_UNANSWERED_ALERT = timedelta(seconds=60)
if not Decimal("0") < DAY_RISK_BUDGET_PCT <= Decimal("3"):  # tripwire: the owner's cap is 3 %
    raise ValueError(f"DAY_RISK_BUDGET_PCT must be in (0, 3], not {DAY_RISK_BUDGET_PCT}")
if not STOP_SLIPPAGE_FACTOR >= Decimal("1"):  # tripwire: a factor below 1 would shrink every risk
    raise ValueError(f"STOP_SLIPPAGE_FACTOR must be >= 1, not {STOP_SLIPPAGE_FACTOR}")
# v7c tokens: the same string starts the bridge.log line, the bracket's
# skip_reason, the manual row's reject_code and the page.
V7C_DAY_RISK_BUDGET = "v7c_day_risk_budget"
V7C_BUDGET_OK = "v7c_budget_ok"
V7C_BUDGET_OVER = "v7c_budget_over"
V7C_STOP_WIDENED = "v7c_stop_widened"
V7C_STOP_UNCONFIRMED = "v7c_stop_unconfirmed"
V7C_UNPROTECTED_POSITION = "v7c_unprotected_position"
V7C_BUDGET_INVARIANT_BROKEN = "v7c_budget_invariant_broken"
V7C_WRONG_THREAD = "v7c_wrong_thread"
V7C_PNL_SOURCE = "v7c_pnl_source"
V7C_PNL_MISMATCH = "v7c_pnl_mismatch"
V7C_PNL_STALE = "v7c_pnl_stale"
V7C_STOP_OVERSIZED = "v7c_stop_oversized"
V7C_ORDER_BOOK_UNAVAILABLE = "v7c_order_book_unavailable"
DAILY_LOSS_LIMIT = "daily_loss_limit"
# What an unprotected position's page tells the owner. Never "/stop SYM PRICE":
# a Telegram stop is a DAY order, so it would unblock only until the close. "or in
# TWS": /close is refused while a past session's bot leg or a needs_attention
# bracket works on the symbol (N18 LOW-5).
UNPROTECTED_ADVICE = "close it with /close {symbol} or in TWS, or protect it with a stop that stays working (GTC)"
ENTRY_TTL = timedelta(minutes=10)
# Session checkpoints relative to the XNYS close (16:00, or 13:00 on a half day).
REGULAR_CLOSE = clock_time(16, 0)
LAST_ENTRY_BEFORE_CLOSE = timedelta(minutes=30)  # 15:30 on a full day
FLATTEN_BEFORE_CLOSE = timedelta(minutes=15)  # 15:45
FLATTEN_ALERT_BEFORE_CLOSE = timedelta(minutes=14)  # 15:46
VERIFY_BEFORE_CLOSE = timedelta(minutes=10)  # 15:50
STOP_BEFORE_CLOSE = timedelta(minutes=5)  # 15:55
FINAL_ALERT_BEFORE_CLOSE = timedelta(minutes=2)  # 15:58
ORDER_REF_PREFIX = "autotrader:"
MANUAL_REF_PREFIX = "manual:"  # owner orders (trading/execution/manual_orders.py)
# Family cutoffs of names that are NOT sendable (legacy brackets, the state of a
# past bridge); a sendable identity's last entry comes from its caps row only.
STRATEGY_CUTOFFS = {"momo": clock_time(15, 0), "orb": clock_time(12, 0)}


@dataclass(frozen=True)
class Caps:
    """One identity's entry limits; its last entry is the session close minus the offset.

    A row is refused at import when it could widen anything: at least one
    position, at least one entry per day when capped, and a last-entry offset
    inside the session (0 < offset <= 6 h). The table is read-only: nothing at
    runtime may add, drop or change a row (a test and --preflight pin it).
    """

    max_positions: int
    max_entries_per_day: int | None
    last_entry_before_close: timedelta

    def __post_init__(self) -> None:
        if isinstance(self.max_positions, bool) or not isinstance(self.max_positions, int) or self.max_positions < 1:
            raise ValueError("max_positions must be an int >= 1")
        entries = self.max_entries_per_day
        if entries is not None and (isinstance(entries, bool) or not isinstance(entries, int) or entries < 1):
            raise ValueError("max_entries_per_day must be None or an int >= 1")
        offset = self.last_entry_before_close
        if not isinstance(offset, timedelta) or not timedelta(0) < offset <= timedelta(hours=6):
            raise ValueError("last_entry_before_close must be a timedelta in (0, 6 h]")


# Exactly the ledger names the owner granted PAPER order authority
# (trading/sendable_identities.py SENDABLE_IDENTITIES; a test pins the two
# equal). Membership is exact and case-sensitive: a prefix would also match
# names nobody granted. Every sendable name needs a caps row; --preflight and
# a test refuse a mismatch, and the gate skips an uncapped name (fail closed).
SENDABLE_STRATEGY_NAMES = frozenset({"MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2"})
IDENTITY_CAPS: Mapping[str, Caps] = MappingProxyType({
    "MOMO_HOD_V2": Caps(max_positions=3, max_entries_per_day=None,
                        last_entry_before_close=timedelta(minutes=60)),  # 15:00 on a full day
    "ORB_V2": Caps(max_positions=1, max_entries_per_day=1,
                   last_entry_before_close=timedelta(minutes=30)),  # 15:30 on a full day
    "VWAP_PB_V2": Caps(max_positions=1, max_entries_per_day=2,
                       last_entry_before_close=timedelta(minutes=30)),
    "GAP_GO_V2": Caps(max_positions=1, max_entries_per_day=1,
                      last_entry_before_close=timedelta(minutes=30)),
})
SHADOW_ONLY = "shadow-only strategy"
TICK = Decimal("0.01")
FLATTEN_DISCOUNT = Decimal("0.995")
LATE_FLATTEN_DISCOUNT = Decimal("0.98")  # the first and only flatten of a start after 15:55
SNAPSHOT_EXTRA_DISCOUNT = Decimal("0.99")
SNAPSHOT_LOOKBACK = 10
POSITION_GAP_GRACE = timedelta(seconds=15)  # a position update or OCA cancel may trail its fill
MODIFY_RETRIES = 3  # re-sends of an exit modification TWS has not echoed, one per tick
QUOTE_TIMEOUT_S = 4.0
SYNC_TIMEOUT_S = 10.0
ACCOUNTS_TIMEOUT_S = 3.0
CONNECT_WATCHDOG_S = 30.0
LOOP_SECONDS = 2.0
EXEC_REQUEST_ID = 7001
SUMMARY_REQUEST_ID = 7002
PNL_REQUEST_ID = 7003
QUOTE_REQUEST_BASE = 900000
LIVE_ORDER_STATUSES = frozenset({"ApiPending", "PendingSubmit", "PreSubmitted", "Submitted"})
WORKING_ORDER_STATUSES = LIVE_ORDER_STATUSES | {"PendingCancel"}  # may still fill
QUOTE_TICKS = {1: "bid", 2: "ask", 4: "last", 66: "bid", 67: "ask", 68: "last"}  # 66-68 are delayed
DEAD_ORDER_STATUSES = frozenset({"Cancelled", "ApiCancelled", "Inactive", "Filled"})
PARENT_CANCELLED_STATUSES = frozenset({"Cancelled", "ApiCancelled", "Inactive"})
# Any error on one of the bridge's own order ids is CRITICAL except these notices:
# 202 cancel confirmed, 399 order warning, 2109/2148 order event warnings, 10311 direct routing.
ORDER_INFO_CODES = frozenset({202, 399, 2109, 2148, 10311})
# 1100 connectivity lost, 1300 socket port reset, 2110 TWS-to-server connectivity broken.
CRITICAL_TWS_CODES = frozenset({1100, 1300, 2110})
FARM_BROKEN_CODES = frozenset({2103, 2105, 2157})  # logged; the other 21xx farm notices are dropped

_LOG_LOCK = threading.RLock()
STOP = threading.Event()  # set by SIGTERM/SIGINT; the loop saves state and exits 0


def log(kind: str, message: str) -> None:
    """Write one notifier event line: ``HH:MM:SS ET  <KIND> <text>``."""
    if kind not in EVENT_KINDS:
        kind, message = "INFO", f"{kind} {message}"
    text = " ".join(str(message).split())  # never let a TWS message break a line
    line = f"{datetime.now(NY):%H:%M:%S} ET  {kind} {text}\n"
    # One write per line: reader-thread callbacks must never splice a line.
    with _LOG_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def now_ny() -> datetime:
    return datetime.now(NY)


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def positive_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


def average(total: Decimal, quantity: int) -> Decimal:
    """Average price; a zero quantity yields 0 instead of the 0/0 that crashed v2."""
    return total / quantity if quantity > 0 else Decimal("0")


def floor_tick(price: Decimal) -> Decimal:
    return price.quantize(TICK, rounding=ROUND_DOWN)


def share_text(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else str(value.normalize())


def strategy_family(name: str) -> str:
    upper = name.strip().upper()
    if upper.startswith("MOMO"):
        return "momo"
    if upper.startswith("ORB"):
        return "orb"
    return "other"


def may_send_orders(strategy_name: str) -> bool:
    return strategy_name.strip() in SENDABLE_STRATEGY_NAMES


def global_max_open() -> int:
    """Brackets that may hold shares across every identity, plus unmanaged live symbols."""
    return sum(caps.max_positions for caps in IDENTITY_CAPS.values())


def caps_problem() -> str | None:
    """The sendable names without a caps row and the caps rows nobody may send for."""
    mismatch = sorted(SENDABLE_STRATEGY_NAMES ^ set(IDENTITY_CAPS))
    return None if not mismatch else f"PREFLIGHT_REFUSED caps: {', '.join(mismatch)}"


def caps_lines() -> list[str]:
    """What the connect line and --preflight print: the sendable set and each caps row."""
    lines = [f"sendable identities = {', '.join(sorted(SENDABLE_STRATEGY_NAMES)) or 'none'}"]
    for name, caps in sorted(IDENTITY_CAPS.items()):
        entries = "none" if caps.max_entries_per_day is None else str(caps.max_entries_per_day)
        minutes = int(caps.last_entry_before_close.total_seconds() // 60)
        lines.append(f"caps {name} positions={caps.max_positions} entries/day={entries} last_entry=close-{minutes}m")
    return lines


def budget_line() -> str:
    """The v7c rule as the connect line, --preflight and the dry run print it (deploy apply looks for it)."""
    return (f"day risk budget {DAY_RISK_BUDGET_PCT}% of net liquidation, account level (realized loss + open risk"
            f" to current stops + new risk; stop slippage factor {STOP_SLIPPAGE_FACTOR})")


def position_risk(held: int | Decimal, avg_entry: Decimal, pending: int | Decimal, limit: Decimal, stop: Decimal,
                  factor: Decimal) -> Decimal:
    """Risk to the stop (pure): held x max(0, avg - stop) x f + unfilled x max(0, limit - stop) x f.

    A stop at or above the entry risks nothing (a break-even stop frees the
    budget); a negative risk never offsets another position's.
    """
    zero = Decimal("0")
    held_risk = Decimal(held) * max(zero, avg_entry - stop)
    pending_risk = Decimal(pending) * max(zero, limit - stop)
    return (held_risk + pending_risk) * factor


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def parse_ref(ref: object) -> tuple[str, str] | None:
    """(identity, decision id prefix) of a bridge orderRef; None for any other ref."""
    if not isinstance(ref, str) or not ref.startswith(ORDER_REF_PREFIX):
        return None
    identity, separator, decision = ref[len(ORDER_REF_PREFIX):].partition(":")
    if not separator or not identity or not decision:
        return None
    return identity, decision


def backend_modules() -> None:
    """Make the backend package importable: the supervisor runs this file as a script."""
    backend = str(Path(__file__).resolve().parents[1])
    if backend not in sys.path:
        sys.path.insert(0, backend)


@dataclass(frozen=True)
class SessionTimes:
    """Every session checkpoint, measured back from the real XNYS close."""

    closes_at: datetime
    last_entry: datetime
    flatten: datetime
    flatten_alert: datetime
    verify: datetime
    stop: datetime
    final_alert: datetime

    @classmethod
    def closing_at(cls, closes_at: datetime) -> "SessionTimes":
        return cls(
            closes_at=closes_at,
            last_entry=closes_at - LAST_ENTRY_BEFORE_CLOSE,
            flatten=closes_at - FLATTEN_BEFORE_CLOSE,
            flatten_alert=closes_at - FLATTEN_ALERT_BEFORE_CLOSE,
            verify=closes_at - VERIFY_BEFORE_CLOSE,
            stop=closes_at - STOP_BEFORE_CLOSE,
            final_alert=closes_at - FINAL_ALERT_BEFORE_CLOSE,
        )


def session_close(session_date: date) -> datetime:
    """The XNYS close of the date (13:00 on a half day); 16:00 when the calendar is unusable."""
    regular = datetime.combine(session_date, REGULAR_CLOSE, tzinfo=NY)
    try:
        backend_modules()
        from trading.scheduler.sessions import market_session

        session = market_session(session_date)
    except Exception as exc:  # the bridge must still flatten: fall back to the regular close
        log("CRITICAL", f"market calendar unavailable ({type(exc).__name__}: {exc}) — assuming the regular"
            " 16:00 close; on a half day act in TWS before 13:00")
        return regular
    return regular if session is None else session.closes_at.astimezone(NY)


def session_times(session_date: date) -> SessionTimes:
    return SessionTimes.closing_at(session_close(session_date))


@dataclass
class Bracket:
    decision_id: str
    symbol: str
    con_id: int
    primary_exchange: str
    strategy: str  # family for the dashboard: momo | orb | other
    qty: int
    entry: str
    stop: str
    target: str
    parent: int | None = None
    target_id: int | None = None
    stop_id: int | None = None
    sent_at: str = ""
    skipped: bool = False
    skip_reason: str = ""
    status: str = "pending"  # sent|open|cancelled|closed|skipped|needs_attention
    filled: int = 0
    avg_entry: str = "0"
    exit_filled: int = 0
    avg_exit: str = "0"
    exit_reason: str = ""
    realized_pnl: str = "0"
    parent_cancel_requested: bool = False
    resized_qty: int = 0  # entry fills TWS confirmed the exits were sized to after a dead entry; 0 = never
    flatten_attempted: bool = False  # persisted before the attempt: a restart never retries a flatten
    flatten_sent: bool = False
    flatten_price: str = ""
    strategy_name: str = ""  # ledger name, e.g. MOMO_HOD_V1
    run_id: str = ""
    attention_reason: str = ""
    setup_fingerprint: str = ""

    @property
    def active(self) -> bool:
        return self.status in {"sent", "open"}

    @property
    def net_held(self) -> int:
        """Shares held per the bridge's fills; negative means exits exceeded entries."""
        return self.filled - self.exit_filled

    @property
    def remaining(self) -> int:
        return max(0, self.net_held)

    @property
    def label(self) -> str:
        return self.strategy_name or self.strategy

    @property
    def legs(self) -> tuple[int, ...]:
        return tuple(leg for leg in (self.parent, self.target_id, self.stop_id) if leg is not None)


BRACKET_FIELDS = frozenset(Bracket.__dataclass_fields__)


@dataclass(frozen=True)
class DecisionRow:
    decision_id: str
    run_id: str
    symbol: str
    con_id: int
    contract_json: str
    payload_json: str
    created_at: str
    strategy_name: str


@dataclass(frozen=True)
class DecisionIntent:
    quantity: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    expires_at: datetime | None
    setup_fingerprint: str


@dataclass(frozen=True)
class SnapshotQuote:
    price: Decimal
    observed_at: str


@dataclass(frozen=True)
class ExecutionRow:
    order_id: int
    shares: int
    price: Decimal
    account: str
    order_ref: str
    day: date | None
    version: int  # correction sequence: the digits after the execId's final period
    con_id: int = 0  # from the execution's contract when TWS sends one
    side: str = ""  # BOT | SLD
    arrival: int = 0  # this client's execution count when it arrived
    positions_seen: int = 0  # the position-update count when it arrived (reader thread)
    basis: Decimal | None = None  # the contract's last known average cost when it arrived (v7c realized)


@dataclass(frozen=True)
class OpenOrderRow:
    status: str
    symbol: str
    con_id: int


@dataclass
class AccountOrder:
    """One open order of any client (or TWS itself), from a reqAllOpenOrders snapshot."""

    client_id: int
    order_id: int
    perm_id: int
    account: str
    con_id: int
    symbol: str
    action: str
    order_type: str
    order_ref: str
    total: Decimal
    status: str
    remaining: Decimal | None = None
    aux_price: Decimal | None = None  # a stop's trigger price (v7c: which shares a stop covers, and at what)
    parent_id: int = 0  # an attached (child) order's parent

    @property
    def open_quantity(self) -> Decimal:
        return self.total if self.remaining is None else self.remaining


@dataclass(frozen=True)
class PendingEntry:
    """A buy about to be placed with its attached stop, measured as if it were already working."""

    con_id: int
    symbol: str
    quantity: int
    price: Decimal  # the limit, or the ask it was sized at
    stop: Decimal


@dataclass(frozen=True)
class OpenRisk:
    """The account's open risk to its current stops (v7c), with what cannot be measured."""

    total: Decimal
    lines: tuple[str, ...] = ()  # one per contributor, for the log
    unprotected: tuple[tuple[str, str], ...] = ()  # (symbol, why): shares no working stop covers
    # (symbol, shares the stops sell, shares they cover, the account's position): working stops
    # that would sell more than the shares they cover (LOW-8: a WARN, never a block)
    oversized: tuple[tuple[str, Decimal, Decimal, Decimal], ...] = ()


@dataclass(frozen=True)
class Budget:
    """One day-risk-budget evaluation; the risks already carry the slippage factor."""

    realized_loss: Decimal
    open_risk: Decimal
    new_risk: Decimal
    net_liq: Decimal

    @property
    def total(self) -> Decimal:
        return self.realized_loss + self.open_risk + self.new_risk

    @property
    def limit(self) -> Decimal:
        return self.net_liq * DAY_RISK_BUDGET_PCT / 100

    def text(self) -> str:
        percent = (self.total / self.net_liq * 100).quantize(Decimal("0.01"))
        return (f"realized {money(self.realized_loss)} + open {money(self.open_risk)} + new {money(self.new_risk)}"
                f" = {money(self.total)} ({percent}%)")


@dataclass(frozen=True)
class FillSnapshot:
    """Everything an exit quantity depends on, read under one lock."""

    parent: int
    target: int
    stop: int
    recorded_exits: int  # the bracket's persisted exit fills (never decrease)
    position: Decimal | None  # account position in the contract; None = unknown


@dataclass(frozen=True)
class ExitPlan:
    total: int = 0  # totalQuantity of the modification; IBKR reads it as the order total
    working: int = 0  # shares the modification leaves to sell: total - this leg's fills
    capped_from: int | None = None  # working before the account-position cap bound
    problem: str = ""


@dataclass(frozen=True)
class ExitResult:
    outcome: str  # sent | unchanged (TWS already shows it) | pending (sent, not yet echoed) | nothing | blocked
    working: int = 0
    capped: bool = False


@dataclass
class PendingModify:
    """An exit modification TWS has not echoed back through openOrder yet."""

    decision_id: str
    leg: str
    purpose: str  # resize | flatten
    kind: str  # LMT | STP
    total: int
    price: Decimal
    sends: int = 1
    exhausted: bool = False


class StateError(RuntimeError):
    """The session's bridge state cannot be trusted; the bridge refuses to run."""


class ReconciliationError(RuntimeError):
    """TWS did not report open orders, executions and positions in time."""


def plan_exit(snapshot: FillSnapshot, leg: str) -> ExitPlan:
    """Total for a modification of one exit leg (pure; the oversell rule).

    The target's total is entry fills - stop fills and the stop's is entry fills
    - target fills, so a leg's own fills never feed its total: a target fill
    that lands after the snapshot cannot inflate the target's total. Exit fills
    the snapshot cannot attribute to a leg are charged to the other leg, which
    can only make this leg's total too small.
    """
    own, other = (snapshot.target, snapshot.stop) if leg == "target" else (snapshot.stop, snapshot.target)
    exits = max(snapshot.recorded_exits, snapshot.target + snapshot.stop)
    if exits > snapshot.parent:
        return ExitPlan(problem=f"exit fills {exits} exceed entry fills {snapshot.parent}")
    unattributed = exits - snapshot.target - snapshot.stop
    total = snapshot.parent - other - unattributed
    if not own <= total <= snapshot.parent:  # tripwire: unreachable unless the rule above breaks
        return ExitPlan(problem=f"exit total {total} inconsistent with entry fills {snapshot.parent}")
    if snapshot.position is None:
        return ExitPlan(problem="account position unknown")
    working = total - own
    held = max(0, int(snapshot.position))  # a short or empty account has nothing to sell
    if working > held:
        return ExitPlan(total=own + held, working=held, capped_from=working)
    return ExitPlan(total=total, working=working)


def parse_intent(payload_json: str) -> DecisionIntent | None:
    """The risk-approved trade intent of a decision payload; None when there is none."""
    payload = json.loads(payload_json)
    risk = payload.get("risk") if isinstance(payload, dict) else None
    intent = risk.get("intent") if isinstance(risk, dict) else None
    if not isinstance(intent, dict) or not intent:
        return None
    quantity = Decimal(str(intent["quantity"]))
    if not quantity.is_finite() or quantity != quantity.to_integral_value():
        raise ValueError("trade intent quantity is not a whole number")
    raw_expiry = intent.get("expires_at")
    expires_at = parse_time(raw_expiry)
    if raw_expiry is not None and expires_at is None:
        raise ValueError("trade intent expiry is unreadable")
    fingerprint = (
        intent.get("setup_fingerprint") or risk.get("setup_fingerprint") or payload.get("setup_fingerprint") or ""
    )
    return DecisionIntent(
        quantity=int(quantity),
        entry=Decimal(str(intent["entry_limit"])),
        stop=Decimal(str(intent["stop_price"])),
        target=Decimal(str(intent["target_price"])),
        expires_at=expires_at,
        setup_fingerprint=str(fingerprint),
    )


def execution_key(exec_id: str) -> tuple[str, int]:
    """IBKR marks a correction by changing only the digits after the execId's final period."""
    base, dot, suffix = exec_id.rpartition(".")
    if dot and base and suffix.isdigit():
        return base, int(suffix)
    return exec_id, 0


def execution_day(value: object) -> date | None:
    """Trade date of an execution time such as '20261013  10:00:05' (TWS local time)."""
    text = str(value or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def execution_matches(row: ExecutionRow, account: str | None, day: date | None,
                      ref_prefix: str | None) -> bool:
    # A field TWS left empty cannot prove the execution belongs elsewhere, so it never drops a fill.
    if account and row.account and row.account != account:
        return False
    if day is not None and row.day is not None and row.day != day:
        return False
    if ref_prefix and row.order_ref and not row.order_ref.startswith(ref_prefix):
        return False
    return True


def not_flat_message(bracket: Bracket) -> str:
    if bracket.net_held < 0:
        return (f"{bracket.symbol}: exits exceed entry fills by {-bracket.net_held} sh — the account may be"
                " short; act in TWS")
    if bracket.status == "open":
        return f"{bracket.symbol}: still open after flatten ({bracket.net_held} sh) — act in TWS"
    return f"{bracket.symbol}: unreconciled bracket, {bracket.net_held} sh per bridge state — act in TWS"


# ---- state -------------------------------------------------------------------


def ensure_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise StateError(f"{path} must not be a symbolic link")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def atomic_write(target: Path, text: str) -> None:
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def parse_brackets(raw: object) -> dict[str, Bracket]:
    if not isinstance(raw, dict):
        raise StateError("bridge state has no submitted map")
    brackets: dict[str, Bracket] = {}
    for decision_id, item in raw.items():
        if not isinstance(item, dict):
            raise StateError(f"bridge state entry {decision_id} is not an object")
        if "qty" not in item:  # v1 entry: never resend, never manage
            brackets[decision_id] = Bracket(
                decision_id=decision_id, symbol=item.get("symbol", "?"), con_id=0,
                primary_exchange="", strategy="orb", qty=0, entry="0", stop="0", target="0",
                parent=item.get("parent"), skipped=True, status="skipped",
                skip_reason="legacy entry",
            )
            continue
        try:
            brackets[decision_id] = Bracket(**{k: v for k, v in item.items() if k in BRACKET_FIELDS})
        except TypeError as exc:
            raise StateError(f"bridge state entry {decision_id} is malformed: {exc}") from exc
    return brackets


class StateStore:
    """One state file per New York session date, mirrored for the dashboard."""

    def __init__(self, *, state_dir: Path, legacy_path: Path | None, session_date: date) -> None:
        self.state_dir = state_dir
        self.legacy_path = legacy_path
        self.session_date = session_date
        self._last_text: str | None = None
        self._mirror_failed = False

    @property
    def path(self) -> Path:
        return self.state_dir / f"{self.session_date.isoformat()}.json"

    def load(self) -> dict[str, Bracket]:
        """Read this session's file only; the legacy mirror is never read back."""
        if self.path.is_symlink():
            raise StateError(f"{self.path} must not be a symbolic link")
        if not self.path.exists():
            return {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise StateError(f"{self.path} is unreadable: {exc}") from exc
        if not isinstance(document, dict):
            raise StateError(f"{self.path} is not a state document")
        stored = document.get("session_date")
        if stored is not None and stored != self.session_date.isoformat():
            raise StateError(f"{self.path} holds session {stored}, not {self.session_date}")
        return parse_brackets(document.get("submitted", {}))

    def save(self, brackets: dict[str, Bracket]) -> None:
        text = json.dumps(
            {
                "schema": STATE_SCHEMA,
                "session_date": self.session_date.isoformat(),
                "submitted": {key: asdict(value) for key, value in brackets.items()},
            },
            indent=2,
        )
        if text == self._last_text:
            return
        ensure_private_dir(self.state_dir)
        atomic_write(self.path, text)
        self._last_text = text
        if self.legacy_path is None:
            return
        try:
            atomic_write(self.legacy_path, text)
        except OSError as exc:  # the dashboard view must never stop order management
            if not self._mirror_failed:
                log("INFO", f"dashboard mirror {self.legacy_path} not written: {exc}")
            self._mirror_failed = True


# ---- ledger reads (read-only) ----------------------------------------------------


READ_ONLY_OPEN_RETRIES = 3  # a database being replaced or re-journaled is retried, briefly
READ_ONLY_OPEN_RETRY_S = 0.25


def _read_only(db: Path) -> sqlite3.Connection:
    """A read-only connection (``mode=ro``), shared by every ledger read, the fences and --preflight.

    "unable to open database file" while the file exists is retried a few times
    (a migration or backup replacing the file, a WAL being re-journaled); a
    missing file or any other error raises at once, so every caller stays
    fail-closed on a database that is really unreadable.
    """
    uri = f"{db.expanduser().resolve().as_uri()}?mode=ro"
    for attempt in range(READ_ONLY_OPEN_RETRIES):
        try:
            return sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.OperationalError as exc:
            transient = "unable to open database file" in str(exc) and db.expanduser().is_file()
            if not transient or attempt == READ_ONLY_OPEN_RETRIES - 1:
                raise
            time.sleep(READ_ONLY_OPEN_RETRY_S)
    raise AssertionError("unreachable")


DECISIONS_SQL = """
    SELECT d.id, d.run_id, c.symbol, c.con_id, c.contract_json, d.payload_json,
           d.created_at, v.spec_json
    FROM autotrader_candidate_decisions d
    JOIN autotrader_runs r ON r.id = d.run_id
    JOIN autotrader_candidates c ON c.id = d.candidate_id
    LEFT JOIN autotrader_strategy_versions v
      ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
    WHERE d.outcome = 'shadow_accepted' AND r.session_date = ?
    ORDER BY d.created_at, d.id
"""
# Same predicate as trading/storage/runtime_fence.py SQLitePaperKillStore.is_stopped.
KILL_FENCE_SQL = "SELECT stopped FROM autotrader_execution_fences WHERE name='paper'"
# One row per identity, name 'paper:<IDENTITY>' (autotraderctl paper stop/resume --identity).
IDENTITY_FENCE_SQL = "SELECT stopped FROM autotrader_execution_fences WHERE name=?"
SNAPSHOT_SQL = """
    SELECT observed_at, payload_json FROM autotrader_screener_snapshots
    WHERE session_date = ? ORDER BY observed_at DESC, id DESC LIMIT ?
"""


def strategy_name_from_spec(spec_json: object) -> str:
    if not isinstance(spec_json, str):
        return ""
    try:
        spec = json.loads(spec_json)
    except ValueError:
        return ""
    name = spec.get("name") if isinstance(spec, dict) else None
    return name.strip() if isinstance(name, str) else ""


def accepted_decisions(db: Path, session_date: date) -> list[DecisionRow]:
    with closing(_read_only(db)) as connection:
        rows = connection.execute(DECISIONS_SQL, (session_date.isoformat(),)).fetchall()
    return [
        DecisionRow(
            decision_id=str(row[0]), run_id=str(row[1]), symbol=str(row[2]), con_id=int(row[3]),
            contract_json=str(row[4]), payload_json=str(row[5]), created_at=str(row[6]),
            strategy_name=strategy_name_from_spec(row[7]),
        )
        for row in rows
    ]


def paper_kill_switch_active(db: Path) -> bool:
    with closing(_read_only(db)) as connection:
        row = connection.execute(KILL_FENCE_SQL).fetchone()
    return bool(row and int(row[0]))


def identity_fence_active(db: Path, identity: str) -> bool:
    with closing(_read_only(db)) as connection:
        row = connection.execute(IDENTITY_FENCE_SQL, (f"paper:{identity}",)).fetchone()
    return bool(row and int(row[0]))


def fenced_identities(db: Path) -> list[str]:
    """The sendable identities whose own fence is stopped (raises when the fence is unreadable)."""
    return sorted(name for name in SENDABLE_STRATEGY_NAMES if identity_fence_active(db, name))


def log_fenced_identities(db: Path) -> None:
    fenced = fenced_identities(db)
    log("INFO" if not fenced else "CRITICAL", f"fenced identities = {', '.join(fenced) or 'none'}")


def screener_quote(db: Path, session_date: date, symbol: str, con_id: int) -> SnapshotQuote | None:
    """Newest same-session screener row for the symbol: the lower of bid and last."""
    with closing(_read_only(db)) as connection:
        snapshots = connection.execute(
            SNAPSHOT_SQL, (session_date.isoformat(), SNAPSHOT_LOOKBACK)
        ).fetchall()
    for observed_at, payload_json in snapshots:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        rows = payload.get("rows") if isinstance(payload, dict) else None
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            if row.get("symbol") != symbol and not (con_id > 0 and row.get("con_id") == con_id):
                continue
            prices = [
                price
                for price in (positive_decimal(row.get("bid")), positive_decimal(row.get("last")))
                if price is not None
            ]
            if prices:
                return SnapshotQuote(price=min(prices), observed_at=str(observed_at))
    return None


# ---- IBKR -------------------------------------------------------------------------


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and abs(number) < Decimal("1e12") else None  # 9.81 UNSET is 1.8e308


def _whole(value: object) -> int:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return int(number) if number.is_finite() else 0


class BrokerState:
    """IBKR callback state shared between the API reader thread and the bridge loop."""

    def __init__(self, client_id: int) -> None:
        self.client_id = client_id
        self.lock = threading.RLock()
        self.next_id: int | None = None
        self.accounts: list[str] = []
        self.ready = threading.Event()
        self.order_status: dict[int, str] = {}
        self.order_filled: dict[int, int] = {}  # orderStatus cumulative filled, never decreasing
        self.open_orders: dict[int, OpenOrderRow] = {}
        self.order_objects: dict[int, Any] = {}  # the Order TWS last reported per order id
        # openOrderEnd carries no request id: open-orders requests (reqOpenOrders, reqAllOpenOrders)
        # and their ends are counted, and a request is answered once every request up to it is.
        self.open_orders_requested = 0
        self.open_orders_answered = 0  # never above open_orders_requested: an end never banks
        self.open_orders_answer = threading.Condition(self.lock)
        self.execs: dict[str, ExecutionRow] = {}  # execId without its correction suffix -> newest
        self.exec_done = threading.Event()
        self.bids: dict[int, Decimal] = {}
        self.net_liq_by_account: dict[str, Decimal] = {}
        self.summary_done = threading.Event()
        self.positions: dict[tuple[str, int], Decimal] = {}  # (account, conId) -> shares
        self.position_updates = 0  # every position callback so far
        self.exec_arrivals = 0  # every execution of this client so far
        self.reconciled_arrival = 0  # executions up to here are in the startup position snapshot
        self.position_contracts: dict[tuple[str, int], tuple[str, str, str, str]] = {}  # symbol, type, ccy, exch
        self.position_seq: dict[tuple[str, int], int] = {}  # (account, conId) -> position_updates at its last update
        self.positions_done = threading.Event()
        self.own_order_ids: set[int] = set()
        self.order_errors: dict[int, int] = {}  # own order id -> non-informational TWS errors so far
        self.error_details: dict[int, tuple[int, str]] = {}  # own order id -> last such (code, message)
        self.order_refs: dict[int, str] = {}  # own order id -> orderRef
        self.account_orders: dict[tuple[int, int, int], AccountOrder] = {}  # every client's open orders
        self.quotes: dict[int, dict[str, Decimal]] = {}  # market data request -> bid/ask/last
        self.delayed_quotes: set[int] = set()
        self.data_types: dict[int, int] = {}  # market data request -> marketDataType (1 = real time)
        self.completed_refs: dict[str, str] = {}  # orderRef -> final status of a completed API order
        self.completed_fills: dict[str, int] = {}  # orderRef -> shares a completed API order filled
        self.completed_done = threading.Event()
        self.position_avg_cost: dict[tuple[str, int], Decimal] = {}  # (account, conId) -> IBKR avgCost per share
        self.position_basis: dict[tuple[str, int], Decimal] = {}  # the last avgCost of a non-zero position
        # reqPnL account realizedPnL: the last value IBKR reported on this connection (an unset
        # report never erases it, LOW-2), and time.monotonic() of that report; None while unset.
        self.pnl_realized: Decimal | None = None
        self.pnl_at: float | None = None

    def nextValidId(self, order_id: int) -> None:
        with self.lock:
            self.next_id = max(self.next_id or 0, int(order_id))
        self.ready.set()

    def managedAccounts(self, accounts: str) -> None:
        self.accounts = [account.strip() for account in str(accounts).split(",") if account.strip()]

    def orderStatus(self, order_id: int, status: str, filled: Any, remaining: Any,
                    avg_price: Any, *args: Any) -> None:
        # 9.81 passes permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice after these.
        perm_id = _whole(args[0]) if args else 0
        client = _whole(args[3]) if len(args) > 3 else self.client_id
        with self.lock:
            for key, account_order in self.account_orders.items():
                if (perm_id and account_order.perm_id == perm_id) or key == (client, int(order_id), 0):
                    account_order.status = str(status)
                    account_order.remaining = _decimal_or_none(remaining)
        if client != self.client_id:
            return  # another client's order: its ids may collide with ours
        shares = _whole(filled)
        with self.lock:
            previous = self.order_status.get(int(order_id))
            self.order_status[int(order_id)] = str(status)
            self.order_filled[int(order_id)] = max(self.order_filled.get(int(order_id), 0), shares)
        if previous != str(status):
            log("INFO", f"order {order_id}: {status} filled={filled} remaining={remaining} avg={avg_price}")

    def openOrder(self, order_id: int, contract: Any, order: Any, state: Any) -> None:
        status = str(getattr(state, "status", "") or "")
        row = OpenOrderRow(
            status=status, symbol=str(getattr(contract, "symbol", "") or ""),
            con_id=_whole(getattr(contract, "conId", 0) or 0),
        )
        client = self.client_id if order is None else _whole(getattr(order, "clientId", 0))
        with self.lock:
            if order is not None:
                perm_id = _whole(getattr(order, "permId", 0))
                self.account_orders[(client, int(order_id), perm_id)] = AccountOrder(
                    client_id=client, order_id=int(order_id), perm_id=perm_id,
                    account=str(getattr(order, "account", "") or ""), con_id=row.con_id, symbol=row.symbol,
                    action=str(getattr(order, "action", "") or ""),
                    order_type=str(getattr(order, "orderType", "") or ""),
                    order_ref=str(getattr(order, "orderRef", "") or ""),
                    total=_decimal_or_none(getattr(order, "totalQuantity", 0)) or Decimal("0"), status=status,
                    aux_price=_decimal_or_none(getattr(order, "auxPrice", None)),
                    parent_id=_whole(getattr(order, "parentId", 0) or 0),
                )
            if client != self.client_id:
                return  # another client's (or TWS's) order: never mixed into this client's order ids
            self.open_orders[int(order_id)] = row
            if order is not None:
                self.order_objects[int(order_id)] = order
                ref = str(getattr(order, "orderRef", "") or "")
                if ref:
                    self.order_refs[int(order_id)] = ref
            if status:
                self.order_status.setdefault(int(order_id), status)

    def completedOrder(self, contract: Any, order: Any, state: Any) -> None:
        ref = str(getattr(order, "orderRef", "") or "")
        if ref.startswith(MANUAL_REF_PREFIX) and _whole(getattr(order, "parentId", 0) or 0) > 0:
            return  # a buy's attached stop shares the buy's orderRef: its record is never the buy's
        if ref:
            filled = _decimal_or_none(getattr(order, "filledQuantity", None))
            with self.lock:
                self.completed_refs[ref] = str(getattr(state, "status", "") or "")
                if filled is not None and filled > 0:
                    self.completed_fills[ref] = int(filled)

    def completedOrdersEnd(self) -> None:
        self.completed_done.set()

    def marketDataType(self, request_id: int, market_data_type: int) -> None:
        with self.lock:
            self.data_types[int(request_id)] = _whole(market_data_type)

    def openOrderEnd(self) -> None:
        # N18 MEDIUM-2. ibapi 9.81 decodes message 53 (OPEN_ORDER_END) with no field at all, and TWS
        # may send one unasked (with the open orders it reports at connect), so an end is counted
        # only against a request still unanswered: it never completes a later request early. TWS
        # answers requests in order, so an end that leaves a newer request unanswered closes an
        # older answer, which may have straddled the newer request's clear: its orders are dropped.
        with self.open_orders_answer:
            if self.open_orders_answered < self.open_orders_requested:
                self.open_orders_answered += 1
                if self.open_orders_answered < self.open_orders_requested:
                    self.account_orders.clear()
            self.open_orders_answer.notify_all()

    def execDetails(self, request_id: int, contract: Any, execution: Any) -> None:
        # Only this client's orders: never the owner's manual TWS trades.
        if int(getattr(execution, "clientId", -1)) != self.client_id:
            return
        exec_id = str(getattr(execution, "execId", "") or "")
        base, version = execution_key(exec_id)
        try:
            row = ExecutionRow(
                order_id=int(execution.orderId),
                shares=int(Decimal(str(execution.shares))),
                price=Decimal(str(execution.price)),
                account=str(getattr(execution, "acctNumber", "") or ""),
                order_ref=str(getattr(execution, "orderRef", "") or ""),
                day=execution_day(getattr(execution, "time", "")),
                version=version,
                con_id=_whole(getattr(contract, "conId", 0) or 0),
                side=str(getattr(execution, "side", "") or ""),
            )
        except (InvalidOperation, TypeError, ValueError):
            log("INFO", f"unreadable execution {exec_id or '?'} ignored")
            return
        with self.lock:
            current = self.execs.get(base)
            if current is not None and current.version > version:
                return  # the original of an execution that was already corrected
            # Stamped here, as it arrives: the position update TWS sends with a fill often lands
            # before the bridge's next loop, and only a later update proves the fill is shown.
            self.exec_arrivals += 1
            basis = self.position_basis.get((row.account, row.con_id))
            self.execs[base] = replace(row, arrival=self.exec_arrivals, positions_seen=self.position_updates,
                                       basis=basis)
        if current is not None and version > current.version:
            log("INFO", f"execution {exec_id} corrects order {row.order_id}: {current.shares} -> {row.shares} sh")

    def execDetailsEnd(self, request_id: int) -> None:
        self.exec_done.set()

    def position(self, account: str, contract: Any, position: Any, avg_cost: Any) -> None:
        try:
            shares = Decimal(str(position))
        except (InvalidOperation, TypeError, ValueError):
            shares = Decimal("NaN")
        con_id = _whole(getattr(contract, "conId", 0) or 0)
        if not shares.is_finite() or con_id <= 0:
            log("INFO", f"unreadable position {position!r} for {account} ignored")
            return
        cost = _decimal_or_none(avg_cost)  # v7c: held x (avgCost - stop); 9.81 sends UNSET as 1.8e308
        with self.lock:
            self.positions[(str(account), con_id)] = shares
            self.position_updates += 1
            self.position_seq[(str(account), con_id)] = self.position_updates
            if cost is not None and cost > 0:
                self.position_avg_cost[(str(account), con_id)] = cost
                if shares != 0:
                    self.position_basis[(str(account), con_id)] = cost
            else:
                self.position_avg_cost.pop((str(account), con_id), None)
            self.position_contracts[(str(account), con_id)] = (
                str(getattr(contract, "symbol", "") or ""), str(getattr(contract, "secType", "") or ""),
                str(getattr(contract, "currency", "") or ""),
                str(getattr(contract, "primaryExchange", "") or getattr(contract, "exchange", "") or ""),
            )

    def positionEnd(self) -> None:
        self.positions_done.set()

    def tickPrice(self, request_id: int, tick_type: int, price: float, *args: Any) -> None:
        name = QUOTE_TICKS.get(int(tick_type))
        if name is None or not price or price <= 0:
            return
        value = Decimal(str(price))
        with self.lock:
            self.quotes.setdefault(request_id, {})[name] = value
            if tick_type >= 66:
                self.delayed_quotes.add(request_id)
            elif tick_type in (1, 4) and (tick_type == 1 or request_id not in self.bids):
                self.bids[request_id] = value  # the flatten's price: the live bid, else the last trade

    def accountSummary(self, request_id: int, account: str, tag: str, value: str,
                       currency: str) -> None:
        amount = positive_decimal(value) if tag == "NetLiquidation" else None
        if amount is not None:
            with self.lock:
                self.net_liq_by_account[str(account)] = amount

    def accountSummaryEnd(self, request_id: int) -> None:
        self.summary_done.set()

    def pnl(self, request_id: int, daily_pnl: Any, unrealized_pnl: Any, realized_pnl: Any) -> None:
        # 9.81 decodes realizedPnL as None below server version 135 and an unset value as
        # sys.float_info.max: both, and NaN or inf, mean "not reported" (spike V-2).
        if request_id != PNL_REQUEST_ID:
            return
        value = _decimal_or_none(realized_pnl) if realized_pnl is not None else None
        with self.lock:
            if value is not None:
                self.pnl_realized = value
            self.pnl_at = time.monotonic() if value is not None else None

    def error(self, request_id: int, code: Any, message: Any, *args: Any) -> None:
        try:
            number = int(code)
        except (TypeError, ValueError):
            number = -1
        with self.lock:
            ours = request_id in self.own_order_ids
            if ours and number not in ORDER_INFO_CODES:
                self.order_errors[request_id] = self.order_errors.get(request_id, 0) + 1
                self.error_details[request_id] = (number, " ".join(str(message).split()))
        if ours:  # a rejected order, modify or cancel of ours is never routine
            log("INFO" if number in ORDER_INFO_CODES else "CRITICAL", f"TWS {request_id} {code} {message}")
            return
        if number in FARM_BROKEN_CODES:
            log("INFO", f"TWS {request_id} {code} {message}")
            return
        if 2100 <= number < 2200 and number not in CRITICAL_TWS_CODES:
            return  # data-farm "connection is OK" notices
        log("CRITICAL" if number in CRITICAL_TWS_CODES else "INFO", f"TWS {request_id} {code} {message}")

    def fills_for(self, order_id: int | None, *, account: str | None = None, day: date | None = None,
                  ref_prefix: str | None = None) -> tuple[int, Decimal]:
        if order_id is None:
            return 0, Decimal("0")
        with self.lock:
            rows = [
                row for row in self.execs.values()
                if row.order_id == order_id and execution_matches(row, account, day, ref_prefix)
            ]
        quantity = sum(row.shares for row in rows)
        if quantity <= 0:
            return 0, Decimal("0")
        value = sum((Decimal(row.shares) * row.price for row in rows), Decimal("0"))
        return quantity, average(value, quantity)


class App(BrokerState, EWrapper, EClient):
    def __init__(self, client_id: int) -> None:
        EClient.__init__(self, self)
        BrokerState.__init__(self, client_id)


def stock(con_id: int, primary_exchange: str) -> Contract:
    contract = Contract()
    contract.conId = con_id
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.primaryExchange = primary_exchange
    return contract


def order(action: str, order_type: str, qty: int, account: str, ref: str, **fields: Any) -> Order:
    result = Order()
    result.action = action
    result.orderType = order_type
    result.totalQuantity = qty
    result.account = account
    result.orderRef = ref
    result.eTradeOnly = False  # rejected by current TWS when left at the 9.81 default
    result.firmQuoteOnly = False
    for name, value in fields.items():
        setattr(result, name, value)
    return result


# ---- bridge -----------------------------------------------------------------------


class Bridge:
    def __init__(
        self,
        app: Any,
        account: str,
        *,
        session_date: date,
        store: StateStore,
        brackets: dict[str, Bracket],
        db: Path,
        clock: Callable[[], datetime] | None = None,
        stop_event: threading.Event | None = None,
        times: SessionTimes | None = None,
    ) -> None:
        self.app = app
        self.account = account
        self.session_date = session_date
        self.store = store
        self.brackets = brackets
        self.db = db
        self.clock = clock if clock is not None else now_ny
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.quote_req = QUOTE_REQUEST_BASE
        self.quote_timeout = QUOTE_TIMEOUT_S
        self.sync_timeout = SYNC_TIMEOUT_S
        self.loop_seconds = LOOP_SECONDS
        self.decisions: Callable[[], list[DecisionRow]] = (
            lambda: accepted_decisions(self.db, self.session_date)
        )
        self.kill_switch_active: Callable[[], bool] = lambda: paper_kill_switch_active(self.db)
        self.identity_fence_active: Callable[[str], bool] = (
            lambda identity: identity_fence_active(self.db, identity)
        )
        self.fallback_quote: Callable[[Bracket], SnapshotQuote | None] = (
            lambda bracket: screener_quote(self.db, self.session_date, bracket.symbol, bracket.con_id)
        )
        # Each intake runs last in a tick and fails alone: order management never waits on it.
        self.intakes: list[tuple[str, Callable[[], None]]] = [("decision", self.intake_decisions)]
        self.intake_failures: dict[str, int] = {}
        self.times = times if times is not None else session_times(session_date)
        self.manual: ManualDesk | None = None  # owner orders; attached by attach_manual_desk()
        self.flattened = False
        self.alerts_done: set[str] = set()
        self.alert_raised = False
        self.alerted: set[tuple[str, ...]] = set()
        self.unmanaged: dict[int, tuple[str, int]] = {}  # live client orders outside the state
        self.pending_modifies: dict[int, PendingModify] = {}  # exit modifications TWS has not echoed
        self.error_marks: dict[int, int] = {}  # order id -> TWS error count when we last modified it
        self.position_gaps: dict[str, datetime] = {}  # decision id -> first tick the account held less
        self.dangling_since: dict[int, datetime] = {}  # exit order id -> first tick live on a finished bracket
        # v7c: the budget is checked and reserved only on the thread that runs the loop (never a callback).
        self.loop_thread = threading.current_thread()
        self.order_book: list[AccountOrder] = []  # every client's working orders, from the last full refresh
        self.order_book_at: datetime | None = None
        self.order_book_failures = 0
        self.pnl_source = ""  # reqpnl | computed: logged once per change
        self.pnl_mismatch = False
        self.pnl_stale = False  # IBKR's last value is not fresh: logged once per episode
        self.stops_seen: dict[tuple[int, int], Decimal] = {}  # (client, order id) -> stop price last seen
        self.unprotected_since: dict[tuple[str, ...], datetime] = {}
        self.attention_gaps: dict[str, datetime] = {}  # needs_attention bracket -> first read the account held less
        self.open_orders_sent: dict[int, datetime] = {}  # open-orders request number -> when it was sent
        self.watch_failures = 0
        with self.app.lock:  # rejections of restored legs must be CRITICAL too
            for bracket in brackets.values():
                self.app.own_order_ids.update(bracket.legs)

    # ---- helpers -------------------------------------------------------
    def now(self) -> datetime:
        return self.clock().astimezone(NY)

    def past(self, moment: datetime) -> bool:
        return self.now() >= moment

    def entry_deadline(self, bracket: Bracket) -> datetime:
        """The bracket's last entry moment: a sendable identity's caps row (an offset from the
        real close), else its family cutoff, never later than 30 min before the close."""
        caps = IDENTITY_CAPS.get(bracket.strategy_name.strip()) if may_send_orders(bracket.strategy_name) else None
        if caps is not None:  # the row decides, but never later than the session's own last entry
            return min(self.times.last_entry, self.times.closes_at - caps.last_entry_before_close)
        cutoff = STRATEGY_CUTOFFS.get(bracket.strategy)
        last = self.times.last_entry
        return last if cutoff is None else min(last, datetime.combine(self.session_date, cutoff, tzinfo=NY))

    def positions_of(self, identity: str) -> int:
        return sum(1 for b in self.brackets.values()
                   if b.strategy_name.strip() == identity and self.holds_or_may_hold(b))

    def entries_today(self, identity: str) -> int:
        """Entries this session sent for the identity, whatever became of them: an attempt is an attempt."""
        return sum(1 for b in self.brackets.values() if b.strategy_name.strip() == identity and b.parent is not None)

    def attach_manual_desk(self, desk: "ManualDesk") -> None:
        """Owner orders become a second intake, isolated like the first (never ahead of the flatten)."""
        self.manual = desk
        self.intakes.append(("manual order", desk.tick))

    def ref(self, bracket: Bracket) -> str:
        return f"{ORDER_REF_PREFIX}{bracket.strategy_name.strip()}:{bracket.decision_id[:12]}"

    def active(self) -> list[Bracket]:
        return [b for b in self.brackets.values() if b.active]

    def realized_pnl(self) -> Decimal:
        """The bot brackets' realized P&L of this date (no commissions); the account's is account_realized_loss."""
        # Only this date's brackets are loaded, so the loss stop never sees yesterday.
        return sum((Decimal(b.realized_pnl) for b in self.brackets.values()), Decimal("0"))

    @property
    def net_liq(self) -> Decimal | None:
        with self.app.lock:
            return self.app.net_liq_by_account.get(self.account)

    def save(self) -> None:
        self.store.save(self.brackets)

    def alert_once(self, key: tuple[str, ...], message: str, kind: str = "CRITICAL") -> None:
        if key not in self.alerted:
            self.alerted.add(key)
            log(kind, message)

    def gap_age(self, bracket: Bracket) -> timedelta:
        """How long the account has held fewer shares than this bracket; every gap alert shares it."""
        now = self.now()
        return now - self.position_gaps.setdefault(bracket.decision_id, now)

    def leg_id(self, bracket: Bracket, leg: str) -> int | None:
        return bracket.target_id if leg == "target" else bracket.stop_id

    def leg_live(self, order_id: int | None) -> bool:
        return order_id is not None and self.app.order_status.get(order_id) in LIVE_ORDER_STATUSES

    def leg_fills(self, bracket: Bracket, order_id: int | None) -> tuple[int, Decimal]:
        """Executions of this session, account and bridge order ref; never above the order size."""
        quantity, price = self.app.fills_for(
            order_id, account=self.account, day=self.session_date, ref_prefix=ORDER_REF_PREFIX
        )
        if quantity > bracket.qty > 0:  # IBKR cannot fill more than the order's quantity
            self.alert_once(
                (bracket.decision_id, "overfill", str(order_id)),
                f"{bracket.symbol}: executions for order {order_id} add up to {quantity} sh, more than its"
                f" {bracket.qty} — counted as {bracket.qty}; check TWS",
            )
            quantity = bracket.qty
        return quantity, price

    def account_position(self, con_id: int) -> Decimal | None:
        with self.app.lock:
            if not self.app.positions_done.is_set():
                return None
            return self.app.positions.get((self.account, con_id), Decimal("0"))

    def fill_snapshot(self, bracket: Bracket) -> FillSnapshot:
        """One locked read of fills, order statuses and the account position."""
        with self.app.lock:
            def leg(order_id: int | None) -> int:
                if order_id is None:
                    return 0
                executed, _ = self.leg_fills(bracket, order_id)
                return min(bracket.qty, max(executed, self.app.order_filled.get(order_id, 0)))

            return FillSnapshot(
                parent=max(bracket.filled, leg(bracket.parent)),
                target=leg(bracket.target_id),
                stop=leg(bracket.stop_id),
                recorded_exits=bracket.exit_filled,
                position=self.account_position(bracket.con_id),
            )

    def positions_not_flat(self) -> list[Bracket]:
        return [
            b for b in self.brackets.values()
            if b.status in {"open", "needs_attention"} and b.net_held != 0
        ]

    def holds_or_may_hold(self, bracket: Bracket) -> bool:
        return bracket.active or (bracket.status == "needs_attention" and bracket.filled > 0)

    def unmanaged_live(self) -> list[tuple[str, int]]:
        """(symbol, conId) of live client orders the bridge does not manage."""
        with self.app.lock:
            return [
                target for order_id, target in self.unmanaged.items()
                if self.app.order_status.get(order_id) not in DEAD_ORDER_STATUSES
            ]

    def slots_used(self) -> int:
        holders = [b for b in self.brackets.values() if self.holds_or_may_hold(b)]
        held = {b.symbol for b in holders}
        extra = {symbol for symbol, _ in self.unmanaged_live() if symbol not in held}
        return len(holders) + len(extra)

    def symbol_problem(self, symbol: str, con_id: int) -> str | None:
        # An unreconciled bracket may still hold shares, so its symbol stays blocked.
        if any(b.symbol == symbol and (b.active or b.status == "needs_attention")
               for b in self.brackets.values()):
            return "symbol already has an active bracket"
        if any(symbol == other or (con_id > 0 and con_id == other_con) for other, other_con in self.unmanaged_live()):
            return "symbol has a live order at IBKR the bridge does not manage"
        if self.manual is not None and symbol in self.manual.working_symbols():
            return "symbol has a working manual order"
        # The exit caps read the whole account position, so the bot only enters a symbol the
        # account does not hold at all: the owner's own shares can then never be sold by a bracket.
        held = self.account_position(con_id)
        if held is None:
            return "account position unknown"
        if held != 0:
            return f"account already holds {share_text(held)} — bot only trades symbols it alone holds"
        return None

    def bid(self, bracket: Bracket) -> Decimal | None:
        self.quote_req += 1
        request_id = self.quote_req
        self.app.reqMktData(request_id, stock(bracket.con_id, bracket.primary_exchange), "", False, False, [])
        value: Decimal | None = None
        deadline = time.monotonic() + self.quote_timeout
        while True:
            with self.app.lock:
                value = self.app.bids.get(request_id)
            if value is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        self.app.cancelMktData(request_id)
        return value

    # ---- lifecycle ----------------------------------------------------
    def refresh(self, bracket: Bracket) -> None:
        # needs_attention is sticky: IBKR could not confirm the bracket, so only
        # the owner can resolve it; the bridge never manages or re-sends it.
        if bracket.skipped or bracket.parent is None or bracket.status == "needs_attention":
            return
        filled, avg = self.leg_fills(bracket, bracket.parent)
        # Fills never decrease: executions can be missing after a TWS restart.
        if filled > bracket.filled:
            bracket.filled, bracket.avg_entry = filled, str(avg.quantize(Decimal("0.0001")))
            log("FILL", f"{bracket.symbol}: {filled}/{bracket.qty} sh @ {bracket.avg_entry} ({bracket.label})")
        exit_qty = 0
        exit_value = Decimal("0")
        exit_reason = bracket.exit_reason
        for leg, reason in ((bracket.target_id, "target"), (bracket.stop_id, "stop")):
            quantity, price = self.leg_fills(bracket, leg)
            if quantity > 0:
                exit_qty += quantity
                exit_value += Decimal(quantity) * price
                exit_reason = "flatten" if bracket.flatten_sent and reason == "target" else reason
        if exit_qty > bracket.exit_filled:
            bracket.exit_filled = exit_qty
            bracket.avg_exit = str(average(exit_value, exit_qty).quantize(Decimal("0.0001")))
            bracket.exit_reason = exit_reason
            entry_price = Decimal(bracket.avg_entry)
            if entry_price > 0:
                pnl = (Decimal(bracket.avg_exit) - entry_price) * exit_qty
                bracket.realized_pnl = str(pnl.quantize(Decimal("0.01")))
            log("EXIT", f"{bracket.symbol}: {exit_qty} sh @ {bracket.avg_exit} via {bracket.exit_reason}"
                f" | P&L {bracket.realized_pnl}")
        self._advance(bracket)

    def _advance(self, bracket: Bracket) -> None:
        if bracket.exit_filled > bracket.filled:
            self._mark_oversold(bracket)
            return
        parent_status = self.app.order_status.get(bracket.parent, "")
        if bracket.filled > 0:
            if bracket.exit_filled >= bracket.filled:
                if bracket.status != "closed":
                    bracket.status = "closed"
                    log("CLOSED", f"{bracket.symbol}: P&L {bracket.realized_pnl} ({bracket.exit_reason})")
                self._cancel_working_entry(bracket, parent_status)
            elif bracket.status == "closed":
                bracket.status = "open"
                log("CRITICAL", f"{bracket.symbol}: entry filled again after the bracket closed"
                    f" ({bracket.remaining} sh) — check TWS")
            elif bracket.status in {"sent", "cancelled"}:
                bracket.status = "open"  # a fill that raced the cancel is still a position
        elif parent_status in PARENT_CANCELLED_STATUSES and bracket.status == "sent":
            bracket.status = "cancelled"
            log("CANCELLED", f"{bracket.symbol}: entry never filled")

    def _mark_oversold(self, bracket: Bracket) -> None:
        """Exit fills above entry fills are never "closed": the account may be short."""
        bracket.status = "needs_attention"
        bracket.attention_reason = f"exit fills {bracket.exit_filled} exceed entry fills {bracket.filled}"
        log("CRITICAL", f"{bracket.symbol}: exits {bracket.exit_filled} sh exceed entry fills {bracket.filled} sh"
            f" — the account may be short {-bracket.net_held} sh; act in TWS")

    def _cancel_working_entry(self, bracket: Bracket, parent_status: str) -> None:
        """Exits closed the position while part of the entry still works: stop it."""
        if (bracket.parent_cancel_requested or bracket.filled >= bracket.qty
                or parent_status not in LIVE_ORDER_STATUSES):
            return
        self.app.cancelOrder(bracket.parent)
        bracket.parent_cancel_requested = True
        log("CANCEL", f"entry {bracket.symbol}: position closed while the entry was working"
            f" (filled {bracket.filled}/{bracket.qty})")

    def _new_bracket(self, row: DecisionRow) -> Bracket:
        try:
            contract = json.loads(row.contract_json)
            primary_exchange = str(contract.get("primary_exchange") or "") if isinstance(contract, dict) else ""
        except (TypeError, ValueError):
            primary_exchange = ""
        return Bracket(
            decision_id=row.decision_id, symbol=row.symbol, con_id=int(row.con_id),
            primary_exchange=primary_exchange, strategy=strategy_family(row.strategy_name),
            strategy_name=row.strategy_name, run_id=row.run_id,
            qty=0, entry="0", stop="0", target="0",
        )

    @staticmethod
    def _apply_intent(bracket: Bracket, intent: DecisionIntent | None) -> str | None:
        if intent is None:
            return "no trade intent"
        bracket.qty, bracket.entry, bracket.stop, bracket.target = (
            intent.quantity, str(intent.entry), str(intent.stop), str(intent.target)
        )
        bracket.setup_fingerprint = intent.setup_fingerprint
        if intent.quantity <= 0 or bracket.con_id <= 0 or not Decimal("0") < intent.stop < intent.entry < intent.target:
            return "invalid trade intent"
        return None

    def _expiry_problem(self, intent: DecisionIntent, row: DecisionRow) -> str | None:
        created = parse_time(row.created_at)
        if created is None:
            return "decision timestamp unreadable"
        now = self.now()
        age = (now - created).total_seconds()
        # The risk approval expires with the intent; without one the engine's 15 s applies.
        deadline = intent.expires_at or created + timedelta(seconds=DEFAULT_DECISION_TTL_S)
        if now >= deadline or age > MAX_DECISION_AGE_S:
            return f"decision expired ({age:.0f}s old)"
        return None

    def _size_problem(self, bracket: Bracket, net_liq: Decimal) -> str | None:
        quantity = Decimal(bracket.qty)
        notional = quantity * Decimal(bracket.entry)
        if notional > net_liq * MAX_ORDER_NOTIONAL_PCT / 100:
            return (f"order notional {notional:.2f} exceeds {MAX_ORDER_NOTIONAL_PCT}% of net liquidation"
                    f" {net_liq:.2f}")
        risk = quantity * (Decimal(bracket.entry) - Decimal(bracket.stop))
        if risk > net_liq * MAX_ORDER_RISK_PCT / 100:
            return f"order risk {risk:.2f} exceeds {MAX_ORDER_RISK_PCT}% of net liquidation {net_liq:.2f}"
        return None

    def _gate(self, bracket: Bracket, row: DecisionRow) -> tuple[DecisionIntent | None, str | None]:
        try:  # parsed first so skipped shadow-only entries still show their intent
            intent = parse_intent(row.payload_json)
            intent_problem = self._apply_intent(bracket, intent)
        except Exception as exc:
            intent, intent_problem = None, f"decision unreadable ({type(exc).__name__})"
        if not may_send_orders(row.strategy_name):
            return intent, SHADOW_ONLY
        identity = row.strategy_name.strip()
        caps = IDENTITY_CAPS.get(identity)
        if caps is None:  # granted but unlimited is never sent at the global limits
            self.alert_once(("identity_caps_missing", identity),
                            f"{identity}: sendable but has no caps row — its entries are skipped (fail closed)")
            return intent, f"identity_caps_missing:{identity}"
        if intent is None or intent_problem:
            return intent, intent_problem or "no trade intent"
        expired = self._expiry_problem(intent, row)
        if expired:
            return intent, expired
        now = self.now()
        if now >= self.times.last_entry:
            return intent, f"after {self.times.last_entry:%H:%M} — no new entries"
        if now >= self.entry_deadline(bracket):
            return intent, f"{bracket.strategy} entry window closed"
        if intent.setup_fingerprint:
            for other in self.brackets.values():
                if other.setup_fingerprint == intent.setup_fingerprint and other.parent is not None:
                    return intent, f"setup already sent (decision {other.decision_id})"
        symbol_problem = self.symbol_problem(bracket.symbol, bracket.con_id)
        if symbol_problem:
            return intent, symbol_problem
        if self.positions_of(identity) >= caps.max_positions:
            return intent, f"{identity}: max {caps.max_positions} positions already active"
        if self.slots_used() >= global_max_open():
            return intent, f"max {global_max_open()} positions/entries already active"
        if caps.max_entries_per_day is not None and self.entries_today(identity) >= caps.max_entries_per_day:
            return intent, f"{identity}: max {caps.max_entries_per_day} entries/day already sent"
        net_liq = self.net_liq
        if net_liq is None:
            return intent, "net liquidation unknown"
        loss = self.daily_loss_problem(self.account_realized_loss(), net_liq)
        if loss:
            return intent, loss
        return intent, self._size_problem(bracket, net_liq)

    def _skip(self, bracket: Bracket, reason: str, *, with_strategy: bool = True) -> None:
        bracket.skipped, bracket.status, bracket.skip_reason = True, "skipped", reason
        self.brackets[bracket.decision_id] = bracket
        self.save()
        if with_strategy:
            log("SKIP", f"{bracket.symbol} ({bracket.label}): {reason}")
        else:
            log("SKIP", f"{bracket.symbol}: {reason}")

    def intake_decisions(self) -> None:
        for row in self.decisions():
            if row.decision_id not in self.brackets:
                self.process_decision(row)

    def process_decision(self, row: DecisionRow) -> None:
        bracket = self._new_bracket(row)
        intent: DecisionIntent | None = None
        try:
            intent, reason = self._gate(bracket, row)
        except Exception as exc:  # a malformed decision must never crash-loop the bridge
            reason = f"decision unreadable ({type(exc).__name__})"
        if reason or intent is None:
            self._skip(bracket, reason or "no trade intent")
            return
        bid = self.bid(bracket)
        if bid is not None and bid <= Decimal(bracket.stop):
            self._skip(bracket, f"price {bid} already at/below stop {bracket.stop}")
            return
        expired = self._expiry_problem(intent, row)  # the quote wait can outlive the approval
        if expired:
            self._skip(bracket, expired)
            return
        # The fence is read last so it is as fresh as possible when orders leave.
        try:
            stopped = self.kill_switch_active()
        except Exception as exc:
            self._skip(bracket, f"kill switch unreadable ({type(exc).__name__}) — fail closed",
                       with_strategy=False)
            return
        if stopped:
            self._skip(bracket, "kill switch active", with_strategy=False)
            return
        identity = bracket.strategy_name.strip()
        try:
            identity_stopped = self.identity_fence_active(identity)
        except Exception as exc:
            self._skip(bracket, f"identity fence unreadable ({type(exc).__name__}) — fail closed",
                       with_strategy=False)
            return
        if identity_stopped:
            self._skip(bracket, f"identity_stopped:{identity}")
            return
        # v7c: the budget is read last, right before the order is stored and leaves, on this loop's
        # thread; submit() stores the bracket before placeOrder, so the next decision sees it.
        new_risk = Decimal(bracket.qty) * (Decimal(bracket.entry) - Decimal(bracket.stop))
        problem, budget = self.budget_check(new_risk, source="bot")
        if problem or budget is None:
            self._skip(bracket, f"{problem or V7C_DAY_RISK_BUDGET} — would have sent BUY {bracket.qty} @"
                       f" {bracket.entry} | stop {bracket.stop} | target {bracket.target}")
            return
        log("INFO", f"{V7C_BUDGET_OK} {bracket.symbol} ({bracket.label}): {budget.text()} <= {DAY_RISK_BUDGET_PCT}%"
            f" of {money(budget.net_liq)} (source=bot)")
        self.submit(bracket)
        self.check_budget_invariant(budget, f"{bracket.symbol} ({bracket.label})")

    def submit(self, bracket: Bracket) -> None:
        if self.stop_event.is_set():  # a SIGTERM during the quote wait: never send after a stop
            log("INFO", f"{bracket.symbol}: stop requested — entry not sent")
            return
        if not self.app.isConnected():
            return  # left unprocessed; the loop exits 75 and the restart re-evaluates it
        with self.app.lock:
            if self.app.next_id is None:
                raise RuntimeError("TWS has not provided a valid order id")
            parent_id = self.app.next_id
            self.app.next_id += 3
            self.app.own_order_ids.update({parent_id, parent_id + 1, parent_id + 2})
        contract = stock(bracket.con_id, bracket.primary_exchange)
        ref = self.ref(bracket)
        parent = order("BUY", "LMT", bracket.qty, self.account, ref, lmtPrice=float(bracket.entry),
                       tif="DAY", transmit=False)
        target = order("SELL", "LMT", bracket.qty, self.account, ref, lmtPrice=float(bracket.target),
                       tif="GTC", parentId=parent_id, transmit=False)
        stop = order("SELL", "STP", bracket.qty, self.account, ref, auxPrice=float(bracket.stop),
                     tif="GTC", parentId=parent_id, transmit=True)
        bracket.parent, bracket.target_id, bracket.stop_id = parent_id, parent_id + 1, parent_id + 2
        bracket.sent_at, bracket.status = self.now().astimezone(timezone.utc).isoformat(), "sent"
        # Persist before transmission so a crash can never cause a resend.
        self.brackets[bracket.decision_id] = bracket
        self.save()
        self.app.placeOrder(parent_id, contract, parent)
        self.app.placeOrder(parent_id + 1, contract, target)
        self.app.placeOrder(parent_id + 2, contract, stop)
        log("SENT", f"{bracket.symbol} ({bracket.label}): BUY {bracket.qty} @ {bracket.entry} | stop {bracket.stop}"
            f" | target {bracket.target} (orders {parent_id}-{parent_id + 2})")

    def expire_entries(self) -> None:
        now = self.now()
        for bracket in self.active():
            if bracket.parent_cancel_requested or bracket.filled >= bracket.qty:
                continue
            sent = parse_time(bracket.sent_at)
            expired = sent is None or now - sent >= ENTRY_TTL
            closed_window = now >= self.entry_deadline(bracket)
            if expired or closed_window:
                self.app.cancelOrder(bracket.parent)
                bracket.parent_cancel_requested = True
                log("CANCEL", f"entry {bracket.symbol}: {'not filled in 10 min' if expired else 'window closed'}"
                    f" (filled {bracket.filled}/{bracket.qty})")

    # ---- exit modifications (the only path that changes an exit leg) -------
    def exit_order(self, bracket: Bracket, leg: str, order_id: int, *, total: int, price: Decimal) -> Any | None:
        """The modification, encoded exactly like the placement TWS accepted.

        It is built by the same order() factory as the placement. From the
        order TWS last reported only the OCA link (ocaGroup, ocaType) is
        copied, after checking the order still has the shape the bridge sent:
        re-sending a decoded 9.81 Order re-encodes dozens of fields differently.
        """
        kind = "LMT" if leg == "target" else "STP"
        price_field = "lmtPrice" if kind == "LMT" else "auxPrice"
        modified = order("SELL", kind, total, self.account, self.ref(bracket), tif="GTC",
                         parentId=bracket.parent, transmit=True, **{price_field: float(price)})
        with self.app.lock:
            cached = self.app.order_objects.get(order_id)
        if cached is None:
            log("INFO", f"{bracket.symbol}: no openOrder from TWS for {leg} {order_id} yet — modification"
                " sent without its OCA fields")
            return modified
        action = str(getattr(cached, "action", "") or "")
        order_type = str(getattr(cached, "orderType", "") or "")
        account = str(getattr(cached, "account", "") or "")
        parent_id = _whole(getattr(cached, "parentId", 0) or 0)
        if (action != "SELL" or order_type != kind or account not in {"", self.account}
                or parent_id not in {0, bracket.parent}):
            self.alert_once(
                (bracket.decision_id, leg, "foreign-shape"),
                f"{bracket.symbol}: {leg} {order_id} at IBKR is {action or '?'} {order_type or '?'}"
                f" ({account or '?'}), not the bridge's SELL {kind} — not modified; act in TWS",
            )
            return None
        modified.ocaGroup = str(getattr(cached, "ocaGroup", "") or "")
        modified.ocaType = _whole(getattr(cached, "ocaType", 0) or 0)
        modified.parentId = parent_id or bracket.parent
        return modified

    def order_shows(self, order_id: int, total: int, price: Decimal, kind: str) -> bool:
        """True once TWS has echoed this quantity and price for the order through openOrder."""
        with self.app.lock:
            cached = self.app.order_objects.get(order_id)
        if cached is None or _whole(getattr(cached, "totalQuantity", 0)) != total:
            return False
        try:
            shown = Decimal(str(getattr(cached, "lmtPrice" if kind == "LMT" else "auxPrice")))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return shown.is_finite() and abs(shown - price) < TICK / 2

    def accepted(self, order_id: int, total: int, price: Decimal, kind: str) -> bool:
        """TWS shows these values and reported no error for the order since we last modified it."""
        with self.app.lock:
            errors = self.app.order_errors.get(order_id, 0)
        if errors > self.error_marks.get(order_id, errors):
            return False  # rejected: an echo of either version proves nothing
        return self.order_shows(order_id, total, price, kind)

    def report_cap(self, bracket: Bracket, leg: str, order_id: int, purpose: str,
                   snapshot: FillSnapshot, plan: ExitPlan) -> None:
        held = share_text(snapshot.position or Decimal("0"))
        outcome = f"limited to {plan.working} sh" if plan.working > 0 else "not sent"
        message = (f"{bracket.symbol}: account holds {held} sh but the bracket expects {plan.capped_from} —"
                   f" {purpose} of {leg} {order_id} {outcome}")
        key = (bracket.decision_id, leg, purpose, "capped", str(plan.working))
        if self.gap_age(bracket) >= POSITION_GAP_GRACE:
            self.alert_once(key, f"{message}; act in TWS")
        else:  # a position update may still be on its way: the cap applies, the alarm waits
            self.alert_once((*key, "early"), f"{message} — re-checked while the position updates", kind="INFO")

    def modify_exit(self, bracket: Bracket, leg: str, *, price: Decimal, purpose: str,
                    only_if_changed: bool = False, retry: bool = False) -> ExitResult:
        order_id = self.leg_id(bracket, leg)
        if order_id is None:
            return ExitResult("blocked")
        kind = "LMT" if leg == "target" else "STP"
        snapshot = self.fill_snapshot(bracket)  # taken after every wait, right before placeOrder
        plan = plan_exit(snapshot, leg)
        if plan.problem:
            self.alert_once(
                (bracket.decision_id, leg, purpose, "blocked", plan.problem),
                f"{bracket.symbol}: {purpose} of {leg} {order_id} not sent — {plan.problem}; act in TWS",
            )
            return ExitResult("blocked")
        capped = plan.capped_from is not None
        if capped:
            self.report_cap(bracket, leg, order_id, purpose, snapshot, plan)
        if plan.working <= 0:
            return ExitResult("nothing", 0, capped)
        if only_if_changed:
            if self.accepted(order_id, plan.total, price, kind):
                self.pending_modifies.pop(order_id, None)
                return ExitResult("unchanged", plan.working, capped)
            pending = self.pending_modifies.get(order_id)
            if pending is not None and (pending.total, pending.price) == (plan.total, price):
                if pending.exhausted:
                    return ExitResult("blocked", plan.working, capped)
                if not retry:  # confirm_modifies owns re-sends, one per tick
                    return ExitResult("pending", plan.working, capped)
        modified = self.exit_order(bracket, leg, order_id, total=plan.total, price=price)
        if modified is None:
            return ExitResult("blocked")
        with self.app.lock:
            self.error_marks[order_id] = self.app.order_errors.get(order_id, 0)
        self.app.placeOrder(order_id, stock(bracket.con_id, bracket.primary_exchange), modified)
        previous = self.pending_modifies.get(order_id)
        same = previous is not None and (
            (previous.purpose, previous.total, previous.price) == (purpose, plan.total, price)
        )
        self.pending_modifies[order_id] = PendingModify(
            decision_id=bracket.decision_id, leg=leg, purpose=purpose, kind=kind, total=plan.total, price=price,
            sends=previous.sends + 1 if same and previous is not None else 1,
        )
        return ExitResult("sent", plan.working, capped)

    def modify_settled(self, order_id: int) -> bool:
        """A modification counts as done only once TWS has echoed it."""
        pending = self.pending_modifies.get(order_id)
        if pending is None:
            return True
        if self.accepted(order_id, pending.total, pending.price, pending.kind):
            del self.pending_modifies[order_id]
            return True
        return False

    def confirm_modifies(self) -> None:
        """Re-send an exit modification TWS has not echoed, once per tick, then CRITICAL."""
        for order_id, pending in list(self.pending_modifies.items()):
            bracket = self.brackets.get(pending.decision_id)
            if (bracket is None or bracket.status != "open"
                    or self.app.order_status.get(order_id) in DEAD_ORDER_STATUSES):
                del self.pending_modifies[order_id]  # filled, cancelled or closed: nothing left to confirm
                continue
            if self.modify_settled(order_id) or pending.exhausted:
                continue
            if pending.sends > MODIFY_RETRIES:
                pending.exhausted = True
                self.alert_once(
                    (bracket.decision_id, pending.leg, pending.purpose, "unconfirmed"),
                    f"{bracket.symbol}: TWS did not confirm the {pending.purpose} of {pending.leg} {order_id}"
                    f" ({pending.total} sh @ {pending.price}) after {pending.sends} attempts — act in TWS",
                )
                continue
            log("INFO", f"{bracket.symbol}: {pending.purpose} of {pending.leg} {order_id} not confirmed by TWS —"
                f" sending again ({pending.sends + 1}/{MODIFY_RETRIES + 1})")
            result = self.modify_exit(bracket, pending.leg, price=pending.price, purpose=pending.purpose,
                                      only_if_changed=True, retry=True)
            if result.outcome in {"nothing", "blocked"}:
                self.pending_modifies.pop(order_id, None)  # no longer needed; the alerts take over

    def leg_price(self, bracket: Bracket, leg: str) -> Decimal | None:
        if leg == "stop":
            # C2 (v7c): a resize re-sends the stop at the higher of its intent and the price IBKR
            # shows, so it never lowers a stop the owner raised in TWS (a long's stop only goes up).
            return max(Decimal(bracket.stop), self.current_stop(bracket))
        if bracket.flatten_sent:  # a target the flatten repriced keeps its flatten price
            return Decimal(bracket.flatten_price) if bracket.flatten_price else None
        return Decimal(bracket.target)

    def report_dead_leg(self, bracket: Bracket, leg: str) -> bool:
        """CRITICAL once for an exit leg that is no longer live; False while its fill is in flight."""
        order_id = self.leg_id(bracket, leg)
        status = self.app.order_status.get(order_id) if order_id is not None else None
        if status == "Filled":
            return False  # its executions are on their way
        if bracket.net_held > 0:
            what = "unprotected" if leg == "stop" else "without a target"
            self.alert_once(
                (bracket.decision_id, f"dead-{leg}"),
                f"{bracket.symbol}: {leg} order {order_id} is not live ({status or 'unknown'}) —"
                f" {bracket.net_held} sh {what}; act in TWS",
            )
        return True

    def parent_dead(self, bracket: Bracket) -> bool:
        status = self.app.order_status.get(bracket.parent)
        if status in PARENT_CANCELLED_STATUSES:
            return True
        # After a restart TWS no longer reports a cancelled order at all.
        return bracket.parent_cancel_requested and status is None

    def resize_children(self, bracket: Bracket) -> None:
        """A dead, partly filled entry must not leave exits sized for the whole order."""
        if bracket.status != "open" or not self.parent_dead(bracket):
            return
        basis = self.fill_snapshot(bracket).parent  # prefers the dead parent's own filled count
        if not 0 < basis < bracket.qty or bracket.resized_qty == basis:
            return
        sent = False
        settled = True
        for leg in ("target", "stop"):
            order_id = self.leg_id(bracket, leg)
            price = self.leg_price(bracket, leg)
            if not self.leg_live(order_id):
                settled = self.report_dead_leg(bracket, leg) and settled
                continue
            if price is None or order_id is None:
                continue  # flattened by v3, which kept no price: never undo the flatten
            result = self.modify_exit(bracket, leg, price=price, purpose="resize", only_if_changed=True)
            sent = sent or result.outcome == "sent"
            settled = (settled and result.outcome not in {"blocked", "pending"} and not result.capped
                       and self.modify_settled(order_id))
        if sent:
            log("RESIZE", f"{bracket.symbol}: exits set to the {basis} filled shares")
        if settled:  # unconfirmed, capped or blocked: re-evaluated on the next tick
            bracket.resized_qty = basis

    def flatten_quote(self, bracket: Bracket, discount: Decimal) -> tuple[Decimal, str] | None:
        bid = self.bid(bracket)
        if bid is not None:
            price, source = floor_tick(bid * discount), f"bid {bid}"
        else:
            try:
                quote = self.fallback_quote(bracket)
            except Exception as exc:
                log("INFO", f"{bracket.symbol}: screener snapshot unreadable ({type(exc).__name__}: {exc})")
                quote = None
            if quote is None:
                log("CRITICAL", f"{bracket.symbol}: no quote for flatten — manual action may be needed")
                return None
            price = floor_tick(quote.price * discount * SNAPSHOT_EXTRA_DISCOUNT)
            source = f"no live bid; screener {quote.price} @ {quote.observed_at} less 1%"
        if price <= 0:
            log("CRITICAL", f"{bracket.symbol}: flatten price {price} unusable — act in TWS")
            return None
        return price, source

    def flatten_bracket(self, bracket: Bracket, discount: Decimal = FLATTEN_DISCOUNT, *,
                        owner_request: bool = False) -> bool:
        """Reprice the bracket's own target once; the OCA link then cancels the stop.

        The once-only rule lives here so every caller inherits it, and the
        attempt is persisted before anything is sent: a second automatic sell
        could race the owner's manual close in TWS, so a failed or interrupted
        flatten is never retried automatically (alerts only). The owner's own
        /close may try again after a failed attempt, never after a sent reprice.
        """
        if (bracket.status != "open" or bracket.remaining <= 0 or bracket.flatten_sent
                or (bracket.flatten_attempted and not owner_request)):
            return False
        bracket.flatten_attempted = True
        self.save()
        if not self.leg_live(bracket.target_id):
            status = self.app.order_status.get(bracket.target_id) or "unknown"
            if status == "Filled":  # its executions are still in flight; the 15:46 check confirms
                log("INFO", f"{bracket.symbol}: target {bracket.target_id} already filled; awaiting executions")
            else:
                log("CRITICAL", f"{bracket.symbol}: target order {bracket.target_id} is not live ({status})"
                    " — cannot flatten without a second sell; act in TWS")
            return False
        quote = self.flatten_quote(bracket, discount)
        if quote is None:
            return False
        price, source = quote
        result = self.modify_exit(bracket, "target", price=price, purpose="flatten")
        if result.outcome != "sent":
            return False
        bracket.flatten_sent, bracket.flatten_price = True, str(price)
        log("FLATTEN", f"{bracket.symbol}: target repriced to {price} for {result.working} sh ({source})")
        return True

    def flatten(self, discount: Decimal = FLATTEN_DISCOUNT) -> None:
        """One attempt per bracket, ever: flatten_attempted is persisted across restarts."""
        for bracket in list(self.brackets.values()):
            self.flatten_bracket(bracket, discount)

    # ---- alerts and health ----------------------------------------------------------------
    def alert_not_flat(self) -> bool:
        positions = self.positions_not_flat()
        for bracket in positions:
            log("CRITICAL", not_flat_message(bracket))
        if positions:
            self.alert_raised = True
        return bool(positions)

    def verify_flat(self) -> None:
        """15:50: alert only. A second automatic sell could race the owner's manual close."""
        if not self.alert_not_flat():
            log("FLAT", "no open engine positions")

    def check_protection(self, bracket: Bracket) -> None:
        """Every tick: an open position without a live stop is CRITICAL once."""
        if bracket.status != "open" or bracket.net_held <= 0 or self.leg_live(bracket.stop_id):
            return
        if self.app.order_status.get(bracket.target_id) == "Filled":
            return  # the target's fill (and the OCA cancel of the stop) is still being reported
        self.report_dead_leg(bracket, "stop")

    def report_dangling(self, bracket: Bracket, order_id: int) -> None:
        """A live exit on a finished bracket could sell shares the bridge no longer holds."""
        self.unmanaged[order_id] = (bracket.symbol, bracket.con_id)  # blocks the symbol, takes a slot
        self.alert_once(
            (bracket.decision_id, "dangling", str(order_id)),
            f"{bracket.symbol}: order {order_id} is still live at IBKR for a {bracket.status} bracket — check TWS",
        )

    def check_dangling_exits(self, bracket: Bracket) -> None:
        """Every tick: an exit leg still live on a closed or cancelled bracket is CRITICAL once."""
        for order_id in (bracket.target_id, bracket.stop_id):
            if order_id is None:
                continue
            if bracket.status not in {"closed", "cancelled"} or not self.leg_live(order_id):
                self.dangling_since.pop(order_id, None)
                continue
            now = self.now()
            if now - self.dangling_since.setdefault(order_id, now) >= POSITION_GAP_GRACE:  # OCA trails a fill
                self.report_dangling(bracket, order_id)

    def check_account_position(self, bracket: Bracket) -> None:
        """An account holding fewer shares than the bracket means its exits could oversell."""
        key = bracket.decision_id
        snapshot = self.fill_snapshot(bracket) if bracket.status == "open" else None
        expected = 0
        if snapshot is not None:
            expected = snapshot.parent - max(snapshot.recorded_exits, snapshot.target + snapshot.stop)
        if snapshot is None or snapshot.position is None or expected <= 0 or snapshot.position >= expected:
            self.position_gaps.pop(key, None)
            return
        if self.gap_age(bracket) >= POSITION_GAP_GRACE:
            self.alert_once(
                (key, "position-gap"),
                f"{bracket.symbol}: account holds {share_text(snapshot.position)} sh but the bracket expects"
                f" {expected} — its exit orders could sell shares the account no longer holds; check TWS",
            )

    # ---- v7c day risk budget (owner decisions D1, D5) -------------------------------------
    def shown_stop(self, bracket: Bracket) -> Decimal | None:
        """The stop leg's price as IBKR last reported it through openOrder; None before any report."""
        if bracket.stop_id is None:
            return None
        with self.app.lock:
            cached = self.app.order_objects.get(bracket.stop_id)
        if cached is None or str(getattr(cached, "orderType", "") or "") != "STP":
            return None
        price = _decimal_or_none(getattr(cached, "auxPrice", None))
        return price if price is not None and price > 0 else None

    def current_stop(self, bracket: Bracket) -> Decimal:
        """The price the budget measures a bracket to: IBKR's (an owner's TWS edit included), else the intent's.

        Without a report from IBKR the intent stop counts (O5): a stop is never assumed raised.
        """
        shown = self.shown_stop(bracket)
        return Decimal(bracket.stop) if shown is None else shown

    def entry_may_fill(self, bracket: Bracket) -> bool:
        """The bracket's entry can still buy: working, or sent and not reported dead yet."""
        if bracket.parent is None or bracket.filled >= bracket.qty:
            return False
        status = self.app.order_status.get(bracket.parent)
        if status == "Filled" or self.parent_dead(bracket):
            return False
        if bracket.status in {"sent", "open"}:
            return True
        return status in WORKING_ORDER_STATUSES  # an unreconciled bracket: only what IBKR shows working

    def stop_dead(self, bracket: Bracket) -> bool:
        """O3: no live stop leg (a filled stop or target whose executions are in flight is not dead)."""
        statuses = {self.app.order_status.get(leg) for leg in (bracket.target_id, bracket.stop_id) if leg is not None}
        return not self.leg_live(bracket.stop_id) and "Filled" not in statuses

    def unsettled_sold(self, con_id: int) -> int:
        """Shares sold this session that the account position does not show yet (the owner desk keeps them)."""
        return self.manual.unsettled_qty(con_id) if self.manual is not None else 0

    def stop_cover(self, item: Any, filled: int = 0) -> tuple[Decimal, Decimal] | None:
        """(open shares, stop price) of a working SELL STP of this account; None for anything else."""
        if (str(getattr(item, "action", "") or "") != "SELL" or str(getattr(item, "orderType", "") or "") != "STP"
                or str(getattr(item, "account", "") or "") not in {"", self.account}):
            return None
        price = _decimal_or_none(getattr(item, "auxPrice", None))
        total = _decimal_or_none(getattr(item, "totalQuantity", None))
        if price is None or price <= 0 or total is None:
            return None
        remaining = total - filled
        return (remaining, price) if remaining > 0 else None

    def book_fresh(self) -> bool:
        return self.order_book_at is not None and self.now() - self.order_book_at <= ORDER_BOOK_MAX_AGE

    def covering_stops(self, con_id: int,
                       entry: PendingEntry | None = None) -> tuple[list[tuple[Decimal, Decimal]], bool]:
        """Working SELL STP orders on the contract besides today's bracket legs, and whether the book is fresh.

        This client's own orders (owner stops, a buy's attached stop, a past session's GTC leg) come
        from what TWS last reported for them; every other client's (TWS's own, client 0) from the
        open-orders book, and only while it is fresh. A stop in any doubt covers nothing.
        """
        legs = {leg for bracket in self.brackets.values() for leg in bracket.legs}
        found: list[tuple[Decimal, Decimal]] = []
        with self.app.lock:
            own = [(order_id, cached, self.app.order_status.get(order_id), self.app.open_orders.get(order_id),
                    self.app.order_filled.get(order_id, 0))
                   for order_id, cached in self.app.order_objects.items()]
        for order_id, cached, status, row, filled in own:
            if order_id in legs or row is None or row.con_id != con_id or status not in LIVE_ORDER_STATUSES:
                continue
            cover = self.stop_cover(cached, filled)
            if cover is not None:
                found.append(cover)
        fresh = self.book_fresh()
        for item in self.order_book if fresh else ():
            if (item.client_id == self.app.client_id or item.con_id != con_id or item.action != "SELL"
                    or item.order_type != "STP" or item.status not in LIVE_ORDER_STATUSES
                    or item.account not in {"", self.account}):
                continue
            if item.aux_price is not None and item.aux_price > 0 and item.open_quantity > 0:
                found.append((item.open_quantity, item.aux_price))
        if entry is not None and entry.con_id == con_id:  # the buy's own attached stop, about to be placed
            found.append((Decimal(entry.quantity), entry.stop))
        return found, fresh

    def stops_sized_to_a_buy(self, con_id: int) -> Decimal:
        """Open shares of this client's stops on the contract that are sized to an owner buy, not to
        what is held yet (N18b-LOW-1): a stop attached to a buy that still works, and every stop the
        desk still sizes itself (its buy followed, its fills settling, its fit pending). They are never
        "oversized": the stop of a partly filled or unfilled buy covers what that buy may still fill,
        and a page telling the owner to resize or cancel it would strip the buy's protection."""
        managed = self.manual.sizing_stops() if self.manual is not None else set()
        legs = {leg for bracket in self.brackets.values() for leg in bracket.legs}
        with self.app.lock:
            own = [(order_id, cached, self.app.order_status.get(order_id), self.app.open_orders.get(order_id),
                    self.app.order_filled.get(order_id, 0),
                    self.app.order_status.get(_whole(getattr(cached, "parentId", 0) or 0)))
                   for order_id, cached in self.app.order_objects.items()]
        total = Decimal("0")
        for order_id, cached, status, row, filled, parent_status in own:
            if status not in LIVE_ORDER_STATUSES or order_id in legs or row is None or row.con_id != con_id:
                continue
            attached = _whole(getattr(cached, "parentId", 0) or 0) > 0
            if not (order_id in managed or (attached and parent_status in WORKING_ORDER_STATUSES)):
                continue
            cover = self.stop_cover(cached, filled)
            if cover is not None:
                total += cover[0]
        return total

    def account_open_risk(self, entry: PendingEntry | None = None) -> OpenRisk:
        """Open risk to current stops across the account (v7c), and every position no stop covers.

        1. Each bot bracket that holds or may still buy shares: held x (avg - stop) + unfilled x
           (limit - stop), at the stop IBKR shows. A held bracket without a live stop is unprotected (O3).
        2. Each owner buy still working: unfilled x (limit or ask - its attached stop).
        3. Every other long (owner shares, TWS-direct, carried over): held x (avgCost - the lowest
           working SELL STP on the contract), when those stops cover every share; else unprotected
           (O2). A short is always unprotected. The bot only enters symbols the account does not
           hold, so a contract's shares up to its brackets' size are the brackets'.

        ``entry`` adds an owner buy and its attached stop as if both were working already: its
        stop can lower the lowest stop of shares the owner holds on that contract.
        """
        factor = STOP_SLIPPAGE_FACTOR
        total = Decimal("0")
        lines: list[str] = []
        unprotected: list[tuple[str, str]] = []
        oversized: list[tuple[str, Decimal, Decimal, Decimal]] = []
        claimed: dict[int, int] = {}  # conId -> shares the bot's brackets hold (the position shows only these)
        for bracket in self.brackets.values():
            if bracket.skipped or bracket.parent is None or not self.holds_or_may_hold(bracket):
                continue
            snapshot = self.fill_snapshot(bracket)
            held = max(0, snapshot.parent - max(snapshot.recorded_exits, snapshot.target + snapshot.stop))
            if bracket.status == "needs_attention":
                held = self.attention_held(bracket, held, snapshot.position)
            pending = max(0, bracket.qty - snapshot.parent) if self.entry_may_fill(bracket) else 0
            stop, limit, average = self.current_stop(bracket), Decimal(bracket.entry), Decimal(bracket.avg_entry)
            basis = average if bracket.filled > 0 and average > 0 else limit
            risk = position_risk(held, basis, pending, limit, stop, factor)
            total += risk
            # LOW-3: unfilled bot shares are not in the account position, so they never hide the owner's.
            claimed[bracket.con_id] = claimed.get(bracket.con_id, 0) + held
            lines.append(f"{bracket.symbol} bot {held} held @ {basis} + {pending} unfilled @ {limit}, stop {stop}:"
                         f" {money(risk)}")
            if held > 0 and self.stop_dead(bracket):
                unprotected.append((bracket.symbol, f"{held} sh of the {bracket.label} bracket, whose stop order"
                                    f" {bracket.stop_id} is not live"))
        entries = self.manual.pending_entries() if self.manual is not None else []
        if entry is not None:
            entries.append((entry.symbol, entry.quantity, entry.price, entry.stop))
        for symbol, pending, price, stop in entries:
            if price is None or stop is None:
                unprotected.append((symbol, f"an owner buy of {pending} sh works at an unknown price or stop"))
                continue
            risk = position_risk(0, price, pending, price, stop, factor)
            total += risk
            lines.append(f"{symbol} owner buy {pending} not in the position yet @ {price}, stop {stop}: {money(risk)}")
        with self.app.lock:
            positions = sorted((con_id, shares) for (account, con_id), shares in self.app.positions.items()
                               if account == self.account and shares != 0)
            contracts = dict(self.app.position_contracts)
            costs = dict(self.app.position_avg_cost)
        for con_id, shares in positions:
            symbol = contracts.get((self.account, con_id), ("",))[0] or f"conId {con_id}"
            if shares < 0:
                unprotected.append((symbol, f"short {share_text(-shares)} sh"))
                continue
            other = shares - self.unsettled_sold(con_id) - claimed.get(con_id, 0)
            if other <= 0:
                continue
            stops, fresh = self.covering_stops(con_id, entry)
            covered = sum((quantity for quantity, _ in stops), Decimal("0"))
            sized = covered - self.stops_sized_to_a_buy(con_id)
            if sized > other and entry is None:
                oversized.append((symbol, sized, other, shares))
            if covered < other:
                unknown = "" if fresh else " (no fresh open-orders book: stops placed in TWS are unknown)"
                unprotected.append((symbol, f"{share_text(other)} sh held, working stops cover"
                                    f" {share_text(covered)}{unknown}"))
                continue
            cost = costs.get((self.account, con_id))
            if cost is None:
                unprotected.append((symbol, f"{share_text(other)} sh held at an unknown average cost"))
                continue
            low = min(price for _, price in stops)
            risk = position_risk(other, cost, 0, low, low, factor)
            total += risk
            lines.append(f"{symbol} account {share_text(other)} held @ {cost}, stop {low}: {money(risk)}")
        return OpenRisk(total=total, lines=tuple(lines), unprotected=tuple(unprotected), oversized=tuple(oversized))

    def attention_held(self, bracket: Bracket, held: int, position: Decimal | None) -> int:
        """LOW-5: a needs_attention bracket's shares come from its own legs' fills, which no longer
        follow the account. Once the account has held fewer for longer than the position grace (the
        owner closed them in TWS, say), only what the account still holds counts."""
        if position is None or position >= held:
            self.attention_gaps.pop(bracket.decision_id, None)
            return held
        now = self.now()
        if now - self.attention_gaps.setdefault(bracket.decision_id, now) < POSITION_GAP_GRACE:
            return held  # a position update may trail a fill: the bracket's own count until the grace ends
        return max(0, int(position))

    def computed_realized(self) -> tuple[Decimal, int]:
        """The bot brackets' realized P&L plus each owner-order sell execution of this session at the
        contract's cost basis when it arrived; and how many sells had no basis (they count 0)."""
        manual = Decimal("0")
        missing = 0
        with self.app.lock:
            sells = [row for row in self.app.execs.values()
                     if row.order_ref.startswith(MANUAL_REF_PREFIX) and row.side == "SLD" and row.shares > 0
                     and execution_matches(row, self.account, self.session_date, None)]
        for row in sells:
            if row.basis is None:
                missing += 1
                continue
            manual += Decimal(row.shares) * (row.price - row.basis)
        return self.realized_pnl() + manual, missing

    def account_realized_loss(self) -> Decimal:
        """Today's realized loss of the whole account, floored at zero: gains never fund risk (D1).

        IBKR's reqPnL realizedPnL (commissions included) is the source while it is at most
        PNL_FRESH old; else the computed one (v7c_pnl_source=computed, logged once per change).
        The larger loss of the two counts (fail closed: an IBKR value that is reset or zero
        mid-session never hides a loss the executions show), and while IBKR's is fresh a gap above
        the larger of $25 and 0.05 % of net liquidation is a WARN line (O15). IBKR's last value of
        this connection stays a lower bound for the loss however old it is (owner Q5, N18 LOW-2):
        TWS may stop sending a value that no longer changes, and a loss outside the computed
        source (a TWS-direct or carried position) must not vanish with it; its staleness is
        logged on its own line (v7c_pnl_stale).
        """
        computed, missing = self.computed_realized()
        with self.app.lock:
            reported, stamp = self.app.pnl_realized, self.app.pnl_at
        fresh = reported is not None and stamp is not None and time.monotonic() - stamp <= PNL_FRESH.total_seconds()
        source = "reqpnl" if fresh else "computed"
        if source != self.pnl_source:
            self.pnl_source = source
            note = f" ({missing} owner sell(s) without a cost basis count 0)" if missing and not fresh else ""
            log("INFO", f"{V7C_PNL_SOURCE}={source}{note}")
        stale = reported is not None and not fresh
        if stale and not self.pnl_stale:
            age = "no longer reported" if stamp is None else f"{time.monotonic() - stamp:.0f}s old"
            log("INFO", f"{V7C_PNL_STALE}: IBKR's last realized {money(reported)} is {age} — it stays a lower bound"
                " for today's loss")
        self.pnl_stale = stale
        if reported is None:
            return max(Decimal("0"), -computed)
        if not fresh:
            return max(Decimal("0"), -min(reported, computed))
        net_liq = self.net_liq
        threshold = max(PNL_MISMATCH_MIN_USD, (net_liq or Decimal("0")) * PNL_MISMATCH_NLV_PCT / 100)
        gap = abs(reported - computed)
        if gap > threshold and not self.pnl_mismatch:
            log("INFO", f"WARN {V7C_PNL_MISMATCH}: IBKR realized {money(reported)} vs computed {money(computed)}"
                f" (gap {money(gap)} > {money(threshold)}); the budget counts the larger loss")
        self.pnl_mismatch = gap > threshold
        return max(Decimal("0"), -min(reported, computed))

    @staticmethod
    def daily_loss_problem(loss: Decimal, net_liq: Decimal) -> str | None:
        """O14: no risk-increasing order once the account's realized loss reaches MAX_DAILY_LOSS_PCT."""
        if loss < net_liq * MAX_DAILY_LOSS_PCT / 100:
            return None
        return (f"{DAILY_LOSS_LIMIT}: daily loss limit reached (account realized loss {money(loss)} >="
                f" {MAX_DAILY_LOSS_PCT}% of net liquidation {money(net_liq)})")

    def budget_check(self, new_risk: Decimal, *, source: str,
                     entry: PendingEntry | None = None) -> tuple[str | None, Budget | None]:
        """May an order adding ``new_risk`` = quantity x (entry - stop) be sent now? (refusal, numbers).

        Call it on the bridge loop immediately before the order is stored and placed: the loop
        is the only thread that stores orders, so the check and the reservation are one step.
        For an owner buy (``entry``) the new risk is how much the account's open risk grows with
        it in place, at least its own: its stop may lower the stop of shares already held.
        Any doubt refuses (fail closed); the refusal text starts with its token.
        """
        where = f"(source={source})"
        if threading.current_thread() is not self.loop_thread:  # tripwire: never from a callback thread
            log("CRITICAL", f"{V7C_WRONG_THREAD}: the day risk budget was checked on thread"
                f" {threading.current_thread().name}, not the bridge loop — refused {where}")
            traceback.print_stack(file=sys.stderr)
            return f"{V7C_WRONG_THREAD}: checked off the bridge loop — refused {where}", None
        net_liq = self.net_liq
        if net_liq is None or net_liq <= 0:
            return f"{V7C_DAY_RISK_BUDGET}: net liquidation unknown — refused {where}", None
        if not new_risk.is_finite() or new_risk < 0:
            return f"{V7C_DAY_RISK_BUDGET}: the order's risk {new_risk} is unusable — refused {where}", None
        try:
            realized = self.account_realized_loss()
            risk = self.account_open_risk()
            added = new_risk * STOP_SLIPPAGE_FACTOR
            if entry is not None:
                added = max(added, self.account_open_risk(entry).total - risk.total)
        except Exception as exc:  # an account the bridge cannot measure takes no new risk
            log("CRITICAL", f"{V7C_DAY_RISK_BUDGET}: open risk unreadable ({type(exc).__name__}: {exc}) —"
                f" refused {where}")
            traceback.print_exc(file=sys.stderr)
            return f"{V7C_DAY_RISK_BUDGET}: open risk unreadable ({type(exc).__name__}) — refused {where}", None
        loss = self.daily_loss_problem(realized, net_liq)
        if loss:
            return f"{loss} {where}", None
        if risk.unprotected:
            symbol, why = risk.unprotected[0]
            more = f" (and {len(risk.unprotected) - 1} more)" if len(risk.unprotected) > 1 else ""
            return (f"{V7C_UNPROTECTED_POSITION}: {symbol} {why}{more} — every risk-increasing order is refused;"
                    f" {UNPROTECTED_ADVICE.format(symbol=symbol)} {where}"), None
        budget = Budget(realized_loss=realized, open_risk=risk.total, new_risk=added, net_liq=net_liq)
        if budget.total > budget.limit:
            return (f"{V7C_DAY_RISK_BUDGET}: {budget.text()} > {DAY_RISK_BUDGET_PCT}% of {money(net_liq)}"
                    f" {where}"), budget
        return None, budget

    def budget_problem(self, new_risk: Decimal, *, source: str = "bot") -> str | None:
        """The refusal for an order adding ``new_risk`` (see budget_check), else None."""
        return self.budget_check(new_risk, source=source)[0]

    def check_budget_invariant(self, budget: Budget, label: str) -> None:
        """Tripwire right after an order is stored: the account's open risk grew by its own risk at most."""
        try:
            after = self.account_open_risk().total
        except Exception as exc:
            log("CRITICAL", f"{V7C_BUDGET_INVARIANT_BROKEN}: open risk unreadable after {label}"
                f" ({type(exc).__name__}: {exc}) — check TWS")
            traceback.print_stack(file=sys.stderr)
            return
        grew = after - budget.open_risk
        if grew > budget.new_risk:
            log("CRITICAL", f"{V7C_BUDGET_INVARIANT_BROKEN}: open risk grew by {money(grew)} with {label}, more than"
                f" its own {money(budget.new_risk)} ({money(budget.open_risk)} -> {money(after)}) — check TWS")
            traceback.print_stack(file=sys.stderr)

    def request_open_orders(self, *, all_clients: bool) -> int:
        """Send one open-orders request; the answer count that completes it (see openOrderEnd)."""
        with self.app.lock:
            if all_clients:
                self.app.account_orders.clear()
            self.app.open_orders_requested += 1
            wanted = self.app.open_orders_requested
        self.open_orders_sent[wanted] = self.now()
        if all_clients:
            self.app.reqAllOpenOrders()
        else:
            self.app.reqOpenOrders()
        return wanted

    def open_orders_complete(self, wanted: int, timeout: float) -> bool:
        """True once TWS has answered every open-orders request up to the one ``wanted`` completes."""
        with self.app.open_orders_answer:
            return self.app.open_orders_answer.wait_for(lambda: self.app.open_orders_answered >= wanted, timeout)

    def refresh_order_book(self, *, timeout: float | None = None) -> list[AccountOrder] | None:
        """Every client's working orders (reqAllOpenOrders), TWS's own stops included; None when TWS
        does not finish in time (the previous book then ages out after ORDER_BOOK_MAX_AGE).

        The book is complete only once every request up to this one is answered: an earlier
        request that timed out is still owed an answer, and its openOrderEnd (which carries no
        request id) must not pass for this one's (N18 MEDIUM-2). While one is owed this returns
        None, and every caller fails closed: an owner sell is refused open_orders_unavailable, a
        /closeall sells nothing it cannot check, and the budget keeps the last full book until it
        ages out. Once TWS answers the backlog, the next request completes again.
        """
        wanted = self.request_open_orders(all_clients=True)
        if not self.open_orders_complete(wanted, self.sync_timeout if timeout is None else timeout):
            return None
        with self.app.lock:
            book = list(self.app.account_orders.values())
        self.order_book, self.order_book_at = book, self.now()
        return book

    def note_stops(self) -> None:
        """Log every stop price change IBKR reports, old -> new (spike V-1: TWS need not push an owner's
        edit, so the book refresh is what shows it); a bot leg first seen off its intent too."""
        seen: list[tuple[tuple[int, int], str, int, Decimal, Decimal | None]] = []
        legs: set[int] = set()
        for bracket in self.brackets.values():
            legs.update(bracket.legs)
            shown = self.shown_stop(bracket)
            if shown is not None and bracket.stop_id is not None:
                seen.append(((self.app.client_id, bracket.stop_id), bracket.symbol, bracket.stop_id, shown,
                             Decimal(bracket.stop)))
        with self.app.lock:
            own = [(order_id, cached, self.app.open_orders.get(order_id))
                   for order_id, cached in self.app.order_objects.items() if order_id not in legs]
        for order_id, cached, row in own:
            cover = self.stop_cover(cached)
            if cover is not None:
                seen.append(((self.app.client_id, order_id), row.symbol if row else "?", order_id, cover[1], None))
        for item in self.order_book:
            if item.client_id != self.app.client_id and item.order_type == "STP" and item.aux_price is not None:
                seen.append(((item.client_id, item.perm_id or item.order_id), item.symbol, item.order_id,
                             item.aux_price, None))
        for key, symbol, order_id, price, intent in seen:
            previous = self.stops_seen.get(key)
            if previous is None and intent is not None and price != intent:
                log("INFO", f"v7c stop {symbol} order {order_id}: intent {intent}, at IBKR {price}")
            elif previous is not None and previous != price:
                log("INFO", f"v7c stop {symbol} order {order_id}: {previous} -> {price}")
            self.stops_seen[key] = price

    def page_unprotected(self, risk: OpenRisk) -> None:
        """O2/O3: page once per unprotected position that outlasts the position-update grace."""
        now = self.now()
        current: set[tuple[str, ...]] = set()
        for symbol, why in risk.unprotected:
            key = (V7C_UNPROTECTED_POSITION, symbol, why)
            current.add(key)
            if now - self.unprotected_since.setdefault(key, now) >= POSITION_GAP_GRACE:
                self.alert_once(key, f"{V7C_UNPROTECTED_POSITION} {symbol}: {why} — every risk-increasing order (bot"
                                f" and manual) is refused; {UNPROTECTED_ADVICE.format(symbol=symbol)}")
        for key in [key for key in self.unprotected_since if key not in current]:
            del self.unprotected_since[key]
            self.alerted.discard(key)  # LOW-4: the episode is over; the next one pages again

    def page_book_unanswered(self) -> None:
        """N18b-LOW-2: an open-orders request TWS has not answered for ORDER_BOOK_UNANSWERED_ALERT pages
        CRITICAL once per episode. Until it is answered no book completes (MEDIUM-2: it is never
        written off), so the owner must know; the episode ends, and the next one pages again, once TWS
        has answered every request. Nothing is exited or reconnected here: a restart is the owner's."""
        with self.app.lock:
            requested, answered = self.app.open_orders_requested, self.app.open_orders_answered
        for number in [number for number in self.open_orders_sent if number <= answered]:
            del self.open_orders_sent[number]
        key = (V7C_ORDER_BOOK_UNAVAILABLE,)
        if answered >= requested:
            if key in self.alerted:
                self.alerted.discard(key)
                log("INFO", f"{V7C_ORDER_BOOK_UNAVAILABLE}: TWS answered every open-orders request again — the order"
                    " book is back")
            return
        sent = self.open_orders_sent.get(answered + 1)  # TWS answers in order: the oldest unanswered one
        if sent is None or self.now() - sent < ORDER_BOOK_UNANSWERED_ALERT:
            return
        self.alert_once(key, f"{V7C_ORDER_BOOK_UNAVAILABLE}: TWS has not answered an open-orders request for"
                        f" {int((self.now() - sent).total_seconds())}s — the order book is unavailable: Telegram"
                        " sells, /close and /closeall are refused, and stops placed in TWS no longer count for the"
                        " day risk budget, so a position they protect blocks every entry; check TWS and restart the"
                        " robot")

    def warn_oversized(self, risk: OpenRisk) -> None:
        """LOW-8 (owner Q6: WARN only, never a block): working stops that would sell more shares than
        they cover, once per contract and quantity; CRITICAL when they would sell more than the whole
        account position (the account would be short if they all fired). Stops sized to an owner buy
        are left out (stops_sized_to_a_buy, N18b-LOW-1)."""
        for symbol, stops, covered, position in risk.oversized:
            key = (V7C_STOP_OVERSIZED, symbol, str(stops), str(covered))
            text = (f"{V7C_STOP_OVERSIZED} {symbol}: working stops sell {share_text(stops)} sh but cover"
                    f" {share_text(covered)} held")
            self.alert_once(key, f"WARN {text} — counted as covering; entries are not blocked", kind="INFO")
            if stops > position:
                self.alert_once((*key, "short"), f"{text}: if they all fire the account is short"
                                f" {share_text(stops - position)} sh — resize or cancel the extra stop in TWS")

    def watch_budget(self) -> None:
        """Every tick: re-read the book on its cadence, note stop prices, page once per widened stop,
        unprotected position and 0.5 % step over budget. It never sends, cancels or flattens anything."""
        timeout = min(self.sync_timeout, ORDER_BOOK_TIMEOUT_S)
        if self.order_book_at is None or self.now() - self.order_book_at >= ORDER_BOOK_REFRESH:
            if self.refresh_order_book(timeout=timeout) is None:
                self.order_book_failures += 1
                if self.order_book_failures == 1:
                    log("INFO", "TWS did not report every client's open orders in time — stops placed outside the"
                        " bridge count only while the last book is fresh")
            elif self.order_book_failures:
                log("INFO", f"every client's open orders reported again after {self.order_book_failures} failed"
                    " refreshes")
                self.order_book_failures = 0
        self.note_stops()
        risk = self.account_open_risk()
        if risk.unprotected and (self.order_book_at is None or self.now() - self.order_book_at >= UNPROTECTED_RECHECK):
            if self.refresh_order_book(timeout=timeout) is not None:  # a stop placed since the last book must not page
                risk = self.account_open_risk()
        self.page_unprotected(risk)
        self.warn_oversized(risk)
        self.page_book_unanswered()
        for bracket in self.brackets.values():
            if bracket.skipped or bracket.parent is None or not self.holds_or_may_hold(bracket):
                continue
            intent, shown = Decimal(bracket.stop), self.shown_stop(bracket)
            if shown is None:
                self.alert_once((bracket.decision_id, V7C_STOP_UNCONFIRMED),
                                f"{V7C_STOP_UNCONFIRMED} {bracket.symbol}: IBKR has not reported stop order"
                                f" {bracket.stop_id} yet — its intent stop {intent} counts", kind="INFO")
            elif shown < intent:
                self.alert_once((bracket.decision_id, V7C_STOP_WIDENED, str(shown)),
                                f"{V7C_STOP_WIDENED} {bracket.symbol} intent {intent} now {shown} — counted at"
                                f" {shown} in the day risk budget; never flattened")
        net_liq = self.net_liq
        if net_liq is None or net_liq <= 0:
            return
        budget = Budget(realized_loss=self.account_realized_loss(), open_risk=risk.total, new_risk=Decimal("0"),
                        net_liq=net_liq)
        if budget.total > budget.limit:
            step = int(budget.total / net_liq * 100 / BUDGET_PAGE_STEP_PCT)
            self.alert_once((V7C_BUDGET_OVER, str(step)),
                            f"{V7C_BUDGET_OVER}: {budget.text()} > {DAY_RISK_BUDGET_PCT}% of {money(net_liq)} —"
                            " no new entries; never flattened")

    def run_budget_watch(self) -> None:
        """The watch fails alone, CRITICAL once per streak; entries stay safe (budget_check reads afresh)."""
        try:
            self.watch_budget()
        except Exception as exc:
            self.watch_failures += 1
            if self.watch_failures == 1:
                log("CRITICAL", f"v7c budget watch failed ({type(exc).__name__}: {exc}) — no budget pages until it"
                    " recovers; every entry is still checked")
                traceback.print_exc(file=sys.stderr)
            return
        if self.watch_failures:
            log("INFO", f"v7c budget watch recovered after {self.watch_failures} failed ticks")
            self.watch_failures = 0

    # ---- loop -----------------------------------------------------------------------------
    def close_out(self) -> None:
        if not self.flattened and self.past(self.times.flatten):
            log("INFO", f"{self.times.flatten:%H:%M} ET: flattening all engine positions")
            self.flatten()
            self.flattened = True
        if self.flattened and "alert" not in self.alerts_done and self.past(self.times.flatten_alert):
            self.alerts_done.add("alert")
            self.alert_not_flat()
        if self.flattened and "verify" not in self.alerts_done and self.past(self.times.verify):
            self.alerts_done.add("verify")
            self.verify_flat()

    def run_intake(self, source: str, step: Callable[[], None]) -> None:
        """A failing intake (e.g. a locked ledger) is CRITICAL once per streak and never blocks a tick."""
        try:
            step()
        except Exception as exc:
            failures = self.intake_failures.get(source, 0)
            self.intake_failures[source] = failures + 1
            if failures == 0:
                log("CRITICAL", f"{source} intake failed ({type(exc).__name__}: {exc}) — no new {source}s until"
                    " it recovers; open brackets are still managed")
                traceback.print_exc(file=sys.stderr)
            return
        failures = self.intake_failures.pop(source, 0)
        if failures:
            log("INFO", f"{source} intake recovered after {failures} failed attempts")

    def tick(self) -> None:
        # Management first; intake last, so nothing it raises can delay a cancel, resize or flatten.
        for bracket in list(self.brackets.values()):
            self.refresh(bracket)
        self.expire_entries()
        self.confirm_modifies()
        for bracket in list(self.brackets.values()):
            self.resize_children(bracket)
        self.close_out()
        for bracket in list(self.brackets.values()):
            self.check_protection(bracket)
            self.check_account_position(bracket)
            self.check_dangling_exits(bracket)
        self.run_budget_watch()  # after management, so it can never delay a cancel, resize or flatten
        for source, step in self.intakes:
            self.run_intake(source, step)

    def reconcile(self) -> None:
        """Startup: every sent/open bracket of this date must still be visible at IBKR."""
        self.app.exec_done.clear()
        self.app.positions_done.clear()
        wanted = self.request_open_orders(all_clients=False)
        execution_filter = ExecutionFilter()
        execution_filter.clientId = self.app.client_id
        self.app.reqExecutions(EXEC_REQUEST_ID, execution_filter)
        self.app.reqPositions()  # stays subscribed: every exit modification is capped by it
        answered = self.open_orders_complete(wanted, self.sync_timeout)
        if not (answered and all(event.wait(self.sync_timeout) for event in (self.app.exec_done,
                                                                              self.app.positions_done))):
            raise ReconciliationError("TWS did not finish reporting open orders, executions and positions")
        with self.app.lock:
            self.app.reconciled_arrival = self.app.exec_arrivals  # the startup positions already show these
            rows = dict(self.app.open_orders)
            executions = list(self.app.execs.values())
        live = {oid for oid, row in rows.items() if row.status not in DEAD_ORDER_STATUSES}
        executed = {
            row.order_id for row in executions
            if row.shares > 0 and execution_matches(row, self.account, self.session_date, ORDER_REF_PREFIX)
        }
        known: set[int] = set()
        for bracket in self.brackets.values():
            known.update(bracket.legs)
            if bracket.parent_cancel_requested and bracket.parent in live:
                # 9.81 drops a cancelOrder sent while disconnected without an error: send it again.
                bracket.parent_cancel_requested = False
                log("INFO", f"entry {bracket.symbol}: still working at IBKR after an earlier cancel request —"
                    " the cancel will be re-sent")
            self.refresh(bracket)
            if bracket.status in {"closed", "cancelled"}:
                for order_id in (bracket.target_id, bracket.stop_id):
                    if order_id is not None and order_id in live:
                        self.report_dangling(bracket, order_id)  # finished long ago: no grace
                continue
            if not bracket.active:
                continue
            if bracket.status == "open":
                # Shares are held: a live exit leg is the only proof they are protected.
                found = bool({bracket.target_id, bracket.stop_id} & live)
            else:
                found = bool(set(bracket.legs) & (live | executed))
            if not found:
                bracket.status = "needs_attention"
                bracket.attention_reason = "not found at IBKR at startup"
                log("CRITICAL", f"{bracket.symbol}: bracket not found at IBKR — check TWS")
        with self.app.lock:
            manual_ids = {oid for oid, ref in self.app.order_refs.items() if ref.startswith(MANUAL_REF_PREFIX)}
        for order_id in sorted(live - known - manual_ids):
            symbol = rows[order_id].symbol or "?"
            self.unmanaged[order_id] = (symbol, rows[order_id].con_id)
            with self.app.lock:
                self.app.own_order_ids.add(order_id)
            log("CRITICAL", f"order {order_id} ({symbol}) is live at IBKR but not in the {self.session_date}"
                f" bridge state — {symbol} blocked for new entries; check TWS")

    def reserve_order_ids(self) -> None:
        """Never reuse an id: TWS's sequence can be reset while today's state and the ledger hold ids."""
        used = max((leg for bracket in self.brackets.values() for leg in bracket.legs), default=0)
        if self.db.exists():
            try:
                with closing(_read_only(self.db)) as connection:
                    # A buy (v7c) holds two ids: its own and its attached stop's, the next one.
                    row = connection.execute(
                        "SELECT MAX(ibkr_order_id + CASE WHEN action = 'BUY' THEN 1 ELSE 0 END)"
                        " FROM autotrader_manual_orders"
                    ).fetchone()
                used = max(used, int(row[0] or 0) if row else 0)
            except (sqlite3.Error, TypeError, ValueError) as exc:
                log("INFO", f"manual-order ids unreadable ({type(exc).__name__}: {exc}); next id from TWS and state")
        with self.app.lock:
            # A live earlier-session order (a GTC leg, say) keeps its id even after a sequence reset.
            used = max([used, *self.app.open_orders, *(row.order_id for row in self.app.execs.values())])
            if self.app.next_id is not None and self.app.next_id <= used:
                log("INFO", f"next order id raised from {self.app.next_id} to {used + 1}: ids up to {used} are in use")
                self.app.next_id = used + 1

    def startup(self) -> None:
        log("INFO", f"state {self.store.path}: {len(self.brackets)} brackets, {len(self.active())} active")
        self.app.summary_done.clear()
        self.app.reqAccountSummary(SUMMARY_REQUEST_ID, "All", "NetLiquidation")
        self.app.summary_done.wait(self.sync_timeout)
        if self.net_liq is not None:
            # Without a value the subscription stays open; entries unblock when it arrives.
            self.app.cancelAccountSummary(SUMMARY_REQUEST_ID)
            log("INFO", f"net liquidation {self.net_liq}; daily loss stop {MAX_DAILY_LOSS_PCT}%")
        else:
            log("CRITICAL", f"net liquidation unavailable for {self.account} — new entries blocked;"
                " open brackets are still managed")
        try:  # v7c: the account's realized P&L, commissions included (O15); the account is never logged
            self.app.reqPnL(PNL_REQUEST_ID, self.account, "")
        except Exception as exc:
            log("INFO", f"{V7C_PNL_SOURCE}: reqPnL not available ({type(exc).__name__}) — realized loss is computed")
        self.app.reqMarketDataType(1)
        self.reconcile()
        self.reserve_order_ids()
        self.save()
        if self.manual is not None:
            try:
                self.manual.startup()
            except Exception as exc:
                log("CRITICAL", f"manual-order startup failed ({type(exc).__name__}: {exc}) — owner orders disabled"
                    " for this run; bot brackets are still managed")
                traceback.print_exc(file=sys.stderr)
                self.intakes = [(name, step) for name, step in self.intakes if name != "manual order"]
                self.manual = None

    def interrupted(self) -> int | None:
        if self.stop_event.is_set():
            log("INFO", "stop requested — saving state and disconnecting")
            code = EXIT_OK
        elif not self.app.isConnected():
            log("CRITICAL", "TWS connection lost — bridge stopping; open brackets keep their GTC stop and"
                f" target at IBKR. Exit {EXIT_RESTART}: the supervisor restarts the bridge.")
            code = EXIT_RESTART
        else:
            return None
        self.abandon_releases("the bridge stopped" if code == EXIT_OK else "TWS disconnected")
        if self.past(self.times.flatten):
            self.alert_not_flat()  # after the flatten nothing else will watch these positions
        self.save()
        return code

    def abandon_releases(self, why: str) -> None:
        """MEDIUM-1: an owner sell still waiting for its attached stops' cancel when the loop stops
        never leaves; each holding is paged by name (the next start never resends it)."""
        if self.manual is None:
            return
        try:
            self.manual.abandon_releases(why)
        except Exception as exc:  # the way out must never fail: the owner is told in general terms
            log("CRITICAL", f"owner stop releases not settled ({type(exc).__name__}: {exc}) — check your attached"
                " stops in TWS")
            traceback.print_exc(file=sys.stderr)

    def finish(self) -> int:
        """15:55 ET: no more management; stay for the 15:58 alert only while not flat."""
        self.abandon_releases(f"the session ended at {self.times.stop:%H:%M} ET")  # no sell after the stop time
        for bracket in list(self.brackets.values()):
            self.refresh(bracket)
        if not self.flattened:  # started too late for the 15:45 pass: the first and only reprice
            self.flatten(LATE_FLATTEN_DISCOUNT)
            self.flattened = True
        self.save()
        while self.positions_not_flat():
            code = self.interrupted()
            if code is not None:
                return code
            if self.past(self.times.final_alert):
                self.alert_not_flat()
                break
            self.stop_event.wait(self.loop_seconds)
            for bracket in list(self.brackets.values()):
                self.refresh(bracket)
            if self.manual is not None:
                self.manual.follow_all()  # isolated per row
            self.save()
        else:
            if self.alert_raised:
                log("FLAT", "no open engine positions")
        log("INFO", f"session end: realized P&L {self.realized_pnl()}")
        self.save()
        return EXIT_OK

    def run(self) -> int:
        code = self.interrupted()
        if code is not None:
            return code
        self.startup()
        while True:
            code = self.interrupted()
            if code is not None:
                return code
            if self.past(self.times.stop):
                return self.finish()
            self.tick()
            self.save()
            self.stop_event.wait(self.loop_seconds)


# ---- owner manual orders (D3) -------------------------------------------------------


MANUAL_TERMINAL = frozenset({"filled", "cancelled", "rejected", "expired", "error"})
POSITION_LAG_ALERT = timedelta(seconds=15)  # a fill the position still does not show: CRITICAL, shares kept
SILENT_REJECTION = timedelta(seconds=10)  # an error and no status for this long: ask TWS what it knows
FLATTEN_GUARD = timedelta(seconds=20)  # no new owner row may delay the flatten's tick
MANUAL_DEAD_STATUSES = frozenset({"Inactive", "Cancelled", "ApiCancelled"})  # final at IBKR, never filled further
# v7c: a /close, /sell all or /closeall cancels the owner's own attached stops on what it sells
# and sends the sell only once IBKR reports them cancelled; it gives up after this long.
RELEASE_TIMEOUT = timedelta(seconds=10)
STOP_FIT_GRACE = POSITION_GAP_GRACE  # a dead buy's stop is fitted once its fills and position have settled
ENTRY_STOP_RELEASE_UNCONFIRMED = "entry_stop_release_unconfirmed"
MANUAL_BUY_STILL_WORKING = "manual_buy_still_working"


@dataclass
class ManualTrack:
    """A manual-order row the bridge sent and follows until IBKR settles it."""

    row_id: str
    order_ref: str
    kind: str  # order | bot_flatten | close_all
    summary: str
    quantity: int | None
    status: str
    side: str = ""
    filled: int = 0
    order_ids: set[int] = field(default_factory=set)
    symbols: set[str] = field(default_factory=set)
    con_ids: dict[int, int] = field(default_factory=dict)  # order id -> contract
    child_quantity: dict[int, int] = field(default_factory=dict)  # close_all: order id -> shares sent
    decision_id: str = ""  # bot_flatten: the bracket being closed
    bot_ids: set[str] = field(default_factory=set)  # close_all: the bot brackets it started closing
    notes: list[str] = field(default_factory=list)  # close_all: what was not sold or closed, and why
    warned: set[int] = field(default_factory=set)  # order ids whose TWS warning is already in the row
    silent_since: datetime | None = None  # first tick with a TWS error and no order status
    next_check: datetime | None = None  # when TWS may be asked again about that order
    # v7c buys: the attached SELL STP child(ren), never among order_ids (its fills are not the buy's),
    # the price the unfilled part risks from (the limit, or the ask it was sized at) and that stop.
    stop_ids: set[int] = field(default_factory=set)
    entry_price: Decimal | None = None
    entry_stop: Decimal | None = None
    exchange: str = ""
    con_id: int = 0

    @property
    def short_id(self) -> str:
        return self.row_id[:6]


@dataclass
class Release:
    """A /close, /sell all or /closeall waiting for IBKR to cancel the owner's own attached stops."""

    row: Any
    stop_ids: frozenset[int]
    since: datetime
    holdings: Mapping[int, tuple[str, frozenset[int]]] = field(default_factory=dict)  # conId -> symbol, its stops
    kept: Mapping[int, str] = field(default_factory=dict)  # /closeall: conId -> why its stop was kept


@dataclass
class StopFit:
    """A dead owner buy's attached stop, fitted down to the shares it may still sell (never up)."""

    stop_id: int
    parent_id: int
    order_ref: str
    symbol: str
    con_id: int
    exchange: str
    due: datetime
    total: int | None = None  # the totalQuantity sent (0 = cancelled); None until sent
    price: Decimal | None = None
    checks: int = 0
    waiting_since: datetime | None = None  # first check that found the buy's fills not yet in the position


def market_quote_problem(ledger: ModuleType, quote: Any, now: datetime) -> str | None:
    """The manual-order rule for a market order: a fresh real-time book no wider than 1%."""
    if quote is None:
        return "no quote"
    if not quote.real_time:
        return "only a delayed quote"
    if now - quote.observed_at > ledger.MARKET_QUOTE_MAX_AGE:
        return "stale quote"
    bid, ask = quote.bid, quote.ask
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return "no valid bid/ask"
    spread = (ask - bid) / ((ask + bid) / 2)
    if spread > ledger.MAX_SPREAD_FRACTION:
        return f"spread {spread * 100:.2f}% over 1%"
    return None


class ManualDesk:
    """Owner orders from the manual-order ledger (trading/execution/manual_orders.py).

    Every tick, step by step and row by row in isolation: write the heartbeat,
    follow sent rows to their IBKR outcome, cancel what the owner asked to
    cancel, then claim at most one queued row, re-validate it against live
    facts right before placeOrder and send it. A row is claimed (sending)
    before anything leaves, so a crash can never resend it. Manual orders
    never count toward the position caps and are never flattened at the close; a
    /close of a bot symbol reprices that bracket's own target instead of
    sending a second sell. Every working sell on a contract, from any client
    and the bot's own legs included, reserves the shares it can still sell.

    v7c: a buy is risk-increasing, so it must pass the bridge's day risk budget,
    and it leaves with its attached stop. Exits never meet the budget.
    """

    def __init__(self, bridge: Bridge, ledger: ModuleType) -> None:
        self.bridge = bridge
        self.app = bridge.app
        self.mo = ledger
        self.tracked: dict[str, ManualTrack] = {}
        self.cancels_sent: set[int] = set()
        self.heartbeat_at: datetime | None = None
        self.noted: set[tuple[str, ...]] = set()
        self.sell_orders: dict[int, int] = {}  # order id -> conId of every owner sell this bridge placed
        self.unsettled_seen: dict[tuple[int, int], datetime] = {}  # (order id, arrival) -> first seen
        self.attached: dict[str, set[int]] = {}  # a buy's orderRef -> the stop ids this run attached to it
        self.releases: dict[str, Release] = {}  # row id -> a sell waiting for its own stops' cancel
        self.stop_fits: dict[int, StopFit] = {}  # stop id -> a dead buy's stop being fitted
        # LOW-1: finished buys whose fills the account position does not show yet; they stay in the
        # day risk budget's reservation until a position update stamped after the fills arrives.
        self.settling: dict[str, ManualTrack] = {}
        self.buy_baselines: dict[int, Decimal] = {}  # a buy's order id -> the account position when it was placed

    # ---- plumbing -------------------------------------------------------
    @staticmethod
    def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
        # The ledger is async; never call this while holding the ibapi lock.
        return asyncio.run(coroutine)

    def note_once(self, key: tuple[str, ...], kind: str, message: str) -> None:
        if key not in self.noted:
            self.noted.add(key)
            log(kind, message)

    def write(self, row_id: str, status: str, **fields: Any) -> bool:
        written = bool(self.run(self.mo.update_order_status(row_id, status, now=self.bridge.now(), **fields)))
        if not written:
            self.note_once(("refused", row_id, status), "INFO",
                           f"manual order #{row_id[:6]}: the ledger kept its status (not {status})")
        return written

    def working_symbols(self) -> set[str]:
        """Symbols with a manual order still working: the bot never enters them."""
        return {symbol for track in self.tracked.values() if track.kind != "bot_flatten" for symbol in track.symbols}

    def verified(self, order_id: int, order_ref: str) -> bool:
        """An order id acts for a row only while IBKR reports it with that row's orderRef."""
        with self.app.lock:
            return self.app.order_refs.get(order_id) == order_ref or any(
                item.order_id == order_id and item.order_ref == order_ref for item in self.app.execs.values()
            )

    # ---- v7c: a buy's attached stop ---------------------------------------------------------
    @staticmethod
    def is_entry_stop(cached: Any) -> bool:
        """A buy's attached stop: a SELL STP with a parent and a manual: orderRef (a /stop has no parent)."""
        return (cached is not None and str(getattr(cached, "orderRef", "") or "").startswith(MANUAL_REF_PREFIX)
                and str(getattr(cached, "action", "") or "") == "SELL"
                and str(getattr(cached, "orderType", "") or "") == "STP"
                and _whole(getattr(cached, "parentId", 0) or 0) > 0)

    def entry_stop_ids(self, order_ref: str) -> set[int]:
        """The stop(s) attached to the buy with this orderRef: what this run sent and what IBKR reports."""
        with self.app.lock:
            ids = {order_id for order_id, cached in self.app.order_objects.items()
                   if self.is_entry_stop(cached) and str(getattr(cached, "orderRef", "")) == order_ref}
        return ids | self.attached.get(order_ref, set())

    def own_entry_stops(self, con_id: int | None) -> list[tuple[int, int]]:
        """(stop id, parent id) of every working attached owner stop, on one contract or on all."""
        with self.app.lock:
            items = [(order_id, cached, self.app.order_status.get(order_id), self.app.open_orders.get(order_id))
                     for order_id, cached in self.app.order_objects.items()]
        return sorted(
            (order_id, _whole(getattr(cached, "parentId", 0) or 0))
            for order_id, cached, status, row in items
            if self.is_entry_stop(cached) and status in LIVE_ORDER_STATUSES
            and (con_id is None or (row is not None and row.con_id == con_id))
        )

    def entry_fills(self, parent_id: int) -> int:
        """Shares a buy's own order filled, by its executions or IBKR's status count."""
        with self.app.lock:
            executed = sum(item.shares for item in self.app.execs.values()
                           if item.order_id == parent_id and item.side != "SLD")
            return max(executed, self.app.order_filled.get(parent_id, 0))

    def pending_entries(self) -> list[tuple[str, int, Decimal | None, Decimal | None]]:
        """Owner buys whose shares the account position does not show yet: (symbol, shares, limit or
        ask, attached stop).

        A buy counts from the moment it is tracked, before placeOrder: its unfilled shares until
        IBKR reports its order dead or filled, and its filled shares until a position update
        stamped after their executions shows them (LOW-1, the rule unsettled_fills applies to
        sells); section 3 of account_open_risk counts them from then on. That is its reservation
        in the day risk budget. Filled shares count at their average price when it is above the
        limit or ask the buy was sized at (a market buy can fill above the ask).
        """
        entries: list[tuple[str, int, Decimal | None, Decimal | None]] = []
        for track in [*self.tracked.values(), *self.settling.values()]:
            if track.kind != "order" or track.side != "BUY" or track.quantity is None:
                continue
            with self.app.lock:
                statuses = [self.app.order_status.get(order_id) for order_id in track.order_ids]
            may_fill = track.row_id in self.tracked and (
                not statuses or any(status is None or status in WORKING_ORDER_STATUSES for status in statuses))
            per_order, average_price = self.fills(track)
            filled = min(track.quantity, sum(per_order.values()))
            unfilled = track.quantity - filled if may_fill else 0
            shares = unfilled + min(filled, self.unshown_fills(track.order_ids, track.con_id, track.order_ref))
            if shares <= 0:
                continue
            price = track.entry_price
            if price is not None and average_price is not None:
                price = max(price, average_price)
            entries.append((next(iter(sorted(track.symbols)), "?"), shares, price, track.entry_stop))
        return entries

    def unshown_fills(self, order_ids: set[int], con_id: int, order_ref: str) -> int:
        """Shares a buy's own order(s) filled that no position update has shown yet.

        An execution counts until a position update for its contract arrives after it (both are
        stamped in the reader thread); one replayed at startup is in the startup positions
        already. Shares IBKR's order status reports filled before their executions arrive are not
        shown either. Never the attached stop's fills (it shares the orderRef).
        """
        stops = self.entry_stop_ids(order_ref)
        own = {order_id for order_id in order_ids if order_id not in stops}
        with self.app.lock:
            shown = self.app.position_seq.get((self.bridge.account, con_id), 0)
            rows = [row for row in self.app.execs.values()
                    if row.order_id in own and row.side != "SLD" and row.shares > 0
                    and row.account in {"", self.bridge.account}]
            unshown = sum(row.shares for row in rows
                          if row.arrival > self.app.reconciled_arrival and row.positions_seen >= shown)
            filled = 0
            for order_id in own:
                executed = sum(row.shares for row in rows if row.order_id == order_id)
                unshown += max(0, self.app.order_filled.get(order_id, 0) - executed)
                filled += max(executed, self.app.order_filled.get(order_id, 0))
            position = self.app.positions.get((self.bridge.account, con_id))
        if unshown > 0 and self.position_holds_the_fills(own, position, filled):
            return 0
        return unshown

    def position_before_buy(self, con_id: int) -> Decimal | None:
        """The position a new buy's fills will add to: the last report plus every other owner buy fill on
        the contract that it does not show yet. An upper bound (sells not shown yet are not taken off),
        so position_holds_the_fills can only wait longer, never accept a report early."""
        reported = self.bridge.account_position(con_id)
        if reported is None:
            return None
        return reported + sum(self.unshown_fills(track.order_ids, track.con_id, track.order_ref)
                              for track in [*self.tracked.values(), *self.settling.values()]
                              if track.side == "BUY" and track.kind == "order" and track.con_id == con_id)

    def position_holds_the_fills(self, own: set[int], position: Decimal | None, filled: int) -> bool:
        """N18b-LOW-3: TWS may push a position update before the fill's execution, and no later update need
        come. The fills then count as shown once the account position, as last reported, holds at least
        the position when the buy was placed plus everything it filled: a report that already includes
        them. Never by time, and never from a position requested afresh (a TWS whose position lags, the
        MEDIUM-1b case, would answer with the lagging number). A buy placed before a restart has no
        baseline: only the stamp rule applies (fail closed: reserved, stop not fitted)."""
        if len(own) != 1 or position is None or filled <= 0:
            return False
        before = self.buy_baselines.get(next(iter(own)))
        return before is not None and position >= before + filled

    def sizing_stops(self) -> set[int]:
        """The attached stops the desk still sizes itself: of a buy it follows, whose fills are settling,
        or whose fit is pending (N18b-LOW-1: never reported oversized meanwhile)."""
        stops = set(self.stop_fits)
        for track in [*self.tracked.values(), *self.settling.values()]:
            if track.side == "BUY" and track.kind == "order":
                stops |= track.stop_ids | self.attached.get(track.order_ref, set())
        return stops

    def settle_buys(self) -> None:
        """LOW-1: a finished buy leaves the reservation once the position shows all of its fills."""
        for row_id, track in list(self.settling.items()):
            if self.unshown_fills(track.order_ids, track.con_id, track.order_ref) <= 0:
                del self.settling[row_id]

    def fit_later(self, order_ref: str, symbol: str, con_id: int | None, exchange: str) -> None:
        """Once a buy is dead, fit its working attached stop to the shares it may still sell (after a grace)."""
        if not con_id:
            return
        due = self.bridge.now() + STOP_FIT_GRACE
        for stop_id in sorted(self.entry_stop_ids(order_ref)):
            with self.app.lock:
                cached = self.app.order_objects.get(stop_id)
                live = self.app.order_status.get(stop_id) in LIVE_ORDER_STATUSES
            if live and cached is not None and stop_id not in self.stop_fits:
                self.stop_fits[stop_id] = StopFit(
                    stop_id=stop_id, parent_id=_whole(getattr(cached, "parentId", 0) or 0), order_ref=order_ref,
                    symbol=symbol, con_id=con_id, exchange=exchange, due=due,
                )

    def tend_stop_fits(self) -> None:
        """A dead buy's stop may sell at most min(its open shares, the buy's fills, the owner's shares):
        cancelled at 0, else reduced, and confirmed through openOrder. Never raised, never re-sent.

        The owner's shares are read only once a position update stamped after the buy's last fill
        has arrived (N18 MEDIUM-1b): a lagging position reads as fewer shares, even 0, and the
        stop of shares just bought would be cancelled. Until then the stop is left as it is and
        checked again next tick; a lag past POSITION_LAG_ALERT is CRITICAL once, never a cancel.
        """
        now = self.bridge.now()
        for stop_id, fit in list(self.stop_fits.items()):
            with self.app.lock:
                status = self.app.order_status.get(stop_id)
                cached = self.app.order_objects.get(stop_id)
                stop_filled = self.app.order_filled.get(stop_id, 0)
            if status in DEAD_ORDER_STATUSES or cached is None or not self.is_entry_stop(cached):
                del self.stop_fits[stop_id]
                continue
            if fit.total is not None:  # sent: done once IBKR shows it
                shown = fit.total > 0 and fit.price is not None
                if shown and self.bridge.order_shows(stop_id, fit.total, fit.price, "STP"):
                    del self.stop_fits[stop_id]
                    continue
                fit.checks += 1
                if fit.checks > MODIFY_RETRIES:
                    self.note_once(("fit-unconfirmed", str(stop_id)), "CRITICAL",
                                   f"{fit.symbol}: IBKR did not confirm fitting the attached stop {stop_id} to"
                                   f" {fit.total} sh — it may sell more than you hold; act in TWS")
                    del self.stop_fits[stop_id]
                continue
            if now < fit.due:
                continue
            if self.unshown_fills({fit.parent_id}, fit.con_id, fit.order_ref) > 0:
                fit.waiting_since = fit.waiting_since or now
                if now - fit.waiting_since >= POSITION_LAG_ALERT:
                    self.note_once(("fit-position-lag", str(stop_id)), "CRITICAL",
                                   f"{fit.symbol}: no position update {int((now - fit.waiting_since).total_seconds())}s"
                                   f" after your buy's fill (order {fit.parent_id}) — its attached stop {stop_id} is"
                                   " kept as it is until the position shows the shares; check TWS")
                continue
            remaining = _whole(getattr(cached, "totalQuantity", 0)) - stop_filled
            bought = self.entry_fills(fit.parent_id)
            held = max(0, int(self.owner_shares(fit.con_id)))
            target = max(0, min(remaining, held, bought if bought > 0 else remaining))
            if target >= remaining:
                del self.stop_fits[stop_id]
                continue
            price = _decimal_or_none(getattr(cached, "auxPrice", None))
            if target == 0 or price is None or price <= 0:
                self.app.cancelOrder(stop_id)
                fit.total = 0
                log("MANUAL", f"STOP CANCELLED {fit.symbol} order {stop_id}: its buy is over and holds nothing it"
                    f" could sell ({held} sh held)")
                continue
            total = stop_filled + target
            modified = order("SELL", "STP", total, self.bridge.account, fit.order_ref, auxPrice=float(price), tif="GTC",
                             parentId=fit.parent_id, transmit=True)
            modified.ocaGroup = str(getattr(cached, "ocaGroup", "") or "")
            modified.ocaType = _whole(getattr(cached, "ocaType", 0) or 0)
            self.app.placeOrder(stop_id, stock(fit.con_id, fit.exchange), modified)
            fit.total, fit.price = total, price
            log("MANUAL", f"STOP FITTED {fit.symbol} order {stop_id}: {remaining} -> {target} sh at {price}, the"
                f" shares its buy still holds")

    # ---- startup: rows that were in flight when the bridge stopped ----------------
    def startup(self) -> None:
        self.app.completed_done.clear()
        self.app.reqCompletedOrders(True)
        if not self.app.completed_done.wait(self.bridge.sync_timeout):
            log("INFO", "TWS did not report completed orders; manual rows are matched on open orders and"
                " executions only")
        with self.app.lock:
            known = set(self.app.order_refs.values()) | set(self.app.completed_refs)
            known |= {row.order_ref for row in self.app.execs.values() if row.order_ref}
        for row_id in self.run(self.mo.mark_unknown_sending_rows(known, now=self.bridge.now())):
            row = self.run(self.mo.get_order(row_id))
            detail = self.release_detail(row) if row is not None else None
            if detail is not None:
                self.report_interrupted_release(row, detail)
                continue
            log("CRITICAL", f"manual order #{row_id[:6]} was being sent when the bridge stopped and IBKR has no"
                " order for it — marked unknown and never resent; check TWS")
        for row in self.run(self.mo.list_active_orders()):
            if row.status == "queued":
                continue
            if self.earlier_session(row) and not self.live_at_ibkr(row):
                self.settle_earlier(row)
            else:
                self.resume(row)

    @staticmethod
    def release_detail(row: Any) -> dict[str, Any] | None:
        """The audit of a row that was cancelling the owner's attached stops (release()), else None."""
        events = row.audit.get("events") if isinstance(row.audit, dict) else None
        for event in reversed(events if isinstance(events, list) else []):
            detail = event.get("detail") if isinstance(event, dict) else None
            if isinstance(detail, dict) and isinstance(detail.get("releasing_stops"), list):
                return detail
        return None

    def report_interrupted_release(self, row: Any, detail: dict[str, Any]) -> None:
        """MEDIUM-1: the bridge stopped while this row's sell waited for the owner's attached stops'
        cancel, so nothing was sold. Named CRITICAL (symbols, stop ids, what IBKR shows now), never a
        plain unknown; the row is an error (its sell certainly never left: IBKR knows no order of it)."""
        stop_ids = sorted(_whole(item) for item in detail["releasing_stops"])
        named = detail.get("releasing") if isinstance(detail.get("releasing"), list) else []
        symbols = sorted({str(item.get("symbol") or "?") for item in named if isinstance(item, dict)}
                         or {row.symbol or "?"})
        with self.app.lock:
            live = sorted(stop_id for stop_id in stop_ids if self.app.order_status.get(stop_id) in LIVE_ORDER_STATUSES)
        gone = [stop_id for stop_id in stop_ids if stop_id not in live]
        state = (f"stop(s) {gone} no longer work at IBKR: {', '.join(symbols)} may have no stop" if gone else
                 f"IBKR still shows stop(s) {live} working")
        message = (f"the bridge stopped while cancelling your attached stop(s) {stop_ids} on {', '.join(symbols)} for"
                   f" it; nothing was sold — {state}")
        log("CRITICAL", f"manual order #{row.short_id} ({row.summary()}): {message}; marked error, never resent;"
            " act in TWS")
        self.write(row.id, "error", error=f"{message}. Never resent; act in TWS.")

    def earlier_session(self, row: Any) -> bool:
        return row.created_at.astimezone(NY).date() < self.bridge.session_date

    def live_at_ibkr(self, row: Any) -> bool:
        # A buy's GTC attached stop outlives the buy: it never keeps an earlier session's buy row open.
        stops = self.entry_stop_ids(row.order_ref) if row.action == "BUY" else set()
        with self.app.lock:
            return any(
                ref == row.order_ref and order_id not in stops
                and self.app.order_status.get(order_id) in WORKING_ORDER_STATUSES
                for order_id, ref in self.app.order_refs.items()
            )

    def settle_earlier(self, row: Any) -> None:
        """A DAY order of an earlier session that IBKR no longer works ended at that close.

        Fills come from executions or IBKR's completed-order record (an order may fill after the
        bridge stopped); without either the row says the fill is unknown instead of guessing.
        """
        track = self.resume(row)
        per_order, average_price = self.fills(track)
        with self.app.lock:
            completed = self.app.completed_refs.get(row.order_ref, "")
            completed_fill = self.app.completed_fills.get(row.order_ref, 0)
        filled = max(sum(per_order.values()), completed_fill, row.filled_qty)
        if row.quantity is not None:
            filled = min(filled, row.quantity)
        fields: dict[str, Any] = {"filled_qty": filled}
        if average_price is not None:
            fields["avg_fill_price"] = average_price
        prefix = f"settled at startup {self.bridge.session_date}: {row.status} on an earlier session"
        if row.quantity is not None and filled >= row.quantity:
            status = "filled"
        elif filled > 0:
            status, fields["error"] = "cancelled", f"{prefix}; {filled} filled, the rest ended at that close"
        elif completed:
            status, fields["error"] = "cancelled", f"{prefix}; IBKR reports it {completed}"
        else:
            status, fields["error"] = "cancelled", f"{prefix}; fill unknown — check TWS"
        if self.write(row.id, status, **fields):
            log("MANUAL", f"SETTLED {row.summary()} #{row.short_id}: {status} (earlier session, filled {filled})")
        self.tracked.pop(row.id, None)
        if row.action == "BUY":  # its GTC stop may still work for more shares than the buy left
            self.fit_later(row.order_ref, row.symbol or "?", row.con_id, row.primary_exchange or "")

    def order_ids_for(self, row: Any) -> set[int]:
        buy = row.action == "BUY"
        with self.app.lock:
            ids = {order_id for order_id, ref in self.app.order_refs.items() if ref == row.order_ref}
            ids |= {item.order_id for item in self.app.execs.values()
                    if item.order_ref == row.order_ref and not (buy and item.side == "SLD")}
        if buy:  # the attached stop shares the buy's orderRef but is never the buy's own order
            ids -= self.entry_stop_ids(row.order_ref)
        stored = int(row.ibkr_order_id) if row.ibkr_order_id else None
        if stored is not None and stored not in ids:
            # IBKR does not report this id with the row's orderRef: it may since name another order.
            self.note_once(("unverified-id", row.id), "INFO",
                           f"manual order #{row.short_id}: IBKR does not report order {stored} as manual:{row.id};"
                           " it is not acted on")
        return ids

    def resume(self, row: Any) -> ManualTrack:
        ids = set() if row.kind == "bot_flatten" else self.order_ids_for(row)
        track = ManualTrack(
            row_id=row.id, order_ref=row.order_ref, kind=row.kind, summary=row.summary(),
            quantity=row.quantity, status=row.status, side=row.side or "", filled=row.filled_qty,
            order_ids=ids, symbols={row.symbol} if row.symbol else set(),
            con_ids={order_id: row.con_id for order_id in ids if row.con_id},
        )
        if row.side == "SELL" and row.con_id:
            self.sell_orders.update({order_id: row.con_id for order_id in ids})
        if row.action == "BUY":
            track.stop_ids = self.entry_stop_ids(row.order_ref)
            track.entry_stop = row.entry_stop_price
            track.entry_price = row.limit_price if row.limit_price is not None else self.sized_at(row)
            track.exchange, track.con_id = row.primary_exchange or "", row.con_id or 0
            if row.con_id:
                self.sell_orders.update({order_id: row.con_id for order_id in track.stop_ids})
        if row.kind == "bot_flatten":
            bracket = self.bot_bracket(row.symbol)
            track.decision_id = bracket.decision_id if bracket is not None else ""
        with self.app.lock:
            self.app.own_order_ids.update(track.order_ids | track.stop_ids)
        self.tracked[row.id] = track
        return track

    @staticmethod
    def sized_at(row: Any) -> Decimal | None:
        """The ask a market buy was sized at, from its audit (its pending risk after a restart)."""
        validation = row.audit.get("validation") if isinstance(row.audit, dict) else None
        return positive_decimal(validation.get("reference_price")) if isinstance(validation, dict) else None

    # ---- the tick ------------------------------------------------------------------
    def tick(self) -> None:
        now = self.bridge.now()
        self.run(self.mo.write_bridge_heartbeat("running", now=now))  # orders are refused once it is 20 s old
        self.heartbeat_at = now
        self.follow_all()
        self.settle_buys()
        self.check_unsettled()
        for row in self.run(self.mo.list_cancel_requests()):
            try:
                self.cancel(row)
            except Exception as exc:  # one bad row never blocks the others
                self.note_once(("cancel-failed", row.id), "CRITICAL", f"manual order #{row.short_id}: cancel failed"
                               f" ({type(exc).__name__}: {exc}) — act in TWS")
        for name, step in (("stop fitting", self.tend_stop_fits), ("stop release", self.follow_releases)):
            try:
                step()
            except Exception as exc:  # it fails alone: exits and every other owner row are still claimed
                self.note_once((name, "failed"), "CRITICAL", f"manual-order {name} failed ({type(exc).__name__}:"
                               f" {exc}) — check the owner's attached stops in TWS")
                traceback.print_exc(file=sys.stderr)
        self.claim_one()

    def follow_all(self) -> None:
        for track in list(self.tracked.values()):
            try:
                self.follow(track)
            except Exception as exc:
                self.note_once(("follow-failed", track.row_id), "CRITICAL",
                               f"manual order #{track.short_id} ({track.summary}) could not be followed"
                               f" ({type(exc).__name__}: {exc}) — check TWS")

    def claim_one(self) -> None:
        if self.bridge.stop_event.is_set() or not self.app.isConnected():
            return  # a row is claimed only when it can be sent this tick
        if not self.bridge.flattened and self.bridge.now() >= self.bridge.times.flatten - FLATTEN_GUARD:
            return  # quote waits must not hold up the flatten; the row is claimed right after it
        row = self.run(self.mo.claim_next_order(self.bridge.now()))
        if row is None:
            return
        self.process_safely(row, released=None)

    def process_safely(self, row: Any, *, released: Release | None) -> None:
        try:
            if released is not None:
                self.process(row, released=released)
            else:
                self.process(row)
        except Exception as exc:
            reached = row.id in self.tracked  # placeOrder may have run
            status = "unknown" if reached else "error"
            log("CRITICAL", f"manual order #{row.short_id} ({row.summary()}) failed while being sent"
                f" ({type(exc).__name__}: {exc}) — marked {status}, never resent; check TWS")
            traceback.print_exc(file=sys.stderr)
            self.write(row.id, status, error=f"bridge error {type(exc).__name__}: {exc}")
        if released is not None:
            self.report_unsold(row, released)

    def report_unsold(self, row: Any, release: Release, why: str = "") -> None:
        """MEDIUM-1: a holding whose attached stop this row cancelled and that it did not sell has no
        stop. CRITICAL by name, whatever stopped the sell (a refusal, a stop request, a lost
        connection, an error, the owner's /cancel): the generic 15 s unprotected page is not enough."""
        track = self.tracked.get(row.id)
        sold = set(track.con_ids.values()) if track is not None else set()
        unsold = [(con_id, symbol, stop_ids) for con_id, (symbol, stop_ids) in sorted(release.holdings.items())
                  if con_id not in sold]
        reasons = ""
        if unsold and not why:
            stored = self.run(self.mo.get_order(row.id))
            reasons = str(stored.error or "") if stored is not None else ""
        for con_id, symbol, stop_ids in unsold:
            if why:
                because = why
            elif row.kind == "close_all":  # its notes name each holding: "SYM: why — not sold"
                because = "; ".join(part for part in reasons.split("; ") if part.startswith(f"{symbol}:"))
            else:
                because = reasons
            with self.app.lock:
                gone = all(self.app.order_status.get(stop_id) in DEAD_ORDER_STATUSES for stop_id in stop_ids)
            what = "was cancelled" if gone else "is being cancelled (IBKR has not confirmed yet)"
            detail = f" ({because})" if because else ""
            log("CRITICAL", f"manual order #{row.short_id} ({row.summary()}): your stop on {symbol} (order(s)"
                f" {sorted(stop_ids)}) {what} and {symbol} was not sold{detail} — it has no stop; act in TWS")

    def abandon_releases(self, why: str) -> None:
        """Every sell still waiting for its stops' cancel when the loop stops (a stop request, a lost
        connection, 15:55): it never leaves. Paged by name first, then the row is an error."""
        for row_id, release in list(self.releases.items()):
            del self.releases[row_id]
            self.report_unsold(release.row, release, why=f"{why} before IBKR confirmed the cancel")
            self.write(row_id, "error", error=f"{why} while your attached stop(s) {sorted(release.stop_ids)} were being"
                       " cancelled for it; nothing sold — act in TWS")

    def follow_releases(self) -> None:
        """A sell waiting for its own stops' cancel goes out once IBKR reports them all dead; after
        RELEASE_TIMEOUT it is refused (a /closeall goes on with what it can close)."""
        for row_id, release in list(self.releases.items()):
            with self.app.lock:
                statuses = [self.app.order_status.get(order_id) for order_id in release.stop_ids]
            if all(status in DEAD_ORDER_STATUSES for status in statuses):
                del self.releases[row_id]
                self.process_safely(release.row, released=release)
                continue
            if self.bridge.now() - release.since < RELEASE_TIMEOUT:
                continue
            del self.releases[row_id]
            row, ids = release.row, sorted(release.stop_ids)
            seconds = int(RELEASE_TIMEOUT.total_seconds())
            if row.kind == "close_all":
                log("CRITICAL", f"manual order #{row.short_id}: IBKR did not confirm the cancel of your attached"
                    f" stop(s) {ids} in {seconds}s — closing everything else; check TWS")
                self.process_safely(row, released=release)
                continue
            message = (f"IBKR did not confirm the cancel of your attached stop(s) {ids} in {seconds}s, so nothing was"
                       " sold; check TWS: the stop may be gone. Nothing sent.")
            log("CRITICAL", f"manual order #{row.short_id} ({row.summary()}): {message}")
            self.reject(row, ENTRY_STOP_RELEASE_UNCONFIRMED, message)

    # ---- facts --------------------------------------------------------------------------
    def quote(self, con_id: int | None, primary_exchange: str) -> Any:
        """A fresh bid/ask/last, real time only when no delayed tick arrived."""
        if not con_id:
            return None
        self.bridge.quote_req += 1
        request_id = self.bridge.quote_req
        self.app.reqMktData(request_id, stock(con_id, primary_exchange), "", False, False, [])
        deadline = time.monotonic() + self.bridge.quote_timeout
        while True:
            with self.app.lock:
                entry = dict(self.app.quotes.get(request_id, {}))
            if ("bid" in entry and "ask" in entry) or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        self.app.cancelMktData(request_id)
        if not entry:
            return None
        with self.app.lock:
            delayed = request_id in self.app.delayed_quotes or self.app.data_types.get(request_id, 1) != 1
        return self.mo.Quote(bid=entry.get("bid"), ask=entry.get("ask"), last=entry.get("last"),
                             observed_at=self.bridge.now(), real_time=not delayed)

    def open_orders_snapshot(self) -> list[AccountOrder] | None:
        """Every client's working orders, including stops the owner placed by hand in TWS (it also
        refreshes the bridge's book, which the day risk budget reads)."""
        return self.bridge.refresh_order_book()

    def reserves_shares(self, item: AccountOrder, con_id: int, counted: frozenset[str]) -> bool:
        # Every working sell on the contract can still take shares: the owner's own (TWS or
        # Telegram), and the bot's legs too, today's or a past session's GTC leg left at IBKR.
        return (
            item.con_id == con_id and item.action == "SELL"
            and (not item.status or item.status in WORKING_ORDER_STATUSES)
            and item.account in {"", self.bridge.account}
            and item.order_ref not in counted  # already reserved through the ledger
        )

    def sell_contracts(self) -> dict[int, int]:
        """Order id -> conId of every sell this client sent: the bots' exit legs and the owner's sells."""
        contracts = dict(self.sell_orders)
        for bracket in self.bridge.brackets.values():
            for leg in (bracket.target_id, bracket.stop_id):
                if leg is not None and bracket.con_id > 0:
                    contracts[leg] = bracket.con_id
        return contracts

    def unsettled_fills(self, con_id: int) -> list[ExecutionRow]:
        """Sell fills of this session the account position does not show yet.

        A fill counts until a position update for its contract arrives after it (both stamped in
        the reader thread). Fills replayed at startup are in the startup positions already.
        """
        sells = self.sell_contracts()
        with self.app.lock:
            shown = self.app.position_seq.get((self.bridge.account, con_id), 0)
            rows = [row for row in self.app.execs.values()
                    if row.arrival > self.app.reconciled_arrival and row.shares > 0 and row.positions_seen >= shown
                    and row.account in {"", self.bridge.account}]
        return [row for row in rows
                if (row.con_id or sells.get(row.order_id)) == con_id and (row.side == "SLD" or row.order_id in sells)]

    def unsettled_qty(self, con_id: int) -> int:
        """Shares already sold that the position still shows; kept reserved until it updates, never released by time."""
        return sum(row.shares for row in self.unsettled_fills(con_id))

    def check_unsettled(self) -> None:
        """A position update that lags a fill by over 15 s is CRITICAL once; the shares stay reserved."""
        now = self.bridge.now()
        sells = self.sell_contracts()
        with self.app.lock:
            con_ids = {row.con_id or sells.get(row.order_id, 0) for row in self.app.execs.values()
                       if row.arrival > self.app.reconciled_arrival}
            contracts = dict(self.app.position_contracts)
        for con_id in sorted(con_id for con_id in con_ids if con_id > 0):
            for row in self.unsettled_fills(con_id):
                seen = self.unsettled_seen.setdefault((row.order_id, row.arrival), now)
                if now - seen >= POSITION_LAG_ALERT:
                    symbol = contracts.get((self.bridge.account, con_id), (f"conId {con_id}",))[0]
                    self.note_once(("position-lag", str(con_id)), "CRITICAL",
                                   f"{symbol}: no position update {int((now - seen).total_seconds())}s after a fill of"
                                   f" {row.shares} sh (order {row.order_id}) — those shares stay reserved; check TWS")

    def owner_shares(self, con_id: int | None) -> Decimal:
        """Account position minus what the bot's brackets hold and what filled sells already took."""
        if not con_id:
            return Decimal("0")
        held = (self.bridge.account_position(con_id) or Decimal("0")) - self.unsettled_qty(con_id)
        bot = sum(
            (max(0, b.net_held) for b in self.bridge.brackets.values()
             if b.con_id == con_id and b.status in {"open", "needs_attention"}),
            0,
        )
        return held - bot

    def today_bot_symbols(self) -> frozenset[str]:
        return frozenset(b.symbol for b in self.bridge.brackets.values()
                         if b.active or b.status == "needs_attention")

    def foreign_bot_con_ids(self, snapshot: list[AccountOrder] | None) -> set[int]:
        """Contracts with a live bot order the bridge does not manage today (a past session's or dangling leg)."""
        con_ids = {con_id for _, con_id in self.bridge.unmanaged_live() if con_id > 0}
        con_ids |= {
            item.con_id for item in snapshot or ()
            if item.order_ref.startswith(ORDER_REF_PREFIX) and (not item.status or item.status in WORKING_ORDER_STATUSES)
        }
        return con_ids

    def bot_symbols(self, row: Any = None, snapshot: list[AccountOrder] | None = None) -> frozenset[str]:
        symbols = set(self.today_bot_symbols()) | {symbol for symbol, _ in self.bridge.unmanaged_live()}
        if row is not None and row.symbol and row.con_id and row.con_id in self.foreign_bot_con_ids(snapshot):
            symbols.add(row.symbol)  # the bot's orders still work there: owner sells must not race them
        return frozenset(symbols)

    def earlier_bot_symbol(self, row: Any, snapshot: list[AccountOrder] | None) -> bool:
        """Live bot orders the bridge does not manage today (no bracket to close) work on this symbol."""
        if not row.symbol or row.symbol in self.today_bot_symbols():
            return False
        unmanaged = self.bridge.unmanaged_live()
        return (any(symbol == row.symbol for symbol, _ in unmanaged)
                or bool(row.con_id and row.con_id in self.foreign_bot_con_ids(snapshot)))

    def bot_bracket(self, symbol: str | None) -> Bracket | None:
        if not symbol:
            return None
        wanted = self.mo.normalize_symbol(symbol)
        for bracket in self.bridge.brackets.values():
            holds = bracket.active or bracket.status == "needs_attention"
            if holds and self.mo.normalize_symbol(bracket.symbol) == wanted:
                return bracket
        return None

    def live_facts(self, row: Any, *, ledger: Any, resting: Decimal, quote: Any,
                   snapshot: list[AccountOrder] | None = None) -> Any:
        now = self.bridge.now()
        accounts = list(self.app.accounts)
        try:
            kill = bool(self.bridge.kill_switch_active())
        except Exception:
            kill = True  # unreadable fence: buys blocked, exits still allowed
        return self.mo.LiveFacts(
            now=now,
            accounts_all_paper=bool(accounts) and all(account.startswith("DU") for account in accounts),
            kill_switch_active=kill,
            bridge_heartbeat_age_s=None if self.heartbeat_at is None else (now - self.heartbeat_at).total_seconds(),
            net_liquidation=self.bridge.net_liq,
            quote=quote,
            owner_position_qty=self.owner_shares(row.con_id),
            open_manual_sell_qty=ledger.open_manual_sell_qty,
            resting_stop_qty=resting,
            bot_symbols=self.bot_symbols(row, snapshot),
            manual_open_buy_notional_today=ledger.manual_open_buy_notional_today,
            recent_manual_orders_10min=ledger.recent_manual_orders_10min,
        )

    # ---- one claimed row ------------------------------------------------------------------
    def sells_whole_holding(self, row: Any) -> bool:
        """A /close or /sell all: the owner's own attached stops on the contract are cancelled for it first."""
        if row.action == "CLOSE":
            return row.kind == "order"
        if row.action != "SELL":
            return False
        parsed = self.mo.parse_order_text(row.raw_text)  # the row stores the resolved count, not "all"
        return isinstance(parsed, self.mo.ParsedOrder) and parsed.fraction == "all"

    def process(self, row: Any, *, released: Release | None = None) -> None:
        now = self.bridge.now()
        contract = None
        if row.symbol and row.con_id and row.primary_exchange:
            contract = self.mo.ContractRef(symbol=row.symbol, con_id=row.con_id, primary_exchange=row.primary_exchange)
        ledger = self.run(self.mo.ledger_facts(symbol=row.symbol, now=now, exclude_order_id=row.id))
        resting = freed_resting = Decimal("0")
        snapshot: list[AccountOrder] | None = None
        freeing: list[tuple[int, int]] = []  # (stop id, parent id): the owner's own attached stops it cancels first
        if (row.side == "SELL" or row.action == "BUY") and row.con_id:
            snapshot = self.open_orders_snapshot()  # a buy's budget also reads it: stops placed in TWS
            if snapshot is None and row.side == "SELL":
                self.reject(row, "open_orders_unavailable",
                            "TWS did not report its open orders, so the shares a sell may use are unknown. Nothing sent.")
                return
            if row.side == "SELL" and released is None and self.sells_whole_holding(row):
                freeing = self.own_entry_stops(row.con_id)
            freed = {stop_id for stop_id, _ in freeing}
            for item in snapshot or ():
                if self.reserves_shares(item, row.con_id, ledger.counted_order_refs):
                    resting += item.open_quantity
                    if not (item.client_id == self.app.client_id and item.order_id in freed):
                        freed_resting += item.open_quantity
        # The quote is read here, after the ledger and the book: a /close or /sell all that must first
        # release its attached stops is checked against it (and can_send) right before the cancel.
        quote = self.quote(row.con_id, row.primary_exchange or "") if row.kind == "order" else None
        facts = self.live_facts(row, ledger=ledger, resting=resting, quote=quote, snapshot=snapshot)
        result = self.mo.revalidate_claimed(row, facts=facts, contract=contract)
        if row.kind == "order" and freeing:
            # A /close or /sell all releases the owner's own attached stops only when it needs their shares.
            if isinstance(result, self.mo.Rejection) and result.code == "insufficient_shares":
                facts = self.live_facts(row, ledger=ledger, resting=freed_resting, quote=quote, snapshot=snapshot)
                result = self.mo.revalidate_claimed(row, facts=facts, contract=contract)
            else:
                freeing = []
        if isinstance(result, self.mo.Rejection):
            if result.code in {"bot_holds_symbol", "state_changed"} and self.earlier_bot_symbol(row, snapshot):
                self.reject(row, "bot_holds_symbol", f"The bot still has orders working on {row.symbol} at IBKR from"
                            " an earlier session; act in TWS. Nothing sent.", facts=facts)
            else:
                self.reject(row, result.code, result.message, facts=facts)
        elif freeing:
            self.release(row, freeing, facts)
        elif result.kind == "bot_flatten":
            self.close_bot_position(row)
        elif result.kind == "close_all":
            if released is not None:
                self.close_all(row, released)
                return
            # MEDIUM-1: each holding is decided first; only the stops of holdings it will sell are released.
            freeing, kept = self.closeall_releases(row)
            if freeing:
                self.release(row, freeing, facts, kept=kept)
            else:
                self.close_all(row, kept=kept)
        elif result.action == "BUY":
            self.buy(row, result, facts)
        else:
            self.send(row, result, facts)

    def release(self, row: Any, freeing: list[tuple[int, int]], facts: Any, *,
                kept: Mapping[int, str] | None = None) -> None:
        """Cancel the owner's own attached stops that a whole-holding sell needs, then wait for IBKR.

        The sell is sent only after IBKR reports every one of them dead (follow_releases), so two
        sells never work on the same shares. A stop whose buy still works keeps protecting what
        that buy may still fill: a /close or /sell all is refused (manual_buy_still_working), a
        /closeall leaves that holding reserved. The caller checked the holding (a /closeall each
        one, closeall_releases) right before this; can_send is the last check before the cancel.
        If the sell then does not leave, each holding is paged by name (report_unsold).
        """
        with self.app.lock:
            working = sorted({parent for _, parent in freeing
                              if self.app.order_status.get(parent) in WORKING_ORDER_STATUSES})
        if working and row.kind != "close_all":
            self.reject(row, MANUAL_BUY_STILL_WORKING,
                        f"Your buy of {row.symbol} (order {working[0]}) still works and its stop protects what it"
                        f" buys; /cancel that buy first, then send this again. Nothing sent.", facts=facts)
            return
        if not self.can_send(row):
            return
        holdings = self.stop_holdings(row, [stop_id for stop_id, parent in freeing if parent not in working])
        release = Release(row=row, stop_ids=frozenset(), since=self.bridge.now(), kept=dict(kept or {}))
        cancelled: list[int] = []
        for stop_id, parent in freeing:
            if parent in working:
                continue
            self.app.cancelOrder(stop_id)
            cancelled.append(stop_id)
        if not cancelled:
            self.process_safely(row, released=release)
            return
        release.stop_ids, release.holdings = frozenset(cancelled), holdings
        self.releases[row.id] = release
        named = [{"stop": stop_id, "con_id": con_id, "symbol": symbol}
                 for con_id, (symbol, stop_ids) in sorted(holdings.items()) for stop_id in sorted(stop_ids)]
        self.write(row.id, "sending", event={"releasing_stops": cancelled, "releasing": named})
        log("MANUAL", f"RELEASE {row.summary()} #{row.short_id}: cancelling your attached stop(s) {cancelled} first;"
            " the sell follows once IBKR confirms")

    def stop_holdings(self, row: Any, stop_ids: list[int]) -> dict[int, tuple[str, frozenset[int]]]:
        """conId -> (symbol, the stops on it) for the attached stops a release cancels (what a page names)."""
        with self.app.lock:
            shown = {stop_id: self.app.open_orders.get(stop_id) for stop_id in stop_ids}
            names = {con_id: contract[0] for (account, con_id), contract in self.app.position_contracts.items()
                     if account == self.bridge.account}
        holdings: dict[int, tuple[str, set[int]]] = {}
        for stop_id in stop_ids:
            item = shown.get(stop_id)
            con_id = item.con_id if item is not None and item.con_id else (row.con_id or 0)
            symbol = (item.symbol if item is not None else "") or names.get(con_id) or row.symbol or f"conId {con_id}"
            holdings.setdefault(con_id, (symbol, set()))[1].add(stop_id)
        return {con_id: (symbol, frozenset(ids)) for con_id, (symbol, ids) in holdings.items()}

    def buy(self, row: Any, validated: Any, facts: Any) -> None:
        """A risk-increasing owner order (v7c, D5): the day risk budget, then the buy and its attached stop."""
        risk = validated.entry_risk
        entry = PendingEntry(con_id=validated.con_id, symbol=validated.symbol, quantity=validated.quantity,
                             price=validated.reference_price, stop=validated.entry_stop_price)
        problem, budget = self.bridge.budget_check(risk if risk is not None else Decimal("NaN"), source="manual",
                                                   entry=entry)
        if problem or budget is None:
            text = problem or f"{V7C_DAY_RISK_BUDGET}: unmeasured (source=manual)"
            self.reject(row, text.split(":", 1)[0], f"{text}. Nothing sent.", facts=facts)
            return
        if not self.can_send(row):
            return
        log("INFO", f"{V7C_BUDGET_OK} {row.symbol} (manual #{row.short_id}): {budget.text()} <= {DAY_RISK_BUDGET_PCT}%"
            f" of {money(budget.net_liq)} (source=manual)")
        track = ManualTrack(row_id=row.id, order_ref=row.order_ref, kind="order", summary=row.summary(),
                            quantity=validated.quantity, status="sending", side="BUY", symbols={row.symbol},
                            entry_price=validated.reference_price, entry_stop=validated.entry_stop_price,
                            exchange=validated.primary_exchange, con_id=validated.con_id)
        before = self.position_before_buy(validated.con_id)  # N18b-LOW-3: nothing of this buy has filled yet
        # Before placeOrder: a failure from here on is "unknown", never "error", and the track is the
        # buy's reservation in the day risk budget (pending_entries) before anything leaves.
        self.tracked[row.id] = track
        parent_id, stop_id = self.place_entry(row, validated)
        if before is not None:
            self.buy_baselines[parent_id] = before
        track.order_ids.add(parent_id)
        track.stop_ids.add(stop_id)
        track.con_ids[parent_id] = validated.con_id
        self.sell_orders[stop_id] = validated.con_id  # its fills are sells the position may not show yet
        event = {"facts": self.mo.facts_to_audit(facts), "entry_stop_order": stop_id, "budget": budget.text()}
        if self.write(row.id, "sent", ibkr_order_id=parent_id, event=event):
            track.status = "sent"
        log("MANUAL", f"SENT {row.summary()} #{row.short_id} (order {parent_id}) with its stop"
            f" {validated.entry_stop_price} GTC (order {stop_id})")
        self.bridge.check_budget_invariant(budget, f"manual #{row.short_id} ({row.summary()})")

    def place_entry(self, row: Any, validated: Any) -> tuple[int, int]:
        """The buy (DAY, transmit=False) and its attached SELL STP (GTC, parentId), which transmits both."""
        with self.app.lock:
            if self.app.next_id is None:
                raise RuntimeError("TWS has not provided a valid order id")
            parent_id = self.app.next_id
            self.app.next_id += 2
            self.app.own_order_ids.update({parent_id, parent_id + 1})
            self.app.order_refs[parent_id] = row.order_ref
            self.app.order_refs[parent_id + 1] = row.order_ref
        self.attached.setdefault(row.order_ref, set()).add(parent_id + 1)
        prices = {"lmtPrice": float(validated.limit_price)} if validated.order_type == "LMT" else {}
        entry = order("BUY", validated.order_type, validated.quantity, self.bridge.account, row.order_ref, tif="DAY",
                      transmit=False, **prices)
        stop = order("SELL", "STP", validated.quantity, self.bridge.account, row.order_ref,
                     auxPrice=float(validated.entry_stop_price), tif="GTC", parentId=parent_id, transmit=True)
        contract = stock(validated.con_id, validated.primary_exchange)
        self.app.placeOrder(parent_id, contract, entry)
        self.app.placeOrder(parent_id + 1, contract, stop)
        return parent_id, parent_id + 1

    def reject(self, row: Any, code: str, message: str, *, facts: Any = None) -> None:
        event = {"facts": self.mo.facts_to_audit(facts)} if facts is not None else None
        self.write(row.id, "rejected", reject_code=code, error=message, event=event)
        log("MANUAL", f"REJECTED {row.summary()} #{row.short_id}: {message} ({code})")

    def can_send(self, row: Any) -> bool:
        if self.bridge.stop_event.is_set() or not self.app.isConnected():
            self.write(row.id, "error", error="the bridge was stopping or TWS was disconnected; not sent")
            log("MANUAL", f"NOT SENT {row.summary()} #{row.short_id}: bridge stopping or TWS disconnected")
            return False
        return True

    def place(self, row: Any, *, side: str, order_type: str, quantity: int, con_id: int, primary_exchange: str,
              **prices: float) -> int:
        with self.app.lock:
            if self.app.next_id is None:
                raise RuntimeError("TWS has not provided a valid order id")
            order_id = self.app.next_id
            self.app.next_id += 1
            self.app.own_order_ids.add(order_id)
            self.app.order_refs[order_id] = row.order_ref
        ticket = order(side, order_type, quantity, self.bridge.account, row.order_ref, tif="DAY", transmit=True,
                       **prices)
        self.app.placeOrder(order_id, stock(con_id, primary_exchange), ticket)
        return order_id

    def send(self, row: Any, validated: Any, facts: Any) -> None:
        """An exit (SELL, SELL_STOP, CLOSE): never budget-checked, never sent with a stop attached."""
        if validated.side != "SELL":  # tripwire (D5): a buy leaves only through buy(), with its stop
            raise RuntimeError(f"{validated.action} is not an exit; buys go through the day risk budget")
        if not self.can_send(row):
            return
        prices: dict[str, float] = {}
        if validated.order_type == "LMT":
            prices["lmtPrice"] = float(validated.limit_price)
        elif validated.order_type == "STP":
            prices["auxPrice"] = float(validated.stop_price)
        track = ManualTrack(row_id=row.id, order_ref=row.order_ref, kind="order", summary=row.summary(),
                            quantity=validated.quantity, status="sending", side=validated.side, symbols={row.symbol})
        self.tracked[row.id] = track  # before placeOrder: a failure from here on is "unknown", never "error"
        order_id = self.place(row, side=validated.side, order_type=validated.order_type, quantity=validated.quantity,
                              con_id=validated.con_id, primary_exchange=validated.primary_exchange, **prices)
        track.order_ids.add(order_id)
        track.con_ids[order_id] = validated.con_id
        if validated.side == "SELL":
            self.sell_orders[order_id] = validated.con_id
        if self.write(row.id, "sent", ibkr_order_id=order_id, event={"facts": self.mo.facts_to_audit(facts)}):
            track.status = "sent"
        log("MANUAL", f"SENT {row.summary()} #{row.short_id} (order {order_id})")

    # ---- closing bot brackets for the owner --------------------------------------------------
    def close_bracket(self, bracket: Bracket) -> str:
        """Stop a working entry and reprice the bracket's own target once (never a second sell)."""
        if bracket.parent is not None and self.bridge.leg_live(bracket.parent) and not bracket.parent_cancel_requested:
            self.app.cancelOrder(bracket.parent)
            bracket.parent_cancel_requested = True
            log("CANCEL", f"entry {bracket.symbol}: owner asked to close (filled {bracket.filled}/{bracket.qty})")
        if bracket.status == "sent":
            return "entry cancelled"
        if bracket.status != "open":
            return "needs attention in TWS"
        if bracket.flatten_sent or bracket.remaining <= 0:
            return "already closing"
        return "closing" if self.bridge.flatten_bracket(bracket, owner_request=True) else "close failed"

    def close_bot_position(self, row: Any) -> None:
        bracket = self.bot_bracket(row.symbol)
        if bracket is None or bracket.status == "needs_attention":
            self.reject(row, "bot_needs_attention",
                        f"The bot's {row.symbol} position needs attention in TWS; nothing sent.")
            return
        if not self.can_send(row):
            return
        outcome = self.close_bracket(bracket)
        if outcome == "close failed":
            self.reject(row, "flatten_failed",
                        f"Could not reprice the bot's {row.symbol} exit (see CRITICAL); act in TWS. Nothing sent.")
            return
        self.tracked[row.id] = ManualTrack(row_id=row.id, order_ref=row.order_ref, kind="bot_flatten",
                                           summary=row.summary(), quantity=None, status="sending",
                                           symbols={row.symbol}, decision_id=bracket.decision_id)
        if self.write(row.id, "sent", event={"bot_bracket": bracket.decision_id, "outcome": outcome}):
            self.tracked[row.id].status = "sent"
        log("MANUAL", f"CLOSE {row.symbol} #{row.short_id}: bot bracket {outcome}")

    def close_all(self, row: Any, released: Release | None = None, *, kept: Mapping[int, str] | None = None) -> None:
        """Close every bot bracket and sell every unreserved owner long US stock at market.

        A holding whose attached stop closeall_releases kept (it could not sell it) is reported not
        sold with that reason and keeps its stop; the stop still reserves its shares here anyway.
        """
        kept = dict(released.kept if released is not None else kept or {})
        if not self.can_send(row):
            return
        track = ManualTrack(row_id=row.id, order_ref=row.order_ref, kind="close_all", summary=row.summary(),
                            quantity=None, status="sending", side="SELL")
        held = stopped = False
        bot_con_ids: set[int] = set()
        for bracket in list(self.bridge.brackets.values()):
            if bracket.active or bracket.status == "needs_attention":
                held, outcome = True, self.close_bracket(bracket)
                bot_con_ids.add(bracket.con_id)
                if outcome in {"closing", "already closing", "entry cancelled"}:
                    track.bot_ids.add(bracket.decision_id)
                else:
                    track.notes.append(f"{bracket.symbol}: bot {outcome}")
        snapshot = self.open_orders_snapshot()
        foreign = self.foreign_bot_con_ids(snapshot) - bot_con_ids
        with self.app.lock:
            positions = {key: value for key, value in self.app.positions.items() if key[0] == self.bridge.account}
            contracts = dict(self.app.position_contracts)
        for (_, con_id), shares in sorted(positions.items()):
            if shares != 0 and con_id in foreign:
                held = True
                symbol = contracts.get((self.bridge.account, con_id), ("?",))[0]
                track.notes.append(f"{symbol}: the bot's orders still work at IBKR — not sold; act in TWS")
        bot_con_ids |= foreign
        with self.app.lock:
            holdings = sorted(
                (con_id, shares, self.app.position_contracts.get((account, con_id), ("?", "", "", "")))
                for (account, con_id), shares in self.app.positions.items()
                if account == self.bridge.account and shares != 0 and con_id not in bot_con_ids
            )
        children: list[dict[str, object]] = []
        for con_id, shown, (symbol, security_type, currency, exchange) in holdings:
            held = True
            if con_id in kept:  # decided before any stop was cancelled: its stop is kept, nothing is sold
                track.notes.append(f"{symbol}: {kept[con_id]} — not sold; its stop is kept")
                continue
            shares, problem, note = self.closeall_sale(row, con_id, shown, (symbol, security_type, currency, exchange),
                                                       snapshot)
            if note:  # never silent: a kept-back holding cannot end "filled"
                track.notes.append(note)
            if shares <= 0 and not problem:
                continue
            if not problem and (self.bridge.stop_event.is_set() or not self.app.isConnected()):
                problem, stopped = "bridge stopping or TWS disconnected", True  # never send after a stop
            if problem:
                track.notes.append(f"{symbol}: {problem} — not sold")
                continue
            quantity = int(shares)
            self.tracked[row.id] = track  # before placeOrder: a failure from here on is "unknown"
            order_id = self.place(row, side="SELL", order_type="MKT", quantity=quantity, con_id=con_id,
                                  primary_exchange=exchange)
            track.order_ids.add(order_id)
            track.con_ids[order_id] = con_id
            self.sell_orders[order_id] = con_id
            track.child_quantity[order_id] = quantity
            track.symbols.add(symbol)
            children.append({"order_id": order_id, "symbol": symbol, "quantity": quantity})
            log("MANUAL", f"SENT SELL {quantity} {symbol} MKT #{row.short_id} (order {order_id}, close all)")
        notes = "; ".join(track.notes)
        started = bool(track.order_ids or track.bot_ids)
        event = {"children": children, "bot_closing": sorted(track.bot_ids), "notes": track.notes}
        if not held:
            status, fields = "filled", {}  # nothing was held: nothing to close
        elif not started and stopped:  # never reached IBKR: the bridge was stopping
            status, fields = "error", {"error": notes or "the bridge was stopping; nothing sent"}
        elif not started:
            status, fields = "rejected", {"reject_code": "nothing_closed", "error": notes or "nothing could be closed"}
        else:  # final once its sells AND the bot closes it started are done (follow)
            status, fields = "sent", ({"error": notes} if notes else {})
        if self.write(row.id, status, event=event, **fields):
            track.status = status
        if status == "sent":
            self.tracked[row.id] = track
        else:
            self.tracked.pop(row.id, None)
        log("MANUAL", f"CLOSEALL #{row.short_id}: {len(track.order_ids)} sell order(s), {len(track.bot_ids)} bot"
            f" close(s) — {status}" + (f"; {notes}" if notes else ""))

    def closeall_sale(self, row: Any, con_id: int, shown: Decimal, contract: tuple[str, str, str, str],
                      snapshot: list[AccountOrder] | None,
                      freed: frozenset[int] = frozenset()) -> tuple[Decimal, str | None, str | None]:
        """One holding of a /closeall: (shares to sell, why it is not sold, a note on shares kept back).

        ``freed`` are the owner's attached stops on it that would be cancelled first: they do not
        reserve shares here. A fresh real-time quote is read only for a holding that passes the rest.
        """
        symbol, security_type, currency, exchange = contract
        shares = shown - self.unsettled_qty(con_id) if shown > 0 else shown
        note = None
        if shown > 0 and shares < shown:
            kept_back = "not sold" if shares <= 0 else f"{share_text(shares)} of {share_text(shown)} sold"
            note = f"{symbol}: {share_text(shown - shares)} sh just sold do not show in the position yet — {kept_back}"
            if shares <= 0:
                return shares, None, note
        problem = self.close_all_problem(row, con_id, shares, symbol, security_type, currency, snapshot, freed)
        quote = None if problem else self.quote(con_id, exchange)
        return shares, problem or market_quote_problem(self.mo, quote, self.bridge.now()), note

    def closeall_releases(self, row: Any) -> tuple[list[tuple[int, int]], dict[int, str]]:
        """The owner's attached stops a /closeall cancels first, and why each other holding keeps its own.

        N18 MEDIUM-1: every holding with an attached stop is decided before anything is cancelled,
        with close_all's own checks (a long US stock in whole shares, not the bot's, no other
        working sell or stop on it, no stop whose buy still works, a fresh real-time quote no
        wider than 1 %). Only a holding that passes has its stops released; one that fails keeps
        its stop and is reported not sold. close_all checks again once IBKR confirms the cancel.
        """
        stops: dict[int, list[tuple[int, int]]] = {}
        with self.app.lock:
            for stop_id, parent in self.own_entry_stops(None):
                item = self.app.open_orders.get(stop_id)
                stops.setdefault(item.con_id if item is not None else 0, []).append((stop_id, parent))
            working = {parent for items in stops.values() for _, parent in items
                       if self.app.order_status.get(parent) in WORKING_ORDER_STATUSES}
            positions = {con_id: shares for (account, con_id), shares in self.app.positions.items()
                         if account == self.bridge.account}
            contracts = dict(self.app.position_contracts)
        freeing: list[tuple[int, int]] = []
        kept: dict[int, str] = {}
        if not stops:
            return freeing, kept
        snapshot = self.open_orders_snapshot()
        bot = {b.con_id for b in self.bridge.brackets.values() if b.active or b.status == "needs_attention"}
        bot |= self.foreign_bot_con_ids(snapshot)
        for con_id, items in sorted(stops.items()):
            shown = positions.get(con_id, Decimal("0"))
            if con_id <= 0 or shown <= 0 or con_id in bot:
                continue  # nothing of the owner's to sell there: close_all leaves it, and its stop stays
            if any(parent in working for _, parent in items):
                kept[con_id] = "your buy there still works and its stop protects what it buys"
                continue
            contract = contracts.get((self.bridge.account, con_id), ("?", "", "", ""))
            freed = frozenset(stop_id for stop_id, _ in items)
            shares, problem, _ = self.closeall_sale(row, con_id, shown, contract, snapshot, freed)
            if problem or shares <= 0:
                kept[con_id] = problem or "its shares just sold do not show in the position yet"
                continue
            freeing.extend(items)
        return freeing, kept

    def close_all_problem(self, row: Any, con_id: int, shares: Decimal, symbol: str, security_type: str,
                          currency: str, snapshot: list[AccountOrder] | None,
                          freed: frozenset[int] = frozenset()) -> str | None:
        if shares < 0:
            return f"short {share_text(-shares)} (sells only)"
        if security_type != "STK" or currency != "USD":
            return f"{security_type or '?'}/{currency or '?'} is not a US stock"
        if shares != shares.to_integral_value():
            return "fractional position"
        if snapshot is None:
            return "open orders unknown"
        ledger = self.run(self.mo.ledger_facts(symbol=self.mo.normalize_symbol(symbol), now=self.bridge.now(),
                                               exclude_order_id=row.id))
        resting = sum((item.open_quantity for item in snapshot
                       if self.reserves_shares(item, con_id, ledger.counted_order_refs)
                       and not (item.client_id == self.app.client_id and item.order_id in freed)), Decimal("0"))
        reserved = ledger.open_manual_sell_qty + int(resting.to_integral_value(rounding=ROUND_CEILING))
        if reserved > 0:
            return f"{reserved} of {share_text(shares)} reserved by working sells/stops — cancel them first"
        return None

    # ---- cancels ------------------------------------------------------------------------------
    def cancel(self, row: Any) -> None:
        release = self.releases.pop(row.id, None)
        if release is not None:  # its sell never left: only the owner's stops were being cancelled
            self.report_unsold(row, release, why="you cancelled the sell while its stops were being released")
            if self.write(row.id, "cancelled", error="cancelled while its attached stops were being released;"
                          " nothing sold"):
                log("MANUAL", f"CANCELLED {row.summary()} #{row.short_id}: nothing sold")
            return
        if row.kind == "bot_flatten":
            self.note_once(("cancel-bot", row.id), "MANUAL",
                           f"CANCEL #{row.short_id}: closing a bot bracket cannot be cancelled")
            return
        track = self.tracked.get(row.id) or self.resume(row)
        ids = self.order_ids_for(row) | track.order_ids
        if row.action == "BUY":  # cancelling a buy never cancels the stop protecting what it bought
            ids -= self.entry_stop_ids(row.order_ref) | track.stop_ids
        track.order_ids |= ids
        if not ids:
            self.note_once(("cancel-unknown", row.id), "CRITICAL", f"manual order #{row.short_id} ({row.summary()})"
                           " has no IBKR order id to cancel — check TWS")
            return
        for order_id in sorted(ids):
            if order_id in self.cancels_sent or self.app.order_status.get(order_id) in DEAD_ORDER_STATUSES:
                continue
            if not self.verified(order_id, row.order_ref):  # never cancel an id IBKR reports as another order
                self.note_once(("cancel-unverified", row.id, str(order_id)), "CRITICAL",
                               f"manual order #{row.short_id}: order {order_id} is not reported as {row.order_ref} —"
                               " not cancelled; check TWS")
                continue
            self.app.cancelOrder(order_id)
            self.cancels_sent.add(order_id)
            log("MANUAL", f"CANCEL {row.summary()} #{row.short_id} (order {order_id})")

    # ---- following rows to their outcome ----------------------------------------------------------
    def fills(self, track: ManualTrack) -> tuple[dict[int, int], Decimal | None]:
        """Fills per order (executions, else IBKR's status count) and their average price.

        A buy counts only its own order's fills: its attached stop shares the orderRef, and a
        sell (SLD) or a stop id is never a buy fill (v7c).
        """
        buy = track.side == "BUY"
        stops = (self.entry_stop_ids(track.order_ref) | track.stop_ids) if buy else set()
        with self.app.lock:
            rows = [item for item in self.app.execs.values()
                    if item.order_ref == track.order_ref and execution_matches(item, self.bridge.account, None, None)
                    and item.order_id not in stops and not (buy and item.side == "SLD")]
            per_order = {order_id: self.app.order_filled.get(order_id, 0) for order_id in track.order_ids
                         if order_id not in stops}
        executed: dict[int, int] = {}
        for item in rows:
            executed[item.order_id] = executed.get(item.order_id, 0) + item.shares
        for order_id, shares in executed.items():
            per_order[order_id] = max(per_order.get(order_id, 0), shares)
        quantity = sum(item.shares for item in rows)
        value = sum((Decimal(item.shares) * item.price for item in rows), Decimal("0"))
        return per_order, (value / quantity).quantize(Decimal("0.0001")) if quantity > 0 else None

    def note_warnings(self, track: ManualTrack, errors: dict[int, tuple[int, str]],
                      statuses: dict[int, str | None]) -> None:
        """A TWS code on an order that is still working is a warning in the row's notes, never final."""
        for order_id, (code, message) in errors.items():
            if order_id in track.warned or statuses.get(order_id) in MANUAL_DEAD_STATUSES:
                continue
            track.warned.add(order_id)
            if track.status in {"sent", "partially_filled", "unknown"}:
                self.write(track.row_id, track.status, event={"tws_warning": f"{code} {message}", "order": order_id})
            log("MANUAL", f"WARNING {track.summary} #{track.short_id}: TWS {code} {message} (order {order_id})")

    def silent_verdict(self, track: ManualTrack) -> str | None:
        """After an error and no order status for 10 s, ask TWS; None while IBKR may still work the order."""
        now = self.bridge.now()
        track.silent_since = track.silent_since or now
        if now - track.silent_since < SILENT_REJECTION or (track.next_check is not None and now < track.next_check):
            return None
        track.next_check = now + SILENT_REJECTION  # at most one query every 10 s; never a resend
        snapshot = self.open_orders_snapshot()
        self.app.completed_done.clear()
        self.app.reqCompletedOrders(True)
        if snapshot is None or not self.app.completed_done.wait(self.bridge.sync_timeout):
            return None  # TWS did not answer: ask again later
        if any(item.order_ref == track.order_ref and (not item.status or item.status in WORKING_ORDER_STATUSES)
               for item in snapshot):
            return None  # IBKR works it: keep following
        with self.app.lock:
            completed = self.app.completed_refs.get(track.order_ref, "")
        return completed or "Inactive"  # unknown to IBKR: it never took the order

    def bots_done(self, track: ManualTrack) -> bool:
        return all(
            (bracket := self.bridge.brackets.get(decision_id)) is None or bracket.status not in {"open", "sent"}
            for decision_id in track.bot_ids
        )

    def follow(self, track: ManualTrack) -> None:
        if track.kind == "bot_flatten":
            self.follow_bot_close(track)
            return
        per_order, average_price = self.fills(track)
        filled = sum(per_order.values())
        if track.quantity is not None:
            filled = min(filled, track.quantity)
        with self.app.lock:
            statuses = {order_id: self.app.order_status.get(order_id) for order_id in track.order_ids}
            errors = {order_id: self.app.error_details[order_id] for order_id in sorted(track.order_ids)
                      if order_id in self.app.error_details}
            completed = self.app.completed_refs.get(track.order_ref, "")
        if completed:  # sent before a restart and finished meanwhile: TWS reports it only as completed
            statuses = {order_id: status or completed for order_id, status in statuses.items()} or {0: completed}
        if not statuses and track.kind != "close_all":
            return  # nothing known at IBKR yet: an unknown row waits for the owner
        self.note_warnings(track, errors, statuses)
        working = any(status in WORKING_ORDER_STATUSES for status in statuses.values())
        unheard = any(status is None for status in statuses.values())
        if errors and unheard and not working:
            verdict = self.silent_verdict(track)
            if verdict is not None:  # IBKR does not work it: its final word, or unknown -> rejected
                statuses = {order_id: status or verdict for order_id, status in statuses.items()}
                unheard = False
        fields: dict[str, Any] = {"filled_qty": filled}
        if average_price is not None:
            fields["avg_fill_price"] = average_price
        if track.status in {"sending", "unknown"} and track.kind == "order" and len(track.order_ids) == 1:
            fields["ibkr_order_id"] = next(iter(track.order_ids))  # found at IBKR after a restart
        if track.quantity is not None and filled >= track.quantity:
            new = "filled"  # done, even when TWS no longer reports the order
        elif working or unheard or not self.bots_done(track):
            fresh = track.status in {"sending", "unknown"}
            new = "partially_filled" if filled > 0 else ("sent" if fresh else track.status)
        elif track.kind == "close_all":
            new, notes = self.close_all_outcome(track, per_order)
            if notes:
                fields["error"] = notes
        elif all(status == "Filled" for status in statuses.values()):
            new = "filled"
        elif filled > 0:
            new = "cancelled"  # partly filled, then cancelled or expired at IBKR
        elif errors:
            code, message = errors[max(errors)]
            new, fields["reject_code"], fields["error"] = "rejected", f"tws_{code}", message
        elif any(status in {"Cancelled", "ApiCancelled"} for status in statuses.values()):
            new = "cancelled"
        else:
            new, fields["reject_code"], fields["error"] = "rejected", "tws_inactive", "TWS made the order inactive"
        if (new, filled) != (track.status, track.filled) and self.write(track.row_id, new, **fields):
            self.log_change(track, new, filled, average_price, fields)
            track.status, track.filled = new, filled
        if new in MANUAL_TERMINAL:
            self.tracked.pop(track.row_id, None)
            if track.side == "BUY" and track.kind == "order":  # a dead buy's GTC stop must not outsize what it holds
                if self.unshown_fills(track.order_ids, track.con_id, track.order_ref) > 0:
                    self.settling[track.row_id] = track  # reserved until the position shows its fills (LOW-1)
                self.fit_later(track.order_ref, next(iter(sorted(track.symbols)), "?"), track.con_id, track.exchange)

    def close_all_outcome(self, track: ManualTrack, per_order: dict[int, int]) -> tuple[str, str]:
        """filled only when every holding was sold and closed in full; otherwise cancelled with the reasons."""
        shortfalls: list[str] = list(track.notes)  # a holding refused or kept back: never "filled"
        for order_id, quantity in sorted(track.child_quantity.items()):
            if per_order.get(order_id, 0) < quantity:
                shortfalls.append(f"order {order_id}: {per_order.get(order_id, 0)} of {quantity} sold")
        for decision_id in sorted(track.bot_ids):
            bracket = self.bridge.brackets.get(decision_id)
            if bracket is not None and bracket.status not in {"closed", "cancelled"}:
                shortfalls.append(f"{bracket.symbol}: bot bracket {bracket.status} — act in TWS")
        return ("cancelled" if shortfalls else "filled"), "; ".join(shortfalls)

    def log_change(self, track: ManualTrack, new: str, filled: int, average_price: Decimal | None,
                   fields: dict[str, Any]) -> None:
        price = f" @ {average_price}" if average_price is not None else ""
        if new == "partially_filled":
            log("MANUAL", f"FILL {track.summary} {filled}/{track.quantity or '?'}{price} #{track.short_id}")
        elif new == "filled":
            log("MANUAL", f"FILLED {track.summary}{price} #{track.short_id}")
        elif new == "cancelled":
            note = f": {fields['error']}" if fields.get("error") else ""
            log("MANUAL", f"CANCELLED {track.summary} #{track.short_id} (filled {filled}){note}")
        elif new == "rejected":
            log("MANUAL", f"REJECTED {track.summary} #{track.short_id}: {fields.get('error')}"
                f" ({fields.get('reject_code')})")
        elif new == "sent":
            log("MANUAL", f"SENT {track.summary} #{track.short_id} (orders {sorted(track.order_ids)})")

    def follow_bot_close(self, track: ManualTrack) -> None:
        bracket = self.bridge.brackets.get(track.decision_id)
        if bracket is None:
            return
        if bracket.status == "closed":
            fields: dict[str, Any] = {"filled_qty": bracket.exit_filled}
            if Decimal(bracket.avg_exit) > 0:
                fields["avg_fill_price"] = Decimal(bracket.avg_exit)
            if self.write(track.row_id, "filled", **fields):
                log("MANUAL", f"FILLED {track.summary} #{track.short_id}: bot bracket closed,"
                    f" P&L {bracket.realized_pnl}")
            self.tracked.pop(track.row_id, None)
        elif bracket.status == "cancelled":
            if self.write(track.row_id, "cancelled", error="the bot's entry was cancelled before any fill"):
                log("MANUAL", f"CANCELLED {track.summary} #{track.short_id}: the bot's entry never filled")
            self.tracked.pop(track.row_id, None)
        elif bracket.status == "open" and bracket.remaining > 0 and not bracket.flatten_sent:
            # The entry was still working at the request and filled since: one attempt to close what it holds.
            if self.close_bracket(bracket) == "close failed":
                message = f"Could not reprice the bot's {bracket.symbol} exit (see CRITICAL); act in TWS."
                if self.write(track.row_id, "rejected", reject_code="flatten_failed", error=message):
                    log("MANUAL", f"REJECTED {track.summary} #{track.short_id}: {message} (flatten_failed)")
                self.tracked.pop(track.row_id, None)


def manual_desk(bridge: Bridge) -> ManualDesk | None:
    """The owner-order intake, or None (CRITICAL) when its ledger cannot be used safely."""
    try:
        backend_modules()
        from config import cfg
        from trading.execution import manual_orders
    except Exception as exc:  # owner orders fail alone: bot brackets are still managed
        log("CRITICAL", f"manual-order ledger unavailable ({type(exc).__name__}: {exc}) — owner orders"
            " disabled; bot brackets are still managed")
        return None
    ledger_db = Path(cfg.DB_PATH).expanduser().resolve()
    if ledger_db != Path(bridge.db).expanduser().resolve():
        log("CRITICAL", f"manual-order ledger {ledger_db} is not the bridge ledger {bridge.db} — owner orders"
            " disabled")
        return None
    return ManualDesk(bridge, manual_orders)



# ---- entry point ------------------------------------------------------------------


def _request_stop(signum: int, frame: FrameType | None) -> None:
    STOP.set()  # only a flag: the loop finishes its step, saves state and exits 0


def _hard_exit(code: int) -> None:
    os._exit(code)  # the main thread is stuck inside the ibapi handshake; nothing else can stop it


def start_connect_watchdog(app: Any) -> threading.Timer:
    """ibapi 9.81 connect() loops forever when TWS accepts TCP but never answers the handshake."""
    def expire() -> None:
        if not app.ready.is_set():
            log("CRITICAL", f"TWS accepted the connection but sent no order id within {CONNECT_WATCHDOG_S:.0f}s"
                f" — exit {EXIT_RESTART}")
            _hard_exit(EXIT_RESTART)

    timer = threading.Timer(CONNECT_WATCHDOG_S, expire)
    timer.daemon = True
    timer.start()
    return timer


def bridge_sha256() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def _session_date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Temporary supervised PAPER bridge (outside the trading package)."
    )
    parser.add_argument(
        "--date", type=_session_date_arg, default=None,
        help="New York session date YYYY-MM-DD (default: today in New York)",
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="print the sendable identities, caps rows and fenced identities without connecting; exit 78 on a problem",
    )
    return parser.parse_args(argv)


def preflight() -> int:
    """The dry run's view of this file: config, sendable set, caps rows, fences. No TWS, no state."""
    try:
        load_config(CONFIG)
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        log("CRITICAL", f"refusing: config {CONFIG} unusable ({type(exc).__name__}: {exc})")
        return EXIT_REFUSED
    for line in caps_lines():
        log("INFO", line)
    try:
        log_fenced_identities(DB)
    except Exception as exc:
        log("CRITICAL", f"PREFLIGHT_REFUSED fences unreadable ({type(exc).__name__}: {exc})")
        return EXIT_REFUSED
    log("INFO", budget_line())  # deploy apply requires exactly one such line in the dry run
    problem = caps_problem()
    if problem:
        log("CRITICAL", problem)
        return EXIT_REFUSED
    return EXIT_OK


def load_config(path: Path) -> tuple[str, int]:
    shadow = tomllib.loads(path.read_text(encoding="utf-8"))["shadow"]
    account = str(shadow["paper_account_id"]).strip()
    client_id = int(shadow["order_client_id"])
    if not account.startswith("DU"):
        raise ValueError("paper_account_id is not a DU paper account")
    return account, client_id


def report_state_after_close(store: StateStore) -> None:
    try:
        brackets = store.load()
    except StateError as exc:
        log("CRITICAL", f"{exc}")
        return
    for bracket in brackets.values():
        if bracket.status not in {"open", "needs_attention"} or bracket.net_held == 0:
            continue
        if bracket.net_held < 0:
            log("CRITICAL", not_flat_message(bracket))
        else:
            log("CRITICAL", f"{bracket.symbol}: still open per bridge state ({bracket.net_held} sh) — act in TWS")


def _connect(app: Any, client_id: int) -> int | None:
    """Connect and wait for nextValidId; an exit code when TWS does not answer."""
    watchdog = start_connect_watchdog(app)
    try:
        app.connect(TWS_HOST, TWS_PORT, client_id)
        threading.Thread(target=app.run, name="ibapi-reader", daemon=True).start()
        answered = app.ready.wait(SYNC_TIMEOUT_S)
    except Exception as exc:
        log("CRITICAL", f"TWS connect failed ({type(exc).__name__}: {exc}) — exit {EXIT_RESTART}")
        app.disconnect()
        return EXIT_RESTART
    finally:
        watchdog.cancel()
    if not answered:
        log("CRITICAL", f"TWS did not answer on {TWS_HOST}:{TWS_PORT} (client {client_id}) — exit {EXIT_RESTART}")
        app.disconnect()
        return EXIT_RESTART
    return None


def _account_refusal(app: Any, account: str) -> int | None:
    deadline = time.monotonic() + ACCOUNTS_TIMEOUT_S
    while not app.accounts and time.monotonic() < deadline:
        time.sleep(0.05)
    if not app.accounts:  # TWS was slow, not unsafe: the supervisor may retry
        log("CRITICAL", f"TWS reported no managed accounts within {ACCOUNTS_TIMEOUT_S:.0f}s — exit {EXIT_RESTART}")
        return EXIT_RESTART
    if not all(a.startswith("DU") for a in app.accounts):
        log("CRITICAL", f"refusing: TWS reports a non-paper account {app.accounts}")
        return EXIT_REFUSED
    if account not in app.accounts:
        log("CRITICAL", "refusing: configured paper account not managed by this TWS")
        return EXIT_REFUSED
    return None


def _connected_session(
    account: str, client_id: int, session_date: date, store: StateStore, brackets: dict[str, Bracket],
    times: SessionTimes | None = None,
) -> int:
    app = App(client_id)
    failure = _connect(app, client_id)
    if failure is not None:
        return failure
    refusal = _account_refusal(app, account)
    if refusal is not None:
        app.disconnect()
        return refusal
    times = times if times is not None else session_times(session_date)
    log("INFO", f"PAPER bridge {BRIDGE_VERSION} connected (all accounts paper). Managing brackets for"
        f" {session_date} until {times.stop:%H:%M} ET.")
    for line in caps_lines():
        log("INFO", line)
    problem = caps_problem()
    if problem:
        log("CRITICAL", problem)  # the gate skips the uncapped name; the session still manages exits
    try:
        log_fenced_identities(DB)
    except Exception as exc:
        log("CRITICAL", f"identity fences unreadable ({type(exc).__name__}: {exc}) — every entry will be skipped")
    log("INFO", budget_line())
    bridge = Bridge(app, account, session_date=session_date, store=store, brackets=brackets, db=DB,
                    clock=now_ny, stop_event=STOP, times=times)
    desk = manual_desk(bridge)
    if desk is not None:
        bridge.attach_manual_desk(desk)
    code = EXIT_RESTART
    try:
        code = bridge.run()
    except Exception as exc:
        log("CRITICAL", f"bridge error {type(exc).__name__}: {exc} — exit {EXIT_RESTART} for a supervised restart")
        traceback.print_exc(file=sys.stderr)
        code = EXIT_RESTART
    finally:
        try:
            bridge.save()
        except Exception as exc:
            log("CRITICAL", f"bridge state not saved ({type(exc).__name__}: {exc})")
        app.disconnect()
        log("INFO", f"bridge stopped (exit {code})")
    return code


def main(argv: list[str] | None = None) -> int:
    STOP.clear()
    args = parse_args(argv)
    log("INFO", f"bridge sha256={bridge_sha256()}")
    if args.preflight:
        return preflight()
    now = now_ny().astimezone(NY)
    today = now.date()
    session_date: date = args.date or today
    if session_date > today:
        log("CRITICAL", f"refusing: --date {session_date} is after today ({today}) in New York")
        return EXIT_REFUSED
    store = StateStore(state_dir=STATE_DIR, legacy_path=LEGACY_STATE, session_date=session_date)
    times = session_times(session_date)
    if now >= times.stop:
        if session_date == today:
            log("INFO", f"started after {times.stop:%H:%M} ET for {session_date} — nothing to manage")
            report_state_after_close(store)
        else:
            log("INFO", f"session {session_date} is over — nothing to manage")
        return EXIT_OK
    try:
        account, client_id = load_config(CONFIG)
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        log("CRITICAL", f"refusing: config {CONFIG} unusable ({type(exc).__name__}: {exc})")
        return EXIT_REFUSED
    try:
        brackets = store.load()
    except StateError as exc:
        log("CRITICAL", f"refusing: {exc} — fix or move the file")
        return EXIT_REFUSED
    previous = {sig: signal.signal(sig, _request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return _connected_session(account, client_id, session_date, store, brackets, times)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    # The notifier reads this stream; an encoding error must never crash a callback.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
