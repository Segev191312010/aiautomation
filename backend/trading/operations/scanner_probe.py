"""Read-only scanner, news and market-data-line probe on raw ``ibapi``.

``autotraderctl shadow probe --scanner-only`` answers the auto-screener
questions before any production scan changes:

* which scan codes and filter fields TWS serves for US stocks;
* how each candidate scan code behaves (rows, latency, errors) with the live
  screener's parameters, with and without the built-in ``aboveVolume`` and
  ``marketCapAbove`` subscription fields;
* which news providers the login carries;
* whether 16 concurrent market-data lines resolve as fast as 10.

It runs alone: a loopback host, the PAPER port and the probe's own client id
are its only inputs. It never opens the database, the Keychain or Claude. It
sends only the requests in ``ALLOWED_CLIENT_CALLS`` (tests pin that no order,
account or position call exists), cancels every subscription it opens, and
never keeps an account id: the managedAccounts list TWS volunteers on connect
is reduced to a paper verdict and dropped, and every string in the report is
scrubbed of account-like ids.
"""
from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import statistics
import sys
import threading
import time
from typing import Any
import xml.etree.ElementTree as ElementTree
from zoneinfo import ZoneInfo

from trading.operations.paths import atomic_private_write

PROBE_VERSION = 1

# TWS accepts one connection per client ID, and a second login with a running
# child's ID knocks that child's data connection over (the old probe used 17,
# ORB's). The probe owns 29, the top of the research/probe range 25-29:
# strategy children grow upward from the data client id (next free 24), and
# the research recorder holds 28.
PROBE_CLIENT_ID = 29
# The supervised client map (shadow_service.STRATEGY_CLIENT_OFFSET,
# screener_service.SCREENER_CLIENT_ID_OFFSET, the bridge's order_client_id,
# telegram_tools.TELEGRAM_TWS_CLIENT_ID, recorder.RESEARCH_CLIENT_ID), kept
# literal so the probe imports none of those modules; a test derives the same
# ids from them.
_DATA_CLIENT_OFFSETS = (0, 2, 3, 5, 6, 7)  # orb, screener, momo, vwap, gap, momo-ap
_FIXED_CLIENT_IDS = (21, 28)  # Telegram assistant, research recorder
DOCUMENTED_DATA_CLIENT_ID = 17
DOCUMENTED_ORDER_CLIENT_ID = 18

# The repository's only PAPER boundary (tws_actor.PAPER_TWS_HOST/PORT).
PAPER_HOST = "127.0.0.1"
PAPER_PORT = 7497

# Every TWS client method the probe may call. Nothing here places, modifies or
# cancels an order, or reads or writes account state; a test checks the source.
ALLOWED_CLIENT_CALLS = frozenset(
    {
        "connect",
        "run",
        "disconnect",
        "serverVersion",
        "reqManagedAccts",
        "reqMarketDataType",
        "reqScannerParameters",
        "reqScannerSubscription",
        "cancelScannerSubscription",
        "reqNewsProviders",
        "reqMktData",
        "cancelMktData",
    }
)

SCAN_INSTRUMENT = "STK"
SCAN_LOCATION = "STK.US.MAJOR"
# IBKR's code for ordinary company shares ("COMMON" is silently ignored).
SCAN_STOCK_TYPE = "CORP"
SCAN_ABOVE_PRICE = 10.0
SCAN_BELOW_PRICE = 300.0
# The live screener's row count (tws_actor.SCREENER_SCAN_ROWS).
SCAN_ROWS = 50
CANDIDATE_SCAN_CODES = (
    "HOT_BY_VOLUME",
    "TOP_PERC_GAIN",
    "MOST_ACTIVE",
    "HIGH_OPEN_GAP",
    "TOP_OPEN_PERC_GAIN",
    "TOP_TRADE_COUNT",
    "HOT_BY_PRICE",
    "HIGH_VS_13W_HL",
)
# Built-in ScannerSubscription fields only (no TagValue filter options).
# marketCapAbove is tried at $2B in both plausible units: millions of USD
# (TWS's scanner screens) and plain USD; a variant whose rows equal the base
# rows was most likely ignored.
SCAN_VARIANTS: Mapping[str, Mapping[str, float | int]] = {
    "base": {},
    "above_volume": {"aboveVolume": 1_000_000},
    "market_cap_musd": {"marketCapAbove": 2_000.0},
    "market_cap_usd": {"marketCapAbove": 2_000_000_000.0},
}
MAX_CONCURRENT_SCANS = 3
SAMPLE_ROWS = 10

# Liquid names with their listing exchanges (contracts are SMART/USD).
LINE_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("SPY", "ARCA"),
    ("QQQ", "NASDAQ"),
    ("AAPL", "NASDAQ"),
    ("MSFT", "NASDAQ"),
    ("NVDA", "NASDAQ"),
    ("AMZN", "NASDAQ"),
    ("META", "NASDAQ"),
    ("GOOGL", "NASDAQ"),
    ("TSLA", "NASDAQ"),
    ("AMD", "NASDAQ"),
    ("JPM", "NYSE"),
    ("XOM", "NYSE"),
    ("UNH", "NYSE"),
    ("V", "NYSE"),
    ("MA", "NYSE"),
    ("COST", "NASDAQ"),
)
# 10 is tws_actor.MAX_SNAPSHOT_LINES today and 16 the candidate. Alternating
# rounds keep TWS's warm contract cache from favouring whichever runs second.
LINE_RUNS = (10, 16, 10, 16)
MAX_LINES = 16
# tws_actor.MARKET_SNAPSHOT_SETTLE_SECONDS / SNAPSHOT_START_INTERVAL_SECONDS:
# lines are held exactly as long as the production snapshot holds them.
LINE_SETTLE_SECONDS = 0.25
LINE_START_INTERVAL_SECONDS = 0.05

REAL_TIME_MARKET_DATA = 1
MARKET_DATA_TYPE_NAMES = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed_frozen"}
_TICK_NAMES = {
    1: "bid",
    2: "ask",
    4: "last",
    6: "high",
    7: "low",
    8: "volume",
    9: "close",
    14: "open",
}
_ALL_PRICE_TICKS = frozenset({1, 2, 4, 6, 7, 9, 14})
# IBKR codes 2100-2199 are informational (farm status); they never fail a request.
_INFO_CODES = range(2100, 2200)
# Market-data warnings that leave the stream running.
_LINE_WARNING_CODES = frozenset({10090, 10167})
# Connection-level failures: client id in use, cannot connect, not connected.
_FATAL_CONNECTION_CODES = frozenset({326, 502, 504})
_MAX_MESSAGE_CHARS = 200
_MAX_UNATTRIBUTED = 50

