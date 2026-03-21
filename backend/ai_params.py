"""
AI Parameter Store — in-memory cache of AI-computed parameters.

The live trading pipeline reads from this store instead of config.py defaults.
In shadow mode, logs AI suggestions but returns config.py defaults.
Falls back to config.py when AI hasn't computed values yet.
"""
from __future__ import annotations

import logging
from config import cfg

log = logging.getLogger(__name__)


class AIParameterStore:
    """In-memory cache of AI-optimized parameters, persisted to DB via snapshots."""

    def __init__(self):
        self._signal_weights: dict[str, dict] = {}        # regime -> weight dict
        self._exit_params: dict[str, dict] = {}            # symbol -> {atr_stop_mult, atr_trail_mult}
        self._risk_multipliers: dict[str, float] = {}      # "global" or symbol -> multiplier
        self._min_score: float | None = None
        self._rule_sizing_multipliers: dict[str, float] = {}  # rule_id -> multiplier
        self._last_recompute: float = 0
        self._shadow_mode: bool = cfg.AI_SHADOW_MODE

    # ── Signal Weights ───────────────────────────────────────────────────────

    def get_signal_weights(self, regime: str) -> dict | None:
        """Return AI-optimized signal weights for the given regime, or None for defaults."""
        if self._shadow_mode:
            return None
        return self._signal_weights.get(regime)

    def set_signal_weights(self, regime: str, weights: dict) -> None:
        self._signal_weights[regime] = weights

    # ── Exit Parameters ──────────────────────────────────────────────────────

    def get_exit_params(self, symbol: str) -> dict:
        """Return AI-optimized exit params for a symbol, falling back to _default then config."""
        if self._shadow_mode:
            return {
                "atr_stop_mult": cfg.ATR_STOP_MULT,
                "atr_trail_mult": cfg.ATR_TRAIL_MULT,
            }
        params = self._exit_params.get(symbol.upper())
        if not params:
            params = self._exit_params.get("_default")
        if not params:
            return {
                "atr_stop_mult": cfg.ATR_STOP_MULT,
                "atr_trail_mult": cfg.ATR_TRAIL_MULT,
            }
        return {
            "atr_stop_mult": params.get("atr_stop_mult", cfg.ATR_STOP_MULT),
            "atr_trail_mult": params.get("atr_trail_mult", cfg.ATR_TRAIL_MULT),
        }

    def set_exit_params(self, symbol: str, params: dict) -> None:
        # Validate: ATR multipliers must be > 0.5 and < 10.0
        validated = {}
        for k in ("atr_stop_mult", "atr_trail_mult"):
            v = params.get(k)
            if v is not None:
                validated[k] = max(0.5, min(10.0, float(v)))
        self._exit_params[symbol.upper()] = validated

    # ── Risk Multiplier ──────────────────────────────────────────────────────

    def get_risk_multiplier(self) -> float:
        """Return AI risk multiplier (1.0 = no change from config defaults)."""
        if self._shadow_mode:
            return 1.0
        return self._risk_multipliers.get("global", 1.0)

    def set_risk_multiplier(self, multiplier: float) -> None:
        # Hard cap: risk multiplier cannot exceed 2.0x or go below 0.2x
        self._risk_multipliers["global"] = max(0.2, min(2.0, multiplier))

    # ── Minimum Score ────────────────────────────────────────────────────────

    def get_min_score(self) -> float:
        """Return AI-optimized min signal score, or default 50."""
        if self._shadow_mode:
            return 50.0
        return self._min_score if self._min_score is not None else 50.0

    def set_min_score(self, score: float) -> None:
        # Hard floor/ceiling: min_score must be between 20 and 90
        self._min_score = max(20.0, min(90.0, score))

    # ── Rule Sizing Multiplier ───────────────────────────────────────────────

    def get_rule_sizing_multiplier(self, rule_id: str) -> float:
        """Return sizing multiplier for a rule (1.0 = no change)."""
        if self._shadow_mode:
            return 1.0
        return self._rule_sizing_multipliers.get(rule_id, 1.0)

    def set_rule_sizing_multiplier(self, rule_id: str, multiplier: float) -> None:
        # Hard clamp: sizing multiplier must be between 0.1x and 3.0x
        self._rule_sizing_multipliers[rule_id] = max(0.1, min(3.0, multiplier))

    # ── Shadow Mode ──────────────────────────────────────────────────────────

    @property
    def shadow_mode(self) -> bool:
        return self._shadow_mode

    @shadow_mode.setter
    def shadow_mode(self, value: bool) -> None:
        self._shadow_mode = value
        if value:
            log.info("AI Parameter Store: shadow mode ENABLED (returning defaults)")
        else:
            log.info("AI Parameter Store: shadow mode DISABLED (returning AI values)")

    # ── Recompute Tracking ───────────────────────────────────────────────────

    @property
    def last_recompute(self) -> float:
        return self._last_recompute

    @last_recompute.setter
    def last_recompute(self, ts: float) -> None:
        self._last_recompute = ts

    def clear(self) -> None:
        """Reset all AI parameters to defaults."""
        self._signal_weights.clear()
        self._exit_params.clear()
        self._risk_multipliers.clear()
        self._min_score = None
        self._rule_sizing_multipliers.clear()
        self._last_recompute = 0
        log.info("AI Parameter Store: cleared all parameters")


# Module-level singleton
ai_params = AIParameterStore()
