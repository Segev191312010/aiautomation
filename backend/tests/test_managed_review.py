"""The managed review provider: an unchanged AIReview, the {step, note} beside it.

Neutral mode is V2's reviewer, untouched. Managed mode asks Claude (a fake
client here; no test reaches the network) with the plan's context; a PROPOSE
whose step, note or order fields break the contract fails closed with its
named token, like any other review failure.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from trading.contracts import AIReview, RuleSetup
from trading.intelligence import managed_review
from trading.intelligence.managed_review import (
    AI_INVALID_NOTE,
    AI_INVALID_STEP,
    AI_ORDER_FIELD_ECHO,
    MANAGED_OUTPUT_SCHEMA,
    NEUTRAL_VERDICT,
    ClaudeManagedReviewer,
    ManagedReviewContext,
    ManagedReviewProvider,
    SetupVerdict,
    blocked_review,
    split_verdict,
)
from trading.intelligence.prompts import (
    MOMO_MANAGED_REVIEW_PROMPT_ID,
    MOMO_MANAGED_REVIEW_V1,
    MOMO_REVIEW_V2,
    REVIEW_PROMPT_STRATEGY_KINDS,
    review_prompt_for,
)
from trading.intelligence.review_provider import (
    ReviewSessionLimits,
    ReviewSessionUsage,
    StructuredAIReviewProvider,
)
from trading.manager import constants

MODEL = "claude-opus-5-5"
CONTEXT = ManagedReviewContext(
    plan_id="20261013-0946-1", regime="trend_up", strategy="momo", day_step=Decimal("0.75"),
    focus_note="new highs on volume",
)


def _setup(symbol: str = "BF B") -> RuleSetup:
    return RuleSetup(
        run_id="run-1",
        candidate_id=f"cand-{symbol}",
        strategy_id="strategy-1",
        strategy_version=1,
        strategy_kind="momo_hod",
        symbol=symbol,
        atr="0.0792",
        entry_limit="26.77",
        stop_price="26.51",
        target_price="27.29",
        bar_fingerprints=("a" * 64,),
        quote_fingerprint="b" * 64,
        detected_at=datetime(2026, 10, 12, 15, 50, 5, tzinfo=timezone.utc),
    )


class _FakeMessages:
    def __init__(self, *texts: str | None, delay: float = 0.0) -> None:
        self.texts = list(texts)
        self.calls: list[dict] = []
        self.delay = delay

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        text = self.texts.pop(0)
        return SimpleNamespace(
            model=MODEL,
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)] if text is not None else [],
            usage=SimpleNamespace(input_tokens=1500, output_tokens=300, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
        )


def _reviewer(messages: _FakeMessages, *, timeout: float = 5.0) -> ClaudeManagedReviewer:
    return ClaudeManagedReviewer(
        client=SimpleNamespace(messages=messages),
        model_id=MODEL,
        prompt_id=MOMO_MANAGED_REVIEW_PROMPT_ID,
        system_prompt=MOMO_MANAGED_REVIEW_V1,
        limits=ReviewSessionLimits(timeout_seconds=timeout),
        session_id="run-1",
        context_provider=None,
    )


def _answer(**overrides: object) -> str:
    answer = {"decision": "PROPOSE", "confidence": 0.7, "rationale": "Clean break.", "step": 0.5,
              "note": "late in the move"}
    answer.update(overrides)
    return json.dumps(answer)


class _Neutral:
    """V2's reviewer stand-in: a fixed review."""

    def __init__(self) -> None:
        self.calls = 0
        self.usage = ReviewSessionUsage(calls=0, cost_usd=Decimal("0"))

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        self.calls += 1
        return AIReview(
            setup_fingerprint=setup.fingerprint(), decision="PROPOSE", confidence=Decimal("0.6"),
            rationale="V2 review.", model_id=MODEL, prompt_version="momo-review-v2",
            response_hash="e" * 64, latency_ms=5, cost_usd=Decimal("0.05"),
        )


def _provider(managed, *, neutral=None, context=CONTEXT, limits=None, verdicts=None):
    sink = verdicts if verdicts is not None else []

    async def on_verdict(setup, ctx, verdict):
        sink.append((setup.symbol, ctx, verdict))

    def context_for(_setup):
        if isinstance(context, Exception):
            raise context
        return context

    return ManagedReviewProvider(
        neutral=neutral or _Neutral(),
        managed=managed,
        context_for=context_for,
        on_verdict=on_verdict,
        limits=limits or ReviewSessionLimits(),
        neutral_model_id=MODEL,
        neutral_prompt_id="momo-review-v2",
        managed_model_id=MODEL,
        managed_prompt_id=MOMO_MANAGED_REVIEW_PROMPT_ID,
    )


