"""Owner manual orders from Telegram: deterministic parser, validator and ledger.

Safety model (owner decision 2026-10-12: any order, sent immediately, no
confirmation step):

* Orders come ONLY from :func:`parse_order_text`, a strict deterministic
  parser of the owner's literal text. A language model never produces an
  order; :func:`queue_order` re-parses the stored raw text and refuses any
  order that does not derive from it.
* :func:`validate` applies the order rules with named rejection codes before
  an order is queued; the bridge applies the same rules again right before
  ``placeOrder`` through :func:`revalidate_claimed`.
* Exits (SELL, SELL_STOP, CLOSE, CLOSE_ALL, CANCEL, CANCEL_ALL) are never
  blocked by the kill switch, the rate limit or the exposure budget. No sell
  may exceed the owner's unreserved shares (no naked shorts, ever).
* The ledger is append-only with an immutable intent and a monotonic status
  (enforced by migration-12 triggers and again here). A row is claimed in one
  IMMEDIATE transaction before it is sent, expires 90 s after the message, and
  a ``sending`` row that IBKR does not know after a restart becomes ``unknown``
  and is never resent.
* A buy must carry its protective stop (owner decision D5, 2026-10-18, v7c):
  ``/buy 100 AAPL at 180 stop 175``, ``/buy $5k NVDA stop 170``. The stop is
  below the entry (``stop_not_below_entry``) and the entry's risk, quantity x
  (entry - stop), is at most 1 % of net liquidation, the bot's own per-order
  ceiling (``manual_entry_risk_over_1pct``). A buy without one is refused by
  the parser (``missing_entry_stop``), by ``revalidate_claimed`` for an older
  row, and by migration 17's trigger. Exits are never subject to any of this.
  Until migration 17 is applied a buy cannot be recorded
  (``manual_schema_pending``); every other order still works.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
import json
import os
import re
from typing import Literal, get_args
import unicodedata
from uuid import uuid4

import aiosqlite

from db.core import get_db, transaction
from trading.scheduler.sessions import MARKET_TIMEZONE, market_session


UTC = timezone.utc

Action = Literal["BUY", "SELL", "SELL_STOP", "CLOSE", "CLOSE_ALL", "CANCEL", "CANCEL_ALL"]
OrderType = Literal["MKT", "LMT", "STP"]
Fraction = Literal["all", "half"]
Source = Literal["command", "text"]
OrderStatus = Literal[
    "queued", "sending", "sent", "partially_filled", "filled",
    "cancelled", "rejected", "expired", "unknown", "error",
]
OrderKind = Literal["order", "bot_flatten", "close_all", "cancel", "cancel_all"]
Side = Literal["BUY", "SELL"]
RefusalCode = Literal[
    "empty", "too_long", "not_an_order", "question", "kill_switch_command",
    "negation", "conditional", "short_selling", "unsupported_instrument",
    "unsupported_order_type", "multiple_actions", "unknown_verb", "closeall_exact",
    "multiple_symbols", "multiple_quantities", "fractional_shares", "ambiguous_amount",
    "missing_symbol", "missing_quantity", "missing_price", "bad_quantity", "bad_price",
    "word_ticker_needs_cashtag", "ticker_case", "unknown_word", "bad_cancel_ref",
    "missing_entry_stop", "stop_not_below_entry",
]
RejectCode = Literal[
    "not_paper", "stale_message", "market_closed", "bridge_offline", "unknown_symbol",
    "bot_holds_symbol", "kill_switch", "account_unavailable", "no_fresh_quote",
    "spread_too_wide", "price_out_of_band", "invalid_tick", "stop_above_bid",
    "amount_too_small", "order_too_large", "manual_exposure_cap", "rate_limited",
    "insufficient_shares", "state_changed",
    "missing_entry_stop", "stop_not_below_entry", "manual_entry_risk_over_1pct",
]
CancelResult = Literal["applied", "no_active_order", "ambiguous_order_ref"]

ACTIONS: frozenset[str] = frozenset(get_args(Action))
ORDER_STATUSES: frozenset[str] = frozenset(get_args(OrderStatus))
REFUSAL_CODES: frozenset[str] = frozenset(get_args(RefusalCode))
REJECT_CODES: frozenset[str] = frozenset(get_args(RejectCode))
EXIT_ACTIONS: frozenset[str] = frozenset(
    {"SELL", "SELL_STOP", "CLOSE", "CLOSE_ALL", "CANCEL", "CANCEL_ALL"}
)
CANCEL_ACTIONS: frozenset[str] = frozenset({"CANCEL", "CANCEL_ALL"})
SELL_SIDE_ACTIONS: frozenset[str] = frozenset({"SELL", "SELL_STOP", "CLOSE"})
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"filled", "cancelled", "rejected", "expired", "error"}
)
# Rows that may still reach, or already sit at, IBKR. ``unknown`` counts as
# live for every reservation so that an unreconciled order is never ignored.
LIVE_STATUSES: frozenset[str] = frozenset(
    {"queued", "sending", "sent", "partially_filled", "unknown"}
)
CANCELLABLE_STATUSES: frozenset[str] = frozenset(
    {"queued", "sending", "sent", "partially_filled"}
)
# Allowed status changes besides keeping the same status. ``queued -> sending``
# is deliberately absent: only claim_next_order may perform it.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"cancelled", "expired", "rejected", "error"}),
    "sending": frozenset(
        {"sent", "partially_filled", "filled", "cancelled", "rejected", "error", "unknown"}
    ),
    "sent": frozenset(
        {"partially_filled", "filled", "cancelled", "rejected", "error", "unknown"}
    ),
    "partially_filled": frozenset({"filled", "cancelled", "unknown"}),
    "unknown": frozenset(
        {"sent", "partially_filled", "filled", "cancelled", "rejected", "error"}
    ),
    "filled": frozenset(),
    "cancelled": frozenset(),
    "rejected": frozenset(),
    "expired": frozenset(),
    "error": frozenset(),
}

MAX_MESSAGE_AGE = timedelta(seconds=60)
MAX_MESSAGE_CLOCK_SKEW = timedelta(seconds=10)
ORDER_TTL = timedelta(seconds=90)
MAX_BRIDGE_HEARTBEAT_AGE_S = 20.0
MARKET_QUOTE_MAX_AGE = timedelta(seconds=5)
REFERENCE_QUOTE_MAX_AGE = timedelta(seconds=60)
QUOTE_CLOCK_SKEW = timedelta(seconds=2)
MAX_SPREAD_FRACTION = Decimal("0.01")
PRICE_BAND_FRACTION = Decimal("0.10")
MAX_ORDER_FRACTION_OF_NET_LIQ = Decimal("0.25")
MAX_MANUAL_EXPOSURE_FRACTION_OF_NET_LIQ = Decimal("0.50")
# D5 (v7c): a buy's risk, quantity x (entry - its stop), may be at most this
# share of net liquidation: the bot's own per-order ceiling (the bridge's
# MAX_ORDER_RISK_PCT = 1). The account-level 3 % day budget is the bridge's.
MAX_MANUAL_ENTRY_RISK_FRACTION_OF_NET_LIQ = Decimal("0.01")
# The word that introduces a buy's protective stop; it must be written last.
ENTRY_STOP_WORD = "stop"
# Migration 17's column; a database without it (v17 pending) records no buy.
ENTRY_STOP_COLUMN = "entry_stop_price"
MANUAL_SCHEMA_PENDING = "manual_schema_pending"
RATE_LIMIT_MAX_ORDERS = 10
RATE_LIMIT_WINDOW = timedelta(minutes=10)
MANUAL_CUTOFF_BEFORE_CLOSE = timedelta(minutes=6)  # 15:54 ET on a full session
SHORT_ID_LENGTH = 6
ORDER_REF_PREFIX = "manual:"
AUDIT_SCHEMA = "autotrader.manual-order-audit.v1"

MAX_TEXT_LENGTH = 200
MAX_SHARES = 1_000_000
MAX_NOTIONAL = Decimal("10000000")
MAX_PRICE = Decimal("1000000")
MAX_PRICE_DECIMALS = 4
_CENT = Decimal("0.01")
_SUB_DOLLAR_TICK = Decimal("0.0001")


class ManualOrderNotFound(LookupError):
    """A manual-order row id does not exist in the ledger."""


class ManualSchemaPending(RuntimeError):
    """A buy reached a ledger without migration 17's stop column; nothing was recorded."""

    code = MANUAL_SCHEMA_PENDING


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _require_int(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def normalize_symbol(symbol: str) -> str:
    """IBKR spelling: upper case, share class separated by one space ("BRK B")."""
    return " ".join(symbol.strip().upper().replace(".", " ").replace("-", " ").split())


def short_id(order_id: str) -> str:
    """The id the owner types, e.g. ``/cancel a3f9c2``."""
    return order_id[:SHORT_ID_LENGTH]


def order_ref(order_id: str) -> str:
    """The IBKR orderRef the bridge must put on every order of this row."""
    return f"{ORDER_REF_PREFIX}{order_id}"


def parse_order_ref(ref: str) -> str | None:
    """Return the row id of a ``manual:<id>`` orderRef, else ``None``."""
    if not isinstance(ref, str) or not ref.startswith(ORDER_REF_PREFIX):
        return None
    candidate = ref[len(ORDER_REF_PREFIX):]
    return candidate if _ROW_ID_RE.fullmatch(candidate) else None


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ParsedOrder:
    """One owner instruction exactly as written; sizing happens in validate()."""

    action: Action
    source: Source
    symbol: str | None = None
    quantity: int | None = None
    notional: Decimal | None = None
    fraction: Fraction | None = None
    order_type: OrderType | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    cancel_ref: str | None = None
    # A BUY's protective stop (D5); never on any other action. ``stop_price``
    # stays the SELL_STOP trigger only, as migration 12's CHECKs require.
    entry_stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError("unknown manual order action")
        if self.source not in ("command", "text"):
            raise ValueError("unknown manual order source")
        if self.symbol is not None and (not self.symbol or normalize_symbol(self.symbol) != self.symbol):
            raise ValueError("symbol must use the normalized IBKR spelling")
        if self.quantity is not None:
            _require_int(self.quantity, "quantity", minimum=1)
        if self.notional is not None:
            _require_decimal(self.notional, "notional", positive=True)
        if self.limit_price is not None:
            _require_decimal(self.limit_price, "limit_price", positive=True)
        if self.stop_price is not None:
            _require_decimal(self.stop_price, "stop_price", positive=True)
        if self.entry_stop_price is not None:
            _require_decimal(self.entry_stop_price, "entry_stop_price", positive=True)
        if self.fraction is not None and self.fraction not in ("all", "half"):
            raise ValueError("fraction must be 'all' or 'half'")
        amounts = sum(value is not None for value in (self.quantity, self.notional, self.fraction))
        action = self.action
        if action in ("BUY", "SELL", "SELL_STOP", "CLOSE"):
            if self.symbol is None:
                raise ValueError(f"{action} requires a symbol")
        elif self.symbol is not None:
            raise ValueError(f"{action} takes no symbol")
        if action in ("BUY", "SELL"):
            if amounts != 1:
                raise ValueError(f"{action} requires exactly one amount")
            if action == "BUY" and self.fraction is not None:
                raise ValueError("BUY cannot use all/half")
            if self.order_type not in ("MKT", "LMT"):
                raise ValueError(f"{action} must be MKT or LMT")
            if (self.order_type == "LMT") != (self.limit_price is not None):
                raise ValueError("a limit price is required exactly for LMT")
            if self.stop_price is not None:
                raise ValueError(f"{action} takes no stop price")
            if action == "BUY":
                # Tripwire (D5): a buy without its protective stop cannot even be represented.
                if self.entry_stop_price is None:
                    raise ValueError("BUY requires its protective stop (entry_stop_price)")
                if self.limit_price is not None and self.entry_stop_price >= self.limit_price:
                    raise ValueError("a BUY stop must be below its limit price")
        elif action == "SELL_STOP":
            if amounts != 1 or self.notional is not None:
                raise ValueError("SELL_STOP requires a share count or all/half")
            if self.order_type != "STP" or self.stop_price is None or self.limit_price is not None:
                raise ValueError("SELL_STOP is a STP order with a stop price only")
        elif (
            amounts
            or self.order_type is not None
            or self.limit_price is not None
            or self.stop_price is not None
        ):
            raise ValueError(f"{action} takes no amount, order type or price")
        if action != "BUY" and self.entry_stop_price is not None:
            raise ValueError(f"{action} takes no entry stop")
        if action == "CANCEL":
            if self.cancel_ref is None or not _SHORT_ID_RE.fullmatch(self.cancel_ref):
                raise ValueError("CANCEL requires a lowercase short order id")
        elif self.cancel_ref is not None:
            raise ValueError(f"{action} takes no cancel reference")


@dataclass(frozen=True, slots=True, kw_only=True)
class ParseRefusal:
    """Text that is not a valid order; nothing may be sent for it.

    ``is_order_attempt`` is False for questions and ordinary chat (route them
    to read-only Q&A) and True when the text tried to be an order (reply with
    ``reason``).
    """

    code: RefusalCode
    reason: str
    is_order_attempt: bool


_ROW_ID_RE = re.compile(r"[0-9a-f]{32}")
_SHORT_ID_RE = re.compile(r"[0-9a-f]{6}")
_CANCEL_REF_RE = re.compile(r"#?([0-9a-fA-F]{6})")
_INT = r"\d{1,3}(?:,\d{3})+|\d+"
_NUM = rf"(?:{_INT})(?:\.\d+)?|\.\d+"
_PLAIN_INT_RE = re.compile(rf"(?:{_INT})")
_PLAIN_DECIMAL_RE = re.compile(rf"(?:{_INT})\.\d+|\.\d+")
_DOLLAR_PREFIX_RE = re.compile(rf"\$(?P<num>{_NUM})(?P<k>k)?", re.IGNORECASE)
_DOLLAR_SUFFIX_RE = re.compile(rf"(?P<num>{_NUM})(?P<k>k)?\$", re.IGNORECASE)
_K_SUFFIX_RE = re.compile(rf"(?P<num>{_NUM})k", re.IGNORECASE)
_FRACTION_TEXT_RE = re.compile("\\d+[/" + chr(0x2044) + "]\\d+")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")
_CASHTAG_RE = re.compile(r"\$(?P<root>[A-Za-z]{1,5})(?:[.\-](?P<cls>[A-Za-z]))?")
_TICKER_RE = re.compile(r"(?P<root>[A-Z]{1,5})(?:[.\-](?P<cls>[A-Z]))?")
_CLASS_LETTER_RE = re.compile(r"[A-Z]")
_LETTERS_RE = re.compile(r"[A-Za-z]{1,5}")
_QUOTE_FOLDS = (
    (chr(0x2019), "'"), (chr(0x2018), "'"), (chr(0x201C), '"'), (chr(0x201D), '"'),
)

ORDER_COMMANDS: frozenset[str] = frozenset({"buy", "sell", "stop", "close", "closeall", "cancel"})
_SENTENCE_VERBS: frozenset[str] = frozenset({"buy", "sell", "close", "cancel"})
_ORDER_VERB_WORDS: frozenset[str] = frozenset({"buy", "sell", "close", "cancel", "stop", "short"})
_ORDERISH_VERBS: frozenset[str] = frozenset(
    {"purchase", "trim", "exit", "dump", "liquidate", "flatten", "reduce", "cover", "unload"}
)
_SHORT_WORDS: frozenset[str] = frozenset({"short", "shorts", "shorting", "naked"})
_POLITE_WORDS: frozenset[str] = frozenset({"please", "pls", "plz"})
_QUESTION_STARTERS: frozenset[str] = frozenset(
    {
        "should", "can", "could", "would", "what", "whats", "what's", "how", "hows",
        "how's", "why", "is", "isnt", "isn't", "do", "does", "did", "will", "are", "am",
        "shall", "may", "might", "which", "who", "whose", "whom", "when", "where",
        "was", "were", "have", "has", "had",
    }
)
_NEGATION_STARTERS: frozenset[str] = frozenset({"don't", "dont", "never", "not", "no"})
_CONDITIONAL_STARTERS: frozenset[str] = frozenset(
    {"if", "unless", "once", "after", "before", "only", "until", "till", "provided", "assuming"}
)
_NEGATIONS: frozenset[str] = frozenset(
    {
        "not", "no", "never", "nope", "nah", "dont", "don't", "cant", "can't", "wont",
        "won't", "shouldnt", "shouldn't", "isnt", "isn't", "doesnt", "doesn't", "didnt",
        "didn't", "wouldnt", "wouldn't", "neither", "nor", "without", "except",
        # "cancel" is an action only as the leading verb; anywhere else it negates.
        "cancel", "cancelled", "canceled", "undo", "abort", "nevermind", "nvm",
    }
)
_CONDITIONALS: frozenset[str] = frozenset(
    {
        "if", "when", "whenever", "unless", "once", "after", "before", "until", "till",
        "while", "provided", "providing", "assuming", "then", "otherwise", "else", "only",
        "below", "above", "under", "over", "dips", "dip", "drops", "drop", "falls", "fall",
        "hits", "hit", "reaches", "reach", "breaks", "break", "crosses", "cross",
        "touches", "touch", "tomorrow", "tonight", "later", "eod",
    }
)
_INSTRUMENT_WORDS: frozenset[str] = frozenset(
    {
        "call", "calls", "put", "puts", "option", "options", "strike", "expiry",
        "expiration", "leaps", "lot", "lots", "contract", "contracts", "future", "futures",
        "cfd", "cfds", "crypto", "forex", "fx", "warrant", "warrants",
    }
)
_BRACKET_WORDS: frozenset[str] = frozenset(
    {
        "stop", "stops", "stoploss", "sl", "target", "tp", "take", "profit", "bracket",
        "trailing", "trail", "oco", "oto",
    }
)
_CONJUNCTIONS: frozenset[str] = frozenset({"and", "or", "also", "plus", "&"})
_PRICE_MARKERS: frozenset[str] = frozenset({"at", "@", "limit", "lmt", "lim"})
_MARKET_WORDS: frozenset[str] = frozenset({"market", "mkt"})
_FRACTION_WORDS: frozenset[str] = frozenset({"all", "half"})
_EVERYTHING_WORDS: frozenset[str] = frozenset({"everything", "anything", "entire", "whole"})
_PRE_SYMBOL_FILLERS: frozenset[str] = frozenset({"shares", "share", "of", "my", "the"})
_SHARE_WORDS: frozenset[str] = frozenset({"shares", "share"})
_CLOSE_FILLERS: frozenset[str] = frozenset({"my", "the", "of"})
_POST_SYMBOL_FILLERS: frozenset[str] = frozenset(
    {"stock", "shares", "share", "position", "now", "please", "pls", "plz"}
)
_TRAILING_FILLERS: frozenset[str] = frozenset({"now", "please", "pls", "plz"})
_CANCEL_FILLERS: frozenset[str] = frozenset(
    {"order", "orders", "my", "the", "manual", "please", "pls", "plz", "now"}
)
# A keyword can never be read as a bare ticker; the owner writes $AT, $NOW...
_RESERVED_WORDS: frozenset[str] = frozenset().union(
    _SENTENCE_VERBS, _ORDER_VERB_WORDS, _ORDERISH_VERBS, _SHORT_WORDS, _POLITE_WORDS,
    _NEGATIONS, _CONDITIONALS, _INSTRUMENT_WORDS, _BRACKET_WORDS, _CONJUNCTIONS,
    _PRICE_MARKERS, _MARKET_WORDS, _FRACTION_WORDS, _EVERYTHING_WORDS,
    _PRE_SYMBOL_FILLERS, _POST_SYMBOL_FILLERS, _CANCEL_FILLERS,
)
_WRAP_CHARS = "\"'()[]{}<>"

# Tickers that are also common English words must be written as $cashtags so a
# sentence can never be misread as an order for, e.g., Agilent (A) or Gartner (IT).
WORD_TICKERS: frozenset[str] = frozenset(
    {
        "A", "I", "ALL", "ANY", "ARE", "BE", "CAN", "FOR", "GO", "HAS", "IT", "NOW", "ON",
        "ONE", "OUT", "SEE", "SO", "TWO", "YOU", "KEY", "LOW", "NEW", "OPEN", "PLAY",
        "REAL", "SAFE", "TRUE", "WELL", "HALF", "AM", "AN", "AS", "BIG", "BY", "CAR",
        "CAT", "DAY", "DO", "EAT", "FAST", "FIVE", "FREE", "FUN", "GOLD", "GOOD", "HE",
        "HOME", "HOPE", "HUGE", "IN", "IS", "JOB", "LIFE", "LOVE", "MAIN", "MAN", "ME",
        "MORE", "MOST", "NEAR", "NEXT", "NICE", "NO", "OK", "OR", "OUR", "PAY", "PEAK",
        "PICK", "PLAN", "POST", "RUN", "SAVE", "SAY", "SHOP", "SNOW", "SUN", "TEAM", "TO",
        "TOP", "UP", "US", "WAIT", "WANT", "WAY", "WE", "WORK", "YES", "ZONE", "BOX",
        "LEAD", "MIND", "RIDE", "ROAD", "TURN", "VERY", "WHY",
    }
)

_HINT = (
    "Examples: /buy 100 AAPL at 180 stop 175, /buy $5k NVDA stop 170, /sell half TSLA, "
    "/stop TSLA 240, /close NVDA, /cancel a3f9c2."
)
# D5 (v7c): replaces the old "buy first, then /stop SYMBOL PRICE" advice.
_ENTRY_STOP_EXAMPLE = "/buy 100 AAPL at 180 stop 175 or /buy $5k NVDA stop 170"


@dataclass(frozen=True, slots=True)
class _Money:
    value: Decimal
    k: bool


@dataclass(frozen=True, slots=True)
class _Tok:
    raw: str
    low: str

    @property
    def upper_word(self) -> bool:
        return self.raw.isalpha() and self.raw.isupper()


def _refuse(code: RefusalCode, reason: str, *, attempt: bool) -> ParseRefusal:
    return ParseRefusal(code=code, reason=reason, is_order_attempt=attempt)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    for source, target in _QUOTE_FOLDS:
        normalized = normalized.replace(source, target)
    return normalized.strip()


def _clean_token(token: str) -> str:
    cleaned = token.strip(_WRAP_CHARS).rstrip(".,!;:")
    return cleaned.strip(_WRAP_CHARS)


def _tokenize(raw_tokens: list[str]) -> list[_Tok]:
    tokens: list[_Tok] = []
    for raw in raw_tokens:
        cleaned = _clean_token(raw)
        if not cleaned:
            continue
        parts = ["@", cleaned[1:]] if cleaned.startswith("@") and len(cleaned) > 1 else [cleaned]
        tokens.extend(_Tok(raw=part, low=part.lower()) for part in parts)
    return tokens


def _decimal_from(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - the regexes admit digits only
        raise ValueError(f"not a number: {text!r}") from exc


def _money(token: _Tok) -> _Money | None:
    """A dollar amount: $5000, $5k, 5000$, 5k$ or 5k (a k suffix always means dollars)."""
    for pattern in (_DOLLAR_PREFIX_RE, _DOLLAR_SUFFIX_RE, _K_SUFFIX_RE):
        match = pattern.fullmatch(token.raw)
        if match is None:
            continue
        k = pattern is _K_SUFFIX_RE or bool(match.group("k"))
        value = _decimal_from(match.group("num"))
        return _Money(value=value * 1000 if k else value, k=k)
    return None


def _is_amountish(token: _Tok) -> bool:
    return (
        token.low in _FRACTION_WORDS
        or _PLAIN_INT_RE.fullmatch(token.raw) is not None
        or _PLAIN_DECIMAL_RE.fullmatch(token.raw) is not None
        or _FRACTION_TEXT_RE.fullmatch(token.raw) is not None
        or _PERCENT_RE.fullmatch(token.raw) is not None
        or _money(token) is not None
    )


def _is_tickerish(token: _Tok) -> bool:
    return _CASHTAG_RE.fullmatch(token.raw) is not None or _TICKER_RE.fullmatch(token.raw) is not None


def _join_symbol(root: str, share_class: str | None) -> str:
    return f"{root.upper()} {share_class.upper()}" if share_class else root.upper()


def _ticker_slot(token: _Tok) -> str | ParseRefusal | None:
    """Read a token in the ticker position: a symbol, a refusal, or None (not ticker-shaped)."""
    cashtag = _CASHTAG_RE.fullmatch(token.raw)
    if cashtag is not None:
        return _join_symbol(cashtag.group("root"), cashtag.group("cls"))
    ticker = _TICKER_RE.fullmatch(token.raw)
    if ticker is not None:
        if token.raw in WORD_TICKERS or token.low in _RESERVED_WORDS:
            return _refuse(
                "word_ticker_needs_cashtag",
                f"'{token.raw}' is also an English word; write ${token.raw} if you mean the "
                "ticker. Nothing sent.",
                attempt=True,
            )
        return _join_symbol(ticker.group("root"), ticker.group("cls"))
    if _LETTERS_RE.fullmatch(token.raw) and token.low not in _RESERVED_WORDS:
        return _refuse(
            "ticker_case",
            f"Write tickers in capitals or as a cashtag ({token.raw.upper()} or ${token.low}). "
            "Nothing sent.",
            attempt=True,
        )
    return None


def _price_from(token: _Tok) -> Decimal | ParseRefusal:
    if _PLAIN_INT_RE.fullmatch(token.raw) or _PLAIN_DECIMAL_RE.fullmatch(token.raw):
        value = _decimal_from(token.raw)
    else:
        money = _money(token)
        if money is None or money.k:
            return _refuse("bad_price", f"'{token.raw}' is not a price; write e.g. 'at 180.50'.", attempt=True)
        value = money.value
    exponent = value.as_tuple().exponent
    decimals = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    if value <= 0 or value > MAX_PRICE or decimals > MAX_PRICE_DECIMALS:
        return _refuse("bad_price", f"'{token.raw}' is not a usable price. Nothing sent.", attempt=True)
    return value


_RED_FLAGS: tuple[tuple[RefusalCode, str, Callable[[str], bool]], ...] = (
    (
        "negation",
        "Negations aren't orders; send one plain instruction such as 'sell 50 TSLA'.",
        lambda word: word in _NEGATIONS or "n't" in word,
    ),
    (
        "conditional",
        "Conditional or timed orders aren't supported; send the order when you want it.",
        lambda word: word in _CONDITIONALS,
    ),
    (
        "short_selling",
        "Short selling is never allowed; sells are capped at the shares you hold.",
        lambda word: word in _SHORT_WORDS,
    ),
    (
        "unsupported_instrument",
        "Only US stocks in whole shares; options, lots and contracts aren't supported.",
        lambda word: word in _INSTRUMENT_WORDS,
    ),
    (
        "unsupported_order_type",
        "No targets, trailing stops or brackets; a buy takes one protective stop, written last "
        f"({_ENTRY_STOP_EXAMPLE}), and a stop on shares you hold is /stop SYMBOL PRICE.",
        lambda word: word in _BRACKET_WORDS,
    ),
    (
        "multiple_actions",
        "One order per message.",
        lambda word: word in _CONJUNCTIONS or word in _ORDER_VERB_WORDS,
    ),
)


def _red_flag(tokens: list[_Tok]) -> ParseRefusal | None:
    """Semantic refusals; they outrank every grammar problem. Cashtags are exempt."""
    words = [token for token in tokens if not token.raw.startswith("$")]
    for code, reason, predicate in _RED_FLAGS:
        hit = next((token for token in words if predicate(token.low)), None)
        if hit is not None:
            return _refuse(code, f"{reason} ('{hit.raw}') Nothing sent.", attempt=True)
    return None


def _unknown(token: _Tok) -> ParseRefusal:
    return _refuse("unknown_word", f"I didn't understand '{token.raw}'. Nothing sent. {_HINT}", attempt=True)


def _parse_cancel(tokens: list[_Tok], source: Source) -> ParsedOrder | ParseRefusal:
    meaningful = [token for token in tokens if token.low not in _CANCEL_FILLERS]
    if len(meaningful) == 1:
        token = meaningful[0]
        if token.low == "all":
            return ParsedOrder(action="CANCEL_ALL", source=source)
        match = _CANCEL_REF_RE.fullmatch(token.raw)
        if match is not None:
            return ParsedOrder(action="CANCEL", source=source, cancel_ref=match.group(1).lower())
    return _refuse(
        "bad_cancel_ref",
        "Cancel needs one 6-character order id (e.g. /cancel a3f9c2) or 'all'. Nothing sent.",
        attempt=True,
    )


def _amount_from(token: _Tok, action: Action) -> int | _Money | Fraction | ParseRefusal:
    if token.low in _FRACTION_WORDS:
        if action == "BUY":
            return _refuse(
                "bad_quantity",
                "A buy needs a share count or a $ amount (buy 100 AAPL, buy $5k AAPL). Nothing sent.",
                attempt=True,
            )
        return "all" if token.low == "all" else "half"
    if _PLAIN_INT_RE.fullmatch(token.raw):
        shares = int(token.raw.replace(",", ""))
        if not 1 <= shares <= MAX_SHARES:
            return _refuse("bad_quantity", f"'{token.raw}' is not a usable share count. Nothing sent.", attempt=True)
        return shares
    if _PLAIN_DECIMAL_RE.fullmatch(token.raw) or _FRACTION_TEXT_RE.fullmatch(token.raw):
        return _refuse(
            "fractional_shares",
            f"Whole shares only ('{token.raw}'); use a whole number or 'half'. Nothing sent.",
            attempt=True,
        )
    if _PERCENT_RE.fullmatch(token.raw):
        return _refuse(
            "bad_quantity", "Percentages aren't supported; use a share count, 'half' or 'all'.",
            attempt=True,
        )
    money = _money(token)
    assert money is not None, "caller checked _is_amountish"
    if action == "SELL_STOP":
        return _refuse(
            "bad_quantity", "A stop takes a share count, 'all' or 'half' (/stop 50 TSLA 240).",
            attempt=True,
        )
    if not 1 <= money.value <= MAX_NOTIONAL:
        return _refuse("bad_quantity", f"'{token.raw}' is not a usable amount. Nothing sent.", attempt=True)
    return money


@dataclass
class _TradeState:
    action: Action
    source: Source
    positional_price: bool
    phase: str  # amount | amount_or_symbol -> pre_symbol -> post_symbol -> end
    amount: int | _Money | Fraction | None = None
    symbol: str | None = None
    price: Decimal | None = None
    explicit_market: bool = False
    entry_stop: Decimal | None = None


def _take_symbol(state: _TradeState, symbol: str, tokens: list[_Tok], index: int) -> int:
    # "BRK B" arrives as two tokens; a lone capital letter right after a
    # ticker is its share class.
    if " " not in symbol and index < len(tokens) and _CLASS_LETTER_RE.fullmatch(tokens[index].raw):
        symbol = f"{symbol} {tokens[index].raw}"
        index += 1
    state.symbol = symbol
    state.phase = "post_symbol"
    return index


_MULTIPLE_QUANTITIES = "Give exactly one quantity. Nothing sent."
_MULTIPLE_SYMBOLS = "One ticker per order. Nothing sent."
_CLOSE_ALL_ONLY = "To close every position send exactly /closeall. Nothing sent."


def _split_entry_stop(tokens: list[_Tok]) -> tuple[list[_Tok], Decimal | None] | ParseRefusal:
    """A buy's ``stop <price>`` clause (D5), which must come last (politeness may follow).

    Returns the other tokens and the stop price (None when the buy names none);
    any other use of the word, a second stop or a stop without a price is refused.
    """
    end = len(tokens)
    while end > 0 and tokens[end - 1].low in _TRAILING_FILLERS:
        end -= 1
    marks = [index for index, token in enumerate(tokens) if token.low == ENTRY_STOP_WORD]
    if not marks:
        return tokens, None
    if len(marks) > 1:
        return _refuse("bad_price", f"Give one stop price, written last ({_ENTRY_STOP_EXAMPLE}). Nothing sent.",
                       attempt=True)
    index = marks[0]
    if index == end - 1:
        return _refuse("missing_price", f"The stop needs a price ({_ENTRY_STOP_EXAMPLE}). Nothing sent.",
                       attempt=True)
    if index != end - 2:
        return _refuse("bad_price", f"Write the stop last ({_ENTRY_STOP_EXAMPLE}). Nothing sent.", attempt=True)
    price = _price_from(tokens[index + 1])
    if isinstance(price, ParseRefusal):
        return price
    return tokens[:index] + tokens[end:], price


def _parse_trade(
    action: Action, tokens: list[_Tok], *, source: Source, positional_price: bool,
    entry_stop: Decimal | None = None,
) -> ParsedOrder | ParseRefusal:
    state = _TradeState(
        action=action, source=source, positional_price=positional_price,
        phase="amount_or_symbol" if action == "SELL_STOP" else "amount", entry_stop=entry_stop,
    )
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.low in _EVERYTHING_WORDS:
            return _refuse("closeall_exact", _CLOSE_ALL_ONLY, attempt=True)
        if state.phase in ("amount", "amount_or_symbol"):
            if _is_amountish(token):
                amount = _amount_from(token, action)
                if isinstance(amount, ParseRefusal):
                    return amount
                state.amount = amount
                state.phase = "pre_symbol"
                continue
            if token.low in _PRE_SYMBOL_FILLERS:
                continue
            slot = _ticker_slot(token)
            if state.phase == "amount_or_symbol" and slot is not None:
                if isinstance(slot, ParseRefusal):
                    return slot
                index = _take_symbol(state, slot, tokens, index)
                continue
            if slot is not None:
                return _refuse(
                    "missing_quantity",
                    "Put the amount before the ticker: 'buy 100 AAPL', 'sell half TSLA'. Nothing sent.",
                    attempt=True,
                )
            return _unknown(token)
        if state.phase == "pre_symbol":
            if token.low in _PRE_SYMBOL_FILLERS:
                if token.low in _SHARE_WORDS and isinstance(state.amount, _Money):
                    return _refuse(
                        "ambiguous_amount",
                        f"'{token.raw}' after a $ amount is ambiguous; write $5k or 5000 shares. "
                        "Nothing sent.",
                        attempt=True,
                    )
                continue
            # In the ticker slot a capitalised keyword (ALL, NOW, AT) is read as
            # an attempted ticker and refused, never as its keyword meaning.
            if _is_amountish(token) and not token.upper_word:
                return _refuse("multiple_quantities", _MULTIPLE_QUANTITIES, attempt=True)
            slot = _ticker_slot(token)
            if isinstance(slot, ParseRefusal):
                return slot
            if slot is not None:
                index = _take_symbol(state, slot, tokens, index)
                continue
            if token.low in _RESERVED_WORDS:
                return _refuse("missing_symbol", f"Which ticker? Nothing sent. {_HINT}", attempt=True)
            return _unknown(token)
        if state.phase == "post_symbol":
            if token.low in _POST_SYMBOL_FILLERS:
                continue
            if token.low in _MARKET_WORDS:
                state.explicit_market = True
                state.phase = "end"
                continue
            if token.low in _PRICE_MARKERS:
                if index >= len(tokens):
                    return _refuse("missing_price", f"'{token.raw}' needs a price after it.", attempt=True)
                following = tokens[index]
                index += 1
                if following.low in _MARKET_WORDS:
                    state.explicit_market = True
                else:
                    price = _price_from(following)
                    if isinstance(price, ParseRefusal):
                        return price
                    state.price = price
                state.phase = "end"
                continue
            if _is_amountish(token):
                if positional_price and token.low not in _FRACTION_WORDS:
                    price = _price_from(token)
                    if not isinstance(price, ParseRefusal):
                        state.price = price
                        state.phase = "end"
                        continue
                return _refuse(
                    "multiple_quantities",
                    f"Give exactly one quantity ('{token.raw}'); for a limit write 'at {token.raw}'. "
                    "Nothing sent.",
                    attempt=True,
                )
            if _is_tickerish(token):
                return _refuse("multiple_symbols", _MULTIPLE_SYMBOLS, attempt=True)
            return _unknown(token)
        # phase == "end": only politeness may follow the price.
        if token.low in _TRAILING_FILLERS:
            continue
        if token.low in _PRICE_MARKERS or token.low in _MARKET_WORDS:
            return _refuse("bad_price", "Give at most one price. Nothing sent.", attempt=True)
        if _is_amountish(token):
            return _refuse("multiple_quantities", _MULTIPLE_QUANTITIES, attempt=True)
        if _is_tickerish(token):
            return _refuse("multiple_symbols", _MULTIPLE_SYMBOLS, attempt=True)
        return _unknown(token)
    return _finish_trade(state)


def _finish_trade(state: _TradeState) -> ParsedOrder | ParseRefusal:
    action = state.action
    if state.symbol is None:
        hint = (
            "e.g. 'sell all TSLA'. To close everything send exactly /closeall."
            if state.amount in ("all", "half") else _HINT
        )
        return _refuse("missing_symbol", f"Which ticker? Nothing sent. {hint}", attempt=True)
    amount = state.amount
    if amount is None and action == "SELL_STOP":
        amount = "all"
    if amount is None:
        return _refuse(
            "missing_quantity",
            "How many? e.g. 'buy 100 AAPL', 'buy $5k AAPL', 'sell half TSLA'. Nothing sent.",
            attempt=True,
        )
    quantity = amount if isinstance(amount, int) else None
    notional = amount.value if isinstance(amount, _Money) else None
    fraction: Fraction | None = None
    if amount == "all":
        fraction = "all"
    elif amount == "half":
        fraction = "half"
    if action == "SELL_STOP":
        if state.explicit_market or state.price is None:
            return _refuse(
                "missing_price",
                "A stop needs a trigger price, e.g. /stop TSLA 240 (/stop alone halts the bot). "
                "Nothing sent.",
                attempt=True,
            )
        return ParsedOrder(
            action="SELL_STOP", source=state.source, symbol=state.symbol, quantity=quantity,
            fraction=fraction, order_type="STP", stop_price=state.price,
        )
    if action == "BUY":
        if state.entry_stop is None:
            return _refuse(
                "missing_entry_stop",
                f"A buy needs its protective stop, written last: {_ENTRY_STOP_EXAMPLE}. Nothing sent.",
                attempt=True,
            )
        if state.price is not None and state.entry_stop >= state.price:
            return _refuse(
                "stop_not_below_entry",
                f"The stop {state.entry_stop} must be below the limit {state.price}. Nothing sent.",
                attempt=True,
            )
    return ParsedOrder(
        action=action, source=state.source, symbol=state.symbol, quantity=quantity,
        notional=notional, fraction=fraction,
        order_type="LMT" if state.price is not None else "MKT", limit_price=state.price,
        entry_stop_price=state.entry_stop if action == "BUY" else None,
    )


def _parse_close(tokens: list[_Tok], source: Source) -> ParsedOrder | ParseRefusal:
    symbol: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.low == "all" or token.low in _EVERYTHING_WORDS:
            return _refuse("closeall_exact", _CLOSE_ALL_ONLY, attempt=True)
        if symbol is None:
            if token.low in _CLOSE_FILLERS:
                continue
            if _is_amountish(token) and not token.upper_word:
                return _refuse(
                    "bad_quantity",
                    "Close sells your whole position; for part of it use 'sell 50 TSLA'. Nothing sent.",
                    attempt=True,
                )
            slot = _ticker_slot(token)
            if isinstance(slot, ParseRefusal):
                return slot
            if slot is None:
                return _unknown(token)
            if " " not in slot and index < len(tokens) and _CLASS_LETTER_RE.fullmatch(tokens[index].raw):
                slot = f"{slot} {tokens[index].raw}"
                index += 1
            symbol = slot
            continue
        if token.low in _POST_SYMBOL_FILLERS:
            continue
        if token.low in _PRICE_MARKERS or token.low in _MARKET_WORDS or _is_amountish(token):
            return _refuse(
                "unsupported_order_type",
                "Close is a market order; for a limit use 'sell all TSLA at 240'. Nothing sent.",
                attempt=True,
            )
        if _is_tickerish(token):
            return _refuse("multiple_symbols", _MULTIPLE_SYMBOLS, attempt=True)
        return _unknown(token)
    if symbol is None:
        return _refuse("missing_symbol", "Close which ticker? e.g. 'close NVDA'. Nothing sent.", attempt=True)
    return ParsedOrder(action="CLOSE", source=source, symbol=symbol)


def _command_name(token: str) -> str:
    return token[1:].split("@", 1)[0].lower()


def is_kill_switch_command(text: str) -> bool:
    """True only for a bare ``/stop`` (optionally ``/stop@BotName``).

    ``/stop`` with arguments is a protective stop order, never the kill switch.
    """
    if not isinstance(text, str):
        return False
    words = _normalize_text(text).split()
    return len(words) == 1 and words[0].startswith("/") and _command_name(words[0]) == "stop"


def parse_order_text(text: str) -> ParsedOrder | ParseRefusal:
    """Parse one owner message into an order, or refuse it with a human reason.

    Accepted (verbs any case; tickers in capitals or as $cashtags; one order):
      /buy 100 AAPL stop 175 · /buy 100 AAPL 180.5 stop 175 · /buy $5000 NVDA stop 170
      /buy 5k NVDA at 180 stop 170
      /sell 50 TSLA [price] · /sell all TSLA · /sell half TSLA
      /stop [N|all|half] TSLA 240 · /close TSLA · /closeall · /cancel a3f9c2 · /cancel all
      buy 100 AAPL stop 175 · buy 100 shares of $AAPL at 180 stop 175 · sell half my TSLA
      sell all $TSLA · close NVDA · cancel a3f9c2 · cancel all
    "$" or a "k" suffix marks dollars (notional, sized later from a fresh quote);
    a bare whole number is shares. A bare price after the ticker is a limit
    only in slash commands; sentences need "at"/"@"/"limit". A buy must end
    with its protective stop, ``stop <price>`` (D5): without one it is refused
    (``missing_entry_stop``), as is a stop at or above its limit
    (``stop_not_below_entry``).
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = _normalize_text(text)
    if not normalized:
        return _refuse("empty", "Empty message.", attempt=False)
    words = normalized.split()
    if words[0].startswith("/"):
        return _parse_command(normalized, words)
    return _parse_sentence(normalized, words)


def _parse_command(normalized: str, words: list[str]) -> ParsedOrder | ParseRefusal:
    name = _command_name(words[0])
    args = words[1:]
    if name == "stop" and not args:
        return _refuse("kill_switch_command", "/stop alone is the kill switch, not an order.", attempt=False)
    if name in _SHORT_WORDS:
        return _refuse("short_selling", "Short selling is never allowed. Nothing sent.", attempt=True)
    if name in _ORDERISH_VERBS:
        return _refuse("unknown_verb", f"Unknown order command /{name}. {_HINT}", attempt=True)
    if name not in ORDER_COMMANDS:
        return _refuse("not_an_order", f"/{name} is not an order command.", attempt=False)
    if "?" in normalized:
        return _refuse("question", "Orders can't contain '?'. Nothing sent.", attempt=True)
    if len(normalized) > MAX_TEXT_LENGTH:
        return _refuse("too_long", "Order messages must be short. Nothing sent.", attempt=True)
    if name == "closeall":
        if args:
            return _refuse(
                "closeall_exact", "Send exactly /closeall with nothing after it. Nothing sent.",
                attempt=True,
            )
        return ParsedOrder(action="CLOSE_ALL", source="command")
    tokens = _tokenize(args)
    entry_stop: Decimal | None = None
    if name == "buy":  # the buy's own stop clause is not a bracket word
        split = _split_entry_stop(tokens)
        if isinstance(split, ParseRefusal):
            return split
        tokens, entry_stop = split
    flagged = _red_flag(tokens)
    if flagged is not None:
        return flagged
    if name == "cancel":
        return _parse_cancel(tokens, "command")
    if name == "close":
        return _parse_close(tokens, "command")
    action: Action = "BUY" if name == "buy" else "SELL" if name == "sell" else "SELL_STOP"
    return _parse_trade(action, tokens, source="command", positional_price=True, entry_stop=entry_stop)


def _parse_sentence(normalized: str, words: list[str]) -> ParsedOrder | ParseRefusal:
    lowered = [_clean_token(word).lower() for word in words]
    start = 0
    while start < len(lowered) - 1 and lowered[start] in _POLITE_WORDS:
        start += 1
    first = lowered[start]
    rest = lowered[start + 1:]
    if "?" in normalized:
        return _refuse("question", "That's a question, not an order.", attempt=False)
    mentions_order = any(word in _ORDER_VERB_WORDS for word in rest)
    if first == "stop":
        return _refuse(
            "unknown_verb",
            "For a stop order send /stop SYMBOL PRICE (e.g. /stop TSLA 240); /stop alone halts "
            "new bot entries. Nothing sent.",
            attempt=True,
        )
    if first in _SHORT_WORDS:
        return _refuse("short_selling", "Short selling is never allowed. Nothing sent.", attempt=True)
    if first in _ORDERISH_VERBS:
        return _refuse("unknown_verb", f"Orders start with buy, sell, close or cancel. {_HINT}", attempt=True)
    if first not in _SENTENCE_VERBS:
        if (first in _NEGATION_STARTERS or (first == "do" and rest[:1] == ["not"])) and mentions_order:
            return _refuse("negation", "Negations aren't orders. Nothing sent.", attempt=True)
        if first in _CONDITIONAL_STARTERS and mentions_order:
            return _refuse(
                "conditional",
                "Conditional orders aren't supported; send the order when you want it. Nothing sent.",
                attempt=True,
            )
        if first in _QUESTION_STARTERS:
            return _refuse("question", "That's a question, not an order.", attempt=False)
        return _refuse("not_an_order", "Not an order.", attempt=False)
    if len(normalized) > MAX_TEXT_LENGTH:
        return _refuse("too_long", "Order messages must be short. Nothing sent.", attempt=True)
    tokens = _tokenize(words[start + 1:])
    entry_stop: Decimal | None = None
    if first == "buy":  # the buy's own stop clause is not a bracket word
        split = _split_entry_stop(tokens)
        if isinstance(split, ParseRefusal):
            return split
        tokens, entry_stop = split
    flagged = _red_flag(tokens)
    if flagged is not None:
        return flagged
    if first == "cancel":
        return _parse_cancel(tokens, "text")
    if first == "close":
        return _parse_close(tokens, "text")
    return _parse_trade(
        "BUY" if first == "buy" else "SELL", tokens, source="text", positional_price=False, entry_stop=entry_stop
    )


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class Quote:
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    observed_at: datetime
    real_time: bool

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "quote.observed_at")
        for name in ("bid", "ask", "last"):
            value = getattr(self, name)
            if value is not None:
                _require_decimal(value, f"quote.{name}")
        if not isinstance(self.real_time, bool):
            raise ValueError("quote.real_time must be a bool")


@dataclass(frozen=True, slots=True, kw_only=True)
class ContractRef:
    """The qualified IBKR contract for the parsed symbol (pass None when unresolved)."""

    symbol: str
    con_id: int
    primary_exchange: str
    currency: str = "USD"
    security_type: str = "STK"


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveFacts:
    """Facts gathered by the caller right before validating one order.

    Share accounting (no naked shorts): ``owner_position_qty`` is the live
    ``reqPositions`` quantity for the contract. ``open_manual_sell_qty`` comes
    from :func:`ledger_facts` (remaining quantity of live manual SELL/SELL_STOP/
    CLOSE rows for the symbol, excluding the row being re-validated).
    ``resting_stop_qty`` is the remaining quantity of every other working SELL
    order on the contract at IBKR that is not a bot bracket leg and whose
    orderRef is not in ``LedgerFacts.counted_order_refs`` (e.g. a stop the
    owner placed in TWS, or a /closeall child order).
    """

    now: datetime
    accounts_all_paper: bool
    kill_switch_active: bool
    bridge_heartbeat_age_s: float | None
    net_liquidation: Decimal | None
    quote: Quote | None
    owner_position_qty: Decimal
    open_manual_sell_qty: int
    resting_stop_qty: Decimal
    bot_symbols: frozenset[str]
    manual_open_buy_notional_today: Decimal
    recent_manual_orders_10min: int

    def __post_init__(self) -> None:
        _require_aware(self.now, "now")
        for name in ("accounts_all_paper", "kill_switch_active"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")
        age = self.bridge_heartbeat_age_s
        if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float)) or age != age):
            raise ValueError("bridge_heartbeat_age_s must be a number or None")
        if self.net_liquidation is not None:
            _require_decimal(self.net_liquidation, "net_liquidation")
        if self.quote is not None and not isinstance(self.quote, Quote):
            raise ValueError("quote must be a Quote")
        object.__setattr__(self, "owner_position_qty", _as_decimal(self.owner_position_qty, "owner_position_qty"))
        object.__setattr__(self, "resting_stop_qty", _as_decimal(self.resting_stop_qty, "resting_stop_qty"))
        if self.resting_stop_qty < 0:
            raise ValueError("resting_stop_qty must be non-negative")
        _require_int(self.open_manual_sell_qty, "open_manual_sell_qty", minimum=0)
        _require_int(self.recent_manual_orders_10min, "recent_manual_orders_10min", minimum=0)
        _require_decimal(self.manual_open_buy_notional_today, "manual_open_buy_notional_today")
        if self.manual_open_buy_notional_today < 0:
            raise ValueError("manual_open_buy_notional_today must be non-negative")
        object.__setattr__(
            self, "bot_symbols", frozenset(normalize_symbol(symbol) for symbol in self.bot_symbols)
        )


def _as_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, int):
        return Decimal(value)
    return _require_decimal(value, name)


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedOrder:
    """An order that passed every rule; the only input queue_order accepts."""

    kind: OrderKind
    action: Action
    parsed: ParsedOrder
    symbol: str | None = None
    con_id: int | None = None
    primary_exchange: str | None = None
    side: Side | None = None
    quantity: int | None = None
    order_type: OrderType | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: Literal["DAY"] = "DAY"
    reference_price: Decimal | None = None
    estimated_notional: Decimal | None = None
    cancel_ref: str | None = None
    # D5: a BUY's protective stop, the bridge sends it attached to the entry.
    entry_stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        # Mirrors the migration-12 and migration-17 CHECKs so an inconsistent
        # order fails here, not halfway through queue_order.
        if self.action != self.parsed.action:
            raise ValueError("validated action does not match the parsed order")
        allowed_kinds = {
            "CLOSE_ALL": ("close_all",), "CANCEL": ("cancel",), "CANCEL_ALL": ("cancel_all",),
            "CLOSE": ("order", "bot_flatten"),
        }.get(self.action, ("order",))
        if self.kind not in allowed_kinds:
            raise ValueError("validated kind does not match the action")
        expected_side = "BUY" if self.action == "BUY" else "SELL" if self.action in SELL_SIDE_ACTIONS else None
        if self.side != expected_side:
            raise ValueError("validated side does not match the action")
        if self.tif != "DAY":
            raise ValueError("manual orders are DAY only")
        if self.kind == "order":
            _require_int(self.quantity, "quantity", minimum=1)
            if not self.symbol or self.con_id is None or not self.primary_exchange:
                raise ValueError("an order needs a qualified contract")
            allowed_types = ("STP",) if self.action == "SELL_STOP" else ("MKT", "LMT")
            if self.order_type not in allowed_types:
                raise ValueError("order type does not match the action")
        elif self.quantity is not None or self.order_type is not None:
            raise ValueError(f"{self.kind} carries no quantity or order type")
        if (self.order_type == "LMT") != (self.limit_price is not None):
            raise ValueError("a limit price is required exactly for LMT")
        if (self.order_type == "STP") != (self.stop_price is not None):
            raise ValueError("a stop price is required exactly for STP")
        if (self.action == "BUY" and self.kind == "order") != (self.entry_stop_price is not None):
            raise ValueError("an entry stop is required exactly for a BUY")
        if self.entry_stop_price is not None:
            _require_decimal(self.entry_stop_price, "entry_stop_price", positive=True)
            if self.entry_stop_price != self.parsed.entry_stop_price:
                raise ValueError("validated entry stop does not match the parsed order")
            if self.reference_price is None or self.entry_stop_price >= self.reference_price:
                raise ValueError("a BUY stop must be below its entry")

    @property
    def entry_risk(self) -> Decimal | None:
        """A BUY's risk to its stop: quantity x (entry - stop), at the limit or the ask it was sized at."""
        if self.entry_stop_price is None or self.reference_price is None or self.quantity is None:
            return None
        return self.quantity * (self.reference_price - self.entry_stop_price)

    def summary(self) -> str:
        return _summary(
            self.action, self.symbol, self.quantity, self.order_type, self.limit_price,
            self.stop_price, self.cancel_ref,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Rejection:
    code: RejectCode
    message: str


def manual_order_window(session_date: date) -> tuple[datetime, datetime] | None:
    """The manual-order window of an XNYS session: the open until 6 minutes before close."""
    session = market_session(session_date)
    if session is None:
        return None
    return session.opens_at, session.closes_at - MANUAL_CUTOFF_BEFORE_CLOSE


def is_manual_window_open(now: datetime) -> bool:
    _require_aware(now, "now")
    window = manual_order_window(now.astimezone(MARKET_TIMEZONE).date())
    return window is not None and window[0] <= now < window[1]


def _usd(value: Decimal) -> str:
    return f"${value.quantize(_CENT):,}"


def _reject(code: RejectCode, message: str) -> Rejection:
    return Rejection(code=code, message=message)


_NOT_PAPER = "Refused: an IBKR account is not a paper (DU) account. Nothing sent."


def _environment(facts: LiveFacts) -> Rejection | None:
    try:
        open_now = is_manual_window_open(facts.now)
    except Exception as exc:  # the calendar must fail closed, whatever broke
        return _reject("market_closed", f"Market calendar unavailable ({type(exc).__name__}). Nothing sent.")
    if not open_now:
        return _reject(
            "market_closed", "Market closed: manual orders run 09:30-15:54 ET on trading days. Nothing sent."
        )
    age = facts.bridge_heartbeat_age_s
    # Both clocks are this machine's; a heartbeat far in the future is as
    # untrustworthy as a stale one.
    if age is None or not -MAX_BRIDGE_HEARTBEAT_AGE_S <= age <= MAX_BRIDGE_HEARTBEAT_AGE_S:
        seen = "never seen" if age is None else f"last heartbeat {age:.0f}s ago"
        return _reject("bridge_offline", f"Order bridge offline ({seen}). Nothing sent; use TWS.")
    return None


def _floor_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _ceil_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _valid_book(quote: Quote) -> tuple[Decimal, Decimal] | None:
    if quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
        return None
    return quote.bid, quote.ask


def _realtime_quote(facts: LiveFacts) -> Quote | Rejection:
    quote = facts.quote
    if quote is None:
        return _reject("no_fresh_quote", "No quote available. Nothing sent.")
    if not quote.real_time:
        return _reject("no_fresh_quote", "Only a delayed quote is available. Nothing sent.")
    age = facts.now - quote.observed_at
    if age > MARKET_QUOTE_MAX_AGE or age < -QUOTE_CLOCK_SKEW:
        return _reject(
            "no_fresh_quote",
            f"Quote is {age.total_seconds():.1f}s old (max {MARKET_QUOTE_MAX_AGE.seconds}s). Nothing sent.",
        )
    return quote


def _market_book(facts: LiveFacts) -> tuple[Decimal, Decimal] | Rejection:
    quote = _realtime_quote(facts)
    if isinstance(quote, Rejection):
        return quote
    book = _valid_book(quote)
    if book is None:
        return _reject("no_fresh_quote", "No valid bid/ask right now. Nothing sent.")
    bid, ask = book
    spread = (ask - bid) / ((ask + bid) / 2)
    if spread > MAX_SPREAD_FRACTION:
        return _reject(
            "spread_too_wide",
            f"Spread is {spread * 100:.2f}% (max 1%) for a market order; use a limit price. Nothing sent.",
        )
    return bid, ask


def _reference_price(quote: Quote) -> Decimal | None:
    """The bid/ask midpoint, else the last trade."""
    book = _valid_book(quote)
    if book is not None:
        return (book[0] + book[1]) / 2
    if quote.last is not None and quote.last > 0:
        return quote.last
    return None


def _band_reference(facts: LiveFacts) -> Decimal | Rejection:
    quote = facts.quote
    if quote is None:
        return _reject("no_fresh_quote", "No quote to check the price against. Nothing sent.")
    age = facts.now - quote.observed_at
    if age > REFERENCE_QUOTE_MAX_AGE or age < -QUOTE_CLOCK_SKEW:
        return _reject("no_fresh_quote", "No recent quote to check the price against. Nothing sent.")
    reference = _reference_price(quote)
    if reference is None:
        return _reject("no_fresh_quote", "No usable last or mid price. Nothing sent.")
    return reference


def _tick_ok(price: Decimal) -> bool:
    return price % (_CENT if price >= 1 else _SUB_DOLLAR_TICK) == 0


def _price_checks(price: Decimal, reference: Decimal, label: str) -> Rejection | None:
    if not _tick_ok(price):
        return _reject(
            "invalid_tick", f"{label} {price} is not a valid tick (cents above $1). Nothing sent."
        )
    if abs(price - reference) > reference * PRICE_BAND_FRACTION:
        return _reject(
            "price_out_of_band",
            f"{label} {price} is more than 10% from the market ({reference.quantize(_CENT)}). Nothing sent.",
        )
    return None


def _contract_ok(contract: ContractRef | None, symbol: str) -> bool:
    return (
        contract is not None
        and not isinstance(contract.con_id, bool)
        and isinstance(contract.con_id, int)
        and contract.con_id > 0
        and contract.currency == "USD"
        and contract.security_type == "STK"
        and bool(contract.primary_exchange.strip())
        and normalize_symbol(contract.symbol) == symbol
    )


def _validated(
    parsed: ParsedOrder,
    contract: ContractRef,
    *,
    kind: OrderKind,
    quantity: int | None = None,
    order_type: OrderType | None = None,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    reference_price: Decimal | None = None,
    entry_stop_price: Decimal | None = None,
) -> ValidatedOrder:
    return ValidatedOrder(
        kind=kind, action=parsed.action, parsed=parsed, symbol=parsed.symbol,
        con_id=contract.con_id, primary_exchange=contract.primary_exchange.strip(),
        side="BUY" if parsed.action == "BUY" else "SELL", quantity=quantity,
        order_type=order_type, limit_price=limit_price, stop_price=stop_price,
        reference_price=reference_price,
        estimated_notional=(
            None if reference_price is None or quantity is None else reference_price * quantity
        ),
        entry_stop_price=entry_stop_price,
    )


def _validate_symbol_order(
    parsed: ParsedOrder,
    *,
    facts: LiveFacts,
    contract: ContractRef | None,
    fixed_quantity: int | None,
) -> ValidatedOrder | Rejection:
    symbol = parsed.symbol
    assert symbol is not None
    if not _contract_ok(contract, symbol):
        return _reject("unknown_symbol", f"Couldn't resolve {symbol} to a US stock. Nothing sent.")
    assert contract is not None
    # Exclusivity: shares in a bot bracket are only ever exited by the bracket.
    if symbol in facts.bot_symbols:
        if parsed.action == "CLOSE":
            return _validated(parsed, contract, kind="bot_flatten")
        return _reject(
            "bot_holds_symbol", f"The bot is trading {symbol}; only /close {symbol} is allowed. Nothing sent."
        )
    if parsed.action == "BUY":
        return _validate_buy(parsed, facts=facts, contract=contract)
    return _validate_sell(parsed, facts=facts, contract=contract, fixed_quantity=fixed_quantity)


def _validate_buy(parsed: ParsedOrder, *, facts: LiveFacts, contract: ContractRef) -> ValidatedOrder | Rejection:
    if facts.kill_switch_active:
        return _reject(
            "kill_switch", "Kill switch is on: buys are blocked (sell, /close, /cancel still work). Nothing sent."
        )
    net_liq = facts.net_liquidation
    if net_liq is None or net_liq <= 0:
        return _reject("account_unavailable", "Net liquidation is unknown, so buys are blocked. Nothing sent.")
    floor: Decimal  # the stop must be below this: the bid, and a limit buy's limit
    if parsed.order_type == "MKT":
        book = _market_book(facts)
        if isinstance(book, Rejection):
            return book
        floor, price = book
    else:
        assert parsed.limit_price is not None
        reference = _band_reference(facts)
        if isinstance(reference, Rejection):
            return reference
        failure = _price_checks(parsed.limit_price, reference, "Limit")
        if failure is not None:
            return failure
        price = floor = parsed.limit_price
        book = _valid_book(facts.quote) if facts.quote is not None else None
        if book is not None and book[0] < floor:  # a marketable limit fills near the bid
            floor = book[0]
    if parsed.notional is not None:
        quantity = _floor_int(parsed.notional / price)
        if quantity < 1:
            return _reject(
                "amount_too_small", f"{_usd(parsed.notional)} buys less than one share at {price}. Nothing sent."
            )
    else:
        assert parsed.quantity is not None
        quantity = parsed.quantity
    notional = price * quantity
    cap = net_liq * MAX_ORDER_FRACTION_OF_NET_LIQ
    if notional > cap:
        return _reject(
            "order_too_large",
            f"BUY {quantity} {parsed.symbol} ~{_usd(notional)} is over 25% of net liquidation "
            f"({_usd(cap)} max). Nothing sent.",
        )
    exposure = facts.manual_open_buy_notional_today + notional
    exposure_cap = net_liq * MAX_MANUAL_EXPOSURE_FRACTION_OF_NET_LIQ
    if exposure > exposure_cap:
        return _reject(
            "manual_exposure_cap",
            f"Manual buys today would reach {_usd(exposure)}, over 50% of net liquidation "
            f"({_usd(exposure_cap)}). Nothing sent.",
        )
    if facts.recent_manual_orders_10min >= RATE_LIMIT_MAX_ORDERS:
        return _reject("rate_limited", "At most 10 manual buys per 10 minutes; exits still work. Nothing sent.")
    stop_failure = _entry_stop_checks(parsed, quantity=quantity, price=price, floor=floor, net_liq=net_liq)
    if stop_failure is not None:
        return stop_failure
    return _validated(
        parsed, contract, kind="order", quantity=quantity, order_type=parsed.order_type,
        limit_price=parsed.limit_price, reference_price=price, entry_stop_price=parsed.entry_stop_price,
    )


def _entry_stop_checks(
    parsed: ParsedOrder, *, quantity: int, price: Decimal, floor: Decimal, net_liq: Decimal
) -> Rejection | None:
    """D5: a buy's protective stop is a valid tick, below ``floor`` (the bid, and a limit
    buy's limit: a stop at or above the bid fires at once) and risks at most 1 % of net
    liquidation, measured from ``price`` (the ask a market buy fills at, else the limit)."""
    stop = parsed.entry_stop_price
    if stop is None:  # pragma: no cover - ParsedOrder refuses a BUY without one
        return _reject("missing_entry_stop", f"A buy needs its protective stop: {_ENTRY_STOP_EXAMPLE}. Nothing sent.")
    if not _tick_ok(stop):
        return _reject("invalid_tick", f"Stop {stop} is not a valid tick (cents above $1). Nothing sent.")
    if stop >= floor:
        where = "limit" if parsed.order_type == "LMT" and floor == parsed.limit_price else "bid"
        return _reject(
            "stop_not_below_entry", f"The stop {stop} must be below the {where} {floor}. Nothing sent."
        )
    risk = quantity * (price - stop)
    ceiling = net_liq * MAX_MANUAL_ENTRY_RISK_FRACTION_OF_NET_LIQ
    if risk > ceiling:
        return _reject(
            "manual_entry_risk_over_1pct",
            f"BUY {quantity} {parsed.symbol} at {price} with stop {stop} risks {_usd(risk)}, over 1% of net"
            f" liquidation ({_usd(ceiling)} max); move the stop up or buy fewer. Nothing sent.",
        )
    return None


def _validate_sell(
    parsed: ParsedOrder,
    *,
    facts: LiveFacts,
    contract: ContractRef,
    fixed_quantity: int | None,
) -> ValidatedOrder | Rejection:
    symbol = parsed.symbol
    held = max(0, _floor_int(facts.owner_position_qty))
    reserved = facts.open_manual_sell_qty + _ceil_int(facts.resting_stop_qty)
    sellable = max(0, held - reserved)
    if sellable < 1:
        detail = f"you hold {held}" if reserved == 0 else f"{held} held, {reserved} reserved by working sells"
        return _reject("insufficient_shares", f"No {symbol} shares available to sell ({detail}). Nothing sent.")
    if parsed.action == "CLOSE" and fixed_quantity is None and reserved > 0:
        return _reject(
            "insufficient_shares",
            f"{reserved} of your {held} {symbol} shares are reserved by working sells/stops; cancel "
            f"them first or use /sell {sellable} {symbol}. Nothing sent.",
        )
    order_type: OrderType
    if parsed.action == "SELL_STOP":
        assert parsed.stop_price is not None
        quote = _realtime_quote(facts)
        if isinstance(quote, Rejection):
            return quote
        if quote.bid is None or quote.bid <= 0:
            return _reject("no_fresh_quote", "No live bid to place the stop under. Nothing sent.")
        failure = _price_checks(parsed.stop_price, _reference_price(quote) or quote.bid, "Stop")
        if failure is not None:
            return failure
        if parsed.stop_price >= quote.bid:
            return _reject(
                "stop_above_bid", f"Stop {parsed.stop_price} must be below the bid {quote.bid}. Nothing sent."
            )
        order_type, price = "STP", parsed.stop_price
    elif parsed.action == "CLOSE" or parsed.order_type == "MKT":
        book = _market_book(facts)
        if isinstance(book, Rejection):
            return book
        order_type, price = "MKT", book[0]
    else:
        assert parsed.limit_price is not None
        reference = _band_reference(facts)
        if isinstance(reference, Rejection):
            return reference
        failure = _price_checks(parsed.limit_price, reference, "Limit")
        if failure is not None:
            return failure
        order_type, price = "LMT", parsed.limit_price
    if fixed_quantity is not None:
        quantity = fixed_quantity
    elif parsed.action == "CLOSE" or parsed.fraction == "all":
        quantity = sellable
    elif parsed.fraction == "half":
        quantity = held // 2
    elif parsed.notional is not None:
        quantity = _floor_int(parsed.notional / price)
        if quantity < 1:
            return _reject(
                "amount_too_small", f"{_usd(parsed.notional)} is less than one share at {price}. Nothing sent."
            )
    else:
        assert parsed.quantity is not None
        quantity = parsed.quantity
    if quantity < 1 or quantity > sellable:
        return _reject(
            "insufficient_shares",
            f"Can't sell {quantity} {symbol}: {sellable} available ({held} held, {reserved} reserved). "
            "No short sales. Nothing sent.",
        )
    return _validated(
        parsed, contract, kind="order", quantity=quantity, order_type=order_type,
        limit_price=parsed.limit_price if order_type == "LMT" else None,
        stop_price=parsed.stop_price if order_type == "STP" else None,
        reference_price=price,
    )


def validate(
    parsed: ParsedOrder,
    *,
    facts: LiveFacts,
    contract: ContractRef | None,
    message_date: datetime,
) -> ValidatedOrder | Rejection:
    """Apply every order rule before a manual order is queued.

    Order of checks: not_paper → CANCEL/CANCEL_ALL accepted (a cancel only
    ever reduces activity, so staleness, hours and the bridge never block it)
    → stale_message → market_closed → bridge_offline → CLOSE_ALL accepted →
    unknown_symbol → exclusivity (CLOSE on a bot symbol becomes ``bot_flatten``;
    anything else there is ``bot_holds_symbol``) → BUY rules (kill_switch,
    account_unavailable, quote/price, amount_too_small, order_too_large,
    manual_exposure_cap, rate_limited, then the stop: invalid_tick,
    stop_not_below_entry, manual_entry_risk_over_1pct) or sell rules
    (insufficient_shares, quote/price, stop_above_bid). Exits never see the
    kill switch, the rate limit, the exposure budget or any entry-stop rule.
    """
    if not isinstance(parsed, ParsedOrder) or not isinstance(facts, LiveFacts):
        raise TypeError("validate takes a ParsedOrder and LiveFacts")
    _require_aware(message_date, "message_date")
    if not facts.accounts_all_paper:
        return _reject("not_paper", _NOT_PAPER)
    if parsed.action == "CANCEL":
        return ValidatedOrder(kind="cancel", action="CANCEL", parsed=parsed, cancel_ref=parsed.cancel_ref)
    if parsed.action == "CANCEL_ALL":
        return ValidatedOrder(kind="cancel_all", action="CANCEL_ALL", parsed=parsed, cancel_ref="all")
    age = facts.now - message_date
    if age > MAX_MESSAGE_AGE or age < -MAX_MESSAGE_CLOCK_SKEW:
        return _reject(
            "stale_message",
            f"Message is {age.total_seconds():.0f}s old (limit 60s); NOT sent. Send it again if you "
            "still want it.",
        )
    environment = _environment(facts)
    if environment is not None:
        return environment
    if parsed.action == "CLOSE_ALL":
        return ValidatedOrder(kind="close_all", action="CLOSE_ALL", parsed=parsed)
    return _validate_symbol_order(parsed, facts=facts, contract=contract, fixed_quantity=None)


def revalidate_claimed(
    row: ManualOrderRow, *, facts: LiveFacts, contract: ContractRef | None
) -> ValidatedOrder | Rejection:
    """Re-apply the order rules to a claimed row right before ``placeOrder``.

    Uses the row's resolved quantity and its expiry instead of the message age.
    A row whose kind changed since it was queued (the bot now holds the symbol,
    or the bot bracket it was meant to flatten is gone) or whose contract
    changed is ``state_changed``. Build ``facts`` with
    ``ledger_facts(..., exclude_order_id=row.id)`` so the row does not reserve
    against itself.
    """
    if row.action in CANCEL_ACTIONS:
        raise ValueError("cancel rows are never sent")
    if row.status != "sending":
        raise ValueError("only a claimed (sending) row can be re-validated")
    if not facts.accounts_all_paper:
        return _reject("not_paper", _NOT_PAPER)
    if facts.now >= row.expires_at:
        return _reject("stale_message", "The order expired before it could be sent. Nothing sent.")
    environment = _environment(facts)
    if environment is not None:
        return environment
    if row.action == "CLOSE_ALL":
        return ValidatedOrder(
            kind="close_all", action="CLOSE_ALL", parsed=ParsedOrder(action="CLOSE_ALL", source=row.source)
        )
    assert row.symbol is not None
    fixed: int | None = None
    if row.action == "CLOSE":
        parsed = ParsedOrder(action="CLOSE", source=row.source, symbol=row.symbol)
        fixed = row.quantity
    elif row.action == "SELL_STOP":
        parsed = ParsedOrder(
            action="SELL_STOP", source=row.source, symbol=row.symbol, quantity=row.quantity,
            order_type="STP", stop_price=row.stop_price,
        )
    else:
        if row.action == "BUY" and row.entry_stop_price is None:
            # A row queued before migration 17 (or written around the parser): never sent without its stop.
            return _reject(
                "missing_entry_stop",
                f"This buy has no protective stop, so it is not sent; send it again as {_ENTRY_STOP_EXAMPLE}.",
            )
        parsed = ParsedOrder(
            action=row.action, source=row.source, symbol=row.symbol, quantity=row.quantity,
            order_type=row.order_type, limit_price=row.limit_price,
            entry_stop_price=row.entry_stop_price if row.action == "BUY" else None,
        )
    result = _validate_symbol_order(parsed, facts=facts, contract=contract, fixed_quantity=fixed)
    if isinstance(result, ValidatedOrder):
        if result.kind != row.kind:
            return _reject(
                "state_changed",
                f"{row.symbol} moved between the bot and you since the order was queued. Nothing sent.",
            )
        if row.con_id is not None and result.con_id != row.con_id:
            return _reject("state_changed", f"{row.symbol} resolved to a different contract. Nothing sent.")
    return result


def facts_to_audit(facts: LiveFacts) -> dict[str, object]:
    """A JSON-ready snapshot of the facts an order was validated against."""
    quote = facts.quote
    return {
        "now": facts.now.isoformat(),
        "accounts_all_paper": facts.accounts_all_paper,
        "kill_switch_active": facts.kill_switch_active,
        "bridge_heartbeat_age_s": facts.bridge_heartbeat_age_s,
        "net_liquidation": None if facts.net_liquidation is None else str(facts.net_liquidation),
        "quote": None if quote is None else {
            "bid": None if quote.bid is None else str(quote.bid),
            "ask": None if quote.ask is None else str(quote.ask),
            "last": None if quote.last is None else str(quote.last),
            "observed_at": quote.observed_at.isoformat(),
            "real_time": quote.real_time,
        },
        "owner_position_qty": str(facts.owner_position_qty),
        "open_manual_sell_qty": facts.open_manual_sell_qty,
        "resting_stop_qty": str(facts.resting_stop_qty),
        "bot_symbols": sorted(facts.bot_symbols),
        "manual_open_buy_notional_today": str(facts.manual_open_buy_notional_today),
        "recent_manual_orders_10min": facts.recent_manual_orders_10min,
    }


def _summary(
    action: str, symbol: str | None, quantity: int | None, order_type: str | None,
    limit_price: Decimal | None, stop_price: Decimal | None, cancel_ref: str | None,
) -> str:
    if action == "CANCEL":
        return f"CANCEL #{cancel_ref}"
    if action in ("CANCEL_ALL", "CLOSE_ALL"):
        return action
    parts = [action] + [
        str(value)
        for value in (quantity, symbol, order_type, limit_price, stop_price)
        if value is not None
    ]
    return " ".join(parts)


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

_COLUMNS = (
    "id", "created_at", "updated_at", "chat_id", "message_id", "update_id", "source",
    "raw_text", "action", "symbol", "con_id", "primary_exchange", "quantity", "order_type",
    "limit_price", "stop_price", "target_order_ref", "status", "reject_code", "error",
    "ibkr_order_id", "perm_id", "filled_qty", "avg_fill_price", "expires_at",
    "cancel_requested", "audit_json",
)
# Migration 17's column is read when the database has it and is NULL
# otherwise, so every exit keeps working while v17 is still pending; a buy
# needs it to be recorded at all (ManualSchemaPending).
_SELECT_V17 = f"SELECT {', '.join(_COLUMNS)}, {ENTRY_STOP_COLUMN} FROM autotrader_manual_orders"
_SELECT_V12 = f"SELECT {', '.join(_COLUMNS)}, NULL AS {ENTRY_STOP_COLUMN} FROM autotrader_manual_orders"
_INSERT_V17 = (
    f"INSERT INTO autotrader_manual_orders ({', '.join(_COLUMNS)}, {ENTRY_STOP_COLUMN}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)}, ?)"
)
_INSERT_V12 = (
    f"INSERT INTO autotrader_manual_orders ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ManualOrderRow:
    id: str
    created_at: datetime
    updated_at: datetime
    chat_id: int
    message_id: int
    update_id: int
    source: Source
    raw_text: str
    action: Action
    symbol: str | None
    con_id: int | None
    primary_exchange: str | None
    quantity: int | None
    order_type: OrderType | None
    limit_price: Decimal | None
    stop_price: Decimal | None
    target_order_ref: str | None
    status: OrderStatus
    reject_code: str | None
    error: str | None
    ibkr_order_id: int | None
    perm_id: int | None
    filled_qty: int
    avg_fill_price: Decimal | None
    expires_at: datetime
    cancel_requested: bool
    audit: dict[str, object] = field(compare=False)
    # D5: a BUY's protective stop (migration 17); None on every other row and
    # on a buy recorded before it (never sent: revalidate_claimed refuses it).
    entry_stop_price: Decimal | None = None

    @property
    def short_id(self) -> str:
        return short_id(self.id)

    @property
    def order_ref(self) -> str:
        return order_ref(self.id)

    @property
    def kind(self) -> OrderKind:
        if self.action == "CANCEL":
            return "cancel"
        if self.action == "CANCEL_ALL":
            return "cancel_all"
        if self.action == "CLOSE_ALL":
            return "close_all"
        if self.action == "CLOSE" and self.quantity is None:
            return "bot_flatten"
        return "order"

    @property
    def side(self) -> Side | None:
        if self.action == "BUY":
            return "BUY"
        return "SELL" if self.action in SELL_SIDE_ACTIONS else None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def remaining_qty(self) -> int | None:
        return None if self.quantity is None else max(0, self.quantity - self.filled_qty)

    def summary(self) -> str:
        return _summary(
            self.action, self.symbol, self.quantity, self.order_type, self.limit_price,
            self.stop_price, self.target_order_ref,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelOutcome:
    ref: str
    result: CancelResult
    cancelled_unsent: tuple[str, ...] = ()
    flagged_live: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerFacts:
    open_manual_sell_qty: int
    counted_order_refs: frozenset[str]
    manual_open_buy_notional_today: Decimal
    recent_manual_orders_10min: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatUsage:
    usage_date: date
    calls: int
    reserved_usd: Decimal
    spent_usd: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class BridgeHeartbeat:
    updated_at: datetime
    pid: int
    phase: str


def _ts(value: datetime, name: str = "timestamp") -> str:
    """Fixed-width UTC text so SQL string comparison orders timestamps correctly."""
    return _require_aware(value, name).astimezone(UTC).isoformat(timespec="microseconds")


def _from_ts(value: object) -> datetime:
    return _require_aware(datetime.fromisoformat(str(value)), "stored timestamp")


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _row(values: tuple[object, ...]) -> ManualOrderRow:
    data = dict(zip((*_COLUMNS, ENTRY_STOP_COLUMN), values, strict=True))
    audit = json.loads(str(data["audit_json"]))
    return ManualOrderRow(
        id=str(data["id"]),
        created_at=_from_ts(data["created_at"]),
        updated_at=_from_ts(data["updated_at"]),
        chat_id=int(str(data["chat_id"])),
        message_id=int(str(data["message_id"])),
        update_id=int(str(data["update_id"])),
        source=str(data["source"]),  # type: ignore[arg-type]
        raw_text=str(data["raw_text"]),
        action=str(data["action"]),  # type: ignore[arg-type]
        symbol=_optional_str(data["symbol"]),
        con_id=_optional_int(data["con_id"]),
        primary_exchange=_optional_str(data["primary_exchange"]),
        quantity=_optional_int(data["quantity"]),
        order_type=_optional_str(data["order_type"]),  # type: ignore[arg-type]
        limit_price=_optional_decimal(data["limit_price"]),
        stop_price=_optional_decimal(data["stop_price"]),
        target_order_ref=_optional_str(data["target_order_ref"]),
        status=str(data["status"]),  # type: ignore[arg-type]
        reject_code=_optional_str(data["reject_code"]),
        error=_optional_str(data["error"]),
        ibkr_order_id=_optional_int(data["ibkr_order_id"]),
        perm_id=_optional_int(data["perm_id"]),
        filled_qty=int(str(data["filled_qty"])),
        avg_fill_price=_optional_decimal(data["avg_fill_price"]),
        expires_at=_from_ts(data["expires_at"]),
        cancel_requested=bool(data["cancel_requested"]),
        audit=audit if isinstance(audit, dict) else {"raw": audit},
        entry_stop_price=_optional_decimal(data[ENTRY_STOP_COLUMN]),
    )


async def _has_entry_stop_column(db: aiosqlite.Connection) -> bool:
    """Whether migration 17 is applied to this ledger (its column exists)."""
    async with db.execute(
        "SELECT 1 FROM pragma_table_info('autotrader_manual_orders') WHERE name=?", (ENTRY_STOP_COLUMN,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def _select(db: aiosqlite.Connection) -> str:
    return _SELECT_V17 if await _has_entry_stop_column(db) else _SELECT_V12


async def _fetch_one(db: aiosqlite.Connection, where: str, params: tuple[object, ...]) -> ManualOrderRow | None:
    async with db.execute(f"{await _select(db)} WHERE {where}", params) as cursor:
        values = await cursor.fetchone()
    return None if values is None else _row(tuple(values))


async def _fetch_all(db: aiosqlite.Connection, where: str, params: tuple[object, ...]) -> list[ManualOrderRow]:
    async with db.execute(f"{await _select(db)} WHERE {where}", params) as cursor:
        rows = await cursor.fetchall()
    return [_row(tuple(values)) for values in rows]


async def entry_stop_ready() -> bool:
    """True once migration 17 is applied: only then can a buy, which must carry its
    stop, be recorded. The assistant refuses /buy with ``manual_schema_pending``
    before that and keeps every exit working (it tolerates a pending v17)."""
    async with get_db() as db:
        return await _has_entry_stop_column(db)


def _with_event(audit: Mapping[str, object], event: Mapping[str, object]) -> str:
    updated = dict(audit)
    previous = updated.get("events")
    updated["events"] = [*(previous if isinstance(previous, list) else []), dict(event)]
    return _dumps(updated)


def _new_order_id() -> str:
    return uuid4().hex


async def record_update(
    update_id: int,
    *,
    chat_id: int,
    message_id: int | None,
    kind: str,
    received_at: datetime,
) -> bool:
    """Record an inbound Telegram update before acting on it; False if already seen.

    Commits immediately. Pass ``chat_id=0`` for an update that has no chat.
    """
    _require_int(update_id, "update_id", minimum=0)
    _require_int(chat_id, "chat_id")
    if message_id is not None:
        _require_int(message_id, "message_id", minimum=1)
    if not isinstance(kind, str) or not 1 <= len(kind) <= 64:
        raise ValueError("kind must be a short non-empty string")
    stamp = _ts(received_at, "received_at")
    async with transaction() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO autotrader_telegram_updates "
            "(update_id, received_at, chat_id, message_id, kind) VALUES (?, ?, ?, ?, ?)",
            (update_id, stamp, chat_id, message_id, kind),
        )
        return cursor.rowcount == 1


async def mark_update_processed(update_id: int, *, outcome: str, processed_at: datetime) -> bool:
    """Record how an update was handled (the first outcome wins)."""
    _require_int(update_id, "update_id", minimum=0)
    if not isinstance(outcome, str) or not outcome.strip():
        raise ValueError("outcome must be a non-empty string")
    stamp = _ts(processed_at, "processed_at")
    async with transaction() as db:
        cursor = await db.execute(
            "UPDATE autotrader_telegram_updates SET outcome=?, processed_at=? "
            "WHERE update_id=? AND processed_at IS NULL",
            (outcome[:500], stamp, update_id),
        )
        return cursor.rowcount == 1


async def max_update_id() -> int | None:
    """Highest recorded update id; the next getUpdates offset is this + 1."""
    async with get_db() as db:
        async with db.execute("SELECT MAX(update_id) FROM autotrader_telegram_updates") as cursor:
            row = await cursor.fetchone()
    return None if row is None or row[0] is None else int(row[0])


async def _apply_cancel(db: aiosqlite.Connection, ref: str, *, now: datetime, by: str) -> CancelOutcome:
    stamp = _ts(now, "now")
    if ref == "all":
        targets = await _fetch_all(
            db,
            "status IN ('queued','sending','sent','partially_filled') "
            "AND action NOT IN ('CANCEL','CANCEL_ALL') ORDER BY created_at, id",
            (),
        )
        if not targets:
            return CancelOutcome(ref=ref, result="no_active_order", detail="no open manual orders")
    else:
        matches = await _fetch_all(db, "substr(id, 1, 6)=? ORDER BY created_at, id", (ref,))
        if len(matches) > 1:
            return CancelOutcome(ref=ref, result="ambiguous_order_ref", detail="id matches several orders")
        if not matches:
            return CancelOutcome(ref=ref, result="no_active_order", detail="no such order")
        target = matches[0]
        if target.action in CANCEL_ACTIONS or target.status not in CANCELLABLE_STATUSES:
            return CancelOutcome(ref=ref, result="no_active_order", detail=f"order is {target.status}")
        targets = [target]
    unsent: list[str] = []
    live: list[str] = []
    for target in targets:
        event = {"at": stamp, "event": "cancel_requested", "by": by, "prior_status": target.status}
        if target.status == "queued":
            # Never sent: cancel it here, in the same transaction, so the
            # bridge can never claim it.
            await db.execute(
                "UPDATE autotrader_manual_orders SET status='cancelled', cancel_requested=1, "
                "error=?, updated_at=?, audit_json=? WHERE id=? AND status='queued'",
                ("cancelled by the owner before it was sent", stamp, _with_event(target.audit, event), target.id),
            )
            unsent.append(target.id)
        else:
            await db.execute(
                "UPDATE autotrader_manual_orders SET cancel_requested=1, updated_at=?, audit_json=? WHERE id=?",
                (stamp, _with_event(target.audit, event), target.id),
            )
            live.append(target.id)
    return CancelOutcome(ref=ref, result="applied", cancelled_unsent=tuple(unsent), flagged_live=tuple(live))


async def request_cancel(ref: str, *, now: datetime) -> CancelOutcome:
    """Cancel one manual order by short id, or every live one with ``'all'``.

    A ``queued`` row becomes ``cancelled`` at once and can never be claimed; a
    ``sending``/``sent``/``partially_filled`` row gets ``cancel_requested=1``
    and the bridge cancels only that row's own IBKR order id(s).
    """
    if not isinstance(ref, str) or not (ref == "all" or _SHORT_ID_RE.fullmatch(ref)):
        raise ValueError("cancel reference must be a 6-character lowercase short id or 'all'")
    async with transaction() as db:
        return await _apply_cancel(db, ref, now=now, by="request_cancel")


async def _unique_order_id(db: aiosqlite.Connection) -> str:
    # The owner types the first 6 characters, so they must stay unique forever.
    for _ in range(32):
        candidate = _new_order_id()
        if not _ROW_ID_RE.fullmatch(candidate):
            raise RuntimeError("manual order ids must be 32 lowercase hex characters")
        async with db.execute(
            "SELECT 1 FROM autotrader_manual_orders WHERE substr(id, 1, 6)=?", (short_id(candidate),)
        ) as cursor:
            if await cursor.fetchone() is None:
                return candidate
    raise RuntimeError("could not allocate a unique manual order short id")


async def queue_order(
    validated: ValidatedOrder,
    *,
    chat_id: int,
    message_id: int,
    update_id: int,
    source: Source,
    raw_text: str,
    message_date: datetime,
    audit: Mapping[str, object],
    now: datetime | None = None,
) -> str:
    """Persist a validated order and return its row id.

    Idempotent per (chat_id, message_id): a repeat returns the existing id and
    changes nothing. The order must re-derive from ``raw_text`` through the
    deterministic parser. ``expires_at = message_date + 90 s``. CANCEL and
    CANCEL_ALL are applied in the same transaction (see request_cancel); their
    own row is stored ``sent`` when applied, else ``rejected`` with reject_code
    ``no_active_order`` or ``ambiguous_order_ref``. Every other kind is
    stored ``queued`` for the bridge. A BUY is stored with its entry stop and
    raises ``ManualSchemaPending`` (nothing recorded) on a ledger without
    migration 17; every other order is recorded either way.
    """
    if not isinstance(validated, ValidatedOrder):
        raise TypeError("queue_order takes a ValidatedOrder")
    _require_int(chat_id, "chat_id")
    _require_int(message_id, "message_id", minimum=1)
    _require_int(update_id, "update_id", minimum=0)
    if source != validated.parsed.source:
        raise ValueError("source does not match the parsed order")
    if not isinstance(raw_text, str) or not 1 <= len(raw_text) <= 4096:
        raise ValueError("raw_text must be 1-4096 characters")
    if parse_order_text(raw_text) != validated.parsed:
        # Tripwire: only the deterministic parser of the owner's own words may
        # produce an order; anything else (e.g. a model) is refused here.
        raise ValueError("order does not derive from the message text")
    _require_aware(message_date, "message_date")
    created = _require_aware(now, "now") if now is not None else datetime.now(UTC)
    stamp = _ts(created, "now")
    expires_at = _ts(message_date + ORDER_TTL, "expires_at")
    payload: dict[str, object] = {
        "schema": AUDIT_SCHEMA,
        "caller": dict(audit),
        "parsed": asdict(validated.parsed),
        "validation": {
            "kind": validated.kind,
            "side": validated.side,
            "tif": validated.tif,
            "reference_price": validated.reference_price,
            "estimated_notional": validated.estimated_notional,
            "entry_stop_price": validated.entry_stop_price,
            "entry_risk": validated.entry_risk,
            "message_date": message_date,
        },
    }
    async with transaction() as db:
        async with db.execute(
            "SELECT id FROM autotrader_manual_orders WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        ) as cursor:
            existing = await cursor.fetchone()
        if existing is not None:
            return str(existing[0])
        has_stop_column = await _has_entry_stop_column(db)
        if validated.action == "BUY" and not has_stop_column:
            raise ManualSchemaPending(
                "a buy needs migration 17 (manual-entry-stop) to record its stop; nothing was recorded"
            )
        order_id = await _unique_order_id(db)
        status: OrderStatus = "queued"
        reject_code: str | None = None
        error: str | None = None
        target_ref: str | None = None
        if validated.kind in ("cancel", "cancel_all"):
            target_ref = "all" if validated.kind == "cancel_all" else validated.cancel_ref
            assert target_ref is not None
            outcome = await _apply_cancel(db, target_ref, now=created, by=f"order:{order_id}")
            payload["cancel"] = asdict(outcome)
            if outcome.result == "applied":
                status = "sent"
            else:
                status, reject_code, error = "rejected", outcome.result, outcome.detail
        payload["events"] = [{"at": stamp, "event": "created", "status": status}]
        values: tuple[object, ...] = (
            order_id, stamp, stamp, chat_id, message_id, update_id, source, raw_text,
            validated.action, validated.symbol, validated.con_id, validated.primary_exchange,
            validated.quantity, validated.order_type,
            None if validated.limit_price is None else str(validated.limit_price),
            None if validated.stop_price is None else str(validated.stop_price),
            target_ref, status, reject_code, error, None, None, 0, None, expires_at,
            0, _dumps(payload),
        )
        if has_stop_column:
            entry_stop = None if validated.entry_stop_price is None else str(validated.entry_stop_price)
            await db.execute(_INSERT_V17, (*values, entry_stop))
        else:
            await db.execute(_INSERT_V12, values)
        return order_id


async def claim_next_order(now: datetime) -> ManualOrderRow | None:
    """Atomically expire overdue rows and claim the oldest sendable one.

    One IMMEDIATE transaction: every ``queued`` row with ``expires_at <= now``
    becomes ``expired``; then the oldest ``queued`` row that has not expired
    and has no cancel request becomes ``sending`` and is returned. Two
    concurrent claimers never receive the same row.
    """
    stamp = _ts(now, "now")
    async with transaction() as db:
        for row in await _fetch_all(db, "status='queued' AND expires_at<=?", (stamp,)):
            event = {"at": stamp, "event": "expired", "status": "expired"}
            await db.execute(
                "UPDATE autotrader_manual_orders SET status='expired', error=?, updated_at=?, "
                "audit_json=? WHERE id=? AND status='queued'",
                ("expired before the bridge could send it", stamp, _with_event(row.audit, event), row.id),
            )
        candidate = await _fetch_one(
            db,
            "status='queued' AND cancel_requested=0 AND expires_at>? "
            "AND action NOT IN ('CANCEL','CANCEL_ALL') ORDER BY created_at, id LIMIT 1",
            (stamp,),
        )
        if candidate is None:
            return None
        event = {"at": stamp, "event": "claimed", "status": "sending"}
        cursor = await db.execute(
            "UPDATE autotrader_manual_orders SET status='sending', updated_at=?, audit_json=? "
            "WHERE id=? AND status='queued'",
            (stamp, _with_event(candidate.audit, event), candidate.id),
        )
        if cursor.rowcount != 1:  # pragma: no cover - the IMMEDIATE lock excludes this
            raise RuntimeError("manual order claim lost its row")
        return await _fetch_one(db, "id=?", (candidate.id,))


async def update_order_status(
    order_id: str,
    status: OrderStatus,
    *,
    now: datetime,
    reject_code: str | None = None,
    error: str | None = None,
    ibkr_order_id: int | None = None,
    perm_id: int | None = None,
    filled_qty: int | None = None,
    avg_fill_price: Decimal | None = None,
    event: Mapping[str, object] | None = None,
) -> bool:
    """Apply one monotonic status/fill update; False (nothing written) when refused.

    Refused: a transition outside the state machine (terminal rows never
    change status; ``queued -> sending`` belongs to claim_next_order), fewer
    fills than recorded, more fills than ordered, an IBKR order/perm id that
    differs from the recorded one, or any change to a cancel row. Use
    ``error`` only when the order certainly never reached IBKR; otherwise
    ``unknown``. ``filled_qty`` is cumulative.
    """
    if status not in ORDER_STATUSES:
        raise ValueError(f"unknown manual order status: {status!r}")
    stamp = _ts(now, "now")
    if ibkr_order_id is not None:
        _require_int(ibkr_order_id, "ibkr_order_id", minimum=1)
    if perm_id is not None:
        _require_int(perm_id, "perm_id")
        perm_id = perm_id if perm_id > 0 else None  # TWS reports 0 before assigning one
    if filled_qty is not None:
        _require_int(filled_qty, "filled_qty", minimum=0)
    if avg_fill_price is not None:
        _require_decimal(avg_fill_price, "avg_fill_price", positive=True)
    async with transaction() as db:
        row = await _fetch_one(db, "id=?", (order_id,))
        if row is None:
            raise ManualOrderNotFound(order_id)
        if row.action in CANCEL_ACTIONS:
            return False
        if status != row.status and status not in _TRANSITIONS[row.status]:
            return False
        new_filled = row.filled_qty if filled_qty is None else filled_qty
        if new_filled < row.filled_qty or (row.quantity is not None and new_filled > row.quantity):
            return False
        if ibkr_order_id is not None and row.ibkr_order_id not in (None, ibkr_order_id):
            return False
        if perm_id is not None and row.perm_id not in (None, perm_id):
            return False
        record: dict[str, object] = {"at": stamp, "event": "status", "status": status}
        if reject_code is not None:
            record["reject_code"] = reject_code
        if filled_qty is not None:
            record["filled_qty"] = filled_qty
        if event:
            record["detail"] = dict(event)
        await db.execute(
            "UPDATE autotrader_manual_orders SET status=?, reject_code=COALESCE(?, reject_code), "
            "error=COALESCE(?, error), ibkr_order_id=COALESCE(?, ibkr_order_id), "
            "perm_id=COALESCE(?, perm_id), filled_qty=?, avg_fill_price=COALESCE(?, avg_fill_price), "
            "updated_at=?, audit_json=? WHERE id=?",
            (
                status, reject_code, None if error is None else error[:2000], ibkr_order_id, perm_id,
                new_filled, None if avg_fill_price is None else str(avg_fill_price), stamp,
                _with_event(row.audit, record), order_id,
            ),
        )
        return True


async def mark_unknown_sending_rows(known_order_refs: Collection[str], *, now: datetime) -> tuple[str, ...]:
    """At bridge start: every ``sending`` row whose ``manual:<id>`` orderRef IBKR does
    not know becomes ``unknown`` (log CRITICAL; it is never resent).

    ``known_order_refs`` = orderRefs seen in IBKR open orders, completed orders
    and executions. Known rows stay ``sending`` for normal event handling.
    Returns the ids marked unknown.
    """
    known = {row_id for ref in known_order_refs if (row_id := parse_order_ref(ref)) is not None}
    stamp = _ts(now, "now")
    marked: list[str] = []
    async with transaction() as db:
        for row in await _fetch_all(db, "status='sending' ORDER BY created_at, id", ()):
            if row.id in known:
                continue
            event = {"at": stamp, "event": "unknown_at_restart", "status": "unknown"}
            await db.execute(
                "UPDATE autotrader_manual_orders SET status='unknown', error=?, updated_at=?, "
                "audit_json=? WHERE id=? AND status='sending'",
                (
                    "was sending at restart and IBKR has no order with its orderRef; never resent",
                    stamp, _with_event(row.audit, event), row.id,
                ),
            )
            marked.append(row.id)
    return tuple(marked)


async def get_order(order_id: str) -> ManualOrderRow | None:
    async with get_db() as db:
        return await _fetch_one(db, "id=?", (order_id,))


async def list_active_orders() -> tuple[ManualOrderRow, ...]:
    """Rows that may still reach or sit at IBKR (including ``unknown``), oldest first."""
    async with get_db() as db:
        return tuple(
            await _fetch_all(
                db,
                "status IN ('queued','sending','sent','partially_filled','unknown') ORDER BY created_at, id",
                (),
            )
        )


async def list_cancel_requests() -> tuple[ManualOrderRow, ...]:
    """Rows whose own IBKR order(s) the bridge must cancel."""
    async with get_db() as db:
        return tuple(
            await _fetch_all(
                db,
                "cancel_requested=1 AND status IN ('sending','sent','partially_filled','unknown') "
                "ORDER BY created_at, id",
                (),
            )
        )


async def list_orders_updated_since(since: datetime, *, limit: int = 200) -> tuple[ManualOrderRow, ...]:
    """Rows changed after ``since``, oldest change first (drives status replies)."""
    _require_int(limit, "limit", minimum=1)
    stamp = _ts(since, "since")
    async with get_db() as db:
        return tuple(await _fetch_all(db, "updated_at>? ORDER BY updated_at, id LIMIT ?", (stamp, limit)))


def _buy_exposure(row: ManualOrderRow) -> Decimal:
    validation = row.audit.get("validation")
    estimate = validation.get("estimated_notional") if isinstance(validation, dict) else None
    reference = validation.get("reference_price") if isinstance(validation, dict) else None
    if estimate is None or reference is None:
        # Fail closed: an unmeasurable buy must block further buys, not vanish.
        raise ValueError(f"manual BUY {row.id} has no notional estimate")
    estimated = Decimal(str(estimate))
    fill_price = row.avg_fill_price if row.avg_fill_price is not None else Decimal(str(reference))
    filled = fill_price * row.filled_qty
    if row.status == "filled":
        return filled if row.avg_fill_price is not None else max(filled, estimated)
    if row.status in LIVE_STATUSES:
        return max(estimated, filled)
    return filled


async def ledger_facts(*, symbol: str | None, now: datetime, exclude_order_id: str | None = None) -> LedgerFacts:
    """The manual-ledger inputs of :class:`LiveFacts` at ``now``.

    ``open_manual_sell_qty``: remaining quantity of live (incl. ``unknown``)
    SELL/SELL_STOP/CLOSE rows for ``symbol``; ``counted_order_refs`` are their
    orderRefs (exclude those from the broker-side ``resting_stop_qty``).
    ``manual_open_buy_notional_today``: this New York day's manual buy
    exposure (live rows at their estimate, filled rows at their fills).
    ``recent_manual_orders_10min``: BUY rows created in the last 10 minutes.
    ``exclude_order_id`` leaves out the row being re-validated.
    """
    _require_aware(now, "now")
    excluded = exclude_order_id or ""
    local_day = now.astimezone(MARKET_TIMEZONE).date()
    day_start = datetime.combine(local_day, datetime.min.time(), tzinfo=MARKET_TIMEZONE)
    day_end = datetime.combine(local_day + timedelta(days=1), datetime.min.time(), tzinfo=MARKET_TIMEZONE)
    async with get_db() as db:
        sells: list[ManualOrderRow] = []
        if symbol is not None:
            sells = await _fetch_all(
                db,
                "symbol=? AND action IN ('SELL','SELL_STOP','CLOSE') AND quantity IS NOT NULL "
                "AND status IN ('queued','sending','sent','partially_filled','unknown') AND id!=?",
                (normalize_symbol(symbol), excluded),
            )
        buys = await _fetch_all(
            db, "action='BUY' AND created_at>=? AND created_at<? AND id!=?", (_ts(day_start), _ts(day_end), excluded)
        )
        async with db.execute(
            "SELECT COUNT(*) FROM autotrader_manual_orders WHERE action='BUY' AND created_at>? AND id!=?",
            (_ts(now - RATE_LIMIT_WINDOW), excluded),
        ) as cursor:
            recent = await cursor.fetchone()
    return LedgerFacts(
        open_manual_sell_qty=sum(row.remaining_qty or 0 for row in sells),
        counted_order_refs=frozenset(row.order_ref for row in sells),
        manual_open_buy_notional_today=sum((_buy_exposure(row) for row in buys), Decimal(0)),
        recent_manual_orders_10min=0 if recent is None else int(recent[0]),
    )


def _usage_day(value: date) -> str:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError("usage_date must be a date")
    return value.isoformat()


def _non_negative(value: Decimal, name: str) -> Decimal:
    if _require_decimal(value, name) < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


async def chat_usage_reserve(
    usage_date: date, max_usd: Decimal, estimate_usd: Decimal, *, max_calls: int | None = None
) -> bool:
    """Reserve ``estimate_usd`` of the day's chat budget before a model call.

    False (nothing reserved) when spent + reserved + estimate would exceed
    ``max_usd`` or the day already made ``max_calls`` calls.
    """
    day = _usage_day(usage_date)
    _non_negative(max_usd, "max_usd")
    _non_negative(estimate_usd, "estimate_usd")
    if max_calls is not None:
        _require_int(max_calls, "max_calls", minimum=0)
    async with transaction() as db:
        await db.execute("INSERT OR IGNORE INTO autotrader_chat_usage (usage_date) VALUES (?)", (day,))
        async with db.execute(
            "SELECT calls, reserved_usd, spent_usd FROM autotrader_chat_usage WHERE usage_date=?", (day,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        calls, reserved, spent = int(row[0]), Decimal(str(row[1])), Decimal(str(row[2]))
        if max_calls is not None and calls >= max_calls:
            return False
        if spent + reserved + estimate_usd > max_usd:
            return False
        await db.execute(
            "UPDATE autotrader_chat_usage SET calls=?, reserved_usd=? WHERE usage_date=?",
            (calls + 1, str(reserved + estimate_usd), day),
        )
        return True


async def chat_usage_settle(usage_date: date, estimate_usd: Decimal, actual_usd: Decimal) -> None:
    """Release a reservation and record the actual cost (spend is never lost)."""
    day = _usage_day(usage_date)
    _non_negative(estimate_usd, "estimate_usd")
    _non_negative(actual_usd, "actual_usd")
    async with transaction() as db:
        await db.execute("INSERT OR IGNORE INTO autotrader_chat_usage (usage_date) VALUES (?)", (day,))
        async with db.execute(
            "SELECT reserved_usd, spent_usd FROM autotrader_chat_usage WHERE usage_date=?", (day,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        reserved = max(Decimal(0), Decimal(str(row[0])) - estimate_usd)
        spent = Decimal(str(row[1])) + actual_usd
        await db.execute(
            "UPDATE autotrader_chat_usage SET reserved_usd=?, spent_usd=? WHERE usage_date=?",
            (str(reserved), str(spent), day),
        )


async def chat_usage(usage_date: date) -> ChatUsage:
    day = _usage_day(usage_date)
    async with get_db() as db:
        async with db.execute(
            "SELECT calls, reserved_usd, spent_usd FROM autotrader_chat_usage WHERE usage_date=?", (day,)
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return ChatUsage(usage_date=usage_date, calls=0, reserved_usd=Decimal(0), spent_usd=Decimal(0))
    return ChatUsage(
        usage_date=usage_date, calls=int(row[0]),
        reserved_usd=Decimal(str(row[1])), spent_usd=Decimal(str(row[2])),
    )


async def write_bridge_heartbeat(phase: str, *, now: datetime | None = None, pid: int | None = None) -> None:
    """Upsert the bridge liveness row; manual orders are refused when it is > 20 s old."""
    if not isinstance(phase, str) or not 1 <= len(phase) <= 64:
        raise ValueError("phase must be a short non-empty string")
    stamp = _ts(now if now is not None else datetime.now(UTC), "now")
    process = _require_int(pid if pid is not None else os.getpid(), "pid", minimum=1)
    async with transaction() as db:
        await db.execute(
            "INSERT INTO autotrader_bridge_heartbeat (id, updated_at, pid, phase) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, pid=excluded.pid, "
            "phase=excluded.phase",
            (stamp, process, phase),
        )


async def read_bridge_heartbeat() -> BridgeHeartbeat | None:
    async with get_db() as db:
        async with db.execute("SELECT updated_at, pid, phase FROM autotrader_bridge_heartbeat WHERE id=1") as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return BridgeHeartbeat(updated_at=_from_ts(row[0]), pid=int(row[1]), phase=str(row[2]))


async def bridge_heartbeat_age(now: datetime) -> float | None:
    """Seconds since the last bridge heartbeat, or None if there never was one."""
    _require_aware(now, "now")
    heartbeat = await read_bridge_heartbeat()
    return None if heartbeat is None else (now - heartbeat.updated_at).total_seconds()


__all__ = [
    "ACTIONS", "CANCEL_ACTIONS", "ENTRY_STOP_COLUMN", "ENTRY_STOP_WORD", "EXIT_ACTIONS", "LIVE_STATUSES",
    "MANUAL_SCHEMA_PENDING", "MAX_MANUAL_ENTRY_RISK_FRACTION_OF_NET_LIQ", "ORDER_STATUSES",
    "REFUSAL_CODES", "REJECT_CODES", "TERMINAL_STATUSES", "WORD_TICKERS",
    "BridgeHeartbeat", "CancelOutcome", "ChatUsage", "ContractRef", "LedgerFacts", "LiveFacts",
    "ManualOrderNotFound", "ManualOrderRow", "ManualSchemaPending", "ParseRefusal", "ParsedOrder", "Quote",
    "Rejection", "ValidatedOrder", "bridge_heartbeat_age", "chat_usage", "chat_usage_reserve",
    "chat_usage_settle", "claim_next_order", "entry_stop_ready", "facts_to_audit", "get_order",
    "is_kill_switch_command", "is_manual_window_open", "ledger_facts", "list_active_orders",
    "list_cancel_requests", "list_orders_updated_since", "manual_order_window",
    "mark_unknown_sending_rows", "mark_update_processed", "max_update_id", "normalize_symbol",
    "order_ref", "parse_order_ref", "parse_order_text", "queue_order", "read_bridge_heartbeat",
    "record_update", "request_cancel", "revalidate_claimed", "short_id", "update_order_status",
    "validate", "write_bridge_heartbeat",
]
