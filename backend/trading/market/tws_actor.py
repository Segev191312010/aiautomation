"""Lazy official ``ibapi`` actor for the local TWS PAPER data boundary."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect
import re
import threading
from typing import Any, Protocol

from trading.market.ibkr_boundary import (
    BarBatch,
    DailyBarBatch,
    IBKRContractDetails,
    IBKRDailyBar,
    IBKRScannerResult,
    IBKRTradeBar,
    ScannerRequest,
    is_regular_session_bar,
)
from trading.market.snapshots import MarketSnapshot, MarketSnapshotError
from trading.contracts import AccountSnapshot, QuoteSnapshot, RawAccountPosition

PAPER_TWS_HOST = "127.0.0.1"
PAPER_TWS_PORT = 7497
PACING_ERROR_CODES = frozenset({162, 165, 366, 420})
# IBKR codes 2100-2199 are informational warnings (farm status, fractional-size
# trimming) and may carry a request ID; they never fail the request.
WARNING_CODE_RANGE = range(2100, 2200)
# Two sessions of five-minute RTH bars: ATR(14) is available at the first
# signal bar (09:50) from the prior session's history.
INTRADAY_HISTORY_DURATION = "2 D"
# Today's five-minute bars outside regular hours too (useRTH=0): pre-market
# from 04:00 ET, the regular session, and after-hours.
EXTENDED_HISTORY_DURATION = "1 D"
INTRADAY_BAR_SIZE = "5 mins"
_RTH_ONLY = 1
_ALL_HOURS = 0
# formatDate=2: bar timestamps arrive as UTC epoch seconds.
_EPOCH_DATES = 2
REAL_TIME_MARKET_DATA = 1
# IBKR's documented baseline allocation of simultaneous market-data lines.
# The API exposes no line-count query; quotes here hold one line at a time.
DEFAULT_MARKET_DATA_LINES = 100
DAILY_HISTORY_DURATION = "2 M"
# "No market data during competing live session": the owner's username is
# logged in elsewhere (phone/web) and that session owns the data lines.
COMPETING_SESSION_ERROR_CODE = 10197
# Historical-data services report the same condition as free text.
_COMPETING_SESSION_TEXT_CODES = frozenset({162, 165})
_COMPETING_SESSION_TEXT = "different ip address"
_TWS_ERROR_REPORT = re.compile(r"^TWS error (-?\d+): (.*)$", re.DOTALL)
# Streaming tick types collected by market_snapshot (IBKR TickType ids).
_TICK_BID = 1
_TICK_ASK = 2
_TICK_LAST = 4
_TICK_HIGH = 6
_TICK_LOW = 7
_TICK_VOLUME = 8
_TICK_CLOSE = 9
_TICK_OPEN = 14
_SNAPSHOT_PRICE_TICKS = frozenset(
    {_TICK_BID, _TICK_ASK, _TICK_LAST, _TICK_HIGH, _TICK_LOW, _TICK_CLOSE, _TICK_OPEN}
)
# Once price, prior close and volume are present, the rest of TWS's initial
# tick burst (open/high/low) normally lands within milliseconds; wait at most
# this long for it rather than publishing rows without session statistics.
MARKET_SNAPSHOT_SETTLE_SECONDS = 0.25
# A fresh subscription takes ~0.5-1.2 s to deliver its first ticks, but TWS
# serves subscriptions in parallel (live 2026-10-12: 16 concurrent streams
# finished in 2.1 s). Batches hold this many lines of the 100 allocated.
MAX_SNAPSHOT_LINES = 10
# Each stream is two messages (request + cancel); spacing starts 50 ms apart
# keeps a batch under TWS's 50 messages/second client limit.
SNAPSHOT_START_INTERVAL_SECONDS = 0.05
# Scans the live screener may merge; the frozen ORB scan() is unaffected.
SCREENER_SCAN_CODES = frozenset({"HOT_BY_VOLUME", "TOP_PERC_GAIN", "MOST_ACTIVE"})
SCREENER_SCAN_ROWS = 50


class TWSActorError(RuntimeError):
    """Base class for TWS actor failures."""


class TWSConfigurationError(TWSActorError, ValueError):
    """The actor is not configured for the loopback PAPER boundary."""


class TWSConnectionError(TWSActorError):
    """The TWS handshake or connection failed."""


class TWSRequestError(TWSActorError):
    """TWS rejected or failed a market-data request."""


class TWSCompetingSessionError(TWSRequestError):
    """Market data is blocked because the IBKR login is active elsewhere."""


def is_competing_session_error(code: int, message: str) -> bool:
    """Whether a TWS error means another login session holds the market data."""
    if code == COMPETING_SESSION_ERROR_CODE:
        return True
    return code in _COMPETING_SESSION_TEXT_CODES and _COMPETING_SESSION_TEXT in message.lower()


def is_competing_session_report(report: str | None) -> bool:
    """Recognize the actor's ``TWS error <code>: <message>`` text, e.g. in a bar batch."""
    if not report:
        return False
    match = _TWS_ERROR_REPORT.match(report.strip())
    if match is None:
        return False
    return is_competing_session_error(int(match.group(1)), match.group(2))


@dataclass(frozen=True, slots=True)
class DataLineCapacityReport:
    """Result of the caller-supplied market-data line capacity check."""

    required_lines: int
    available_lines: int | None
    passed: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.required_lines < 1:
            raise ValueError("required_lines must be positive")
        if self.available_lines is not None and self.available_lines < 0:
            raise ValueError("available_lines must be non-negative")
        if self.passed and (
            self.available_lines is None or self.available_lines < self.required_lines
        ):
            raise ValueError("a passed capacity check must cover required lines")


@dataclass(frozen=True, slots=True)
class TWSPreflightReport:
    """Non-secret account, transport, capability, and capacity readiness report."""

    host: str
    port: int
    account: str
    client_id: int
    connected: bool
    account_verified: bool
    scanner_supported: bool
    data_line_capacity: DataLineCapacityReport
    ready: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TWSActorConfig:
    """Explicit local PAPER identity; live hosts and ports are rejected."""

    account: str
    client_id: int
    host: str = PAPER_TWS_HOST
    port: int = PAPER_TWS_PORT
    handshake_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 20.0

    def validate(self) -> None:
        if self.host != PAPER_TWS_HOST:
            raise TWSConfigurationError(
                f"TWS actor requires loopback host {PAPER_TWS_HOST}"
            )
        if self.port != PAPER_TWS_PORT:
            raise TWSConfigurationError("TWS actor requires PAPER port 7497")
        if not isinstance(self.account, str) or not self.account.strip():
            raise TWSConfigurationError("a PAPER account is required")
        if self.client_id <= 0:
            raise TWSConfigurationError("a positive dedicated client_id is required")
        if self.handshake_timeout_seconds <= 0 or self.request_timeout_seconds <= 0:
            raise TWSConfigurationError("TWS timeouts must be positive")


