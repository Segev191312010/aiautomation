"""Read-only projections for the local live-monitor dashboard.

These GET routes only read the decision ledger, the screener snapshots, and the
temporary PAPER bridge state file. They expose no order, cancel, or authority
operation: the dashboard is a monitor, never a control surface.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from auth import User, get_current_user
from db.core import get_db
from trading.contracts import Decision
from trading.operations.paths import UserPaths
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session, next_market_session


SCREENER_SCHEMA = "screener.v1"
BRIDGE_STATE_FILENAME = "paper_bridge_state.json"
RATIONALE_PREVIEW_CHARS = 160
RULE_REJECTED_REASON = "Rules: no qualifying setup (price/liquidity/no breakout)"
DATA_BLOCKED_REASON = "Market data unavailable in time"
UNREADABLE_DECISION_REASON = "Decision payload unreadable"
_INTENT_REASON_PREFIXES = {
    "shadow_accepted": "Would buy",
    "approved": "Approved to buy",
    "submitted": "Submitted buy",
}

Clock = Callable[[], datetime]
BridgeStatus = Literal[
    "pending", "sent", "open", "closed", "cancelled", "skipped", "needs_attention", "unknown"
]
BridgeFileState = Literal["ok", "missing", "unreadable"]
# Bridge lifecycle states that imply a parent order reached the PAPER account.
_TRANSMITTED_STATUSES: dict[str, BridgeStatus] = {
    "sent": "sent",
    "open": "open",
    "closed": "closed",
    "cancelled": "cancelled",
}
_PLACEHOLDER_SYMBOLS = frozenset({"?"})
MarketPhase = Literal["pre_open", "open", "after_close", "no_session", "unknown"]


def _system_clock() -> datetime:
    return datetime.now(timezone.utc)


def live_clock() -> Clock:
    """Wall-clock dependency; tests override it to pin the New York session date."""
    return _system_clock


def bridge_state_path() -> Path:
    """Location of the temporary PAPER bridge state file; tests override it."""
    return UserPaths.current().data_directory / BRIDGE_STATE_FILENAME


router = APIRouter(
    prefix="/api/live",
    tags=["live"],
    dependencies=[Depends(get_current_user)],
)

CurrentUser = Annotated[User, Depends(get_current_user)]
ClockDependency = Annotated[Clock, Depends(live_clock)]
BridgePathDependency = Annotated[Path, Depends(bridge_state_path)]


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketView(_View):
    session_date: date
    phase: MarketPhase
    is_open: bool
    opens_at: str | None = None
    closes_at: str | None = None
    entry_cutoff_at: str | None = None
    next_open_at: str | None = None
    half_day: bool = False


class OrderCounts(_View):
    sent: int = 0
    open: int = 0
    skipped: int = 0
    needs_attention: int = 0
    realized_pnl: str = "0.00"


class LiveStatus(_View):
    server_time_utc: str
    server_time_new_york: str
    session_date: date
    market: MarketView
    snapshot_observed_at: str | None
    snapshot_age_seconds: float | None
    decisions_today: dict[str, int]
    decisions_total: int
    orders_today: OrderCounts
    bridge_state: BridgeFileState


class SignalAI(_View):
    decision: str
    confidence: str | None
    rationale: str


class SignalIntent(_View):
    quantity: int
    entry_limit: str
    stop_price: str
    target_price: str


class Signal(_View):
    id: str
    created_at: str
    run_id: str
    strategy: Literal["momo", "orb"]
    symbol: str
    primary_exchange: str | None
    outcome: str
    ai: SignalAI | None
    risk_codes: list[str]
    intent: SignalIntent | None
    reason: str


class BridgeOrder(_View):
    decision_id: str
    symbol: str | None
    primary_exchange: str | None
    strategy: Literal["momo", "orb"] | None
    parent_order_id: int | None
    status: BridgeStatus
    skip_reason: str | None
    decided_at: str | None
    sent_at: str | None
    run_id: str | None
    intent: SignalIntent | None
    filled_quantity: int | None
    avg_entry_price: str | None
    exit_quantity: int | None
    avg_exit_price: str | None
    exit_reason: str | None
    realized_pnl: str | None


class BridgeStateUnreadable(RuntimeError):
    """The bridge state file is missing its contract or a read was torn."""


@dataclass(frozen=True, slots=True)
class BridgeEntry:
    """One decision as the temporary PAPER bridge recorded it (v1 or v2 state)."""

    decision_id: str
    symbol: str | None
    parent_order_id: int | None
    status: BridgeStatus
    primary_exchange: str | None = None
    strategy: Literal["momo", "orb"] | None = None
    skip_reason: str | None = None
    sent_at: str | None = None
    intent: SignalIntent | None = None
    filled_quantity: int | None = None
    avg_entry_price: str | None = None
    exit_quantity: int | None = None
    avg_exit_price: str | None = None
    exit_reason: str | None = None
    realized_pnl: str | None = None


@dataclass(frozen=True, slots=True)
class _DecisionDetail:
    run_id: str
    created_at: str
    symbol: str
    primary_exchange: str | None
    intent: SignalIntent | None


# --------------------------------------------------------------------------
# Pure projections
# --------------------------------------------------------------------------


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("live dashboard clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    # Millisecond precision keeps every browser's Date parser exact.
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _iso_new_york(value: datetime) -> str:
    return value.astimezone(MARKET_TIMEZONE).isoformat(timespec="seconds")


def _parse_stored_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    # Ledger writers emit UTC; a naive value is read as UTC rather than dropped.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stored_time_text(value: object) -> str:
    parsed = _parse_stored_time(value)
    return _iso_utc(parsed) if parsed is not None else str(value)


def _age_seconds(now: datetime, observed: datetime | None) -> float | None:
    if observed is None:
        return None
    return round(max(0.0, (now - observed).total_seconds()), 1)


def new_york_date(now: datetime) -> date:
    return _require_utc(now).astimezone(MARKET_TIMEZONE).date()


def project_market(now: datetime) -> MarketView:
    """Describe the XNYS session around ``now`` without guessing on calendar failure."""
    utc_now = _require_utc(now)
    today = utc_now.astimezone(MARKET_TIMEZONE).date()
    try:
        session = market_session(today)
        following = next_market_session(today)
    except (RuntimeError, ValueError):
        return MarketView(session_date=today, phase="unknown", is_open=False)
    if session is None:
        return MarketView(
            session_date=today,
            phase="no_session",
            is_open=False,
            next_open_at=_iso_new_york(following.opens_at),
        )
    phase: MarketPhase
    next_open: datetime | None
    if utc_now < session.opens_at:
        phase, next_open = "pre_open", session.opens_at
    elif utc_now < session.closes_at:
        phase, next_open = "open", None
    else:
        phase, next_open = "after_close", following.opens_at
    return MarketView(
        session_date=today,
        phase=phase,
        is_open=phase == "open",
        opens_at=_iso_new_york(session.opens_at),
        closes_at=_iso_new_york(session.closes_at),
        entry_cutoff_at=_iso_new_york(session.entry_cutoff_at),
        next_open_at=_iso_new_york(next_open) if next_open is not None else None,
        half_day=session.half_day,
    )


def _load_decision(payload_json: object) -> Decision | None:
    try:
        return Decision.model_validate_json(str(payload_json))
    except ValidationError:
        return None


def _primary_exchange(contract_json: object) -> str | None:
    try:
        contract = json.loads(str(contract_json))
    except (TypeError, ValueError):
        return None
    value = contract.get("primary_exchange") if isinstance(contract, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _intent_view(decision: Decision | None) -> SignalIntent | None:
    if decision is None or decision.risk is None or decision.risk.intent is None:
        return None
    intent = decision.risk.intent
    return SignalIntent(
        quantity=intent.quantity,
        entry_limit=str(intent.entry_limit),
        stop_price=str(intent.stop_price),
        target_price=str(intent.target_price),
    )


def _ai_view(decision: Decision | None) -> SignalAI | None:
    if decision is None or decision.ai_review is None:
        return None
    review = decision.ai_review
    return SignalAI(
        decision=review.decision,
        confidence=str(review.confidence) if review.confidence is not None else None,
        rationale=review.rationale,
    )


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= RATIONALE_PREVIEW_CHARS:
        return flat
    return flat[:RATIONALE_PREVIEW_CHARS].rstrip() + "…"


def signal_reason(outcome: str, decision: Decision | None) -> str:
    """One short operator-facing sentence explaining why a signal was taken or skipped."""
    if outcome == "rule_rejected":
        return RULE_REJECTED_REASON
    if outcome == "data_blocked":
        return DATA_BLOCKED_REASON
    if decision is None:
        return UNREADABLE_DECISION_REASON
    review = decision.ai_review
    risk = decision.risk
    intent = risk.intent if risk is not None else None
    if outcome in _INTENT_REASON_PREFIXES and intent is not None:
        return f"{_INTENT_REASON_PREFIXES[outcome]}: {intent.quantity} @ {intent.entry_limit}"
    if outcome == "ai_abstained" and review is not None:
        return f"Claude abstained: {_preview(review.rationale)}"
    if outcome == "ai_blocked" and review is not None:
        return f"Claude review failed: {_preview(review.failure_reason or review.rationale)}"
    if outcome == "risk_blocked" and risk is not None:
        return f"Risk blocked: {', '.join(risk.codes)}"
    return outcome.replace("_", " ").capitalize()


def strategy_for_run(run_id: str) -> Literal["momo", "orb"]:
    return "momo" if "momo" in run_id else "orb"


def project_signal(row: Sequence[object]) -> Signal:
    decision_id, run_id, outcome, payload_json, created_at, symbol, contract_json = row
    decision = _load_decision(payload_json)
    outcome_text = str(outcome)
    return Signal(
        id=str(decision_id),
        created_at=_stored_time_text(created_at),
        run_id=str(run_id),
        strategy=strategy_for_run(str(run_id)),
        symbol=str(symbol),
        primary_exchange=_primary_exchange(contract_json),
        outcome=outcome_text,
        ai=_ai_view(decision),
        risk_codes=list(decision.risk.codes) if decision is not None and decision.risk else [],
        intent=_intent_view(decision),
        reason=signal_reason(outcome_text, decision),
    )


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _clean_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _clean_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _decimal_text(value: object, *, positive: bool = False) -> str | None:
    amount = _clean_decimal(value)
    if amount is None or (positive and amount <= 0):
        return None
    return str(amount)


def _entry_status(entry: dict[str, Any], parent_id: int | None) -> BridgeStatus:
    """Map the bridge lifecycle without ever claiming an untransmitted order was sent."""
    declared = _clean_text(entry.get("status"))
    if entry.get("skipped") is True or declared == "skipped":
        return "skipped"
    if declared is None:  # v1 entries carry no lifecycle field
        return "sent" if parent_id is not None else "unknown"
    if declared == "needs_attention":
        return "needs_attention"
    if declared == "pending":
        return "pending"
    if declared in _TRANSMITTED_STATUSES and parent_id is not None:
        return _TRANSMITTED_STATUSES[declared]
    return "unknown"


def _entry_intent(entry: dict[str, Any]) -> SignalIntent | None:
    quantity = _clean_int(entry.get("qty"))
    entry_limit = _decimal_text(entry.get("entry"), positive=True)
    stop_price = _decimal_text(entry.get("stop"), positive=True)
    target_price = _decimal_text(entry.get("target"), positive=True)
    if quantity is None or quantity <= 0 or not (entry_limit and stop_price and target_price):
        return None
    return SignalIntent(
        quantity=quantity, entry_limit=entry_limit, stop_price=stop_price, target_price=target_price
    )


def _parse_bridge_entry(decision_id: str, entry: dict[str, Any]) -> BridgeEntry:
    parent_id = _clean_int(entry.get("parent"))
    symbol = _clean_text(entry.get("symbol"))
    strategy = entry.get("strategy")
    sent_at = _clean_text(entry.get("sent_at"))
    filled = _clean_int(entry.get("filled"))
    exited = _clean_int(entry.get("exit_filled"))
    return BridgeEntry(
        decision_id=decision_id,
        symbol=None if symbol in _PLACEHOLDER_SYMBOLS else symbol,
        parent_order_id=parent_id,
        status=_entry_status(entry, parent_id),
        primary_exchange=_clean_text(entry.get("primary_exchange")),
        strategy=strategy if strategy in ("momo", "orb") else None,
        skip_reason=_clean_text(entry.get("skip_reason")),
        sent_at=_stored_time_text(sent_at) if sent_at else None,
        intent=_entry_intent(entry),
        filled_quantity=filled,
        avg_entry_price=_decimal_text(entry.get("avg_entry")) if filled else None,
        exit_quantity=exited,
        avg_exit_price=_decimal_text(entry.get("avg_exit")) if exited else None,
        exit_reason=_clean_text(entry.get("exit_reason")),
        realized_pnl=_decimal_text(entry.get("realized_pnl")),
    )


def parse_bridge_state(raw: str) -> list[BridgeEntry]:
    """Parse ``{"submitted": {decision_id: entry}}`` from bridge v1 or v2.

    v1 entries hold ``parent``/``symbol``/``skipped``; v2 entries add the
    bracket lifecycle (``status``, fills, exits, realized P&L). An entry is
    only reported as transmitted when it carries a broker parent order id.
    """
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise BridgeStateUnreadable("PAPER bridge state file is not valid JSON") from exc
    submitted = document.get("submitted") if isinstance(document, dict) else None
    if not isinstance(submitted, dict):
        raise BridgeStateUnreadable("PAPER bridge state file has no submitted map")
    return [
        _parse_bridge_entry(str(decision_id), value if isinstance(value, dict) else {})
        for decision_id, value in submitted.items()
    ]


def read_bridge_state(path: Path) -> list[BridgeEntry] | None:
    """Return parsed entries, or ``None`` when the bridge has never run."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise BridgeStateUnreadable("PAPER bridge state file could not be read") from exc
    return parse_bridge_state(raw)


