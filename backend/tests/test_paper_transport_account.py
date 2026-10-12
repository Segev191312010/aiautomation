"""Outgoing orders must carry the validated account, never a TWS default."""
import asyncio
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from trading.execution.tws_paper_transport import TWSPaperTransport, TWSPaperTransportConfig


@pytest.mark.asyncio
async def test_submission_binds_actual_broker_order_to_configured_account(monkeypatch):
    broker_order = SimpleNamespace(account="different-default")
    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17),
        contract_factory=lambda order: object(),
        order_factory=lambda order: broker_order,
    )
    monkeypatch.setattr(transport, "_require_ready", lambda: None)
    monkeypatch.setattr(transport, "_flush_observations", AsyncMock())
    monkeypatch.setattr(transport, "_validate_order", lambda order: None)
    transport._loop = asyncio.get_running_loop()
    transport._reserved_order_ids.add(100)
    observed = []
    receipt = object()

    def place_order(order_id, contract, actual_order):
        observed.append(actual_order.account)
        transport._pending[order_id].future.set_result(receipt)

    transport._client = SimpleNamespace(placeOrder=place_order)
    order = SimpleNamespace(order_id=100, order_ref="test-only", leg_type="parent")
    result = await transport.submit_order(order, account="DU_TEST_ONLY", client_id=17)
    assert result is receipt
    assert observed == ["DU_TEST_ONLY"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PendingSubmit", "ApiPending", "Submitted"])
async def test_order_status_alone_cannot_acknowledge_identity(status):
    from trading.execution.tws_paper_transport import _PendingSubmission

    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17))
    transport._loop = asyncio.get_running_loop()
    future = transport._loop.create_future()
    transport._pending[100] = _PendingSubmission(future, object())
    transport._on_order_status(100, status, 0, 10, 0, 1, 0, 0, 17)
    assert not future.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [None, "account", "conId", "orderRef", "totalQuantity"])
async def test_open_order_requires_matching_broker_identity(mutation):
    from trading.execution.tws_paper_transport import _PendingSubmission, TWSSubmissionError

    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17))
    transport._loop = asyncio.get_running_loop()
    future = transport._loop.create_future()
    expected = SimpleNamespace(
        con_id=101, primary_exchange="NASDAQ", symbol="AAPL", order_ref="ref",
        quantity=10, side="BUY", order_type="LMT", price=Decimal("100"),
        parent_order_id=None, oca_group=None, transmit=False,
    )
    transport._pending[100] = _PendingSubmission(future, expected)
    contract = SimpleNamespace(conId=101, primaryExchange="NASDAQ", symbol="AAPL", secType="STK", currency="USD")
    order = SimpleNamespace(account="DU_TEST_ONLY", orderRef="ref", totalQuantity=10,
                            action="BUY", orderType="LMT", lmtPrice=100, parentId=0,
                            transmit=False, tif="DAY", outsideRth=False, clientId=17)
    if mutation == "conId":
        contract.conId = 999
    elif mutation is not None:
        setattr(order, mutation, "wrong")
    transport._on_open_order(100, contract, order, SimpleNamespace(status="Submitted"))
    if mutation is None:
        assert (await future).accepted is True
        observation = transport.events[-1]
        assert observation.con_id == 101
        assert observation.primary_exchange == "NASDAQ"
        assert observation.currency == "USD"
        assert observation.client_id == 17
        assert observation.tif == "DAY"
        assert observation.outside_rth is False
    else:
        with pytest.raises(TWSSubmissionError, match="does not match"):
            await future


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["disconnect", "late_rejection", "connectivity_error"])
async def test_late_failure_blocks_reuse_and_reconnect(event):
    from trading.execution.tws_paper_transport import TWSSubmissionError, TWSHandshakeError

    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17))
    transport._connected = True
    transport._client = object()
    transport._next_order_id = 101
    if event == "disconnect":
        transport._on_connection_closed()
    elif event == "late_rejection":
        transport._on_order_status(100, "Inactive", 0, 10, 0, 1, 0, 0, 17)
    else:
        transport._on_error(-1, 1100, "connection lost")
    with pytest.raises(TWSSubmissionError, match="new orders blocked"):
        transport._require_ready()
    with pytest.raises(TWSHandshakeError, match="reconciliation"):
        await transport.connect()


@pytest.mark.asyncio
async def test_queued_acceptance_then_disconnect_does_not_report_success(monkeypatch):
    from trading.execution.tws_paper_transport import TWSSubmissionError

    transport = TWSPaperTransport(
        TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17),
        contract_factory=lambda order: object(), order_factory=lambda order: SimpleNamespace(),
    )
    monkeypatch.setattr(transport, "_require_ready", lambda: None)
    monkeypatch.setattr(transport, "_flush_observations", AsyncMock())
    monkeypatch.setattr(transport, "_validate_order", lambda order: None)
    transport._loop = asyncio.get_running_loop()
    transport._reserved_order_ids.add(100)

    def place_order(order_id, contract, broker_order):
        transport._pending[order_id].future.set_result(object())
        transport._on_connection_closed()

    transport._client = SimpleNamespace(placeOrder=place_order)
    with pytest.raises(TWSSubmissionError, match="uncertainty"):
        await transport.submit_order(
            SimpleNamespace(order_id=100, order_ref="ref", leg_type="parent"),
            account="DU_TEST_ONLY", client_id=17,
        )


@pytest.mark.asyncio
async def test_explicit_disconnect_cannot_reconnect_without_callback():
    from trading.execution.tws_paper_transport import TWSHandshakeError

    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17))
    await transport.disconnect()
    with pytest.raises(TWSHandshakeError, match="reconciliation"):
        await transport.connect()


def test_execution_callback_alone_blocks_transport():
    from trading.execution.tws_paper_transport import TWSSubmissionError

    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17))
    transport._on_execution(1, object(), SimpleNamespace(
        execId="test-fill", orderId=100, acctNumber="DU_TEST_ONLY", shares=1, price=100,
    ))
    with pytest.raises(TWSSubmissionError, match="new orders blocked"):
        transport._require_ready()
    assert len(transport.events) == 1


@pytest.mark.asyncio
async def test_block_during_order_construction_prevents_send(monkeypatch):
    from trading.execution.tws_paper_transport import TWSSubmissionError

    transport = TWSPaperTransport(TWSPaperTransportConfig(account="DU_TEST_ONLY", client_id=17))
    monkeypatch.setattr(transport, "_require_ready", lambda: None)
    monkeypatch.setattr(transport, "_flush_observations", AsyncMock())
    monkeypatch.setattr(transport, "_validate_order", lambda order: None)
    transport._loop = asyncio.get_running_loop()
    transport._reserved_order_ids.add(100)
    sent = []
    transport._client = SimpleNamespace(placeOrder=lambda *args: sent.append(args))
    transport._contract_factory = lambda order: object()

    def build_order(order):
        transport._block_execution()
        return SimpleNamespace()

    transport._order_factory = build_order
    with pytest.raises(TWSSubmissionError, match="blocks new orders"):
        await transport.submit_order(
            SimpleNamespace(order_id=100, order_ref="ref", leg_type="parent"),
            account="DU_TEST_ONLY", client_id=17,
        )
    assert sent == []
