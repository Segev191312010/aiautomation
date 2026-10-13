"""A scripted TWS for the scanner probe.

It replays scanner rows, scanner parameters, news providers and market-data
ticks through the EWrapper callbacks the real reader thread would call, from
its own ``run()`` thread. It implements only the probe's allowlisted client
calls, so any other call (an order, an account request) fails the test with
an AttributeError.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import heapq
import itertools
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Any
from xml.sax.saxutils import escape

from trading.operations.scanner_probe import (
    FIXTURE_SCAN_CODES,
    FIXTURE_SCAN_ROWS,
    SCAN_INSTRUMENT,
    SCAN_LOCATION,
    SCAN_VARIANTS,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scanner"
# ibapi's "not set" markers for ScannerSubscription fields.
UNSET_INTEGER = 2**31 - 1
UNSET_DOUBLE = 1.7976931348623157e308
_VARIANT_FIELDS = sorted({name for fields in SCAN_VARIANTS.values() for name in fields})

ScanRows = Mapping[tuple[str, str], Sequence[Mapping[str, Any]]]


def load_scanner_fixtures(directory: Path = FIXTURE_DIR) -> tuple[dict[str, Any], dict[str, Any]]:
    codes = json.loads((directory / FIXTURE_SCAN_CODES).read_text(encoding="utf-8"))
    rows = json.loads((directory / FIXTURE_SCAN_ROWS).read_text(encoding="utf-8"))
    return codes, rows


def rows_by_scan(scan_rows_fixture: Mapping[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    return {
        (scan["scan_code"], scan["variant"]): list(scan["rows"])
        for scan in scan_rows_fixture["scans"]
    }


def scanner_parameters_xml(
    scan_codes: Sequence[str],
    filter_codes: Sequence[str],
    *,
    instrument: str = SCAN_INSTRUMENT,
    location: str = SCAN_LOCATION,
) -> str:
    """The real response's shape, cut down: one filter id per filter code.

    A futures-only scan type and a filter outside the instrument's list are
    included so a parser that ignores the instrument fails the tests.
    """
    filter_ids = ",".join(code.upper() for code in filter_codes)
    scan_types = "".join(
        "<ScanType>"
        f"<displayName>{escape(code.title())}</displayName>"
        f"<scanCode>{escape(code)}</scanCode>"
        f"<instruments>{instrument},STOCK.NA,STOCK.EU,ETF.EQ.US</instruments>"
        "<absoluteColumns>false</absoluteColumns>"
        "<access>unrestricted</access>"
        "</ScanType>"
        for code in scan_codes
    )
    filters = "".join(
        "<RangeFilter>"
        f"<id>{escape(code.upper())}</id>"
        "<category>Fixture</category>"
        '<AbstractField type="scanner.field.DoubleField">'
        f"<code>{escape(code)}</code>"
        f"<displayName>{escape(code)}</displayName>"
        "</AbstractField>"
        "</RangeFilter>"
        for code in filter_codes
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<ScanParameterResponse>"
        '<InstrumentList varName="instrumentList">'
        f"<Instrument><name>US Stocks</name><type>{instrument}</type>"
        f"<filters>{filter_ids}</filters></Instrument>"
        "<Instrument><name>US Futures</name><type>FUT.US</type>"
        "<filters>FUTURESONLYFILTER</filters></Instrument>"
        "</InstrumentList>"
        '<LocationTree varName="locationTree">'
        "<Location><displayName>US Stocks</displayName><locationCode>STK.US</locationCode>"
        f"<instruments>{instrument}</instruments><LocationTree>"
        f"<Location><displayName>Listed/NASDAQ</displayName><locationCode>{location}</locationCode>"
        f"<instruments>{instrument}</instruments></Location>"
        "</LocationTree></Location>"
        "</LocationTree>"
        f'<ScanTypeList varName="scanTypeList">{scan_types}'
        "<ScanType><displayName>Futures Only</displayName><scanCode>FUTURES_ONLY_SCAN</scanCode>"
        "<instruments>FUT.US,FUT.EU</instruments></ScanType>"
        "</ScanTypeList>"
        f'<FilterList varName="filterList">{filters}'
        "<RangeFilter><id>FUTURESONLYFILTER</id>"
        "<AbstractField><code>futuresOnlyAbove</code></AbstractField></RangeFilter>"
        "</FilterList>"
        "</ScanParameterResponse>"
    )


def variant_of(subscription: Any) -> str:
    """Which SCAN_VARIANTS entry a subscription's built-in fields match."""
    present = {
        name: getattr(subscription, name)
        for name in _VARIANT_FIELDS
        if getattr(subscription, name, UNSET_DOUBLE) not in (UNSET_INTEGER, UNSET_DOUBLE)
    }
    for name, fields in SCAN_VARIANTS.items():
        if present == dict(fields):
            return name
    return "unknown"


