"""Claude review transport: request shape, cost accounting, and fail-to-ABSTAIN."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import subprocess
from types import SimpleNamespace

import pytest

from trading.contracts import RuleSetup
from trading.intelligence.anthropic_transport import (
    CLAUDE_OPUS_5_5_PRICING,
    FALLBACK_CEILING_PRICING,
    AnthropicMessagesTransport,
    AnthropicReviewError,
)
from trading.intelligence.pricing import ModelPricing
from trading.intelligence.prompts import ORB_REVIEW_V1, review_prompt
from trading.intelligence.review_provider import StructuredAIReviewProvider
from trading.operations.keychain import (
    ANTHROPIC_KEYCHAIN_ACCOUNT,
    ANTHROPIC_KEYCHAIN_SERVICE,
    MacOSKeychainReader,
    read_anthropic_api_key,
)

HASH = "a" * 64
MODEL = "claude-opus-5-5"


def _setup() -> RuleSetup:
    return RuleSetup(
        run_id="run-1",
        candidate_id="candidate-1",
        strategy_id="strategy-1",
        strategy_version=1,
        symbol="AAPL",
        opening_range_high="100",
        opening_range_low="98",
        atr="1",
        entry_limit="100.10",
        stop_price="99.10",
        target_price="102.10",
        bar_fingerprints=(HASH,),
        quote_fingerprint=HASH,
        detected_at=datetime.now(timezone.utc),
    )


def _response(
    *,
    text: str | None = None,
    stop_reason: str = "end_turn",
    model: str = MODEL,
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_read: int | None = None,
) -> SimpleNamespace:
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=None,
        ),
    )


class FakeMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


def _transport(response: object) -> tuple[AnthropicMessagesTransport, FakeMessages]:
    messages = FakeMessages(response)
    client = SimpleNamespace(messages=messages)
    return AnthropicMessagesTransport(client=client, system_prompt=ORB_REVIEW_V1), messages


def _provider(transport: AnthropicMessagesTransport) -> StructuredAIReviewProvider:
    return StructuredAIReviewProvider(
        transport=transport, model_id=MODEL, prompt_version="orb-review-v1"
    )


@pytest.mark.asyncio
async def test_request_is_schema_constrained_with_fallback_and_immutable_setup():
    body = json.dumps({"decision": "PROPOSE", "confidence": 0.7, "rationale": "Clean break."})
    transport, messages = _transport(_response(text=body))

    await transport.complete(setup=_setup(), model_id=MODEL, prompt_version="orb-review-v1")

    call = messages.calls[0]
    assert call["model"] == MODEL
    assert call["system"] == ORB_REVIEW_V1
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    schema = call["output_config"]["format"]["schema"]
    assert schema["properties"]["decision"]["enum"] == ["PROPOSE", "ABSTAIN"]
    assert schema["additionalProperties"] is False
    assert call["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}
    assert call["extra_body"] == {"fallbacks": "default"}
    payload = json.loads(call["messages"][0]["content"])
    assert payload["constraints"]["order_fields_are_immutable"] is True
    assert payload["setup"]["entry_limit"] == "100.10"
    # Without a context provider (ORB) the request carries no market context.
    assert set(payload) == {"constraints", "prompt_version", "setup"}


@pytest.mark.asyncio
async def test_valid_review_is_accepted_and_costed_at_primary_pricing():
    body = json.dumps({"decision": "PROPOSE", "confidence": 0.7, "rationale": "Clean break."})
    transport, _ = _transport(_response(text=body, cache_read=500))

    review = await _provider(transport).propose_trade(setup=_setup())

    assert review.decision == "PROPOSE"
    assert review.failure_reason is None
    expected = CLAUDE_OPUS_5_5_PRICING.cost(
        input_tokens=1000, output_tokens=200, cached_input_tokens=500
    )
    assert review.cost_usd == expected


@pytest.mark.asyncio
async def test_fallback_served_response_is_costed_at_ceiling_pricing():
    body = json.dumps({"decision": "ABSTAIN", "confidence": 0.4, "rationale": "Wide stop."})
    transport, _ = _transport(_response(text=body, model="claude-opus-4-8"))

    response = await transport.complete(setup=_setup(), model_id=MODEL, prompt_version="v1")

    assert response.cost_usd == FALLBACK_CEILING_PRICING.cost(input_tokens=1000, output_tokens=200)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_incomplete_review_abstains_but_still_charges_budget(stop_reason):
    transport, _ = _transport(_response(text='{"decision": "PROP', stop_reason=stop_reason))
    provider = _provider(transport)

    review = await provider.propose_trade(setup=_setup())

    assert review.decision == "ABSTAIN"
    assert review.failure_reason == "invalid_response_schema"
    assert provider.usage.cost_usd == CLAUDE_OPUS_5_5_PRICING.cost(
        input_tokens=1000, output_tokens=200
    )


@pytest.mark.asyncio
async def test_out_of_range_confidence_is_rejected_by_contract():
    body = json.dumps({"decision": "PROPOSE", "confidence": 7, "rationale": "Sure."})
    transport, _ = _transport(_response(text=body))

    review = await _provider(transport).propose_trade(setup=_setup())

    assert review.decision == "ABSTAIN"


@pytest.mark.asyncio
async def test_invalid_usage_becomes_provider_unavailable():
    response = _response(text="{}")
    response.usage.output_tokens = -1
    transport, _ = _transport(response)

    with pytest.raises(AnthropicReviewError):
        await transport.complete(setup=_setup(), model_id=MODEL, prompt_version="v1")
    review = await _provider(transport).propose_trade(setup=_setup())
    assert review.failure_reason == "provider_unavailable"


def test_transport_requires_prompt_and_known_effort():
    client = SimpleNamespace(messages=FakeMessages(None))
    with pytest.raises(ValueError):
        AnthropicMessagesTransport(client=client, system_prompt="  ")
    with pytest.raises(ValueError):
        AnthropicMessagesTransport(client=client, system_prompt="x", effort="turbo")


def test_prompt_registry_rejects_unknown_ids():
    assert review_prompt("orb-review-v1") == ORB_REVIEW_V1
    with pytest.raises(ValueError):
        review_prompt("orb-review-v0")


def test_orb_review_v1_text_never_changes():
    # Recorded ORB reviews name orb-review-v1; ORB reviews carry no market context.
    assert (
        sha256(ORB_REVIEW_V1.encode("utf-8")).hexdigest()
        == "7776a8930f7ee0ad043d45835545e9ef03d446780286b19a68047286304aebc4"
    )
    assert "market_context" not in ORB_REVIEW_V1


def test_pricing_counts_cache_reads_and_writes_separately():
    pricing = ModelPricing(
        input_usd_per_million_tokens=Decimal("4"),
        output_usd_per_million_tokens=Decimal("20"),
        cached_input_usd_per_million_tokens=Decimal("0.2"),
        cache_write_usd_per_million_tokens=Decimal("5"),
    )
    cost = pricing.cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cached_input_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert cost == Decimal("29.2")
    with pytest.raises(ValueError):
        pricing.cost(input_tokens=-1, output_tokens=0)


def test_anthropic_key_is_read_from_its_keychain_item_without_echo():
    commands: list[tuple[str, ...]] = []

    def runner(command):
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout="sk-test-value\n", stderr="")

    key = read_anthropic_api_key(MacOSKeychainReader(command_runner=runner))

    assert key == "sk-test-value"
    assert ANTHROPIC_KEYCHAIN_SERVICE in commands[0]
    assert ANTHROPIC_KEYCHAIN_ACCOUNT in commands[0]
