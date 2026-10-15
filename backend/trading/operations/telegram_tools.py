"""Read-only data for the owner's Telegram assistant.

Every tool here only reads: the database, the private logs and state files,
and IBKR account and market data over the dedicated TWS data client 21.
Nothing in this module can place, modify, or cancel an order, and the tool
set offered to Claude is exactly ``TOOL_DEFINITIONS`` below: data tools only.
Orders come from the deterministic parser and validator in
``trading.execution.manual_orders``, driven by ``telegram_bot``.

Each tool returns plain text that states when its data was observed, so the
same words answer a slash command and feed Claude's answer.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import importlib
import json
import logging
import os
from pathlib import Path
import re
import time as monotonic_time
from typing import Any, Protocol

from trading.operations.paths import UserPaths
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session, next_market_session

logger = logging.getLogger("autotrader.shadow.telegram")

# TWS allows one connection per client ID: 17 (ORB data), 18 (bridge orders),
# 19 (screener), 20 (MOMO data); the assistant reads on its own ID.
TELEGRAM_TWS_CLIENT_ID = 21
RESERVED_TWS_CLIENT_IDS = frozenset({17, 18, 19, 20})
TWS_CONNECT_TIMEOUT_SECONDS = 15.0
TWS_IDLE_SECONDS = 60.0
QUOTE_TIMEOUT_SECONDS = 4.0
ACCOUNT_TIMEOUT_SECONDS = 25.0
SUPERVISOR_LOG_TAIL_BYTES = 256 * 1024
MAX_TOOL_TEXT = 6000
HOT_ROWS_SHOWN = 15
SIGNALS_SHOWN = 6
# The bridge's bracket statuses that still hold, or may still take, a position.
BOT_ACTIVE_STATUSES = frozenset({"pending", "sent", "open", "needs_attention"})
MANUAL_ORDERS_MODULE = "trading.execution.manual_orders"
CENT = Decimal("0.01")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[ .][A-Z]{1,2})?$")

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolError(RuntimeError):
    """A data source is unavailable; the message is safe to show the owner."""


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def et_clock(moment: datetime | None, pattern: str = "%H:%M:%S ET") -> str:
    if moment is None:
        return "time unknown"
    return moment.astimezone(MARKET_TIMEZONE).strftime(pattern)


def age_text(seconds: float) -> str:
    """A short human age: 45 s, 12 min, 3 h 5 min, 2 d."""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{hours // 24} d"


def observed_text(observed_at: datetime | None, now: datetime) -> str:
    if observed_at is None:
        return "time unknown"
    return f"{et_clock(observed_at, '%a %H:%M:%S ET')} ({age_text((now - observed_at).total_seconds())} ago)"


def decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def money(value: Decimal | None, *, signed: bool = False) -> str:
    if value is None:
        return "?"
    cents = value.quantize(CENT)
    if not signed:
        return f"-${abs(cents):,.2f}" if cents < 0 else f"${cents:,.2f}"
    sign = "-" if cents < 0 else "+" if cents > 0 else ""
    return f"{sign}${abs(cents):,.2f}"


def pct(value: Decimal | None) -> str:
    if value is None:
        return "?"
    rounded = value.quantize(CENT)
    if rounded == 0:
        return "0.00%"
    return f"{'+' if rounded > 0 else ''}{rounded}%"


def price(value: Decimal | None) -> str:
    if value is None:
        return "?"
    quantized = value.quantize(Decimal("0.0001")).normalize()
    text = format(quantized, "f")
    whole, _, fraction = text.partition(".")
    return f"{whole}.{fraction.ljust(2, '0')}"


def quantity(value: Decimal | int | None) -> str:
    if value is None:
        return "?"
    number = Decimal(value)
    if number == number.to_integral_value():
        return f"{int(number):,}"
    return format(number.normalize(), "f")


def clip(text: str, limit: int = MAX_TOOL_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n… (truncated)"


def normalize_symbol(raw: str) -> str:
    """``$aapl`` -> ``AAPL``; ``BRK.B``/``BF-B`` -> IBKR's ``BRK B``/``BF B``."""
    text = str(raw).strip().upper().lstrip("$")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^([A-Z]{1,5})[.\-/]([A-Z]{1,2})$", r"\1 \2", text)
    if not _SYMBOL.fullmatch(text):
        raise ToolError(f"{raw!r} does not look like a US stock symbol")
    return text


# --------------------------------------------------------------------------
# Market clock
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketClock:
    is_open: bool
    closes_at: datetime | None
    next_open: datetime | None
    half_day: bool = False

    def text(self) -> str:
        if self.is_open:
            close = et_clock(self.closes_at, "%H:%M ET")
            return f"OPEN · closes {close}" + (" (half day)" if self.half_day else "")
        return f"CLOSED · opens {et_clock(self.next_open, '%a %d %b %H:%M ET')}"


def market_clock(now: datetime) -> MarketClock:
    local_day = now.astimezone(MARKET_TIMEZONE).date()
    session = market_session(local_day)
    if session is not None and session.opens_at <= now < session.closes_at:
        return MarketClock(True, session.closes_at, None, session.half_day)
    upcoming = session if session is not None and now < session.opens_at else None
    upcoming = upcoming or next_market_session(local_day)
    return MarketClock(False, None, upcoming.opens_at, upcoming.half_day)


