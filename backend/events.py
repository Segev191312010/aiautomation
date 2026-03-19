"""
Stage 1 — Typed Event Core & Priority-Queue Bus.

Deterministic, replayable event backbone for live IBKR streams,
historical backtests, and unit-test simulations.
"""
from __future__ import annotations

import queue
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional


class EventType(Enum):
    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"
    TICKER = "TICKER"
    REGIME = "REGIME"
    METRIC = "METRIC"


@dataclass(order=True)
class Event:
    timestamp: datetime
    type: EventType = field(compare=False)

    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


@dataclass(order=True)
class MarketEvent(Event):
    symbol: str = field(default="", compare=False)
    open: float = field(default=0.0, compare=False)
    high: float = field(default=0.0, compare=False)
    low: float = field(default=0.0, compare=False)
    close: float = field(default=0.0, compare=False)
    volume: float = field(default=0.0, compare=False)
    adj_close: Optional[float] = field(default=None, compare=False)
    exchange: str = field(default="", compare=False)
    is_delisted: bool = field(default=False, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.MARKET)


@dataclass(order=True)
class SignalEvent(Event):
    symbol: str = field(default="", compare=False)
    rule_id: str = field(default="", compare=False)
    signal_type: Literal["LONG", "SHORT", "EXIT"] = field(default="LONG", compare=False)
    strength: float = field(default=0.0, compare=False)  # 0-1
    raw_score: float = field(default=0.0, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.SIGNAL)


@dataclass(order=True)
class OrderEvent(Event):
    symbol: str = field(default="", compare=False)
    order_type: Literal["MKT", "LMT"] = field(default="MKT", compare=False)
    quantity: float = field(default=0.0, compare=False)
    price: Optional[float] = field(default=None, compare=False)
    direction: Literal["LONG", "SHORT"] = field(default="LONG", compare=False)
    rule_id: str = field(default="", compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.ORDER)


@dataclass(order=True)
class FillEvent(Event):
    symbol: str = field(default="", compare=False)
    quantity: float = field(default=0.0, compare=False)
    fill_price: float = field(default=0.0, compare=False)
    commission: float = field(default=0.0, compare=False)
    exchange: str = field(default="SIM", compare=False)
    direction: Literal["LONG", "SHORT"] = field(default="LONG", compare=False)
    rule_id: str = field(default="", compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.FILL)


@dataclass(order=True)
class RegimeEvent(Event):
    regime: Literal["BULL", "BEAR", "HIGH_VOL", "LOW_VOL"] = field(default="BULL", compare=False)
    volatility: float = field(default=0.0, compare=False)
    market_score: float = field(default=0.5, compare=False)  # 0-1 bullishness

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.REGIME)


@dataclass(order=True)
class MetricEvent(Event):
    metric_type: str = field(default="", compare=False)
    value: float = field(default=0.0, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.METRIC)


class EventQueue:
    """Thread-safe priority queue that processes events in chronological order."""

    def __init__(self):
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = 0  # tie-breaker for same-timestamp events

    def put(self, event: Event) -> None:
        self._counter += 1
        self._q.put((event.timestamp, self._counter, event))

    def get(self) -> Event:
        _, _, event = self._q.get()
        return event

    def empty(self) -> bool:
        return self._q.empty()

    def qsize(self) -> int:
        return self._q.qsize()

    def get_batch(self) -> list[Event]:
        """Get all events with the same timestamp as the next event."""
        if self.empty():
            return []
        first = self.get()
        batch = [first]
        while not self.empty():
            ts, counter, event = self._q.queue[0]  # peek
            if ts == first.timestamp:
                self._q.get()
                batch.append(event)
            else:
                break
        return batch
