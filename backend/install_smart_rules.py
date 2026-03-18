"""Install smart trading rules — replaces simple rules with multi-factor strategies."""
import asyncio
import sys
sys.path.insert(0, ".")

from models import Rule, Condition, TradeAction
from database import init_db, save_rule, get_rules, delete_rule


SMART_RULES = [
    Rule(
        name="Oversold Bounce + Volume",
        universe="all", enabled=True, logic="AND",
        conditions=[
            Condition(indicator="RSI", params={"period": 14}, operator="<", value=30),
            Condition(indicator="PRICE", params={}, operator="<", value="BBANDS_LOWER_20"),
        ],
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
        cooldown_minutes=4320,
    ),
    Rule(
        name="Trend Pullback Buy",
        universe="all", enabled=True, logic="AND",
        conditions=[
            Condition(indicator="PRICE", params={}, operator=">", value="SMA_200"),
            Condition(indicator="RSI", params={"period": 14}, operator="<", value=40),
            Condition(indicator="MACD", params={"fast": 12, "slow": 26, "signal": 9}, operator=">", value=0),
        ],
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
        cooldown_minutes=4320,
    ),
    Rule(
        name="Breakout Momentum",
        universe="all", enabled=True, logic="AND",
        conditions=[
            Condition(indicator="PRICE", params={}, operator=">", value="SMA_50"),
            Condition(indicator="RSI", params={"period": 14}, operator=">", value=60),
            Condition(indicator="PRICE", params={}, operator=">", value="BBANDS_UPPER_20"),
        ],
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
        cooldown_minutes=4320,
    ),
    Rule(
        name="Triple Confirmation Buy",
        universe="all", enabled=True, logic="AND",
        conditions=[
            Condition(indicator="PRICE", params={}, operator=">", value="SMA_20"),
            Condition(indicator="SMA", params={"period": 20}, operator=">", value="SMA_50"),
            Condition(indicator="RSI", params={"period": 14}, operator=">", value=50),
            Condition(indicator="RSI", params={"period": 14}, operator="<", value=70),
        ],
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
        cooldown_minutes=4320,
    ),
    Rule(
        name="Stochastic + RSI Oversold",
        universe="all", enabled=True, logic="AND",
        conditions=[
            Condition(indicator="STOCH", params={"k_period": 14, "d_period": 3}, operator="<", value=20),
            Condition(indicator="RSI", params={"period": 14}, operator="<", value=35),
        ],
        action=TradeAction(type="BUY", quantity=1, order_type="MKT"),
        cooldown_minutes=4320,
    ),
    Rule(
        name="Overbought Exit",
        universe="all", enabled=True, logic="AND",
        conditions=[
            Condition(indicator="RSI", params={"period": 14}, operator=">", value=75),
            Condition(indicator="PRICE", params={}, operator=">", value="BBANDS_UPPER_20"),
        ],
        action=TradeAction(type="SELL", quantity=1, order_type="MKT"),
        cooldown_minutes=4320,
    ),
    Rule(
        name="Trend Breakdown Exit",
        universe="all", enabled=True, logic="AND",
        conditions=[
            Condition(indicator="PRICE", params={}, operator="<", value="SMA_50"),
            Condition(indicator="RSI", params={"period": 14}, operator="<", value=40),
            Condition(indicator="MACD", params={"fast": 12, "slow": 26, "signal": 9}, operator="<", value=0),
        ],
        action=TradeAction(type="SELL", quantity=1, order_type="MKT"),
        cooldown_minutes=4320,
    ),
]


async def main():
    await init_db()

    # Disable old universe rules
    existing = await get_rules()
    disabled = 0
    for r in existing:
        if r.universe and r.enabled:
            r.enabled = False
            await save_rule(r)
            disabled += 1
    print(f"Disabled {disabled} old universe rules")

    # Install new rules
    for rule in SMART_RULES:
        await save_rule(rule)
        conds = " AND ".join(
            f"{c.indicator}({c.params.get('period', '')}) {c.operator} {c.value}"
            for c in rule.conditions
        )
        print(f"  + {rule.name} [{rule.action.type}] :: {conds}")

    print(f"\nInstalled {len(SMART_RULES)} smart rules (all enabled)")


if __name__ == "__main__":
    asyncio.run(main())
