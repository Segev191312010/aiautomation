"""
IBKR connection singleton using ib_insync.

Features
--------
* Lazy IB() creation — binds to the live FastAPI event loop, not import time.
* Callbacks registered before connect so no events are missed.
* Auto-reconnect background task (configurable interval via cfg.RECONNECT_INTERVAL).
* WS broadcast hook: connection-state changes propagate to all WebSocket clients.

Usage:
    from ibkr_client import ibkr
    await ibkr.connect()
    summary = await ibkr.get_account_summary()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from ib_insync import IB, Future, Option, Stock
from ib_insync import Trade as IBTrade

from config import cfg
from models import AccountSummary, Position

log = logging.getLogger(__name__)


class IBKRClient:
    def __init__(self) -> None:
        # IB() is created lazily in connect() so it binds to the correct loop.
        self._ib: Optional[IB] = None
        self._connected: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Auto-reconnect
        self._reconnect_task: Optional[asyncio.Task] = None
        self._stopping: bool = False

        # Broadcast hook — set by main.py so we can push state to WS clients
        self._broadcast: Optional[Callable] = None

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def set_broadcast(self, cb: Callable) -> None:
        """Register a callback for broadcasting WS events."""
        self._broadcast = cb

    async def _emit(self, payload: dict) -> None:
        if self._broadcast:
            try:
                await self._broadcast(payload)
            except Exception:
                pass

    def _schedule_emit(self, payload: dict) -> None:
        if not self._broadcast:
            return
        coro = self._emit(payload)
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        target_loop = self._loop or running_loop
        if target_loop and target_loop.is_running():
            if running_loop is target_loop:
                target_loop.create_task(coro)
            else:
                asyncio.run_coroutine_threadsafe(coro, target_loop)
            return
        log.debug("No running event loop available to emit IBKR state")

    # ------------------------------------------------------------------
    # Internal: lazy IB factory + callback wiring
    # ------------------------------------------------------------------

    def _get_or_create_ib(self) -> IB:
        if self._ib is None:
            self._ib = IB()
            self._ib.errorEvent        += self._on_error
            self._ib.orderStatusEvent  += self._on_order_status
            self._ib.disconnectedEvent += self._on_disconnected
            log.debug("IB instance created and callbacks wired")
        return self._ib

    # ------------------------------------------------------------------
    # Event callbacks
    # ------------------------------------------------------------------

    def _on_error(
        self,
        req_id: int,
        error_code: int,
        error_string: str,
        advanced_order_reject_json: str = "",
    ) -> None:
        """
        Handles every TWS error/warning.

        201 / 202 — order rejected / cancelled
        2100–2110  — connectivity notices
        < 1000     — actual errors
        >= 1000    — informational
        """
        if error_code in (201, 202):
            log.error(
                "Order REJECTED/CANCELLED — req_id=%d code=%d: %s %s",
                req_id, error_code, error_string, advanced_order_reject_json,
            )
        elif 2100 <= error_code <= 2110:
            log.warning("IBKR connectivity notice [%d]: %s", error_code, error_string)
        elif error_code < 1000:
            log.error("IBKR error — req_id=%d code=%d: %s", req_id, error_code, error_string)
        else:
            log.info("IBKR info [%d]: %s", error_code, error_string)

    def _on_order_status(self, trade: IBTrade) -> None:
        """Full order-lifecycle logging."""
        status    = trade.orderStatus.status
        order_id  = trade.order.orderId
        symbol    = trade.contract.symbol if trade.contract else "?"
        filled    = trade.orderStatus.filled
        remaining = trade.orderStatus.remaining
        avg_fill  = trade.orderStatus.avgFillPrice

        if status == "Filled":
            log.info(
                "Order FILLED — id=%d %s filled=%.0f avg_price=%.4f",
                order_id, symbol, filled, avg_fill,
            )
        elif status in ("Cancelled", "ApiCancelled", "Inactive"):
            log.warning(
                "Order CANCELLED/INACTIVE — id=%d %s status=%s",
                order_id, symbol, status,
            )
        else:
            log.debug(
                "Order status — id=%d %s status=%s filled=%.0f remaining=%.0f",
                order_id, symbol, status, filled, remaining,
            )

    def _on_disconnected(self) -> None:
        """Called by ib_insync when the TWS connection drops unexpectedly."""
        self._connected = False
        log.warning("IBKR disconnected — will attempt auto-reconnect")
        self._schedule_emit({"type": "ibkr_state", "connected": False})

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """
        Connect to IB Gateway / TWS.

        Tries cfg.IBKR_CLIENT_ID first, then up to 4 incrementing IDs
        (to survive "clientId already in use" errors which manifest as
        asyncio.TimeoutError rather than ConnectionRefusedError).
        """
        asyncio.set_event_loop(asyncio.get_running_loop())
        self._loop = asyncio.get_running_loop()

        ib = self._get_or_create_ib()
        if self._connected and ib.isConnected():
            return True

        self._connected = False

        for offset in range(5):
            client_id = cfg.IBKR_CLIENT_ID + offset
            try:
                await ib.connectAsync(
                    host=cfg.IBKR_HOST,
                    port=cfg.IBKR_PORT,
                    clientId=client_id,
                    timeout=4,
                )
                self._connected = True
                if offset:
                    log.warning(
                        "Connected with clientId=%d (configured %d was in use)",
                        client_id, cfg.IBKR_CLIENT_ID,
                    )
                log.info(
                    "Connected to IBKR at %s:%s (clientId=%d)",
                    cfg.IBKR_HOST, cfg.IBKR_PORT, client_id,
                )
                await self._emit({"type": "ibkr_state", "connected": True})
                return True

            except ConnectionRefusedError:
                log.error(
                    "Connection refused at %s:%s — is IB Gateway / TWS running?",
                    cfg.IBKR_HOST, cfg.IBKR_PORT,
                )
                break   # IB Gateway not running; retrying won't help

            except Exception as exc:
                # If the socket is up but account sync timed out, keep the live
                # connection and allow market data to flow instead of thrashing.
                if ib.isConnected():
                    self._connected = True
                    log.warning(
                        "Connected to IBKR at %s:%s (clientId=%d) with degraded sync (%s: %s)",
                        cfg.IBKR_HOST,
                        cfg.IBKR_PORT,
                        client_id,
                        type(exc).__name__,
                        exc,
                    )
                    await self._emit({"type": "ibkr_state", "connected": True})
                    return True

                # TimeoutError can also mean clientId in use; reconnect with next ID.
                log.warning(
                    "clientId=%d failed (%s: %s) — trying %d",
                    client_id, type(exc).__name__, exc, client_id + 1,
                )
                try:
                    ib.disconnect()
                except Exception:
                    pass
                self._ib = None
                ib = self._get_or_create_ib()

        self._connected = False
        return False

    async def disconnect(self) -> None:
        await self.stop_reconnect_loop()
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
        self._connected = False
        log.info("Disconnected from IBKR")

    def is_connected(self) -> bool:
        return bool(self._ib and self._ib.isConnected())

    @property
    def ib(self) -> IB:
        return self._get_or_create_ib()

    # ------------------------------------------------------------------
    # Auto-reconnect loop
    # ------------------------------------------------------------------

    async def start_reconnect_loop(self) -> None:
        """
        Start a background task that periodically tries to re-establish
        the IBKR connection whenever it drops.
        Does nothing if cfg.RECONNECT_INTERVAL == 0.
        """
        if cfg.RECONNECT_INTERVAL <= 0:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._stopping = False
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        log.info("Auto-reconnect loop started (interval=%ds)", cfg.RECONNECT_INTERVAL)

    async def stop_reconnect_loop(self) -> None:
        self._stopping = True
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

    async def _reconnect_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(cfg.RECONNECT_INTERVAL)
            if self._stopping:
                break
            if not self.is_connected():
                log.info("Auto-reconnect: attempting connection…")
                try:
                    ok = await self.connect()
                    if ok:
                        log.info("Auto-reconnect: success — reconciling pending orders")
                        try:
                            from order_executor import reconcile_pending_orders
                            await reconcile_pending_orders()
                        except Exception as recon_exc:
                            log.error("Post-reconnect order reconciliation failed: %s", recon_exc)
                    else:
                        log.warning("Auto-reconnect: failed — will retry in %ds", cfg.RECONNECT_INTERVAL)
                except Exception as exc:
                    log.warning("Auto-reconnect error: %s", exc)

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_account_summary(self) -> AccountSummary:
        vals = {
            v.tag: v.value
            for v in await self._ib.accountSummaryAsync()
            if v.currency in ("USD", "BASE", "")
        }

        def _f(tag: str, default: float = 0.0) -> float:
            try:
                return float(vals.get(tag, default))
            except (ValueError, TypeError):
                return default

        return AccountSummary(
            balance=_f("NetLiquidation"),
            cash=_f("TotalCashValue"),
            margin_used=_f("MaintMarginReq"),
            unrealized_pnl=_f("UnrealizedPnL"),
            realized_pnl=_f("RealizedPnL"),
            currency="USD",
        )

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def get_positions(self) -> list[Position]:
        """
        Try portfolio() first (includes live price/P&L), fall back to positions().
        For multi-account setups, filter to the primary trading account.
        """
        positions: list[Position] = []

        # Determine primary account
        accounts = self._ib.managedAccounts()
        primary_account = accounts[0] if accounts else None

        # Try portfolio() first — has live price data
        portfolio_items = self._ib.portfolio(primary_account) if primary_account else self._ib.portfolio()
        if portfolio_items:
            for item in portfolio_items:
                if item.position == 0:
                    continue
                contract = item.contract
                positions.append(
                    Position(
                        symbol=contract.symbol,
                        asset_type=contract.secType,
                        qty=item.position,
                        avg_cost=item.averageCost,
                        market_price=item.marketPrice,
                        market_value=item.marketValue,
                        unrealized_pnl=item.unrealizedPNL,
                        realized_pnl=item.realizedPNL,
                    )
                )
            return positions

        # Fallback: positions() + fetch live prices
        raw_positions = self._ib.positions(primary_account) if primary_account else self._ib.positions()
        raw_positions = [p for p in raw_positions if p.position != 0]

        if not raw_positions:
            return positions

        # Fetch live prices via IBKR reqTickers (fast snapshot)
        live_prices: dict[str, float] = {}
        try:
            contracts = [p.contract for p in raw_positions]
            await self._ib.qualifyContractsAsync(*contracts)
            tickers = await self._ib.reqTickersAsync(*contracts)
            for ticker in tickers:
                sym = ticker.contract.symbol
                price = ticker.marketPrice()
                if price and price > 0 and price != float('inf'):
                    live_prices[sym] = price
                elif ticker.last and ticker.last > 0:
                    live_prices[sym] = ticker.last
                elif ticker.close and ticker.close > 0:
                    live_prices[sym] = ticker.close
        except Exception as e:
            log.warning("Failed to fetch live prices via IBKR: %s — trying yfinance", e)

        # Fallback: yfinance batch for any missing prices
        missing = [p.contract.symbol for p in raw_positions if p.contract.symbol not in live_prices]
        if missing:
            try:
                import yfinance as yf
                data = yf.download(missing, period="1d", progress=False)
                if not data.empty:
                    close = data["Close"]
                    if hasattr(close, "columns"):
                        for sym in missing:
                            if sym in close.columns:
                                val = close[sym].dropna()
                                if len(val) > 0:
                                    live_prices[sym] = float(val.iloc[-1])
                    else:
                        if len(missing) == 1 and len(close.dropna()) > 0:
                            live_prices[missing[0]] = float(close.dropna().iloc[-1])
            except Exception as e:
                log.warning("yfinance price fallback failed: %s", e)

        for item in raw_positions:
            sym = item.contract.symbol
            avg_cost = item.avgCost
            qty = item.position
            live_price = live_prices.get(sym, avg_cost)
            market_value = qty * live_price
            unrealized_pnl = (live_price - avg_cost) * qty

            positions.append(
                Position(
                    symbol=sym,
                    asset_type=item.contract.secType,
                    qty=qty,
                    avg_cost=avg_cost,
                    market_price=live_price,
                    market_value=market_value,
                    unrealized_pnl=round(unrealized_pnl, 2),
                    realized_pnl=0.0,
                )
            )
        return positions

    # ------------------------------------------------------------------
    # Contract factories
    # ------------------------------------------------------------------

    def make_stock_contract(
        self, symbol: str, exchange: str = "SMART", currency: str = "USD"
    ) -> Stock:
        return Stock(symbol, exchange, currency)

    def make_future_contract(
        self,
        symbol: str,
        exchange: str,
        last_trade_date: str,
        currency: str = "USD",
    ) -> Future:
        return Future(symbol, last_trade_date, exchange, currency=currency)

    def make_option_contract(
        self,
        symbol: str,
        last_trade_date: str,
        strike: float,
        right: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> Option:
        return Option(symbol, last_trade_date, strike, right, exchange, currency=currency)


# Module-level singleton — IB() is created lazily inside connect().
ibkr = IBKRClient()
