"""The managed review provider for managed MOMO (AP_MOMO_HOD_V1, shadow).

It returns an unchanged ``AIReview`` (the contract and its fingerprint are
V2's), and keeps the manager's extra answer, ``{step, note}``, beside it:

* neutral mode (no valid plan, the switch off, any plan failure): the setup
  goes to V2's own reviewer, prompt ``momo-review-v2``, and the verdict is
  step 1 with no note; the review is exactly V2's;
* managed mode: the setup goes to the managed reviewer (prompt
  ``momo-managed-review-v1``) with the plan's context; its answer is split
  into the AIReview fields and the verdict. A PROPOSE whose step is outside
  the fixed set, whose note is not one short line, or that echoes an order
  field fails closed: the review becomes an ABSTAIN with the named token
  (``ai_invalid_step``, ``ai_invalid_note``, ``ai_order_field_echo``), like
  any other review failure.

The per-run review limits are unchanged: both reviewers count toward one
20-call / $5 limit per run. Nothing here sizes, orders or reads an account.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import logging
from typing import Any, Protocol

from trading.contracts import AIReview, RuleSetup, fingerprint_payload
from trading.intelligence.anthropic_transport import (
    CLAUDE_OPUS_5_5_PRICING,
    DEFAULT_REVIEW_EFFORT,
    FALLBACK_CEILING_PRICING,
    MarketContextProvider,
    _first_text,
    _market_context,
    _usage_int,
)
from trading.intelligence.pricing import ModelPricing
from trading.intelligence.provider import TradeProposalProvider
from trading.intelligence.review_provider import (
    AITransportResponse,
    ReviewSessionLimits,
    ReviewSessionUsage,
    StructuredAIReviewProvider,
)

logger = logging.getLogger("autotrader.manager.review")

# Mirrors trading.manager.constants (a test pins them equal); kept literal so
# the intelligence layer never imports the manager.
STEPS: tuple[Decimal, ...] = (
    Decimal("0"),
    Decimal("0.25"),
    Decimal("0.5"),
    Decimal("0.75"),
    Decimal("1"),
)
NEUTRAL_STEP = Decimal("1")
NOTE_MAX = 140
AI_INVALID_STEP = "ai_invalid_step"
AI_INVALID_NOTE = "ai_invalid_note"
AI_ORDER_FIELD_ECHO = "ai_order_field_echo"
_REVIEW_KEYS = ("decision", "confidence", "rationale")
_VERDICT_KEYS = ("step", "note")
_ORDER_KEYS = frozenset(
    {
        "quantity", "qty", "shares", "size", "position_size", "notional", "price", "limit",
        "limit_price", "entry", "entry_limit", "entry_price", "stop", "stop_price", "target",
        "target_price", "side", "order", "order_type", "risk",
    }
)
_MAX_TOKENS = 4096
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
MANAGED_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["PROPOSE", "ABSTAIN"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "step": {"type": "number", "enum": [float(step) for step in STEPS]},
        "note": {"type": "string"},
    },
    "required": ["decision", "confidence", "rationale", "step", "note"],
}


@dataclass(frozen=True, slots=True)
class ManagedReviewContext:
    """What the plan says about one setup; ``None`` in its place means neutral mode."""

    plan_id: str
    regime: str
    strategy: str
    day_step: Decimal
    focus_note: str | None

    def payload(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "regime": self.regime,
            "strategy": self.strategy,
            "day_step": float(self.day_step),
            "focus_note": self.focus_note,
        }


@dataclass(frozen=True, slots=True)
class SetupVerdict:
    """The managed reviewer's size step and note for one setup (``token`` = refused)."""

    step: Decimal
    note: str | None = None
    token: str | None = None

    @property
    def valid(self) -> bool:
        return self.token is None


NEUTRAL_VERDICT = SetupVerdict(step=NEUTRAL_STEP)