# Paper (DU/DF) and live (U + 6+ digits) IBKR account ids.
ACCOUNT_ID_PATTERN = re.compile(r"(?:DU|DF)\d+|U\d{6,}")
_SCAN_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{2,40}$")
_NEW_YORK = ZoneInfo("America/New_York")

FIXTURE_SCAN_CODES = "scan_codes.json"
FIXTURE_SCAN_ROWS = "scan_rows.json"

logger = logging.getLogger("autotrader.scanner_probe")


class ScannerProbeError(RuntimeError):
    """The scanner probe cannot run or cannot write its output."""


class ScannerProbeConfigError(ScannerProbeError, ValueError):
    """Host, port or client id is outside the probe's PAPER boundary."""


def child_client_ids(data_client_id: int, order_client_id: int) -> frozenset[int]:
    """Every TWS client id a supervised child, the bridge or the assistant uses."""
    return frozenset(
        {data_client_id + offset for offset in _DATA_CLIENT_OFFSETS}
        | {order_client_id, *_FIXED_CLIENT_IDS}
    )


def require_probe_client_id(
    client_id: int,
    *,
    data_client_id: int = DOCUMENTED_DATA_CLIENT_ID,
    order_client_id: int = DOCUMENTED_ORDER_CLIENT_ID,
) -> None:
    """Refuse a client id a child holds (or 0, TWS's manual-order client)."""
    if isinstance(client_id, bool) or not isinstance(client_id, int) or client_id <= 0:
        raise ScannerProbeConfigError("the probe needs a positive TWS client id")
    if client_id in child_client_ids(data_client_id, order_client_id):
        raise ScannerProbeConfigError(
            f"TWS client id {client_id} belongs to a supervised child, the bridge or the "
            f"assistant; the probe uses its own id {PROBE_CLIENT_ID}"
        )


def scrub(text: str) -> str:
    return ACCOUNT_ID_PATTERN.sub("[account]", text)


