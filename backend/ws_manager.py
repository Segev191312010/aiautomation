"""
WebSocket connection manager — broadcast hub for general /ws events.

Consumed by:
    main.py              -> manager singleton, _broadcast helper
    runtime_state.py     -> manager stored via initialize_runtime_state
"""
from __future__ import annotations

import json
import logging
from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket, subprotocol: str | None = None) -> None:
        await ws.accept(subprotocol=subprotocol)
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._connections.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, data: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_text(json.dumps(data))
            except Exception as exc:
                log.debug("Broadcast: websocket send failed, marking dead: %s", exc)
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _broadcast(payload: dict) -> None:
    await manager.broadcast(payload)
