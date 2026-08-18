import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ws_manager import ConnectionManager, PRIVATE_EVENT_TYPES, PUBLIC_EVENT_TYPES
from notification_service import NotificationService
from simulation import ReplayEngine


class FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.accepted_subprotocol: str | None = None
        self.messages: list[dict] = []
        self.fail_send = fail_send

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted_subprotocol = subprotocol

    async def send_text(self, payload: str) -> None:
        if self.fail_send:
            raise RuntimeError("socket closed")
        self.messages.append(json.loads(payload))


@pytest.mark.asyncio
async def test_private_event_isolated_between_two_users() -> None:
    manager = ConnectionManager()
    alice = FakeWebSocket()
    bob = FakeWebSocket()
    await manager.connect(alice, "alice")
    await manager.connect(bob, "bob")

    await manager.route_event(
        {"type": "alert_fired", "symbol": "AAPL"},
        owner_user_id="alice",
    )

    assert alice.messages == [{"type": "alert_fired", "symbol": "AAPL"}]
    assert bob.messages == []


@pytest.mark.asyncio
async def test_private_event_reaches_all_sockets_for_same_user() -> None:
    manager = ConnectionManager()
    first = FakeWebSocket()
    second = FakeWebSocket()
    other = FakeWebSocket()
    await manager.connect(first, "alice")
    await manager.connect(second, "alice")
    await manager.connect(other, "bob")

    await manager.send_to_user("alice", {"type": "order_filled", "symbol": "MSFT"})

    assert first.messages == second.messages
    assert len(first.messages) == 1
    assert other.messages == []


@pytest.mark.asyncio
async def test_disconnect_removes_only_target_socket_and_empty_user_bucket() -> None:
    manager = ConnectionManager()
    first = FakeWebSocket()
    second = FakeWebSocket()
    await manager.connect(first, "alice")
    await manager.connect(second, "alice")

    manager.disconnect(first)
    await manager.send_to_user("alice", {"type": "signal", "symbol": "NVDA"})
    assert first.messages == []
    assert len(second.messages) == 1

    manager.disconnect(second)
    assert "alice" not in manager._connections_by_user
    assert second not in manager._user_by_connection


@pytest.mark.asyncio
async def test_public_allowlisted_event_fans_out_to_every_socket() -> None:
    manager = ConnectionManager()
    sockets = [FakeWebSocket(), FakeWebSocket(), FakeWebSocket()]
    await manager.connect(sockets[0], "alice")
    await manager.connect(sockets[1], "alice")
    await manager.connect(sockets[2], "bob")

    await manager.route_event({"type": "bar", "symbol": "AAPL"})

    assert all(ws.messages == [{"type": "bar", "symbol": "AAPL"}] for ws in sockets)


@pytest.mark.asyncio
async def test_private_event_without_trusted_owner_is_dropped() -> None:
    manager = ConnectionManager()
    socket = FakeWebSocket()
    await manager.connect(socket, "alice")

    await manager.route_event({"type": "positions_update", "positions": []})

    assert socket.messages == []
    assert manager.metrics["private_events_missing_owner"] == 1


@pytest.mark.asyncio
async def test_payload_identity_cannot_spoof_routing_or_leak_to_client() -> None:
    manager = ConnectionManager()
    alice = FakeWebSocket()
    bob = FakeWebSocket()
    await manager.connect(alice, "alice")
    await manager.connect(bob, "bob")

    await manager.route_event(
        {
            "type": "order_filled",
            "symbol": "TSLA",
            "user_id": "bob",
            "owner_user_id": "bob",
        },
        owner_user_id="alice",
    )

    assert alice.messages == [{"type": "order_filled", "symbol": "TSLA"}]
    assert bob.messages == []


@pytest.mark.asyncio
async def test_unknown_and_private_events_cannot_use_public_broadcast() -> None:
    manager = ConnectionManager()
    socket = FakeWebSocket()
    await manager.connect(socket, "alice")

    await manager.broadcast({"type": "alert_fired"})
    await manager.broadcast({"type": "not_classified"})
    await manager.route_event({"type": "order_modified", "symbol": "AAPL"})

    assert socket.messages == []
    assert manager.metrics["rejected_public_events"] == 2
    assert manager.metrics["unknown_events"] == 1