def scrub_report(value: Any) -> Any:
    """Every string (keys included) with account-like ids replaced."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, Mapping):
        return {scrub(str(key)): scrub_report(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_report(item) for item in value]
    return value


def require_no_account_ids(payload: Any) -> None:
    """Tripwire before anything is written: no account-like id may remain."""
    text = json.dumps(payload, sort_keys=True, default=str)
    if ACCOUNT_ID_PATTERN.search(text):
        raise ScannerProbeError("refusing to write output that contains an account-like id")


def et_phase(now_utc: datetime) -> str:
    """The US equity session phase by the New York clock (holidays not considered)."""
    local = now_utc.astimezone(_NEW_YORK)
    if local.weekday() >= 5:
        return "weekend"
    minutes = local.hour * 60 + local.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre-market"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "after-hours"
    return "overnight"


@dataclass(frozen=True, slots=True)
class ScannerProbeSettings:
    host: str = PAPER_HOST
    port: int = PAPER_PORT
    client_id: int = PROBE_CLIENT_ID
    scan_codes: tuple[str, ...] = CANDIDATE_SCAN_CODES
    variants: tuple[str, ...] = tuple(SCAN_VARIANTS)
    max_concurrent_scans: int = MAX_CONCURRENT_SCANS
    line_symbols: tuple[tuple[str, str], ...] = LINE_SYMBOLS
    line_runs: tuple[int, ...] = LINE_RUNS
    handshake_timeout_seconds: float = 10.0
    accounts_timeout_seconds: float = 3.0
    parameters_timeout_seconds: float = 30.0
    news_timeout_seconds: float = 10.0
    scan_timeout_seconds: float = 20.0
    line_timeout_seconds: float = 8.0
    scan_start_interval_seconds: float = 0.2
    line_start_interval_seconds: float = LINE_START_INTERVAL_SECONDS
    line_settle_seconds: float = LINE_SETTLE_SECONDS
    line_run_pause_seconds: float = 1.0
    deadline_seconds: float = 300.0

    def validate(self) -> None:
        if self.host != PAPER_HOST:
            raise ScannerProbeConfigError(f"the probe connects only to loopback {PAPER_HOST}")
        if self.port != PAPER_PORT:
            raise ScannerProbeConfigError(f"the probe connects only to the PAPER port {PAPER_PORT}")
        require_probe_client_id(self.client_id)
        if not self.scan_codes or any(
            not _SCAN_CODE_PATTERN.fullmatch(code) for code in self.scan_codes
        ):
            raise ScannerProbeConfigError("scan codes must be upper-case IBKR scan codes")
        if not self.variants or any(variant not in SCAN_VARIANTS for variant in self.variants):
            raise ScannerProbeConfigError(f"variants must be among {sorted(SCAN_VARIANTS)}")
        if not 1 <= self.max_concurrent_scans <= MAX_CONCURRENT_SCANS:
            raise ScannerProbeConfigError(
                f"at most {MAX_CONCURRENT_SCANS} concurrent scanner subscriptions"
            )
        if any(not 1 <= lines <= MAX_LINES for lines in self.line_runs):
            raise ScannerProbeConfigError(f"line runs hold between 1 and {MAX_LINES} lines")
        timings = (
            self.handshake_timeout_seconds,
            self.accounts_timeout_seconds,
            self.parameters_timeout_seconds,
            self.news_timeout_seconds,
            self.scan_timeout_seconds,
            self.line_timeout_seconds,
            self.deadline_seconds,
        )
        if any(not 0 < value < float("inf") for value in timings):
            raise ScannerProbeConfigError("probe timeouts must be finite and positive")
        spacing = (
            self.scan_start_interval_seconds,
            self.line_start_interval_seconds,
            self.line_settle_seconds,
            self.line_run_pause_seconds,
        )
        if any(not 0 <= value < float("inf") for value in spacing):
            raise ScannerProbeConfigError("probe spacing must be finite and non-negative")


@dataclass(slots=True)
class _ScanState:
    scan_code: str
    variant: str
    sent_at: float
    rows: list[tuple[int, str, int, str]] = field(default_factory=list)
    first_row_at: float | None = None
    end_at: float | None = None
    error_at: float | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    timed_out: bool = False


@dataclass(slots=True)
class _LineState:
    symbol: str
    sent_at: float
    data_type: int | None = None
    ticks: dict[int, float] = field(default_factory=dict)
    ready_at: float | None = None
    full_at: float | None = None
    error_at: float | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    timed_out: bool = False


class _ProbeState:
    """Callback sink: TWS's reader thread writes, the probe thread waits.

    Method names are the EWrapper callbacks. Every callback holds the
    condition only to record and notify; none calls back into TWS.
    """

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.version = 0
        self.next_valid_id: int | None = None
        self.paper_login: bool | None = None
        self.account_count = 0
        self.closed = False
        self.fatal: dict[str, Any] | None = None
        self.system_codes: Counter[int] = Counter()
        self.unattributed: list[dict[str, Any]] = []
        self.callback_failures: list[str] = []
        self.scanner_xml: str | None = None
        self.news: list[dict[str, str]] | None = None
        self.scans: dict[int, _ScanState] = {}
        self.lines: dict[int, _LineState] = {}

    def _changed(self) -> None:
        self.version += 1
        self.condition.notify_all()

    def nextValidId(self, order_id: Any, *_: Any) -> None:
        with self.condition:
            try:
                self.next_valid_id = int(order_id)
            except (TypeError, ValueError):
                self.next_valid_id = -1
            self._changed()

    def managedAccounts(self, accounts: Any, *_: Any) -> None:
        # Reduced to a verdict on arrival; the ids themselves are never kept.
        names = [item.strip() for item in str(accounts or "").split(",") if item.strip()]
        with self.condition:
            self.account_count = len(names)
            self.paper_login = bool(names) and all(name.startswith("DU") for name in names)
            self._changed()

    def error(self, request_id: Any, code: Any, message: Any = "", *_: Any) -> None:
        now = time.monotonic()
        try:
            request = int(request_id)
        except (TypeError, ValueError):
            request = -1
        try:
            error_code = int(code)
        except (TypeError, ValueError):
            error_code = -1
        text = scrub(str(message or ""))[:_MAX_MESSAGE_CHARS]
        with self.condition:
            scan = self.scans.get(request)
            line = self.lines.get(request)
            if error_code in _INFO_CODES:
                self.system_codes[error_code] += 1
            elif scan is not None:
                scan.errors.append(
                    {"code": error_code, "message": text, "after_cancel": scan.cancelled}
                )
                if not scan.cancelled and scan.error_at is None:
                    scan.error_at = now
            elif line is not None:
                line.errors.append(
                    {"code": error_code, "message": text, "after_cancel": line.cancelled}
                )
                if (
                    not line.cancelled
                    and line.error_at is None
                    and error_code not in _LINE_WARNING_CODES
                ):
                    line.error_at = now
            else:
                if error_code in _FATAL_CONNECTION_CODES and self.fatal is None:
                    self.fatal = {"code": error_code, "message": text}
                if len(self.unattributed) < _MAX_UNATTRIBUTED:
                    self.unattributed.append(
                        {"request_id": request, "code": error_code, "message": text}
                    )
            self._changed()

    def connectionClosed(self, *_: Any) -> None:
        with self.condition:
            self.closed = True
            self._changed()

    def scannerParameters(self, xml: Any, *_: Any) -> None:
        with self.condition:
            self.scanner_xml = str(xml or "")
            self._changed()

    def scannerData(self, request_id: Any, rank: Any, details: Any, *_: Any) -> None:
        now = time.monotonic()
        contract = getattr(details, "contract", details)
        try:
            row_rank = int(rank)
        except (TypeError, ValueError):
            row_rank = -1
        try:
            con_id = int(getattr(contract, "conId", 0) or 0)
        except (TypeError, ValueError):
            con_id = 0
        symbol = str(getattr(contract, "symbol", "") or "")
        exchange = str(
            getattr(contract, "primaryExchange", "") or getattr(contract, "exchange", "") or ""
        )
        with self.condition:
            scan = self.scans.get(int(request_id))
            # A live subscription re-sends its whole list periodically; only
            # the first list counts.
            if scan is None or scan.end_at is not None or scan.cancelled:
                return
            if scan.first_row_at is None:
                scan.first_row_at = now
            scan.rows.append((row_rank, symbol, con_id, exchange))
            self._changed()

    def scannerDataEnd(self, request_id: Any, *_: Any) -> None:
        now = time.monotonic()
        with self.condition:
            scan = self.scans.get(int(request_id))
            if scan is not None and scan.end_at is None and not scan.cancelled:
                scan.end_at = now
            self._changed()

    def newsProviders(self, providers: Any, *_: Any) -> None:
        items = [
            {
                "code": scrub(str(getattr(provider, "code", "") or "")),
                "name": scrub(str(getattr(provider, "name", "") or "")),
            }
            for provider in (providers or ())
        ]
        with self.condition:
            self.news = items
            self._changed()

    def marketDataType(self, request_id: Any, data_type: Any, *_: Any) -> None:
        with self.condition:
            line = self.lines.get(int(request_id))
            if line is None or line.cancelled:
                return
            try:
                line.data_type = int(data_type)
            except (TypeError, ValueError):
                line.data_type = -1
            self._update_line(line)
            self._changed()

    def tickPrice(self, request_id: Any, tick_type: Any, price: Any, *_: Any) -> None:
        try:
            value = float(price)
        except (TypeError, ValueError):
            return
        # IBKR sends -1/0 when a side or statistic is unavailable.
        if not value > 0 or value == float("inf"):
            return
        self._tick(request_id, tick_type)

    def tickSize(self, request_id: Any, tick_type: Any, size: Any, *_: Any) -> None:
        try:
            value = float(size)
        except (TypeError, ValueError):
            return
        if int(tick_type) != 8 or not value >= 0:
            return
        self._tick(request_id, tick_type)

    def _tick(self, request_id: Any, tick_type: Any) -> None:
        now = time.monotonic()
        with self.condition:
            line = self.lines.get(int(request_id))
            kind = int(tick_type)
            if line is None or line.cancelled or kind not in _TICK_NAMES:
                return
            line.ticks.setdefault(kind, now)
            self._update_line(line)
            self._changed()

    @staticmethod
    def _update_line(line: _LineState) -> None:
        """The production snapshot's readiness: live data, a price, prior close, volume."""
        if line.data_type != REAL_TIME_MARKET_DATA:
            return
        ticks = line.ticks
        has_price = 4 in ticks or (1 in ticks and 2 in ticks)
        if line.ready_at is None and has_price and 9 in ticks and 8 in ticks:
            line.ready_at = time.monotonic()
        if line.ready_at is not None and line.full_at is None and _ALL_PRICE_TICKS.issubset(ticks):
            line.full_at = time.monotonic()

    def record_callback_failure(self, name: str, exc: BaseException) -> None:
        with self.condition:
            if len(self.callback_failures) < _MAX_UNATTRIBUTED:
                self.callback_failures.append(scrub(f"{name}: {type(exc).__name__}: {exc}"))
            self._changed()


