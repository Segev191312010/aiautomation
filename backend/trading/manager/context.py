"""The manager's input, built from the database alone (no TWS client, no headlines).

The context pack offers the model at most ``MAX_CONTEXT_ROWS`` candidate rows,
the set C a plan may focus on: the newest screener sweep (the hot list
first), ORB's 09:45 scan, the gappers, the day universe when its table
exists (phase 1), then the rest of the sweep by score. Beside them: the
market context, the latest replay statistics, today's decisions, and the
bridge's P&L and slots, read from its own state file (read-only).

Free text from outside (news, headlines) is never included: the only text a
row carries is its IBKR symbol. Every reader is failure-tolerant, so a part
that cannot be read is sent as unavailable and never stops a plan.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Any

from db.core import get_db
from trading.manager.constants import (
    DAY_STEPS,
    FOCUS_MAX,
    MANAGER_VERSION,
    MAX_CONTEXT_ROWS,
    NOTE_MAX,
    REGIMES,
)
from trading.manager.plan import GateStates
from trading.scheduler.sessions import MARKET_TIMEZONE, MarketSession

logger = logging.getLogger("autotrader.manager.context")

# The pinned bridge's MAX_OPEN (moderate pilot); a test reads the bridge's
# source to keep the two equal.
BRIDGE_MAX_OPEN = 3
BRIDGE_ACTIVE_STATUSES = frozenset({"pending", "sent", "open", "needs_attention"})
DAILY_LOSS_SKIP = "daily loss limit reached"
MARKET_CONTEXT_MAX_AGE = timedelta(minutes=5)
_ROW_METRICS = ("last", "change_pct", "gap_pct", "rvol", "pct_from_high", "dollar_volume")
_DECISION_SAMPLE = 20


@dataclass(slots=True)
class CandidateRow:
    con_id: int
    symbol: str
    sources: list[str] = field(default_factory=list)
    hot_rank: int | None = None
    metrics: dict[str, str | None] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        return {
            "con_id": self.con_id,
            "symbol": self.symbol,
            "sources": list(dict.fromkeys(self.sources)),
            "hot_rank": self.hot_rank,
            **{name: self.metrics.get(name) for name in _ROW_METRICS},
        }


@dataclass(frozen=True, slots=True)
class ContextPack:
    session_date: date
    built_at: datetime
    slot: str
    running: tuple[str, ...]
    candidates: tuple[CandidateRow, ...]
    known: frozenset[int]
    gates: GateStates
    market: Mapping[str, object] | None
    replay: Mapping[str, object]
    decisions: Mapping[str, object]
    bridge: Mapping[str, object]
    half_day: bool = False
    closes_at: datetime | None = None

    @property
    def offered(self) -> frozenset[int]:
        """C: the only contracts a plan may focus on."""
        return frozenset(row.con_id for row in self.candidates)

    def payload(self) -> dict[str, object]:
        """The JSON the model reads (deterministic key and row order)."""
        return {
            "manager_version": MANAGER_VERSION,
            "session": {
                "date": self.session_date.isoformat(),
                "now_et": f"{self.built_at.astimezone(MARKET_TIMEZONE):%H:%M}",
                "slot": self.slot,
                "half_day": self.half_day,
                "closes_et": (
                    f"{self.closes_at.astimezone(MARKET_TIMEZONE):%H:%M}" if self.closes_at else None
                ),
            },
            "constraints": {
                "running_strategies": list(self.running),
                "day_steps": [float(step) for step in DAY_STEPS],
                "regimes": list(REGIMES),
                "focus_max": FOCUS_MAX,
                "note_max_chars": NOTE_MAX,
                "focus_only_from_candidates": True,
                "order_fields_are_not_yours": True,
            },
            "deterministic_gates": self.gates.payload(),
            "market_context": dict(self.market) if self.market is not None else {"available": False},
            "replay_stats": dict(self.replay),
            "todays_decisions": dict(self.decisions),
            "bridge": dict(self.bridge),
            "candidates": [row.payload() for row in self.candidates],
        }

    def encoded(self) -> str:
        return json.dumps(self.payload(), sort_keys=True, separators=(",", ":"), default=str)

    def digest(self) -> str:
        return sha256(self.encoded().encode("utf-8")).hexdigest()


# -- readers -------------------------------------------------------------------------


def _text(value: object) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return str(number) if number.is_finite() else None


async def _table_exists(db: Any, table: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def _sweep_rows(db: Any, session_date: date, now: datetime) -> list[dict[str, Any]]:
    async with db.execute(
        """SELECT payload_json FROM autotrader_screener_snapshots
           WHERE session_date = ? AND observed_at <= ?
           ORDER BY observed_at DESC, id DESC LIMIT 1""",
        (session_date.isoformat(), now.astimezone(timezone.utc).isoformat()),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return []
    rows = json.loads(str(row[0])).get("rows", [])
    return [item for item in rows if isinstance(item, dict) and isinstance(item.get("con_id"), int)]


async def _scan_rows(db: Any, session_date: date, suffix: str) -> list[tuple[int, str, int]]:
    """``(con_id, symbol, rank)`` of today's candidates in scanner runs named ``*<suffix>``."""
    async with db.execute(
        """SELECT c.con_id, c.symbol, MIN(c.rank)
           FROM autotrader_candidates c
           JOIN autotrader_scanner_runs s ON s.id = c.scanner_run_id
           JOIN autotrader_runs r ON r.id = s.run_id
           WHERE r.session_date = ? AND r.mode = 'shadow' AND s.id LIKE ?
           GROUP BY c.con_id, c.symbol
           ORDER BY MIN(c.rank), c.con_id""",
        (session_date.isoformat(), f"%{suffix}"),
    ) as cursor:
        return [(int(con_id), str(symbol), int(rank)) for con_id, symbol, rank in await cursor.fetchall()]