def market_day(now: datetime) -> date:
    """The ET calendar date: the bridge state and the decisions are keyed by it."""
    return now.astimezone(MARKET_TIMEZONE).date()


# --------------------------------------------------------------------------
# TWS data client 21
# --------------------------------------------------------------------------


class DataActor(Protocol):
    """The read-only surface of ``TWSPaperActor`` the assistant uses."""

    @property
    def connected(self) -> bool: ...

    @property
    def managed_accounts(self) -> frozenset[str]: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def account_snapshot(self) -> Any: ...

    async def market_snapshot(self, con_id: int, *, timeout_seconds: float = 3.0) -> Any: ...

    async def contract_details_for_symbol(
        self, symbol: str, primary_exchange: str | None
    ) -> Any: ...


ActorFactory = Callable[[Any], DataActor]


def default_actor_factory(config: Any) -> DataActor:
    from trading.market.tws_actor import TWSPaperActor

    return TWSPaperActor(config)


def tws_problem(exc: BaseException) -> str:
    """Owner-facing reason for a TWS failure; never a traceback."""
    name = type(exc).__name__
    if name == "TWSCompetingSessionError":
        return "IBKR login active elsewhere (phone/web) — log out there"
    if name in {"TWSConnectionError", "ConnectionError", "TimeoutError"}:
        return "TWS is not reachable on this Mac (is it running and logged in?)"
    if isinstance(exc, asyncio.TimeoutError):
        return "TWS did not answer in time"
    text = str(exc).strip()
    return f"{name}: {text[:160]}" if text else name


class TWSDataSession:
    """One lazily connected TWS data client, shared and serialized.

    Every user holds the lock for its whole operation, so a Claude tool call
    and an order's fact gathering never interleave on the connection. The
    connection is dropped after ``idle_seconds`` without use and after any
    connection-level failure; it is never used to send orders.
    """

    def __init__(
        self,
        *,
        account: str,
        client_id: int = TELEGRAM_TWS_CLIENT_ID,
        factory: ActorFactory = default_actor_factory,
        idle_seconds: float = TWS_IDLE_SECONDS,
        connect_timeout: float = TWS_CONNECT_TIMEOUT_SECONDS,
        monotonic: Callable[[], float] = monotonic_time.monotonic,
    ) -> None:
        if client_id in RESERVED_TWS_CLIENT_IDS or client_id <= 0:
            raise ValueError(f"TWS client id {client_id} is reserved or invalid")
        self._account = account
        self._client_id = client_id
        self._factory = factory
        self._idle = idle_seconds
        self._connect_timeout = connect_timeout
        self._monotonic = monotonic
        self._lock = asyncio.Lock()
        self._actor: DataActor | None = None
        self._last_used = 0.0
        self._accounts: frozenset[str] | None = None
        self._accounts_seen_at = 0.0

    @property
    def client_id(self) -> int:
        return self._client_id

    def recent_accounts(self, max_age_seconds: float) -> frozenset[str] | None:
        """Managed accounts from a handshake within ``max_age_seconds``, else None."""
        if self._accounts is None or self._monotonic() - self._accounts_seen_at > max_age_seconds:
            return None
        return self._accounts

    @asynccontextmanager
    async def use(self) -> AsyncIterator[DataActor]:
        async with self._lock:
            actor = await self._connected()
            try:
                yield actor
            except BaseException as exc:
                if not actor.connected or type(exc).__name__ in {
                    "TWSConnectionError",
                    "TWSCompetingSessionError",
                }:
                    await self._drop()
                raise
            finally:
                self._last_used = self._monotonic()

    async def close_if_idle(self) -> None:
        if self._actor is None or self._lock.locked():
            return
        if self._monotonic() - self._last_used >= self._idle:
            async with self._lock:
                await self._drop()

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def _connected(self) -> DataActor:
        if self._actor is not None and self._actor.connected:
            return self._actor
        await self._drop()
        from trading.market.tws_actor import TWSActorConfig

        actor = self._factory(TWSActorConfig(account=self._account, client_id=self._client_id))
        try:
            await asyncio.wait_for(actor.connect(), timeout=self._connect_timeout)
        except BaseException:
            try:
                await actor.disconnect()
            except Exception:  # noqa: BLE001 - the connect failure is what matters
                pass
            raise
        self._actor = actor
        self._accounts = frozenset(actor.managed_accounts)
        self._accounts_seen_at = self._monotonic()
        return actor

    async def _drop(self) -> None:
        actor, self._actor = self._actor, None
        if actor is not None:
            try:
                await actor.disconnect()
            except Exception:  # noqa: BLE001 - disconnect is best effort
                logger.warning("TWS data client %d did not disconnect cleanly", self._client_id)


# --------------------------------------------------------------------------
# Supervisor log
# --------------------------------------------------------------------------


_LOG_LINE = re.compile(r"^(?P<at>\S+) (?P<kind>[A-Z_]+)(?: (?P<rest>.*))?$")
_CHILD_START = re.compile(r"^(?P<name>\S+) pid=(?P<pid>\d+)")
_CHILD_EXIT = re.compile(r"^(?P<name>\S+) code=(?P<code>-?\d+) restart_in=(?P<restart>\S+)")
_SUPERVISOR_START = re.compile(r"pid=(?P<pid>\d+)")


@dataclass(frozen=True, slots=True)
class ChildState:
    name: str
    running: bool
    since: datetime | None
    detail: str


