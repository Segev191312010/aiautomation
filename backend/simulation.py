"""
Simulation Engine — virtual paper trading + historical replay.

SimEngine
---------
When SIM_MODE=true, ALL orders are routed here instead of IBKR.
Tracks virtual cash and positions in the existing SQLite database
(adds three new tables: sim_account, sim_positions, sim_orders).

Order logic uses the **average-cost** method for position tracking.
Commission is deducted on every fill.

ReplayEngine
-----------
Streams pre-loaded historical OHLCV bars via WebSocket at N× speed.
Each bar is broadcast as {"type": "replay_bar", "symbol": ..., ...}.
Speed control uses a wall-clock cap (MAX_INTERVAL_S) so the UI is
always responsive regardless of bar size or speed setting.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import aiosqlite

from config import cfg
from models import (
    PlaybackState,
    SimAccountState,
    SimOrderRecord,
    SimPositionState,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_CREATE_SIM_ACCOUNT = """
CREATE TABLE IF NOT EXISTS sim_account (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    cash        REAL    NOT NULL,
    initial_cash REAL   NOT NULL,
    realized_pnl REAL   NOT NULL DEFAULT 0
);
"""

_CREATE_SIM_POSITIONS = """
CREATE TABLE IF NOT EXISTS sim_positions (
    symbol   TEXT PRIMARY KEY,
    qty      REAL NOT NULL,
    avg_cost REAL NOT NULL
);
"""

_CREATE_SIM_ORDERS = """
CREATE TABLE IF NOT EXISTS sim_orders (
    id         TEXT PRIMARY KEY,
    symbol     TEXT NOT NULL,
    action     TEXT NOT NULL,
    qty        REAL NOT NULL,
    price      REAL NOT NULL,
    commission REAL NOT NULL DEFAULT 0,
    pnl        REAL,
    timestamp  TEXT NOT NULL
);
"""


async def _ensure_sim_tables() -> None:
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        await db.execute(_CREATE_SIM_ACCOUNT)
        await db.execute(_CREATE_SIM_POSITIONS)
        await db.execute(_CREATE_SIM_ORDERS)
        await db.commit()


# ---------------------------------------------------------------------------
# SimEngine
# ---------------------------------------------------------------------------

class SimEngine:
    """
    Virtual paper-trading account backed by SQLite.

    Thread-safety: all mutations happen inside a single ``async with`` block
    on the aiosqlite connection, which serialises concurrent coroutines.
    """

    def __init__(self) -> None:
        self._db = cfg.DB_PATH
        self._ready = False
        self._broadcast: Optional[Callable] = None

    def set_broadcast(self, cb: Callable) -> None:
        self._broadcast = cb

    # ── Initialisation ──────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create tables and seed account if this is a fresh database."""
        await _ensure_sim_tables()
        async with aiosqlite.connect(self._db) as db:
            async with db.execute("SELECT COUNT(*) FROM sim_account") as cur:
                (count,) = await cur.fetchone()  # type: ignore[misc]
            if count == 0:
                await db.execute(
                    "INSERT INTO sim_account (id, cash, initial_cash, realized_pnl) VALUES (1, ?, ?, 0)",
                    (cfg.SIM_INITIAL_CASH, cfg.SIM_INITIAL_CASH),
                )
                await db.commit()
        self._ready = True
        log.info("SimEngine ready — initial cash = $%.2f", cfg.SIM_INITIAL_CASH)

    # ── Read helpers ─────────────────────────────────────────────────────────

    async def get_positions(
        self, price_fn: Optional[Callable[[str], Optional[float]]] = None
    ) -> list[SimPositionState]:
        """Return all virtual positions with live P&L."""
        async with aiosqlite.connect(self._db) as db:
            async with db.execute(
                "SELECT symbol, qty, avg_cost FROM sim_positions WHERE qty > 0"
            ) as cur:
                rows = await cur.fetchall()

        result: list[SimPositionState] = []
        for symbol, qty, avg_cost in rows:
            current_price = (price_fn(symbol) if price_fn else None) or avg_cost
            market_value = qty * current_price
            unrealized_pnl = (current_price - avg_cost) * qty
            pnl_pct = (current_price - avg_cost) / avg_cost * 100 if avg_cost else 0.0
            result.append(
                SimPositionState(
                    symbol=symbol,
                    qty=qty,
                    avg_cost=avg_cost,
                    current_price=current_price,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                    pnl_pct=pnl_pct,
                )
            )
        return result

    async def get_account(
        self, positions: Optional[list[SimPositionState]] = None
    ) -> SimAccountState:
        """Return current virtual account summary."""
        if positions is None:
            positions = await self.get_positions()

        async with aiosqlite.connect(self._db) as db:
            async with db.execute(
                "SELECT cash, initial_cash, realized_pnl FROM sim_account WHERE id=1"
            ) as cur:
                row = await cur.fetchone()

        if not row:
            return SimAccountState(
                cash=cfg.SIM_INITIAL_CASH,
                initial_cash=cfg.SIM_INITIAL_CASH,
                net_liquidation=cfg.SIM_INITIAL_CASH,
                positions_value=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
            )

        cash, initial_cash, realized_pnl = row
        positions_value = sum(p.market_value for p in positions)
        unrealized_pnl = sum(p.unrealized_pnl for p in positions)
        net_liq = cash + positions_value
        total_return_pct = (net_liq - initial_cash) / initial_cash * 100 if initial_cash else 0.0

        return SimAccountState(
            cash=cash,
            initial_cash=initial_cash,
            net_liquidation=net_liq,
            positions_value=positions_value,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            total_return_pct=total_return_pct,
        )

    async def get_orders(self, limit: int = 100) -> list[SimOrderRecord]:
        """Return recent virtual order history (newest first)."""
        async with aiosqlite.connect(self._db) as db:
            async with db.execute(
                "SELECT id, symbol, action, qty, price, commission, pnl, timestamp "
                "FROM sim_orders ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [
            SimOrderRecord(
                id=r[0], symbol=r[1], action=r[2],  # type: ignore[arg-type]
                qty=r[3], price=r[4], commission=r[5], pnl=r[6], timestamp=r[7],
            )
            for r in rows
        ]

    # ── Order execution ──────────────────────────────────────────────────────

    async def execute_order(
        self,
        symbol: str,
        action: str,     # "BUY" | "SELL"
        qty: float,
        price: float,
        order_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Execute a virtual order against the sim account.

        Returns (success: bool, message: str).
        """
        if not self._ready:
            await self.initialize()

        oid = order_id or str(uuid.uuid4())
        commission = cfg.SIM_COMMISSION
        ts = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self._db) as db:
            # Load account state
            async with db.execute(
                "SELECT cash, realized_pnl FROM sim_account WHERE id=1"
            ) as cur:
                acct_row = await cur.fetchone()
            if not acct_row:
                return False, "Sim account not initialised"
            cash, realized_pnl = acct_row

            # Load current position
            async with db.execute(
                "SELECT qty, avg_cost FROM sim_positions WHERE symbol=?", (symbol,)
            ) as cur:
                pos_row = await cur.fetchone()
            pos_qty: float = pos_row[0] if pos_row else 0.0
            pos_avg: float = pos_row[1] if pos_row else 0.0

            pnl: Optional[float] = None

            if action == "BUY":
                total_cost = qty * price + commission
                if cash < total_cost:
                    return (
                        False,
                        f"Insufficient cash: need ${total_cost:.2f}, have ${cash:.2f}",
                    )
                new_cash = cash - total_cost
                new_qty = pos_qty + qty
                new_avg = ((pos_qty * pos_avg) + (qty * price)) / new_qty if new_qty else price
                await db.execute(
                    "INSERT OR REPLACE INTO sim_positions (symbol, qty, avg_cost) VALUES (?, ?, ?)",
                    (symbol, new_qty, new_avg),
                )
                await db.execute(
                    "UPDATE sim_account SET cash=? WHERE id=1", (new_cash,)
                )

            elif action == "SELL":
                if pos_qty < qty:
                    return (
                        False,
                        f"Insufficient position: need {qty}, have {pos_qty}",
                    )
                proceeds = qty * price - commission
                pnl = (price - pos_avg) * qty - commission
                new_cash = cash + proceeds
                new_realized = realized_pnl + pnl
                new_qty = pos_qty - qty

                if new_qty <= 1e-9:
                    await db.execute(
                        "DELETE FROM sim_positions WHERE symbol=?", (symbol,)
                    )
                else:
                    await db.execute(
                        "UPDATE sim_positions SET qty=? WHERE symbol=?",
                        (new_qty, symbol),
                    )
                await db.execute(
                    "UPDATE sim_account SET cash=?, realized_pnl=? WHERE id=1",
                    (new_cash, new_realized),
                )
            else:
                return False, f"Unknown action: {action}"

            # Persist order record
            await db.execute(
                "INSERT INTO sim_orders "
                "(id, symbol, action, qty, price, commission, pnl, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (oid, symbol, action, qty, price, commission, pnl, ts),
            )
            await db.commit()

        log.info("SIM %s %.4g %s @ %.4f  pnl=%s", action, qty, symbol, price, pnl)

        if self._broadcast:
            await self._broadcast(
                {
                    "type": "sim_order",
                    "id": oid,
                    "symbol": symbol,
                    "action": action,
                    "qty": qty,
                    "price": price,
                    "commission": commission,
                    "pnl": pnl,
                    "timestamp": ts,
                }
            )
        return True, f"{action} {qty} {symbol} @ {price:.4f}"

    # ── Reset ────────────────────────────────────────────────────────────────

    async def reset(self) -> None:
        """Wipe all positions and orders; restore cash to initial amount."""
        async with aiosqlite.connect(self._db) as db:
            await db.execute(
                "UPDATE sim_account SET cash=?, realized_pnl=0 WHERE id=1",
                (cfg.SIM_INITIAL_CASH,),
            )
            await db.execute("DELETE FROM sim_positions")
            await db.execute("DELETE FROM sim_orders")
            await db.commit()
        log.info("SimEngine reset — cash restored to $%.2f", cfg.SIM_INITIAL_CASH)

        if self._broadcast:
            await self._broadcast({"type": "sim_reset"})


# ---------------------------------------------------------------------------
# ReplayEngine
# ---------------------------------------------------------------------------

class ReplayEngine:
    """
    Stream historical OHLCV bars via WebSocket at configurable speed.

    Wall-clock interval per bar = min(bar_duration / speed, MAX_INTERVAL_S).
    This caps latency so the UI stays responsive even for very slow bar sizes.

    Usage:
        await replay.load("AAPL", bars)
        await replay.play()
        replay.set_speed(5)
        await replay.pause()
        await replay.stop()
    """

    MAX_INTERVAL_S: float = 2.0   # never wait more than this between bars
    MIN_INTERVAL_S: float = 0.04  # 25 fps max

    def __init__(self) -> None:
        self._state = PlaybackState()
        self._bars: list[dict] = []
        self._task: Optional[asyncio.Task] = None
        self._broadcast: Optional[Callable] = None

    def set_broadcast(self, cb: Callable) -> None:
        self._broadcast = cb

    @property
    def state(self) -> PlaybackState:
        return self._state

    # ── Control ──────────────────────────────────────────────────────────────

    async def load(self, symbol: str, bars: list[dict]) -> None:
        """Load sorted-ascending OHLCV bars and reset to start."""
        await self.stop()
        self._bars = bars
        n = len(bars)
        self._state = PlaybackState(
            active=False,
            symbol=symbol,
            speed=self._state.speed,
            current_index=0,
            total_bars=n,
            start_ts=bars[0]["time"] if n else None,
            current_ts=bars[0]["time"] if n else None,
            end_ts=bars[-1]["time"] if n else None,
            progress=0.0,
        )
        log.info("ReplayEngine loaded %d bars for %s", n, symbol)

    async def play(self) -> None:
        if self._state.active:
            return
        if not self._bars:
            log.warning("ReplayEngine: no bars loaded, cannot play")
            return
        self._state.active = True
        self._task = asyncio.create_task(self._run())
        log.info("Replay started — %s @ %dx", self._state.symbol, self._state.speed)

    async def pause(self) -> None:
        self._state.active = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("Replay paused at %d/%d", self._state.current_index, self._state.total_bars)

    async def stop(self) -> None:
        await self.pause()
        self._state.current_index = 0
        self._state.progress = 0.0
        if self._bars:
            self._state.current_ts = self._state.start_ts
        log.info("Replay stopped")

    def set_speed(self, speed: int) -> None:
        self._state.speed = max(1, min(int(speed), 100))
        log.info("Replay speed → %dx", self._state.speed)

    # ── Background playback task ─────────────────────────────────────────────

    async def _run(self) -> None:
        bars = self._bars
        idx = self._state.current_index
        n = len(bars)

        while self._state.active and idx < n:
            bar = bars[idx]
            self._state.current_index = idx
            self._state.current_ts = bar["time"]
            self._state.progress = idx / max(n - 1, 1)

            if self._broadcast:
                await self._broadcast(
                    {
                        "type": "replay_bar",
                        "symbol": self._state.symbol,
                        "progress": self._state.progress,
                        "current_index": idx,
                        "total_bars": n,
                        **bar,
                    }
                )

            idx += 1

            # Compute wait time
            if idx < n:
                dur = float(bars[idx]["time"] - bar["time"])
            else:
                dur = 86400.0
            delay = min(max(dur / self._state.speed, self.MIN_INTERVAL_S), self.MAX_INTERVAL_S)

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

        # Playback complete
        self._state.active = False
        self._state.progress = 1.0
        if self._broadcast:
            await self._broadcast(
                {"type": "replay_done", "symbol": self._state.symbol}
            )
        log.info("Replay finished for %s", self._state.symbol)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

sim_engine = SimEngine()
replay_engine = ReplayEngine()
