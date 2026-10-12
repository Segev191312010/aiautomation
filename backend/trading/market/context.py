"""Market context: the index, breadth and sector picture of one screener sweep.

Every live-screener sweep snapshots fifteen index and sector ETFs and stores
one ``market_context.v1`` document under the ``market`` key of its
``screener.v1`` payload. Percentages are rounded to hundredths before the
regime is classified, so the stored numbers always re-derive the stored
regime; a missing input can only ever yield ``neutral`` because missing data
must never block trading by itself.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import json
import logging
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from db.core import get_db
from trading.market.snapshots import MarketSnapshot

logger = logging.getLogger("autotrader.shadow.screener")

MARKET_CONTEXT_SCHEMA = "market_context.v1"
CONTEXT_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("SPY", "ARCA"),
    ("QQQ", "NASDAQ"),
    ("IWM", "ARCA"),
    ("DIA", "ARCA"),
    ("XLK", "ARCA"),
    ("XLV", "ARCA"),
    ("XLF", "ARCA"),
    ("XLE", "ARCA"),
    ("XLY", "ARCA"),
    ("XLP", "ARCA"),
    ("XLI", "ARCA"),
    ("XLU", "ARCA"),
    ("XLB", "ARCA"),
    ("XLRE", "ARCA"),
    ("XLC", "ARCA"),
)
CONTEXT_SYMBOL_SET = frozenset(symbol for symbol, _ in CONTEXT_SYMBOLS)
INDEX_SYMBOLS = ("SPY", "QQQ", "IWM", "DIA")
SECTOR_NAMES = {
    "XLK": "Technology",
    "XLV": "Health Care",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication",
}

Regime = Literal["risk_on", "neutral", "risk_off"]
REGIMES: tuple[Regime, ...] = ("risk_on", "neutral", "risk_off")
# market_context.v1 thresholds; changing any of them requires a new schema
# version because stored documents are checked against them on read.
RISK_OFF_SPY_CHANGE_PCT = Decimal("-0.75")
RISK_OFF_QQQ_CHANGE_PCT = Decimal("-1.0")
RISK_OFF_BREADTH_BELOW_PCT = Decimal("40")
RISK_ON_SPY_CHANGE_PCT = Decimal("0.30")
RISK_ON_BREADTH_ABOVE_PCT = Decimal("55")
SECTOR_RANK_SIZE = 3

_WATCHLIST_SOURCE = "watchlist"
_HUNDREDTH = Decimal("0.01")
_TENTH = Decimal("0.1")
_HUNDRED = Decimal("100")
_REGIME_LABELS = {"risk_on": "Risk-on", "neutral": "Neutral", "risk_off": "Risk-off"}
_PCT_FIELDS = (
    "spy_change_pct",
    "qqq_change_pct",
    "iwm_change_pct",
    "dia_change_pct",
    "breadth_up_pct",
    "median_change_pct",
)

SectorChange = tuple[str, Decimal]


class MarketContextError(ValueError):
    """A market context violates the ``market_context.v1`` contract."""


# --- pure computation ----------------------------------------------------------------


def _round_pct(value: Decimal) -> Decimal:
    rounded = value.quantize(_HUNDREDTH, rounding=ROUND_HALF_UP)
    # A tiny negative rounds to -0.00; zero carries no sign.
    return rounded.copy_abs() if rounded.is_zero() else rounded


def snapshot_change_pct(snapshot: MarketSnapshot) -> Decimal | None:
    """Change vs the prior close from the last trade, else the bid/ask midpoint.

    Pre-market this is the move since yesterday's close, which is exactly
    what the context reports before 09:30 ET.
    """
    price = snapshot.reference_price
    if price is None:
        return None
    return _round_pct((price - snapshot.prior_close) / snapshot.prior_close * _HUNDRED)


def classify_regime(
    *,
    spy_change_pct: Decimal | None,
    qqq_change_pct: Decimal | None,
    breadth_up_pct: Decimal | None,
) -> Regime:
    """risk_off: (SPY <= -0.75% or QQQ <= -1.0%) and breadth < 40%.

    risk_on: SPY >= +0.30% and breadth > 55%. Anything else, including any
    missing or non-finite input, is neutral.
    """
    spy, qqq, breadth = spy_change_pct, qqq_change_pct, breadth_up_pct
    if spy is None or qqq is None or breadth is None:
        return "neutral"
    for value in (spy, qqq, breadth):
        if not isinstance(value, Decimal):
            raise TypeError("regime inputs must be Decimal or None")
    if not (spy.is_finite() and qqq.is_finite() and breadth.is_finite()):
        return "neutral"
    if (
        spy <= RISK_OFF_SPY_CHANGE_PCT or qqq <= RISK_OFF_QQQ_CHANGE_PCT
    ) and breadth < RISK_OFF_BREADTH_BELOW_PCT:
        return "risk_off"
    if spy >= RISK_ON_SPY_CHANGE_PCT and breadth > RISK_ON_BREADTH_ABOVE_PCT:
        return "risk_on"
    return "neutral"


def _row_change(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise MarketContextError(f"change_pct must be a decimal, not {type(value).__name__}")
    try:
        change = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as exc:
        raise MarketContextError(f"change_pct {value!r} is not a decimal") from exc
    if not change.is_finite():
        raise MarketContextError(f"change_pct {value!r} is not finite")
    return _round_pct(change)


def watchlist_changes(rows: Sequence[Mapping[str, object]]) -> tuple[Decimal, ...]:
    """Rounded change of every watchlist row with a known change.

    IBKR scan additions and unquoted rows are not part of the owner's
    universe, so they never move breadth.
    """
    changes: list[Decimal] = []
    for row in rows:
        if row.get("source") != _WATCHLIST_SOURCE:
            continue
        change = _row_change(row.get("change_pct"))
        if change is not None:
            changes.append(change)
    return tuple(changes)


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return _round_pct(ordered[middle])
    return _round_pct((ordered[middle - 1] + ordered[middle]) / 2)


def rank_sectors(
    changes: Mapping[str, Decimal],
) -> tuple[tuple[SectorChange, ...], tuple[SectorChange, ...]]:
    """Best three and worst three sectors by change; ties order by name.

    With fewer than six sectors quoted the quoted ones are split (leaders
    take the odd one), so a sector is never both a leader and a laggard.
    """
    sectors = [
        (SECTOR_NAMES[symbol], change)
        for symbol, change in changes.items()
        if symbol in SECTOR_NAMES
    ]
    best_first = sorted(sectors, key=lambda item: (-item[1], item[0]))
    lead = min(SECTOR_RANK_SIZE, (len(best_first) + 1) // 2)
    rest = sorted(best_first[lead:], key=lambda item: (item[1], item[0]))
    return tuple(best_first[:lead]), tuple(rest[:SECTOR_RANK_SIZE])


def _summary_pct(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    rounded = value.quantize(_TENTH, rounding=ROUND_HALF_UP)
    if rounded.is_zero():
        return "0.0%"
    return f"{rounded:+f}%"


def _summary_sector(entries: Sequence[SectorChange]) -> str:
    if not entries:
        return "n/a"
    name, change = entries[0]
    return f"{name} {_summary_pct(change)}"


def market_summary(
    *,
    regime: Regime,
    spy_change_pct: Decimal | None,
    qqq_change_pct: Decimal | None,
    breadth_up_pct: Decimal | None,
    advancers: int,
    decliners: int,
    sector_leaders: Sequence[SectorChange],
    sector_laggards: Sequence[SectorChange],
) -> str:
    """One plain line; unknown values read ``n/a`` instead of disappearing."""
    breadth = "breadth n/a" if breadth_up_pct is None else f"{advancers}↑/{decliners}↓"
    return " · ".join(
        (
            _REGIME_LABELS[regime],
            f"S&P {_summary_pct(spy_change_pct)}",
            f"Nasdaq {_summary_pct(qqq_change_pct)}",
            breadth,
            f"Leading: {_summary_sector(sector_leaders)}",
            f"Lagging: {_summary_sector(sector_laggards)}",
        )
    )


# --- the context -------------------------------------------------------------------------


def _utc_text(value: datetime) -> str:
    """Fixed-precision UTC text, matching the screener's stored ``observed_at``."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _sector_payload(entries: Sequence[SectorChange]) -> list[list[str]]:
    return [[name, format(change, "f")] for name, change in entries]


