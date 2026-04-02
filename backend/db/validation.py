"""Rule validation runs, manual interventions, and starter rule seeding."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
import aiosqlite
from models import Rule
from db.core import get_db

log = logging.getLogger(__name__)

async def save_rule_validation_run(
    *,
    rule_id: str,
    version: int,
    validation_mode: str,
    trades_count: int,
    hit_rate: float | None,
    net_pnl: float | None,
    expectancy: float | None,
    max_drawdown: float | None,
    overlap_score: float | None,
    passed: bool,
    notes: str | None = None,
    details: dict | None = None,
    user_id: str = "demo",
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details) if details else None
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_rule_validation_runs "
            "(rule_id, version, validation_mode, trades_count, hit_rate, net_pnl, expectancy, "
            " max_drawdown, overlap_score, passed, notes, details, created_at, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule_id,
                version,
                validation_mode,
                trades_count,
                hit_rate,
                net_pnl,
                expectancy,
                max_drawdown,
                overlap_score,
                1 if passed else 0,
                notes,
                details_json,
                created_at,
                user_id,
            ),
        )
        await db.commit()


async def get_rule_validation_runs(rule_id: str, user_id: str = "demo") -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT version, validation_mode, trades_count, hit_rate, net_pnl, expectancy, "
            "max_drawdown, overlap_score, passed, notes, details, created_at "
            "FROM ai_rule_validation_runs WHERE rule_id=? AND user_id=? "
            "ORDER BY created_at DESC",
            (rule_id, user_id),
        ) as cur:
            rows = await cur.fetchall()
    results = []
    for row in rows:
        entry = {
            "version": row[0],
            "validation_mode": row[1],
            "trades_count": row[2],
            "hit_rate": row[3],
            "net_pnl": row[4],
            "expectancy": row[5],
            "max_drawdown": row[6],
            "overlap_score": row[7],
            "passed": bool(row[8]),
            "notes": row[9],
            "created_at": row[11],
        }
        # S9: flatten details JSON into the response dict
        if row[10]:
            try:
                details = json.loads(row[10])
                entry.update(details)
            except Exception:
                pass
        results.append(entry)
    return [
        entry
        for entry in results
    ]


async def open_manual_intervention(
    *,
    severity: str,
    category: str,
    source: str,
    summary: str,
    required_action: str,
    symbol: str | None = None,
    user_id: str = "demo",
) -> int:
    opened_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO manual_interventions "
            "(opened_at, severity, category, symbol, source, summary, required_action, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (opened_at, severity, category, symbol, source, summary, required_action, user_id),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def get_manual_interventions(user_id: str = "demo", include_resolved: bool = False) -> list[dict]:
    where = "WHERE user_id=?" if include_resolved else "WHERE user_id=? AND resolved_at IS NULL"
    params: tuple[object, ...] = (user_id,)
    async with get_db() as db:
        async with db.execute(
            f"SELECT id, opened_at, severity, category, symbol, source, summary, required_action, "
            f"acknowledged_at, resolved_at, resolved_by FROM manual_interventions {where} "
            f"ORDER BY opened_at DESC",
            params,
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": row[0],
            "opened_at": row[1],
            "severity": row[2],
            "category": row[3],
            "symbol": row[4],
            "source": row[5],
            "summary": row[6],
            "required_action": row[7],
            "acknowledged_at": row[8],
            "resolved_at": row[9],
            "resolved_by": row[10],
        }
        for row in rows
    ]


async def acknowledge_manual_intervention(intervention_id: int, user_id: str = "demo") -> bool:
    acknowledged_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE manual_interventions SET acknowledged_at=? "
            "WHERE id=? AND user_id=? AND acknowledged_at IS NULL",
            (acknowledged_at, intervention_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def resolve_manual_intervention(intervention_id: int, resolved_by: str = "operator", user_id: str = "demo") -> bool:
    resolved_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE manual_interventions SET resolved_at=?, resolved_by=? "
            "WHERE id=? AND user_id=? AND resolved_at IS NULL",
            (resolved_at, resolved_by, intervention_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Seed starter rules
# ---------------------------------------------------------------------------

_STARTER_RULES = [
    {
        "name": "RSI Oversold Bounce",
        "symbol": "AAPL",
        "enabled": False,
        "conditions": [
            {"indicator": "RSI", "params": {"length": 14}, "operator": "crosses_below", "value": 30}
        ],
        "logic": "AND",
        "action": {"type": "BUY", "asset_type": "STK", "quantity": 100, "order_type": "MKT"},
        "cooldown_minutes": 60,
    },
    {
        "name": "Golden Cross",
        "symbol": "AAPL",
        "enabled": False,
        "conditions": [
            {"indicator": "SMA", "params": {"length": 50}, "operator": "crosses_above",
             "value": "SMA_200"},
        ],
        "logic": "AND",
        "action": {"type": "BUY", "asset_type": "STK", "quantity": 50, "order_type": "MKT"},
        "cooldown_minutes": 1440,
    },
    {
        "name": "RSI Overbought Exit",
        "symbol": "AAPL",
        "enabled": False,
        "conditions": [
            {"indicator": "RSI", "params": {"length": 14}, "operator": "crosses_above", "value": 70}
        ],
        "logic": "AND",
        "action": {"type": "SELL", "asset_type": "STK", "quantity": 100, "order_type": "MKT"},
        "cooldown_minutes": 60,
    },
]


async def _seed_starter_rules(db: aiosqlite.Connection) -> None:
    async with db.execute("SELECT COUNT(*) FROM rules") as cur:
        (count,) = await cur.fetchone()  # type: ignore[misc]
    if count == 0:
        for raw in _STARTER_RULES:
            rule = Rule.model_validate(raw)
            await db.execute(
                "INSERT INTO rules (id, data, user_id) VALUES (?, ?, ?)",
                (rule.id, rule.model_dump_json(), "demo"),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Screener presets CRUD
# ---------------------------------------------------------------------------
