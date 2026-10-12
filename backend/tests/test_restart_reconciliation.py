from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading.execution.reconciliation import (
    SnapshotBracketExpectation,
    SnapshotExpectedLeg,
    analyze_broker_snapshot,
)
from trading.execution.tws_paper_transport import (
    BrokerReconciliationSnapshot,
    ExecutionEvent,
    OpenOrderEvent,
    PositionEvent,
)


NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def expectation() -> SnapshotBracketExpectation:
    return SnapshotBracketExpectation(
        group_id="group-1",
        owner_id="owner-1",
        account="DU_TEST",
        client_id=17,
        symbol="AAPL",
        con_id=101,
        primary_exchange="NASDAQ",
        currency="USD",
        security_type="STK",
        quantity=10,
        oca_group="oca-1",
        state="submitted",
        legs=(
            SnapshotExpectedLeg(
                leg_id="parent",
                leg_type="parent",
                broker_order_id=100,
                order_ref="group-1:parent",
                quantity=10,
                price=Decimal("100"),
                transmit=False,
                state="accepted",
            ),
            SnapshotExpectedLeg(
                leg_id="target",
                leg_type="target",
                broker_order_id=101,
                parent_order_id=100,
                order_ref="group-1:target",
                oca_group="oca-1",
                quantity=10,
                price=Decimal("104"),
                transmit=False,
                state="accepted",
            ),
            SnapshotExpectedLeg(
                leg_id="stop",
                leg_type="stop",
                broker_order_id=102,
                parent_order_id=100,
                order_ref="group-1:stop",
                oca_group="oca-1",
                quantity=10,
                price=Decimal("98"),
                transmit=True,
                state="accepted",
            ),
        ),
    )


def open_order(
    leg_type: str, *, con_id: int = 101, client_id: int = 17, order_id: int | None = None
) -> OpenOrderEvent:
    resolved_order_id = order_id or {"parent": 100, "target": 101, "stop": 102}[leg_type]
    price = {"parent": Decimal("100"), "target": Decimal("104"), "stop": Decimal("98")}[leg_type]
    child = leg_type != "parent"
    return OpenOrderEvent(
        order_id=resolved_order_id,
        con_id=con_id,
        symbol="AAPL",
        security_type="STK",
        primary_exchange="NASDAQ",
        currency="USD",
        side="BUY" if not child else "SELL",
        order_type="STP" if leg_type == "stop" else "LMT",
        quantity=Decimal("10"),
        limit_price=None if leg_type == "stop" else price,
        auxiliary_price=price if leg_type == "stop" else None,
        parent_order_id=100 if child else None,
        transmit=leg_type == "stop",
        oca_group="oca-1" if child else None,
        account="DU_TEST",
        client_id=client_id,
        order_ref=f"group-1:{leg_type}",
        perm_id=None,
        tif="DAY",
        outside_rth=False,
        oca_type=1 if child else None,
        status="Submitted",
        received_at=NOW,
    )


def snapshot(
    *,
    open_orders: tuple[OpenOrderEvent, ...],
    positions: tuple[PositionEvent, ...] = (),
    executions: tuple[ExecutionEvent, ...] = (),
) -> BrokerReconciliationSnapshot:
    return BrokerReconciliationSnapshot(
        open_orders=open_orders,
        positions=positions,
        executions=executions,
        observed_at=NOW,
    )


def test_complete_matching_open_bracket_is_clear() -> None:
    result = analyze_broker_snapshot(
        (expectation(),),
        snapshot(open_orders=(open_order("parent"), open_order("target"), open_order("stop"))),
        expected_account="DU_TEST",
        expected_client_id=17,
    )

    assert result.status == "clear"
    assert result.findings == ()


def test_wrong_contract_or_client_blocks_restart() -> None:
    result = analyze_broker_snapshot(
        (expectation(),),
        snapshot(
            open_orders=(
                open_order("parent", con_id=999),
                open_order("target", client_id=18),
                open_order("stop"),
            )
        ),
        expected_account="DU_TEST",
        expected_client_id=17,
    )

    assert result.blocking
    assert sum(item.code == "identity_mismatch" for item in result.findings) == 2


def test_open_position_without_stop_is_blocked() -> None:
    position = PositionEvent(
        account="DU_TEST",
        con_id=101,
        symbol="AAPL",
        security_type="STK",
        primary_exchange="NASDAQ",
        currency="USD",
        quantity=Decimal("4"),
        average_cost=Decimal("100"),
        received_at=NOW,
    )
    execution = ExecutionEvent(
        request_id=1,
        execution_id="exec-1",
        order_id=100,
        con_id=101,
        symbol="AAPL",
        security_type="STK",
        currency="USD",
        perm_id=None,
        account="DU_TEST",
        shares=Decimal("4"),
        price=Decimal("100"),
        side="BOT",
        received_at=NOW,
    )
    result = analyze_broker_snapshot(
        (expectation(),),
        snapshot(open_orders=(open_order("target"),), positions=(position,), executions=(execution,)),
        expected_account="DU_TEST",
        expected_client_id=17,
    )

    assert result.blocking
    assert any(item.code == "missing_protection" for item in result.findings)


def test_unknown_order_position_and_execution_block_restart() -> None:
    unknown_order = open_order("parent", order_id=999)
    unknown_position = PositionEvent(
        account="DU_TEST",
        con_id=999,
        symbol="MSFT",
        security_type="STK",
        primary_exchange="NASDAQ",
        currency="USD",
        quantity=Decimal("1"),
        average_cost=Decimal("400"),
        received_at=NOW,
    )
    unknown_execution = ExecutionEvent(
        request_id=1,
        execution_id="exec-unknown",
        order_id=998,
        con_id=999,
        symbol="MSFT",
        security_type="STK",
        currency="USD",
        perm_id=None,
        account="DU_TEST",
        shares=Decimal("1"),
        price=Decimal("400"),
        side="BOT",
        received_at=NOW,
    )
    result = analyze_broker_snapshot(
        (expectation(),),
        snapshot(
            open_orders=(unknown_order,),
            positions=(unknown_position,),
            executions=(unknown_execution,),
        ),
        expected_account="DU_TEST",
        expected_client_id=17,
    )

    codes = {item.code for item in result.findings}
    assert {"unexpected_order", "unexpected_position", "unexpected_execution"} <= codes
