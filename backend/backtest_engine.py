"""
Stage 6 — Unified Backtest/Live Engine.

Same event loop as the live bot. Only DataHandler (DuckDB vs IBKR) and
ExecutionHandler (simulated fills vs real IBKR) differ.
Strategy logic is identical between backtest and live.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from events import (
    Event, EventQueue, EventType,
    MarketEvent, SignalEvent, OrderEvent, FillEvent, RegimeEvent, MetricEvent,
)
from data_handler import DataHandler
from regime_detector import RegimeDetector

log = logging.getLogger(__name__)


class Strategy:
    """Base strategy interface. Override on_market_event to generate signals."""

    def on_market_event(self, event: MarketEvent, bars: Any = None) -> list[SignalEvent]:
        """Return list of signals for this bar. Override in subclass."""
        return []


class SimulatedExecution:
    """Simulated order execution for backtesting."""

    def __init__(self, slippage_pct: float = 0.05, commission_per_trade: float = 1.0):
        self.slippage_pct = slippage_pct
        self.commission = commission_per_trade

    def execute_order(self, order: OrderEvent, current_bar: MarketEvent | None = None) -> FillEvent | None:
        """Simulate a fill at close price + slippage."""
        if current_bar is None:
            return None
        price = current_bar.close
        if order.order_type == "MKT":
            slippage = price * (self.slippage_pct / 100)
            fill_price = price + slippage if order.direction == "LONG" else price - slippage
        else:
            fill_price = order.price or price

        return FillEvent(
            timestamp=order.timestamp,
            type=EventType.FILL,
            symbol=order.symbol,
            quantity=order.quantity,
            fill_price=round(fill_price, 4),
            commission=self.commission,
            exchange="SIM",
            direction=order.direction,
            rule_id=order.rule_id,
        )


class Portfolio:
    """Track positions, equity, and risk limits."""

    def __init__(self, initial_capital: float = 100000, risk_per_trade_pct: float = 1.0,
                 max_position_pct: float = 15.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.equity = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_position_pct = max_position_pct
        self.positions: dict[str, dict] = {}  # symbol -> {qty, avg_cost, rule_id}
        self.equity_curve: list[dict] = []
        self.trades: list[dict] = []

    def handle_signal(self, signal: SignalEvent, current_price: float, atr: float = 0) -> OrderEvent | None:
        """Convert a signal into an order with position sizing."""
        if signal.signal_type == "LONG" and signal.symbol not in self.positions:
            # 1% risk position sizing
            stop_distance = 2.0 * atr if atr > 0 else current_price * 0.02
            risk_amount = self.equity * (self.risk_per_trade_pct / 100)
            qty = int(risk_amount / max(stop_distance, 0.01))
            max_qty = int(self.equity * (self.max_position_pct / 100) / max(current_price, 0.01))
            qty = min(qty, max_qty)
            if qty < 1 or qty * current_price > self.cash:
                return None
            return OrderEvent(
                timestamp=signal.timestamp,
                type=EventType.ORDER,
                symbol=signal.symbol,
                order_type="MKT",
                quantity=qty,
                direction="LONG",
                rule_id=signal.rule_id,
            )
        elif signal.signal_type == "EXIT" and signal.symbol in self.positions:
            pos = self.positions[signal.symbol]
            return OrderEvent(
                timestamp=signal.timestamp,
                type=EventType.ORDER,
                symbol=signal.symbol,
                order_type="MKT",
                quantity=pos["qty"],
                direction="LONG",
                rule_id=signal.rule_id,
            )
        return None

    def update_fill(self, fill: FillEvent) -> None:
        """Update portfolio state on fill."""
        cost = fill.quantity * fill.fill_price + fill.commission
        if fill.symbol not in self.positions:
            # Opening position
            self.positions[fill.symbol] = {
                "qty": fill.quantity, "avg_cost": fill.fill_price,
                "rule_id": fill.rule_id, "entry_time": fill.timestamp,
            }
            self.cash -= cost
        else:
            # Closing position
            pos = self.positions.pop(fill.symbol)
            pnl = (fill.fill_price - pos["avg_cost"]) * pos["qty"] - fill.commission
            self.cash += pos["qty"] * fill.fill_price - fill.commission
            self.trades.append({
                "symbol": fill.symbol, "rule_id": pos["rule_id"],
                "entry_price": pos["avg_cost"], "exit_price": fill.fill_price,
                "qty": pos["qty"], "pnl": round(pnl, 2),
                "entry_time": pos["entry_time"], "exit_time": fill.timestamp,
            })

    def update_timeindex(self, event: MarketEvent) -> None:
        """Update equity curve with current market prices."""
        positions_value = sum(
            p["qty"] * event.close for sym, p in self.positions.items()
            if sym == event.symbol
        )
        # Simplified: only updates for the symbol in this event
        self.equity = self.cash + sum(
            p["qty"] * p["avg_cost"] for p in self.positions.values()
        )


class BacktestEngine:
    """Event-driven backtester using the same architecture as the live bot."""

    def __init__(self, data_handler: DataHandler, strategy: Strategy,
                 initial_capital: float = 100000):
        self.data = data_handler
        self.strategy = strategy
        self.portfolio = Portfolio(initial_capital)
        self.execution = SimulatedExecution()
        self.regime = RegimeDetector()
        self.events = EventQueue()
        self._current_bars: dict[str, MarketEvent] = {}

    def run(self) -> dict:
        """Run the full backtest. Returns results dict."""
        log.info("Starting backtest...")
        self.data.load()

        while self.data.continue_backtest:
            bars = self.data.next_bars()
            for market_event in bars:
                self.events.put(market_event)
                self._current_bars[market_event.symbol] = market_event

            while not self.events.empty():
                event = self.events.get()

                if event.type == EventType.MARKET:
                    me = event  # type: MarketEvent
                    # Strategy generates signals
                    symbol_bars = self.data.get_bars(me.symbol)
                    signals = self.strategy.on_market_event(me, bars=symbol_bars)
                    for sig in signals:
                        self.events.put(sig)
                    self.portfolio.update_timeindex(me)

                elif event.type == EventType.SIGNAL:
                    se = event  # type: SignalEvent
                    bar = self._current_bars.get(se.symbol)
                    price = bar.close if bar else 0
                    order = self.portfolio.handle_signal(se, price)
                    if order:
                        self.events.put(order)

                elif event.type == EventType.ORDER:
                    oe = event  # type: OrderEvent
                    bar = self._current_bars.get(oe.symbol)
                    fill = self.execution.execute_order(oe, bar)
                    if fill:
                        self.events.put(fill)

                elif event.type == EventType.FILL:
                    self.portfolio.update_fill(event)

        return self._results()

    def _results(self) -> dict:
        """Compile backtest results."""
        trades = self.portfolio.trades
        if not trades:
            return {"trades": [], "total_pnl": 0, "win_rate": 0, "total_trades": 0}

        pnls = [t["pnl"] for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        return {
            "trades": trades,
            "total_trades": len(trades),
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(len(winners) / len(trades) * 100, 1) if trades else 0,
            "avg_win": round(sum(winners) / len(winners), 2) if winners else 0,
            "avg_loss": round(sum(losers) / len(losers), 2) if losers else 0,
            "profit_factor": round(abs(sum(winners)) / abs(sum(losers)), 2) if losers and sum(losers) != 0 else 999,
            "final_equity": round(self.portfolio.equity, 2),
        }