@dataclass(frozen=True, slots=True)
class SupervisorState:
    seen: bool
    alive: bool
    pid: int | None
    started_at: datetime | None
    stopped_at: datetime | None
    stop_reason: str | None
    session: str | None
    children: tuple[ChildState, ...] = ()


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def read_log_tail(path: Path, *, max_bytes: int = SUPERVISOR_LOG_TAIL_BYTES) -> list[str]:
    """The last whole lines of a log; a symlink or missing file reads as empty."""
    if path.is_symlink() or not path.is_file():
        return []
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - max_bytes))
        raw = stream.read()
    lines = raw.decode("utf-8", "replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # the first line was cut mid-way
    return lines


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def supervisor_state(
    lines: Sequence[str], *, pid_alive: Callable[[int], bool] = _pid_alive
) -> SupervisorState:
    """The newest supervisor run as its own log tells it."""
    pid: int | None = None
    started: datetime | None = None
    stopped: datetime | None = None
    reason: str | None = None
    session: str | None = None
    seen = False
    children: dict[str, ChildState] = {}
    for line in lines:
        match = _LOG_LINE.match(line.strip())
        if match is None:
            continue
        at = _parse_iso(match.group("at"))
        if at is None:
            continue
        kind, rest = match.group("kind"), match.group("rest") or ""
        if kind == "SUPERVISOR_START":
            found = _SUPERVISOR_START.search(rest)
            seen = True
            pid = int(found.group("pid")) if found else None
            started, stopped, reason, session = at, None, None, None
            children = {}
        elif kind == "SUPERVISOR_STOP":
            stopped = at
            reason = rest.removeprefix("reason=") or None
        elif kind.startswith("SESSION_"):
            session = f"{kind.removeprefix('SESSION_').lower()} {rest}".strip()
        elif kind == "CHILD_START":
            found = _CHILD_START.match(rest)
            if found:
                name = found.group("name")
                children[name] = ChildState(name, True, at, f"pid {found.group('pid')}")
        elif kind == "CHILD_EXIT":
            found = _CHILD_EXIT.match(rest)
            if found:
                name, code, restart = found.group("name", "code", "restart")
                if restart == "none":
                    detail = "finished" if code == "0" else f"exited with code {code}, not restarting"
                else:
                    detail = f"exited with code {code}, restarting in {restart} s"
                children[name] = ChildState(name, False, at, detail)
    alive = seen and stopped is None and pid is not None and pid_alive(pid)
    return SupervisorState(
        seen=seen,
        alive=alive,
        pid=pid,
        started_at=started,
        stopped_at=stopped,
        stop_reason=reason,
        session=session,
        children=tuple(children.values()),
    )


def format_supervisor(state: SupervisorState, now: datetime) -> list[str]:
    if not state.seen:
        return ["Supervisor: no supervisor.log yet (autotraderctl run has not run here)"]
    if state.alive:
        head = f"Supervisor: running (pid {state.pid}) since {observed_text(state.started_at, now)}"
    elif state.stopped_at is not None:
        head = f"Supervisor: STOPPED at {observed_text(state.stopped_at, now)} ({state.stop_reason or 'no reason'})"
    else:
        head = f"Supervisor: NOT RUNNING (pid {state.pid} is gone without a stop line — crashed?)"
    lines = [head]
    if state.session:
        lines.append(f"Session: {state.session}")
    for child in state.children:
        icon = "✅" if child.running else ("⏹" if child.detail == "finished" else "⚠️")
        if child.running:
            lines.append(f"{icon} {child.name}: running since {et_clock(child.since, '%H:%M ET')} ({child.detail})")
        else:
            lines.append(f"{icon} {child.name}: {child.detail} ({et_clock(child.since, '%H:%M ET')})")
    return lines


# --------------------------------------------------------------------------
# Bridge state, manual-order ledger, kill switch
# --------------------------------------------------------------------------


def bridge_trades(paths: UserPaths, session_date: date) -> tuple[tuple[Mapping[str, Any], ...], str | None]:
    """``(trades, problem)`` from the bridge's own state file for ``session_date``."""
    from trading.operations.notify_service import load_bridge_trades

    trades, _source, problem = load_bridge_trades(paths, session_date)
    return trades, problem


def bot_symbols(trades: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Symbols a bot bracket holds or is entering."""
    return frozenset(
        str(trade.get("symbol") or "").upper()
        for trade in trades
        if str(trade.get("status") or "") in BOT_ACTIVE_STATUSES and trade.get("symbol")
    )


class ManualLedger(Protocol):
    """Read access to the manual-order ledger (trading.execution.manual_orders)."""

    async def open_orders(self) -> Sequence[Any]: ...

    async def heartbeat_age(self, now: datetime) -> float | None: ...


def _module(name: str) -> Any:
    return importlib.import_module(name)


class DefaultManualLedger:
    """Adapter over trading.execution.manual_orders; imported on use."""

    async def open_orders(self) -> Sequence[Any]:
        return await _module(MANUAL_ORDERS_MODULE).list_active_orders()

    async def heartbeat_age(self, now: datetime) -> float | None:
        return await _module(MANUAL_ORDERS_MODULE).bridge_heartbeat_age(now)


@dataclass(frozen=True, slots=True)
class OrderView:
    """A manual-order ledger row as the owner reads it."""

    order_id: str
    short_id: str
    status: str
    side: str = ""
    qty: str = ""
    symbol: str = ""
    order_type: str = ""
    price: str = ""
    filled_qty: str = ""
    avg_fill_price: str = ""
    error: str = ""
    cancel_requested: bool = False
    expires_at: datetime | None = None

    @property
    def body(self) -> str:
        """``BUY 100 AAPL LMT 180.50``: the order without its id."""
        parts = (self.side, self.qty, self.symbol, self.order_type, self.price)
        return " ".join(part for part in parts if part) or "order"

    @property
    def label(self) -> str:
        return f"#{self.short_id} {self.body}"


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return price(value)
    return str(value)


def order_view(row: Any) -> OrderView:
    """``BUY 100 AAPL LMT 180.50``, ``SELL 50 TSLA STP 240.00``, ``CLOSE BF B bot
    bracket``, ``CLOSE ALL``: what the owner sent, as the ledger holds it."""
    order_id = str(_field(row, "id") or "")
    action = str(_field(row, "action") or "")
    symbol = str(_field(row, "symbol") or "")
    quantity = _field(row, "quantity")
    order_type = str(_field(row, "order_type") or "")
    limit_price, stop_price = _field(row, "limit_price"), _field(row, "stop_price")
    if action == "CLOSE_ALL":
        side, qty, symbol, order_type, shown = "CLOSE ALL", "", "", "", ""
    elif action == "CLOSE" and quantity is None:
        side, qty, order_type, shown = "CLOSE", "", "bot bracket", ""
    else:
        side = "SELL" if action in {"SELL", "SELL_STOP", "CLOSE"} else action
        qty = "" if quantity is None else str(quantity)
        shown = _text(limit_price if limit_price is not None else stop_price)
    filled = _field(row, "filled_qty")
    return OrderView(
        order_id=order_id,
        short_id=order_id[:6],
        status=str(_field(row, "status") or "?"),
        side=side,
        qty=qty,
        symbol=symbol,
        order_type=order_type,
        price=shown,
        filled_qty="" if filled in (None, 0, "0") else str(filled),
        avg_fill_price=_text(_field(row, "avg_fill_price")),
        error=str(_field(row, "error") or _field(row, "reject_code") or ""),
        cancel_requested=bool(_field(row, "cancel_requested")),
        expires_at=expires if isinstance(expires := _field(row, "expires_at"), datetime) else None,
    )


def format_manual_row(row: Any) -> str:
    """One ledger row: ``#id SIDE QTY SYM TYPE [price] · filled N · status``."""
    view = order_view(row)
    text = view.label
    if view.filled_qty:
        text += f" · filled {view.filled_qty}"
    if view.cancel_requested:
        text += " · cancel requested"
    return f"{text} · {view.status}"


class KillSwitch(Protocol):
    async def is_stopped(self) -> bool: ...


def heartbeat_text(age: float | None, *, max_age: float = 20.0) -> str:
    if age is None:
        return "Bridge: no heartbeat (not running) — manual orders are refused"
    if age > max_age:
        return f"Bridge: last heartbeat {age_text(age)} ago — manual orders are refused until it is back"
    return f"Bridge: alive (heartbeat {age_text(age)} ago)"


# --------------------------------------------------------------------------
# Screener and market context
# --------------------------------------------------------------------------


async def newest_screener_payload() -> Any | None:
    """The newest sweep of any session (weekends read Friday's)."""
    from db.core import get_db
    from trading.market.screener import ScreenerPayload

    async with get_db() as db:
        async with db.execute(
            """SELECT payload_json FROM autotrader_screener_snapshots
               ORDER BY observed_at DESC, id DESC LIMIT 1"""
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return ScreenerPayload.model_validate_json(str(row[0]))


def _context_from(payload: Any) -> Any | None:
    market = getattr(payload, "market", None)
    if not isinstance(market, Mapping):
        return None
    try:
        from trading.market.context import MarketContext
    except ImportError:
        return None
    return MarketContext.from_payload(market)


def format_hot_rows(payload: Any, now: datetime, *, limit: int = HOT_ROWS_SHOWN) -> str:
    from trading.market.screener import hotlist_rows

    rows = hotlist_rows(payload)
    observed = _parse_iso(payload.observed_at)
    lines = [f"🔥 Hot list · sweep {observed_text(observed, now)} · {len(rows)} hot of {payload.quoted} quoted"]
    if not rows:
        lines.append("No hot names in this sweep.")
    for row in rows[:limit]:
        details = [price(row.last), pct(row.change_pct)]
        if row.gap_pct is not None:
            details.append(f"gap {pct(row.gap_pct)}")
        if row.rvol is not None:
            details.append(f"rvol {row.rvol}")
        lines.append(f"{row.hot_rank}. {row.symbol} " + " · ".join(details))
    if len(rows) > limit:
        lines.append(f"… and {len(rows) - limit} more")
    return "\n".join(lines)


def format_context(context: Any, now: datetime, payload: Any | None = None) -> str:
    from trading.operations.notify_service import format_market_update, regime_label

    hot = None
    if payload is not None:
        from trading.market.screener import hotlist_rows

        hot = hotlist_rows(payload)
    lines = [format_market_update(context, hot)]
    indexes = [
        f"{name} {pct(getattr(context, attribute))}"
        for name, attribute in (
            ("SPY", "spy_change_pct"),
            ("QQQ", "qqq_change_pct"),
            ("IWM", "iwm_change_pct"),
            ("DIA", "dia_change_pct"),
        )
    ]
    lines.append("Indexes: " + ", ".join(indexes))
    if context.breadth_up_pct is not None:
        lines.append(
            f"Breadth: {context.breadth_up_pct}% of watchlist up "
            f"({context.advancers} up / {context.decliners} down)"
        )
    lines.append(
        f"Regime {regime_label(str(context.regime))} as of {observed_text(context.observed_at, now)}"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The tools
# --------------------------------------------------------------------------


def _no_input_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


# Data tools only. There is deliberately no tool that can create, change or
# cancel an order: orders come from the owner's own text through the
# deterministic parser and validator, never from Claude.
TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "get_status",
        "description": (
            "System health right now: market open/closed and the next open, the supervisor and "
            "each child process (screener, bridge, strategies, notifier, telegram), the bridge "
            "heartbeat, the market regime, and whether the PAPER kill switch is on. Call this when "
            "the owner asks whether things are running, why nothing is happening, or about the "
            "kill switch."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
    {
        "name": "get_positions",
        "description": (
            "Current PAPER account positions from IBKR (quantity, average cost, market price, "
            "unrealized P&L), each marked as held by the bot's bracket or by the owner. Call this "
            "for questions about holdings, exposure, or what the account owns."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
    {
        "name": "get_pnl",
        "description": (
            "Today's P&L: realized P&L of each bot trade from the bridge, plus the account's "
            "realized and unrealized day P&L and net liquidation from IBKR. Call this for "
            "questions about profit, loss, or performance today."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
    {
        "name": "get_orders",
        "description": (
            "Open orders: the owner's manual orders (queued, sent, working) and the bot's active "
            "brackets. Call this when asked about pending, working, or recent orders."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
    {
        "name": "get_hotlist",
        "description": (
            "The newest live-screener hot list: top movers with price, % change, gap and relative "
            "volume, and the sweep time. Call this for questions about what is moving or hot."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
    {
        "name": "get_market_context",
        "description": (
            "The newest market context: regime (risk-on, neutral, risk-off), index changes (SPY, "
            "QQQ, IWM, DIA), breadth, leading and lagging sectors, and its time. Call this for "
            "questions about the overall market."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
    {
        "name": "get_signals",
        "description": (
            "Today's strategy signals that reached the AI review, with the outcome (would buy, "
            "risk blocked, abstained) and the reasons. Call this for questions about what the bot "
            "saw, wanted to buy, or why it did or did not trade."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
    {
        "name": "get_quote",
        "description": (
            "A quote for one US stock: a real-time IBKR snapshot when available, otherwise the "
            "last screener values, clearly marked. Call this when asked for the price of a "
            "specific symbol."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "The ticker exactly as the owner wrote it, e.g. AAPL or BF B.",
                }
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_report",
        "description": (
            "The daily report so far: decisions by strategy, bot trades and realized P&L, AI "
            "review cost, and the last screener sweep. Call this for an overall summary of the day."
        ),
        "strict": True,
        "input_schema": _no_input_schema(),
    },
)
TOOL_NAMES = frozenset(tool["name"] for tool in TOOL_DEFINITIONS)


@dataclass(slots=True)
class DataTools:
    """The assistant's read-only data; every dependency is injectable."""

    paths: UserPaths
    tws: TWSDataSession | None
    tws_problem_text: str | None = None
    clock: Clock = _utc_now
    kill_switch: KillSwitch | None = None
    ledger: ManualLedger = field(default_factory=DefaultManualLedger)
    read_screener: Callable[[], Awaitable[Any | None]] = newest_screener_payload
    pid_alive: Callable[[int], bool] = _pid_alive

    def __post_init__(self) -> None:
        if self.kill_switch is None:
            from trading.storage.runtime_fence import SQLitePaperKillStore

            self.kill_switch = SQLitePaperKillStore()

    # -- dispatch ----------------------------------------------------------

    async def run_tool(self, name: str, arguments: Mapping[str, Any]) -> str:
        """One Claude tool call; failures come back as text, never as an exception."""
        if name not in TOOL_NAMES:
            raise ToolError(f"unknown tool {name!r}")
        handlers: dict[str, Callable[[], Awaitable[str]]] = {
            "get_status": self.status,
            "get_positions": self.positions,
            "get_pnl": self.pnl,
            "get_orders": self.orders,
            "get_hotlist": self.hotlist,
            "get_market_context": self.market,
            "get_signals": self.signals,
            "get_report": self.report,
        }
        if name == "get_quote":
            symbol = arguments.get("symbol") if isinstance(arguments, Mapping) else None
            if not isinstance(symbol, str):
                raise ToolError("get_quote needs a symbol")
            return clip(await self.quote(symbol))
        return clip(await handlers[name]())

    # -- shared pieces -----------------------------------------------------

    def _now(self) -> datetime:
        return self.clock()

    def _tws_unavailable(self) -> str | None:
        if self.tws is None:
            return self.tws_problem_text or "IBKR data is not configured for the assistant"
        return None

    async def _account(self) -> Any:
        problem = self._tws_unavailable()
        if problem is not None:
            raise ToolError(problem)
        assert self.tws is not None
        try:
            async with self.tws.use() as actor:
                return await asyncio.wait_for(actor.account_snapshot(), ACCOUNT_TIMEOUT_SECONDS)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - shown to the owner as a reason
            raise ToolError(f"IBKR account data unavailable: {tws_problem(exc)}") from exc

    async def _kill_switch_text(self) -> str:
        try:
            stopped = await self.kill_switch.is_stopped()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - unreadable means unknown, said plainly
            return f"Kill switch: UNREADABLE ({type(exc).__name__}) — treated as ON by the bridge"
        text = "Kill switch: ON (new buys blocked; there is no /resume)" if stopped else "Kill switch: off"
        rows = getattr(self.kill_switch, "identity_rows", None)
        if rows is None:
            return text
        try:
            fences = await rows()
        except Exception as exc:  # noqa: BLE001 - unreadable means unknown, said plainly
            return f"{text}\nIdentity fences: UNREADABLE ({type(exc).__name__}) — treated as ON by the bridge"
        stopped_names = [name for name, fenced, _reason, _at in fences if fenced]
        if stopped_names:
            return f"{text}\nIdentity fences ON: {', '.join(stopped_names)} (autotraderctl paper resume --identity)"
        return text

    async def _heartbeat_text(self) -> str:
        try:
            age = await self.ledger.heartbeat_age(self._now())
        except Exception as exc:  # noqa: BLE001
            return f"Bridge: heartbeat unknown ({type(exc).__name__})"
        return heartbeat_text(age)

    def _bridge(self) -> tuple[tuple[Mapping[str, Any], ...], str | None]:
        try:
            return bridge_trades(self.paths, market_day(self._now()))
        except Exception as exc:  # noqa: BLE001
            return (), f"bridge state unreadable ({type(exc).__name__})"

    # -- tools -------------------------------------------------------------

    async def status(self) -> str:
        now = self._now()
        lines = [f"🤖 Status · {et_clock(now, '%a %d %b %H:%M:%S ET')}"]
        lines.append("Market: " + market_clock(now).text())
        state = supervisor_state(
            read_log_tail(self.paths.log_directory / "supervisor.log"), pid_alive=self.pid_alive
        )
        lines.extend(format_supervisor(state, now))
        lines.append(await self._heartbeat_text())
        lines.append(await self._regime_line(now))
        lines.append(await self._kill_switch_text())
        return "\n".join(lines)

    async def _regime_line(self, now: datetime) -> str:
        try:
            payload = await self.read_screener()
            context = _context_from(payload) if payload is not None else None
        except Exception as exc:  # noqa: BLE001
            return f"Regime: unavailable ({type(exc).__name__})"
        if context is None:
            return "Regime: unknown (the newest screener sweep has no market context)"
        from trading.operations.notify_service import regime_label

        return f"Regime: {regime_label(str(context.regime))} as of {observed_text(context.observed_at, now)}"

    async def positions(self) -> str:
        now = self._now()
        try:
            account = await self._account()
        except ToolError as exc:
            return f"Positions unavailable: {exc}"
        trades, problem = self._bridge()
        bot = bot_symbols(trades)
        held = [position for position in account.positions if position.quantity != 0]
        lines = [f"📒 Positions · IBKR {observed_text(account.observed_at, now)}"]
        if not held:
            lines.append("No open positions.")
        for position in sorted(held, key=lambda item: item.symbol):
            owner = "🤖 bot bracket" if position.symbol.upper() in bot else "👤 yours"
            lines.append(
                f"{position.symbol}: {quantity(position.quantity)} sh @ avg {price(position.average_cost)}"
                f" · now {price(position.market_price)} · value {money(position.market_value)}"
                f" · unrealized {money(position.unrealized_pnl, signed=True)} · {owner}"
            )
        lines.append(
            f"Net liquidation {money(account.net_liquidation)} · unrealized "
            f"{money(account.unrealized_pnl, signed=True)}"
        )
        if problem:
            lines.append(f"(bot-held marks may be incomplete: {problem})")
        return "\n".join(lines)

    async def pnl(self) -> str:
        now = self._now()
        day = market_day(now)
        trades, problem = self._bridge()
        lines = [f"💰 P&L · {day:%a %d %b}"]
        total = Decimal("0")
        shown = 0
        for trade in trades:
            status = str(trade.get("status") or "")
            if status == "skipped" or trade.get("skipped") is True:
                continue
            symbol = str(trade.get("symbol") or "?")
            realized = decimal_or_none(trade.get("realized_pnl")) or Decimal("0")
            total += realized
            shown += 1
            filled = decimal_or_none(trade.get("filled")) or Decimal("0")
            exited = decimal_or_none(trade.get("exit_filled")) or Decimal("0")
            if status == "closed":
                detail = (
                    f"closed {quantity(filled)} sh {trade.get('avg_entry')} → {trade.get('avg_exit')}"
                    f" ({trade.get('exit_reason') or 'exit'})"
                )
            elif status == "open":
                detail = f"OPEN {quantity(filled - exited)} sh @ {trade.get('avg_entry')}"
            elif status == "cancelled":
                detail = "entry cancelled (never filled)"
            else:
                detail = status.replace("_", " ") or "unknown"
            label = f"{symbol} ({trade.get('strategy') or 'bot'})"
            lines.append(f"• {label}: {detail} · realized {money(realized, signed=True)}")
        if problem:
            lines.append(f"• ⚠️ {problem}")
        elif shown == 0:
            lines.append("• no bot trades today")
        lines.append(f"Bot realized today: {money(total, signed=True)}")
        try:
            account = await self._account()
        except ToolError as exc:
            lines.append(f"Account P&L unavailable: {exc}")
        else:
            lines.append(
                f"Account (IBKR, {observed_text(account.observed_at, now)}): realized "
                f"{money(account.realized_day_pnl, signed=True)} · unrealized "
                f"{money(account.unrealized_pnl, signed=True)} · net liq {money(account.net_liquidation)}"
            )
        return "\n".join(lines)

    async def orders(self) -> str:
        now = self._now()
        lines = [f"🧾 Open orders · {et_clock(now, '%H:%M:%S ET')}"]
        try:
            manual = list(await self.ledger.open_orders())
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Manual orders: unavailable ({type(exc).__name__})")
        else:
            if not manual:
                lines.append("Manual: none open")
            for row in manual:
                lines.append("Manual " + format_manual_row(row))
        trades, problem = self._bridge()
        active = [trade for trade in trades if str(trade.get("status") or "") in BOT_ACTIVE_STATUSES]
        if problem:
            lines.append(f"Bot brackets: unavailable ({problem})")
        elif not active:
            lines.append("Bot brackets: none active")
        for trade in active:
            lines.append(
                f"Bot {trade.get('strategy_name') or trade.get('strategy') or 'bracket'} {trade.get('symbol')}: "
                f"{trade.get('status')} · entry {trade.get('qty')} @ {trade.get('entry')} · stop "
                f"{trade.get('stop')} · target {trade.get('target')} · filled {trade.get('filled', 0)}"
                f" · exited {trade.get('exit_filled', 0)}"
            )
        return "\n".join(lines)

    async def hotlist(self) -> str:
        now = self._now()
        try:
            payload = await self.read_screener()
        except Exception as exc:  # noqa: BLE001
            return f"Hot list unavailable ({type(exc).__name__})"
        if payload is None:
            return "No screener sweep has been recorded yet."
        return format_hot_rows(payload, now)

    async def market(self) -> str:
        now = self._now()
        try:
            payload = await self.read_screener()
        except Exception as exc:  # noqa: BLE001
            return f"Market context unavailable ({type(exc).__name__})"
        if payload is None:
            return "No screener sweep has been recorded yet, so there is no market context."
        observed = _parse_iso(payload.observed_at)
        try:
            context = _context_from(payload)
        except Exception as exc:  # noqa: BLE001 - a bad document is reported, not raised
            return f"Market context in the newest sweep is unusable ({type(exc).__name__})."
        if context is None:
            return (
                f"No market context in the newest screener sweep ({observed_text(observed, now)}). "
                "It is recorded by sweeps from the market-context release onwards."
            )
        return format_context(context, now, payload)

    async def signals(self) -> str:
        from trading.operations.notify_service import format_decision, load_signal_decisions

        day = market_day(self._now())
        try:
            decisions = await load_signal_decisions(day)
        except Exception as exc:  # noqa: BLE001
            return f"Signals unavailable ({type(exc).__name__})"
        if not decisions:
            return f"No strategy signals reached the AI review on {day:%a %d %b}."
        shown = decisions[-SIGNALS_SHOWN:]
        header = f"🔔 Signals · {day:%a %d %b} · {len(decisions)} reviewed"
        if len(decisions) > len(shown):
            header += f" (newest {len(shown)} shown)"
        return "\n\n".join([header, *(format_decision(decision) for decision in shown)])

    async def report(self) -> str:
        from trading.operations.notify_service import collect_daily_report, format_daily_report

        now = self._now()
        try:
            report = await collect_daily_report(market_day(now), self.paths, now=now)
        except Exception as exc:  # noqa: BLE001
            return f"Daily report unavailable ({type(exc).__name__})"
        return format_daily_report(report) + f"\n(so far, as of {et_clock(now, '%H:%M ET')})"

    async def quote(self, raw_symbol: str) -> str:
        now = self._now()
        try:
            symbol = normalize_symbol(raw_symbol)
        except ToolError as exc:
            return str(exc)
        payload = None
        try:
            payload = await self.read_screener()
        except Exception as exc:  # noqa: BLE001 - the live quote may still work
            logger.warning("screener read for /quote failed: %s", type(exc).__name__)
        row = _screener_row(payload, symbol)
        live_problem = self._tws_unavailable()
        if live_problem is None:
            try:
                snapshot = await self.live_snapshot(symbol, row)
            except Exception as exc:  # noqa: BLE001 - falls back to the screener
                live_problem = tws_problem(exc) if not isinstance(exc, ToolError) else str(exc)
            else:
                return format_live_quote(symbol, snapshot, now)
        return format_fallback_quote(symbol, row, payload, live_problem, now)

    async def live_snapshot(self, symbol: str, row: Any | None = None) -> Any:
        """A real-time IBKR market snapshot; raises when none is available."""
        problem = self._tws_unavailable()
        if problem is not None:
            raise ToolError(problem)
        assert self.tws is not None
        con_id = row.con_id if row is not None else await universe_con_id(symbol)
        async with self.tws.use() as actor:
            if con_id is None:
                details = await actor.contract_details_for_symbol(symbol, None)
                con_id = details.con_id
            return await actor.market_snapshot(con_id, timeout_seconds=QUOTE_TIMEOUT_SECONDS)


async def universe_con_id(symbol: str) -> int | None:
    """The watchlist universe's resolved conId for ``symbol``."""
    from db.core import get_db

    async with get_db() as db:
        async with db.execute(
            """SELECT con_id FROM autotrader_universe
               WHERE status = 'resolved' AND con_id IS NOT NULL
                 AND (upper(symbol) = ? OR upper(ibkr_symbol) = ?)
               ORDER BY source LIMIT 1""",
            (symbol, symbol),
        ) as cursor:
            row = await cursor.fetchone()
    return int(row[0]) if row else None


async def universe_contract(symbol: str) -> tuple[str, int, str] | None:
    """``(ibkr_symbol, con_id, primary_exchange)`` of a resolved universe row."""
    from db.core import get_db

    async with get_db() as db:
        async with db.execute(
            """SELECT ibkr_symbol, con_id, primary_exchange FROM autotrader_universe
               WHERE status = 'resolved' AND con_id IS NOT NULL
                 AND primary_exchange IS NOT NULL AND trim(primary_exchange) != ''
                 AND (upper(ibkr_symbol) = ? OR upper(symbol) = ?)
               ORDER BY source LIMIT 1""",
            (symbol, symbol),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return str(row[0]), int(row[1]), str(row[2])


async def recent_accepted_decisions(session_date: date, *, since: datetime) -> list[tuple[str, str]]:
    """``(decision_id, symbol)`` of live accepted decisions the bridge may still
    be entering; a decision without a readable time counts (fail closed)."""
    from db.core import get_db

    async with get_db() as db:
        async with db.execute(
            """SELECT d.id, c.symbol, d.created_at
               FROM autotrader_candidate_decisions d
               JOIN autotrader_runs r ON r.id = d.run_id
               JOIN autotrader_candidates c ON c.id = d.candidate_id
               WHERE r.session_date = ? AND r.mode IN ('shadow', 'paper')
                 AND d.outcome = 'shadow_accepted'""",
            (session_date.isoformat(),),
        ) as cursor:
            rows = await cursor.fetchall()
    recent: list[tuple[str, str]] = []
    for decision_id, symbol, created_at in rows:
        created = _parse_iso(str(created_at))
        if created is None or created >= since:
            recent.append((str(decision_id), " ".join(str(symbol).upper().split())))
    return recent


def _screener_row(payload: Any | None, symbol: str) -> Any | None:
    if payload is None:
        return None
    for row in payload.rows:
        if row.symbol.upper() == symbol:
            return row
    return None


def format_live_quote(symbol: str, snapshot: Any, now: datetime) -> str:
    from trading.market.screener import TICK_VOLUME_MULTIPLIER

    reference = snapshot.reference_price
    change = None
    if reference is not None and snapshot.prior_close:
        change = (reference - snapshot.prior_close) / snapshot.prior_close * 100
    head = f"{symbol} {price(reference)} · {pct(change)} vs close {price(snapshot.prior_close)}"
    lines = [head]
    if snapshot.bid is not None and snapshot.ask is not None:
        mid = (snapshot.bid + snapshot.ask) / 2
        spread = (snapshot.ask - snapshot.bid) / mid * 100 if mid > 0 else None
        lines.append(
            f"bid {price(snapshot.bid)} / ask {price(snapshot.ask)} (spread {pct(spread).lstrip('+')})"
        )
    day = []
    if snapshot.open is not None:
        day.append(f"open {price(snapshot.open)}")
    if snapshot.day_high is not None:
        day.append(f"high {price(snapshot.day_high)}")
    if snapshot.day_low is not None:
        day.append(f"low {price(snapshot.day_low)}")
    if snapshot.volume_raw is not None:
        day.append(f"volume {quantity(snapshot.volume_raw * TICK_VOLUME_MULTIPLIER)}")
    if day:
        lines.append("Day: " + " · ".join(day))
    lines.append(f"Real-time IBKR snapshot · {observed_text(snapshot.observed_at, now)}")
    return "\n".join(lines)


def format_fallback_quote(
    symbol: str, row: Any | None, payload: Any | None, problem: str | None, now: datetime
) -> str:
    reason = problem or "unavailable"
    if row is None:
        return (
            f"{symbol}: no live quote ({reason}), and {symbol} is not in the newest screener sweep."
        )
    observed = _parse_iso(payload.observed_at) if payload is not None else None
    last = decimal_or_none(row.last)
    lines = [
        f"{symbol}: no live quote ({reason}).",
        f"Last screener sweep {observed_text(observed, now)} — NOT live:",
        f"last {price(last)} · {pct(decimal_or_none(row.change_pct))} vs close "
        f"{price(decimal_or_none(row.prior_close))}",
    ]
    bid, ask = decimal_or_none(row.bid), decimal_or_none(row.ask)
    if bid is not None and ask is not None:
        lines.append(f"bid {price(bid)} / ask {price(ask)}")
    return "\n".join(lines)


def compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "BOT_ACTIVE_STATUSES",
    "TELEGRAM_TWS_CLIENT_ID",
    "TOOL_DEFINITIONS",
    "TOOL_NAMES",
    "ChildState",
    "DataTools",
    "DefaultManualLedger",
    "ManualLedger",
    "MarketClock",
    "SupervisorState",
    "TWSDataSession",
    "ToolError",
    "age_text",
    "bot_symbols",
    "bridge_trades",
    "et_clock",
    "format_manual_row",
    "heartbeat_text",
    "market_clock",
    "market_day",
    "normalize_symbol",
    "read_log_tail",
    "supervisor_state",
    "tws_problem",
    "universe_con_id",
    "universe_contract",
    "recent_accepted_decisions",
    "order_view",
    "OrderView",
]
