"""Pinned model pricing used to enforce the AI review budget locally."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading.contracts import fingerprint_payload


_MILLION = Decimal("1000000")


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD per million tokens; the session budget is enforced from these values."""

    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal
    cached_input_usd_per_million_tokens: Decimal = Decimal("0")
    cache_write_usd_per_million_tokens: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        values = (
            self.input_usd_per_million_tokens,
            self.output_usd_per_million_tokens,
            self.cached_input_usd_per_million_tokens,
            self.cache_write_usd_per_million_tokens,
        )
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError("AI pricing values must be finite and non-negative")

    def fingerprint(self) -> str:
        return fingerprint_payload(
            {
                "input_usd_per_million_tokens": str(self.input_usd_per_million_tokens),
                "output_usd_per_million_tokens": str(self.output_usd_per_million_tokens),
                "cached_input_usd_per_million_tokens": str(
                    self.cached_input_usd_per_million_tokens
                ),
                "cache_write_usd_per_million_tokens": str(
                    self.cache_write_usd_per_million_tokens
                ),
            }
        )

    def cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> Decimal:
        """Cost where ``input_tokens`` excludes cached reads and cache writes."""
        if min(input_tokens, output_tokens, cached_input_tokens, cache_write_tokens) < 0:
            raise ValueError("AI token counts must be non-negative")
        return (
            Decimal(input_tokens) * self.input_usd_per_million_tokens
            + Decimal(cached_input_tokens) * self.cached_input_usd_per_million_tokens
            + Decimal(cache_write_tokens) * self.cache_write_usd_per_million_tokens
            + Decimal(output_tokens) * self.output_usd_per_million_tokens
        ) / _MILLION


__all__ = ["ModelPricing"]
