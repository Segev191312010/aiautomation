"""Server-side Web Push readiness and delivery."""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from config import cfg
from db.push_subscriptions import (
    PushSubscriptionRecord,
    delete_push_subscription_by_id,
    list_push_subscriptions,
    record_push_failure,
    record_push_success,
)
from settings import get_notification_preferences
from metrics import record_web_push_delivery, set_web_push_readiness

log = logging.getLogger(__name__)
_BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


@dataclass(frozen=True)
class PushReadiness:
    enabled: bool
    ready: bool
    public_key: str | None
    missing: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()
    vapid: Vapid | None = field(default=None, repr=False, compare=False)

    def api_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "public_key": self.public_key,
            "missing_configuration": list(self.missing),
            "invalid_configuration": list(self.invalid),
        }


@dataclass
class PushDeliveryResult:
    subscription_count: int = 0
    delivered: int = 0
    expired_removed: int = 0
    failed: int = 0
    skipped_preference: bool = False

    def api_payload(self) -> dict[str, object]:
        return {
            "subscription_count": self.subscription_count,
            "delivered": self.delivered,
            "expired_removed": self.expired_removed,
            "failed": self.failed,
            "skipped_preference": self.skipped_preference,
        }


class PushNotReadyError(RuntimeError):
    pass


def _decode_public_key(value: str) -> bytes:
    if not _BASE64URL_PATTERN.fullmatch(value):
        raise ValueError("invalid base64url")
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _encode_public_key(vapid: Vapid) -> str:
    raw = vapid.public_key.public_bytes(
        encoding=Encoding.X962,
        format=PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _load_private_key(value: str) -> Vapid:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parent / candidate
    if candidate.is_file():
        return Vapid.from_file(private_key_file=str(candidate))
    if value.lstrip().startswith("-----BEGIN"):
        return Vapid.from_pem(value.encode("ascii"))
    return Vapid.from_string(private_key=value)


def _valid_subject(subject: str) -> bool:
    if subject.startswith("mailto:"):
        address = subject.removeprefix("mailto:")
        return "@" in address and not address.startswith("@") and not address.endswith("@")
    parsed = urlparse(subject)
    return parsed.scheme == "https" and bool(parsed.netloc)


def allowed_push_hosts() -> tuple[str, ...]:
    configured = str(getattr(cfg, "WEB_PUSH_ALLOWED_HOSTS", ""))
    return tuple(
        host.strip().lower().removeprefix("*.")
        for host in configured.split(",")
        if host.strip()
    )


def is_allowed_push_endpoint(endpoint: str) -> bool:
    hostname = (urlparse(endpoint).hostname or "").lower().rstrip(".")
    return any(
        hostname == allowed or hostname.endswith(f".{allowed}")
        for allowed in allowed_push_hosts()
    )


def get_push_readiness() -> PushReadiness:
    enabled = bool(getattr(cfg, "WEB_PUSH_ENABLED", False))
    values = {
        "VAPID_PUBLIC_KEY": str(getattr(cfg, "VAPID_PUBLIC_KEY", "")).strip(),
        "VAPID_PRIVATE_KEY": str(getattr(cfg, "VAPID_PRIVATE_KEY", "")).strip(),
        "VAPID_SUBJECT": str(getattr(cfg, "VAPID_SUBJECT", "")).strip(),
    }
    missing = tuple(name for name, value in values.items() if not value)
    invalid: list[str] = []
    public_key: str | None = None
    vapid: Vapid | None = None

    if values["VAPID_PUBLIC_KEY"]:
        try:
            decoded = _decode_public_key(values["VAPID_PUBLIC_KEY"])
            if len(decoded) != 65 or decoded[0] != 4:
                raise ValueError
            public_key = values["VAPID_PUBLIC_KEY"].rstrip("=")
        except (ValueError, UnicodeError):
            invalid.append("VAPID_PUBLIC_KEY")

    if values["VAPID_PRIVATE_KEY"]:
        try:
            vapid = _load_private_key(values["VAPID_PRIVATE_KEY"])
        except (OSError, TypeError, ValueError):
            invalid.append("VAPID_PRIVATE_KEY")

    if values["VAPID_SUBJECT"] and not _valid_subject(values["VAPID_SUBJECT"]):
        invalid.append("VAPID_SUBJECT")
    if not allowed_push_hosts():
        invalid.append("WEB_PUSH_ALLOWED_HOSTS")

    if vapid is not None and public_key is not None:
        derived_public_key = _encode_public_key(vapid)
        if not hmac.compare_digest(derived_public_key, public_key):
            invalid.append("VAPID_PUBLIC_KEY")

    invalid_tuple = tuple(dict.fromkeys(invalid))
    readiness = PushReadiness(
        enabled=enabled,
        ready=enabled and not missing and not invalid_tuple,
        public_key=public_key,
        missing=missing,
        invalid=invalid_tuple,
        vapid=vapid,
    )
    set_web_push_readiness(enabled=readiness.enabled, ready=readiness.ready)
    return readiness


async def _record_failure(subscription_id: str, error_code: str) -> None:
    try:
        await record_push_failure(subscription_id, error_code)
    except Exception:
        log.error("Failed to record Web Push failure metadata for subscription %s", subscription_id)


async def _deliver_one(
    subscription: PushSubscriptionRecord,
    *,
    payload_json: str,
    readiness: PushReadiness,
) -> str:
    if not is_allowed_push_endpoint(subscription.endpoint):
        record_web_push_delivery("blocked")
        await _record_failure(subscription.id, "endpoint_not_allowed")
        log.warning("Blocked disallowed Web Push endpoint for subscription %s", subscription.id)
        return "failed"
    try:
        await asyncio.to_thread(
            webpush,
            subscription_info=subscription.subscription_info(),
            data=payload_json,
            vapid_private_key=readiness.vapid,
            vapid_claims={"sub": str(cfg.VAPID_SUBJECT).strip()},
            ttl=300,
            timeout=10,
        )
    except WebPushException as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code in (404, 410):
            record_web_push_delivery("expired")
            await delete_push_subscription_by_id(subscription.id)
            log.info("Removed expired Web Push subscription %s", subscription.id)
            return "expired"
        error_code = f"http_{status_code}" if status_code else "webpush_error"
        record_web_push_delivery("failed")
        await _record_failure(subscription.id, error_code)
        log.warning(
            "Web Push delivery failed for subscription %s (%s)",
            subscription.id,
            error_code,
        )
        return "failed"
    except Exception:
        record_web_push_delivery("failed")
        await _record_failure(subscription.id, "delivery_error")
        log.warning("Web Push delivery failed for subscription %s", subscription.id)
        return "failed"

    try:
        await record_push_success(subscription.id)
    except Exception:
        log.error("Failed to record Web Push success metadata for subscription %s", subscription.id)
    record_web_push_delivery("delivered")
    return "delivered"


async def deliver_push(
    *,
    user_id: str,
    payload: dict[str, object],
    respect_preference: bool = True,
) -> PushDeliveryResult:
    readiness = get_push_readiness()
    if not readiness.ready:
        record_web_push_delivery("disabled" if not readiness.enabled else "not_ready")
        raise PushNotReadyError

    if respect_preference:
        preferences = await get_notification_preferences(user_id)
        if not preferences.browser_push:
            record_web_push_delivery("preference_skipped")
            return PushDeliveryResult(skipped_preference=True)

    subscriptions = await list_push_subscriptions(user_id)
    result = PushDeliveryResult(subscription_count=len(subscriptions))
    if not subscriptions:
        record_web_push_delivery("no_subscription")
        return result

    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    outcomes = await asyncio.gather(
        *(
            _deliver_one(
                subscription,
                payload_json=payload_json,
                readiness=readiness,
            )
            for subscription in subscriptions
        )
    )
    result.delivered = outcomes.count("delivered")
    result.expired_removed = outcomes.count("expired")
    result.failed = outcomes.count("failed")
    return result


async def deliver_alert_push(
    *,
    user_id: str,
    alert_id: str,
    name: str,
    symbol: str,
    condition_summary: str,
    price: float,
    timestamp: str,
) -> PushDeliveryResult:
    return await deliver_push(
        user_id=user_id,
        payload={
            "type": "alert_fired",
            "title": f"Alert: {name}",
            "body": f"{symbol} at {price:.2f} — {condition_summary}",
            "icon": "/favicon.ico",
            "tag": f"alert:{alert_id}",
            "data": {
                "alert_id": alert_id,
                "symbol": symbol,
                "price": price,
                "timestamp": timestamp,
            },
        },
        respect_preference=True,
    )
