"""Notification delivery service for alerts and trading signals."""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class NotificationType(str, Enum):
    IN_APP = "in_app"
    SOUND = "sound"
    BROWSER_PUSH = "browser_push"
    EMAIL = "email"


@dataclass
class NotificationPayload:
    title: str
    body: str
    symbol: str | None = None
    alert_id: str | None = None  # str to match Alert.id (uuid4) in models.py
    notification_type: str = "alert_fired"
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class NotificationService:
    """Centralized notification delivery. Broadcasts via WebSocket and manages
    per-user notification preferences and rate limiting."""

    def __init__(self):
        self._rate_limits: dict[int, list[float]] = {}  # user_id -> timestamps
        self._max_per_minute = 10
        self._ws_broadcast: Any = None  # Set by main.py on startup

    def set_ws_broadcast(self, broadcast_fn):
        """Register the WebSocket broadcast function from main.py."""
        self._ws_broadcast = broadcast_fn

    def _check_rate_limit(self, user_id: int) -> bool:
        """Return True if notification is allowed, False if rate-limited."""
        now = time.time()
        timestamps = self._rate_limits.get(user_id, [])
        # Clean old entries
        timestamps = [t for t in timestamps if now - t < 60]
        self._rate_limits[user_id] = timestamps
        return len(timestamps) < self._max_per_minute

    def _record_notification(self, user_id: int):
        now = time.time()
        if user_id not in self._rate_limits:
            self._rate_limits[user_id] = []
        self._rate_limits[user_id].append(now)

    async def notify_alert_fired(self, alert: dict, user_id: int = 1):
        """Send notification when an alert fires."""
        if not self._check_rate_limit(user_id):
            logger.warning("Rate limited notifications for user %d", user_id)
            return

        payload = NotificationPayload(
            title=f"Alert: {alert.get('name', 'Alert')}",
            body=f"{alert.get('symbol', '')} - {alert.get('message', 'Alert triggered')}",
            symbol=alert.get("symbol"),
            alert_id=alert.get("id"),
            notification_type="alert_fired",
            data=alert,
        )
        await self._broadcast(payload)
        self._record_notification(user_id)

    async def notify_signal(self, signal: dict, user_id: int = 1):
        """Send notification when a trading signal is generated."""
        if not self._check_rate_limit(user_id):
            return

        payload = NotificationPayload(
            title=f"Signal: {signal.get('action', 'SIGNAL')} {signal.get('symbol', '')}",
            body=f"Rule '{signal.get('rule_name', '')}' triggered",
            symbol=signal.get("symbol"),
            notification_type="signal",
            data=signal,
        )
        await self._broadcast(payload)
        self._record_notification(user_id)

    async def notify_order_filled(self, order: dict, user_id: int = 1):
        """Send notification when an order is filled."""
        payload = NotificationPayload(
            title=f"Order Filled: {order.get('action', '')} {order.get('symbol', '')}",
            body=f"{order.get('qty', 0)} shares at ${order.get('fill_price', 0):.2f}",
            symbol=order.get("symbol"),
            notification_type="order_filled",
            data=order,
        )
        await self._broadcast(payload)

    async def notify_risk_event(self, event: dict, user_id: int = 1):
        """Send notification for risk warnings/breaches."""
        payload = NotificationPayload(
            title=f"Risk {event.get('level', 'Warning')}: {event.get('symbol', '')}",
            body=event.get("message", "Risk limit triggered"),
            symbol=event.get("symbol"),
            notification_type="risk_event",
            data=event,
        )
        await self._broadcast(payload)

    async def _broadcast(self, payload: NotificationPayload):
        """Broadcast notification via WebSocket."""
        if self._ws_broadcast is None:
            logger.debug("No WS broadcast function registered; skipping notification")
            return
        msg = {
            "type": payload.notification_type,
            "data": {
                "title": payload.title,
                "body": payload.body,
                "symbol": payload.symbol,
                "alert_id": payload.alert_id,
                "timestamp": payload.timestamp,
                **payload.data,
            },
        }
        try:
            await self._ws_broadcast(msg)
        except Exception:
            logger.exception("Failed to broadcast notification")


# Singleton instance
notification_service = NotificationService()
