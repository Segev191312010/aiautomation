"""
Stage 1 — Typed Event Core & Priority-Queue Bus.

Deterministic, replayable event backbone for live IBKR streams,
historical backtests, and unit-test simulations.

Features:
  - Typed event hierarchy (7 event types)
  - Priority queue with timestamp ordering + tie-breaking
  - Batch processing (all events at same timestamp)
  - JSON serialization / deserialization (for JSONL replay)
  - Event bus with pub/sub (components subscribe to specific types)
  - IBKR adapter (convert ib_insync objects → typed events)
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Optional

log = logging.getLogger(__name__)


# ── Event Types ──────────────────────────────────────────────────────────────

class EventType(Enum):
    MARKET = "MARKET"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"
    TICKER = "TICKER"
    REGIME = "REGIME"
    METRIC = "METRIC"
    AI_DECISION = "AI_DECISION"


# ── Base Event ───────────────────────────────────────────────────────────────

@dataclass(order=True)
class Event:
    timestamp: datetime
    type: EventType = field(compare=False)

    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, EventType):
                d[k] = v.value
            elif isinstance(v, Enum):
                d[k] = v.value
            else:
                d[k] = v
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ── Concrete Event Types ─────────────────────────────────────────────────────

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
    rule_name: str = field(default="", compare=False)
    signal_type: Literal["LONG", "SHORT", "EXIT"] = field(default="LONG", compare=False)
    strength: float = field(default=0.0, compare=False)
    raw_score: float = field(default=0.0, compare=False)
    regime: str = field(default="", compare=False)

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
    stop_price: Optional[float] = field(default=None, compare=False)
    tp_price: Optional[float] = field(default=None, compare=False)

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
    order_id: Optional[int] = field(default=None, compare=False)
    slippage: float = field(default=0.0, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.FILL)


@dataclass(order=True)
class RegimeEvent(Event):
    regime: Literal["BULL", "BEAR", "HIGH_VOL", "LOW_VOL"] = field(default="BULL", compare=False)
    volatility: float = field(default=0.0, compare=False)
    market_score: float = field(default=0.5, compare=False)
    spy_vs_sma200: float = field(default=0.0, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.REGIME)


@dataclass(order=True)
class MetricEvent(Event):
    metric_type: str = field(default="", compare=False)
    value: float = field(default=0.0, compare=False)
    symbol: str = field(default="", compare=False)
    rule_id: str = field(default="", compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.METRIC)


@dataclass(order=True)
class AIDecisionEvent(Event):
    decision_type: str = field(default="", compare=False)   # optimization, rule_change, etc.
    description: str = field(default="", compare=False)
    old_params: str = field(default="", compare=False)      # JSON string
    new_params: str = field(default="", compare=False)      # JSON string
    confidence: float = field(default=0.0, compare=False)

    def __post_init__(self):
        super().__post_init__()
        if self.type is None:
            object.__setattr__(self, "type", EventType.AI_DECISION)


# ── Event Deserialization ────────────────────────────────────────────────────

_EVENT_CLASSES = {
    "MARKET": MarketEvent,
    "SIGNAL": SignalEvent,
    "ORDER": OrderEvent,
    "FILL": FillEvent,
    "REGIME": RegimeEvent,
    "METRIC": MetricEvent,
    "AI_DECISION": AIDecisionEvent,
}


def event_from_dict(d: dict) -> Event:
    """Deserialize a dict (from JSON) back into a typed Event."""
    event_type = d.get("type", "MARKET")
    cls = _EVENT_CLASSES.get(event_type, Event)
    ts = d.get("timestamp")
    if isinstance(ts, str):
        d["timestamp"] = datetime.fromisoformat(ts)
    d["type"] = EventType(event_type)
    # Filter to only fields the dataclass accepts
    import dataclasses
    valid_fields = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in d.items() if k in valid_fields}
    return cls(**filtered)


def event_from_json(json_str: str) -> Event:
    return event_from_dict(json.loads(json_str))


# ── Priority Queue ───────────────────────────────────────────────────────────

class EventQueue:
    """Thread-safe priority queue with chronological ordering and batch support."""

    def __init__(self):
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = 0
        self._lock = threading.Lock()

    def put(self, event: Event) -> None:
        with self._lock:
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
            with self._lock:
                if not self._q.queue:
                    break
                ts, counter, event = self._q.queue[0]
            if ts == first.timestamp:
                self._q.get()
                batch.append(event)
            else:
                break
        return batch

    def clear(self) -> None:
        with self._lock:
            while not self._q.empty():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break


# ── Event Bus (Pub/Sub) ──────────────────────────────────────────────────────

EventHandler = Callable[[Event], None]


class EventBus:
    """Publish/subscribe system for decoupled event processing.

    Components register handlers for specific event types.
    When an event is published, all matching handlers fire synchronously.

    Usage:
        bus = EventBus()
        bus.subscribe(EventType.MARKET, my_strategy.on_market)
        bus.subscribe(EventType.FILL, my_portfolio.on_fill)
        bus.publish(market_event)  # fires my_strategy.on_market
    """

    def __init__(self):
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._global_handlers: list[EventHandler] = []  # fires for ALL events
        self._event_count = 0

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to ALL event types (useful for logging/replay)."""
        self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type in self._handlers:
            self._handlers[event_type] = [h for h in self._handlers[event_type] if h is not handler]

    def publish(self, event: Event) -> None:
        """Dispatch event to all registered handlers."""
        self._event_count += 1
        # Type-specific handlers
        for handler in self._handlers.get(event.type, []):
            try:
                handler(event)
            except Exception as e:
                log.error("EventBus handler error (%s): %s", event.type.value, e)
        # Global handlers
        for handler in self._global_handlers:
            try:
                handler(event)
            except Exception as e:
                log.error("EventBus global handler error: %s", e)

    @property
    def event_count(self) -> int:
        return self._event_count

    def handler_count(self, event_type: EventType | None = None) -> int:
        if event_type:
            return len(self._handlers.get(event_type, []))
        return sum(len(h) for h in self._handlers.values()) + len(self._global_handlers)


# ── IBKR Adapter ─────────────────────────────────────────────────────────────

def ibkr_bar_to_market_event(symbol: str, bar: Any) -> MarketEvent:
    """Convert an ib_insync BarData object to a MarketEvent."""
    ts = getattr(bar, "date", None)
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return MarketEvent(
        timestamp=ts,
        type=EventType.MARKET,
        symbol=symbol.upper(),
        open=float(getattr(bar, "open", 0)),
        high=float(getattr(bar, "high", 0)),
        low=float(getattr(bar, "low", 0)),
        close=float(getattr(bar, "close", 0)),
        volume=float(getattr(bar, "volume", 0)),
    )


def ibkr_fill_to_fill_event(trade_obj: Any) -> FillEvent | None:
    """Convert an ib_insync Trade fill to a FillEvent."""
    try:
        status = trade_obj.orderStatus
        if status.status != "Filled":
            return None
        order = trade_obj.order
        contract = trade_obj.contract
        return FillEvent(
            timestamp=datetime.now(timezone.utc),
            type=EventType.FILL,
            symbol=contract.symbol,
            quantity=float(status.filled),
            fill_price=float(status.avgFillPrice),
            commission=0.0,  # IBKR reports commission separately
            exchange=getattr(contract, "exchange", "IBKR"),
            direction="LONG" if order.action == "BUY" else "SHORT",
            rule_id="",
            order_id=order.orderId,
        )
    except Exception as e:
        log.warning("Failed to convert IBKR fill to FillEvent: %s", e)
        return None