class TWSClient(Protocol):
    def connect(self, host: str, port: int, client_id: int) -> Any: ...

    def disconnect(self) -> Any: ...

    def run(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class _ScannerSubscription:
    instrument: str
    locationCode: str
    scanCode: str
    stockTypeFilter: str
    numberOfRows: int


@dataclass(frozen=True, slots=True)
class _ContractRequest:
    conId: int
    secType: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"


@dataclass
class _PendingScan:
    future: asyncio.Future[tuple[IBKRScannerResult, ...]]
    results: list[IBKRScannerResult] = field(default_factory=list)


@dataclass
class _PendingBars:
    future: asyncio.Future[BarBatch]
    con_id: int
    bars: list[IBKRTradeBar] = field(default_factory=list)


@dataclass
class _PendingDailyBars:
    future: asyncio.Future[DailyBarBatch]
    con_id: int
    bars: list[IBKRDailyBar] = field(default_factory=list)


@dataclass
class _PendingAccount:
    future: asyncio.Future[AccountSnapshot]
    values: dict[tuple[str, str], str] = field(default_factory=dict)
    positions: list[RawAccountPosition] = field(default_factory=list)
    error: str | None = None


@dataclass
class _PendingDetails:
    future: asyncio.Future[IBKRContractDetails]
    con_id: int
    details: list[Any] = field(default_factory=list)


@dataclass
class _PendingQuote:
    future: asyncio.Future[QuoteSnapshot]
    con_id: int
    min_tick: Decimal
    request_id: int
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_at: datetime | None = None
    ask_at: datetime | None = None
    data_type: int | None = None


@dataclass
class _PendingMarketSnapshot:
    # ``ready``: real-time confirmed with a price, prior close and volume.
    # ``complete``: every collected tick is present; no settle wait needed.
    ready: asyncio.Future[None]
    complete: asyncio.Future[None]
    con_id: int
    data_type: int | None = None
    prices: dict[int, Decimal] = field(default_factory=dict)
    volume_raw: Decimal | None = None


class _CallbackSink:
    """Translate official camelCase callbacks and test-double callbacks."""

    def __init__(self, actor: "TWSPaperActor") -> None:
        self.actor = actor

    def nextValidId(self, order_id: int) -> None:
        self.actor._on_next_valid_id(order_id)

    def managedAccounts(self, accounts: str) -> None:
        self.actor._on_managed_accounts(accounts)

    def scannerData(self, request_id: int, rank: int, details: Any, *args: Any) -> None:
        self.actor._on_scanner_data(request_id, rank, details)

    def scannerDataEnd(self, request_id: int) -> None:
        self.actor._on_scanner_end(request_id)

    def contractDetails(self, request_id: int, details: Any) -> None:
        self.actor._on_contract_details(request_id, details)

    def contractDetailsEnd(self, request_id: int) -> None:
        self.actor._on_contract_details_end(request_id)

    def updateAccountValue(self, key: str, value: str, currency: str, account: str) -> None:
        self.actor._on_account_value(key, value, currency, account)

    def updatePortfolio(self, contract: Any, position: Any, *args: Any) -> None:
        self.actor._on_portfolio(contract, position, *args)

    def accountDownloadEnd(self, account: str) -> None:
        self.actor._on_account_download_end(account)

    def updateAccountTime(self, *args: Any) -> None:
        return None

    def historicalData(self, request_id: int, bar: Any) -> None:
        self.actor._on_historical_data(request_id, bar)

    def historicalDataEnd(self, request_id: int, start: str = "", end: str = "") -> None:
        self.actor._on_historical_end(request_id)

    def historicalDataUpdate(self, request_id: int, bar: Any) -> None:
        self.actor._on_historical_update(request_id, bar)

    def tickPrice(self, request_id: int, tick_type: int, price: float, *args: Any) -> None:
        self.actor._on_tick_price(request_id, tick_type, price)

    def tickSize(self, request_id: int, tick_type: int, size: Any, *args: Any) -> None:
        self.actor._on_tick_size(request_id, tick_type, size)

    def marketDataType(self, request_id: int, data_type: int) -> None:
        self.actor._on_market_data_type(request_id, data_type)

    def tickSnapshotEnd(self, request_id: int) -> None:
        self.actor._on_tick_snapshot_end(request_id)

    def error(self, request_id: int, code: int, message: str, *args: Any) -> None:
        self.actor._on_error(request_id, code, message)

    def connectionClosed(self) -> None:
        self.actor._on_connection_closed()

    next_valid_id = nextValidId
    managed_accounts = managedAccounts
    scanner_data = scannerData
    scanner_data_end = scannerDataEnd
    contract_details = contractDetails
    update_account_value = updateAccountValue
    update_portfolio = updatePortfolio
    account_download_end = accountDownloadEnd
    contract_details_end = contractDetailsEnd
    historical_data = historicalData
    historical_data_end = historicalDataEnd
    historical_data_update = historicalDataUpdate
    tick_price = tickPrice
    tick_size = tickSize
    market_data_type = marketDataType
    tick_snapshot_end = tickSnapshotEnd


def _official_client_factory(sink: _CallbackSink) -> TWSClient:
    """Import and instantiate official ibapi only after explicit connect."""
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    class Client(EWrapper, EClient):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self._sink = sink

        def nextValidId(self, value: int) -> None:
            self._sink.nextValidId(value)

        def managedAccounts(self, value: str) -> None:
            self._sink.managedAccounts(value)

        def scannerData(self, *args: Any) -> None:
            self._sink.scannerData(*args)

        def scannerDataEnd(self, value: int) -> None:
            self._sink.scannerDataEnd(value)

        def contractDetails(self, *args: Any) -> None:
            self._sink.contractDetails(*args)

        def updateAccountValue(self, *args: Any) -> None:
            self._sink.updateAccountValue(*args)

        def updatePortfolio(self, *args: Any) -> None:
            self._sink.updatePortfolio(*args)

        def accountDownloadEnd(self, *args: Any) -> None:
            self._sink.accountDownloadEnd(*args)

        def updateAccountTime(self, *args: Any) -> None:
            self._sink.updateAccountTime(*args)

        def contractDetailsEnd(self, *args: Any) -> None:
            self._sink.contractDetailsEnd(*args)

        def historicalData(self, *args: Any) -> None:
            self._sink.historicalData(*args)

        def historicalDataEnd(self, *args: Any) -> None:
            self._sink.historicalDataEnd(*args)

        def historicalDataUpdate(self, *args: Any) -> None:
            self._sink.historicalDataUpdate(*args)

        def tickPrice(self, *args: Any) -> None:
            self._sink.tickPrice(*args)

        def tickSize(self, *args: Any) -> None:
            self._sink.tickSize(*args)

        def marketDataType(self, *args: Any) -> None:
            self._sink.marketDataType(*args)

        def tickSnapshotEnd(self, *args: Any) -> None:
            self._sink.tickSnapshotEnd(*args)

        def error(self, *args: Any) -> None:
            self._sink.error(*args)

        def connectionClosed(self) -> None:
            self._sink.connectionClosed()

    return Client()


def _official_scanner_factory(request: ScannerRequest) -> Any:
    from ibapi.scanner import ScannerSubscription

    subscription = ScannerSubscription()
    subscription.instrument = request.instrument
    subscription.locationCode = request.location
    subscription.scanCode = request.scan_code
    # IBKR's scanner code for ordinary company shares is "CORP"; "COMMON" is
    # silently ignored (2026-10-12 live scan returned 45/50 ETFs with it).
    subscription.stockTypeFilter = {"COMMON": "CORP"}[request.stock_type_filter]
    subscription.numberOfRows = request.number_of_rows
    subscription.abovePrice = float(request.above_price)
    subscription.belowPrice = float(request.below_price)
    return subscription


def _official_screener_scan_factory(scan_code: str, number_of_rows: int) -> Any:
    from ibapi.scanner import ScannerSubscription

    subscription = ScannerSubscription()
    subscription.instrument = "STK"
    subscription.locationCode = "STK.US.MAJOR"
    subscription.scanCode = scan_code
    # "CORP" is IBKR's code for ordinary company shares (see the ORB factory).
    subscription.stockTypeFilter = "CORP"
    subscription.numberOfRows = number_of_rows
    subscription.abovePrice = 10.0
    subscription.belowPrice = 300.0
    return subscription


def _official_contract_factory(con_id: int) -> Any:
    from ibapi.contract import Contract

    contract = Contract()
    contract.conId = con_id
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract


def _official_symbol_contract_factory(symbol: str, primary_exchange: str | None) -> Any:
    from ibapi.contract import Contract

    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    if primary_exchange:
        contract.primaryExchange = primary_exchange
    return contract


def documented_line_allocation(required_lines: int) -> DataLineCapacityReport:
    """Capacity from IBKR's documented baseline; the API offers no line-count query.

    Quotes hold a single streaming line at a time and release it immediately,
    so the baseline covers any candidate count up to the allocation.
    """
    available = DEFAULT_MARKET_DATA_LINES
    passed = required_lines <= available
    return DataLineCapacityReport(
        required_lines=required_lines,
        available_lines=available,
        passed=passed,
        reason=None if passed else "required lines exceed the documented allocation",
    )


class TWSPaperActor:
    """One callback-owning, request-ID-owning local TWS PAPER actor."""

    def __init__(
        self,
        config: TWSActorConfig,
        *,
        client_factory: Callable[[_CallbackSink], TWSClient] | None = None,
        scanner_factory: Callable[[ScannerRequest], Any] | None = None,
        contract_factory: Callable[[int], Any] | None = None,
        capacity_checker: Callable[[int], Any] | None = None,
        symbol_contract_factory: Callable[[str, str | None], Any] | None = None,
        screener_scan_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._client_factory = client_factory or _official_client_factory
        self._scanner_factory = scanner_factory or _official_scanner_factory
        self._contract_factory = contract_factory or _official_contract_factory
        self._symbol_contract_factory = (
            symbol_contract_factory or _official_symbol_contract_factory
        )
        self._screener_scan_factory = screener_scan_factory or _official_screener_scan_factory
        self._capacity_checker = capacity_checker
        self._client: TWSClient | None = None
        self._callback_sink = _CallbackSink(self)
        self._run_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._handshake_event: asyncio.Event | None = None
        self._accounts_event: asyncio.Event | None = None
        self._managed_accounts: frozenset[str] = frozenset()
        self._next_request_id: int | None = None
        self._request_lock = asyncio.Lock()
        self._request_gate = asyncio.Lock()
        self._pending_scans: dict[int, _PendingScan] = {}
        self._pending_bars: dict[int, _PendingBars] = {}
        self._pending_daily: dict[int, _PendingDailyBars] = {}
        self._pending_details: dict[int, _PendingDetails] = {}
        self._pending_quotes: dict[int, _PendingQuote] = {}
        self._pending_snapshots: dict[int, _PendingMarketSnapshot] = {}
        self._pending_account: _PendingAccount | None = None
        self._last_error: str | None = None
        self._callbacks_seen = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str | None:
        """Most recent non-informational TWS error text, for operator diagnostics."""
        return self._last_error

    @property
    def managed_accounts(self) -> frozenset[str]:
        return self._managed_accounts

    @property
    def callbacks_seen(self) -> int:
        return self._callbacks_seen

    async def connect(self) -> None:
        """Connect only when explicitly called and wait for nextValidId."""
        self.config.validate()
        if self._connected:
            return
        self._loop = asyncio.get_running_loop()
        self._handshake_event = asyncio.Event()
        self._accounts_event = asyncio.Event()
        try:
            self._client = self._client_factory(self._callback_sink)
            self._client.connect(self.config.host, self.config.port, self.config.client_id)
            run = getattr(self._client, "run", None)
            if callable(run):
                self._run_thread = threading.Thread(target=run, daemon=True)
                self._run_thread.start()
            await asyncio.wait_for(
                self._handshake_event.wait(), timeout=self.config.handshake_timeout_seconds
            )
            self._connected = True
            request_accounts = getattr(self._client, "reqManagedAccts", None)
            if not callable(request_accounts):
                raise TWSConnectionError("TWS client cannot verify the PAPER account")
            request_accounts()
            await asyncio.wait_for(
                self._accounts_event.wait(), timeout=self.config.handshake_timeout_seconds
            )
            if self.config.account not in self._managed_accounts:
                raise TWSConnectionError("configured PAPER account was not returned by TWS")
            # Ask TWS for live data explicitly; anything else is rejected per quote.
            request_type = getattr(self._client, "reqMarketDataType", None)
            if not callable(request_type):
                raise TWSConnectionError("TWS client cannot request real-time market data")
            request_type(REAL_TIME_MARKET_DATA)
        except Exception as exc:
            await self.disconnect()
            if isinstance(exc, TWSActorError):
                raise
            raise TWSConnectionError("TWS PAPER handshake failed") from exc

    async def disconnect(self) -> None:
        """Disconnect and fail pending requests; never reconnect implicitly."""
        self._connected = False
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        error = TWSConnectionError("TWS PAPER actor disconnected")
        for pending in (
            *self._pending_scans.values(),
            *self._pending_bars.values(),
            *self._pending_daily.values(),
            *self._pending_details.values(),
            *self._pending_quotes.values(),
        ):
            self._set_future_exception(pending.future, error)
        self._pending_scans.clear()
        self._pending_bars.clear()
        self._pending_daily.clear()
        self._pending_details.clear()
        self._pending_quotes.clear()
        for snapshot in tuple(self._pending_snapshots.values()):
            self._fail_snapshot(snapshot, error)
        self._pending_snapshots.clear()
        if self._pending_account is not None:
            self._set_future_exception(self._pending_account.future, error)
            self._pending_account = None
        self._run_thread = None

    def _require_connected(self) -> None:
        if not self._connected or self._client is None:
            raise TWSConnectionError("connect() must complete before a TWS request")

    async def _allocate_request_id(self) -> int:
        async with self._request_lock:
            self._require_connected()
            if self._next_request_id is None:
                raise TWSConnectionError("TWS has not supplied a request ID")
            request_id = self._next_request_id
            self._next_request_id += 1
            return request_id

    @staticmethod
    def _validate_scanner_request(request: ScannerRequest) -> None:
        if request != ScannerRequest():
            raise TWSRequestError("only the frozen HOT_BY_VOLUME scanner is supported")

    async def supports_scanner(self, request: ScannerRequest) -> bool:
        self._validate_scanner_request(request)
        self._require_connected()
        return callable(getattr(self._client, "reqScannerSubscription", None))

    async def scan(self, request: ScannerRequest) -> Sequence[IBKRScannerResult]:
        self._validate_scanner_request(request)
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingScan(asyncio.get_running_loop().create_future())
            self._pending_scans[request_id] = pending
            try:
                subscription = self._scanner_factory(request)
                method = getattr(self._client, "reqScannerSubscription")
                try:
                    method(request_id, subscription, [], [])
                except TypeError:
                    method(request_id, subscription)
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                self._cancel_scanner(request_id)
                raise TWSRequestError("TWS scanner request timed out") from exc
            finally:
                self._pending_scans.pop(request_id, None)

    async def screener_scan(self, scan_code: str) -> tuple[IBKRScannerResult, ...]:
        """One allowlisted $10-$300 US common-stock scan for the live screener.

        Kept apart from ``scan()``, whose single frozen ORB request must not
        change; rows are returned unfiltered for the caller to vet.
        """
        if scan_code not in SCREENER_SCAN_CODES:
            raise TWSRequestError(f"scan code {scan_code!r} is not allowlisted for the screener")
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingScan(asyncio.get_running_loop().create_future())
            self._pending_scans[request_id] = pending
            try:
                subscription = self._screener_scan_factory(scan_code, SCREENER_SCAN_ROWS)
                method = getattr(self._client, "reqScannerSubscription")
                try:
                    method(request_id, subscription, [], [])
                except TypeError:
                    method(request_id, subscription)
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                self._cancel_scanner(request_id)
                raise TWSRequestError("TWS screener scan timed out") from exc
            finally:
                self._pending_scans.pop(request_id, None)

    async def contract_details(self, con_id: int) -> IBKRContractDetails:
        """Resolve one conId to its qualified details; ambiguity is an error."""
        if con_id <= 0:
            raise TWSRequestError("con_id must be positive")
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingDetails(asyncio.get_running_loop().create_future(), con_id)
            self._pending_details[request_id] = pending
            try:
                method = getattr(self._client, "reqContractDetails")
                method(request_id, self._contract_factory(con_id))
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                raise TWSRequestError("TWS contract-details request timed out") from exc
            finally:
                self._pending_details.pop(request_id, None)

    async def contract_details_for_symbol(
        self, symbol: str, primary_exchange: str | None
    ) -> IBKRContractDetails:
        """Resolve a US stock symbol (SMART/USD) to exactly one contract.

        ``primary_exchange`` narrows the listing; zero or several matches is
        an error because a guessed contract would trade the wrong company.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise TWSRequestError("symbol must be non-empty")
        hint = primary_exchange.strip() if primary_exchange else None
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingDetails(asyncio.get_running_loop().create_future(), 0)
            self._pending_details[request_id] = pending
            try:
                method = getattr(self._client, "reqContractDetails")
                method(request_id, self._symbol_contract_factory(symbol.strip(), hint or None))
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                raise TWSRequestError("TWS contract-details request timed out") from exc
            finally:
                self._pending_details.pop(request_id, None)

    async def completed_trade_bars(self, con_id: int) -> BarBatch:
        """Resolve only after historicalDataEnd, never from an update callback."""
        return await self._intraday_trade_bars(
            con_id,
            duration=INTRADAY_HISTORY_DURATION,
            use_rth=_RTH_ONLY,
            timeout_message="TWS historical-data request timed out",
        )

    async def extended_trade_bars(self, con_id: int) -> BarBatch:
        """Today's five-minute TRADES bars including pre-market and after-hours.

        Same completion rule as ``completed_trade_bars``; each bar's ``rth``
        flag comes from the XNYS calendar, so callers can tell pre-market bars
        from regular-session ones.
        """
        return await self._intraday_trade_bars(
            con_id,
            duration=EXTENDED_HISTORY_DURATION,
            use_rth=_ALL_HOURS,
            timeout_message="TWS extended-hours history request timed out",
        )

    async def _intraday_trade_bars(
        self, con_id: int, *, duration: str, use_rth: int, timeout_message: str
    ) -> BarBatch:
        if con_id <= 0:
            raise TWSRequestError("con_id must be positive")
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingBars(asyncio.get_running_loop().create_future(), con_id)
            self._pending_bars[request_id] = pending
            try:
                contract = self._contract_factory(con_id)
                method = getattr(self._client, "reqHistoricalData")
                method(
                    request_id,
                    contract,
                    "",
                    duration,
                    INTRADAY_BAR_SIZE,
                    "TRADES",
                    use_rth,
                    _EPOCH_DATES,
                    False,
                    [],
                )
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                self._cancel_historical(request_id)
                raise TWSRequestError(timeout_message) from exc
            finally:
                self._pending_bars.pop(request_id, None)

    async def daily_trade_bars(self, con_id: int) -> DailyBarBatch:
        """Regular-session daily TRADES bars; today's forming bar is filtered downstream."""
        if con_id <= 0:
            raise TWSRequestError("con_id must be positive")
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingDailyBars(asyncio.get_running_loop().create_future(), con_id)
            self._pending_daily[request_id] = pending
            try:
                method = getattr(self._client, "reqHistoricalData")
                method(
                    request_id,
                    self._contract_factory(con_id),
                    "",
                    DAILY_HISTORY_DURATION,
                    "1 day",
                    "TRADES",
                    1,
                    1,
                    False,
                    [],
                )
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                self._cancel_historical(request_id)
                raise TWSRequestError("TWS daily-history request timed out") from exc
            finally:
                self._pending_daily.pop(request_id, None)

    async def account_snapshot(self) -> AccountSnapshot:
        """One full account download: net liquidation, day P&L and positions.

        Only US common-stock positions are representable; anything else in the
        PAPER account blocks rather than being silently ignored by risk.
        """
        self._require_connected()
        async with self._request_gate:
            pending = _PendingAccount(asyncio.get_running_loop().create_future())
            self._pending_account = pending
            method = getattr(self._client, "reqAccountUpdates")
            try:
                method(True, self.config.account)
                return await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                raise TWSRequestError("TWS account download timed out") from exc
            finally:
                self._pending_account = None
                if self._client is not None:
                    method(False, self.config.account)

    async def quote_snapshot(
        self,
        con_id: int,
        *,
        min_tick: Decimal,
        max_age_seconds: Decimal = Decimal("2"),
    ) -> QuoteSnapshot:
        """Return a fresh bid/ask from a confirmed real-time stream.

        A streaming request resolves as soon as both sides and the real-time
        data type are present; snapshot requests can take ~11s to complete,
        which would make every quote stale before it is used.
        """
        if con_id <= 0:
            raise TWSRequestError("con_id must be positive")
        try:
            min_tick = Decimal(str(min_tick))
        except Exception as exc:
            raise TWSRequestError("min_tick must be a decimal value") from exc
        if not min_tick.is_finite() or min_tick <= 0:
            raise TWSRequestError("min_tick must be finite and positive")
        self._require_connected()
        async with self._request_gate:
            request_id = await self._allocate_request_id()
            pending = _PendingQuote(
                asyncio.get_running_loop().create_future(), con_id, min_tick, request_id
            )
            self._pending_quotes[request_id] = pending
            try:
                method = getattr(self._client, "reqMktData")
                method(
                    request_id,
                    self._contract_factory(con_id),
                    "",
                    False,
                    False,
                    [],
                )
                quote = await asyncio.wait_for(
                    pending.future, timeout=self.config.request_timeout_seconds
                )
                if not quote.is_fresh(
                    now=datetime.now(timezone.utc), max_age_seconds=max_age_seconds
                ):
                    raise TWSRequestError("TWS quote snapshot is stale")
                return quote
            except asyncio.TimeoutError as exc:
                raise TWSRequestError("TWS quote snapshot timed out") from exc
            finally:
                # The line is released on every path, including success.
                self._cancel_market_data(request_id)
                self._pending_quotes.pop(request_id, None)

    async def market_snapshot(
        self, con_id: int, *, timeout_seconds: float = 3.0
    ) -> MarketSnapshot:
        """Session statistics from a short-lived real-time stream (snapshot=False).

        Resolves once real-time data is confirmed and a price (last, or bid
        and ask), the prior close and volume are present, then waits at most
        ``MARKET_SNAPSHOT_SETTLE_SECONDS`` for the remaining open/high/low
        ticks. On timeout a confirmed price and prior close are still
        returned; anything less is an error. The line is always released.
        """
        _validate_snapshot_request((con_id,), timeout_seconds)
        self._require_connected()
        async with self._request_gate:
            return await self._stream_snapshot(con_id, timeout_seconds)

    async def market_snapshots(
        self,
        con_ids: Sequence[int],
        *,
        timeout_seconds: float = 3.0,
        max_in_flight: int = MAX_SNAPSHOT_LINES,
    ) -> tuple[MarketSnapshot | TWSRequestError, ...]:
        """``market_snapshot`` for many contracts over a bounded set of lines.

        The whole batch is one gated request, so nothing else interleaves
        with it. Results keep the input order; a per-contract failure is
        returned in place, while a competing login or a lost connection
        releases every open line and raises.
        """
        _validate_snapshot_request(con_ids, timeout_seconds)
        if not 1 <= max_in_flight <= DEFAULT_MARKET_DATA_LINES:
            raise TWSRequestError("max_in_flight must be between 1 and the line allocation")
        self._require_connected()
        async with self._request_gate:
            loop = asyncio.get_running_loop()
            lines = asyncio.Semaphore(max_in_flight)
            start_lock = asyncio.Lock()
            next_start = loop.time()

            async def one(con_id: int) -> MarketSnapshot | TWSRequestError:
                nonlocal next_start
                async with lines:
                    async with start_lock:
                        delay = next_start - loop.time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                        next_start = loop.time() + SNAPSHOT_START_INTERVAL_SECONDS
                    try:
                        return await self._stream_snapshot(con_id, timeout_seconds)
                    except (TWSCompetingSessionError, TWSConnectionError):
                        raise
                    except TWSRequestError as exc:
                        return exc

            tasks = [asyncio.create_task(one(con_id)) for con_id in con_ids]
            try:
                return tuple(await asyncio.gather(*tasks))
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    async def _stream_snapshot(self, con_id: int, timeout_seconds: float) -> MarketSnapshot:
        """One subscription; callers hold the request gate."""
        request_id = await self._allocate_request_id()
        loop = asyncio.get_running_loop()
        pending = _PendingMarketSnapshot(loop.create_future(), loop.create_future(), con_id)
        self._pending_snapshots[request_id] = pending
        started = loop.time()
        try:
            method = getattr(self._client, "reqMktData")
            method(request_id, self._contract_factory(con_id), "", False, False, [])
            try:
                await asyncio.wait_for(pending.ready, timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                missing = _snapshot_missing(pending)
                if missing:
                    raise TWSRequestError(
                        "TWS market snapshot timed out without " + ", ".join(missing)
                    ) from exc
                return self._build_market_snapshot(pending)
            settle = min(
                MARKET_SNAPSHOT_SETTLE_SECONDS, timeout_seconds - (loop.time() - started)
            )
            if not pending.complete.done() and settle > 0:
                try:
                    await asyncio.wait_for(pending.complete, timeout=settle)
                except asyncio.TimeoutError:
                    pass
            elif pending.complete.done():
                # Surfaces an error that arrived after the minimum data.
                pending.complete.result()
            return self._build_market_snapshot(pending)
        finally:
            # The line is released on every path, including success.
            self._cancel_market_data(request_id)
            self._pending_snapshots.pop(request_id, None)
            for future in (pending.ready, pending.complete):
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()

    async def check_data_line_capacity(self, required_lines: int) -> DataLineCapacityReport:
        """Call the injected entitlement/capacity probe without assuming capacity."""
        if required_lines < 1:
            raise TWSRequestError("required market-data lines must be positive")
        if self._capacity_checker is None:
            return DataLineCapacityReport(
                required_lines=required_lines,
                available_lines=None,
                passed=False,
                reason="market-data line capacity checker is not configured",
            )
        try:
            outcome = self._capacity_checker(required_lines)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        except Exception as exc:
            return DataLineCapacityReport(
                required_lines=required_lines,
                available_lines=None,
                passed=False,
                reason=f"capacity check failed: {type(exc).__name__}",
            )
        if isinstance(outcome, DataLineCapacityReport):
            if outcome.required_lines != required_lines:
                return DataLineCapacityReport(
                    required_lines=required_lines,
                    available_lines=outcome.available_lines,
                    passed=False,
                    reason="capacity checker returned a mismatched requirement",
                )
            return outcome
        if isinstance(outcome, bool):
            return DataLineCapacityReport(
                required_lines=required_lines,
                available_lines=required_lines if outcome else 0,
                passed=outcome,
                reason=None if outcome else "capacity checker denied required lines",
            )
        if isinstance(outcome, int):
            available = max(0, outcome)
            return DataLineCapacityReport(
                required_lines=required_lines,
                available_lines=available,
                passed=available >= required_lines,
                reason=None
                if available >= required_lines
                else "available market-data lines are below requirement",
            )
        return DataLineCapacityReport(
            required_lines=required_lines,
            available_lines=None,
            passed=False,
            reason="capacity checker returned an unsupported result",
        )

    async def preflight(self, *, required_data_lines: int = 1) -> TWSPreflightReport:
        """Return a non-secret readiness report without opening a connection."""
        capacity = await self.check_data_line_capacity(required_data_lines)
        connected = self.connected
        account_verified = connected and self.config.account in self._managed_accounts
        scanner_supported = False
        reasons: list[str] = []
        if connected:
            try:
                scanner_supported = await self.supports_scanner(ScannerRequest())
            except TWSActorError as exc:
                reasons.append(str(exc))
        else:
            reasons.append("TWS actor is disconnected")
        if not account_verified:
            reasons.append("configured PAPER account is not verified")
        if not scanner_supported:
            reasons.append("frozen scanner capability is unavailable")
        if not capacity.passed:
            reasons.append(capacity.reason or "market-data line capacity is unavailable")
        return TWSPreflightReport(
            host=self.config.host,
            port=self.config.port,
            account=self.config.account,
            client_id=self.config.client_id,
            connected=connected,
            account_verified=account_verified,
            scanner_supported=scanner_supported,
            data_line_capacity=capacity,
            ready=not reasons,
            reasons=tuple(reasons),
        )

    def _on_next_valid_id(self, value: int) -> None:
        self._callbacks_seen += 1
        if value < 0:
            self._last_error = "TWS returned an invalid request ID"
            return
        self._next_request_id = value
        self._signal_event(self._handshake_event)

    def _on_managed_accounts(self, accounts: str) -> None:
        self._callbacks_seen += 1
        self._managed_accounts = frozenset(
            item.strip() for item in accounts.split(",") if item.strip()
        )
        self._signal_event(self._accounts_event)

    def _on_scanner_data(self, request_id: int, rank: int, details: Any) -> None:
        self._callbacks_seen += 1
        pending = self._pending_scans.get(request_id)
        if pending is None:
            return
        result = self._scanner_result(rank, details)
        if isinstance(result, Exception):
            self._set_future_exception(pending.future, result)
        else:
            pending.results.append(result)

    def _on_account_value(self, key: str, value: str, currency: str, account: str) -> None:
        self._callbacks_seen += 1
        pending = self._pending_account
        if pending is None or account != self.config.account:
            return
        pending.values[(str(key), str(currency))] = str(value)

    def _on_portfolio(self, contract: Any, position: Any, *args: Any) -> None:
        self._callbacks_seen += 1
        pending = self._pending_account
        if pending is None or pending.future.done():
            return
        # Official order: marketPrice, marketValue, averageCost, unrealizedPNL,
        # realizedPNL, accountName.
        if len(args) < 6 or str(args[5]) != self.config.account:
            return
        try:
            quantity = Decimal(str(position))
            if quantity == 0:
                return
            if (
                str(getattr(contract, "secType", "")) != "STK"
                or str(getattr(contract, "currency", "")) != "USD"
            ):
                pending.error = "PAPER account holds a position outside US stocks"
                return
            pending.positions.append(
                RawAccountPosition(
                    paper_account_id=self.config.account,
                    con_id=int(getattr(contract, "conId")),
                    symbol=str(getattr(contract, "symbol")),
                    quantity=quantity,
                    market_price=Decimal(str(args[0])),
                    market_value=Decimal(str(args[1])),
                    average_cost=Decimal(str(args[2])),
                    unrealized_pnl=Decimal(str(args[3])),
                    realized_pnl=Decimal(str(args[4])),
                    observed_at=datetime.now(timezone.utc),
                )
            )
        except Exception:
            pending.error = "TWS portfolio callback was malformed"

    def _on_account_download_end(self, account: str) -> None:
        self._callbacks_seen += 1
        pending = self._pending_account
        if pending is None or pending.future.done() or account != self.config.account:
            return
        if pending.error is not None:
            self._set_future_exception(pending.future, TWSRequestError(pending.error))
            return

        def amount(key: str) -> Decimal:
            for currency in ("BASE", "USD"):
                raw = pending.values.get((key, currency))
                if raw is not None:
                    return Decimal(raw)
            raise TWSRequestError(f"TWS account download is missing {key}")

        try:
            snapshot = AccountSnapshot(
                paper_account_id=self.config.account,
                client_id=self.config.client_id,
                observed_at=datetime.now(timezone.utc),
                net_liquidation=amount("NetLiquidation"),
                available_funds=amount("AvailableFunds"),
                realized_day_pnl=amount("RealizedPnL"),
                unrealized_pnl=amount("UnrealizedPnL"),
                positions=tuple(pending.positions),
            )
        except TWSRequestError as exc:
            self._set_future_exception(pending.future, exc)
            return
        except Exception:
            self._set_future_exception(
                pending.future, TWSRequestError("TWS account download failed validation")
            )
            return
        self._set_future_result(pending.future, snapshot)

    def _on_contract_details(self, request_id: int, details: Any) -> None:
        self._callbacks_seen += 1
        pending = self._pending_details.get(request_id)
        if pending is not None and not pending.future.done():
            pending.details.append(details)

    def _on_contract_details_end(self, request_id: int) -> None:
        self._callbacks_seen += 1
        pending = self._pending_details.get(request_id)
        if pending is None or pending.future.done():
            return
        if len(pending.details) != 1:
            self._set_future_exception(
                pending.future,
                TWSRequestError("TWS contract details were missing or ambiguous"),
            )
            return
        raw = pending.details[0]
        contract = getattr(raw, "contract", None)
        try:
            details = IBKRContractDetails(
                con_id=int(getattr(contract, "conId")),
                symbol=str(getattr(contract, "symbol", "") or ""),
                currency=str(getattr(contract, "currency", "") or ""),
                security_type=str(getattr(contract, "secType", "") or ""),
                stock_type=str(getattr(raw, "stockType", "") or ""),
                primary_exchange=str(getattr(contract, "primaryExchange", "") or ""),
                min_tick=Decimal(str(getattr(raw, "minTick"))),
            )
        except Exception:
            self._set_future_exception(
                pending.future, TWSRequestError("TWS contract details were malformed")
            )
            return
        self._set_future_result(pending.future, details)

    def _on_scanner_end(self, request_id: int) -> None:
        self._callbacks_seen += 1
        pending = self._pending_scans.get(request_id)
        if pending is not None and not pending.future.done():
            self._set_future_result(
                pending.future,
                tuple(sorted(pending.results, key=lambda item: item.rank)),
            )
        self._cancel_scanner(request_id)

    def _on_historical_data(self, request_id: int, bar: Any) -> None:
        self._callbacks_seen += 1
        daily = self._pending_daily.get(request_id)
        if daily is not None:
            if daily.future.done():
                return
            try:
                daily.bars.append(self._daily_bar(bar, con_id=daily.con_id))
            except Exception as exc:
                self._last_error = str(exc)
                self._set_future_exception(
                    daily.future, TWSRequestError("invalid TWS daily bar callback")
                )
            return
        pending = self._pending_bars.get(request_id)
        if pending is None or pending.future.done():
            return
        try:
            pending.bars.append(self._trade_bar(bar, con_id=pending.con_id))
        except Exception as exc:
            self._last_error = str(exc)
            self._set_future_exception(pending.future, TWSRequestError("invalid TWS bar callback"))

    def _on_historical_update(self, request_id: int, bar: Any) -> None:
        # keepUpToDate is false; unfinished updates are never admitted.
        self._callbacks_seen += 1

    def _on_historical_end(self, request_id: int) -> None:
        self._callbacks_seen += 1
        daily = self._pending_daily.get(request_id)
        if daily is not None and not daily.future.done():
            self._set_future_result(
                daily.future,
                DailyBarBatch(bars=tuple(daily.bars), connected=self._connected),
            )
            return
        pending = self._pending_bars.get(request_id)
        if pending is not None and not pending.future.done():
            self._set_future_result(
                pending.future,
                BarBatch(bars=tuple(pending.bars), connected=self._connected),
            )

    def _on_tick_price(self, request_id: int, tick_type: int, price: float) -> None:
        self._callbacks_seen += 1
        snapshot = self._pending_snapshots.get(request_id)
        if snapshot is not None:
            self._on_snapshot_price(snapshot, tick_type, price)
            return
        pending = self._pending_quotes.get(request_id)
        if pending is None:
            return
        try:
            value = Decimal(str(price))
        except Exception:
            return
        if not value.is_finite() or value <= 0:
            return
        received = datetime.now(timezone.utc)
        if tick_type == 1:
            pending.bid, pending.bid_at = value, received
        elif tick_type == 2:
            pending.ask, pending.ask_at = value, received
        else:
            return
        self._maybe_resolve_quote(pending)

    def _on_market_data_type(self, request_id: int, data_type: int) -> None:
        self._callbacks_seen += 1
        snapshot = self._pending_snapshots.get(request_id)
        if snapshot is not None:
            snapshot.data_type = data_type
            if data_type != REAL_TIME_MARKET_DATA:
                self._fail_snapshot(
                    snapshot,
                    TWSRequestError(
                        "real-time market data is required; delayed/frozen data rejected"
                    ),
                )
                return
            self._update_snapshot(snapshot)
            return
        pending = self._pending_quotes.get(request_id)
        if pending is None or pending.future.done():
            return
        pending.data_type = data_type
        if data_type != REAL_TIME_MARKET_DATA:
            self._set_future_exception(
                pending.future,
                TWSRequestError("real-time market data is required; delayed/frozen data rejected"),
            )
            return
        self._maybe_resolve_quote(pending)

    def _on_tick_size(self, request_id: int, tick_type: int, size: Any) -> None:
        self._callbacks_seen += 1
        snapshot = self._pending_snapshots.get(request_id)
        if snapshot is None or tick_type != _TICK_VOLUME:
            return
        try:
            value = Decimal(str(size))
        except Exception:
            return
        # IBKR sends -1 for "unknown"; that is absence, not zero volume.
        if not value.is_finite() or value < 0:
            return
        snapshot.volume_raw = value
        self._update_snapshot(snapshot)

    def _on_snapshot_price(
        self, snapshot: _PendingMarketSnapshot, tick_type: int, price: float
    ) -> None:
        if tick_type not in _SNAPSHOT_PRICE_TICKS:
            return
        try:
            value = Decimal(str(price))
        except Exception:
            return
        # IBKR sends -1/0 when a side or statistic is unavailable.
        if not value.is_finite() or value <= 0:
            return
        snapshot.prices[tick_type] = value
        self._update_snapshot(snapshot)

    def _update_snapshot(self, snapshot: _PendingMarketSnapshot) -> None:
        if snapshot.data_type != REAL_TIME_MARKET_DATA or _snapshot_missing(snapshot):
            return
        if snapshot.volume_raw is None:
            return
        self._set_future_result(snapshot.ready, None)
        if _SNAPSHOT_PRICE_TICKS.issubset(snapshot.prices):
            self._set_future_result(snapshot.complete, None)

    def _fail_snapshot(self, snapshot: _PendingMarketSnapshot, error: Exception) -> None:
        self._set_future_exception(snapshot.ready, error)
        self._set_future_exception(snapshot.complete, error)

    def _build_market_snapshot(self, snapshot: _PendingMarketSnapshot) -> MarketSnapshot:
        prices = dict(snapshot.prices)
        try:
            return MarketSnapshot(
                con_id=snapshot.con_id,
                observed_at=datetime.now(timezone.utc),
                prior_close=prices[_TICK_CLOSE],
                last=prices.get(_TICK_LAST),
                bid=prices.get(_TICK_BID),
                ask=prices.get(_TICK_ASK),
                open=prices.get(_TICK_OPEN),
                day_high=prices.get(_TICK_HIGH),
                day_low=prices.get(_TICK_LOW),
                volume_raw=snapshot.volume_raw,
            )
        except (KeyError, MarketSnapshotError) as exc:
            self._last_error = str(exc)
            raise TWSRequestError("TWS market snapshot failed validation") from exc

    def _maybe_resolve_quote(self, pending: _PendingQuote) -> None:
        if (
            pending.future.done()
            or pending.data_type != REAL_TIME_MARKET_DATA
            or pending.bid is None
            or pending.ask is None
        ):
            return
        self._resolve_quote(pending)

    def _resolve_quote(self, pending: _PendingQuote) -> None:
        try:
            quote = QuoteSnapshot(
                con_id=pending.con_id,
                bid=pending.bid,
                ask=pending.ask,
                observed_at=datetime.now(timezone.utc),
                bid_observed_at=pending.bid_at,
                ask_observed_at=pending.ask_at,
                min_tick=pending.min_tick,
                request_id=pending.request_id,
            )
        except ValueError as exc:
            self._last_error = str(exc)
            self._set_future_exception(
                pending.future, TWSRequestError("TWS quote snapshot failed validation")
            )
            return
        self._set_future_result(pending.future, quote)

    def _on_tick_snapshot_end(self, request_id: int) -> None:
        """A stream that ends before resolving explains why it could not."""
        self._callbacks_seen += 1
        pending = self._pending_quotes.get(request_id)
        if pending is None or pending.future.done():
            return
        if pending.data_type != 1:
            self._set_future_exception(
                pending.future,
                TWSRequestError("real-time market data is required; delayed/frozen data rejected"),
            )
            return
        if pending.bid is None or pending.ask is None:
            self._set_future_exception(
                pending.future,
                TWSRequestError("TWS quote snapshot did not contain bid and ask"),
            )
            return
        self._resolve_quote(pending)

    def _on_error(self, request_id: int, code: int, message: str) -> None:
        self._callbacks_seen += 1
        if code in WARNING_CODE_RANGE:
            return
        self._last_error = f"TWS error {code}: {message}"
        competing = is_competing_session_error(code, str(message))
        error_type = TWSCompetingSessionError if competing else TWSRequestError
        if request_id == -1:
            if competing:
                # No stream can deliver data while another login holds it.
                for snapshot in tuple(self._pending_snapshots.values()):
                    self._fail_snapshot(snapshot, TWSCompetingSessionError(self._last_error))
            return
        pending_snapshot = self._pending_snapshots.get(request_id)
        if pending_snapshot is not None:
            self._fail_snapshot(pending_snapshot, error_type(self._last_error))
        pending_scan = self._pending_scans.get(request_id)
        pending_bars = self._pending_bars.get(request_id)
        pending_daily = self._pending_daily.get(request_id)
        pending_details = self._pending_details.get(request_id)
        pending_quote = self._pending_quotes.get(request_id)
        if pending_scan is not None:
            self._set_future_exception(pending_scan.future, error_type(self._last_error))
        if pending_details is not None:
            self._set_future_exception(pending_details.future, error_type(self._last_error))
        if pending_bars is not None:
            self._set_future_result(
                pending_bars.future,
                BarBatch(
                    bars=(),
                    connected=self._connected,
                    pacing_failed=code in PACING_ERROR_CODES,
                    error=self._last_error,
                ),
            )
        if pending_daily is not None:
            self._set_future_result(
                pending_daily.future,
                DailyBarBatch(
                    bars=(),
                    connected=self._connected,
                    pacing_failed=code in PACING_ERROR_CODES,
                    error=self._last_error,
                ),
            )
        if pending_quote is not None:
            self._set_future_exception(pending_quote.future, error_type(self._last_error))

    def _on_connection_closed(self) -> None:
        self._callbacks_seen += 1
        self._connected = False
        error = TWSConnectionError("TWS connection closed")
        for pending in (
            *self._pending_scans.values(),
            *self._pending_bars.values(),
            *self._pending_daily.values(),
            *self._pending_details.values(),
            *self._pending_quotes.values(),
        ):
            self._set_future_exception(pending.future, error)
        for snapshot in tuple(self._pending_snapshots.values()):
            self._fail_snapshot(snapshot, error)

    def _set_future_result(self, future: asyncio.Future[Any], value: Any) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            if not future.done():
                future.set_result(value)
        else:
            loop.call_soon_threadsafe(self._resolve_result, future, value)

    @staticmethod
    def _resolve_result(future: asyncio.Future[Any], value: Any) -> None:
        if not future.done():
            future.set_result(value)

    def _set_future_exception(self, future: asyncio.Future[Any], error: Exception) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            if not future.done():
                future.set_exception(error)
        else:
            loop.call_soon_threadsafe(self._resolve_exception, future, error)

    @staticmethod
    def _resolve_exception(future: asyncio.Future[Any], error: Exception) -> None:
        if not future.done():
            future.set_exception(error)

    def _signal_event(self, event: asyncio.Event | None) -> None:
        if event is None or self._loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            event.set()
        else:
            self._loop.call_soon_threadsafe(event.set)

    def _scanner_result(self, rank: int, details: Any) -> IBKRScannerResult | Exception:
        """Translate IBKR's zero-based rank; leave missing fields empty for row rejection."""
        contract = getattr(details, "contract", details)

        def text(name: str) -> str:
            value = getattr(contract, name, "")
            return "" if value is None else str(value)

        try:
            raw_rank = int(rank)
        except (TypeError, ValueError):
            return TWSRequestError("TWS scanner callback has an invalid rank")
        if raw_rank < 0:
            return TWSRequestError("TWS scanner callback has a negative rank")
        try:
            con_id = int(getattr(contract, "conId", 0) or 0)
        except (TypeError, ValueError):
            con_id = 0
        return IBKRScannerResult(
            con_id=con_id,
            symbol=text("symbol"),
            currency=text("currency"),
            security_type=text("secType"),
            stock_type=str(getattr(details, "stockType", "") or text("stockType")),
            primary_exchange=text("primaryExchange"),
            rank=raw_rank + 1,
            raw_rank=raw_rank,
            exchange=text("exchange"),
        )

    @staticmethod
    def _trade_bar(raw: Any, *, con_id: int) -> IBKRTradeBar:
        start = _parse_ibkr_timestamp(getattr(raw, "date"))
        end = start + timedelta(minutes=5)
        return IBKRTradeBar(
            con_id=int(getattr(raw, "conId", getattr(raw, "con_id", con_id))),
            interval_start_utc=start,
            interval_end_utc=end,
            open=Decimal(str(getattr(raw, "open"))),
            high=Decimal(str(getattr(raw, "high"))),
            low=Decimal(str(getattr(raw, "low"))),
            close=Decimal(str(getattr(raw, "close"))),
            volume=Decimal(str(getattr(raw, "volume"))),
            # From the calendar, never from the request: the same parser serves
            # RTH-only and all-hours requests, and strategies split on this flag.
            rth=is_regular_session_bar(start, end),
            bar_type="TRADES",
            delayed=False,
            frozen=False,
            complete=True,
            received_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _daily_bar(raw: Any, *, con_id: int) -> IBKRDailyBar:
        return IBKRDailyBar(
            con_id=con_id,
            session_date=_parse_ibkr_session_date(getattr(raw, "date")),
            close=Decimal(str(getattr(raw, "close"))),
            volume=Decimal(str(getattr(raw, "volume"))),
        )

    def _cancel_scanner(self, request_id: int) -> None:
        if self._client is not None:
            method = getattr(self._client, "cancelScannerSubscription", None)
            if callable(method):
                method(request_id)

    def _cancel_historical(self, request_id: int) -> None:
        if self._client is not None:
            method = getattr(self._client, "cancelHistoricalData", None)
            if callable(method):
                method(request_id)

    def _cancel_market_data(self, request_id: int) -> None:
        if self._client is not None:
            method = getattr(self._client, "cancelMktData", None)
            if callable(method):
                method(request_id)


def _validate_snapshot_request(con_ids: Sequence[int], timeout_seconds: float) -> None:
    if any(
        isinstance(con_id, bool) or not isinstance(con_id, int) or con_id <= 0
        for con_id in con_ids
    ):
        raise TWSRequestError("con_id must be positive")
    if not 0 < timeout_seconds < float("inf"):
        raise TWSRequestError("timeout_seconds must be finite and positive")


def _snapshot_missing(snapshot: _PendingMarketSnapshot) -> list[str]:
    """What a snapshot still lacks before it may be returned at all."""
    missing: list[str] = []
    if snapshot.data_type != REAL_TIME_MARKET_DATA:
        missing.append("real-time confirmation")
    prices = snapshot.prices
    if _TICK_LAST not in prices and not (_TICK_BID in prices and _TICK_ASK in prices):
        missing.append("a last trade or bid/ask")
    if _TICK_CLOSE not in prices:
        missing.append("prior close")
    return missing


def _parse_ibkr_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TWSRequestError("TWS bar timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)
    try:
        epoch = int(str(value))
    except (TypeError, ValueError) as exc:
        raise TWSRequestError("TWS bar timestamp must be a UTC epoch") from exc
    if epoch <= 0:
        raise TWSRequestError("TWS bar timestamp must be positive")
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _parse_ibkr_session_date(value: Any) -> date:
    """Daily bars carry the exchange session date as ``YYYYMMDD``."""
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise TWSRequestError("TWS daily bar date must be YYYYMMDD")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError as exc:
        raise TWSRequestError("TWS daily bar date is invalid") from exc