def split_verdict(answer: Mapping[str, Any]) -> SetupVerdict:
    """Validate the managed reviewer's ``step`` and ``note`` (pure)."""
    echoed = sorted(str(key) for key in answer if str(key).strip().lower() in _ORDER_KEYS)
    if echoed:
        logger.critical(
            "manager_widening_attempt: the managed reviewer echoed order fields %s", ",".join(echoed)
        )
        return SetupVerdict(step=Decimal("0"), token=AI_ORDER_FIELD_ECHO)
    raw_step = answer.get("step")
    step: Decimal | None = None
    if not isinstance(raw_step, bool) and isinstance(raw_step, (int, float, str)):
        try:
            candidate = Decimal(str(raw_step))
        except (InvalidOperation, ValueError):
            candidate = None
        if candidate is not None and candidate.is_finite():
            step = next((allowed for allowed in STEPS if allowed == candidate), None)
    if step is None:
        logger.critical(
            "manager_widening_attempt: the managed reviewer's step %r is not a fixed step",
            str(raw_step)[:32],
        )
        return SetupVerdict(step=Decimal("0"), token=AI_INVALID_STEP)
    note = answer.get("note")
    if isinstance(note, str) and not note.strip():
        note = None  # the schema asks for a note; an empty one means none
    if note is not None and (
        not isinstance(note, str)
        or not 1 <= len(note) <= NOTE_MAX
        or "\n" in note
        or "\r" in note
    ):
        return SetupVerdict(step=Decimal("0"), token=AI_INVALID_NOTE)
    return SetupVerdict(step=step, note=note or None)


class _ManagedTransport:
    """Claude call for one managed review; answers only the review fields upward.

    The full answer is split here: the review fields go to the structured
    provider (which keeps its schema, limit and failure handling), the
    verdict is kept by setup fingerprint for the managed provider.
    """

    def __init__(
        self,
        *,
        client: Any,
        system_prompt: str,
        context_provider: MarketContextProvider | None,
        pricing: ModelPricing = CLAUDE_OPUS_5_5_PRICING,
        fallback_pricing: ModelPricing = FALLBACK_CEILING_PRICING,
        effort: str = DEFAULT_REVIEW_EFFORT,
    ) -> None:
        prompt = system_prompt.strip()
        if not prompt:
            raise ValueError("managed review system prompt is required")
        self._client = client
        self._system_prompt = prompt
        self._context_provider = context_provider
        self._pricing = pricing
        self._fallback_pricing = fallback_pricing
        self._effort = effort
        self.prompt_hash = sha256(prompt.encode("utf-8")).hexdigest()
        self.pricing_hash = fingerprint_payload(
            {"primary": pricing.fingerprint(), "fallback": fallback_pricing.fingerprint()}
        )
        self._contexts: dict[str, ManagedReviewContext] = {}
        self._verdicts: dict[str, SetupVerdict] = {}

    def set_context(self, setup_fingerprint: str, context: ManagedReviewContext) -> None:
        self._contexts[setup_fingerprint] = context

    def take_verdict(self, setup_fingerprint: str) -> SetupVerdict | None:
        self._contexts.pop(setup_fingerprint, None)
        return self._verdicts.pop(setup_fingerprint, None)

    async def complete(
        self, *, setup: RuleSetup, model_id: str, prompt_version: str
    ) -> AITransportResponse:
        fingerprint = setup.fingerprint()
        context = self._contexts.get(fingerprint)
        request: dict[str, object] = {
            "prompt_version": prompt_version,
            "setup": setup.model_dump(mode="json"),
            "constraints": {
                "allowed_decisions": ["PROPOSE", "ABSTAIN"],
                "order_fields_are_immutable": True,
                "steps": [float(step) for step in STEPS],
            },
            "manager_context": context.payload() if context is not None else {"available": False},
        }
        if self._context_provider is not None:
            request["market_context"] = await _market_context(self._context_provider)
        response = await self._client.messages.create(
            model=model_id,
            max_tokens=_MAX_TOKENS,
            system=self._system_prompt,
            messages=[{"role": "user", "content": json.dumps(request, sort_keys=True, separators=(",", ":"))}],
            output_config={
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": MANAGED_OUTPUT_SCHEMA},
            },
            extra_headers={"anthropic-beta": _FALLBACK_BETA},
            extra_body={"fallbacks": "default"},
        )
        usage = getattr(response, "usage", None)
        input_tokens = _usage_int(usage, "input_tokens")
        output_tokens = _usage_int(usage, "output_tokens")
        cache_read = _usage_int(usage, "cache_read_input_tokens", default=0)
        cache_write = _usage_int(usage, "cache_creation_input_tokens", default=0)
        pricing = self._pricing if getattr(response, "model", None) == model_id else self._fallback_pricing
        cost = pricing.cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        stop_reason = getattr(response, "stop_reason", None)
        text = _first_text(response) if stop_reason == "end_turn" else None
        output: str | dict[str, object] = text if text is not None else {"incomplete_review": str(stop_reason)}
        if text is not None:
            output = self._split(fingerprint, text)
        return AITransportResponse(
            output=output,
            cost_usd=cost,
            input_tokens=input_tokens + cache_read + cache_write,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            prompt_hash=self.prompt_hash,
            pricing_hash=self.pricing_hash,
        )

    def _split(self, fingerprint: str, text: str) -> str:
        """Keep the verdict; hand the review fields (only) to the structured provider."""
        try:
            answer = json.loads(text)
        except json.JSONDecodeError:
            return text  # the provider refuses it: invalid_response_schema
        if not isinstance(answer, dict):
            return text
        self._verdicts[fingerprint] = split_verdict(
            {key: value for key, value in answer.items() if key not in _REVIEW_KEYS}
        )
        review = {key: answer[key] for key in _REVIEW_KEYS if key in answer}
        return json.dumps(review, sort_keys=True, separators=(",", ":"))


