from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading.market.ibkr_boundary import ScannerRequest
from trading.market.tws_actor import (
    PAPER_TWS_HOST,
    PAPER_TWS_PORT,
    TWSActorConfig,
    TWSConfigurationError,
    TWSConnectionError,
    TWSRequestError,
    DataLineCapacityReport,
    TWSPaperActor,
)


class FakeTWSClient:
    def __init__(
        self,
        callbacks,
        *,
        pacing_error: bool = False,
        quote_data_type: int | None = 1,
        quote_bid: str | None = "100",
        quote_ask: str | None = "100.25",
    ) -> None:
        self.callbacks = callbacks
        self.pacing_error = pacing_error
        self.quote_data_type = quote_data_type
        self.quote_bid = quote_bid
        self.quote_ask = quote_ask
        self.connect_calls: list[tuple[str, int, int]] = []
        self.scanner_calls: list[tuple[object, ...]] = []
        self.history_calls: list[tuple[object, ...]] = []
        self.cancelled_scanners: list[int] = []
        self.cancelled_history: list[int] = []
        self.disconnected = False

    def connect(self, host: str, port: int, client_id: int) -> None:
        self.connect_calls.append((host, port, client_id))
        self.callbacks.nextValidId(900)

    def run(self) -> None:
        return None

    def reqManagedAccts(self) -> None:
        self.callbacks.managedAccounts("DU123456")

    def reqScannerSubscription(self, *args: object) -> None:
        self.scanner_calls.append(args)
        request_id = int(args[0])
        contract = SimpleNamespace(
            conId=101,
            symbol="AAPL",
            currency="USD",
            secType="STK",
            stockType="COMMON",
            primaryExchange="NASDAQ",
        )
        # IBKR scanner ranks are zero-based.
        self.callbacks.scannerData(request_id, 0, SimpleNamespace(contract=contract))
        incomplete = SimpleNamespace(conId=202, symbol="", currency="USD", secType="STK")
        self.callbacks.scannerData(request_id, 1, SimpleNamespace(contract=incomplete))
        self.callbacks.scannerDataEnd(request_id)

    def reqContractDetails(self, request_id: int, contract: object) -> None:
        con_id = int(getattr(contract, "conId"))
        if con_id != 404:
            details = SimpleNamespace(
                contract=SimpleNamespace(
                    conId=con_id,
                    symbol="AAPL",
                    currency="USD",
                    secType="STK",
                    primaryExchange="NASDAQ",
                ),
                minTick=0.01,
                stockType="COMMON",
            )
            self.callbacks.contractDetails(request_id, details)
        self.callbacks.contractDetailsEnd(request_id)

    def cancelScannerSubscription(self, request_id: int) -> None:
        self.cancelled_scanners.append(request_id)

    def reqHistoricalData(self, *args: object) -> None:
        self.history_calls.append(args)
        request_id = int(args[0])
        if self.pacing_error:
            self.callbacks.error(request_id, 162, "pacing")
            return
        bar = SimpleNamespace(
            date="1770000000",
            open="100",
            high="101",
            low="99",
            close="100.5",
            volume="1000",
        )
        self.callbacks.historicalData(request_id, bar)
        self.callbacks.historicalDataEnd(request_id)

    def cancelHistoricalData(self, request_id: int) -> None:
        self.cancelled_history.append(request_id)

    def reqMktData(self, *args: object) -> None:
        request_id = int(args[0])
        if self.quote_data_type is not None:
            self.callbacks.marketDataType(request_id, self.quote_data_type)
        if self.quote_bid is not None:
            self.callbacks.tickPrice(request_id, 1, float(self.quote_bid))
        if self.quote_ask is not None:
            self.callbacks.tickPrice(request_id, 2, float(self.quote_ask))
        self.callbacks.tickSnapshotEnd(request_id)

    def cancelMktData(self, request_id: int) -> None:
        return None

    def disconnect(self) -> None:
        self.disconnected = True


def config(**overrides: object) -> TWSActorConfig:
    values: dict[str, object] = {
        "account": "DU123456",
        "client_id": 17,
        "handshake_timeout_seconds": 0.2,
        "request_timeout_seconds": 0.2,
    }
    values.update(overrides)
    return TWSActorConfig(**values)


