"""Pure learning metrics for immutable closed-trade outcomes."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TradeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    strategy_version: int = Field(ge=1)
    realized_pnl: Decimal
    initial_risk_amount: Decimal = Field(gt=Decimal("0"))

    @property
    def r_multiple(self) -> Decimal:
        return self.realized_pnl / self.initial_risk_amount


class LearningMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    closed_trades: int
    win_rate: Decimal | None
    expectancy_r: Decimal | None
    total_r: Decimal


def summarize_outcomes(outcomes: list[TradeOutcome]) -> LearningMetrics:
    if not outcomes:
        return LearningMetrics(closed_trades=0, win_rate=None, expectancy_r=None, total_r=Decimal("0"))
    r_multiples = [outcome.r_multiple for outcome in outcomes]
    wins = sum(1 for result in r_multiples if result > Decimal("0"))
    total_r = sum(r_multiples, Decimal("0"))
    closed_trades = len(r_multiples)
    return LearningMetrics(
        closed_trades=closed_trades,
        win_rate=Decimal(wins) / Decimal(closed_trades),
        expectancy_r=total_r / Decimal(closed_trades),
        total_r=total_r,
    )
