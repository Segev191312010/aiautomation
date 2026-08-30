"""Conservative, no-lookahead bracket simulation for long stock entries."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading.contracts import TradeIntent


class OHLCBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime
    symbol: str
    open: Decimal = Field(gt=Decimal("0"))
    high: Decimal = Field(gt=Decimal("0"))
    low: Decimal = Field(gt=Decimal("0"))
    close: Decimal = Field(gt=Decimal("0"))

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_range(self) -> "OHLCBar":
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC bar range does not contain open and close")
        return self


class BacktestTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["unfilled", "open", "closed"]
    entry_at: datetime | None = None
    entry_price: Decimal | None = None
    exit_at: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: Literal["stop", "target"] | None = None
    realized_pnl: Decimal | None = None


def _validate_ordered_bars(*, bars: list[OHLCBar], symbol: str) -> list[OHLCBar]:
    relevant = [bar for bar in bars if bar.symbol == symbol]
    if relevant != sorted(relevant, key=lambda bar: bar.timestamp):
        raise ValueError("bars must be sorted by timestamp")
    if len({bar.timestamp for bar in relevant}) != len(relevant):
        raise ValueError("bars must not contain duplicate timestamps")
    return relevant


def simulate_bracket_trade(
    *,
    signal_at: datetime,
    intent: TradeIntent,
    bars: list[OHLCBar],
) -> BacktestTrade:
    relevant = _validate_ordered_bars(bars=bars, symbol=intent.symbol)
    entry_index: int | None = None
    for index, bar in enumerate(relevant):
        if bar.timestamp <= signal_at:
            continue
        if bar.low <= intent.entry_limit <= bar.high:
            entry_index = index
            break
    if entry_index is None:
        return BacktestTrade(status="unfilled")

    entry_bar = relevant[entry_index]
    for bar in relevant[entry_index + 1 :]:
        stop_hit = bar.low <= intent.stop_price
        target_hit = intent.target_price is not None and bar.high >= intent.target_price
        if stop_hit:
            exit_price = min(intent.stop_price, bar.open)
            return BacktestTrade(
                status="closed",
                entry_at=entry_bar.timestamp,
                entry_price=intent.entry_limit,
                exit_at=bar.timestamp,
                exit_price=exit_price,
                exit_reason="stop",
                realized_pnl=(exit_price - intent.entry_limit) * intent.quantity,
            )
        if target_hit and intent.target_price is not None:
            return BacktestTrade(
                status="closed",
                entry_at=entry_bar.timestamp,
                entry_price=intent.entry_limit,
                exit_at=bar.timestamp,
                exit_price=intent.target_price,
                exit_reason="target",
                realized_pnl=(intent.target_price - intent.entry_limit) * intent.quantity,
            )
    return BacktestTrade(
        status="open",
        entry_at=entry_bar.timestamp,
        entry_price=intent.entry_limit,
    )