# --- the prompt and the mirrored constants ------------------------------------------------


def test_the_managed_prompt_is_v2s_review_plus_the_managers_context():
    v2_body = MOMO_REVIEW_V2.rsplit("You cannot change any price", 1)[0]
    assert MOMO_MANAGED_REVIEW_V1.startswith(v2_body)
    assert "manager_context" in MOMO_MANAGED_REVIEW_V1 and "step" in MOMO_MANAGED_REVIEW_V1
    assert REVIEW_PROMPT_STRATEGY_KINDS[MOMO_MANAGED_REVIEW_PROMPT_ID] == "momo_hod"
    assert review_prompt_for(MOMO_MANAGED_REVIEW_PROMPT_ID, strategy_kind="momo_hod") == MOMO_MANAGED_REVIEW_V1
    with pytest.raises(ValueError):
        review_prompt_for(MOMO_MANAGED_REVIEW_PROMPT_ID, strategy_kind="orb")


def test_the_intelligence_layer_mirrors_the_managers_constants():
    assert managed_review.STEPS == constants.DAY_STEPS
    assert managed_review.NOTE_MAX == constants.NOTE_MAX
    assert managed_review._ORDER_KEYS == constants.ORDER_FIELD_KEYS
    assert (AI_INVALID_STEP, AI_INVALID_NOTE, AI_ORDER_FIELD_ECHO) == (
        constants.AI_INVALID_STEP, constants.AI_INVALID_NOTE, constants.AI_ORDER_FIELD_ECHO,
    )
    assert MANAGED_OUTPUT_SCHEMA["properties"]["step"]["enum"] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert MANAGED_OUTPUT_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"step": 0.5, "note": "late"}, SetupVerdict(step=Decimal("0.5"), note="late")),
        ({"step": 1, "note": ""}, SetupVerdict(step=Decimal("1"), note=None)),
        ({"step": "0.75"}, SetupVerdict(step=Decimal("0.75"))),
        ({"step": 2.0, "note": "x"}, SetupVerdict(step=Decimal("0"), token=AI_INVALID_STEP)),
        ({"step": 0.3}, SetupVerdict(step=Decimal("0"), token=AI_INVALID_STEP)),
        ({"note": "no step"}, SetupVerdict(step=Decimal("0"), token=AI_INVALID_STEP)),
        ({"step": True}, SetupVerdict(step=Decimal("0"), token=AI_INVALID_STEP)),
        ({"step": 1, "note": "a\nb"}, SetupVerdict(step=Decimal("0"), token=AI_INVALID_NOTE)),
        ({"step": 1, "note": "x" * 141}, SetupVerdict(step=Decimal("0"), token=AI_INVALID_NOTE)),
        ({"step": 1, "quantity": 500}, SetupVerdict(step=Decimal("0"), token=AI_ORDER_FIELD_ECHO)),
        ({"step": 1, "Entry_Price": 27}, SetupVerdict(step=Decimal("0"), token=AI_ORDER_FIELD_ECHO)),
    ],
)
def test_the_verdict_is_split_and_validated(answer, expected):
    assert split_verdict(answer) == expected


# --- managed mode with a fake Claude ---------------------------------------------------------


@pytest.mark.anyio
async def test_a_managed_review_keeps_the_aireview_contract_and_records_the_verdict():
    messages = _FakeMessages(_answer())
    verdicts: list = []
    provider = _provider(_reviewer(messages), verdicts=verdicts)

    review = await provider.propose_trade(setup=_setup())

    assert review.decision == "PROPOSE" and review.prompt_version == MOMO_MANAGED_REVIEW_PROMPT_ID
    assert review.failure_reason is None and review.confidence == Decimal("0.7")
    # The AIReview contract is V2's: no step or note inside it.
    assert set(review.model_dump()) == set(AIReview.model_fields)
    assert verdicts == [("BF B", CONTEXT, SetupVerdict(step=Decimal("0.5"), note="late in the move"))]
    [call] = messages.calls
    request = json.loads(call["messages"][0]["content"])
    assert request["manager_context"] == {
        "plan_id": "20261013-0946-1", "regime": "trend_up", "strategy": "momo", "day_step": 0.75,
        "focus_note": "new highs on volume",
    }
    assert call["output_config"]["format"]["schema"] == MANAGED_OUTPUT_SCHEMA
    assert call["system"] == MOMO_MANAGED_REVIEW_V1
    assert call["extra_body"] == {"fallbacks": "default"}