def test_config_rejects_non_loopback_or_non_paper_identity() -> None:
    with pytest.raises(TWSConfigurationError, match=PAPER_TWS_HOST):
        TWSActorConfig(account="DU123456", client_id=17, host="0.0.0.0").validate()
    with pytest.raises(TWSConfigurationError, match=str(PAPER_TWS_PORT)):
        TWSActorConfig(account="DU123456", client_id=17, port=7496).validate()
    with pytest.raises(TWSConfigurationError, match="account"):
        TWSActorConfig(account="", client_id=17).validate()
    with pytest.raises(TWSConfigurationError, match="client_id"):
        TWSActorConfig(account="DU123456", client_id=0).validate()


@pytest.mark.asyncio
async def test_constructor_and_import_do_not_create_client_or_connect() -> None:
    created: list[FakeTWSClient] = []

    def factory(callbacks):
        client = FakeTWSClient(callbacks)
        created.append(client)
        return client

    actor = TWSPaperActor(config(), client_factory=factory)

    assert created == []
    assert actor.connected is False
    with pytest.raises(TWSConnectionError):
        await actor.scan(ScannerRequest())


@pytest.mark.asyncio
async def test_connect_validates_identity_and_allocates_callback_request_ids() -> None:
    client: FakeTWSClient | None = None

    def factory(callbacks):
        nonlocal client
        client = FakeTWSClient(callbacks)
        return client

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()

    assert client is not None
    assert client.connect_calls == [(PAPER_TWS_HOST, PAPER_TWS_PORT, 17)]
    assert actor.connected is True
    assert actor.managed_accounts == frozenset({"DU123456"})
    assert actor.callbacks_seen >= 2

    results = await actor.scan(ScannerRequest())
    assert results[0].con_id == 101
    assert results[0].symbol == "AAPL"
    assert (results[0].raw_rank, results[0].rank) == (0, 1)
    # An incomplete row is surfaced for boundary rejection, not a failed scan.
    assert results[1].con_id == 202 and results[1].symbol == ""
    assert client.scanner_calls[0][0] == 900
    assert client.cancelled_scanners == [900]


@pytest.mark.asyncio
async def test_connect_rejects_a_tws_account_different_from_configured_identity() -> None:
    def factory(callbacks):
        client = FakeTWSClient(callbacks)

        def wrong_account() -> None:
            callbacks.managedAccounts("DU999999")

        client.reqManagedAccts = wrong_account
        return client

    actor = TWSPaperActor(config(), client_factory=factory)
    with pytest.raises(TWSConnectionError, match="account"):
        await actor.connect()
    assert actor.connected is False


@pytest.mark.asyncio
async def test_historical_request_returns_completed_trades_with_unique_request_id() -> None:
    client: FakeTWSClient | None = None

    def factory(callbacks):
        nonlocal client
        client = FakeTWSClient(callbacks)
        return client

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()
    batch = await actor.completed_trade_bars(101)

    assert client is not None
    assert len(batch.bars) == 1
    assert batch.bars[0].con_id == 101
    assert batch.bars[0].bar_type == "TRADES"
    assert batch.bars[0].complete is True
    assert batch.bars[0].interval_start_utc.tzinfo is timezone.utc
    assert client.history_calls[0][0] == 900
    assert client.history_calls[0][5] == "TRADES"
    assert client.history_calls[0][7] == 2


@pytest.mark.asyncio
async def test_pacing_error_is_reported_without_retry_or_fake_data() -> None:
    client: FakeTWSClient | None = None

    def factory(callbacks):
        nonlocal client
        client = FakeTWSClient(callbacks, pacing_error=True)
        return client

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()
    batch = await actor.completed_trade_bars(101)

    assert batch.bars == ()
    assert batch.pacing_failed is True
    assert batch.error is not None
    assert actor.connected is True
    assert client is not None
    assert len(client.history_calls) == 1


