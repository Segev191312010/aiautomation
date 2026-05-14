"""Shared order recovery helpers for post-submit reconciliation."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Literal

from database import update_trade_status
from market_data import finite_positive
from models import OpenPosition, Trade
from services import order_lifecycle

log = logging.getLogger(__name__)

NormalizedTradeStatus = Literal["PENDING", "FILLED", "CANCELLED", "ERROR", "UNKNOWN"]
PendingExitState = Literal["pending", "filled", "retry"]


@dataclass(slots=True)
class PendingExitResolution:
    state: PendingExitState
    reason: str | None = None
    should_cancel: bool = False


def normalize_trade_status(status: str | None) -> NormalizedTradeStatus:
    raw = str(status or "").strip().upper()
    if raw in {"FILLED"}:
        return "FILLED"
    if raw in {"CANCELLED", "APICANCELLED", "INACTIVE"}:
        return "CANCELLED"
    if raw in {"ERROR"}:
        return "ERROR"
    if raw in {"PENDING", "SUBMITTED", "PRESUBMITTED", "APIPENDING", "PENDINGSUBMIT"}:
        return "PENDING"
    return "UNKNOWN"


def is_pending_status(status: str | None) -> bool:
    return normalize_trade_status(status) == "PENDING"


def is_filled_status(status: str | None) -> bool:
    return normalize_trade_status(status) == "FILLED"


def is_cancelled_status(status: str | None) -> bool:
    return normalize_trade_status(status) == "CANCELLED"


def is_error_status(status: str | None) -> bool:
    return normalize_trade_status(status) == "ERROR"


async def reconcile_trade_status_update(
    trade_rec: Trade,
    status: str | None,
    *,
    fill_price: float | None = None,
    fill_callbacks: Iterable[Callable[[Trade], None]] | None = None,
) -> NormalizedTradeStatus:
    """Apply a broker/app status update to a trade record consistently.

    INVARIANT: a FILLED status with a non-finite fill_price is treated as the
    named outcome ``avg_fill_non_finite``. The trade is marked ERROR and
    fill_callbacks are NOT invoked. The recovery layer must NEVER infer a
    fill from position deltas or a stale ticker when the broker's
    avgFillPrice is non-finite — that would let a NaN price reach
    OpenPosition.entry_price and silently break every subsequent
    stop/target/PnL comparison.
    """
    normalized = normalize_trade_status(status)
    if normalized == "FILLED":
        clean_price = finite_positive(fill_price)
        if clean_price is None:
            log.critical(
                "avg_fill_non_finite: trade=%s symbol=%s broker_avg=%r — "
                "marking ERROR, not invoking fill_callbacks",
                trade_rec.id, trade_rec.symbol, fill_price,
            )
            await update_trade_status(trade_rec.id, "ERROR")
            trade_rec.status = "ERROR"  # type: ignore[assignment]
            return "ERROR"
        persisted = await order_lifecycle.persist_filled_trade_record(trade_rec, clean_price)
        for cb in fill_callbacks or []:
            cb(persisted)
        return normalized

    if normalized in {"CANCELLED", "ERROR"}:
        await update_trade_status(trade_rec.id, normalized)
        trade_rec.status = normalized  # type: ignore[assignment]
        return normalized

    return normalized


def evaluate_pending_exit_resolution(
    position: OpenPosition,
    trade: Trade | None,
    *,
    now: datetime,
    timeout_seconds: int,
) -> PendingExitResolution:
    """Decide what to do with a pending tracked exit based on current trade state."""
    if trade is None:
        return PendingExitResolution(state="retry", reason="Trade record not found for pending order")

    normalized = normalize_trade_status(trade.status)
    if normalized == "FILLED":
        return PendingExitResolution(state="filled")
    if normalized in {"CANCELLED", "ERROR"}:
        return PendingExitResolution(state="retry", reason=f"Exit order {normalized}")

    if position.last_exit_attempt_at:
        try:
            placed_at = datetime.fromisoformat(position.last_exit_attempt_at.replace("Z", "+00:00"))
            elapsed = (now - placed_at).total_seconds()
        except (ValueError, TypeError):
            elapsed = timeout_seconds + 1
    else:
        elapsed = timeout_seconds + 1

    if elapsed >= timeout_seconds:
        return PendingExitResolution(
            state="retry",
            reason=f"Exit order timed out after {timeout_seconds}s",
            should_cancel=True,
        )
    return PendingExitResolution(state="pending")


def mark_exit_retry_state(position: OpenPosition, reason: str, *, now: datetime) -> OpenPosition:
    position.exit_pending_order_id = None
    position.exit_attempts += 1
    position.last_exit_error = reason
    position.last_exit_attempt_at = now.isoformat()
    return position


def mark_exit_pending_submitted(position: OpenPosition, order_id: int | None, *, now: datetime) -> OpenPosition:
    position.exit_pending_order_id = order_id
    position.last_exit_attempt_at = now.isoformat()
    return position


def clear_pending_exit(position: OpenPosition) -> OpenPosition:
    position.exit_pending_order_id = None
    return position
