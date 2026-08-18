"""Web Push API, persistence, delivery, and alert integration tests."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiosqlite
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from httpx import ASGITransport, AsyncClient
from pywebpush import WebPushException


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _vapid_config() -> tuple[str, str, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_value = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_value = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return _base64url(public_value), _base64url(private_value), "mailto:ops@example.com"


def _subscription(endpoint: str = "https://push.example.test/send/device-a") -> dict:
    client_key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return {
        "endpoint": endpoint,
        "expirationTime": None,
        "keys": {
            "p256dh": _base64url(client_key),
            "auth": _base64url(b"0123456789abcdef"),
        },
    }


async def _auth_headers(client: AsyncClient, user_id: str = "demo") -> dict[str, str]:
    if user_id == "demo":
        from config import cfg

        response = await client.post(
            "/api/auth/token",
            headers={"X-Bootstrap-Secret": cfg.JWT_BOOTSTRAP_SECRET},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
    else:
        from auth import create_token

        token = create_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def push_client(tmp_path):
    from config import cfg
    import database
    from main import app

    cfg.DB_PATH = str(tmp_path / "push.db")
    await database.init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def ready_vapid(monkeypatch):
    from config import cfg

    public_key, private_key, subject = _vapid_config()
    monkeypatch.setattr(cfg, "WEB_PUSH_ENABLED", True)
    monkeypatch.setattr(cfg, "VAPID_PUBLIC_KEY", public_key)
    monkeypatch.setattr(cfg, "VAPID_PRIVATE_KEY", private_key)
    monkeypatch.setattr(cfg, "VAPID_SUBJECT", subject)
    monkeypatch.setattr(cfg, "WEB_PUSH_ALLOWED_HOSTS", "push.example.test")
    return public_key, private_key, subject


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/push/status", None),
        ("POST", "/api/push/subscribe", _subscription()),
        ("POST", "/api/push/subscription/status", {"endpoint": _subscription()["endpoint"]}),
        ("DELETE", "/api/push/subscribe", {"endpoint": _subscription()["endpoint"]}),
        ("GET", "/api/push/preferences", None),
        ("PUT", "/api/push/preferences", {"browser_push": True}),
        ("POST", "/api/push/test", None),
    ],
)
async def test_push_routes_require_authentication(push_client, method, path, body):
    response = await push_client.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_reports_missing_configuration_without_secrets(push_client, monkeypatch):
    from config import cfg

    monkeypatch.setattr(cfg, "WEB_PUSH_ENABLED", True)
    monkeypatch.setattr(cfg, "VAPID_PUBLIC_KEY", "")
    monkeypatch.setattr(cfg, "VAPID_PRIVATE_KEY", "")
    monkeypatch.setattr(cfg, "VAPID_SUBJECT", "")
    response = await push_client.get(
        "/api/push/status", headers=await _auth_headers(push_client)
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is False
    assert set(payload["missing_configuration"]) == {
        "VAPID_PUBLIC_KEY",
        "VAPID_PRIVATE_KEY",
        "VAPID_SUBJECT",
    }
    assert "VAPID_PRIVATE_KEY" in payload["missing_configuration"]


@pytest.mark.asyncio
async def test_status_reports_environment_kill_switch(push_client, monkeypatch):
    from config import cfg

    monkeypatch.setattr(cfg, "WEB_PUSH_ENABLED", False)
    response = await push_client.get(
        "/api/push/status", headers=await _auth_headers(push_client)
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["ready"] is False


@pytest.mark.asyncio
async def test_subscribe_returns_503_when_server_not_ready(push_client, monkeypatch):
    from config import cfg

    monkeypatch.setattr(cfg, "WEB_PUSH_ENABLED", True)
    monkeypatch.setattr(cfg, "VAPID_PUBLIC_KEY", "")
    monkeypatch.setattr(cfg, "VAPID_PRIVATE_KEY", "")
    monkeypatch.setattr(cfg, "VAPID_SUBJECT", "")
    response = await push_client.post(
        "/api/push/subscribe",
        headers=await _auth_headers(push_client),
        json=_subscription(),
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_mismatched_vapid_keys_are_reported_invalid(
    push_client, ready_vapid, monkeypatch
):
    from config import cfg

    different_public_key, _, _ = _vapid_config()
    monkeypatch.setattr(cfg, "VAPID_PUBLIC_KEY", different_public_key)
    response = await push_client.get(
        "/api/push/status", headers=await _auth_headers(push_client)
    )
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert "VAPID_PUBLIC_KEY" in response.json()["invalid_configuration"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {**_subscription(), "endpoint": "http://push.example.test/device"},
        {
            **_subscription(),
            "keys": {"p256dh": "not-a-key", "auth": "not-a-secret"},
        },
        {**_subscription(), "unexpected": True},
    ],
)
async def test_subscription_payload_is_strictly_validated(push_client, ready_vapid, body):
    response = await push_client.post(
        "/api/push/subscribe",
        headers=await _auth_headers(push_client),
        json=body,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_subscription_rejects_unapproved_provider(push_client, ready_vapid):
    response = await push_client.post(
        "/api/push/subscribe",
        headers=await _auth_headers(push_client),
        json=_subscription("https://attacker.example.test/push"),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_subscribe_upserts_and_enables_browser_push(push_client, ready_vapid):
    from db.push_subscriptions import list_push_subscriptions

    headers = await _auth_headers(push_client)
    body = _subscription()
    created = await push_client.post("/api/push/subscribe", headers=headers, json=body)
    assert created.status_code == 201
    assert created.json()["created"] is True
    assert created.json()["preferences"]["browser_push"] is True

    replacement = _subscription()
    replacement["keys"]["auth"] = _base64url(b"fedcba9876543210")
    updated = await push_client.post(
        "/api/push/subscribe", headers=headers, json=replacement
    )
    assert updated.status_code == 200
    assert updated.json()["created"] is False

    records = await list_push_subscriptions("demo")
    assert len(records) == 1
    assert records[0].auth == replacement["keys"]["auth"]

    status = await push_client.get("/api/push/status", headers=headers)
    serialized = json.dumps(status.json())
    assert status.json()["subscribed"] is True
    assert status.json()["subscription_count"] == 1
    assert body["endpoint"] not in serialized
    assert ready_vapid[1] not in serialized


@pytest.mark.asyncio
async def test_per_user_subscription_limit_is_enforced(
    push_client, ready_vapid, monkeypatch
):
    from config import cfg

    monkeypatch.setattr(cfg, "WEB_PUSH_MAX_SUBSCRIPTIONS_PER_USER", 1)
    headers = await _auth_headers(push_client)
    first = await push_client.post(
        "/api/push/subscribe",
        headers=headers,
        json=_subscription("https://push.example.test/send/device-a"),
    )
    second = await push_client.post(
        "/api/push/subscribe",
        headers=headers,
        json=_subscription("https://push.example.test/send/device-b"),
    )
    assert first.status_code == 201
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_subscription_cannot_transfer_between_users(push_client, ready_vapid):
    from config import cfg

    async with aiosqlite.connect(cfg.DB_PATH) as database:
        await database.execute(
            "INSERT INTO users (id, email, password_hash, created_at, settings) "
            "VALUES (?, ?, ?, ?, ?)",
            ("user-b", "b@example.test", "unused", "2026-08-17T00:00:00+00:00", "{}"),
        )
        await database.commit()

    endpoint = _subscription()
    response_a = await push_client.post(
        "/api/push/subscribe",
        headers=await _auth_headers(push_client, "demo"),
        json=endpoint,
    )
    assert response_a.status_code == 201

    status_a = await push_client.post(
        "/api/push/subscription/status",
        headers=await _auth_headers(push_client, "demo"),
        json={"endpoint": endpoint["endpoint"]},
    )
    assert status_a.json() == {"registered": True}

    headers_b = await _auth_headers(push_client, "user-b")
    status_b = await push_client.post(
        "/api/push/subscription/status",
        headers=headers_b,
        json={"endpoint": endpoint["endpoint"]},
    )
    assert status_b.json() == {"registered": False}
    response_b = await push_client.post(
        "/api/push/subscribe", headers=headers_b, json=endpoint
    )
    assert response_b.status_code == 409
    delete_b = await push_client.request(
        "DELETE",
        "/api/push/subscribe",
        headers=headers_b,
        json={"endpoint": endpoint["endpoint"]},
    )
    assert delete_b.status_code == 404


@pytest.mark.asyncio
async def test_unsubscribe_deletes_owned_endpoint_and_disables_preference(
    push_client, ready_vapid
):
    headers = await _auth_headers(push_client)
    body = _subscription()
    await push_client.post("/api/push/subscribe", headers=headers, json=body)
    response = await push_client.request(
        "DELETE",
        "/api/push/subscribe",
        headers=headers,
        json={"endpoint": body["endpoint"]},
    )
    assert response.status_code == 200
    assert response.json() == {"subscribed": False, "subscription_count": 0}
    preferences = await push_client.get("/api/push/preferences", headers=headers)
    assert preferences.json()["browser_push"] is False


@pytest.mark.asyncio
async def test_preferences_are_validated_and_persisted(push_client):
    headers = await _auth_headers(push_client)
    response = await push_client.put(
        "/api/push/preferences",
        headers=headers,
        json={"browser_push": True, "volume": 0.25, "sound": "ding"},
    )
    assert response.status_code == 200
    assert response.json()["browser_push"] is True
    assert response.json()["volume"] == 0.25

    fetched = await push_client.get("/api/push/preferences", headers=headers)
    assert fetched.json()["sound"] == "ding"
    invalid = await push_client.put(
        "/api/push/preferences", headers=headers, json={"volume": 1.5}
    )
    assert invalid.status_code == 422
    null_value = await push_client.put(
        "/api/push/preferences", headers=headers, json={"browser_push": None}
    )
    assert null_value.status_code == 422


@pytest.mark.asyncio
async def test_test_route_sends_real_webpush_and_records_success(
    push_client, ready_vapid
):
    from db.push_subscriptions import list_push_subscriptions

    headers = await _auth_headers(push_client)
    await push_client.post(
        "/api/push/subscribe", headers=headers, json=_subscription()
    )
    provider = Mock(return_value=SimpleNamespace(status_code=201))
    with patch("push_service.webpush", provider):
        response = await push_client.post("/api/push/test", headers=headers)

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["delivered"] == 1
    assert response.json()["subscription_count"] == 1
    assert provider.call_count == 1
    records = await list_push_subscriptions("demo")
    assert records[0].last_success_at is not None
    assert records[0].failure_count == 0


@pytest.mark.asyncio
async def test_test_route_removes_expired_subscription(push_client, ready_vapid):
    from db.push_subscriptions import list_push_subscriptions

    headers = await _auth_headers(push_client)
    await push_client.post(
        "/api/push/subscribe", headers=headers, json=_subscription()
    )
    expired = WebPushException(
        "expired", response=SimpleNamespace(status_code=410)
    )
    with patch("push_service.webpush", Mock(side_effect=expired)):
        response = await push_client.post("/api/push/test", headers=headers)

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Web Push delivery failed for every registered subscription"
    )
    assert await list_push_subscriptions("demo") == []


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_and_persisted(
    push_client, ready_vapid, caplog
):
    from db.push_subscriptions import list_push_subscriptions

    headers = await _auth_headers(push_client)
    body = _subscription()
    await push_client.post("/api/push/subscribe", headers=headers, json=body)
    failure = WebPushException(
        "provider included sensitive endpoint",
        response=SimpleNamespace(status_code=500),
    )
    with patch("push_service.webpush", Mock(side_effect=failure)):
        response = await push_client.post("/api/push/test", headers=headers)

    assert response.status_code == 502
    records = await list_push_subscriptions("demo")
    assert len(records) == 1
    assert records[0].last_error == "http_500"
    assert records[0].last_failure_at is not None
    assert records[0].failure_count == 1
    assert body["endpoint"] not in caplog.text
    assert body["keys"]["auth"] not in caplog.text


@pytest.mark.asyncio
async def test_delivery_rechecks_stored_endpoint_allowlist(push_client, ready_vapid):
    from db.push_subscriptions import (
        list_push_subscriptions,
        upsert_push_subscription,
    )

    body = _subscription("https://attacker.example.test/push")
    await upsert_push_subscription(
        user_id="demo",
        endpoint=body["endpoint"],
        p256dh=body["keys"]["p256dh"],
        auth=body["keys"]["auth"],
    )
    provider = Mock()
    with patch("push_service.webpush", provider):
        response = await push_client.post(
            "/api/push/test", headers=await _auth_headers(push_client)
        )
    assert response.status_code == 502
    provider.assert_not_called()
    records = await list_push_subscriptions("demo")
    assert records[0].last_error == "endpoint_not_allowed"


@pytest.mark.asyncio
async def test_test_route_reports_no_subscription(push_client, ready_vapid):
    response = await push_client.post(
        "/api/push/test", headers=await _auth_headers(push_client)
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_alert_push_runs_after_history_and_only_when_enabled():
    import alert_engine
    from models import Alert, Condition
    from settings import NotificationPreferences

    alert = Alert(
        name="Push order",
        symbol="SPY",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=500),
        user_id="demo",
    )
    sequence: list[str] = []

    async def save_history(*args, **kwargs):
        sequence.append("history")

    async def deliver(**kwargs):
        assert sequence == ["history", "websocket"]
        sequence.append("push")

    async def emit(*args, **kwargs):
        sequence.append("websocket")

    with (
        patch.object(alert_engine.cfg, "WEB_PUSH_ENABLED", True),
        patch.object(alert_engine, "save_alert", AsyncMock()),
        patch.object(alert_engine, "save_alert_history", side_effect=save_history),
        patch.object(
            alert_engine,
            "get_notification_preferences",
            AsyncMock(return_value=NotificationPreferences(browser_push=True)),
        ),
        patch.object(alert_engine, "deliver_alert_push", side_effect=deliver) as push,
        patch.object(alert_engine, "_emit", side_effect=emit) as emit_mock,
    ):
        await alert_engine._fire_alert(alert, 510)
    assert sequence == ["history", "websocket", "push"]
    push.assert_awaited_once()
    assert emit_mock.await_args.kwargs == {"owner_user_id": "demo"}

    with (
        patch.object(alert_engine.cfg, "WEB_PUSH_ENABLED", True),
        patch.object(alert_engine, "save_alert", AsyncMock()),
        patch.object(alert_engine, "save_alert_history", AsyncMock()),
        patch.object(
            alert_engine,
            "get_notification_preferences",
            AsyncMock(return_value=NotificationPreferences(browser_push=False)),
        ),
        patch.object(alert_engine, "deliver_alert_push", AsyncMock()) as disabled_push,
        patch.object(alert_engine, "_emit", AsyncMock()),
    ):
        await alert_engine._fire_alert(alert, 511)
    disabled_push.assert_not_awaited()


@pytest.mark.asyncio
async def test_push_failure_does_not_undo_alert_or_block_websocket():
    import alert_engine
    from models import Alert, Condition
    from settings import NotificationPreferences

    alert = Alert(
        name="Provider outage",
        symbol="QQQ",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=400),
        user_id="demo",
    )
    with (
        patch.object(alert_engine.cfg, "WEB_PUSH_ENABLED", True),
        patch.object(alert_engine, "save_alert", AsyncMock()) as save_alert,
        patch.object(alert_engine, "save_alert_history", AsyncMock()) as save_history,
        patch.object(
            alert_engine,
            "get_notification_preferences",
            AsyncMock(return_value=NotificationPreferences(browser_push=True)),
        ),
        patch.object(
            alert_engine,
            "deliver_alert_push",
            AsyncMock(side_effect=RuntimeError("provider down")),
        ),
        patch.object(alert_engine, "_emit", AsyncMock()) as emit,
    ):
        await alert_engine._fire_alert(alert, 410)
    save_alert.assert_awaited_once()
    save_history.assert_awaited_once()
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_failure_does_not_block_persisted_push_fallback():
    import alert_engine
    from models import Alert, Condition
    from settings import NotificationPreferences

    alert = Alert(
        name="Socket outage",
        symbol="DIA",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=400),
        user_id="demo",
    )
    with (
        patch.object(alert_engine.cfg, "WEB_PUSH_ENABLED", True),
        patch.object(alert_engine, "save_alert", AsyncMock()) as save_alert,
        patch.object(alert_engine, "save_alert_history", AsyncMock()) as save_history,
        patch.object(
            alert_engine,
            "get_notification_preferences",
            AsyncMock(return_value=NotificationPreferences(browser_push=True)),
        ),
        patch.object(
            alert_engine,
            "_emit",
            AsyncMock(side_effect=RuntimeError("socket unavailable")),
        ),
        patch.object(alert_engine, "deliver_alert_push", AsyncMock()) as push,
    ):
        await alert_engine._fire_alert(alert, 410)

    save_alert.assert_awaited_once()
    save_history.assert_awaited_once()
    assert push.await_args.kwargs["user_id"] == "demo"


@pytest.mark.asyncio
async def test_preference_read_failure_does_not_block_websocket():
    import alert_engine
    from models import Alert, Condition

    alert = Alert(
        name="Settings outage",
        symbol="IWM",
        condition=Condition(indicator="PRICE", params={}, operator=">", value=200),
        user_id="demo",
    )
    with (
        patch.object(alert_engine.cfg, "WEB_PUSH_ENABLED", True),
        patch.object(alert_engine, "save_alert", AsyncMock()),
        patch.object(alert_engine, "save_alert_history", AsyncMock()),
        patch.object(
            alert_engine,
            "get_notification_preferences",
            AsyncMock(side_effect=RuntimeError("settings unavailable")),
        ),
        patch.object(alert_engine, "deliver_alert_push", AsyncMock()) as push,
        patch.object(alert_engine, "_emit", AsyncMock()) as emit,
    ):
        await alert_engine._fire_alert(alert, 210)
    emit.assert_awaited_once()
    push.assert_not_awaited()