def _sector_values(entries: Sequence[tuple[str, str]]) -> tuple[SectorChange, ...]:
    return tuple((name, Decimal(text)) for name, text in entries)


def _sector_entries(field: str, raw: object) -> tuple[SectorChange, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise MarketContextError(f"{field} must be a sequence of (sector, change) pairs")
    if len(raw) > SECTOR_RANK_SIZE:
        raise MarketContextError(f"{field} holds at most {SECTOR_RANK_SIZE} sectors")
    entries: list[SectorChange] = []
    for entry in raw:
        if isinstance(entry, (str, bytes)) or not isinstance(entry, Sequence) or len(entry) != 2:
            raise MarketContextError(f"{field} entries must be (sector, change) pairs")
        name, change = entry
        if name not in SECTOR_NAMES.values():
            raise MarketContextError(f"{field} names unknown sector {name!r}")
        if not isinstance(change, Decimal) or not change.is_finite():
            raise MarketContextError(f"{field} changes must be finite Decimals")
        entries.append((name, change))
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class MarketContext:
    """The market picture of one sweep; immutable and JSON round-trippable.

    Construction validates shape (aware time, known regime and sectors,
    finite Decimals, one-line summary). ``from_payload`` additionally
    re-derives the regime from the stored numbers and rejects a mismatch.
    """

    session_date: date
    observed_at: datetime
    regime: Regime
    spy_change_pct: Decimal | None
    qqq_change_pct: Decimal | None
    iwm_change_pct: Decimal | None
    dia_change_pct: Decimal | None
    breadth_up_pct: Decimal | None
    advancers: int
    decliners: int
    median_change_pct: Decimal | None
    sector_leaders: tuple[SectorChange, ...]
    sector_laggards: tuple[SectorChange, ...]
    summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise MarketContextError("session_date must be a date")
        observed = self.observed_at
        if (
            not isinstance(observed, datetime)
            or observed.tzinfo is None
            or observed.utcoffset() is None
        ):
            raise MarketContextError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", observed.astimezone(timezone.utc))
        if self.regime not in REGIMES:
            raise MarketContextError(f"unknown regime {self.regime!r}")
        for name in _PCT_FIELDS:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise MarketContextError(f"{name} must be a finite Decimal or None")
        breadth = self.breadth_up_pct
        if breadth is not None and not Decimal("0") <= breadth <= _HUNDRED:
            raise MarketContextError("breadth_up_pct must be within 0..100")
        for name in ("advancers", "decliners"):
            count = getattr(self, name)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise MarketContextError(f"{name} must be a non-negative integer")
        for name in ("sector_leaders", "sector_laggards"):
            object.__setattr__(self, name, _sector_entries(name, getattr(self, name)))
        summary = self.summary
        if not isinstance(summary, str) or not summary.strip() or summary.splitlines() != [summary]:
            raise MarketContextError("summary must be one non-empty line")

    def to_payload(self) -> dict[str, object]:
        """JSON-native ``market_context.v1`` document; Decimals are strings."""
        return {
            "schema": MARKET_CONTEXT_SCHEMA,
            "session_date": self.session_date.isoformat(),
            "observed_at": _utc_text(self.observed_at),
            "regime": self.regime,
            "spy_change_pct": _decimal_text(self.spy_change_pct),
            "qqq_change_pct": _decimal_text(self.qqq_change_pct),
            "iwm_change_pct": _decimal_text(self.iwm_change_pct),
            "dia_change_pct": _decimal_text(self.dia_change_pct),
            "breadth_up_pct": _decimal_text(self.breadth_up_pct),
            "advancers": self.advancers,
            "decliners": self.decliners,
            "median_change_pct": _decimal_text(self.median_change_pct),
            "sector_leaders": _sector_payload(self.sector_leaders),
            "sector_laggards": _sector_payload(self.sector_laggards),
            "summary": self.summary,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> MarketContext:
        """Validate a stored document; any violation raises ``MarketContextError``."""
        if not isinstance(payload, Mapping):
            raise MarketContextError(f"{MARKET_CONTEXT_SCHEMA} payload must be an object")
        try:
            document = _MarketContextDocument.model_validate(dict(payload))
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"]) or "payload"
            raise MarketContextError(
                f"invalid {MARKET_CONTEXT_SCHEMA} payload at {location}: {first['msg']}"
            ) from exc

        def number(text: str | None) -> Decimal | None:
            return None if text is None else Decimal(text)

        try:
            context = cls(
                session_date=date.fromisoformat(document.session_date),
                observed_at=datetime.fromisoformat(document.observed_at),
                regime=document.regime,
                spy_change_pct=number(document.spy_change_pct),
                qqq_change_pct=number(document.qqq_change_pct),
                iwm_change_pct=number(document.iwm_change_pct),
                dia_change_pct=number(document.dia_change_pct),
                breadth_up_pct=number(document.breadth_up_pct),
                advancers=document.advancers,
                decliners=document.decliners,
                median_change_pct=number(document.median_change_pct),
                sector_leaders=_sector_values(document.sector_leaders),
                sector_laggards=_sector_values(document.sector_laggards),
                summary=document.summary,
            )
        except (ValueError, InvalidOperation) as exc:
            raise MarketContextError(f"invalid {MARKET_CONTEXT_SCHEMA} payload: {exc}") from exc
        derived = classify_regime(
            spy_change_pct=context.spy_change_pct,
            qqq_change_pct=context.qqq_change_pct,
            breadth_up_pct=context.breadth_up_pct,
        )
        if derived != context.regime:
            # A regime its own numbers contradict would mislead every consumer.
            raise MarketContextError(
                f"stored regime {context.regime!r} contradicts its inputs ({derived!r})"
            )
        return context


_DecimalText = Annotated[str, StringConstraints(pattern=r"^-?\d+(\.\d+)?$")]


class _MarketContextDocument(BaseModel):
    """The stored JSON shape; field semantics are checked by ``MarketContext``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["market_context.v1"] = Field(alias="schema")
    session_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    observed_at: str
    regime: Regime
    spy_change_pct: _DecimalText | None
    qqq_change_pct: _DecimalText | None
    iwm_change_pct: _DecimalText | None
    dia_change_pct: _DecimalText | None
    breadth_up_pct: _DecimalText | None
    advancers: int = Field(ge=0, strict=True)
    decliners: int = Field(ge=0, strict=True)
    median_change_pct: _DecimalText | None
    sector_leaders: tuple[tuple[str, _DecimalText], ...] = Field(max_length=SECTOR_RANK_SIZE)
    sector_laggards: tuple[tuple[str, _DecimalText], ...] = Field(max_length=SECTOR_RANK_SIZE)
    summary: str = Field(min_length=1, max_length=500)


def build_market_context(
    *,
    session_date: date,
    observed_at: datetime,
    etf_snapshots: Mapping[str, MarketSnapshot],
    watchlist_rows: Sequence[Mapping[str, object]],
) -> MarketContext:
    """Deterministic context from the ETF snapshots and the sweep's rows.

    ``etf_snapshots`` is keyed by context symbol; absent ETFs leave their
    values unknown. ``watchlist_rows`` are ``screener.v1`` rows: only rows
    with ``source == "watchlist"`` and a non-null ``change_pct`` count.
    """
    changes: dict[str, Decimal] = {}
    for symbol, snapshot in etf_snapshots.items():
        if symbol not in CONTEXT_SYMBOL_SET:
            continue
        change = snapshot_change_pct(snapshot)
        if change is not None:
            changes[symbol] = change
    row_changes = watchlist_changes(watchlist_rows)
    advancers = sum(1 for change in row_changes if change > 0)
    decliners = sum(1 for change in row_changes if change < 0)
    breadth = (
        _round_pct(Decimal(advancers) / Decimal(len(row_changes)) * _HUNDRED)
        if row_changes
        else None
    )
    leaders, laggards = rank_sectors(changes)
    spy, qqq = changes.get("SPY"), changes.get("QQQ")
    regime = classify_regime(spy_change_pct=spy, qqq_change_pct=qqq, breadth_up_pct=breadth)
    return MarketContext(
        session_date=session_date,
        observed_at=observed_at,
        regime=regime,
        spy_change_pct=spy,
        qqq_change_pct=qqq,
        iwm_change_pct=changes.get("IWM"),
        dia_change_pct=changes.get("DIA"),
        breadth_up_pct=breadth,
        advancers=advancers,
        decliners=decliners,
        median_change_pct=_median(row_changes),
        sector_leaders=leaders,
        sector_laggards=laggards,
        summary=market_summary(
            regime=regime,
            spy_change_pct=spy,
            qqq_change_pct=qqq,
            breadth_up_pct=breadth,
            advancers=advancers,
            decliners=decliners,
            sector_leaders=leaders,
            sector_laggards=laggards,
        ),
    )


# --- persistence ---------------------------------------------------------------------------


async def latest_market_context(
    session_date: date,
    *,
    now: datetime,
    max_age: timedelta = timedelta(minutes=5),
) -> MarketContext | None:
    """The newest sweep's context for ``session_date`` as of ``now``, or None.

    None means unknown: no sweep yet, the newest sweep is older than
    ``max_age`` or carries no ``market`` key, or its document is unusable
    (logged as an error). Sweeps observed after ``now`` are never read, so
    a replayed ``now`` cannot see the future.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_age <= timedelta(0):
        raise ValueError("max_age must be positive")
    async with get_db() as db:
        async with db.execute(
            """SELECT id, json_type(payload_json, '$.market'),
                      json_extract(payload_json, '$.market')
               FROM autotrader_screener_snapshots
               WHERE session_date = ? AND observed_at <= ?
               ORDER BY observed_at DESC, id DESC LIMIT 1""",
            (session_date.isoformat(), _utc_text(now)),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None or row[1] is None or row[1] == "null":
        return None
    snapshot_id = str(row[0])
    try:
        if row[1] != "object":
            raise MarketContextError(f"market is a JSON {row[1]}, not an object")
        context = MarketContext.from_payload(json.loads(str(row[2])))
    except (ValueError, MarketContextError) as exc:
        logger.error("market context of screener snapshot %s is unusable: %s", snapshot_id, exc)
        return None
    if context.session_date != session_date:
        logger.error(
            "market context of screener snapshot %s belongs to %s, not %s",
            snapshot_id,
            context.session_date.isoformat(),
            session_date.isoformat(),
        )
        return None
    if context.observed_at > now or now - context.observed_at > max_age:
        return None
    return context


__all__ = [
    "CONTEXT_SYMBOLS",
    "CONTEXT_SYMBOL_SET",
    "INDEX_SYMBOLS",
    "MARKET_CONTEXT_SCHEMA",
    "REGIMES",
    "RISK_OFF_BREADTH_BELOW_PCT",
    "RISK_OFF_QQQ_CHANGE_PCT",
    "RISK_OFF_SPY_CHANGE_PCT",
    "RISK_ON_BREADTH_ABOVE_PCT",
    "RISK_ON_SPY_CHANGE_PCT",
    "SECTOR_NAMES",
    "SECTOR_RANK_SIZE",
    "MarketContext",
    "MarketContextError",
    "Regime",
    "build_market_context",
    "classify_regime",
    "latest_market_context",
    "market_summary",
    "rank_sectors",
    "snapshot_change_pct",
    "watchlist_changes",
]
