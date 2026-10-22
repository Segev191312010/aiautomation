"""Live R ledger: R for every real PAPER bracket, from the order bridge's own state.

The pinned bridge (``scripts_local/paper_bridge.py``) keeps one JSON state file
per New York session (``~/.local/share/autotrader-v1/bridge/<date>.json``) with
every bracket it managed: the intended entry and stop, the shares filled on
entry and on exit, their average prices and the exit reason. This module only
reads those files: regular files under a size limit, never through a symbolic
link, never written, never the dashboard's mirror. Nothing here imports or
talks to the bridge.

A closed bracket's R uses the risk the bracket was sized on, per share
(intended entry - stop), times the shares actually bought:

    gross R = (average exit - average entry) x exit shares / (risk per share x entry shares)

Commissions follow the IBKR Pro fixed model (``costs.DEFAULT_COST_MODEL``)
per order with its $1 minimum and 1% cap, one entry order and one exit order
(a bracket's exit legs are one OCA group). Slippage needs no model: the
average fill prices already carry it.

Open, unreconciled (needs_attention) and unfilled brackets are counted but
have no R; a bracket or file the ledger cannot trust is named, never guessed.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import re
from typing import Any

from trading.operations.paths import UserPaths
from trading.research.costs import COST_QUANTUM, DEFAULT_COST_MODEL, CostModel

logger = logging.getLogger("autotrader.shadow.research")

BRIDGE_STATE_DIRECTORY = "bridge"
# The bridge's STATE_SCHEMA family; the fields read here exist since v2.
STATE_SCHEMA_PREFIX = "paper-bridge-state."
# One session's state file name; ``read_submitted`` reads it.
STATE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
_MAX_STATE_BYTES = 2 * 1024 * 1024
_R_QUANTUM = Decimal("0.0001")
_ZERO = Decimal("0")


def bridge_state_directory(paths: UserPaths | None = None) -> Path:
    return (paths or UserPaths.current()).data_directory / BRIDGE_STATE_DIRECTORY


@dataclass(frozen=True, slots=True)
class LiveTrade:
    """One closed real bracket in R."""

    session_date: date
    decision_id: str
    strategy: str
    symbol: str
    shares: int
    risk_per_share: Decimal
    gross_pnl: Decimal
    commissions: Decimal
    gross_r: Decimal
    cost_r: Decimal
    exit_reason: str

    @property
    def net_r(self) -> Decimal:
        return self.gross_r - self.cost_r

    def payload(self) -> dict[str, object]:
        return {
            "date": self.session_date.isoformat(),
            "decision_id": self.decision_id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "shares": self.shares,
            "risk_per_share": str(self.risk_per_share),
            "gross_pnl": str(self.gross_pnl),
            "commissions": str(self.commissions),
            "gross_r": str(self.gross_r),
            "cost_r": str(self.cost_r),
            "net_r": str(self.net_r),
            "exit_reason": self.exit_reason,
        }


@dataclass(frozen=True, slots=True)
class LiveBracket:
    """Every real bracket the bridge sent (any status), by session: when an identity
    first ordered, and which brackets need attention or could not be read."""

    session_date: date
    decision_id: str
    strategy: str
    status: str


@dataclass(frozen=True, slots=True)
class StrategyLiveR:
    strategy: str
    trades: int
    wins: int
    gross_r: Decimal
    cost_r: Decimal
    open: int
    attention: int
    unfilled: int

    @property
    def net_r(self) -> Decimal:
        return self.gross_r - self.cost_r

    @property
    def expectancy_r(self) -> Decimal | None:
        return None if not self.trades else self.net_r / self.trades


@dataclass(frozen=True, slots=True)
class LiveLedger:
    trades: tuple[LiveTrade, ...] = ()
    sessions: tuple[date, ...] = ()
    open: Mapping[str, int] = field(default_factory=dict)
    attention: Mapping[str, int] = field(default_factory=dict)
    unfilled: Mapping[str, int] = field(default_factory=dict)
    skipped: int = 0
    # File names or "<file>:<decision id>" the ledger could not trust.
    unreadable: tuple[str, ...] = ()
    # Every bracket with a quantity, dated (status "unreadable" when it could not be read).
    brackets: tuple[LiveBracket, ...] = ()

    def since(self, first: date) -> LiveLedger:
        """The same ledger limited to sessions from ``first`` on (open counts are current)."""
        return LiveLedger(
            trades=tuple(trade for trade in self.trades if trade.session_date >= first),
            sessions=tuple(day for day in self.sessions if day >= first),
            open=self.open,
            attention=self.attention,
            unfilled=self.unfilled,
            skipped=self.skipped,
            unreadable=self.unreadable,
            brackets=tuple(item for item in self.brackets if item.session_date >= first),
        )

    def by_strategy(self) -> tuple[StrategyLiveR, ...]:
        names = sorted(
            {trade.strategy for trade in self.trades}
            | set(self.open)
            | set(self.attention)
            | set(self.unfilled)
        )
        rows = []
        for name in names:
            mine = [trade for trade in self.trades if trade.strategy == name]
            rows.append(
                StrategyLiveR(
                    strategy=name,
                    trades=len(mine),
                    wins=sum(1 for trade in mine if trade.net_r > 0),
                    gross_r=sum((trade.gross_r for trade in mine), _ZERO),
                    cost_r=sum((trade.cost_r for trade in mine), _ZERO),
                    open=self.open.get(name, 0),
                    attention=self.attention.get(name, 0),
                    unfilled=self.unfilled.get(name, 0),
                )
            )
        return tuple(rows)

    def payload(self) -> dict[str, object]:
        return {
            "basis": "pinned PAPER bridge state files, read-only; IBKR Pro fixed commissions",
            "cost_model": DEFAULT_COST_MODEL.model_id,
            "sessions": [day.isoformat() for day in self.sessions],
            "trades": [trade.payload() for trade in self.trades],
            "open": dict(self.open),
            "needs_attention": dict(self.attention),
            "unfilled": dict(self.unfilled),
            "skipped": self.skipped,
            "unreadable": list(self.unreadable),
        }


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} is not finite")
    return result


def _whole(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} is not a share count")
    return value


def _read_document(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("not a regular file")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_STATE_BYTES + 1)
    if len(raw) > _MAX_STATE_BYTES:
        raise ValueError("larger than a bridge state")
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("not a state document")
    schema = document.get("schema")
    if not isinstance(schema, str) or not schema.startswith(STATE_SCHEMA_PREFIX):
        raise ValueError("not a bridge state schema")
    return document


def read_submitted(path: Path) -> tuple[date, dict[str, Any]]:
    """One session file (named ``STATE_FILE``): its session date and its ``submitted`` map.

    The same checks for every reader of the bridge's state (this ledger, the v7c
    report): a regular file under the size limit, the bridge's schema, its own
    session. Raises ``ValueError`` (or ``OSError``, ``UnicodeDecodeError``) when
    the file cannot be trusted.
    """
    match = STATE_FILE.match(path.name)
    if match is None:
        raise ValueError("not a session state file name")
    session_date = date.fromisoformat(match.group(1))
    document = _read_document(path)
    if document.get("session_date", session_date.isoformat()) != session_date.isoformat():
        raise ValueError("holds another session")
    submitted = document.get("submitted", {})
    if not isinstance(submitted, dict):
        raise ValueError("no submitted map")
    return session_date, submitted


def _trade(
    session_date: date, decision_id: str, item: Mapping[str, Any], *, cost_model: CostModel
) -> LiveTrade:
    filled = _whole(item.get("filled"), "filled")
    exit_filled = _whole(item.get("exit_filled"), "exit_filled")
    if exit_filled != filled or filled == 0:
        raise ValueError("a closed bracket sells exactly the shares it bought")
    entry = _decimal(item.get("entry"), "entry")
    stop = _decimal(item.get("stop"), "stop")
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        raise ValueError("the stop must sit below the entry")
    avg_entry = _decimal(item.get("avg_entry"), "avg_entry")
    avg_exit = _decimal(item.get("avg_exit"), "avg_exit")
    if avg_entry <= 0 or avg_exit <= 0:
        raise ValueError("fill prices must be positive")
    gross_pnl = (avg_exit - avg_entry) * exit_filled
    risk = risk_per_share * filled
    commissions = cost_model.order_commission(shares=filled, price=avg_entry) + cost_model.order_commission(
        shares=exit_filled, price=avg_exit
    )
    strategy = str(item.get("strategy_name") or item.get("strategy") or "unknown").strip()
    return LiveTrade(
        session_date=session_date,
        decision_id=decision_id,
        strategy=strategy,
        symbol=str(item.get("symbol") or "?"),
        shares=filled,
        risk_per_share=risk_per_share,
        gross_pnl=gross_pnl,
        commissions=commissions,
        gross_r=(gross_pnl / risk).quantize(_R_QUANTUM, rounding=ROUND_HALF_EVEN),
        cost_r=(commissions / risk).quantize(COST_QUANTUM, rounding=ROUND_CEILING),
        exit_reason=str(item.get("exit_reason") or "unknown"),
    )


def read_live_ledger(
    directory: Path | None = None,
    *,
    cost_model: CostModel = DEFAULT_COST_MODEL,
) -> LiveLedger:
    """Every session file's brackets in R; a missing directory is an empty ledger."""
    folder = bridge_state_directory() if directory is None else directory
    if folder.is_symlink() or not folder.is_dir():
        return LiveLedger()
    trades: list[LiveTrade] = []
    sessions: list[date] = []
    open_: dict[str, int] = defaultdict(int)
    attention: dict[str, int] = defaultdict(int)
    unfilled: dict[str, int] = defaultdict(int)
    skipped = 0
    unreadable: list[str] = []
    brackets: list[LiveBracket] = []
    for path in sorted(folder.iterdir()):
        if STATE_FILE.match(path.name) is None:
            continue
        try:
            session_date, submitted = read_submitted(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            logger.warning("bridge state %s not read: %s", path.name, exc)
            unreadable.append(path.name)
            continue
        sessions.append(session_date)
        for decision_id, item in sorted(submitted.items()):
            if not isinstance(item, dict) or "qty" not in item or item.get("skipped"):
                skipped += 1  # legacy or skipped entries never traded
                continue
            strategy = str(item.get("strategy_name") or item.get("strategy") or "unknown").strip()
            status = str(item.get("status") or "")
            try:
                filled = _whole(item.get("filled", 0), "filled")
            except ValueError:
                unreadable.append(f"{path.name}:{decision_id}")
                brackets.append(LiveBracket(session_date, str(decision_id), strategy, "unreadable"))
                continue
            if status == "closed":
                try:
                    trades.append(_trade(session_date, str(decision_id), item, cost_model=cost_model))
                except ValueError as exc:
                    logger.warning("bracket %s in %s not read: %s", decision_id, path.name, exc)
                    unreadable.append(f"{path.name}:{decision_id}")
                    status = "unreadable"
            elif status == "needs_attention":
                attention[strategy] += 1
            elif filled > 0:
                open_[strategy] += 1
            elif status in ("cancelled", "sent", "pending"):
                unfilled[strategy] += 1
            else:
                skipped += 1
                continue
            brackets.append(LiveBracket(session_date, str(decision_id), strategy, status))
    return LiveLedger(
        trades=tuple(sorted(trades, key=lambda trade: (trade.session_date, trade.decision_id))),
        sessions=tuple(sorted(sessions)),
        open=dict(open_),
        attention=dict(attention),
        unfilled=dict(unfilled),
        skipped=skipped,
        unreadable=tuple(unreadable),
        brackets=tuple(brackets),
    )


def _r(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN):+}R"


