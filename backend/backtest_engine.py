"""
Event-driven backtest engine — unified architecture with live bot.

Same event loop concept as bot_runner. DataHandler streams historical bars,
Strategy evaluates conditions via rule_engine, Portfolio tracks positions
with proper mark-to-market equity.

This module provides the advanced engine for multi-symbol backtesting
and rule-based strategy evaluation. For simple single-symbol backtesting,
see backtester.py (the primary endpoint).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from config import cfg
from events import (
    Event, EventQueue, EventType,
    MarketEvent, SignalEvent, OrderEvent, FillEvent,
)
from data_handler import DataHandler
from indicators import _atr
from models import Condition
from rule_engine import evaluate_conditions

log = logging.getLogger(__name__)


class RuleStrategy:
    """Strategy that evaluates rule conditions via the shared rule engine.

    Uses the same evaluate_conditions() as the live bot — no look-ahead bias
    is guaranteed by DataHandler.get_bars() returning only bars up to current.
    """

    def __init__(
        self,
        entry_conditions: list[Condition],
        exit_conditions: list[Condition],
        condition_logic: str = "AND",
    ):
        self.entry_conditions = entry_conditions
        self.exit_conditions = exit_conditions
        self.condition_logic = condition_logic

    def on_market_event(
        self,
        event: MarketEvent,
        bars: pd.DataFrame | None = None,
        held_symbols: set[str] | None = None,
    ) -> list[SignalEvent]:
        """Evaluate entry/exit conditions for this bar.

        Returns signal events for entries or exits.
        """
        if bars is None or len(bars) < 2:
            return []

        signals: list[SignalEvent] = []
        _held = held_symbols or set()

        if event.symbol in _held:
            # Check exit conditions
            if self.exit_conditions and evaluate_conditions(
                self.exit_conditions, bars, self.condition_logic,
                symbol=event.symbol,
            ):
                signals.append(SignalEvent(
                    timestamp=event.timestamp,
                    type=EventType.SIGNAL,
                    symbol=event.symbol,
                    signal_type="EXIT",
                    rule_id="backtest",
                ))
        else:
            # Check entry conditions
            if evaluate_conditions(
                self.entry_conditions, bars, self.condition_logic,
                symbol=event.symbol,
            ):
                signals.append(SignalEvent(
                    timestamp=event.timestamp,
                    type=EventType.SIGNAL,
                    symbol=event.symbol,
                    signal_type="LONG",
                    rule_id="backtest",
                ))

        return signals


class SimulatedExecution:
    """Simulated order execution for backtesting."""

    def __init__(self, slippage_pct: float = 0.05, commission_per_trade: float = 0.0):
        self.slippage_pct = slippage_pct
        self.commission = commission_per_trade or cfg.SIM_COMMISSION

    def execute_order(
        self, order: OrderEvent, current_bar: MarketEvent | None = None,
    ) -> FillEvent | None:
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
    """Track positions, equity, and risk limits with proper mark-to-market."""

    def __init__(
        self,
        initial_capital: float = 100_000,
        risk_per_trade_pct: float = 1.0,
        max_position_pct: float = 15.0,
        position_size_pct: float = 100.0,
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.equity = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_position_pct = max_position_pct
        self.position_size_pct = position_size_pct
        self.positions: dict[str, dict] = {}  # symbol -> {qty, avg_cost, entry_time, ...}
        self.equity_curve: list[dict] = []
        self.trades: list[dict] = []
        self._latest_prices: dict[str, float] = {}
        self._running_peak = initial_capital

    @property
    def held_symbols(self) -> set[str]:
        return set(self.positions.keys())

    def handle_signal(
        self, signal: SignalEvent, current_price: float, atr: float = 0,
    ) -> OrderEvent | None:
        if signal.signal_type == "LONG" and signal.symbol not in self.positions:
            # Position sizing: % of equity
            available = self.equity * (self.position_size_pct / 100)
            qty = int(available / max(current_price, 0.01))
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
        cost = fill.quantity * fill.fill_price + fill.commission
        if fill.symbol not in self.positions:
            # Opening position
            self.positions[fill.symbol] = {
                "qty": fill.quantity,
                "avg_cost": fill.fill_price,
                "rule_id": fill.rule_id,
                "entry_time": fill.timestamp,
            }
            self.cash -= cost
        else:
            # Closing position
            pos = self.positions.pop(fill.symbol)
            pnl = (fill.fill_price - pos["avg_cost"]) * pos["qty"] - 2 * fill.commission
            self.cash += pos["qty"] * fill.fill_price - fill.commission
            duration = (fill.timestamp - pos["entry_time"]).total_seconds() / 86400
            self.trades.append({
                "symbol": fill.symbol,
                "rule_id": pos["rule_id"],
                "entry_price": round(pos["avg_cost"], 2),
                "exit_price": round(fill.fill_price, 2),
                "qty": pos["qty"],
                "pnl": round(pnl, 2),
                "pnl_pct": round((fill.fill_price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2),
                "entry_time": pos["entry_time"].isoformat(),
                "exit_time": fill.timestamp.isoformat(),
                "duration_days": round(duration, 2),
                "exit_reason": "signal",
            })

    def update_mark_to_market(self, latest_prices: dict[str, float], timestamp: datetime) -> None:
        """Update equity curve with true mark-to-market across all positions."""
        self._latest_prices.update(latest_prices)

        # Mark all positions to latest known prices
        positions_value = 0.0
        for sym, pos in self.positions.items():
            price = self._latest_prices.get(sym, pos["avg_cost"])
            positions_value += pos["qty"] * price

        self.equity = self.cash + positions_value

        if self.equity > self._running_peak:
            self._running_peak = self.equity
        dd_pct = (
            (self._running_peak - self.equity) / self._running_peak * 100
            if self._running_peak > 0 else 0.0
        )

        self.equity_curve.append({
            "time": int(timestamp.timestamp()),
            "equity": round(self.equity, 2),
            "drawdown_pct": round(dd_pct, 2),
        })


class BacktestEngine:
    """Event-driven backtester using the same architecture as the live bot."""

    def __init__(
        self,
        data_handler: DataHandler,
        strategy: RuleStrategy,
        initial_capital: float = 100_000,
        position_size_pct: float = 100.0,
    ):
        self.data = data_handler
        self.strategy = strategy
        self.portfolio = Portfolio(initial_capital, position_size_pct=position_size_pct)
        self.execution = SimulatedExecution()
        self.events = EventQueue()
        self._current_bars: dict[str, MarketEvent] = {}

    def run(self) -> dict:
        """Run the full backtest. Returns results dict."""
        log.info("Starting event-driven backtest...")
        self.data.load()

        while self.data.continue_backtest:
            bars = self.data.next_bars()
            if not bars:
                continue

            latest_prices: dict[str, float] = {}

            for market_event in bars:
                self.events.put(market_event)
                self._current_bars[market_event.symbol] = market_event
                latest_prices[market_event.symbol] = market_event.close

            while not self.events.empty():
                event = self.events.get()

                if event.type == EventType.MARKET:
                    me: MarketEvent = event  # type: ignore[assignment]
                    symbol_bars = self.data.get_bars(me.symbol)
                    signals = self.strategy.on_market_event(
                        me, bars=symbol_bars,
                        held_symbols=self.portfolio.held_symbols,
                    )
                    for sig in signals:
                        self.events.put(sig)

                elif event.type == EventType.SIGNAL:
                    se: SignalEvent = event  # type: ignore[assignment]
                    bar = self._current_bars.get(se.symbol)
                    price = bar.close if bar else 0
                    order = self.portfolio.handle_signal(se, price)
                    if order:
                        self.events.put(order)

                elif event.type == EventType.ORDER:
                    oe: OrderEvent = event  # type: ignore[assignment]
                    bar = self._current_bars.get(oe.symbol)
                    fill = self.execution.execute_order(oe, bar)
                    if fill:
                        self.events.put(fill)

                elif event.type == EventType.FILL:
                    self.portfolio.update_fill(event)  # type: ignore[arg-type]

            # Mark-to-market at end of this timestamp
            if bars:
                self.portfolio.update_mark_to_market(
                    latest_prices, bars[0].timestamp,
                )

        # Force-close any remaining positions
        self._force_close_open_positions()

        return self._results()

    def _force_close_open_positions(self) -> None:
        """Close all open positions at last known prices."""
        for sym in list(self.portfolio.positions.keys()):
            bar = self._current_bars.get(sym)
            if bar is None:
                continue
            pos = self.portfolio.positions[sym]
            fill = FillEvent(
                timestamp=bar.timestamp,
                type=EventType.FILL,
                symbol=sym,
                quantity=pos["qty"],
                fill_price=bar.close,
                commission=self.execution.commission,
                exchange="SIM",
                direction="LONG",
                rule_id=pos.get("rule_id", ""),
            )
            self.portfolio.update_fill(fill)
            self.portfolio.trades[-1]["exit_reason"] = "end_of_data"

    def _results(self) -> dict:
        """Compile backtest results with full metrics."""
        trades = self.portfolio.trades
        if not trades:
            return {
                "trades": [],
                "total_pnl": 0,
                "win_rate": 0,
                "total_trades": 0,
                "equity_curve": self.portfolio.equity_curve,
                "final_equity": round(self.portfolio.equity, 2),
            }

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
            "profit_factor": (
                round(abs(sum(winners)) / abs(sum(losers)), 2)
                if losers and sum(losers) != 0 else 999
            ),
            "final_equity": round(self.portfolio.equity, 2),
            "equity_curve": self.portfolio.equity_curve,
        }