_CALLBACKS = (
    "nextValidId",
    "managedAccounts",
    "error",
    "connectionClosed",
    "scannerParameters",
    "scannerData",
    "scannerDataEnd",
    "newsProviders",
    "marketDataType",
    "tickPrice",
    "tickSize",
)


def _official_client_factory(state: _ProbeState) -> Any:
    """Import and build the official ibapi client only for a live run."""
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    # ibapi logs raw inbound messages (the account list among them) at DEBUG
    # and every callback at INFO; neither may reach a handler.
    logging.getLogger("ibapi").setLevel(logging.WARNING)

    def forward(name: str) -> Callable[..., None]:
        def method(self: Any, *args: Any) -> None:
            try:
                getattr(state, name)(*args)
            except Exception as exc:  # noqa: BLE001 - a probe bug must not kill TWS's reader
                state.record_callback_failure(name, exc)

        method.__name__ = name
        return method

    def initialize(self: Any) -> None:
        EWrapper.__init__(self)
        EClient.__init__(self, self)

    namespace: dict[str, Any] = {"__init__": initialize}
    namespace.update({name: forward(name) for name in _CALLBACKS})
    return type("ScannerProbeClient", (EWrapper, EClient), namespace)()


def _subscription(scan_code: str, variant: str) -> Any:
    from ibapi.scanner import ScannerSubscription

    subscription = ScannerSubscription()
    subscription.instrument = SCAN_INSTRUMENT
    subscription.locationCode = SCAN_LOCATION
    subscription.scanCode = scan_code
    subscription.stockTypeFilter = SCAN_STOCK_TYPE
    subscription.numberOfRows = SCAN_ROWS
    subscription.abovePrice = SCAN_ABOVE_PRICE
    subscription.belowPrice = SCAN_BELOW_PRICE
    for name, value in SCAN_VARIANTS[variant].items():
        setattr(subscription, name, value)
    return subscription


def _contract(symbol: str, primary_exchange: str) -> Any:
    from ibapi.contract import Contract

    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.primaryExchange = primary_exchange
    return contract


