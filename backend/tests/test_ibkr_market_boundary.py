from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect

import pytest

from trading.market.ibkr_boundary import (
    BarBatch,
    ConnectionConfigurationError,
    IBKRConnectionActor,
    IBKRScannerResult,
    IBKRTradeBar,
    MarketDataQualityError,
    PaperIBKRMarketData,
    ScannerDataError,
    ScannerSupportError,
    qualify_scanner_results,
    validate_completed_trade_bars,
)


def _scanner_result(rank: int, *, currency: str = "USD") -> IBKRScannerResult:
    return IBKRScannerResult(
        con_id=1000 + rank,
        symbol=f"STK{rank}",
        currency=currency,
        security_type="STK",
        stock_type="COMMON",
        primary_exchange="NASDAQ",
        rank=rank,
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


class _Actor:
    def __init__(self, *, scanner_supported: bool = True, batch: BarBatch | None = None):
        self.scanner_supported = scanner_supported
        self.batch = batch or BarBatch(bars=(_bar(),))
        self.scanner_requests = []
        self.bar_requests = []

    async def supports_scanner(self, request):
        self.scanner_requests.append(request)
        return self.scanner_supported

    async def scan(self, request):
        return tuple(_scanner_result(rank) for rank in range(1, 4))

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


def test_unsupported_scanner_identity_fails_closed():
    with pytest.raises(ScannerDataError, match="USD"):
        qualify_scanner_results(
            (_scanner_result(1, currency="CAD"),),
            scanner_run_id="scan-1",
        )


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