@pytest.mark.asyncio
async def test_quote_snapshot_requires_live_data_and_fresh_bid_ask() -> None:
    client: FakeTWSClient | None = None

    def factory(callbacks):
        nonlocal client
        client = FakeTWSClient(callbacks)
        return client

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()
    quote = await actor.quote_snapshot(101, min_tick=Decimal("0.01"))

    assert quote.con_id == 101
    assert quote.bid == Decimal("100")
    assert quote.ask == Decimal("100.25")
    assert quote.min_tick == Decimal("0.01")
    assert quote.is_fresh(now=datetime.now(timezone.utc))
    assert client is not None
    assert client.history_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("data_type", [2, 3, 4, None])
async def test_quote_snapshot_rejects_frozen_delayed_or_unconfirmed_data(
    data_type: int | None,
) -> None:
    def factory(callbacks):
        return FakeTWSClient(callbacks, quote_data_type=data_type)

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()
    with pytest.raises(TWSRequestError, match="real-time"):
        await actor.quote_snapshot(101, min_tick=Decimal("0.01"))


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["bid", "ask"])
async def test_quote_snapshot_requires_both_sides(missing: str) -> None:
    values = {"quote_bid": "100", "quote_ask": "100.25"}
    values[f"quote_{missing}"] = None

    def factory(callbacks):
        return FakeTWSClient(callbacks, **values)

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()
    with pytest.raises(TWSRequestError, match="bid and ask"):
        await actor.quote_snapshot(101, min_tick=Decimal("0.01"))


@pytest.mark.asyncio
async def test_preflight_reports_account_scanner_and_capacity_without_connecting() -> None:
    def factory(callbacks):
        return FakeTWSClient(callbacks)

    actor = TWSPaperActor(
        config(),
        client_factory=factory,
        capacity_checker=lambda required: required + 2,
    )
    disconnected = await actor.preflight(required_data_lines=3)
    assert disconnected.ready is False
    assert disconnected.connected is False
    assert disconnected.data_line_capacity.passed is True
    assert "disconnected" in disconnected.reasons[0]

    await actor.connect()
    ready = await actor.preflight(required_data_lines=3)
    assert ready.ready is True
    assert ready.account_verified is True
    assert ready.scanner_supported is True
    assert ready.data_line_capacity.available_lines == 5


@pytest.mark.asyncio
async def test_preflight_fails_when_capacity_hook_is_absent_or_insufficient() -> None:
    actor = TWSPaperActor(config(), client_factory=lambda callbacks: FakeTWSClient(callbacks))
    await actor.connect()
    without_hook = await actor.preflight(required_data_lines=2)
    assert without_hook.ready is False
    assert without_hook.data_line_capacity.available_lines is None

    limited = TWSPaperActor(
        config(),
        client_factory=lambda callbacks: FakeTWSClient(callbacks),
        capacity_checker=lambda required: DataLineCapacityReport(
            required_lines=required,
            available_lines=required - 1,
            passed=False,
            reason="not enough entitled market-data lines",
        ),
    )
    await limited.connect()
    report = await limited.preflight(required_data_lines=2)
    assert report.ready is False
    assert "not enough" in report.reasons[-1]


@pytest.mark.asyncio
async def test_disconnect_fails_closed_and_does_not_implicitly_reconnect() -> None:
    client: FakeTWSClient | None = None

    def factory(callbacks):
        nonlocal client
        client = FakeTWSClient(callbacks)
        return client

    actor = TWSPaperActor(config(), client_factory=factory)
    await actor.connect()
    await actor.disconnect()

    assert client is not None and client.disconnected is True
    assert actor.connected is False
    with pytest.raises(TWSConnectionError):
        await actor.completed_trade_bars(101)


@pytest.mark.asyncio
async def test_contract_details_resolve_min_tick_and_primary_exchange() -> None:
    actor = TWSPaperActor(config(), client_factory=lambda callbacks: FakeTWSClient(callbacks))
    await actor.connect()

    details = await actor.contract_details(101)

    assert details.con_id == 101
    assert details.primary_exchange == "NASDAQ"
    assert details.stock_type == "COMMON"
    assert details.min_tick == Decimal("0.01")
    with pytest.raises(TWSRequestError, match="missing or ambiguous"):
        await actor.contract_details(404)
