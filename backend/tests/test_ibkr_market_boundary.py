from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect

import pytest

from trading.market.ibkr_boundary import (
    BarBatch,
    ConnectionConfigurationError,
    IBKRConnectionActor,
    IBKRContractDetails,
    IBKRScannerResult,
    IBKRTradeBar,
    MarketDataQualityError,
    PaperIBKRMarketData,
    ScannerDataError,
    ScannerSupportError,
    qualify_scanner_results,
    validate_completed_trade_bars,
)


def _scanner_result(
    rank: int,
    *,
    currency: str = "USD",
    stock_type: str = "COMMON",
    primary_exchange: str = "NASDAQ",
) -> IBKRScannerResult:
    return IBKRScannerResult(
        con_id=1000 + rank,
        symbol=f"STK{rank}",
        currency=currency,
        security_type="STK",
        stock_type=stock_type,
        primary_exchange=primary_exchange,
        rank=rank,
        raw_rank=rank - 1,
    )


def _bar(**overrides) -> IBKRTradeBar:
    values = {
        "con_id": 101,
        "interval_start_utc": datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
        "interval_end_utc": datetime(2026, 1, 2, 14, 35, tzinfo=timezone.utc),
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100.5"),
        "volume": Decimal("1000"),
        "rth": True,
        "received_at": datetime(2026, 1, 2, 14, 35, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return IBKRTradeBar(**values)


def _details(
    con_id: int,
    *,
    stock_type: str = "COMMON",
    primary_exchange: str = "NASDAQ",
    min_tick: str = "0.01",
) -> IBKRContractDetails:
    return IBKRContractDetails(
        con_id=con_id,
        symbol="X",
        currency="USD",
        security_type="STK",
        stock_type=stock_type,
        primary_exchange=primary_exchange,
        min_tick=Decimal(min_tick),
    )


class _Actor:
    def __init__(
        self,
        *,
        scanner_supported: bool = True,
        batch: BarBatch | None = None,
        scan_results: tuple[IBKRScannerResult, ...] | None = None,
        details: dict[int, IBKRContractDetails] | None = None,
    ):
        self.scanner_supported = scanner_supported
        self.batch = batch or BarBatch(bars=(_bar(),))
        self.scan_results = scan_results
        self.details = details
        self.scanner_requests = []
        self.bar_requests = []

    async def supports_scanner(self, request):
        self.scanner_requests.append(request)
        return self.scanner_supported

    async def scan(self, request):
        if self.scan_results is not None:
            return self.scan_results
        return tuple(_scanner_result(rank) for rank in range(1, 4))

    async def contract_details(self, con_id):
        if self.details is not None:
            if con_id not in self.details:
                raise RuntimeError("no details")
            return self.details[con_id]
        return _details(con_id)

    async def completed_trade_bars(self, con_id):
        self.bar_requests.append(con_id)
        return self.batch


def test_boundary_has_one_actor_protocol_and_never_imports_network_clients():
    assert IBKRConnectionActor is not None
    source = inspect.getsource(__import__("trading.market.ibkr_boundary", fromlist=["module"]))
    assert "ib_insync" not in source
    assert "httpx" not in source
    assert "socket" not in source


def test_only_paper_tws_port_is_accepted():
    actor = _Actor()
    PaperIBKRMarketData(actor=actor, host="127.0.0.1", port=7497)
    with pytest.raises(ConnectionConfigurationError, match="7497"):
        PaperIBKRMarketData(actor=actor, host="127.0.0.1", port=7496)
    with pytest.raises(ConnectionConfigurationError, match="7497"):
        PaperIBKRMarketData(actor=actor, host="127.0.0.1", port=4001)


@pytest.mark.anyio
async def test_scanner_support_is_verified_and_admission_is_capped():
    actor = _Actor()
    adapter = PaperIBKRMarketData(actor=actor, host="127.0.0.1")
    await adapter.initialize()
    snapshot = qualify_scanner_results(
        tuple(_scanner_result(rank) for rank in range(1, 56)),
        scanner_run_id="scan-1",
    )

    assert len(snapshot.raw_results) == 55
    assert len(snapshot.admitted_candidates) == 50
    assert snapshot.admitted_candidates[-1].rank == 50
    assert snapshot.records[-1].rejection_reason == "admission_cap_reached"
    assert actor.scanner_requests[0].scan_code == "HOT_BY_VOLUME"
    assert actor.scanner_requests[0].location == "STK.US.MAJOR"
    assert actor.scanner_requests[0].stock_type_filter == "COMMON"


@pytest.mark.anyio
async def test_unsupported_scanner_blocks_snapshot():
    adapter = PaperIBKRMarketData(actor=_Actor(scanner_supported=False), host="127.0.0.1")
    with pytest.raises(ScannerSupportError, match="does not support"):
        await adapter.initialize()


def test_unusable_scanner_rows_are_rejected_individually():
    snapshot = qualify_scanner_results(
        (
            _scanner_result(1, currency="CAD"),
            _scanner_result(2, primary_exchange=""),
            _scanner_result(3, stock_type="ETF"),
            _scanner_result(4),
        ),
        scanner_run_id="scan-1",
    )

    reasons = [record.rejection_reason for record in snapshot.records]
    assert reasons == ["not_usd_contract", "missing_primary_exchange", "not_common_stock", None]
    assert [candidate.rank for candidate in snapshot.admitted_candidates] == [4]


def test_broken_ranking_still_fails_the_whole_scan():
    with pytest.raises(ScannerDataError, match="duplicate scanner rank"):
        qualify_scanner_results(
            (_scanner_result(1), _scanner_result(1)),
            scanner_run_id="scan-1",
        )
    mismatched = IBKRScannerResult(
        con_id=5, symbol="X", currency="USD", security_type="STK",
        stock_type="COMMON", primary_exchange="NYSE", rank=1, raw_rank=1,
    )
    with pytest.raises(ScannerDataError, match="raw IBKR rank"):
        qualify_scanner_results((mismatched,), scanner_run_id="scan-1")


@pytest.mark.anyio
async def test_snapshot_qualifies_rows_from_contract_details():
    scan = (
        _scanner_result(1, primary_exchange="", stock_type=""),
        _scanner_result(2, primary_exchange=""),
        _scanner_result(3, stock_type=""),
    )
    details = {
        1001: _details(1001, stock_type="", primary_exchange="NYSE", min_tick="0.01"),
        1003: _details(1003, stock_type="ETF"),
    }
    adapter = PaperIBKRMarketData(
        actor=_Actor(scan_results=scan, details=details), host="127.0.0.1"
    )

    snapshot = await adapter.scanner_snapshot(scanner_run_id="scan-1")

    by_rank = {record.source.rank: record for record in snapshot.records}
    # Exchange filled from details; the request's COMMON filter stands when TWS omits stockType.
    assert by_rank[1].admitted and by_rank[1].candidate.primary_exchange == "NYSE"
    assert by_rank[2].rejection_reason == "contract_details_unavailable"
    assert by_rank[3].rejection_reason == "not_common_stock"
    assert adapter.min_tick(1001) == Decimal("0.01")
    with pytest.raises(MarketDataQualityError):
        adapter.min_tick(1002)


@pytest.mark.anyio
async def test_valid_completed_trade_bars_cross_the_adapter():
    actor = _Actor()
    adapter = PaperIBKRMarketData(actor=actor, host="127.0.0.1")
    bars = await adapter.completed_bars(
        con_id=101,
        now=datetime(2026, 1, 2, 14, 35, 10, tzinfo=timezone.utc),
    )

    assert len(bars) == 1
    assert bars[0].source == "ibkr"
    assert bars[0].data_mode == "live"
    assert bars[0].completed is True
    assert actor.bar_requests == [101]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bar_type", "BID_ASK", "TRADES"),
        ("delayed", True, "delayed"),
        ("frozen", True, "frozen"),
        ("complete", False, "incomplete"),
    ],
)
def test_non_trade_or_unusable_bars_are_rejected(field, value, message):
    batch = BarBatch(bars=(_bar(**{field: value}),))
    with pytest.raises(MarketDataQualityError, match=message):
        validate_completed_trade_bars(
            batch,
            now=datetime(2026, 1, 2, 14, 35, 10, tzinfo=timezone.utc),
        )


def test_stale_duplicate_disconnected_and_pacing_data_are_rejected():
    now = datetime(2026, 1, 2, 14, 35, 40, tzinfo=timezone.utc)
    with pytest.raises(MarketDataQualityError, match="stale"):
        validate_completed_trade_bars(BarBatch(bars=(_bar(),)), now=now)
    with pytest.raises(MarketDataQualityError, match="duplicate"):
        validate_completed_trade_bars(BarBatch(bars=(_bar(), _bar())), now=now - timedelta(seconds=30))
    with pytest.raises(MarketDataQualityError, match="unavailable"):
        validate_completed_trade_bars(BarBatch(bars=(), connected=False), now=now)
    with pytest.raises(MarketDataQualityError, match="pacing"):
        validate_completed_trade_bars(BarBatch(bars=(), pacing_failed=True), now=now)
