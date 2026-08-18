"""Authenticated browser Web Push subscription and preference routes."""
from __future__ import annotations

import base64
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from auth import get_current_user
from config import cfg
from db.push_subscriptions import (
    PushSubscriptionLimitError,
    PushSubscriptionOwnershipError,
    delete_push_subscription,
    list_push_subscriptions,
    upsert_push_subscription,
)
from push_service import (
    PushNotReadyError,
    deliver_push,
    get_push_readiness,
    is_allowed_push_endpoint,
)
from settings import (
    NotificationPreferences,
    NotificationPreferencesUpdate,
    get_notification_preferences,
    update_notification_preferences,
)

router = APIRouter(prefix="/api/push", tags=["push"])
_BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def _decode_base64url(value: str) -> bytes:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


class PushSubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=80, max_length=128)
    auth: str = Field(min_length=20, max_length=128)

    @field_validator("p256dh")
    @classmethod
    def validate_p256dh(cls, value: str) -> str:
        if not _BASE64URL_PATTERN.fullmatch(value):
            raise PydanticCustomError("push_key", "p256dh must be base64url encoded")
        try:
            decoded = _decode_base64url(value)
        except (ValueError, UnicodeError):
            raise PydanticCustomError("push_key", "p256dh must be base64url encoded") from None
        if len(decoded) != 65 or decoded[0] != 4:
            raise PydanticCustomError(
                "push_key",
                "p256dh must contain an uncompressed P-256 public key",
            )
        return value.rstrip("=")

    @field_validator("auth")
    @classmethod
    def validate_auth_secret(cls, value: str) -> str:
        if not _BASE64URL_PATTERN.fullmatch(value):
            raise PydanticCustomError("push_auth", "auth must be base64url encoded")
        try:
            decoded = _decode_base64url(value)
        except (ValueError, UnicodeError):
            raise PydanticCustomError("push_auth", "auth must be base64url encoded") from None
        if len(decoded) < 16:
            raise PydanticCustomError("push_auth", "auth must contain at least 16 bytes")
        return value.rstrip("=")


class PushSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    endpoint: str = Field(min_length=12, max_length=4096)
    expiration_time: float | None = Field(default=None, alias="expirationTime")
    keys: PushSubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise PydanticCustomError(
                "push_endpoint", "push endpoint must be an HTTPS URL"
            )
        if parsed.username or parsed.password or parsed.fragment:
            raise PydanticCustomError(
                "push_endpoint", "push endpoint contains unsupported URL components"
            )
        return value


class PushUnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=12, max_length=4096)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise PydanticCustomError(
                "push_endpoint", "push endpoint must be an HTTPS URL"
            )
        return value


def _require_push_ready() -> None:
    if not get_push_readiness().ready:
        raise HTTPException(
            status_code=503,
            detail="Web Push is not ready; inspect /api/push/status",
        )


@router.get("/status")
async def get_push_status(user=Depends(get_current_user)):
    readiness = get_push_readiness()
    subscriptions = await list_push_subscriptions(user.id)
    preferences = await get_notification_preferences(user.id)
    return {
        **readiness.api_payload(),
        "subscribed": bool(subscriptions),
        "subscription_count": len(subscriptions),
        "preferences": preferences.model_dump(),
    }


@router.post("/subscribe")
async def subscribe_push(
    body: PushSubscriptionRequest,
    response: Response,
    user=Depends(get_current_user),
):
    _require_push_ready()
    if not is_allowed_push_endpoint(body.endpoint):
        raise HTTPException(422, "Push endpoint provider is not allowed")
    try:
        _, created = await upsert_push_subscription(
            user_id=user.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            max_subscriptions=cfg.WEB_PUSH_MAX_SUBSCRIPTIONS_PER_USER,
        )
    except PushSubscriptionOwnershipError:
        raise HTTPException(409, "Push subscription cannot be registered") from None
    except PushSubscriptionLimitError:
        raise HTTPException(409, "Push subscription limit reached") from None

    preferences = await update_notification_preferences(
        user.id,
        NotificationPreferencesUpdate(browser_push=True),
    )
    subscriptions = await list_push_subscriptions(user.id)
    response.status_code = 201 if created else 200
    return {
        "subscribed": True,
        "created": created,
        "subscription_count": len(subscriptions),
        "preferences": preferences.model_dump(),
    }


@router.delete("/subscribe")
async def unsubscribe_push(
    body: PushUnsubscribeRequest,
    user=Depends(get_current_user),
):
    deleted = await delete_push_subscription(user_id=user.id, endpoint=body.endpoint)
    if not deleted:
        raise HTTPException(404, "Push subscription not found")

    subscriptions = await list_push_subscriptions(user.id)
    if not subscriptions:
        await update_notification_preferences(
            user.id,
            NotificationPreferencesUpdate(browser_push=False),
        )
    return {"subscribed": bool(subscriptions), "subscription_count": len(subscriptions)}


@router.get("/preferences", response_model=NotificationPreferences)
async def get_push_preferences(user=Depends(get_current_user)):
    return await get_notification_preferences(user.id)


@router.put("/preferences", response_model=NotificationPreferences)
async def put_push_preferences(
    body: NotificationPreferencesUpdate,
    user=Depends(get_current_user),
):
    return await update_notification_preferences(user.id, body)


@router.post("/test")
async def test_push_delivery(user=Depends(get_current_user)):
    _require_push_ready()
    try:
        result = await deliver_push(
            user_id=user.id,
            payload={
                "type": "push_test",
                "title": "Trading Dashboard",
                "body": "Browser notifications are connected.",
                "icon": "/favicon.ico",
                "tag": "push-test",
                "data": {"test": True},
            },
            respect_preference=False,
        )
    except PushNotReadyError:
        raise HTTPException(503, "Web Push is not ready") from None

    if result.subscription_count == 0:
        raise HTTPException(409, "No browser PushSubscription is registered")
    if result.delivered == 0:
        raise HTTPException(
            status_code=502,
            detail="Web Push delivery failed for every registered subscription",
        )
    return {"success": True, **result.api_payload()}
