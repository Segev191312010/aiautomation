"""Official OpenAI Responses API transport for immutable shadow reviews."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Protocol

from trading.contracts import RuleSetup, fingerprint_payload
from trading.intelligence.review_provider import AITransportResponse


_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["PROPOSE", "ABSTAIN"]},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 2000},
    },
    "required": ["decision", "confidence", "rationale"],
}


class ResponsesAPI(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class OpenAIClient(Protocol):
    responses: ResponsesAPI


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Pinned USD pricing used to enforce the session budget locally."""

    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal
    cached_input_usd_per_million_tokens: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        values = (
            self.input_usd_per_million_tokens,
            self.output_usd_per_million_tokens,
            self.cached_input_usd_per_million_tokens,
        )
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError("OpenAI pricing values must be finite and non-negative")

    def fingerprint(self) -> str:
        return fingerprint_payload(
            {
                "input_usd_per_million_tokens": str(self.input_usd_per_million_tokens),
                "output_usd_per_million_tokens": str(self.output_usd_per_million_tokens),
                "cached_input_usd_per_million_tokens": str(
                    self.cached_input_usd_per_million_tokens
                ),
            }
        )

    def cost(
        self, *, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
    ) -> Decimal:
        if min(input_tokens, output_tokens, cached_input_tokens) < 0:
            raise ValueError("OpenAI token counts must be non-negative")
        uncached_input = max(0, input_tokens - cached_input_tokens)
        million = Decimal("1000000")
        return (
            Decimal(uncached_input) * self.input_usd_per_million_tokens
            + Decimal(cached_input_tokens) * self.cached_input_usd_per_million_tokens
            + Decimal(output_tokens) * self.output_usd_per_million_tokens
        ) / million


class OpenAIResponsesTransport:
    """Call an injected official SDK client without reading process environment."""

    def __init__(
        self,
        *,
        client: OpenAIClient,
        system_prompt: str,
        pricing: ModelPricing,
    ) -> None:
        prompt = system_prompt.strip()
        if not prompt:
            raise ValueError("OpenAI review system prompt is required")
        self._client = client
        self._system_prompt = prompt
        self._pricing = pricing
        self._prompt_hash = sha256(prompt.encode("utf-8")).hexdigest()

    @property
    def prompt_hash(self) -> str:
        return self._prompt_hash

    @property
    def pricing_hash(self) -> str:
        return self._pricing.fingerprint()

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
        response = await self._client.responses.create(
            model=model_id,
            input=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": payload},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "autotrader_shadow_ai_review",
                    "strict": True,
                    "schema": _OUTPUT_SCHEMA,
                }
            },
            store=False,
        )
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError("OpenAI response did not contain output_text")
        usage = getattr(response, "usage", None)
        input_tokens = _usage_int(usage, "input_tokens")
        output_tokens = _usage_int(usage, "output_tokens")
        input_details = getattr(usage, "input_tokens_details", None)
        cached_input_tokens = _usage_int(input_details, "cached_tokens", default=0)
        return AITransportResponse(
            output=output_text,
            cost_usd=self._pricing.cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            prompt_hash=self.prompt_hash,
            pricing_hash=self.pricing_hash,
        )


def _usage_int(value: Any, name: str, *, default: int | None = None) -> int:
    raw = getattr(value, name, default)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"OpenAI response usage.{name} is invalid")
    return raw


__all__ = ["ModelPricing", "OpenAIResponsesTransport"]
