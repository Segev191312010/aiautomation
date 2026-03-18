"""Pre-built trading strategy templates for the Rule Builder."""
from __future__ import annotations
from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "golden_cross": {
        "id": "golden_cross", "name": "Golden Cross",
        "description": "Buy when 50-day SMA crosses above 200-day SMA.",
        "category": "trend_following",
        "entry_conditions": [{"indicator": "SMA", "params": {"period": 50}, "operator": "crosses_above", "value": "SMA_200"}],
        "exit_conditions": [{"indicator": "SMA", "params": {"period": 50}, "operator": "crosses_below", "value": "SMA_200"}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Slow signal — works best in trending markets.",
    },
    "death_cross": {
        "id": "death_cross", "name": "Death Cross",
        "description": "Sell when 50-day SMA crosses below 200-day SMA.",
        "category": "trend_following",
        "entry_conditions": [{"indicator": "SMA", "params": {"period": 50}, "operator": "crosses_below", "value": "SMA_200"}],
        "exit_conditions": [{"indicator": "SMA", "params": {"period": 50}, "operator": "crosses_above", "value": "SMA_200"}],
        "action": {"type": "SELL", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Lagging indicator — may trigger late.",
    },
    "rsi_oversold_bounce": {
        "id": "rsi_oversold_bounce", "name": "RSI Oversold Bounce",
        "description": "Buy when RSI drops below 30, sell when it rises above 70.",
        "category": "mean_reversion",
        "entry_conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "value": 30}],
        "exit_conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "value": 70}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Can catch falling knives in strong downtrends.",
    },
    "rsi_overbought_fade": {
        "id": "rsi_overbought_fade", "name": "RSI Overbought Fade",
        "description": "Sell when RSI exceeds 70, cover when RSI drops below 30.",
        "category": "mean_reversion",
        "entry_conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "value": 70}],
        "exit_conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "value": 30}],
        "action": {"type": "SELL", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Counter-trend — risky in strong uptrends.",
    },
    "macd_crossover": {
        "id": "macd_crossover", "name": "MACD Crossover",
        "description": "Buy when MACD line crosses above signal line.",
        "category": "momentum",
        "entry_conditions": [{"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}, "operator": "crosses_above", "value": "MACD_SIGNAL"}],
        "exit_conditions": [{"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}, "operator": "crosses_below", "value": "MACD_SIGNAL"}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Frequent signals in choppy markets.",
    },
    "bollinger_breakout": {
        "id": "bollinger_breakout", "name": "Bollinger Band Breakout",
        "description": "Buy when price crosses above upper Bollinger Band.",
        "category": "breakout",
        "entry_conditions": [{"indicator": "PRICE", "params": {}, "operator": "crosses_above", "value": "BBANDS_UPPER_20"}],
        "exit_conditions": [{"indicator": "PRICE", "params": {}, "operator": "crosses_below", "value": "BBANDS_MID_20"}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Works best after low-volatility squeeze periods.",
    },
    "mean_reversion": {
        "id": "mean_reversion", "name": "Mean Reversion",
        "description": "Buy when price < lower Bollinger AND RSI < 35, exit at SMA 20.",
        "category": "mean_reversion",
        "entry_conditions": [
            {"indicator": "PRICE", "params": {}, "operator": "<", "value": "BBANDS_LOWER_20"},
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "value": 35},
        ],
        "exit_conditions": [{"indicator": "PRICE", "params": {}, "operator": ">", "value": "SMA_20"}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Dual confirmation reduces false signals.",
    },
    "momentum_breakout": {
        "id": "momentum_breakout", "name": "Momentum Breakout",
        "description": "Buy when price > SMA 20, RSI > 60, and MACD > 0.",
        "category": "momentum",
        "entry_conditions": [
            {"indicator": "PRICE", "params": {}, "operator": ">", "value": "SMA_20"},
            {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "value": 60},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}, "operator": ">", "value": 0},
        ],
        "exit_conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "value": 40}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Strict entry — fewer but higher quality trades.",
    },
    "trend_following": {
        "id": "trend_following", "name": "Trend Following",
        "description": "Buy when price > SMA 50 > SMA 200 and RSI > 50.",
        "category": "trend_following",
        "entry_conditions": [
            {"indicator": "PRICE", "params": {}, "operator": ">", "value": "SMA_50"},
            {"indicator": "SMA", "params": {"period": 50}, "operator": ">", "value": "SMA_200"},
            {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "value": 50},
        ],
        "exit_conditions": [{"indicator": "PRICE", "params": {}, "operator": "<", "value": "SMA_50"}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Conservative — waits for strong trend confirmation.",
    },
    "triple_ma": {
        "id": "triple_ma", "name": "Triple Moving Average",
        "description": "Buy when SMA 20 > SMA 50 > SMA 200.",
        "category": "trend_following",
        "entry_conditions": [
            {"indicator": "SMA", "params": {"period": 20}, "operator": ">", "value": "SMA_50"},
            {"indicator": "SMA", "params": {"period": 50}, "operator": ">", "value": "SMA_200"},
        ],
        "exit_conditions": [{"indicator": "SMA", "params": {"period": 20}, "operator": "<", "value": "SMA_50"}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Very slow to trigger. Best for long-term positions.",
    },
    "stochastic_oversold": {
        "id": "stochastic_oversold", "name": "Stochastic Oversold",
        "description": "Buy when Stochastic %K < 20, exit when %K > 80.",
        "category": "mean_reversion",
        "entry_conditions": [{"indicator": "STOCH", "params": {"k_period": 14, "d_period": 3}, "operator": "<", "value": 20}],
        "exit_conditions": [{"indicator": "STOCH", "params": {"k_period": 14, "d_period": 3}, "operator": ">", "value": 80}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Can stay oversold in trending markets.",
    },
    "rsi_macd_combo": {
        "id": "rsi_macd_combo", "name": "RSI + MACD Combo",
        "description": "Buy when RSI < 40 AND MACD crosses above signal.",
        "category": "composite",
        "entry_conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "value": 40},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}, "operator": "crosses_above", "value": "MACD_SIGNAL"},
        ],
        "exit_conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "value": 70}],
        "action": {"type": "BUY", "quantity": 10, "order_type": "MKT"}, "logic": "AND",
        "suggested_timeframe": "1d", "risk_notes": "Composite — higher win rate, fewer opportunities.",
    },
}


def get_templates() -> list[dict[str, Any]]:
    return list(TEMPLATES.values())


def get_template(template_id: str) -> dict[str, Any] | None:
    return TEMPLATES.get(template_id)


def get_categories() -> list[str]:
    return sorted({t["category"] for t in TEMPLATES.values()})