async def _day_universe(db: Any, session_date: date) -> list[tuple[int, str, int | None]]:
    if not await _table_exists(db, "autotrader_manager_day_universe"):
        return []
    async with db.execute(
        """SELECT con_id, symbol, best_rank FROM autotrader_manager_day_universe
           WHERE session_date = ? AND qualified = 1
           ORDER BY best_rank IS NULL, best_rank, con_id""",
        (session_date.isoformat(),),
    ) as cursor:
        return [
            (int(con_id), str(symbol), None if rank is None else int(rank))
            for con_id, symbol, rank in await cursor.fetchall()
        ]


async def _known_con_ids(db: Any) -> frozenset[int]:
    async with db.execute(
        "SELECT DISTINCT con_id FROM autotrader_universe WHERE con_id IS NOT NULL"
    ) as cursor:
        return frozenset(int(con_id) for (con_id,) in await cursor.fetchall())


async def _decisions(db: Any, session_date: date) -> dict[str, object]:
    async with db.execute(
        """SELECT COALESCE(json_extract(v.spec_json, '$.name'), r.strategy_id), d.outcome, c.symbol
           FROM autotrader_candidate_decisions d
           JOIN autotrader_runs r ON r.id = d.run_id
           JOIN autotrader_candidates c ON c.id = d.candidate_id
           LEFT JOIN autotrader_strategy_versions v
             ON v.strategy_id = r.strategy_id AND v.version = r.strategy_version
           WHERE r.session_date = ? AND r.mode = 'shadow'
           ORDER BY d.created_at, d.id""",
        (session_date.isoformat(),),
    ) as cursor:
        rows = await cursor.fetchall()
    counts: dict[str, dict[str, int]] = {}
    accepted: list[dict[str, str]] = []
    for strategy, outcome, symbol in rows:
        name = str(strategy)
        by_outcome = counts.setdefault(name, {})
        by_outcome[str(outcome)] = by_outcome.get(str(outcome), 0) + 1
        if outcome == "shadow_accepted" and len(accepted) < _DECISION_SAMPLE:
            accepted.append({"strategy": name, "symbol": str(symbol)})
    return {"by_strategy": counts, "accepted": accepted}


async def _kill_switch(db: Any) -> bool:
    if not await _table_exists(db, "autotrader_execution_fences"):
        return False
    async with db.execute(
        "SELECT stopped FROM autotrader_execution_fences WHERE name = 'paper'"
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row and int(row[0]))


BridgeReader = Callable[[date], tuple[Sequence[Mapping[str, Any]], str | None]]
ReplayReader = Callable[[], Mapping[str, object]]
MarketReader = Callable[..., Any]