class FakeScannerTWS:
    """Allowlisted calls only; callbacks arrive on the ``run()`` thread."""

    def __init__(
        self,
        wrapper: Any,
        *,
        scan_codes: Sequence[str] = (),
        filter_codes: Sequence[str] = (),
        scan_rows: ScanRows | None = None,
        news: Sequence[tuple[str, str]] = (("BRFG", "Briefing.com General Market Columns"),),
        accounts: str = "DU1234567",
        delay: float = 0.002,
        scan_errors: Mapping[str, tuple[int, str]] | None = None,
        silent_scans: Sequence[str] = (),
        line_errors: Mapping[str, tuple[int, str]] | None = None,
        line_data_type: int = 1,
        partial_lines: Sequence[str] = (),
        refuse_connect: tuple[int, str] | None = None,
        send_accounts: bool = True,
        extra_scanner_rows_after_end: bool = False,
        hang_connect: bool = False,
        close_after_scan_requests: int | None = None,
    ) -> None:
        self._wrapper = wrapper
        self.scan_codes = tuple(scan_codes)
        self.filter_codes = tuple(filter_codes)
        self.scan_rows = dict(scan_rows or {})
        self.news = tuple(news)
        self.accounts = accounts
        self.delay = delay
        self.scan_errors = dict(scan_errors or {})
        self.silent_scans = frozenset(silent_scans)
        self.line_errors = dict(line_errors or {})
        self.line_data_type = line_data_type
        self.partial_lines = frozenset(partial_lines)
        self.refuse_connect = refuse_connect
        self.send_accounts = send_accounts
        self.extra_scanner_rows_after_end = extra_scanner_rows_after_end
        self.hang_connect = hang_connect
        self.close_after_scan_requests = close_after_scan_requests
        self._unblock = threading.Event()
        self.calls: list[str] = []
        self.connect_args: tuple[str, int, int] | None = None
        self.callback_errors: list[BaseException] = []
        self.market_data_types: list[int] = []
        self.scan_requests: dict[int, Any] = {}
        self.scan_cancels: list[int] = []
        self.max_active_scans = 0
        self.line_requests: dict[int, Any] = {}
        self.line_cancels: list[int] = []
        self.max_active_lines = 0
        self._active_scans: set[int] = set()
        self._active_lines: set[int] = set()
        self._events: list[tuple[float, int, str, tuple[Any, ...], Callable[[], bool] | None]] = []
        self._sequence = itertools.count()
        self._condition = threading.Condition()
        self._connected = False
        self._stopped = False

    # -- delivery -------------------------------------------------------
    def _emit(
        self, delay: float, name: str, *args: Any, alive: Callable[[], bool] | None = None
    ) -> None:
        with self._condition:
            heapq.heappush(
                self._events, (time.monotonic() + delay, next(self._sequence), name, args, alive)
            )
            self._condition.notify_all()

    def run(self) -> None:
        self.calls.append("run")
        while True:
            with self._condition:
                while True:
                    if self._stopped:
                        return
                    now = time.monotonic()
                    if self._events and self._events[0][0] <= now:
                        break
                    wait = self._events[0][0] - now if self._events else 0.05
                    self._condition.wait(max(0.0, wait))
                _, _, name, args, alive = heapq.heappop(self._events)
            # TWS stops a cancelled stream; nothing for it is delivered after.
            if alive is not None and not alive():
                continue
            try:
                getattr(self._wrapper, name)(*args)
            except Exception as exc:  # noqa: BLE001 - surfaced by the tests
                self.callback_errors.append(exc)

    # -- connection -----------------------------------------------------
    def connect(self, host: str, port: int, client_id: int) -> None:
        self.calls.append("connect")
        self.connect_args = (host, port, client_id)
        if self.hang_connect:
            # The socket opened but TWS never answers the API handshake.
            self._unblock.wait(timeout=10.0)
            return
        if self.refuse_connect is not None:
            code, message = self.refuse_connect
            self._emit(0.0, "error", -1, code, message)
            self._emit(0.0, "connectionClosed")
            return
        self._connected = True
        self._emit(0.0, "nextValidId", 1)
        if self.send_accounts:
            self._emit(0.0, "managedAccounts", self.accounts)
        self._emit(0.0, "error", -1, 2104, "Market data farm connection is OK:usfarm")

    def disconnect(self) -> None:
        self.calls.append("disconnect")
        self._unblock.set()
        with self._condition:
            self._connected = False
            self._stopped = True
            self._events.clear()
            self._condition.notify_all()
        # ibapi's disconnect reports the closed connection too.
        self._wrapper.connectionClosed()

    def serverVersion(self) -> int:
        self.calls.append("serverVersion")
        return 151

    def reqManagedAccts(self) -> None:
        self.calls.append("reqManagedAccts")
        self._emit(self.delay, "managedAccounts", self.accounts)

    def reqMarketDataType(self, data_type: int) -> None:
        self.calls.append("reqMarketDataType")
        self.market_data_types.append(data_type)

    # -- scanner --------------------------------------------------------
    def reqScannerParameters(self) -> None:
        self.calls.append("reqScannerParameters")
        self._emit(
            self.delay,
            "scannerParameters",
            scanner_parameters_xml(self.scan_codes, self.filter_codes),
        )

    def reqScannerSubscription(
        self, request_id: int, subscription: Any, options: list[Any], filter_options: list[Any]
    ) -> None:
        self.calls.append("reqScannerSubscription")
        with self._condition:
            self.scan_requests[request_id] = subscription
            self._active_scans.add(request_id)
            self.max_active_scans = max(self.max_active_scans, len(self._active_scans))
            closing = len(self.scan_requests) == self.close_after_scan_requests
        if closing:
            # TWS restarts or drops the socket mid-run.
            self._emit(self.delay, "connectionClosed")
            return

        def alive() -> bool:
            return request_id in self._active_scans

        code = subscription.scanCode
        if code in self.scan_errors:
            error_code, message = self.scan_errors[code]
            self._emit(self.delay, "error", request_id, error_code, message, alive=alive)
            return
        if code in self.silent_scans:
            return
        rows = self.scan_rows.get((code, variant_of(subscription)), ())
        for row in rows:
            details = SimpleNamespace(
                contract=SimpleNamespace(
                    symbol=row["symbol"],
                    conId=row["con_id"],
                    primaryExchange=row["exchange"],
                    exchange="SMART",
                    secType="STK",
                    currency="USD",
                )
            )
            self._emit(self.delay, "scannerData", request_id, row["rank"], details, "", "", "", "", alive=alive)
        self._emit(self.delay, "scannerDataEnd", request_id, alive=alive)
        if self.extra_scanner_rows_after_end and rows:
            # A live subscription re-sends its list; the probe keeps the first.
            row = rows[0]
            details = SimpleNamespace(
                contract=SimpleNamespace(
                    symbol="LATE", conId=1, primaryExchange=row["exchange"], exchange="SMART"
                )
            )
            self._emit(self.delay, "scannerData", request_id, 99, details, "", "", "", "", alive=alive)

    def cancelScannerSubscription(self, request_id: int) -> None:
        self.calls.append("cancelScannerSubscription")
        with self._condition:
            self.scan_cancels.append(request_id)
            self._active_scans.discard(request_id)

    def reqNewsProviders(self) -> None:
        self.calls.append("reqNewsProviders")
        providers = [SimpleNamespace(code=code, name=name) for code, name in self.news]
        self._emit(self.delay, "newsProviders", providers)

    # -- market data ----------------------------------------------------
    def reqMktData(
        self,
        request_id: int,
        contract: Any,
        generic_ticks: str,
        snapshot: bool,
        regulatory_snapshot: bool,
        options: list[Any],
    ) -> None:
        self.calls.append("reqMktData")
        with self._condition:
            self.line_requests[request_id] = contract
            self._active_lines.add(request_id)
            self.max_active_lines = max(self.max_active_lines, len(self._active_lines))

        def alive() -> bool:
            return request_id in self._active_lines

        symbol = contract.symbol
        if symbol in self.line_errors:
            error_code, message = self.line_errors[symbol]
            self._emit(self.delay, "error", request_id, error_code, message, alive=alive)
            return
        self._emit(self.delay, "marketDataType", request_id, self.line_data_type, alive=alive)
        for tick in (1, 2, 4, 9):
            self._emit(self.delay, "tickPrice", request_id, tick, 100.0, None, alive=alive)
        self._emit(self.delay, "tickSize", request_id, 8, 1000, alive=alive)
        if symbol not in self.partial_lines:
            for tick in (6, 7, 14):
                self._emit(2 * self.delay, "tickPrice", request_id, tick, 100.0, None, alive=alive)

    def cancelMktData(self, request_id: int) -> None:
        self.calls.append("cancelMktData")
        with self._condition:
            self.line_cancels.append(request_id)
            self._active_lines.discard(request_id)


__all__ = [
    "FIXTURE_DIR",
    "FakeScannerTWS",
    "load_scanner_fixtures",
    "rows_by_scan",
    "scanner_parameters_xml",
    "variant_of",
]
