"""Bounded AI review provider with no order or broker authority."""
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


@dataclass(frozen=True, slots=True)
class ReviewSessionLimits:
    timeout_seconds: float = 10.0
    max_calls: int = 20
    max_cost_usd: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_calls < 1:
            raise ValueError("max_calls must be positive")
        if not self.max_cost_usd.is_finite() or self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be finite and non-negative")


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

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        """Review a setup; failures abstain and never become rules-only approval."""
        async with self._lock:
            await self._load_usage(setup)
            if self._calls >= self._limits.max_calls:
                review = self._blocked_review(setup=setup, reason="call_limit_exhausted")
                await self._persist_review(setup=setup, review=review, response=None)
                return review
            if self._cost_usd >= self._limits.max_cost_usd:
                review = self._blocked_review(setup=setup, reason="budget_exhausted")
                await self._persist_review(setup=setup, review=review, response=None)
                return review

            self._calls += 1
            await self._persist_usage(setup)
            started = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    self._transport.complete(
                        setup=setup,
                        model_id=self._model_id,
                        prompt_version=self._prompt_version,
                    ),
                    timeout=self._limits.timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning("AI review timed out")
                review = self._blocked_review(setup=setup, reason="provider_timeout")
                await self._persist_review(setup=setup, review=review, response=None)
                return review
            except Exception:
                logger.warning("AI review provider unavailable")
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
            self._cost_usd += cost
            await self._persist_usage(setup)
            if self._cost_usd > self._limits.max_cost_usd:
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
    "AIReviewPersistencePort",
    "AIReviewTransport",
    "AITransportResponse",
    "ReviewSessionLimits",
    "ReviewSessionUsage",
    "StructuredAIReviewProvider",
]