class ManagedReviewer(Protocol):
    @property
    def usage(self) -> ReviewSessionUsage: ...

    async def review(
        self, *, setup: RuleSetup, context: ManagedReviewContext
    ) -> tuple[AIReview, SetupVerdict]: ...


class ClaudeManagedReviewer:
    """The managed prompt behind V2's structured provider (same limits, same failures)."""

    def __init__(
        self,
        *,
        client: Any,
        model_id: str,
        prompt_id: str,
        system_prompt: str,
        limits: ReviewSessionLimits,
        session_id: str,
        context_provider: MarketContextProvider | None,
    ) -> None:
        self._transport = _ManagedTransport(
            client=client, system_prompt=system_prompt, context_provider=context_provider
        )
        self._provider = StructuredAIReviewProvider(
            transport=self._transport,
            model_id=model_id,
            prompt_version=prompt_id,
            limits=limits,
            session_id=session_id,
        )

    @property
    def usage(self) -> ReviewSessionUsage:
        return self._provider.usage

    async def review(
        self, *, setup: RuleSetup, context: ManagedReviewContext
    ) -> tuple[AIReview, SetupVerdict]:
        fingerprint = setup.fingerprint()
        self._transport.set_context(fingerprint, context)
        review = await self._provider.propose_trade(setup=setup)
        verdict = self._transport.take_verdict(fingerprint)
        if verdict is None:
            # No usable answer reached the split (timeout, refusal, budget):
            # the review itself already failed closed.
            verdict = SetupVerdict(step=Decimal("0"), token=None if review.failure_reason else AI_INVALID_STEP)
        return review, verdict


ContextFor = Callable[[RuleSetup], ManagedReviewContext | None]
VerdictSink = Callable[[RuleSetup, ManagedReviewContext | None, SetupVerdict], Awaitable[None]]


def blocked_review(
    *,
    setup: RuleSetup,
    reason: str,
    model_id: str,
    prompt_version: str,
    latency_ms: int = 0,
    cost_usd: Decimal = Decimal("0"),
    response_hash: str | None = None,
) -> AIReview:
    """An ABSTAIN shaped exactly like StructuredAIReviewProvider's own failures."""
    if response_hash is None:
        serialized = json.dumps(
            {"failure": reason}, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True
        )
        response_hash = sha256(serialized.encode("utf-8")).hexdigest()
    return AIReview(
        setup_fingerprint=setup.fingerprint(),
        decision="ABSTAIN",
        confidence=None,
        rationale=f"AI review blocked: {reason}.",
        model_id=model_id,
        prompt_version=prompt_version,
        response_hash=response_hash,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        failure_reason=reason,
    )