@pytest.mark.parametrize(
    ("text", "failure"),
    [
        (_answer(step=2.0), AI_INVALID_STEP),
        (_answer(step=0.3), AI_INVALID_STEP),
        (json.dumps({"decision": "PROPOSE", "confidence": 0.7, "rationale": "x"}), AI_INVALID_STEP),
        (_answer(note="line one\nline two"), AI_INVALID_NOTE),
        (_answer(quantity=5000), AI_ORDER_FIELD_ECHO),
        (_answer(stop_price=25.0), AI_ORDER_FIELD_ECHO),
        ("not json at all", "invalid_response_schema"),
        (json.dumps(["PROPOSE"]), "invalid_response_schema"),
        (None, "invalid_response_schema"),
    ],
    ids=["step-2.0", "step-0.3", "no-step", "two-line-note", "echoed-quantity", "echoed-stop",
         "malformed", "not-an-object", "empty"],
)
@pytest.mark.anyio
async def test_adversarial_managed_answers_fail_closed_with_their_named_token(text, failure):
    verdicts: list = []
    provider = _provider(_reviewer(_FakeMessages(text)), verdicts=verdicts)

    review = await provider.propose_trade(setup=_setup())

    assert (review.decision, review.failure_reason) == ("ABSTAIN", failure)
    # The cost of the call is kept on the failed review.
    if failure in {AI_INVALID_STEP, AI_INVALID_NOTE, AI_ORDER_FIELD_ECHO}:
        assert review.cost_usd > 0
    # No failed review leaves a size step behind it.
    assert all(not verdict.valid or verdict.step == 0 for _symbol, _ctx, verdict in verdicts)


@pytest.mark.anyio
async def test_a_managed_review_that_times_out_abstains():
    provider = _provider(_reviewer(_FakeMessages(_answer(), delay=5.0), timeout=0.05))

    review = await provider.propose_trade(setup=_setup())

    assert (review.decision, review.failure_reason) == ("ABSTAIN", "provider_timeout")


@pytest.mark.anyio
async def test_an_abstain_with_a_bad_step_stays_a_plain_abstain():
    provider = _provider(_reviewer(_FakeMessages(_answer(decision="ABSTAIN", step=2.0))))

    review = await provider.propose_trade(setup=_setup())

    assert review.decision == "ABSTAIN" and review.failure_reason is None


# --- neutral mode ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_neutral_mode_returns_v2s_review_unchanged():
    neutral = _Neutral()
    messages = _FakeMessages()
    verdicts: list = []
    provider = _provider(_reviewer(messages), neutral=neutral, context=None, verdicts=verdicts)

    review = await provider.propose_trade(setup=_setup())

    assert review == await _Neutral().propose_trade(setup=_setup())
    assert messages.calls == [] and neutral.calls == 1
    assert verdicts == [("BF B", None, NEUTRAL_VERDICT)]


@pytest.mark.anyio
async def test_a_failing_plan_context_is_neutral_never_an_abstain():
    neutral = _Neutral()
    provider = _provider(_reviewer(_FakeMessages()), neutral=neutral, context=RuntimeError("no plan"))

    review = await provider.propose_trade(setup=_setup())

    assert review.decision == "PROPOSE" and review.prompt_version == "momo-review-v2"


@pytest.mark.anyio
async def test_both_reviewers_share_one_per_run_limit():
    limits = ReviewSessionLimits(max_calls=2)
    reviewer = _reviewer(_FakeMessages(_answer(), _answer()))
    contexts = [CONTEXT, CONTEXT, None]
    neutral = _Neutral()

    def context_for(_setup):
        return contexts.pop(0)

    async def on_verdict(*_args):
        return None

    provider = ManagedReviewProvider(
        neutral=neutral, managed=reviewer, context_for=context_for, on_verdict=on_verdict,
        limits=limits, neutral_model_id=MODEL, neutral_prompt_id="momo-review-v2",
        managed_model_id=MODEL, managed_prompt_id=MOMO_MANAGED_REVIEW_PROMPT_ID,
    )
    await provider.propose_trade(setup=_setup("BF B"))
    await provider.propose_trade(setup=_setup("FIGS"))
    third = await provider.propose_trade(setup=_setup("TSLA"))

    assert (third.decision, third.failure_reason) == ("ABSTAIN", "call_limit_exhausted")
    assert third.prompt_version == "momo-review-v2" and neutral.calls == 0
    # Shaped exactly like V2's own provider's limit failure.
    v2 = StructuredAIReviewProvider(transport=None, model_id=MODEL, prompt_version="momo-review-v2")  # type: ignore[arg-type]
    assert third == v2._blocked_review(setup=_setup("TSLA"), reason="call_limit_exhausted")
    assert blocked_review(setup=_setup("TSLA"), reason="x", model_id=MODEL, prompt_version="p") == (
        StructuredAIReviewProvider(transport=None, model_id=MODEL, prompt_version="p")._blocked_review(  # type: ignore[arg-type]
            setup=_setup("TSLA"), reason="x"
        )
    )
