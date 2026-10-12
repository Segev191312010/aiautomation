from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from trading.execution.tws_paper_transport import (
    TWSSubmissionError,
    TWSPaperTransport,
    TWSPaperTransportConfig,
)


class SnapshotClient:
    def __init__(self, transport, *, account="DU_TEST"):
        self.transport = transport
        self.account = account
        self.cancelled_positions = False
        self.execution_filter = None

    def reqAllOpenOrders(self):
        self.transport._on_open_order_end()

    def reqPositions(self):
        self.transport._on_position(
            self.account,
            SimpleNamespace(conId=101, symbol="AAPL", secType="STK", currency="USD"),
            3,
            100,
        )
        self.transport._on_position_end()

    def reqExecutions(self, request_id, execution_filter):
        self.execution_filter = execution_filter
        self.transport._on_execution_end(request_id)

    def cancelPositions(self):
        self.cancelled_positions = True


@pytest.mark.asyncio
async def test_snapshot_waits_for_all_boundaries_and_filters_account(monkeypatch):
    observed_filter = SimpleNamespace()
    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        execution_filter_factory=lambda account, client_id: (
            setattr(observed_filter, "account", account),
            setattr(observed_filter, "client_id", client_id),
            observed_filter,
        )[-1],
    )
    client = SnapshotClient(transport)
    transport._client = client
    transport._loop = asyncio.get_running_loop()
    monkeypatch.setattr(transport, "_require_observation_ready", lambda: None)
    flush = AsyncMock()
    monkeypatch.setattr(transport, "_flush_observations", flush)
    snapshot = await transport.request_reconciliation_snapshot()
    assert snapshot.open_orders == ()
    assert len(snapshot.positions) == 1
    assert snapshot.executions == ()
    assert observed_filter.account == "DU_TEST"
    assert observed_filter.client_id == 17
    assert client.cancelled_positions
    flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_wrong_account_blocks_transport(monkeypatch):
    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        execution_filter_factory=lambda account, client_id: object(),
    )
    transport._client = SnapshotClient(transport, account="DU_OTHER")
    transport._loop = asyncio.get_running_loop()
    monkeypatch.setattr(transport, "_require_observation_ready", lambda: None)
    monkeypatch.setattr(transport, "_flush_observations", AsyncMock())
    with pytest.raises(TWSSubmissionError, match="unexpected account"):
        await transport.request_reconciliation_snapshot()
    assert transport._execution_blocked


@pytest.mark.asyncio
async def test_snapshot_timeout_blocks_and_cancels_position_subscription(monkeypatch):
    class NeverCompletes(SnapshotClient):
        def reqAllOpenOrders(self):
            return None

    transport = TWSPaperTransport(
        TWSPaperTransportConfig(
            account="DU_TEST", client_id=17, handshake_timeout_seconds=0.01,
        ),
        execution_filter_factory=lambda account, client_id: object(),
    )
    client = NeverCompletes(transport)
    transport._client = client
    transport._loop = asyncio.get_running_loop()
    monkeypatch.setattr(transport, "_require_observation_ready", lambda: None)
    with pytest.raises(TWSSubmissionError, match="snapshot failed"):
        await transport.request_reconciliation_snapshot()
    assert transport._execution_blocked
    assert client.cancelled_positions
    client.requests = 0
    transport._on_open_order_end()
    with pytest.raises(TWSSubmissionError, match="fresh connection"):
        await transport.request_reconciliation_snapshot()
    assert client.requests == 0


@pytest.mark.asyncio
async def test_overlapping_snapshots_are_serialized(monkeypatch):
    class ControlledClient:
        def __init__(self, transport):
            self.transport = transport
            self.requests = 0

        def reqAllOpenOrders(self):
            self.requests += 1

        def reqPositions(self):
            self.transport._on_position_end()

        def reqExecutions(self, request_id, execution_filter):
            self.transport._on_execution_end(request_id)

        def cancelPositions(self):
            return None

    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST", client_id=17),
        execution_filter_factory=lambda account, client_id: object(),
    )
    transport._loop = asyncio.get_running_loop()
    client = ControlledClient(transport)
    transport._client = client
    monkeypatch.setattr(transport, "_require_observation_ready", lambda: None)
    monkeypatch.setattr(transport, "_flush_observations", AsyncMock())
    first = asyncio.create_task(transport.request_reconciliation_snapshot())
    second = asyncio.create_task(transport.request_reconciliation_snapshot())
    await asyncio.sleep(0)
    assert client.requests == 1
    transport._on_open_order_end()
    await first
    await asyncio.sleep(0)
    assert client.requests == 2
    transport._on_open_order_end()
    await second