class ManagedReviewProvider:
    """A ``TradeProposalProvider`` for managed MOMO: neutral = V2, managed = plan-aware."""

    def __init__(
        self,
        *,
        neutral: TradeProposalProvider,
        managed: ManagedReviewer,
        context_for: ContextFor,
        on_verdict: VerdictSink,
        limits: ReviewSessionLimits,
        neutral_model_id: str,
        neutral_prompt_id: str,
        managed_model_id: str,
        managed_prompt_id: str,
    ) -> None:
        self._neutral = neutral
        self._managed = managed
        self._context_for = context_for
        self._on_verdict = on_verdict
        self._limits = limits
        self._ids = {
            "neutral": (neutral_model_id, neutral_prompt_id),
            "managed": (managed_model_id, managed_prompt_id),
        }
        self._lock = asyncio.Lock()

    @property
    def usage(self) -> ReviewSessionUsage:
        neutral = _usage_of(self._neutral)
        managed = self._managed.usage
        return ReviewSessionUsage(calls=neutral.calls + managed.calls, cost_usd=neutral.cost_usd + managed.cost_usd)

    def _exhausted(self) -> str | None:
        # Only a run that used both reviewers needs the shared count: a run that
        # stayed neutral is limited by V2's own provider, exactly as V2 is.
        if self._managed.usage.calls == 0:
            return None
        usage = self.usage
        if usage.calls >= self._limits.max_calls:
            return "call_limit_exhausted"
        if usage.cost_usd >= self._limits.max_cost_usd:
            return "budget_exhausted"
        return None

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        async with self._lock:
            try:
                context = self._context_for(setup)
            except Exception:  # noqa: BLE001 - a plan failure is neutral mode (Principle 3)
                logger.critical("managed review context failed; neutral review", exc_info=True)
                context = None
            mode = "neutral" if context is None else "managed"
            exhausted = self._exhausted()
            if exhausted is not None:
                model_id, prompt_id = self._ids[mode]
                review = blocked_review(setup=setup, reason=exhausted, model_id=model_id, prompt_version=prompt_id)
                verdict = NEUTRAL_VERDICT if context is None else SetupVerdict(step=Decimal("0"))
            elif context is None:
                review = await self._neutral.propose_trade(setup=setup)
                verdict = NEUTRAL_VERDICT
            else:
                review, verdict = await self._managed.review(setup=setup, context=context)
                if review.decision == "PROPOSE" and verdict.token is not None:
                    review = blocked_review(
                        setup=setup,
                        reason=verdict.token,
                        model_id=review.model_id,
                        prompt_version=review.prompt_version,
                        latency_ms=review.latency_ms,
                        cost_usd=review.cost_usd,
                        response_hash=review.response_hash,
                    )
            try:
                await self._on_verdict(setup, context, verdict)
            except Exception:  # noqa: BLE001 - evidence never changes a review
                logger.error("managed verdict not recorded for %s", setup.symbol, exc_info=True)
            return review


def _usage_of(provider: object) -> ReviewSessionUsage:
    usage = getattr(provider, "usage", None)
    if isinstance(usage, ReviewSessionUsage):
        return usage
    return ReviewSessionUsage(calls=0, cost_usd=Decimal("0"))


__all__ = [
    "AI_INVALID_NOTE",
    "AI_INVALID_STEP",
    "AI_ORDER_FIELD_ECHO",
    "ClaudeManagedReviewer",
    "MANAGED_OUTPUT_SCHEMA",
    "ManagedReviewContext",
    "ManagedReviewProvider",
    "ManagedReviewer",
    "NEUTRAL_VERDICT",
    "SetupVerdict",
    "blocked_review",
    "split_verdict",
]
