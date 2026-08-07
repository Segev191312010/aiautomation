"""
AI Telemetry — structured observability for all AI operations.

Tracks every AI invocation with immutable records, counters, and health state.
Replaces the current pattern of silent degradation (parse failures logged as
warnings with no telemetry, neutral fallbacks with no counters).

Records are persisted to SQLite for audit trail and surfaced via the
AI health status endpoint.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from database import get_db

log = logging.getLogger(__name__)

# ── In-memory counters (fast, no DB round-trip for dashboard reads) ─────────

@dataclass
class AITelemetryCounters:
    """Fast in-memory counters for dashboard health display."""
    invocations: int = 0
    successes: int = 0
    failures: int = 0
    parse_failures: int = 0
    fallback_used: int = 0
    neutral_outcomes: int = 0
    proposals_created: int = 0
    proposals_approved: int = 0
    proposals_rejected: int = 0
    proposals_expired: int = 0
    guardrail_blocks: int = 0
    circuit_breaker_trips: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_invocation_at: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocations": self.invocations,
            "successes": self.successes,
            "failures": self.failures,
            "parse_failures": self.parse_failures,
            "fallback_used": self.fallback_used,
            "neutral_outcomes": self.neutral_outcomes,
            "proposals_created": self.proposals_created,
            "proposals_approved": self.proposals_approved,
            "proposals_rejected": self.proposals_rejected,
            "proposals_expired": self.proposals_expired,
            "guardrail_blocks": self.guardrail_blocks,
            "circuit_breaker_trips": self.circuit_breaker_trips,
            "last_success_at": datetime.fromtimestamp(self.last_success_at, tz=timezone.utc).isoformat() if self.last_success_at else None,
            "last_failure_at": datetime.fromtimestamp(self.last_failure_at, tz=timezone.utc).isoformat() if self.last_failure_at else None,
            "last_invocation_at": datetime.fromtimestamp(self.last_invocation_at, tz=timezone.utc).isoformat() if self.last_invocation_at else None,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "avg_latency_ms": round(self.total_latency_ms / self.invocations) if self.invocations > 0 else 0,
            "success_rate": round(self.successes / self.invocations, 3) if self.invocations > 0 else 0,
        }


# Global counters (per-source)
_counters: dict[str, AITelemetryCounters] = {}


def _get_counter(source: str) -> AITelemetryCounters:
    if source not in _counters:
        _counters[source] = AITelemetryCounters()
    return _counters[source]


# ── Structured run record ────────────────────────────────────────────────────

@dataclass
class AIRunRecord:
    """Immutable record of a single AI invocation."""
    run_id: str
    source: str
    strategy: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model_used: str = ""
    model_fallback: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    success: bool = False
    parse_success: bool = False
    parse_error: str = ""
    validation_success: bool = False
    validation_error: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""
    neutral_outcome: bool = False
    recommendation: str = ""
    confidence: float = 0.0
    proposal_count: int = 0
    guardrail_blocks: int = 0
    error: str = ""
    prompt_version: str = ""
    input_symbols: list[str] = field(default_factory=list)
    raw_response_snippet: str = ""  # first 500 chars for diagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source": self.source,
            "strategy": self.strategy,
            "timestamp": self.timestamp,
            "model_used": self.model_used,
            "model_fallback": self.model_fallback,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "parse_success": self.parse_success,
            "parse_error": self.parse_error,
            "validation_success": self.validation_success,
            "validation_error": self.validation_error,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "neutral_outcome": self.neutral_outcome,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "proposal_count": self.proposal_count,
            "guardrail_blocks": self.guardrail_blocks,
            "error": self.error,
            "prompt_version": self.prompt_version,
            "input_symbols": self.input_symbols,
        }


# ── Public API ───────────────────────────────────────────────────────────────

def record_invocation(
    source: str,
    *,
    success: bool = False,
    parse_success: bool = False,
    parse_error: str = "",
    fallback_used: bool = False,
    neutral_outcome: bool = False,
    model_used: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    proposal_count: int = 0,
    guardrail_blocks: int = 0,
    error: str = "",
) -> None:
    """Record an AI invocation outcome (fast in-memory counter update)."""
    c = _get_counter(source)
    c.invocations += 1
    c.last_invocation_at = time.time()
    c.total_tokens_in += tokens_in
    c.total_tokens_out += tokens_out
    c.total_latency_ms += latency_ms

    if success:
        c.successes += 1
        c.last_success_at = time.time()
    else:
        c.failures += 1
        c.last_failure_at = time.time()

    if not parse_success and not success:
        c.parse_failures += 1

    if fallback_used:
        c.fallback_used += 1

    if neutral_outcome:
        c.neutral_outcomes += 1

    c.proposals_created += proposal_count
    c.guardrail_blocks += guardrail_blocks


def record_proposal_outcome(source: str, *, approved: bool = False, rejected: bool = False, expired: bool = False) -> None:
    """Record what happened to an AI proposal."""
    c = _get_counter(source)
    if approved:
        c.proposals_approved += 1
    elif rejected:
        c.proposals_rejected += 1
    elif expired:
        c.proposals_expired += 1


def record_circuit_breaker_trip(source: str) -> None:
    """Record a circuit breaker trip."""
    c = _get_counter(source)
    c.circuit_breaker_trips += 1


async def persist_run_record(record: AIRunRecord) -> None:
    """Persist a structured AI run record to the database."""
    try:
        async with get_db() as db:
            await db.execute(
                """INSERT INTO ai_run_records
                   (run_id, source, strategy, timestamp, model_used, model_fallback,
                    tokens_in, tokens_out, latency_ms, success, parse_success,
                    parse_error, validation_success, validation_error,
                    fallback_used, fallback_reason, neutral_outcome,
                    recommendation, confidence, proposal_count,
                    guardrail_blocks, error, prompt_version, input_symbols,
                    raw_response_snippet)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.run_id, record.source, record.strategy, record.timestamp,
                    record.model_used, record.model_fallback,
                    record.tokens_in, record.tokens_out, record.latency_ms,
                    int(record.success), int(record.parse_success),
                    record.parse_error, int(record.validation_success),
                    record.validation_error,
                    int(record.fallback_used), record.fallback_reason,
                    int(record.neutral_outcome),
                    record.recommendation, record.confidence, record.proposal_count,
                    record.guardrail_blocks, record.error, record.prompt_version,
                    json.dumps(record.input_symbols),
                    record.raw_response_snippet[:500] if record.raw_response_snippet else "",
                ),
            )
            await db.commit()
    except Exception as e:
        log.warning("Failed to persist AI run record: %s", e)


