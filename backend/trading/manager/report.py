"""Plain-text views of the manager for the owner: the CLI, Telegram and the phone.

Reads only; formats only. Telegram shows Markdown literally, so no Markdown.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from trading.manager.constants import (
    BUDGET_EXHAUSTED,
    MANAGER_DAILY_BUDGET_USD,
    PLAN_SPEND_PURPOSE,
    PLAN_TTL,
    PLAN_VALID,
)
from trading.manager.store import SpendRow, StoredPlan, list_plans, read_spend
from trading.manager.switch import DayLatch, SwitchState, read_day_mode, read_switch
from trading.manager.schedule import plan_times
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession

MANAGED_FAMILY = "momo"
TOP_FOCUS = 5


def _et(moment: datetime | None) -> str:
    if moment is None:
        return "?"
    return f"{moment.astimezone(MARKET_TIMEZONE):%H:%M}"


def _step(value: Decimal) -> str:
    if value == 0:
        return "OFF"
    if value == 1:
        return "on"
    return f"on x{value.normalize()}"


async def symbols_for(con_ids: Sequence[int]) -> dict[int, str]:
    """Display symbols for con_ids, from the owner's universe (read-only)."""
    wanted = sorted(set(int(item) for item in con_ids))
    if not wanted:
        return {}
    from db.core import get_db

    marks = ", ".join("?" for _ in wanted)
    async with get_db() as db:
        async with db.execute(
            f"SELECT con_id, MIN(symbol) FROM autotrader_universe WHERE con_id IN ({marks}) GROUP BY con_id",
            wanted,
        ) as cursor:
            rows = await cursor.fetchall()
    return {int(con_id): str(symbol) for con_id, symbol in rows}


def plan_in_force(plans: Sequence[StoredPlan], now: datetime) -> StoredPlan | None:
    valid = [plan for plan in plans if plan.in_force(now)]
    return valid[-1] if valid else None


def format_plan(plan: StoredPlan, *, symbols: dict[int, str] | None = None, top: int = TOP_FOCUS) -> str:
    """Regime, strategies on/off with steps, the top focus names with notes."""
    assert plan.plan is not None
    day = plan.plan
    names = symbols or {}
    lines = [
        f"🧭 Day plan {plan.slot} ET · regime {day.regime.replace('_', ' ')}"
        f" · valid until {_et(plan.valid_until)} ET",
        "Strategies: "
        + ", ".join(f"{name} {_step(item.step)}" for name, item in sorted(day.strategies.items())),
    ]
    if day.focus:
        lines.append(f"Focus ({len(day.focus)}):")
        for entry in day.focus[:top]:
            label = names.get(entry.con_id, f"con {entry.con_id}")
            lines.append(f"• {label} ({', '.join(entry.strategies)}): {entry.note}")
        if len(day.focus) > top:
            lines.append(f"• … {len(day.focus) - top} more (/plan)")
    else:
        lines.append("Focus: none (managed strategies take no new entries)")
    if plan.dropped:
        lines.append(f"Dropped by the narrow-only gate: {len(plan.dropped)}")
    lines.append(f"Plan {plan.plan_id} · ${plan.cost_usd.quantize(Decimal('0.0001'))}")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ManagerStatus:
    session_date: date
    switch: SwitchState
    latch: DayLatch | None
    plans: tuple[StoredPlan, ...]
    spend: SpendRow
    now: datetime

    @property
    def current(self) -> StoredPlan | None:
        return plan_in_force(self.plans, self.now)


async def read_status(session_date: date, now: datetime, *, family: str = MANAGED_FAMILY) -> ManagerStatus:
    return ManagerStatus(
        session_date=session_date,
        switch=await read_switch(),
        latch=await read_day_mode(session_date, family),
        plans=tuple(await list_plans(session_date)),
        spend=await read_spend(spend_date=session_date, purpose=PLAN_SPEND_PURPOSE),
        now=now.astimezone(timezone.utc),
    )