def summarize_scanner_parameters(
    xml_text: str,
    *,
    instrument: str = SCAN_INSTRUMENT,
    location: str = SCAN_LOCATION,
    candidates: Sequence[str] = CANDIDATE_SCAN_CODES,
) -> dict[str, Any]:
    """Scan codes and filter-field codes for one instrument; never the XML itself."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        return {"parsed": False, "error": f"ParseError: {exc}"[:_MAX_MESSAGE_CHARS]}

    def text(element: ElementTree.Element, tag: str) -> str:
        return (element.findtext(tag) or "").strip()

    def items(value: str) -> set[str]:
        return {item.strip() for item in value.split(",") if item.strip()}

    scan_codes = sorted(
        {
            text(scan_type, "scanCode")
            for scan_type in root.iter("ScanType")
            if text(scan_type, "scanCode") and instrument in items(text(scan_type, "instruments"))
        }
    )
    location_found = any(
        text(element, "locationCode") == location for element in root.iter("Location")
    )
    instrument_filters: set[str] = set()
    for element in root.iter("Instrument"):
        if text(element, "type") == instrument:
            instrument_filters |= {item.upper() for item in items(text(element, "filters"))}
    filter_codes: set[str] = set()
    for element in root.iter():
        if not element.tag.endswith("Filter") or element.find("id") is None:
            continue
        if instrument_filters and text(element, "id").upper() not in instrument_filters:
            continue
        filter_codes |= {
            text(field_element, "code")
            for field_element in element.iter("AbstractField")
            if text(field_element, "code")
        }
    summary: dict[str, Any] = {
        "parsed": True,
        "instrument": instrument,
        "location": location,
        "location_found": location_found,
        "scan_code_count": len(scan_codes),
        "scan_codes": scan_codes,
        "candidates": {code: code in scan_codes for code in candidates},
        "filter_scope": instrument if instrument_filters else "all",
        "filter_code_count": len(filter_codes),
        "filter_codes": sorted(filter_codes),
    }
    if not scan_codes:
        # Tag names and counts only, so a parser mismatch can be fixed offline.
        summary["structure"] = dict(Counter(element.tag for element in root.iter()).most_common(30))
    return summary


def _milliseconds(start: float, end: float | None) -> int | None:
    return None if end is None else max(0, round((end - start) * 1000))


def _distribution(values: Sequence[int]) -> dict[str, int | None]:
    if not values:
        return {"median": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered)),
        "p90": ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))],
        "max": ordered[-1],
    }


class _ScannerProbe:
    def __init__(self, settings: ScannerProbeSettings, state: _ProbeState, client: Any) -> None:
        self.settings = settings
        self.state = state
        self.client = client
        self.deadline = time.monotonic() + settings.deadline_seconds
        self.deadline_reached = False
        self.thread: threading.Thread | None = None
        self.requests: Counter[str] = Counter()
        self._next_request_id = 1
        self._scan_rows: dict[tuple[str, str], list[str]] = {}

    def _request_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _wait_for(self, predicate: Callable[[], bool], timeout: float) -> bool:
        end = min(time.monotonic() + timeout, self.deadline)
        with self.state.condition:
            while not predicate():
                remaining = end - time.monotonic()
                if remaining <= 0 or self.state.closed:
                    return predicate()
                self.state.condition.wait(remaining)
            return True

    def _wait_for_change(self, version: int, delay: float) -> None:
        with self.state.condition:
            if self.state.version == version and not self.state.closed:
                self.state.condition.wait(max(0.0, delay))

    def _past_deadline(self) -> bool:
        if time.monotonic() >= self.deadline:
            self.deadline_reached = True
        return self.deadline_reached

    def _stop_reason(self) -> str | None:
        """Why no further request may start, or None while the probe may go on."""
        if self._past_deadline():
            return "probe deadline reached"
        with self.state.condition:
            closed = self.state.closed
        return "TWS closed the connection" if closed else None

    def _run_client(self) -> None:
        try:
            self.client.run()
        except Exception as exc:  # noqa: BLE001 - reported, never raised from the reader
            self.state.record_callback_failure("run", exc)

    # -- connection -----------------------------------------------------
    def connect(self) -> dict[str, Any]:
        settings = self.settings
        started = time.monotonic()
        base: dict[str, Any] = {
            "host": settings.host,
            "port": settings.port,
            "client_id": settings.client_id,
        }
        # ibapi's connect() reads the API handshake synchronously and never
        # times out if the socket opens but TWS does not answer (an API dialog
        # pending, say); it runs on its own thread so the probe can give up.
        failures: list[BaseException] = []

        def open_connection() -> None:
            try:
                self.client.connect(settings.host, settings.port, settings.client_id)
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                failures.append(exc)

        connector = threading.Thread(
            target=open_connection, name="scanner-probe-connect", daemon=True
        )
        connector.start()
        connector.join(min(settings.handshake_timeout_seconds, max(0.0, self.deadline - started)))
        if connector.is_alive():
            return {**base, "ok": False, "error": "TWS did not complete the API handshake in time"}
        if failures:
            exc = failures[0]
            return {**base, "ok": False, "error": scrub(f"{type(exc).__name__}: {exc}")}
        self.thread = threading.Thread(
            target=self._run_client, name="scanner-probe-tws", daemon=True
        )
        self.thread.start()
        handshake = self._wait_for(
            lambda: self.state.next_valid_id is not None or self.state.fatal is not None,
            settings.handshake_timeout_seconds,
        )
        with self.state.condition:
            fatal = self.state.fatal
            closed = self.state.closed
        if fatal is not None or not handshake or closed:
            reason = (
                f"TWS error {fatal['code']}: {fatal['message']}"
                if fatal is not None
                else "TWS closed the connection"
                if closed
                else "no TWS handshake (nextValidId) in time"
            )
            return {**base, "ok": False, "error": reason}
        handshake_ms = _milliseconds(started, time.monotonic())
        # TWS volunteers managedAccounts with the handshake; ask once if it has not.
        if not self._wait_for(
            lambda: self.state.paper_login is not None, settings.accounts_timeout_seconds
        ):
            self.client.reqManagedAccts()
            self._wait_for(
                lambda: self.state.paper_login is not None, settings.accounts_timeout_seconds
            )
        with self.state.condition:
            paper_login = self.state.paper_login
        if paper_login is not True:
            reason = (
                "TWS did not report the login's accounts"
                if paper_login is None
                else "refusing a TWS login with a non-paper account"
            )
            return {**base, "ok": False, "handshake_ms": handshake_ms, "error": reason}
        # The production data path asks for live data explicitly; so does the probe.
        self.client.reqMarketDataType(REAL_TIME_MARKET_DATA)
        server_version: Any = None
        try:
            server_version = self.client.serverVersion()
        except Exception:  # noqa: BLE001 - informational only
            server_version = None
        return {
            **base,
            "ok": True,
            "handshake_ms": handshake_ms,
            "paper_login": True,
            "server_version": server_version,
            "market_data_type_requested": MARKET_DATA_TYPE_NAMES[REAL_TIME_MARKET_DATA],
        }

    def disconnect(self) -> None:
        try:
            self.client.disconnect()
        except Exception as exc:  # noqa: BLE001
            self.state.record_callback_failure("disconnect", exc)
        if self.thread is not None:
            self.thread.join(timeout=5.0)

    # -- scanner parameters and news ------------------------------------
    def scanner_parameters(self) -> dict[str, Any]:
        stop = self._stop_reason()
        if stop is not None:
            return {"ok": False, "error": f"skipped: {stop}"}
        started = time.monotonic()
        self.client.reqScannerParameters()
        self.requests["scanner_parameters"] += 1
        received = self._wait_for(
            lambda: self.state.scanner_xml is not None,
            self.settings.parameters_timeout_seconds,
        )
        elapsed = _milliseconds(started, time.monotonic())
        with self.state.condition:
            xml_text, self.state.scanner_xml = self.state.scanner_xml, None
        if not received or xml_text is None:
            return {"ok": False, "ms": elapsed, "error": "no scanner parameters in time"}
        summary = summarize_scanner_parameters(
            xml_text, candidates=self.settings.scan_codes
        )
        return {
            "ok": bool(summary.get("parsed")),
            "ms": elapsed,
            "xml_bytes": len(xml_text.encode("utf-8")),
            **summary,
        }

    def news_providers(self) -> dict[str, Any]:
        stop = self._stop_reason()
        if stop is not None:
            return {"ok": False, "error": f"skipped: {stop}"}
        started = time.monotonic()
        self.client.reqNewsProviders()
        self.requests["news_providers"] += 1
        received = self._wait_for(
            lambda: self.state.news is not None, self.settings.news_timeout_seconds
        )
        elapsed = _milliseconds(started, time.monotonic())
        with self.state.condition:
            providers = list(self.state.news or ())
        if not received:
            return {"ok": False, "ms": elapsed, "error": "no news providers in time"}
        return {"ok": True, "ms": elapsed, "count": len(providers), "providers": providers}

    # -- scans -----------------------------------------------------------
    def _start_scan(self, scan_code: str, variant: str) -> tuple[int, _ScanState]:
        request_id = self._request_id()
        scan = _ScanState(scan_code=scan_code, variant=variant, sent_at=time.monotonic())
        with self.state.condition:
            self.state.scans[request_id] = scan
        self.requests["scanner_subscriptions"] += 1
        try:
            self.client.reqScannerSubscription(
                request_id, _subscription(scan_code, variant), [], []
            )
        except Exception as exc:  # noqa: BLE001 - recorded on the scan
            with self.state.condition:
                scan.errors.append(
                    {"code": -1, "message": scrub(f"{type(exc).__name__}: {exc}"), "after_cancel": False}
                )
                scan.error_at = time.monotonic()
        return request_id, scan

    def _cancel_scan(self, request_id: int, scan: _ScanState) -> None:
        with self.state.condition:
            scan.cancelled = True
            scan.timed_out = scan.end_at is None and scan.error_at is None
        self.requests["scanner_cancels"] += 1
        try:
            self.client.cancelScannerSubscription(request_id)
        except Exception as exc:  # noqa: BLE001
            self.state.record_callback_failure("cancelScannerSubscription", exc)

    def scans(self, variants: Sequence[str]) -> list[dict[str, Any]]:
        settings = self.settings
        pending = deque((code, variant) for variant in variants for code in settings.scan_codes)
        active: dict[int, _ScanState] = {}
        started: list[_ScanState] = []
        next_start = time.monotonic()
        stop = self._stop_reason()
        while (pending or active) and stop is None:
            with self.state.condition:
                version = self.state.version
                now = time.monotonic()
                finished = [
                    request_id
                    for request_id, scan in active.items()
                    if scan.end_at is not None
                    or scan.error_at is not None
                    or now - scan.sent_at >= settings.scan_timeout_seconds
                ]
            for request_id in finished:
                self._cancel_scan(request_id, active.pop(request_id))
            stop = self._stop_reason()
            if stop is not None:
                break
            while (
                pending
                and len(active) < settings.max_concurrent_scans
                and time.monotonic() >= next_start
            ):
                request_id, scan = self._start_scan(*pending.popleft())
                active[request_id] = scan
                started.append(scan)
                next_start = time.monotonic() + settings.scan_start_interval_seconds
            if not pending and not active:
                break
            wake = [scan.sent_at + settings.scan_timeout_seconds for scan in active.values()]
            if pending and len(active) < settings.max_concurrent_scans:
                wake.append(next_start)
            wake.append(self.deadline)
            self._wait_for_change(version, min(wake) - time.monotonic())
            stop = self._stop_reason()
        for request_id, scan in active.items():
            self._cancel_scan(request_id, scan)
        results = [self._scan_result(scan) for scan in started]
        for code, variant in pending:
            results.append({"scan_code": code, "variant": variant, "skipped": stop})
        return results

    def _scan_result(self, scan: _ScanState) -> dict[str, Any]:
        with self.state.condition:
            rows = sorted(scan.rows, key=lambda row: row[0])
            errors = list(scan.errors)
        self._scan_rows[(scan.scan_code, scan.variant)] = [row[1] for row in rows]
        active_errors = [error for error in errors if not error["after_cancel"]]
        return {
            "scan_code": scan.scan_code,
            "variant": scan.variant,
            "fields": dict(SCAN_VARIANTS[scan.variant]),
            "rows": len(rows),
            "first_row_ms": _milliseconds(scan.sent_at, scan.first_row_at),
            "total_ms": _milliseconds(scan.sent_at, scan.end_at),
            "completed": scan.end_at is not None,
            "timed_out": scan.timed_out,
            "error_codes": sorted({error["code"] for error in active_errors}),
            "errors": active_errors[:3],
            "late_error_codes": sorted(
                {error["code"] for error in errors if error["after_cancel"]}
            ),
            "sample_symbols": [row[1] for row in rows[:SAMPLE_ROWS]],
            "sample_rows": [
                {"rank": rank, "symbol": symbol, "con_id": con_id, "exchange": exchange}
                for rank, symbol, con_id, exchange in rows[:SAMPLE_ROWS]
            ],
        }

    def compare_with_base(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for result in results:
            if result.get("variant") == "base" or "skipped" in result:
                continue
            base = self._scan_rows.get((result["scan_code"], "base"))
            rows = self._scan_rows.get((result["scan_code"], result["variant"]))
            if base is None or rows is None:
                continue
            result["vs_base"] = {
                "same_rows": rows == base,
                "dropped": [symbol for symbol in base if symbol not in set(rows)][:SAMPLE_ROWS],
                "added": [symbol for symbol in rows if symbol not in set(base)][:SAMPLE_ROWS],
            }
        return results

    # -- market-data lines ---------------------------------------------
    def _start_line(self, symbol: str, exchange: str) -> tuple[int, _LineState]:
        request_id = self._request_id()
        line = _LineState(symbol=symbol, sent_at=time.monotonic())
        with self.state.condition:
            self.state.lines[request_id] = line
        self.requests["market_data_lines"] += 1
        try:
            self.client.reqMktData(request_id, _contract(symbol, exchange), "", False, False, [])
        except Exception as exc:  # noqa: BLE001 - recorded on the line
            with self.state.condition:
                line.errors.append(
                    {"code": -1, "message": scrub(f"{type(exc).__name__}: {exc}"), "after_cancel": False}
                )
                line.error_at = time.monotonic()
        return request_id, line

    def _cancel_line(self, request_id: int, line: _LineState) -> None:
        with self.state.condition:
            line.cancelled = True
            line.timed_out = line.ready_at is None and line.error_at is None
        self.requests["market_data_cancels"] += 1
        try:
            self.client.cancelMktData(request_id)
        except Exception as exc:  # noqa: BLE001
            self.state.record_callback_failure("cancelMktData", exc)

    def _line_finished(self, line: _LineState, now: float) -> bool:
        settings = self.settings
        return (
            line.error_at is not None
            or line.full_at is not None
            or (line.ready_at is not None and now >= line.ready_at + settings.line_settle_seconds)
            or now - line.sent_at >= settings.line_timeout_seconds
        )

    def line_run(self, lines: int) -> dict[str, Any]:
        settings = self.settings
        pending = deque(settings.line_symbols)
        active: dict[int, _LineState] = {}
        started: list[_LineState] = []
        run_started = time.monotonic()
        next_start = run_started
        max_active = 0
        last_finished = run_started
        stop = self._stop_reason()
        while (pending or active) and stop is None:
            with self.state.condition:
                version = self.state.version
                now = time.monotonic()
                finished = [
                    request_id
                    for request_id, line in active.items()
                    if self._line_finished(line, now)
                ]
            for request_id in finished:
                self._cancel_line(request_id, active.pop(request_id))
                last_finished = time.monotonic()
            stop = self._stop_reason()
            if stop is not None:
                break
            while pending and len(active) < lines and time.monotonic() >= next_start:
                request_id, line = self._start_line(*pending.popleft())
                active[request_id] = line
                started.append(line)
                max_active = max(max_active, len(active))
                next_start = time.monotonic() + settings.line_start_interval_seconds
            if not pending and not active:
                break
            wake = [line.sent_at + settings.line_timeout_seconds for line in active.values()]
            wake.extend(
                line.ready_at + settings.line_settle_seconds
                for line in active.values()
                if line.ready_at is not None
            )
            if pending and len(active) < lines:
                wake.append(next_start)
            wake.append(self.deadline)
            self._wait_for_change(version, min(wake) - time.monotonic())
            stop = self._stop_reason()
        for request_id, line in active.items():
            self._cancel_line(request_id, line)
            last_finished = time.monotonic()
        per_symbol = [self._line_result(line) for line in started]
        ready = [item["ready_ms"] for item in per_symbol if item["ready_ms"] is not None]
        ready_offsets = [
            _milliseconds(run_started, line.ready_at)
            for line in started
            if line.ready_at is not None
        ]
        return {
            "lines": lines,
            "symbols": len(started),
            "stopped": stop,
            "skipped_symbols": [symbol for symbol, _ in pending],
            "max_concurrent": max_active,
            "total_ms": _milliseconds(run_started, last_finished),
            "last_ready_ms": max(ready_offsets) if ready_offsets else None,
            "ready": len(ready),
            "not_ready": [item["symbol"] for item in per_symbol if item["ready_ms"] is None],
            "ready_ms": _distribution(ready),
            "per_symbol": per_symbol,
        }

    def _line_result(self, line: _LineState) -> dict[str, Any]:
        with self.state.condition:
            ticks = dict(line.ticks)
            errors = list(line.errors)
        active_errors = [error for error in errors if not error["after_cancel"]]
        return {
            "symbol": line.symbol,
            "ready_ms": _milliseconds(line.sent_at, line.ready_at),
            "full_ms": _milliseconds(line.sent_at, line.full_at),
            "data_type": (
                None
                if line.data_type is None
                else MARKET_DATA_TYPE_NAMES.get(line.data_type, str(line.data_type))
            ),
            "ticks": sorted(_TICK_NAMES[kind] for kind in ticks),
            "timed_out": line.timed_out,
            "error_codes": sorted({error["code"] for error in active_errors}),
            "errors": active_errors[:2],
        }

    def lines(self) -> dict[str, Any]:
        runs: list[dict[str, Any]] = []
        for index, lines in enumerate(self.settings.line_runs):
            stop = self._stop_reason()
            if stop is not None:
                runs.append({"lines": lines, "skipped": stop})
                continue
            if index and self.settings.line_run_pause_seconds:
                time.sleep(self.settings.line_run_pause_seconds)
            runs.append(self.line_run(lines))
        observed: Counter[str] = Counter(
            item["data_type"] or "none"
            for run in runs
            for item in run.get("per_symbol", ())
        )
        comparison: dict[str, Any] = {}
        for lines in sorted(set(self.settings.line_runs)):
            done = [run for run in runs if run["lines"] == lines and "skipped" not in run]
            comparison[str(lines)] = {
                "runs": len(done),
                "total_ms": [run["total_ms"] for run in done],
                "last_ready_ms": [run["last_ready_ms"] for run in done],
                "median_ready_ms": [run["ready_ms"]["median"] for run in done],
                "not_ready": sum(len(run["not_ready"]) for run in done),
                "error_codes": sorted(
                    {
                        code
                        for run in done
                        for item in run["per_symbol"]
                        for code in item["error_codes"]
                    }
                ),
            }
        return {
            "symbols": [symbol for symbol, _ in self.settings.line_symbols],
            "market_data_type_requested": MARKET_DATA_TYPE_NAMES[REAL_TIME_MARKET_DATA],
            "market_data_types_observed": dict(observed),
            "comparison": comparison,
            "runs": runs,
        }

    def cancel_everything(self) -> None:
        """Release any subscription still open (an interrupt or a probe bug)."""
        with self.state.condition:
            scans = [(rid, scan) for rid, scan in self.state.scans.items() if not scan.cancelled]
            lines = [(rid, line) for rid, line in self.state.lines.items() if not line.cancelled]
        for request_id, scan in scans:
            self._cancel_scan(request_id, scan)
        for request_id, line in lines:
            self._cancel_line(request_id, line)


def _scan_summary(scans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = [scan for scan in scans if scan.get("variant") == "base" and "skipped" not in scan]
    variants: dict[str, dict[str, int]] = {}
    for scan in scans:
        if "skipped" in scan:
            continue
        entry = variants.setdefault(
            scan["variant"],
            {"scans": 0, "with_rows": 0, "with_errors": 0, "timed_out": 0, "changed_vs_base": 0},
        )
        entry["scans"] += 1
        entry["with_rows"] += 1 if scan["rows"] else 0
        entry["with_errors"] += 1 if scan["error_codes"] else 0
        entry["timed_out"] += 1 if scan["timed_out"] else 0
        if scan.get("vs_base") and not scan["vs_base"]["same_rows"]:
            entry["changed_vs_base"] += 1
    return {
        "codes_with_rows": [scan["scan_code"] for scan in base if scan["rows"]],
        "codes_without_rows": [scan["scan_code"] for scan in base if not scan["rows"]],
        "codes_with_errors": {
            scan["scan_code"]: scan["error_codes"] for scan in base if scan["error_codes"]
        },
        "base_first_row_ms": _distribution(
            [scan["first_row_ms"] for scan in base if scan["first_row_ms"] is not None]
        ),
        "base_total_ms": _distribution(
            [scan["total_ms"] for scan in base if scan["total_ms"] is not None]
        ),
        "variants": variants,
    }


def run_scanner_probe(
    settings: ScannerProbeSettings | None = None,
    *,
    client_factory: Callable[[_ProbeState], Any] | None = None,
) -> dict[str, Any]:
    """Run every read-only section once and return the scrubbed report."""
    settings = settings or ScannerProbeSettings()
    settings.validate()
    started_wall = datetime.now(timezone.utc)
    started = time.monotonic()
    state = _ProbeState()
    report: dict[str, Any] = {
        "probe": "scanner",
        "probe_version": PROBE_VERSION,
        "read_only": True,
        "orders": "none: no order, account or position request is ever sent",
        "source": "tws" if client_factory is None else "injected",
        "started_at_utc": started_wall.isoformat(),
        "et_clock": started_wall.astimezone(_NEW_YORK).isoformat(),
        "et_phase": et_phase(started_wall),
        "settings": {
            "host": settings.host,
            "port": settings.port,
            "client_id": settings.client_id,
            "instrument": SCAN_INSTRUMENT,
            "location": SCAN_LOCATION,
            "stock_type_filter": SCAN_STOCK_TYPE,
            "above_price": SCAN_ABOVE_PRICE,
            "below_price": SCAN_BELOW_PRICE,
            "rows_requested": SCAN_ROWS,
            "scan_codes": list(settings.scan_codes),
            "variants": {name: dict(SCAN_VARIANTS[name]) for name in settings.variants},
            "max_concurrent_scans": settings.max_concurrent_scans,
            "scan_timeout_seconds": settings.scan_timeout_seconds,
            "line_runs": list(settings.line_runs),
            "line_timeout_seconds": settings.line_timeout_seconds,
        },
    }
    client = (client_factory or _official_client_factory)(state)
    probe = _ScannerProbe(settings, state, client)
    closed_by_tws = False
    try:
        report["connection"] = probe.connect()
        if report["connection"]["ok"]:
            report["scanner_parameters"] = probe.scanner_parameters()
            report["news_providers"] = probe.news_providers()
            # What the screener design needs most runs first: the plain scans,
            # then the line timing, then the filter variants. Nothing overlaps,
            # so scanner traffic never skews the line timing.
            base = [variant for variant in settings.variants if variant == "base"]
            others = [variant for variant in settings.variants if variant != "base"]
            scans = probe.scans(base)
            report["lines"] = probe.lines()
            scans += probe.scans(others)
            report["scans"] = probe.compare_with_base(scans)
            report["scan_summary"] = _scan_summary(report["scans"])
    finally:
        probe.cancel_everything()
        with state.condition:
            closed_by_tws = state.closed
        probe.disconnect()
    with state.condition:
        report["system_message_codes"] = {
            str(code): count for code, count in sorted(state.system_codes.items())
        }
        report["unattributed_errors"] = list(state.unattributed)
        report["callback_failures"] = list(state.callback_failures)
    report["tws_closed_connection"] = closed_by_tws
    report["requests"] = dict(sorted(probe.requests.items()))
    report["deadline_reached"] = probe.deadline_reached
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_ms"] = _milliseconds(started, time.monotonic())
    scrubbed = scrub_report(report)
    require_no_account_ids(scrubbed)
    return scrubbed


def fixtures_from_report(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Trimmed public test fixtures: scan codes, filter codes, sample rows."""
    parameters = report.get("scanner_parameters") or {}
    meta = {
        "source": report.get("source"),
        "captured_at_utc": report.get("started_at_utc"),
        "et_phase": report.get("et_phase"),
        "probe_version": report.get("probe_version"),
    }
    scan_codes = {
        **meta,
        "instrument": parameters.get("instrument", SCAN_INSTRUMENT),
        "location": parameters.get("location", SCAN_LOCATION),
        "location_found": parameters.get("location_found"),
        "scan_codes": list(parameters.get("scan_codes") or ()),
        "filter_scope": parameters.get("filter_scope"),
        "filter_codes": list(parameters.get("filter_codes") or ()),
    }
    scan_rows = {
        **meta,
        "rank_base": 0,
        "scans": [
            {
                "scan_code": scan["scan_code"],
                "variant": scan["variant"],
                "rows": [
                    {
                        "rank": int(row["rank"]),
                        "symbol": str(row["symbol"]),
                        "con_id": int(row["con_id"]),
                        "exchange": str(row["exchange"]),
                    }
                    for row in scan.get("sample_rows", ())
                ],
            }
            for scan in report.get("scans", ())
            if "skipped" not in scan
        ],
    }
    payloads = {FIXTURE_SCAN_CODES: scan_codes, FIXTURE_SCAN_ROWS: scan_rows}
    for payload in payloads.values():
        require_no_account_ids(payload)
    return payloads


