"""
AI Model Router — resilient LLM call layer with automatic fallback.

Inspired by nofx's autonomous model selection: if the primary model fails,
falls back to cheaper/faster alternatives. If ALL models fail, records the
failure for the circuit breaker.

Usage:
    from ai_model_router import ai_call

    result = await ai_call(
        system="You are a trading analyst.",
        prompt="Analyze AAPL",
        source="optimizer",
        model=cfg.AI_MODEL_OPTIMIZER,
        max_tokens=2000,
    )
    if result.ok:
        print(result.text, result.tokens_in, result.tokens_out)
    else:
        print(result.error)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from config import cfg

log = logging.getLogger(__name__)


@dataclass
class AIResult:
    ok: bool
    text: str = ""
    error: str = ""
    model_used: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    fallback_used: bool = False


# Model tiers: primary → fallback → last resort
def _build_model_chain(primary: str) -> list[str]:
    """Build ordered fallback chain, deduplicating."""
    chain = [primary]
    fallback = cfg.AI_MODEL_FALLBACK
    if fallback and fallback != primary:
        chain.append(fallback)
    # Last resort: always try haiku if nothing else worked
    last_resort = "claude-haiku-4-5-20251001"
    if last_resort not in chain:
        chain.append(last_resort)
    return chain


async def ai_call(
    *,
    system: str,
    prompt: str,
    source: str,
    model: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0,
) -> AIResult:
    """
    Call Claude API with automatic model fallback.

    On success, records success to circuit breaker.
    On total failure (all models exhausted), records failure.
    """
    api_key = cfg.ANTHROPIC_API_KEY
    if not api_key:
        return AIResult(ok=False, error="No ANTHROPIC_API_KEY configured")

    primary = model or cfg.AI_MODEL_OPTIMIZER
    chain = _build_model_chain(primary) if cfg.AI_FALLBACK_ENABLED else [primary]

    last_error = ""
    for idx, model_id in enumerate(chain):
        is_fallback = idx > 0
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)

            start = time.time()
            msg = await client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            elapsed_ms = int((time.time() - start) * 1000)

            text = msg.content[0].text
            tokens_in = msg.usage.input_tokens
            tokens_out = msg.usage.output_tokens

            if is_fallback:
                log.warning(
                    "AI call succeeded on FALLBACK model '%s' (primary '%s' failed)",
                    model_id, primary,
                )

            # Record success for circuit breaker
            from safety_kernel import record_ai_success
            record_ai_success(source)

            return AIResult(
                ok=True,
                text=text,
                model_used=model_id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=elapsed_ms,
                fallback_used=is_fallback,
            )

        except Exception as exc:
            last_error = str(exc)
            log.warning(
                "AI model '%s' failed for source='%s': %s%s",
                model_id, source, exc,
                " — trying next fallback" if idx < len(chain) - 1 else " — all models exhausted",
            )

    # All models failed — record failure for circuit breaker
    from safety_kernel import record_ai_failure, trip_circuit_breaker
    tripped = record_ai_failure(source)
    if tripped:
        try:
            await trip_circuit_breaker(source)
        except Exception as exc:
            log.error("Circuit breaker trip failed: %s", exc)

    return AIResult(ok=False, error=f"All models failed: {last_error}")
