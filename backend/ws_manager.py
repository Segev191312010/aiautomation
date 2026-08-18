"""Authenticated, user-scoped WebSocket delivery for the general ``/ws`` hub."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from collections.abc import Mapping

from fastapi import WebSocket

from metrics import record_websocket_outcome

log = logging.getLogger(__name__)


PUBLIC_EVENT_TYPES = frozenset({
    "bar",
    "heartbeat",
    "quote",
})

PRIVATE_EVENT_TYPES = frozenset({
    "alert_fired",
    "bot",
    "bot_health",
    "error",
    "exit",
    "ibkr_state",
    "order_filled",
    "positions_update",
    "replay_bar",
    "replay_done",
    "risk_event",
    "sim_order",
    "sim_reset",
    "signal",
})

_UNTRUSTED_IDENTITY_FIELDS = frozenset({"user_id", "owner_user_id"})


class ConnectionManager:
    def __init__(self) -> None:
        self._connections_by_user: dict[str, set[WebSocket]] = defaultdict(set)
        self._user_by_connection: dict[WebSocket, str] = {}
        self.metrics: Counter[str] = Counter()

    @staticmethod
    def _require_user_id(user_id: str) -> str:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        return user_id.strip()

    async def connect(
        self,
        ws: WebSocket,
        user_id: str,
        subprotocol: str | None = None,
    ) -> None:
        bound_user_id = self._require_user_id(user_id)
        await ws.accept(subprotocol=subprotocol)
        self._connections_by_user[bound_user_id].add(ws)
        self._user_by_connection[ws] = bound_user_id
        self.metrics["connections"] += 1
        record_websocket_outcome("connected")

    def disconnect(self, ws: WebSocket) -> None:
        user_id = self._user_by_connection.pop(ws, None)
        if user_id is None:
            return
        sockets = self._connections_by_user.get(user_id)
        if sockets is not None:
            sockets.discard(ws)
            if not sockets:
                self._connections_by_user.pop(user_id, None)
        self.metrics["disconnects"] += 1
        record_websocket_outcome("disconnected")

    async def broadcast_public(self, data: Mapping[str, object]) -> None:
        event = self._sanitize(data)
        event_type = self._event_type(event)
        if event_type not in PUBLIC_EVENT_TYPES:
            self.metrics["rejected_public_events"] += 1
            record_websocket_outcome("public_rejected")
            log.warning("Rejected non-public WebSocket fanout event type=%r", event_type)
            return
        await self._send_many(set(self._user_by_connection), event)
        self.metrics["public_events"] += 1
        record_websocket_outcome("public_delivered")

    async def send_to_user(self, user_id: str, data: Mapping[str, object]) -> None:
        owner_user_id = self._require_user_id(user_id)
        event = self._sanitize(data)
        event_type = self._event_type(event)
        if event_type not in PRIVATE_EVENT_TYPES:
            self.metrics["rejected_private_events"] += 1
            record_websocket_outcome("private_rejected")
            log.warning("Rejected non-private user WebSocket event type=%r", event_type)
            return
        await self._send_many(set(self._connections_by_user.get(owner_user_id, ())), event)
        self.metrics["private_events"] += 1
        record_websocket_outcome("private_delivered")

    async def route_event(
        self,
        data: Mapping[str, object],
        *,
        owner_user_id: str | None = None,
    ) -> None:
        event_type = self._event_type(data)
        if event_type in PUBLIC_EVENT_TYPES:
            await self.broadcast_public(data)
            return
        if event_type in PRIVATE_EVENT_TYPES:
            if owner_user_id is None:
                self.metrics["private_events_missing_owner"] += 1
                record_websocket_outcome("private_missing_owner")
                log.error("Dropped private WebSocket event without trusted owner type=%s", event_type)
                return
            await self.send_to_user(owner_user_id, data)
            return
        self.metrics["unknown_events"] += 1
        record_websocket_outcome("unknown_event")
        log.warning("Dropped WebSocket event with unclassified type=%r", event_type)

    async def broadcast(self, data: Mapping[str, object]) -> None:
        """Compatibility entry point restricted to explicitly public events."""
        await self.broadcast_public(data)

    @staticmethod
    def _event_type(data: Mapping[str, object]) -> str:
        value = data.get("type")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _sanitize(data: Mapping[str, object]) -> dict[str, object]:
        return {key: value for key, value in data.items() if key not in _UNTRUSTED_IDENTITY_FIELDS}

    async def _send_many(self, sockets: set[WebSocket], data: Mapping[str, object]) -> None:
        payload = json.dumps(data)
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(payload)
            except Exception as exc:
                log.debug("WebSocket send failed, marking dead: %s", exc)
                record_websocket_outcome("send_failed")
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _broadcast(payload: dict, *, owner_user_id: str | None = None) -> None:
    await manager.route_event(payload, owner_user_id=owner_user_id)
