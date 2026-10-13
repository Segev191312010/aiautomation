"""The AI day manager's pinned limits and named tokens (Design D/E, Principles 2-3).

Every number here bounds what the model can do or cost; a test pins each one,
so a change is a reviewed change. The manager only ever narrows: it picks a
focus inside the deterministic candidates, switches running strategies off
for the day, and scales size down by the fixed steps.
"""
from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

# Identity of the manager's logic; every stored plan carries it, and a change
# resets the evidence identity (Design F).
MANAGER_VERSION = "manager-v1"
MANAGER_MODEL = "claude-opus-5-5"
# Opus 5.5 always thinks: cost is bounded by effort and max_tokens (LEARNED.md).
MANAGER_EFFORT = "medium"
PLAN_MAX_TOKENS = 16000
PLAN_TIMEOUT_SECONDS = 90.0

# Plan schedule (ET): before GAP's window, after ORB's 09:45 scan, then hourly.
PLAN_TIMES: tuple[time, ...] = (
    time(9, 20),
    time(9, 46),
    time(10, 30),
    time(11, 30),
    time(12, 30),
    time(13, 30),
    time(14, 30),
)
# A plan time is kept only this long before the session's close: a half day
# (13:00 close) plans until 11:30, matching MOMO's half-day cutoff (12:00).
LAST_PLAN_BEFORE_CLOSE = timedelta(minutes=90)
# A plan is in force for this long after it is written; past it, managed
# strategies fall back to neutral mode until a newer plan arrives.
PLAN_TTL = timedelta(minutes=75)
# A failed attempt is retried once, this long after its slot, if it still
# comes before the next slot.
PLAN_RETRY_AFTER = timedelta(minutes=3)

# Size steps: 0 = off (a day step) or skip (a setup step). Never above 1.
DAY_STEPS: tuple[Decimal, ...] = (
    Decimal("0"),
    Decimal("0.25"),
    Decimal("0.5"),
    Decimal("0.75"),
    Decimal("1"),
)
NEUTRAL_STEP = Decimal("1")
REGIMES: tuple[str, ...] = ("trend_up", "trend_down", "range", "high_vol", "unclear")
FOCUS_MAX = 30
NOTE_MAX = 140
REASON_MAX = 200
# The context pack offers the model at most this many candidate rows (the set C).
MAX_CONTEXT_ROWS = 80

# Spend: the manager's plans only; the per-run review limits are separate and
# unchanged, and real MOMO's review budget is never counted here.
MANAGER_DAILY_BUDGET_USD = Decimal("5")
PLAN_SPEND_PURPOSE = "plan"

# Plan outcomes (autotrader_manager_plans.outcome).
PLAN_VALID = "valid"
AI_TIMEOUT = "ai_timeout"
AI_MALFORMED = "ai_malformed"
AI_REFUSAL = "ai_refusal"
AI_TRUNCATED = "ai_truncated"
AI_UNAVAILABLE = "ai_unavailable"
AI_ORDER_FIELD_ECHO = "ai_order_field_echo"
BUDGET_EXHAUSTED = "budget_exhausted"
PLAN_OUTCOMES = frozenset(
    {
        PLAN_VALID,
        AI_TIMEOUT,
        AI_MALFORMED,
        AI_REFUSAL,
        AI_TRUNCATED,
        AI_UNAVAILABLE,
        AI_ORDER_FIELD_ECHO,
        BUDGET_EXHAUSTED,
    }
)

# Narrow-only gate tokens: one bad entry is dropped, never the whole plan.
AI_INVALID_ENTRY = "ai_invalid_entry"
AI_INVALID_STEP = "ai_invalid_step"
AI_UNKNOWN_STRATEGY = "ai_unknown_strategy"
AI_INVALID_NOTE = "ai_invalid_note"
GATE_HELD_OFF = "gate_held_off"
# Why an entry was invalid (the detail beside AI_INVALID_ENTRY).
CON_ID_NOT_OFFERED = "con_id_not_offered"
CON_ID_UNKNOWN = "con_id_unknown"
STRATEGY_NOT_RUNNING = "strategy_not_running"
NO_STRATEGIES = "no_strategies"
DUPLICATE_CON_ID = "duplicate_con_id"
FOCUS_OVER_LIMIT = "focus_over_limit"
MALFORMED_ENTRY = "malformed_entry"
# Logged CRITICAL whenever the model tried to widen (a tripwire, never silent).
MANAGER_WIDENING_ATTEMPT = "manager_widening_attempt"

# Neutral mode (Principle 3): why managed strategies run as their unmanaged
# selves. Plan failures carry MANAGER_FALLBACK_NEUTRAL; the switch being off
# is neutral by the owner's choice, not a fallback.
MANAGER_FALLBACK_NEUTRAL = "manager_fallback_neutral"
SWITCH_OFF = "switch_off"
SWITCH_OFF_MIDDAY = "switch_off_midday"
PLAN_MISSING = "plan_missing"
PLAN_EXPIRED = "plan_expired"
PLAN_ERROR = "plan_error"
FALLBACK_REASONS = frozenset({PLAN_MISSING, PLAN_EXPIRED, PLAN_ERROR})
NEUTRAL_REASONS = FALLBACK_REASONS | {SWITCH_OFF, SWITCH_OFF_MIDDAY}

# The reason on a managed run's record for a candidate the plan's focus left
# out until the cutoff (its decision keeps its outcome, e.g. data_blocked).
MANAGER_NOT_IN_FOCUS = "manager_not_in_focus"
# A managed setup the steps scale to zero shares is skipped, never resized up.
MANAGER_SIZE_ZERO = "manager_size_zero"
# The managed reviewer's own failures (the review fails closed, like V2's).
MANAGED_REVIEW_TOKENS = frozenset({AI_INVALID_STEP, AI_INVALID_NOTE, AI_ORDER_FIELD_ECHO})

# Keys an AI answer must never carry: order fields belong to deterministic code.
ORDER_FIELD_KEYS = frozenset(
    {
        "quantity",
        "qty",
        "shares",
        "size",
        "position_size",
        "notional",
        "price",
        "limit",
        "limit_price",
        "entry",
        "entry_limit",
        "entry_price",
        "stop",
        "stop_price",
        "target",
        "target_price",
        "side",
        "order",
        "order_type",
        "risk",
    }
)


__all__ = [name for name in dir() if name.isupper()]
