"""Per-strategy outcome statistics in R multiples.

Pure functions over any trade that knows its session, symbol, exit time and
R multiple, so replayed and (later) live PAPER trades are summarized alike.
Win rate and mean R come from ``trading.learning.summarize_outcomes``; the
expectancy is recomputed from its win/loss decomposition as a cross-check.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Protocol

from trading.learning.analytics import TradeOutcome, summarize_outcomes

_R_QUANTUM = Decimal("0.0001")
_RATE_QUANTUM = Decimal("0.0001")
_ONE = Decimal("1")
_ZERO = Decimal("0")


class RTrade(Protocol):
    """What the statistics need from a closed trade."""

    @property
    def session_date(self) -> date: ...

    @property
    def symbol(self) -> str: ...

    @property
    def exit_at(self) -> datetime: ...

    @property
    def exit_reason(self) -> str: ...

    @property
    def r_multiple(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class StrategyStats:
    strategy: str
    trades: int
    wins: int
    losses: int
    scratches: int
    win_rate: Decimal | None
    average_r: Decimal | None
    expectancy_r: Decimal | None
    average_win_r: Decimal | None
    average_loss_r: Decimal | None
    total_r: Decimal
    # Gross winning R over gross losing R; None when no trade lost.
    profit_factor: Decimal | None
    max_drawdown_r: Decimal
    best_day: tuple[date, Decimal] | None
    worst_day: tuple[date, Decimal] | None
    exit_reasons: tuple[tuple[str, int], ...]
    # (symbol, trades, total R), most traded first.
    by_symbol: tuple[tuple[str, int, Decimal], ...]
    unfilled: int = 0
    symbol_days: int = 0
    sessions: int = 0


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, _ZERO) / Decimal(len(values)) if values else None


def chronological(trades: Iterable[RTrade]) -> list[RTrade]:
    """Order of realization: exit time, then session and symbol for ties."""
    return sorted(trades, key=lambda trade: (trade.exit_at, trade.session_date, trade.symbol))


def max_drawdown_r(r_multiples: Iterable[Decimal]) -> Decimal:
    """Largest peak-to-trough fall of cumulative R; the curve starts at 0R."""
    equity = peak = drawdown = _ZERO
    for value in r_multiples:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def daily_r(trades: Iterable[RTrade]) -> dict[date, Decimal]:
    totals: dict[date, Decimal] = defaultdict(lambda: _ZERO)
    for trade in trades:
        totals[trade.session_date] += trade.r_multiple
    return dict(totals)


def summarize(
    strategy: str,
    trades: Iterable[RTrade],
    *,
    unfilled: int = 0,
    symbol_days: int = 0,
    sessions: int = 0,
) -> StrategyStats:
    ordered = chronological(trades)
    r_values = [trade.r_multiple for trade in ordered]
    if any(not value.is_finite() for value in r_values):
        raise ValueError("R multiples must be finite")
    learning = summarize_outcomes(
        [
            # One unit of risk per trade: realized R is the R multiple itself.
            TradeOutcome(
                strategy_id=strategy,
                strategy_version=1,
                realized_pnl=value,
                initial_risk_amount=_ONE,
            )
            for value in r_values
        ]
    )
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value < 0]
    non_wins = [value for value in r_values if value <= 0]
    count = len(r_values)
    average_win = _mean(wins)
    average_non_win = _mean(non_wins)
    expectancy: Decimal | None = None
    if count:
        win_share = Decimal(len(wins)) / Decimal(count)
        expectancy = win_share * (average_win or _ZERO) + (_ONE - win_share) * (
            average_non_win or _ZERO
        )
    gross_loss = -sum(losses, _ZERO)
    profit_factor = sum(wins, _ZERO) / gross_loss if gross_loss > 0 else None
    days = daily_r(ordered)
    best_day = (
        min(days.items(), key=lambda item: (-item[1], item[0])) if days else None
    )
    worst_day = min(days.items(), key=lambda item: (item[1], item[0])) if days else None
    symbol_counts = Counter(trade.symbol for trade in ordered)
    symbol_r: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for trade in ordered:
        symbol_r[trade.symbol] += trade.r_multiple
    return StrategyStats(
        strategy=strategy,
        trades=count,
        wins=len(wins),
        losses=len(losses),
        scratches=count - len(wins) - len(losses),
        win_rate=learning.win_rate,
        average_r=learning.expectancy_r,
        expectancy_r=expectancy,
        average_win_r=average_win,
        average_loss_r=_mean(losses),
        total_r=learning.total_r,
        profit_factor=profit_factor,
        max_drawdown_r=max_drawdown_r(r_values),
        best_day=best_day,
        worst_day=worst_day,
        exit_reasons=tuple(
            sorted(
                Counter(trade.exit_reason for trade in ordered).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        by_symbol=tuple(
            sorted(
                ((symbol, symbol_counts[symbol], symbol_r[symbol]) for symbol in symbol_counts),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        unfilled=unfilled,
        symbol_days=symbol_days,
        sessions=sessions,
    )


def _quantized(value: Decimal | None, quantum: Decimal = _R_QUANTUM) -> str | None:
    return None if value is None else str(value.quantize(quantum, rounding=ROUND_HALF_EVEN))


def stats_payload(stats: StrategyStats) -> dict[str, Any]:
    def day(item: tuple[date, Decimal] | None) -> dict[str, str] | None:
        if item is None:
            return None
        return {"date": item[0].isoformat(), "r": str(item[1].quantize(_R_QUANTUM))}

    return {
        "strategy": stats.strategy,
        "trades": stats.trades,
        "wins": stats.wins,
        "losses": stats.losses,
        "scratches": stats.scratches,
        "unfilled": stats.unfilled,
        "symbol_days": stats.symbol_days,
        "sessions": stats.sessions,
        "win_rate": _quantized(stats.win_rate, _RATE_QUANTUM),
        "average_r": _quantized(stats.average_r),
        "expectancy_r": _quantized(stats.expectancy_r),
        "average_win_r": _quantized(stats.average_win_r),
        "average_loss_r": _quantized(stats.average_loss_r),
        "total_r": _quantized(stats.total_r),
        "profit_factor": _quantized(stats.profit_factor),
        "max_drawdown_r": _quantized(stats.max_drawdown_r),
        "best_day": day(stats.best_day),
        "worst_day": day(stats.worst_day),
        "exit_reasons": dict(stats.exit_reasons),
        "by_symbol": [
            {"symbol": symbol, "trades": trades, "total_r": str(total.quantize(_R_QUANTUM))}
            for symbol, trades, total in stats.by_symbol
        ],
    }


def _r(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):+}R"


def _percent(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN)}%"


def format_strategy(
    stats: StrategyStats,
    *,
    label: str | None = None,
    top_symbols: int = 5,
    extra: Sequence[str] = (),
) -> str:
    """Plain text for Telegram: no Markdown, one fact per line."""
    lines = [f"{label or stats.strategy}"]
    filled = f"{stats.trades} trade{'' if stats.trades == 1 else 's'}"
    if stats.unfilled:
        filled += f" (+{stats.unfilled} unfilled setups)"
    if stats.symbol_days:
        filled += f" from {stats.symbol_days} symbol-days"
    lines.append(f"  {filled}")
    if not stats.trades:
        lines.append("  no filled trades")
        lines.extend(extra)
        return "\n".join(lines)
    profit_factor = (
        "n/a (no losses)"
        if stats.profit_factor is None
        else str(stats.profit_factor.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
    )
    lines.extend(
        [
            f"  win rate {_percent(stats.win_rate)} | avg R {_r(stats.average_r)}"
            f" | expectancy {_r(stats.expectancy_r)}/trade",
            f"  profit factor {profit_factor} | total {_r(stats.total_r)}"
            f" | max drawdown {_r(stats.max_drawdown_r).lstrip('+')}",
            f"  avg win {_r(stats.average_win_r)} | avg loss {_r(stats.average_loss_r)}",
            "  exits: " + ", ".join(f"{reason} {count}" for reason, count in stats.exit_reasons),
        ]
    )
    if stats.best_day is not None and stats.worst_day is not None:
        lines.append(
            f"  best day {stats.best_day[0].isoformat()} {_r(stats.best_day[1])}"
            f" | worst day {stats.worst_day[0].isoformat()} {_r(stats.worst_day[1])}"
        )
    if stats.by_symbol:
        lines.append(
            "  most traded: "
            + ", ".join(
                f"{symbol} {count} ({_r(total)})"
                for symbol, count, total in stats.by_symbol[:top_symbols]
            )
        )
    lines.extend(extra)
    return "\n".join(lines)


def format_text_report(
    stats: Sequence[StrategyStats],
    *,
    title: str,
    notes: Sequence[str] = (),
    labels: Mapping[str, str] | None = None,
    extra: Mapping[str, Sequence[str]] | None = None,
) -> str:
    sections = [title, *notes]
    for item in stats:
        sections.append("")
        sections.append(
            format_strategy(
                item,
                label=(labels or {}).get(item.strategy),
                extra=(extra or {}).get(item.strategy, ()),
            )
        )
    return "\n".join(sections)


__all__ = [
    "RTrade",
    "StrategyStats",
    "chronological",
    "daily_r",
    "format_strategy",
    "format_text_report",
    "max_drawdown_r",
    "stats_payload",
    "summarize",
]
