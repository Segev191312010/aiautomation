"""Broker execution port; no adapter is enabled during the manual-PAPER phase."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from trading.contracts import ReconciliationReport, TradeIntent


class BrokerBracketResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["accepted", "rejected", "manual_intervention"]
    parent_order_id: str | None = None
    stop_order_id: str | None = None
    target_order_id: str | None = None
    reason: str | None = None
    observed_at: datetime


class BrokerExecutionAdapter(Protocol):
    async def submit_bracket(self, intent: TradeIntent) -> BrokerBracketResult:
        """Submit an atomic broker-native bracket after an authority gate permits it."""

    async def reconcile(self) -> ReconciliationReport:
        """Return a typed blocking report without repairing unknown orders."""
