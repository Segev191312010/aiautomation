"""Bounded AI review provider with no order or broker authority.

Reviews of one strategy run may run concurrently (owner, 2026-10-23, plan v2
item 3b: the session runner keeps at most ``REVIEW_POOL_SIZE`` in flight).
The run's cap is counted atomically across them:

* a call is reserved under the provider's lock before it starts: the call
  count and the in-flight count rise in one step, so ``max_calls`` is never
  exceeded, however many reviews arrive at once;
* each call in flight holds ``call_cost_reserve_usd`` of the budget until its
  real cost replaces it, in one synchronous step (no await between them): a
  call starts only while the spent cost plus every reservation, its own
  included, stays within ``max_cost_usd``. The cap is never exceeded unless a
  single call costs more than its reservation; such a call is logged, its
  cost is counted, and a review that took the total over the cap is an
  ABSTAIN (``budget_exceeded``), exactly as before;
* every call's cost is added exactly once, and its reservation is released
  exactly once, also when the review is cancelled;
* a timeout, a 429 (the SDK's ``RateLimitError``) or any other provider error
  is an ABSTAIN with no cost, never a retry and never rules-only approval.

Nothing here orders or sizes; the AI only proposes or abstains.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import time
from typing import Protocol

from pydantic import ValidationError

from trading.contracts import AIReview, RuleSetup

logger = logging.getLogger(__name__)
_REVIEW_FIELDS = frozenset({"decision", "confidence", "rationale"})
_REQUIRED_REVIEW_FIELDS = frozenset({"decision", "rationale"})
_MAX_RESPONSE_CHARS = 10000


@dataclass(frozen=True, slots=True)
class AITransportResponse:
    output: str | Mapping[str, object]
    cost_usd: Decimal
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    prompt_hash: str | None = None
    pricing_hash: str | None = None


class AIReviewTransport(Protocol):
    async def complete(
        self,
        *,
        setup: RuleSetup,
        model_id: str,
        prompt_version: str,
    ) -> AITransportResponse:
        """Return an untrusted structured review for the immutable setup."""


# At most this many AI reviews of one strategy run are in flight at once (owner,
# 2026-10-23, plan v2 item 3b; the session runner's pool).
REVIEW_POOL_SIZE = 4
# The budget one call holds while in flight. Above the dearest review the
# transport can bill: at most 4096 output tokens at the fallback ceiling's
# $50/M ($0.20) plus the prompt and setup at its $10/M input (well under
# $0.10 at the live ~5k tokens); live reviews cost about $0.02-0.05.
REVIEW_CALL_COST_RESERVE_USD = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class ReviewSessionLimits:
    timeout_seconds: float = 10.0
    max_calls: int = 20
    max_cost_usd: Decimal = Decimal("5")
    call_cost_reserve_usd: Decimal = REVIEW_CALL_COST_RESERVE_USD

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_calls < 1:
            raise ValueError("max_calls must be positive")
        if not self.max_cost_usd.is_finite() or self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be finite and non-negative")
        if not self.call_cost_reserve_usd.is_finite() or self.call_cost_reserve_usd < 0:
            raise ValueError("call_cost_reserve_usd must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ReviewSessionUsage:
    calls: int
    cost_usd: Decimal


class AIReviewPersistencePort(Protocol):
    """Durable hooks implemented by the storage owner for one shadow session."""

    async def load_usage(self, *, run_id: str) -> ReviewSessionUsage: ...

    async def persist_usage(self, *, run_id: str, usage: ReviewSessionUsage) -> None: ...

    async def persist_review(
        self,
        *,
        run_id: str,
        candidate_id: str,
        review: AIReview,
        prompt_hash: str,
        pricing_hash: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
    ) -> None: ...


class StructuredAIReviewProvider:
    """Convert untrusted model output into bounded ``AIReview`` records."""

    def __init__(
        self,
        *,
        transport: AIReviewTransport,
        model_id: str,
        prompt_version: str,
        limits: ReviewSessionLimits | None = None,
        session_id: str | None = None,
        persistence: AIReviewPersistencePort | None = None,
        prompt_hash: str | None = None,
        pricing_hash: str | None = None,
    ) -> None:
        if not model_id or not model_id.strip():
            raise ValueError("model_id is required")
        if not prompt_version or not prompt_version.strip():
            raise ValueError("prompt_version is required")
        if persistence is not None and (session_id is None or not session_id.strip()):
            raise ValueError("durable AI usage requires a shadow session_id")
        self._transport = transport
        self._model_id = model_id
        self._prompt_version = prompt_version
        self._limits = limits or ReviewSessionLimits()
        self._calls = 0
        self._cost_usd = Decimal("0")
        # Calls reserved and not yet settled (each holds call_cost_reserve_usd).
        self._in_flight = 0
        self._lock = asyncio.Lock()
        self._session_id = session_id
        self._persistence = persistence
        self._usage_loaded = False
        self._prompt_hash = prompt_hash or getattr(transport, "prompt_hash", None)
        self._pricing_hash = pricing_hash or getattr(transport, "pricing_hash", None)
        for name, digest in (("prompt_hash", self._prompt_hash), ("pricing_hash", self._pricing_hash)):
            if digest is not None and not self._is_digest(digest):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")

    @property
    def usage(self) -> ReviewSessionUsage:
        return ReviewSessionUsage(calls=self._calls, cost_usd=self._cost_usd)

    @property
    def in_flight(self) -> int:
        """Calls reserved and not yet settled."""
        return self._in_flight

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        """Review a setup; failures abstain and never become rules-only approval.

        Safe to call concurrently: the cap is reserved atomically before the
        call and settled after it (see the module docstring).
        """
        refused = await self._reserve(setup)
        if refused is not None:
            review = self._blocked_review(setup=setup, reason=refused)
            await self._persist_review(setup=setup, review=review, response=None)
            return review
        settled = False
        try:
            started = time.monotonic()
            try:
                # asyncio.timeout, not wait_for: on Python 3.11 wait_for can drop
                # a cancel that lands as the call finishes (LEARNED.md).
                async with asyncio.timeout(self._limits.timeout_seconds):
                    response = await self._transport.complete(
                        setup=setup,
                        model_id=self._model_id,
                        prompt_version=self._prompt_version,
                    )
            except TimeoutError:
                logger.warning("AI review timed out")
                review = self._blocked_review(setup=setup, reason="provider_timeout")
                await self._persist_review(setup=setup, review=review, response=None)
                return review
            except Exception as exc:  # noqa: BLE001 - a 429 under load included: ABSTAIN, no retry
                logger.warning("AI review provider unavailable (%s)", type(exc).__name__)
                review = self._blocked_review(setup=setup, reason="provider_unavailable")
                await self._persist_review(setup=setup, review=review, response=None)
                return review

            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            cost = self._response_cost(response)
            if cost is None:
                review = self._blocked_review(
                    setup=setup,
                    reason="invalid_provider_response",
                    latency_ms=latency_ms,
                )
                await self._persist_review(setup=setup, review=review, response=None)
                return review
            # The call's real cost replaces its reservation in one synchronous
            # step: counted exactly once, never lost to a cancellation below.
            self._cost_usd += cost
            self._in_flight -= 1
            settled = True
            over_budget = self._cost_usd > self._limits.max_cost_usd
            if cost > self._limits.call_cost_reserve_usd:
                logger.warning(
                    "AI review cost %s is above its reservation %s", cost, self._limits.call_cost_reserve_usd
                )
            async with self._lock:
                await self._persist_usage(setup)
            if over_budget:
                review = self._blocked_review(
                    setup=setup,
                    reason="budget_exceeded",
                    latency_ms=latency_ms,
                    cost_usd=cost,
                )
                await self._persist_review(setup=setup, review=review, response=response)
                return review

            payload = self._parse_output(response.output)
            if (
                payload is None
                or not _REQUIRED_REVIEW_FIELDS.issubset(payload)
                or not set(payload).issubset(_REVIEW_FIELDS)
            ):
                review = self._blocked_review(
                    setup=setup,
                    reason="invalid_response_schema",
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    response_hash=self._hash_output(response.output),
                )
                await self._persist_review(setup=setup, review=review, response=response)
                return review
            try:
                review = AIReview(
                    setup_fingerprint=setup.fingerprint(),
                    decision=payload["decision"],
                    confidence=payload.get("confidence"),
                    rationale=payload["rationale"],
                    model_id=self._model_id,
                    prompt_version=self._prompt_version,
                    response_hash=self._hash_output(response.output),
                    latency_ms=latency_ms,
                    cost_usd=cost,
                )
            except (ValidationError, TypeError, ValueError):
                review = self._blocked_review(
                    setup=setup,
                    reason="invalid_response_schema",
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    response_hash=self._hash_output(response.output),
                )
                await self._persist_review(setup=setup, review=review, response=response)
                return review
            await self._persist_review(setup=setup, review=review, response=response)
            return review
        finally:
            if not settled:
                # No cost known (timeout, error, cancellation): the reservation
                # is released, synchronously, exactly once.
                self._in_flight -= 1

    async def _reserve(self, setup: RuleSetup) -> str | None:
        """Reserve one call atomically; the refusal reason when the cap is reached."""
        async with self._lock:
            await self._load_usage(setup)
            if self._calls >= self._limits.max_calls:
                return "call_limit_exhausted"
            reserved = (self._in_flight + 1) * self._limits.call_cost_reserve_usd
            if self._cost_usd >= self._limits.max_cost_usd or (
                self._cost_usd + reserved > self._limits.max_cost_usd
            ):
                return "budget_exhausted"
            # Check and reservation are one step under the lock: no other review
            # can pass the check between them.
            self._calls += 1
            self._in_flight += 1
            try:
                await self._persist_usage(setup)
            except BaseException:
                # The call stays counted (as before); its reservation is released.
                self._in_flight -= 1
                raise
            return None

    async def _load_usage(self, setup: RuleSetup) -> None:
        if self._session_id is not None and setup.run_id != self._session_id:
            raise ValueError("AI review setup does not belong to the bound shadow session")
        if self._persistence is None or self._usage_loaded:
            return
        usage = await self._persistence.load_usage(run_id=setup.run_id)
        if usage.calls < 0 or not usage.cost_usd.is_finite() or usage.cost_usd < 0:
            raise ValueError("persisted AI session usage is invalid")
        self._calls = usage.calls
        self._cost_usd = usage.cost_usd
        self._usage_loaded = True

    async def _persist_usage(self, setup: RuleSetup) -> None:
        if self._persistence is not None:
            await self._persistence.persist_usage(run_id=setup.run_id, usage=self.usage)

    async def _persist_review(
        self,
        *,
        setup: RuleSetup,
        review: AIReview,
        response: AITransportResponse | None,
    ) -> None:
        if self._persistence is None:
            return
        prompt_hash = (response.prompt_hash if response is not None else None) or self._prompt_hash
        pricing_hash = (response.pricing_hash if response is not None else None) or self._pricing_hash
        if not self._is_digest(prompt_hash) or not self._is_digest(pricing_hash):
            raise ValueError("durable AI reviews require prompt and pricing hashes")
        await self._persistence.persist_review(
            run_id=setup.run_id,
            candidate_id=setup.candidate_id,
            review=review,
            prompt_hash=prompt_hash,
            pricing_hash=pricing_hash,
            input_tokens=response.input_tokens if response is not None else 0,
            output_tokens=response.output_tokens if response is not None else 0,
            cached_input_tokens=response.cached_input_tokens if response is not None else 0,
        )

    @staticmethod
    def _is_digest(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @staticmethod
    def _response_cost(response: object) -> Decimal | None:
        if not isinstance(response, AITransportResponse):
            return None
        try:
            cost = Decimal(str(response.cost_usd))
        except (InvalidOperation, ValueError):
            return None
        if not cost.is_finite() or cost < 0:
            return None
        return cost

    @staticmethod
    def _parse_output(output: object) -> dict[str, object] | None:
        if isinstance(output, str):
            if len(output) > _MAX_RESPONSE_CHARS:
                return None
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                return None
        if not isinstance(output, Mapping) or len(output) > len(_REVIEW_FIELDS):
            return None
        return dict(output)

    @staticmethod
    def _hash_output(output: object) -> str:
        try:
            serialized = json.dumps(
                output,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
                ensure_ascii=True,
            )
        except (TypeError, ValueError):
            serialized = type(output).__name__
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _blocked_review(
        self,
        *,
        setup: RuleSetup,
        reason: str,
        latency_ms: int = 0,
        cost_usd: Decimal = Decimal("0"),
        response_hash: str | None = None,
    ) -> AIReview:
        return AIReview(
            setup_fingerprint=setup.fingerprint(),
            decision="ABSTAIN",
            confidence=None,
            rationale=f"AI review blocked: {reason}.",
            model_id=self._model_id,
            prompt_version=self._prompt_version,
            response_hash=response_hash or self._hash_output({"failure": reason}),
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            failure_reason=reason,
        )


__all__ = [
    "REVIEW_CALL_COST_RESERVE_USD",
    "REVIEW_POOL_SIZE",
    "AIReviewPersistencePort",
    "AIReviewTransport",
    "AITransportResponse",
    "ReviewSessionLimits",
    "ReviewSessionUsage",
    "StructuredAIReviewProvider",
]
