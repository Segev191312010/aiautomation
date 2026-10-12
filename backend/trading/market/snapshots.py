"""Point-in-time session statistics from one short-lived TWS streaming request."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class MarketSnapshotError(ValueError):
    """A market snapshot violates its admission invariants."""


_PRICE_FIELDS = ("prior_close", "last", "bid", "ask", "open", "day_high", "day_low")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketSnapshot:
    """Last/bid/ask plus session statistics for one contract.

    ``volume_raw`` is exactly what TWS reported; under ibapi 9.81 US-stock
    volume arrives in round lots, so callers convert it with the verified
    multiplier instead of this type guessing a unit.
    """

    con_id: int
    observed_at: datetime
    prior_close: Decimal
    last: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    open: Decimal | None = None
    day_high: Decimal | None = None
    day_low: Decimal | None = None
    volume_raw: Decimal | None = None

    def __post_init__(self) -> None:
        if isinstance(self.con_id, bool) or not isinstance(self.con_id, int) or self.con_id <= 0:
            raise MarketSnapshotError("con_id must be a positive integer")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise MarketSnapshotError("observed_at must be timezone-aware")
        for name in _PRICE_FIELDS:
            value = getattr(self, name)
            if value is None:
                if name == "prior_close":
                    raise MarketSnapshotError("prior_close is required")
                continue
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise MarketSnapshotError(f"{name} must be a finite positive Decimal")
        volume = self.volume_raw
        if volume is not None and (
            not isinstance(volume, Decimal) or not volume.is_finite() or volume < 0
        ):
            raise MarketSnapshotError("volume_raw must be a finite non-negative Decimal")
        # A row without any tradable price cannot be ranked against its close.
        if self.reference_price is None:
            raise MarketSnapshotError("a snapshot needs a last trade or both quote sides")

    @property
    def reference_price(self) -> Decimal | None:
        """The last trade, else the bid/ask midpoint, else unknown."""
        if self.last is not None:
            return self.last
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return None


__all__ = ["MarketSnapshot", "MarketSnapshotError"]
