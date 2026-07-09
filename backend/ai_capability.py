"""AI provider capability checks for startup and status reporting."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

try:
    from .config import DEFAULT_AI_FALLBACK_MODEL, DEFAULT_AI_PRIMARY_MODEL
except (ImportError, ValueError):  # pragma: no cover - top-level imports are used by pytest in this repo
    from config import DEFAULT_AI_FALLBACK_MODEL, DEFAULT_AI_PRIMARY_MODEL

AICapabilityState = Literal["disabled", "unconfigured", "invalid_model", "ready", "degraded"]
ModelLifecycleState = Literal["active", "deprecated", "retired"]

MODEL_RETIREMENT_BLOCK_WINDOW_DAYS = 30


@dataclass(frozen=True)
class ModelLifecycle:
    state: ModelLifecycleState
    retirement_date: date | None = None
    replacement: str | None = None


# Source: Anthropic model overview and model deprecation docs checked 2026-07-09.
# Keep this registry deliberately explicit so a configured model approaching
# retirement fails in tests before runtime calls start failing.
MODEL_LIFECYCLE: dict[str, ModelLifecycle] = {
    "claude-fable-5": ModelLifecycle("active"),
    "claude-opus-4-8": ModelLifecycle("active", retirement_date=date(2027, 5, 28)),
    "claude-opus-4-7": ModelLifecycle("active", retirement_date=date(2027, 4, 16)),
    "claude-opus-4-6": ModelLifecycle("active", retirement_date=date(2027, 2, 5)),
    "claude-opus-4-5-20251101": ModelLifecycle("active", retirement_date=date(2026, 11, 24)),
    "claude-sonnet-5": ModelLifecycle("active"),
    DEFAULT_AI_PRIMARY_MODEL: ModelLifecycle("active", retirement_date=date(2027, 2, 17)),
    "claude-sonnet-4-5-20250929": ModelLifecycle("active", retirement_date=date(2026, 9, 29)),
    DEFAULT_AI_FALLBACK_MODEL: ModelLifecycle("active", retirement_date=date(2026, 10, 15)),
    "claude-opus-4-1-20250805": ModelLifecycle(
        "deprecated",
        retirement_date=date(2026, 8, 5),
        replacement="claude-opus-4-8",
    ),
    "claude-opus-4-20250514": ModelLifecycle(
        "retired",
        retirement_date=date(2026, 6, 15),
        replacement="claude-opus-4-8",
    ),
    "claude-sonnet-4-20250514": ModelLifecycle(
        "retired",
        retirement_date=date(2026, 6, 15),
        replacement=DEFAULT_AI_PRIMARY_MODEL,
    ),
    "claude-3-7-sonnet-20250219": ModelLifecycle(
        "retired",
        retirement_date=date(2026, 2, 19),
        replacement=DEFAULT_AI_PRIMARY_MODEL,
    ),
    "claude-3-5-sonnet-20241022": ModelLifecycle(
        "retired",
        retirement_date=date(2025, 10, 28),
        replacement=DEFAULT_AI_PRIMARY_MODEL,
    ),
    "claude-3-5-sonnet-20240620": ModelLifecycle(
        "retired",
        retirement_date=date(2025, 10, 28),
        replacement=DEFAULT_AI_PRIMARY_MODEL,
    ),
    "claude-3-5-haiku-20241022": ModelLifecycle(
        "retired",
        retirement_date=date(2026, 2, 19),
        replacement=DEFAULT_AI_FALLBACK_MODEL,
    ),
    "claude-3-haiku-20240307": ModelLifecycle(
        "retired",
        retirement_date=date(2026, 4, 20),
        replacement=DEFAULT_AI_FALLBACK_MODEL,
    ),
}


@dataclass(frozen=True)
class AICapability:
    state: AICapabilityState
    mode: str
    provider: str = "anthropic"
    provider_configured: bool = False
    primary_model: str = ""
    fallback_model: str = ""
    checked_at: date = field(default_factory=date.today)
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_status_fields(self) -> dict[str, object]:
        return {
            "ai_capability": self.state,
            "ai_provider": self.provider,
            "ai_provider_configured": self.provider_configured,
            "ai_primary_model": self.primary_model or None,
            "ai_fallback_model": self.fallback_model or None,
            "ai_capability_errors": list(self.errors),
            "ai_capability_warnings": list(self.warnings),
        }


def _model_ids_for_config(c: object) -> dict[str, str]:
    return {
        "optimizer": str(getattr(c, "AI_MODEL_OPTIMIZER", "") or "").strip(),
        "narrative": str(getattr(c, "AI_MODEL_NARRATIVE", "") or "").strip(),
        "regime": str(getattr(c, "AI_MODEL_REGIME", "") or "").strip(),
        "portfolio": str(getattr(c, "AI_MODEL_PORTFOLIO", "") or "").strip(),
        "fallback": str(getattr(c, "AI_MODEL_FALLBACK", "") or "").strip(),
    }


def _check_model_lifecycle(role: str, model_id: str, *, today: date) -> tuple[list[str], list[str]]:
    if not model_id:
        return [f"AI model for {role} is empty."], []

    lifecycle = MODEL_LIFECYCLE.get(model_id)
    if lifecycle is None:
        return [
            f"AI model for {role} is unknown: {model_id}. "
            "Update backend/ai_capability.py after verifying the model is active."
        ], []

    replacement = f" Use {lifecycle.replacement}." if lifecycle.replacement else ""
    if lifecycle.state == "retired":
        retired_on = lifecycle.retirement_date.isoformat() if lifecycle.retirement_date else "unknown date"
        return [f"AI model for {role} is retired: {model_id} retired on {retired_on}.{replacement}"], []

    if lifecycle.state == "deprecated":
        if lifecycle.retirement_date and today >= lifecycle.retirement_date:
            return [
                f"AI model for {role} is retired: {model_id} retired on "
                f"{lifecycle.retirement_date.isoformat()}.{replacement}"
            ], []
        if lifecycle.retirement_date:
            block_date = lifecycle.retirement_date - timedelta(days=MODEL_RETIREMENT_BLOCK_WINDOW_DAYS)
            if today >= block_date:
                return [
                    f"AI model for {role} is within {MODEL_RETIREMENT_BLOCK_WINDOW_DAYS} days of "
                    f"retirement: {model_id} retires on {lifecycle.retirement_date.isoformat()}.{replacement}"
                ], []
        retires = f" retires on {lifecycle.retirement_date.isoformat()}" if lifecycle.retirement_date else ""
        return [], [f"AI model for {role} is deprecated: {model_id}{retires}.{replacement}"]

    return [], []


def resolve_ai_capability(
    c: object,
    *,
    mode: str | None = None,
    today: date | None = None,
    failure_status: dict | None = None,
) -> AICapability:
    today = today or date.today()
    resolved_mode = (mode or getattr(c, "AUTOPILOT_MODE", "OFF") or "OFF").upper()
    models = _model_ids_for_config(c)
    primary_model = models["optimizer"]
    fallback_model = models["fallback"]
    provider_configured = bool(str(getattr(c, "ANTHROPIC_API_KEY", "") or "").strip())

    errors: list[str] = []
    warnings: list[str] = []
    for role, model_id in models.items():
        model_errors, model_warnings = _check_model_lifecycle(role, model_id, today=today)
        errors.extend(model_errors)
        warnings.extend(model_warnings)

    if resolved_mode == "OFF":
        return AICapability(
            state="disabled",
            mode=resolved_mode,
            provider_configured=provider_configured,
            primary_model=primary_model,
            fallback_model=fallback_model,
            checked_at=today,
            errors=(),
            warnings=tuple(errors + warnings),
        )

    if not provider_configured:
        errors.insert(0, f"AUTOPILOT_MODE={resolved_mode} requires ANTHROPIC_API_KEY.")
        return AICapability(
            state="unconfigured",
            mode=resolved_mode,
            provider_configured=False,
            primary_model=primary_model,
            fallback_model=fallback_model,
            checked_at=today,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    if errors:
        return AICapability(
            state="invalid_model",
            mode=resolved_mode,
            provider_configured=True,
            primary_model=primary_model,
            fallback_model=fallback_model,
            checked_at=today,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    if not bool(getattr(c, "AI_FALLBACK_ENABLED", True)):
        warnings.append("AI_FALLBACK_ENABLED=false leaves Anthropic calls without model fallback.")

    if failure_status and failure_status.get("breaker_tripped"):
        warnings.append("AI circuit breaker is tripped after consecutive provider failures.")

    state: AICapabilityState = "degraded" if warnings else "ready"
    return AICapability(
        state=state,
        mode=resolved_mode,
        provider_configured=True,
        primary_model=primary_model,
        fallback_model=fallback_model,
        checked_at=today,
        errors=(),
        warnings=tuple(warnings),
    )


def startup_ai_capability_errors_warnings(c: object) -> tuple[list[str], list[str], AICapability]:
    capability = resolve_ai_capability(c)
    errors: list[str] = []
    warnings: list[str] = []

    if capability.state in ("unconfigured", "invalid_model"):
        errors.extend(capability.errors)
        warnings.extend(capability.warnings)
    elif capability.state in ("disabled", "degraded"):
        warnings.extend(capability.warnings)

    return errors, warnings, capability
