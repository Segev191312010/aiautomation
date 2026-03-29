"""Shared trade outcome extraction — single source of truth for P&L reading."""
from __future__ import annotations

from models import Trade


def get_trade_realized_pnl(trade: Trade) -> float | None:
    """Extract realized P&L, preferring canonical field over legacy metadata."""
    if trade.realized_pnl is not None:
        return trade.realized_pnl
    if trade.metadata and "pnl" in trade.metadata:
        try:
            return float(trade.metadata["pnl"])
        except (ValueError, TypeError):
            return None
    return None


def is_closed_canonical(trade: Trade) -> bool:
    """True if this trade has a canonical closed outcome from finalize_trade_outcome."""
    return (
        trade.status == "FILLED"
        and trade.closed_at is not None
        and trade.realized_pnl is not None
        and trade.outcome_quality == "canonical"
    )


def is_closed_any(trade: Trade) -> bool:
    """True if this trade has any closed outcome (canonical or legacy metadata)."""
    return (
        trade.status == "FILLED"
        and (
            (trade.closed_at is not None and trade.realized_pnl is not None)
            or bool(trade.metadata and "pnl" in trade.metadata)
        )
    )
