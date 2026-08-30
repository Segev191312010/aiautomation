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


class StructuredAIReviewProvider:
    """Convert untrusted model output into bounded ``AIReview`` records."""

    def __init__(
        self,
        *,
        transport: AIReviewTransport,
        model_id: str,
        prompt_version: str,
        limits: ReviewSessionLimits | None = None,
    ) -> None:
        if not model_id or not model_id.strip():
            raise ValueError("model_id is required")
        if not prompt_version or not prompt_version.strip():
            raise ValueError("prompt_version is required")
        self._transport = transport
        self._model_id = model_id
        self._prompt_version = prompt_version
        self._limits = limits or ReviewSessionLimits()
        self._calls = 0
        self._cost_usd = Decimal("0")
        self._lock = asyncio.Lock()

    @property
    def usage(self) -> ReviewSessionUsage:
        return ReviewSessionUsage(calls=self._calls, cost_usd=self._cost_usd)

    async def propose_trade(self, *, setup: RuleSetup) -> AIReview:
        """Review a setup; failures abstain and never become rules-only approval."""
        async with self._lock:
            if self._calls >= self._limits.max_calls:
                return self._blocked_review(setup=setup, reason="call_limit_exhausted")
            if self._cost_usd >= self._limits.max_cost_usd:
                return self._blocked_review(setup=setup, reason="budget_exhausted")

            self._calls += 1
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
                return self._blocked_review(setup=setup, reason="provider_timeout")
            except Exception:
                logger.warning("AI review provider unavailable")
                return self._blocked_review(setup=setup, reason="provider_unavailable")

            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            cost = self._response_cost(response)
            if cost is None:
                return self._blocked_review(
                    setup=setup,
                    reason="invalid_provider_response",
                    latency_ms=latency_ms,
                )
            self._cost_usd += cost
            if self._cost_usd > self._limits.max_cost_usd:
                return self._blocked_review(
                    setup=setup,
                    reason="budget_exceeded",
                    latency_ms=latency_ms,
                    cost_usd=cost,
                )

            payload = self._parse_output(response.output)
            if (
                payload is None
                or not _REQUIRED_REVIEW_FIELDS.issubset(payload)
                or not set(payload).issubset(_REVIEW_FIELDS)
            ):
                return self._blocked_review(
                    setup=setup,
                    reason="invalid_response_schema",
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    response_hash=self._hash_output(response.output),
                )
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
                return self._blocked_review(
                    setup=setup,
                    reason="invalid_response_schema",
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    response_hash=self._hash_output(response.output),
                )
            return review

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
    "AIReviewTransport",
    "AITransportResponse",
    "ReviewSessionLimits",
    "ReviewSessionUsage",
    "StructuredAIReviewProvider",
]