async def get_recent_runs(source: str | None = None, limit: int = 20) -> list[dict]:
    """Get recent AI run records from the database."""
    try:
        async with get_db() as db:
            if source:
                cur = await db.execute(
                    "SELECT * FROM ai_run_records WHERE source = ? ORDER BY timestamp DESC LIMIT ?",
                    (source, limit),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM ai_run_records ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            rows = await cur.fetchall()
            # Return as list of dicts (column names from cursor description)
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        log.warning("Failed to fetch AI run records: %s", e)
        return []


def get_health_status() -> dict[str, Any]:
    """Return AI health status for the dashboard.

    Returns per-source counters and overall health state.
    """
    sources: dict[str, dict] = {}
    overall = AITelemetryCounters()

    for source, c in _counters.items():
        sources[source] = c.to_dict()
        overall.invocations += c.invocations
        overall.successes += c.successes
        overall.failures += c.failures
        overall.parse_failures += c.parse_failures
        overall.fallback_used += c.fallback_used
        overall.neutral_outcomes += c.neutral_outcomes
        overall.proposals_created += c.proposals_created
        overall.guardrail_blocks += c.guardrail_blocks
        overall.circuit_breaker_trips += c.circuit_breaker_trips
        overall.total_tokens_in += c.total_tokens_in
        overall.total_tokens_out += c.total_tokens_out
        overall.total_latency_ms += c.total_latency_ms
        if c.last_success_at > overall.last_success_at:
            overall.last_success_at = c.last_success_at
        if c.last_failure_at > overall.last_failure_at:
            overall.last_failure_at = c.last_failure_at
        if c.last_invocation_at > overall.last_invocation_at:
            overall.last_invocation_at = c.last_invocation_at

    # Determine health state
    now = time.time()
    health_state = _compute_health_state(overall, now)

    return {
        "health_state": health_state,
        "overall": overall.to_dict(),
        "by_source": sources,
        "circuit_breaker_tripped": bool(overall.circuit_breaker_trips > 0),
    }


def _compute_health_state(c: AITelemetryCounters, now: float) -> str:
    """Compute the AI health state from counters."""
    from config import cfg

    if c.invocations == 0:
        return "disabled"

    # Check for recent activity
    seconds_since_last = now - c.last_invocation_at if c.last_invocation_at > 0 else 999999
    if seconds_since_last > 7200:  # 2 hours
        return "stale"

    # Check for circuit breaker
    if c.circuit_breaker_trips > 0:
        return "blocked"

    # Check for degradation
    if c.failures > 0 and c.successes == 0:
        return "degraded"

    success_rate = c.successes / c.invocations if c.invocations > 0 else 0
    if success_rate < 0.5 and c.invocations >= 3:
        return "degraded"

    # Check for parse failures
    if c.parse_failures > 0 and c.parse_failures >= c.successes:
        return "degraded"

    # Check for delayed (last success was a while ago)
    seconds_since_success = now - c.last_success_at if c.last_success_at > 0 else 999999
    if seconds_since_success > 3600 and c.invocations > 0:  # 1 hour
        return "delayed"

    # Check if proposals are available
    if c.proposals_created > c.proposals_approved + c.proposals_rejected + c.proposals_expired:
        return "proposal_available"

    return "healthy"


async def ensure_ai_run_records_table() -> None:
    """Create the ai_run_records table if it doesn't exist."""
    try:
        async with get_db() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ai_run_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    strategy TEXT DEFAULT '',
                    timestamp TEXT NOT NULL,
                    model_used TEXT DEFAULT '',
                    model_fallback INTEGER DEFAULT 0,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    parse_success INTEGER DEFAULT 0,
                    parse_error TEXT DEFAULT '',
                    validation_success INTEGER DEFAULT 0,
                    validation_error TEXT DEFAULT '',
                    fallback_used INTEGER DEFAULT 0,
                    fallback_reason TEXT DEFAULT '',
                    neutral_outcome INTEGER DEFAULT 0,
                    recommendation TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.0,
                    proposal_count INTEGER DEFAULT 0,
                    guardrail_blocks INTEGER DEFAULT 0,
                    error TEXT DEFAULT '',
                    prompt_version TEXT DEFAULT '',
                    input_symbols TEXT DEFAULT '[]',
                    raw_response_snippet TEXT DEFAULT ''
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_runs_source_ts ON ai_run_records(source, timestamp)"
            )
            await db.commit()
    except Exception as e:
        log.warning("Failed to create ai_run_records table: %s", e)