def format_status(status: ManagerStatus) -> str:
    switch = status.switch
    lines = [f"🤖 Autopilot is {switch.state.upper()} (shadow only in this phase)"]
    if switch.event_id is not None:
        lines.append(f"Set {_et(switch.ts)} ET {switch.ts:%Y-%m-%d} via {switch.source}: {switch.reason}")
    latch = status.latch
    if latch is None:
        lines.append(f"Today ({status.session_date:%a %d %b}): not latched yet — managed MOMO latches when it starts")
    else:
        mode = "managed by the day plan" if latch.managed else "neutral (exactly MOMO_HOD_V2)"
        lines.append(f"Today: managed MOMO latched {mode} at {_et(latch.latched_at)} ET")
        if latch.managed and not switch.on:
            lines.append("Switched off since: neutral for the rest of today")
        if not latch.managed and switch.on:
            lines.append("Switched on after the latch: takes effect next session")
    current = status.current
    attempts = len(status.plans)
    valid = sum(1 for plan in status.plans if plan.outcome == PLAN_VALID)
    if current is not None:
        lines.append(f"Plan in force: {current.plan_id} (until {_et(current.valid_until)} ET)")
    elif attempts:
        lines.append(f"No plan in force (last attempt: {status.plans[-1].outcome})")
    else:
        lines.append("No plan written today yet")
    lines.append(
        f"Plans today: {valid} valid of {attempts} · spend ${status.spend.spent_usd.quantize(Decimal('0.01'))}"
        f" of ${MANAGER_DAILY_BUDGET_USD}"
    )
    lines.append("Real MOMO stays MOMO_HOD_V2, unchanged, whatever the switch says.")
    return "\n".join(lines)


def format_current_plan(status: ManagerStatus, *, symbols: dict[int, str] | None = None) -> str:
    current = status.current
    if current is None:
        if status.plans:
            last = status.plans[-1]
            return (
                f"No plan in force now (last attempt {last.slot} ET: {last.outcome}). "
                "Managed MOMO runs neutral, exactly as MOMO_HOD_V2."
            )
        return "No day plan written today yet (first plan 09:20 ET)."
    return format_plan(current, symbols=symbols, top=30)


# How long after a plan was due its absence counts as an outage (the first
# slot's call takes up to 90 s, and one retry follows three minutes later).
OUTAGE_GRACE_MINUTES = 5


def manager_outage(
    plans: Sequence[StoredPlan], session: MarketSession, now: datetime
) -> tuple[str, str] | None:
    """``(alert key, text)`` while no plan is in force inside the planning day.

    The key names the moment the outage began, so a restart never repeats
    the alert and a second outage later in the day gets its own.
    """
    from datetime import timedelta

    slots = plan_times(session)
    if not slots:
        return None
    grace = timedelta(minutes=OUTAGE_GRACE_MINUTES)
    if now < slots[0] + grace or now >= slots[-1] + PLAN_TTL:
        return None
    if plan_in_force(plans, now) is not None:
        return None
    valid = [plan for plan in plans if plan.valid and plan.valid_until and plan.created_at <= now]
    since = max((plan.valid_until for plan in valid if plan.valid_until), default=slots[0])
    if valid and now < since:
        return None
    key = f"manager_outage:{since.astimezone(MARKET_TIMEZONE):%H%M}"
    attempts = [plan for plan in plans if plan.created_at <= now]
    latest = attempts[-1] if attempts else None
    if latest is not None and latest.outcome == BUDGET_EXHAUSTED:
        text = (
            f"⚠️ AI day manager: today's ${MANAGER_DAILY_BUDGET_USD} plan budget is spent, so no "
            "new plans. Managed MOMO (shadow) runs neutral, exactly as MOMO_HOD_V2, for the rest "
            "of the day. Real MOMO is unaffected."
        )
    else:
        if latest is None or latest.valid:
            # Nothing was even attempted after the last valid plan.
            reason = "no plan written (is the manager child running?)"
        else:
            reason = f"last attempt {latest.slot} ET: {latest.outcome}"
        text = (
            f"⚠️ AI day manager: no plan in force since {_et(since)} ET ({reason}). Managed "
            "MOMO (shadow) runs neutral, exactly as MOMO_HOD_V2, until a valid plan arrives. "
            "Real MOMO is unaffected."
        )
    return key, text


__all__ = [
    "ManagerStatus",
    "OUTAGE_GRACE_MINUTES",
    "manager_outage",
    "format_current_plan",
    "format_plan",
    "format_status",
    "plan_in_force",
    "read_status",
    "symbols_for",
]