def project_orders(
    entries: Iterable[BridgeEntry], details: dict[str, _DecisionDetail]
) -> list[BridgeOrder]:
    """Join bridge entries with their ledger decisions; the bridge's own fields win."""
    orders: list[BridgeOrder] = []
    for entry in entries:
        detail = details.get(entry.decision_id)
        orders.append(
            BridgeOrder(
                decision_id=entry.decision_id,
                symbol=entry.symbol or (detail.symbol if detail else None),
                primary_exchange=entry.primary_exchange or (detail.primary_exchange if detail else None),
                strategy=entry.strategy or (strategy_for_run(detail.run_id) if detail else None),
                parent_order_id=entry.parent_order_id,
                status=entry.status,
                skip_reason=entry.skip_reason,
                decided_at=detail.created_at if detail else None,
                sent_at=entry.sent_at,
                run_id=detail.run_id if detail else None,
                intent=entry.intent or (detail.intent if detail else None),
                filled_quantity=entry.filled_quantity,
                avg_entry_price=entry.avg_entry_price,
                exit_quantity=entry.exit_quantity,
                avg_exit_price=entry.avg_exit_price,
                exit_reason=entry.exit_reason,
                realized_pnl=entry.realized_pnl,
            )
        )
    orders.sort(key=lambda order: order.decided_at or order.sent_at or "", reverse=True)
    return orders