def ledger_lines(ledger: LiveLedger, *, week_start: date | None = None) -> list[str]:
    """Plain text for the weekly summary: per strategy, since the first session."""
    if not ledger.sessions and not ledger.unreadable:
        return ["no bridge sessions recorded"]
    lines = []
    if ledger.sessions:
        first, last = ledger.sessions[0], ledger.sessions[-1]
        lines.append(f"{len(ledger.sessions)} sessions {first.isoformat()}..{last.isoformat()}")
    for row in ledger.by_strategy():
        text = f"{row.strategy}: {row.trades} trade{'' if row.trades == 1 else 's'}"
        if row.trades:
            text += (
                f", net {_r(row.net_r)} (gross {_r(row.gross_r)}, costs {_r(row.cost_r).lstrip('+')}),"
                f" {_r(row.expectancy_r)}/trade, {row.wins}/{row.trades} won"
            )
        counts = ((row.open, "open"), (row.attention, "need attention"), (row.unfilled, "unfilled"))
        extras = [f"{count} {label}" for count, label in counts if count]
        if extras:
            text += "; " + ", ".join(extras)
        lines.append(text)
    if week_start is not None:
        week = ledger.since(week_start)
        net = sum((trade.net_r for trade in week.trades), _ZERO)
        lines.append(f"this week: {len(week.trades)} trades, net {_r(net)}")
    if ledger.unreadable:
        lines.append("not read (see the research log): " + ", ".join(ledger.unreadable[:5]))
    return lines


__all__ = [
    "BRIDGE_STATE_DIRECTORY",
    "STATE_FILE",
    "STATE_SCHEMA_PREFIX",
    "LiveBracket",
    "LiveLedger",
    "LiveTrade",
    "StrategyLiveR",
    "bridge_state_directory",
    "ledger_lines",
    "read_live_ledger",
    "read_submitted",
]