@pytest.mark.asyncio
async def test_failed_socket_is_cleaned_up_without_affecting_healthy_socket() -> None:
    manager = ConnectionManager()
    dead = FakeWebSocket(fail_send=True)
    healthy = FakeWebSocket()
    await manager.connect(dead, "alice")
    await manager.connect(healthy, "alice")

    await manager.send_to_user("alice", {"type": "alert_fired"})

    assert dead not in manager._user_by_connection
    assert healthy in manager._user_by_connection
    assert len(healthy.messages) == 1


@pytest.mark.asyncio
async def test_user_ids_are_required_non_empty_strings() -> None:
    manager = ConnectionManager()
    with pytest.raises(ValueError):
        await manager.connect(FakeWebSocket(), "")
    with pytest.raises(ValueError):
        await manager.connect(FakeWebSocket(), 123)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_notification_service_requires_and_propagates_trusted_string_owner() -> None:
    service = NotificationService()
    deliveries: list[tuple[dict, str | None]] = []

    async def deliver(payload: dict, *, owner_user_id: str | None = None) -> None:
        deliveries.append((payload, owner_user_id))

    service.set_ws_broadcast(deliver)
    await service.notify_order_filled(
        {"symbol": "AAPL", "action": "BUY", "qty": 1, "fill_price": 100.0},
        user_id="alice",
    )

    assert deliveries[0][1] == "alice"
    assert deliveries[0][0]["type"] == "order_filled"
    assert "user_id" not in deliveries[0][0]["data"]

    with pytest.raises(ValueError):
        await service.notify_signal({"symbol": "AAPL"}, user_id="")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
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
        "signal",
        "sim_order",
        "sim_reset",
    ],
)
async def test_every_private_event_type_routes_only_to_its_owner(event_type: str) -> None:
    manager = ConnectionManager()
    alice = FakeWebSocket()
    bob = FakeWebSocket()
    await manager.connect(alice, "alice")
    await manager.connect(bob, "bob")

    await manager.route_event({"type": event_type}, owner_user_id="alice")

    assert alice.messages == [{"type": event_type}]
    assert bob.messages == []


def test_public_allowlist_is_market_transport_only() -> None:
    assert PUBLIC_EVENT_TYPES == {"bar", "heartbeat", "quote"}


@pytest.mark.asyncio
async def test_replay_events_and_controls_are_locked_to_loading_user() -> None:
    engine = ReplayEngine()
    deliveries: list[tuple[dict, str | None]] = []

    async def deliver(payload: dict, *, owner_user_id: str | None = None) -> None:
        deliveries.append((payload, owner_user_id))

    engine.set_broadcast(deliver)
    engine.MIN_INTERVAL_S = 0
    engine.MAX_INTERVAL_S = 0
    await engine.load("AAPL", [{"time": 1, "close": 100.0}], user_id="alice")

    with pytest.raises(PermissionError):
        await engine.play(user_id="bob")
    with pytest.raises(PermissionError):
        await engine.load("MSFT", [{"time": 1}], user_id="bob")

    await engine.play(user_id="alice")
    assert engine._task is not None
    await engine._task
    assert [owner for _, owner in deliveries] == ["alice", "alice"]
    assert [payload["type"] for payload, _ in deliveries] == ["replay_bar", "replay_done"]


def test_frontend_subscriptions_match_classified_backend_contract() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    hook_source = (repo_root / "dashboard/src/hooks/useWebSocket.ts").read_text()
    type_source = (repo_root / "dashboard/src/types/index.ts").read_text()
    subscribers = set(re.findall(r"wsService\.subscribe\('([^']+)'", hook_source))
    declared_types = set(re.findall(r"\| '([^']+)'", type_source[type_source.index("export type WsEventType"):]))

    assert subscribers <= PUBLIC_EVENT_TYPES | PRIVATE_EVENT_TYPES
    assert subscribers <= declared_types
    assert "filled" not in declared_types
    assert "order_modified" not in subscribers | declared_types
    assert "account_update" not in subscribers | declared_types


def test_emergency_close_outcome_is_audit_logging_not_websocket_contract() -> None:
    safety_source = (Path(__file__).resolve().parents[1] / "safety_kernel.py").read_text()
    assert "emergency_close_outcome" in safety_source
    assert "set_broadcast" not in safety_source
    assert "_broadcast(" not in safety_source
