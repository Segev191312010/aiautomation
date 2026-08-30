from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading.contracts import RuleSetup
from trading.intelligence.review_provider import (
    AITransportResponse,
    ReviewSessionLimits,
    StructuredAIReviewProvider,
)

HASH = "c" * 64


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
        detected_at=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
    )


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def complete(self, *, setup, model_id, prompt_version):
        self.calls.append(
            {
                "setup": setup,
                "model_id": model_id,
                "prompt_version": prompt_version,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _provider(
    transport: FakeTransport,
    *,
    limits: ReviewSessionLimits | None = None,
) -> StructuredAIReviewProvider:
    return StructuredAIReviewProvider(
        transport=transport,
        model_id="review-model-v1",
        prompt_version="review-prompt-v1",
        limits=limits,
    )


@pytest.mark.anyio
async def test_provider_returns_provenance_bound_review_without_order_authority():
    transport = FakeTransport(
        [
            AITransportResponse(
                output={"decision": "PROPOSE", "confidence": "0.8", "rationale": "valid setup"},
                cost_usd=Decimal("0.25"),
            )
        ]
    )
    setup = _setup()
    provider = _provider(transport)

    review = await provider.propose_trade(setup=setup)

    assert review.decision == "PROPOSE"
    assert review.setup_fingerprint == setup.fingerprint()
    assert review.model_id == "review-model-v1"
    assert review.prompt_version == "review-prompt-v1"
    assert review.cost_usd == Decimal("0.25")
    assert review.latency_ms >= 0
    assert len(review.response_hash) == 64
    assert not hasattr(review, "quantity")
    assert transport.calls[0]["setup"] is setup
    assert provider.usage.calls == 1
    assert provider.usage.cost_usd == Decimal("0.25")


@pytest.mark.anyio
async def test_provider_accepts_optional_confidence_but_rejects_order_fields():
    transport = FakeTransport(
        [
            AITransportResponse(
                output={"decision": "ABSTAIN", "rationale": "uncertain"},
                cost_usd=Decimal("0"),
            ),
            AITransportResponse(
                output={
                    "decision": "PROPOSE",
                    "confidence": "0.9",
                    "rationale": "buy",
                    "quantity": 10,
                },
                cost_usd=Decimal("0"),
            ),
        ]
    )
    provider = _provider(transport)

    abstained = await provider.propose_trade(setup=_setup())
    blocked = await provider.propose_trade(setup=_setup())

    assert abstained.decision == "ABSTAIN"
    assert abstained.failure_reason is None
    assert blocked.decision == "ABSTAIN"
    assert blocked.failure_reason == "invalid_response_schema"
    assert "quantity" not in blocked.rationale


@pytest.mark.anyio
async def test_provider_maps_invalid_and_unavailable_results_to_blocked_abstention():
    transport = FakeTransport(
        [
            AITransportResponse(
                output={"decision": "BUY", "rationale": "invalid"},
                cost_usd=Decimal("0"),
            ),
            RuntimeError("provider key must never reach review metadata"),
        ]
    )
    provider = _provider(transport)

    invalid = await provider.propose_trade(setup=_setup())
    unavailable = await provider.propose_trade(setup=_setup())

    assert invalid.decision == "ABSTAIN"
    assert invalid.failure_reason == "invalid_response_schema"
    assert unavailable.decision == "ABSTAIN"
    assert unavailable.failure_reason == "provider_unavailable"
    assert "provider key" not in unavailable.rationale


@pytest.mark.anyio
async def test_provider_timeout_is_bounded_and_has_no_rules_only_fallback():
    class SlowTransport:
        async def complete(self, **kwargs):
            await asyncio.sleep(0.05)
            return AITransportResponse(
                output={"decision": "PROPOSE", "rationale": "too late"},
                cost_usd=Decimal("0"),
            )

    provider = StructuredAIReviewProvider(
        transport=SlowTransport(),
        model_id="review-model-v1",
        prompt_version="review-prompt-v1",
        limits=ReviewSessionLimits(timeout_seconds=0.001),
    )

    review = await provider.propose_trade(setup=_setup())

    assert review.decision == "ABSTAIN"
    assert review.failure_reason == "provider_timeout"


@pytest.mark.anyio
async def test_provider_enforces_call_and_usd_session_limits():
    transport = FakeTransport(
        [
            AITransportResponse(
                output={"decision": "PROPOSE", "rationale": "first"},
                cost_usd=Decimal("4"),
            ),
            AITransportResponse(
                output={"decision": "PROPOSE", "rationale": "too expensive"},
                cost_usd=Decimal("2"),
            ),
            AITransportResponse(
                output={"decision": "PROPOSE", "rationale": "must not call"},
                cost_usd=Decimal("0"),
            ),
        ]
    )
    provider = _provider(
        transport,
        limits=ReviewSessionLimits(max_calls=20, max_cost_usd=Decimal("5")),
    )

    first = await provider.propose_trade(setup=_setup())
    over_budget = await provider.propose_trade(setup=_setup())
    exhausted = await provider.propose_trade(setup=_setup())

    assert first.decision == "PROPOSE"
    assert over_budget.failure_reason == "budget_exceeded"
    assert exhausted.failure_reason == "budget_exhausted"
    assert len(transport.calls) == 2

    limited_transport = FakeTransport(
        [
            AITransportResponse(
                output={"decision": "ABSTAIN", "rationale": "one"},
                cost_usd=Decimal("0"),
            ),
            AITransportResponse(
                output={"decision": "PROPOSE", "rationale": "must not call"},
                cost_usd=Decimal("0"),
            ),
        ]
    )
    limited = _provider(
        limited_transport,
        limits=ReviewSessionLimits(max_calls=1, max_cost_usd=Decimal("5")),
    )
    await limited.propose_trade(setup=_setup())
    call_limited = await limited.propose_trade(setup=_setup())
    assert call_limited.failure_reason == "call_limit_exhausted"
    assert len(limited_transport.calls) == 1
