"""Anthropic Claude Messages API transport for immutable setup reviews."""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Protocol

from trading.contracts import RuleSetup, fingerprint_payload
from trading.intelligence.pricing import ModelPricing
from trading.intelligence.review_provider import AITransportResponse


DEFAULT_REVIEW_MODEL = "claude-opus-5-5"
DEFAULT_REVIEW_EFFORT = "low"
_MAX_TOKENS = 4096
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

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
    ) -> None:
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
        payload = json.dumps(
            {
                "prompt_version": prompt_version,
                "setup": setup.model_dump(mode="json"),
                "constraints": {
                    "allowed_decisions": ["PROPOSE", "ABSTAIN"],
                    "order_fields_are_immutable": True,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
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
    "build_anthropic_client",
]
