"""
Risk configuration — default limits and per-user overrides.

RiskLimits is a plain dataclass so it can be instantiated cheaply without
going through Pydantic validation (used inside hot paths in risk_manager).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    # ── Position sizing ──────────────────────────────────────────────────────
    # Maximum single-position size as a percentage of total account value.
    max_position_pct: float = 10.0

    # ── Concentration / correlation ──────────────────────────────────────────
    # Maximum aggregate exposure to any single GICS sector (%).
    max_sector_pct: float = 30.0
    # Maximum number of open positions whose pairwise correlation exceeds the
    # threshold defined by CORR_THRESHOLD below.
    max_correlated_positions: int = 3

    # ── Loss limits ──────────────────────────────────────────────────────────
    # Intra-day loss limit as % of account value at the start of the session.
    max_daily_loss_pct: float = 2.0
    # Peak-to-trough drawdown percentage that triggers an auto-pause.
    max_drawdown_pct: float = 10.0

    # ── Open positions ───────────────────────────────────────────────────────
    max_open_positions: int = 20

    # ── Position sizing method ───────────────────────────────────────────────
    # fixed_fractional  — risk a fixed % of account per trade
    # kelly             — fractional Kelly criterion
    # equal_weight      — divide account evenly across max_open_positions
    # atr_based         — normalise risk by N × ATR
    position_sizing_method: str = "fixed_fractional"

    # ── Correlation threshold ────────────────────────────────────────────────
    # Pearson |r| above this value flags two positions as "highly correlated".
    corr_threshold: float = 0.80

    # ── ATR multiplier (used when method == "atr_based") ────────────────────
    atr_multiplier: float = 2.0

    # ── Kelly fraction cap ───────────────────────────────────────────────────
    # Full Kelly can suggest enormous sizes; cap at this fraction.
    kelly_fraction: float = 0.25  # quarter-Kelly by default

    # ── Risk per trade (% of account) — used by fixed_fractional + atr_based
    risk_per_trade_pct: float = 1.0


DEFAULT_LIMITS = RiskLimits()
