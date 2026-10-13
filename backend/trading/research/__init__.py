"""Strategy research: real five-minute bars, deterministic replay, R statistics.

``recorder`` fills the private bar store from TWS PAPER (read-only data
client), ``replay`` walks stored sessions through a strategy without
lookahead and simulates each first setup as a conservative bracket,
``stats`` summarizes the outcomes in R, and ``report_notify`` sends the
weekly summary to the owner's Telegram. The supervisor runs the recording
and the replay after every session close. Nothing here can place an order.
"""
from __future__ import annotations

from typing import Any

__all__ = ["run_backfill_cli", "run_replay_cli"]


def __getattr__(name: str) -> Any:
    # Lazy: the recorder loads the TWS actor stack, which replay never needs.
    if name == "run_backfill_cli":
        from trading.research.recorder import run_backfill_cli

        return run_backfill_cli
    if name == "run_replay_cli":
        from trading.research.replay import run_replay_cli

        return run_replay_cli
    raise AttributeError(f"module 'trading.research' has no attribute {name!r}")
