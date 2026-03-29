"""
Risk configuration — default limits and per-user overrides.

RiskLimits is a plain dataclass so it can be instantiated cheaply without
going through Pydantic validation (used inside hot paths in risk_manager).

This is the SINGLE SOURCE OF TRUTH for all risk-limit constants.
bot_runner.py, risk_manager.py, and safety_kernel.py must import from here
(or from config.py for env-overridable operational thresholds).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    # ── Position sizing ──────────────────────────────────────────────────────
    # Maximum single-position size as a percentage of total account value.
    # Used as an upper cap even when risk-per-trade sizing would suggest
    # a larger position.
    max_position_pct: float = 10.0

    # ── Concentration / correlation ─────────────────────────────────────────
    # Maximum aggregate exposure to any single GICS sector as a percentage of
    # net liquidation value. This is the *projected* post-trade sector weight:
    #
    #   sector_weight_after = (existing_sector_notional + new_order_notional)
    #                         / net_liquidation
    #
    # If sector_weight_after > max_sector_pct the order is blocked by the
    # portfolio concentration guard in risk_manager.check_portfolio_impact().
    max_sector_pct: float = 30.0

    # Maximum number of open, pending, or same-cycle-approved positions whose
    # absolute pairwise Pearson correlation with the candidate exceeds
    # corr_threshold. Checked pre-trade; if the count would exceed this limit
    # the order is blocked for "correlation_limit".
    max_correlated_positions: int = 3

    # ── Correlation threshold ───────────────────────────────────────────────
    # Pearson |r| above this value flags two positions as "highly correlated".
    # Used by portfolio concentration enforcement: any open/pending/same-cycle
    # position with abs(corr(candidate, existing)) > corr_threshold counts
    # toward the max_correlated_positions cap.
    corr_threshold: float = 0.80

    # ── Loss limits ─────────────────────────────────────────────────────────
    # Intra-day loss limit as % of account value at the start of the session.
    # When breached, the daily-loss lock should prevent NEW entries while still
    # allowing exits and de-risking actions.
    max_daily_loss_pct: float = 2.0

    # Peak-to-trough drawdown percentage that triggers an auto-pause at the
    # account level (e.g. switch AUTOPILOT_MODE from LIVE to PAPER/OFF).
    max_drawdown_pct: float = 10.0

    # ── Open positions ──────────────────────────────────────────────────────
    # Hard cap on the number of concurrently open positions across the account.
    max_open_positions: int = 20

    # ── Position sizing method ──────────────────────────────────────────────
    # fixed_fractional  — risk a fixed % of account per trade
    # kelly             — fractional Kelly criterion
    # equal_weight      — divide account evenly across max_open_positions
    # atr_based         — normalise risk by N × ATR
    position_sizing_method: str = "fixed_fractional"

    # ── ATR multiplier (used when method == "atr_based") ───────────────────
    # Number of ATRs used to set stop distance when sizing via ATR, so that
    # risk per trade is normalised across different volatility regimes.
    atr_multiplier: float = 2.0

    # ── Kelly fraction cap ──────────────────────────────────────────────────
    # Full Kelly can suggest enormous sizes; cap at this fraction of Kelly
    # to avoid extreme leverage. Used only when position_sizing_method="kelly".
    kelly_fraction: float = 0.25  # quarter-Kelly by default

    # ── Risk per trade (% of account) ──────────────────────────────────────
    # Core risk budget per trade as a percentage of net liquidation value.
    # This underpins your "1% risk per trade" framework and is enforced by
    # safety_kernel.assert_risk_budget() and risk_manager sizing logic.
    risk_per_trade_pct: float = 1.0


# Default global risk limits used by the bot unless overridden per-account.
DEFAULT_LIMITS = RiskLimits()