def default_bridge_reader(session_date: date) -> tuple[Sequence[Mapping[str, Any]], str | None]:
    """The bridge's own per-date state file, read-only (as the notifier reads it)."""
    from trading.operations.notify_service import load_bridge_trades
    from trading.operations.paths import UserPaths

    trades, _source, problem = load_bridge_trades(UserPaths.current(), session_date)
    return trades, problem


def bridge_summary(trades: Sequence[Mapping[str, Any]], problem: str | None) -> dict[str, object]:
    """Realized P&L, open slots and whether the day's loss stop has fired."""
    if problem:
        return {"available": False, "problem": problem}
    realized = Decimal("0")
    active: list[str] = []
    day_stop = False
    for trade in trades:
        status = str(trade.get("status") or "")
        if status == "skipped" or trade.get("skipped") is True:
            if DAILY_LOSS_SKIP in str(trade.get("skip_reason") or "").lower():
                day_stop = True
            continue
        try:
            realized += Decimal(str(trade.get("realized_pnl") or "0"))
        except (InvalidOperation, ValueError):
            pass
        if status in BRIDGE_ACTIVE_STATUSES:
            active.append(str(trade.get("symbol") or "?"))
    return {
        "available": True,
        "realized_pnl_usd": str(realized),
        "active": sorted(active),
        "slots_used": len(active),
        "slots_free": max(0, BRIDGE_MAX_OPEN - len(active)),
        "day_stop": day_stop,
    }


def default_replay_reader() -> Mapping[str, object]:
    """The newest research replay's headline statistics per strategy (read-only files)."""
    from trading.operations.paths import UserPaths
    from trading.research.bars_store import ResearchPaths

    return replay_stats(ResearchPaths.for_user(UserPaths.current()).reports_directory)


_REPLAY_FIELDS = (
    "trades", "sessions", "win_rate", "expectancy_r", "profit_factor", "max_drawdown_r", "total_r",
)


def replay_stats(reports: Path) -> dict[str, object]:
    if not reports.is_dir():
        return {"available": False}
    dated = sorted((item for item in reports.iterdir() if item.is_dir()), key=lambda item: item.name)
    if not dated:
        return {"available": False}
    newest = dated[-1]
    strategies: dict[str, object] = {}
    for path in sorted(newest.glob("replay-*.json")):
        try:
            if path.stat().st_size > 5 * 1024 * 1024:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stats = payload.get("stats") if isinstance(payload, dict) else None
        if isinstance(stats, dict):
            strategies[path.stem.removeprefix("replay-")] = {
                name: stats.get(name) for name in _REPLAY_FIELDS
            }
    return {"available": bool(strategies), "as_of": newest.name, "strategies": strategies}


async def default_market_reader(session_date: date, *, now: datetime) -> Mapping[str, object] | None:
    from trading.market.context import latest_market_context

    context = await latest_market_context(session_date, now=now, max_age=MARKET_CONTEXT_MAX_AGE)
    return None if context is None else context.to_payload()


def _merge(
    rows: dict[int, CandidateRow], order: list[int], con_id: int, symbol: str, source: str
) -> CandidateRow:
    row = rows.get(con_id)
    if row is None:
        row = CandidateRow(con_id=con_id, symbol=symbol)
        rows[con_id] = row
        order.append(con_id)
    if source not in row.sources:
        row.sources.append(source)
    return row


