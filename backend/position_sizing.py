"""
Stage 7 — Advanced Risk & Position-Sizing Framework.

Kelly-lite sizing per rule, ATR-based multiplier, per-rule equity curves,
R-multiple tracking, and hard risk limits.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class RuleStats:
    """Tracks per-rule performance for adaptive sizing."""
    rule_id: str
    total_trades: int = 0
    winners: int = 0
    total_pnl: float = 0.0
    total_win_pnl: float = 0.0
    total_loss_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    r_multiples: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.winners / self.total_trades if self.total_trades > 0 else 0

    @property
    def avg_win(self) -> float:
        return self.total_win_pnl / self.winners if self.winners > 0 else 0

    @property
    def avg_loss(self) -> float:
        losers = self.total_trades - self.winners
        return self.total_loss_pnl / losers if losers > 0 else 0

    @property
    def profit_factor(self) -> float:
        if self.total_loss_pnl == 0:
            return 999.0
        return abs(self.total_win_pnl / self.total_loss_pnl)

    @property
    def kelly_fraction(self) -> float:
        """Kelly criterion: f* = W - (1-W)/R, capped at 25%."""
        w = self.win_rate
        r = abs(self.avg_win / self.avg_loss) if self.avg_loss != 0 else 1.5
        if r == 0:
            return 0
        kelly = w - (1 - w) / r
        return max(0, min(kelly, 0.25))


class AdaptiveSizer:
    """Position sizing that adapts to per-rule performance."""

    def __init__(self, base_risk_pct: float = 1.0, min_risk_pct: float = 0.5,
                 max_risk_pct: float = 2.0, max_position_pct: float = 15.0):
        self.base_risk_pct = base_risk_pct
        self.min_risk_pct = min_risk_pct
        self.max_risk_pct = max_risk_pct
        self.max_position_pct = max_position_pct
        self.rule_stats: dict[str, RuleStats] = {}

    def get_stats(self, rule_id: str) -> RuleStats:
        if rule_id not in self.rule_stats:
            self.rule_stats[rule_id] = RuleStats(rule_id=rule_id)
        return self.rule_stats[rule_id]

    def record_trade(self, rule_id: str, pnl: float, risk_amount: float) -> None:
        """Record a completed trade for adaptive sizing."""
        stats = self.get_stats(rule_id)
        stats.total_trades += 1
        stats.total_pnl += pnl
        if pnl > 0:
            stats.winners += 1
            stats.total_win_pnl += pnl
        else:
            stats.total_loss_pnl += pnl

        # R-multiple
        r = pnl / risk_amount if risk_amount > 0 else 0
        stats.r_multiples.append(round(r, 2))

        # Equity tracking
        stats.current_equity += pnl
        if stats.current_equity > stats.peak_equity:
            stats.peak_equity = stats.current_equity
        dd = stats.peak_equity - stats.current_equity
        if dd > stats.max_drawdown:
            stats.max_drawdown = dd

    def calculate_size(self, rule_id: str, entry_price: float, stop_price: float,
                       equity: float, regime_multiplier: float = 1.0) -> dict:
        """
        Calculate position size using Kelly-lite adaptive sizing.

        Returns: {shares, value, risk_pct, method}
        """
        stats = self.get_stats(rule_id)
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
            return {"shares": 0, "value": 0, "risk_pct": 0, "method": "skip"}

        # Adaptive risk %: base adjusted by Kelly + regime
        if stats.total_trades >= 10:
            kelly = stats.kelly_fraction
            risk_pct = self.base_risk_pct * (1 + kelly)  # Kelly boosts good rules
            if stats.win_rate < 0.35:
                risk_pct *= 0.5  # Halve for underperformers
        else:
            risk_pct = self.base_risk_pct  # Not enough data, use base

        risk_pct = max(self.min_risk_pct, min(self.max_risk_pct, risk_pct))
        risk_pct *= regime_multiplier

        # Size calculation
        risk_amount = equity * (risk_pct / 100)
        shares = math.floor((risk_amount - 1.0) / stop_distance)  # -$1 commission

        # Position cap
        max_shares = math.floor(equity * (self.max_position_pct / 100) / entry_price)
        shares = min(shares, max_shares)
        shares = max(shares, 0)

        return {
            "shares": shares,
            "value": round(shares * entry_price, 2),
            "risk_pct": round(risk_pct, 2),
            "risk_amount": round(shares * stop_distance, 2),
            "method": "kelly_lite" if stats.total_trades >= 10 else "fixed",
            "kelly_f": round(stats.kelly_fraction, 3) if stats.total_trades >= 10 else None,
            "win_rate": round(stats.win_rate * 100, 1),
        }

    def should_disable_rule(self, rule_id: str, min_trades: int = 15) -> tuple[bool, str]:
        """Check if a rule should be auto-disabled due to poor performance."""
        stats = self.get_stats(rule_id)
        if stats.total_trades < min_trades:
            return False, "not enough trades"
        if stats.win_rate < 0.30:
            return True, f"win rate {stats.win_rate:.0%} < 30%"
        if stats.profit_factor < 0.8:
            return True, f"profit factor {stats.profit_factor:.1f} < 0.8"
        if len(stats.r_multiples) >= 7 and all(r < 0 for r in stats.r_multiples[-7:]):
            return True, "7 consecutive losing trades"
        return False, "performing OK"
