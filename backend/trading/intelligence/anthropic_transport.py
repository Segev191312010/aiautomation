"""Anthropic Claude Messages API transport for immutable setup reviews."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from hashlib import sha256
import json
import logging
from typing import Any, Protocol

from trading.contracts import RuleSetup, fingerprint_payload
from trading.intelligence.pricing import ModelPricing
from trading.intelligence.review_provider import AITransportResponse


logger = logging.getLogger("autotrader.shadow.review")

DEFAULT_REVIEW_MODEL = "claude-opus-5-5"
DEFAULT_REVIEW_EFFORT = "low"
_MAX_TOKENS = 4096
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Market context is advisory: a missing, slow, malformed or oversized snapshot
# is sent as {"available": false} and never fails or stalls the review. The
# byte cap keeps a producer bug from inflating every review's input tokens.
MarketContextProvider = Callable[[], Awaitable[Mapping[str, object] | None]]
MARKET_CONTEXT_TIMEOUT_SECONDS = 2.0
MAX_MARKET_CONTEXT_BYTES = 8192

CLAUDE_OPUS_5_5_PRICING = ModelPricing(
    input_usd_per_million_tokens=Decimal("4"),
    output_usd_per_million_tokens=Decimal("20"),
    cached_input_usd_per_million_tokens=Decimal("0.20"),
    cache_write_usd_per_million_tokens=Decimal("5"),
)
# A server-side refusal fallback may be served by a different model. Its cost
# is charged at the most expensive published tier so the budget never undercounts.
FALLBACK_CEILING_PRICING = ModelPricing(
    input_usd_per_million_tokens=Decimal("10"),
    output_usd_per_million_tokens=Decimal("50"),
    cached_input_usd_per_million_tokens=Decimal("1"),
    cache_write_usd_per_million_tokens=Decimal("12.5"),
)

# Numeric and length bounds are enforced by the AIReview contract after parsing,
# not here, so the schema stays within what structured outputs accept.
_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["PROPOSE", "ABSTAIN"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["decision", "confidence", "rationale"],
}


class AnthropicReviewError(RuntimeError):
    """The model returned no usable review; the provider records ABSTAIN."""


class MessagesAPI(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class AnthropicClient(Protocol):
    messages: MessagesAPI


class AnthropicMessagesTransport:
    """Call an injected official SDK client; never reads process environment."""

    def __init__(
        self,
        *,
        client: AnthropicClient,
        system_prompt: str,
        pricing: ModelPricing = CLAUDE_OPUS_5_5_PRICING,
        fallback_pricing: ModelPricing = FALLBACK_CEILING_PRICING,
        effort: str = DEFAULT_REVIEW_EFFORT,
        context_provider: MarketContextProvider | None = None,
    ) -> None:
        """``context_provider`` adds ``market_context`` to every request; without
        it the request is byte-for-byte what it was before market context existed."""
        prompt = system_prompt.strip()
        if not prompt:
            raise ValueError("review system prompt is required")
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("unsupported review effort")
        self._client = client
        self._system_prompt = prompt
        self._pricing = pricing
        self._fallback_pricing = fallback_pricing
        self._effort = effort
        self._context_provider = context_provider
        self._prompt_hash = sha256(prompt.encode("utf-8")).hexdigest()
        self._pricing_hash = fingerprint_payload(
            {
                "primary": pricing.fingerprint(),
                "fallback": fallback_pricing.fingerprint(),
            }
        )

    @property
    def prompt_hash(self) -> str:
        return self._prompt_hash

    @property
    def pricing_hash(self) -> str:
        return self._pricing_hash

    async def complete(
        self,
        *,
        setup: RuleSetup,
        model_id: str,
        prompt_version: str,
    ) -> AITransportResponse:
        request: dict[str, object] = {
            "prompt_version": prompt_version,
            "setup": setup.model_dump(mode="json"),
            "constraints": {
                "allowed_decisions": ["PROPOSE", "ABSTAIN"],
                "order_fields_are_immutable": True,
            },
        }
        if self._context_provider is not None:
            request["market_context"] = await _market_context(self._context_provider)
        payload = json.dumps(request, sort_keys=True, separators=(",", ":"))
        response = await self._client.messages.create(
            model=model_id,
            max_tokens=_MAX_TOKENS,
            system=self._system_prompt,
            messages=[{"role": "user", "content": payload}],
            output_config={
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA},
            },
            extra_headers={"anthropic-beta": _FALLBACK_BETA},
            extra_body={"fallbacks": "default"},
        )

        # Usage is billed even when the output is unusable (refusal, truncation),
        # so every response carries its cost back to the provider's budget.
        usage = getattr(response, "usage", None)
        input_tokens = _usage_int(usage, "input_tokens")
        output_tokens = _usage_int(usage, "output_tokens")
        cache_read = _usage_int(usage, "cache_read_input_tokens", default=0)
        cache_write = _usage_int(usage, "cache_creation_input_tokens", default=0)
        served_by = getattr(response, "model", None)
        pricing = self._pricing if served_by == model_id else self._fallback_pricing
        cost = pricing.cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

        stop_reason = getattr(response, "stop_reason", None)
        output_text = _first_text(response) if stop_reason == "end_turn" else None
        # A non-review mapping fails the provider's schema check, which records
        # ABSTAIN while still charging the cost above.
        output: str | dict[str, object] = (
            output_text
            if output_text is not None
            else {"incomplete_review": str(stop_reason)}
        )
        return AITransportResponse(
            output=output,
            cost_usd=cost,
            input_tokens=input_tokens + cache_read + cache_write,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            prompt_hash=self.prompt_hash,
            pricing_hash=self.pricing_hash,
        )


def build_anthropic_client(*, api_key: str, timeout_seconds: float) -> AnthropicClient:
    """Construct the official async client with an explicit key and timeout."""
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key, timeout=timeout_seconds, max_retries=1)


def _context_unavailable() -> dict[str, object]:
    return {"available": False}


async def _market_context(provider: MarketContextProvider) -> dict[str, object]:
    """The provider's snapshot as JSON-safe data, else ``{"available": false}``."""
    try:
        context = await asyncio.wait_for(provider(), timeout=MARKET_CONTEXT_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - advisory input; the review proceeds without it
        logger.warning("market context unavailable for review: %s", type(exc).__name__)
        return _context_unavailable()
    if context is None:
        return _context_unavailable()
    if not isinstance(context, Mapping):
        logger.warning(
            "market context is a %s, not a mapping; sent as unavailable", type(context).__name__
        )
        return _context_unavailable()
    try:
        encoded = json.dumps(
            dict(context), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "market context is not JSON-safe (%s); sent as unavailable", type(exc).__name__
        )
        return _context_unavailable()
    size = len(encoded.encode("utf-8"))
    if size > MAX_MARKET_CONTEXT_BYTES:
        logger.warning(
            "market context is %d bytes (limit %d); sent as unavailable",
            size,
            MAX_MARKET_CONTEXT_BYTES,
        )
        return _context_unavailable()
    decoded: dict[str, object] = json.loads(encoded)
    return decoded


def _first_text(response: object) -> str | None:
    for block in getattr(response, "content", None) or ():
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                return text
    return None


def _usage_int(value: Any, name: str, *, default: int | None = None) -> int:
    raw = getattr(value, name, None)
    if raw is None:
        raw = default
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise AnthropicReviewError(f"review response usage.{name} is invalid")
    return raw


__all__ = [
    "AnthropicMessagesTransport",
    "AnthropicReviewError",
    "CLAUDE_OPUS_5_5_PRICING",
    "DEFAULT_REVIEW_EFFORT",
    "DEFAULT_REVIEW_MODEL",
    "FALLBACK_CEILING_PRICING",
    "MARKET_CONTEXT_TIMEOUT_SECONDS",
    "MAX_MARKET_CONTEXT_BYTES",
    "MarketContextProvider",
    "build_anthropic_client",
]