def rank_candidates(
    *,
    sweep: Iterable[Mapping[str, Any]],
    orb_scan: Iterable[tuple[int, str, int]],
    gappers: Iterable[tuple[int, str, int]],
    day_universe: Iterable[tuple[int, str, int | None]],
    limit: int = MAX_CONTEXT_ROWS,
) -> tuple[CandidateRow, ...]:
    """C in a fixed order: hot list, ORB scan, gappers, day universe, the rest by score."""
    sweep_rows = list(sweep)
    by_con_id = {int(row["con_id"]): row for row in sweep_rows}
    rows: dict[int, CandidateRow] = {}
    order: list[int] = []

    def fill(row: CandidateRow) -> None:
        source = by_con_id.get(row.con_id)
        if source is None or row.metrics:
            return
        row.metrics = {name: _text(source.get(name)) for name in _ROW_METRICS}
        rank = source.get("hot_rank")
        row.hot_rank = int(rank) if isinstance(rank, int) and not isinstance(rank, bool) else None

    hot = sorted(
        (row for row in sweep_rows if row.get("hot") and isinstance(row.get("hot_rank"), int)),
        key=lambda row: int(row["hot_rank"]),
    )
    for item in hot:
        row = _merge(rows, order, int(item["con_id"]), str(item.get("symbol")), "hot")
        _merge(rows, order, row.con_id, row.symbol, str(item.get("source") or "sweep"))
        fill(row)
    for con_id, symbol, _rank in orb_scan:
        fill(_merge(rows, order, con_id, symbol, "orb_scan"))
    for con_id, symbol, _rank in gappers:
        fill(_merge(rows, order, con_id, symbol, "gapper"))
    for con_id, symbol, _rank in day_universe:
        fill(_merge(rows, order, con_id, symbol, "day_universe"))

    def score(row: Mapping[str, Any]) -> Decimal:
        try:
            return Decimal(str(row.get("score")))
        except (InvalidOperation, ValueError):
            return Decimal("-1")

    for item in sorted(sweep_rows, key=lambda row: (-score(row), int(row["con_id"]))):
        fill(_merge(rows, order, int(item["con_id"]), str(item.get("symbol")), str(item.get("source") or "sweep")))
    return tuple(rows[con_id] for con_id in order[:limit])


async def build_context(
    session: MarketSession,
    *,
    now: datetime,
    slot: str,
    running: Sequence[str],
    bridge_reader: BridgeReader = default_bridge_reader,
    replay_reader: ReplayReader = default_replay_reader,
    market_reader: MarketReader = default_market_reader,
    risk_off_families: frozenset[str] = frozenset({"momo"}),
) -> ContextPack:
    """Everything a plan sees, read from the database (and the bridge's state file)."""
    session_date = session.session_date
    async with get_db() as db:
        sweep = await _sweep_rows(db, session_date, now)
        orb_scan = await _scan_rows(db, session_date, "-scan")
        gappers = await _scan_rows(db, session_date, "-gappers")
        day_universe = await _day_universe(db, session_date)
        known = await _known_con_ids(db)
        decisions = await _decisions(db, session_date)
        kill_switch = await _kill_switch(db)
    candidates = rank_candidates(
        sweep=sweep, orb_scan=orb_scan, gappers=gappers, day_universe=day_universe
    )
    try:
        market = await market_reader(session_date, now=now)
    except Exception as exc:  # noqa: BLE001 - advisory input; the plan proceeds without it
        logger.warning("market context unavailable for the plan: %s", type(exc).__name__)
        market = None
    try:
        trades, problem = bridge_reader(session_date)
        bridge = bridge_summary(trades, problem)
    except Exception as exc:  # noqa: BLE001 - advisory input
        logger.warning("bridge state unavailable for the plan: %s", type(exc).__name__)
        bridge = {"available": False, "problem": type(exc).__name__}
    try:
        replay = dict(replay_reader())
    except Exception as exc:  # noqa: BLE001 - advisory input
        logger.warning("replay statistics unavailable for the plan: %s", type(exc).__name__)
        replay = {"available": False}
    gates = GateStates(
        kill_switch=kill_switch,
        day_stop=bool(bridge.get("day_stop")),
        risk_off=bool(market is not None and market.get("regime") == "risk_off"),
        risk_off_families=risk_off_families,
    )
    every = known | {row.con_id for row in candidates} | {int(row["con_id"]) for row in sweep}
    return ContextPack(
        session_date=session_date,
        built_at=now,
        slot=slot,
        running=tuple(running),
        candidates=candidates,
        known=frozenset(every),
        gates=gates,
        market=market,
        replay=replay,
        decisions=decisions,
        bridge=bridge,
        half_day=session.half_day,
        closes_at=session.closes_at,
    )


__all__ = [
    "BRIDGE_MAX_OPEN",
    "CandidateRow",
    "ContextPack",
    "bridge_summary",
    "build_context",
    "rank_candidates",
    "replay_stats",
]