def _orders_for_day(
    entries: Iterable[BridgeEntry], details: dict[str, _DecisionDetail], session_day: date
) -> OrderCounts:
    marker = session_day.isoformat()
    todays = [
        entry
        for entry in entries
        if (detail := details.get(entry.decision_id)) is not None and marker in detail.run_id
    ]
    realized = sum(
        (_clean_decimal(entry.realized_pnl) or Decimal("0") for entry in todays), Decimal("0")
    )
    return OrderCounts(
        sent=len([e for e in todays if e.parent_order_id is not None and e.status != "skipped"]),
        open=len([e for e in todays if e.status == "open"]),
        skipped=len([e for e in todays if e.status == "skipped"]),
        needs_attention=len([e for e in todays if e.status == "needs_attention"]),
        realized_pnl=str(realized.quantize(Decimal("0.01"))),
    )


def _screener_body(
    raw: object, *, session_date: object, observed_at: object
) -> dict[str, Any]:
    try:
        document = json.loads(str(raw))
    except (TypeError, ValueError):
        document = {}
    payload: dict[str, Any] = document if isinstance(document, dict) else {}
    rows = payload.get("rows")
    return {
        **payload,
        "schema": payload.get("schema", SCREENER_SCHEMA),
        "session_date": payload.get("session_date", str(session_date)),
        "observed_at": payload.get("observed_at", str(observed_at)),
        "rows": [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else [],
    }


# --------------------------------------------------------------------------
# Ledger reads
# --------------------------------------------------------------------------


# These reads run on every dashboard poll, so each must stay on an index as the
# ledgers grow. SQLite keeps CROSS JOIN operands in the written loop order:
# starting from the few runs whose id names the day keeps every later step an
# index search (the decision ledger has no run_id index and would otherwise be
# scanned in full). The candidate hop is exact because a trigger pins each
# decision's candidate to its own run's scanner.
_DAY_DECISIONS = """
    FROM autotrader_runs r
    CROSS JOIN autotrader_scanner_runs sr
    CROSS JOIN autotrader_candidates c
    CROSS JOIN autotrader_candidate_decisions d
    WHERE r.owner_id = ? AND instr(r.id, ?) > 0
      AND sr.run_id = r.id
      AND c.scanner_run_id = sr.id
      AND d.candidate_id = c.id
      AND d.run_id = r.id
"""
_SNAPSHOT_COLUMNS = "SELECT id, session_date, observed_at, payload_json FROM autotrader_screener_snapshots"
# (session_date, observed_at) is indexed; this order reads one index entry.
_NEWEST_SNAPSHOT_ORDER = "ORDER BY session_date DESC, observed_at DESC LIMIT 1"


async def _latest_snapshot_time(db: aiosqlite.Connection) -> datetime | None:
    async with db.execute(
        f"SELECT observed_at FROM autotrader_screener_snapshots {_NEWEST_SNAPSHOT_ORDER}"
    ) as cursor:
        row = await cursor.fetchone()
    return _parse_stored_time(row[0]) if row else None


async def _decision_counts(
    db: aiosqlite.Connection, *, owner_id: str, session_day: date
) -> dict[str, int]:
    async with db.execute(
        f"SELECT d.outcome, COUNT(*) {_DAY_DECISIONS} GROUP BY d.outcome ORDER BY d.outcome",
        (owner_id, session_day.isoformat()),
    ) as cursor:
        rows = await cursor.fetchall()
    return {str(outcome): int(count) for outcome, count in rows}


async def _decision_details(
    db: aiosqlite.Connection, *, owner_id: str, decision_ids: Sequence[str]
) -> dict[str, _DecisionDetail]:
    if not decision_ids:
        return {}
    async with db.execute(
        """
        SELECT d.id, d.run_id, d.created_at, d.payload_json, c.symbol, c.contract_json
        FROM autotrader_candidate_decisions d
        JOIN autotrader_runs r ON r.id = d.run_id
        JOIN autotrader_candidates c ON c.id = d.candidate_id
        WHERE r.owner_id = ? AND d.id IN (SELECT value FROM json_each(?))
        """,
        (owner_id, json.dumps(list(decision_ids))),
    ) as cursor:
        rows = await cursor.fetchall()
    return {
        str(decision_id): _DecisionDetail(
            run_id=str(run_id),
            created_at=_stored_time_text(created_at),
            symbol=str(symbol),
            primary_exchange=_primary_exchange(contract_json),
            intent=_intent_view(_load_decision(payload_json)),
        )
        for decision_id, run_id, created_at, payload_json, symbol, contract_json in rows
    }


def _bridge_entries_or_status(path: Path) -> tuple[list[BridgeEntry], BridgeFileState]:
    try:
        entries = read_bridge_state(path)
    except BridgeStateUnreadable:
        return [], "unreadable"
    if entries is None:
        return [], "missing"
    return entries, "ok"


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/status")
async def get_live_status(
    user: CurrentUser, clock: ClockDependency, state_path: BridgePathDependency
) -> LiveStatus:
    now = _require_utc(clock())
    session_day = new_york_date(now)
    entries, bridge_state = _bridge_entries_or_status(state_path)
    async with get_db() as db:
        observed = await _latest_snapshot_time(db)
        counts = await _decision_counts(db, owner_id=user.id, session_day=session_day)
        details = await _decision_details(
            db, owner_id=user.id, decision_ids=[entry.decision_id for entry in entries]
        )
    return LiveStatus(
        server_time_utc=_iso_utc(now),
        server_time_new_york=_iso_new_york(now),
        session_date=session_day,
        market=project_market(now),
        snapshot_observed_at=_iso_utc(observed) if observed is not None else None,
        snapshot_age_seconds=_age_seconds(now, observed),
        decisions_today=counts,
        decisions_total=sum(counts.values()),
        orders_today=_orders_for_day(entries, details, session_day),
        bridge_state=bridge_state,
    )


@router.get("/screener")
async def get_live_screener(clock: ClockDependency) -> dict[str, Any]:
    now = _require_utc(clock())
    session_day = new_york_date(now).isoformat()
    async with get_db() as db:
        async with db.execute(
            f"{_SNAPSHOT_COLUMNS} WHERE session_date = ? ORDER BY observed_at DESC LIMIT 1",
            (session_day,),
        ) as cursor:
            row = await cursor.fetchone()
        stale = False
        if row is None:
            async with db.execute(f"{_SNAPSHOT_COLUMNS} {_NEWEST_SNAPSHOT_ORDER}") as cursor:
                row = await cursor.fetchone()
            stale = row is not None
    if row is None:
        return {
            "schema": SCREENER_SCHEMA,
            "rows": [],
            "empty": True,
            "stale": False,
            "snapshot_age_seconds": None,
        }
    snapshot_id, snapshot_date, observed_at, payload_json = row
    return {
        **_screener_body(payload_json, session_date=snapshot_date, observed_at=observed_at),
        "snapshot_id": str(snapshot_id),
        "empty": False,
        "stale": stale,
        "snapshot_age_seconds": _age_seconds(now, _parse_stored_time(observed_at)),
    }


@router.get("/signals")
async def get_live_signals(
    user: CurrentUser,
    clock: ClockDependency,
    session_day: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> list[Signal]:
    day = session_day or new_york_date(clock())
    async with get_db() as db:
        async with db.execute(
            f"""
            SELECT d.id, d.run_id, d.outcome, d.payload_json, d.created_at,
                   c.symbol, c.contract_json
            {_DAY_DECISIONS}
            ORDER BY d.created_at DESC, d.id DESC
            LIMIT ?
            """,
            (user.id, day.isoformat(), limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [project_signal(row) for row in rows]


@router.get("/orders")
async def get_live_orders(user: CurrentUser, state_path: BridgePathDependency) -> list[BridgeOrder]:
    try:
        entries = read_bridge_state(state_path)
    except BridgeStateUnreadable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not entries:
        return []
    async with get_db() as db:
        details = await _decision_details(
            db, owner_id=user.id, decision_ids=[entry.decision_id for entry in entries]
        )
    return project_orders(entries, details)