def write_fixtures(report: Mapping[str, Any], directory: Path) -> list[Path]:
    payloads = fixtures_from_report(report)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, payload in payloads.items():
        path = directory / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def format_summary(report: Mapping[str, Any]) -> str:
    """A few human lines; the JSON report holds the detail."""
    connection = report.get("connection") or {}
    lines = [
        f"scanner probe ({report.get('et_phase')}, client {connection.get('client_id')}): "
        + ("connected" if connection.get("ok") else f"not connected: {connection.get('error')}")
    ]
    parameters = report.get("scanner_parameters") or {}
    if parameters:
        lines.append(
            f"  scanner parameters: {parameters.get('scan_code_count')} STK scan codes, "
            f"{parameters.get('filter_code_count')} filter codes, {parameters.get('ms')} ms"
        )
    news = report.get("news_providers") or {}
    if news.get("ok"):
        codes = ", ".join(provider["code"] for provider in news.get("providers", ()))
        lines.append(f"  news providers: {codes or 'none'}")
    for scan in report.get("scans", ()):
        if "skipped" in scan:
            lines.append(f"  {scan['scan_code']} [{scan['variant']}]: skipped")
            continue
        errors = f" errors {scan['error_codes']}" if scan["error_codes"] else ""
        lines.append(
            f"  {scan['scan_code']} [{scan['variant']}]: {scan['rows']} rows, first "
            f"{scan['first_row_ms']} ms, total {scan['total_ms']} ms{errors}"
        )
    for run in (report.get("lines") or {}).get("runs", ()):
        if "skipped" in run:
            continue
        lines.append(
            f"  {run['lines']} lines: {run['ready']}/{run['symbols']} ready, last ready "
            f"{run['last_ready_ms']} ms, median {run['ready_ms']['median']} ms"
        )
    return "\n".join(lines)


def run_cli(
    *,
    out: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
    fixtures_dir: Path | None = None,
    from_report: Path | None = None,
) -> int:
    """``autotraderctl shadow probe --scanner-only``: no config, database or Keychain."""
    if from_report is not None:
        if fixtures_dir is None:
            print("error: --from-report needs --fixtures-dir", file=sys.stderr)
            return 2
        try:
            report = json.loads(from_report.expanduser().read_text(encoding="utf-8"))
            written = write_fixtures(report, fixtures_dir.expanduser())
        except (OSError, ValueError, KeyError, TypeError, ScannerProbeError) as exc:
            print(f"error: cannot rebuild fixtures: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        for path in written:
            print(f"fixture: {path}")
        return 0
    settings = ScannerProbeSettings(
        host=PAPER_HOST if host is None else host,
        port=PAPER_PORT if port is None else port,
        client_id=PROBE_CLIENT_ID if client_id is None else client_id,
    )
    try:
        settings.validate()
    except ScannerProbeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        report = run_scanner_probe(settings)
    except KeyboardInterrupt:
        return 130
    text = json.dumps(report, indent=2) + "\n"
    if out is None:
        print(text, end="")
    else:
        atomic_private_write(out, text.encode("utf-8"))
        print(format_summary(report))
        print(f"report: {out.expanduser()}")
    connected = bool((report.get("connection") or {}).get("ok"))
    if fixtures_dir is not None and connected:
        for path in write_fixtures(report, fixtures_dir.expanduser()):
            print(f"fixture: {path}")
    return 0 if connected else 1


__all__ = [
    "ACCOUNT_ID_PATTERN",
    "ALLOWED_CLIENT_CALLS",
    "CANDIDATE_SCAN_CODES",
    "LINE_RUNS",
    "LINE_SYMBOLS",
    "PROBE_CLIENT_ID",
    "SCAN_VARIANTS",
    "ScannerProbeConfigError",
    "ScannerProbeError",
    "ScannerProbeSettings",
    "child_client_ids",
    "et_phase",
    "fixtures_from_report",
    "format_summary",
    "require_no_account_ids",
    "require_probe_client_id",
    "run_cli",
    "run_scanner_probe",
    "scrub",
    "scrub_report",
    "summarize_scanner_parameters",
    "write_fixtures",
]
