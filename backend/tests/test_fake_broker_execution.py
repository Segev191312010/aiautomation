from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.contracts import TradeIntent
from tests.fakes.bracket_executor import (
    OPTIONAL_TARGET_CONTRACT_MISMATCH,
    BracketValidationError,
    InMemoryBracketPersistence,
    ManualInterventionRequired,
    StagedBracketExecutor,
)
from tests.fakes.fake_broker import (
    BrokerConfigurationError,
    FakePaperBroker,
    PaperBrokerConfig,
    SubmissionEvent,
)


def make_intent(key: str = "execution-1", expires_in: int = 60) -> TradeIntent:
    return TradeIntent(
        execution_key=key,
        setup_fingerprint="a" * 64,
        approval_token="t" * 32,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        strategy_id="orb",
        strategy_version=1,
        symbol="AAPL",
        quantity=10,
        side="BUY",
        entry_limit=Decimal("100"),
        stop_price=Decimal("98"),
        target_price=Decimal("104"),
    )


def make_broker(**kwargs: object) -> FakePaperBroker:
    return FakePaperBroker(
        PaperBrokerConfig(
            host="host.docker.internal",
            port=7497,
            account="DU123456",
            client_id=17,
        ),
        **kwargs,
    )


async def submit(executor: StagedBracketExecutor, key: str = "execution-1", include_target: bool = True):
    return await executor.submit(
        make_intent(key), owner_id="owner-1", run_id="run-1",
        candidate_id="candidate-1", reservation_id="reservation-1",
        include_target=include_target,
    )


def test_non_paper_ports_and_identity_are_rejected() -> None:
    with pytest.raises(BrokerConfigurationError, match="7497"):
        FakePaperBroker(PaperBrokerConfig("host.docker.internal", 7496, "DU123456", 17))
    with pytest.raises(BrokerConfigurationError, match="client_id"):
        FakePaperBroker(PaperBrokerConfig("host.docker.internal", 7497, "DU123456", 0))


@pytest.mark.asyncio
async def test_persistence_precedes_submission_and_transmit_order() -> None:
    broker = make_broker()
    persistence = InMemoryBracketPersistence()
    observations: list[tuple[str, int]] = []

    async def callback(group, legs) -> None:
        observations.append((group.state, len(legs)))
        assert broker.submission_log == ()

    executor = StagedBracketExecutor(
        broker=broker, persistence=persistence, persist_callback=callback
    )
    result = await submit(executor)
    submission = [event for event in broker.submission_log if event.kind == "submit"]
    assert observations == [("staged", 3)]
    assert [event.role for event in submission] == ["parent", "target", "stop"]
    assert [event.transmit for event in submission] == [False, False, True]
    assert result.group.state == "submitted"
    assert [leg.state for leg in result.legs] == ["accepted"] * 3
    assert persistence.events[0][0] == "persist"


@pytest.mark.asyncio
async def test_execution_key_is_single_use_even_before_any_retry() -> None:
    broker = make_broker()
    executor = StagedBracketExecutor(broker=broker)
    await submit(executor)

    with pytest.raises(BracketValidationError, match="already been submitted"):
        await submit(executor)

    assert broker.submission_attempts == 1


@pytest.mark.asyncio
async def test_distinct_execution_keys_can_use_one_fake_broker() -> None:
    broker = make_broker()
    executor = StagedBracketExecutor(broker=broker)
    first = await submit(executor, key="execution-1")
    second = await submit(executor, key="execution-2")

    assert first.group.id != second.group.id
    assert broker.submission_attempts == 2
    assert len(broker.orders) == 6


def test_public_broker_events_reject_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SubmissionEvent(
            kind="submit",
            order_id=1,
            role="parent",
            observed_at=datetime.now(),
        )


@pytest.mark.asyncio
async def test_executor_rejects_naive_clock() -> None:
    executor = StagedBracketExecutor(broker=make_broker(), clock=datetime.now)
    with pytest.raises(ValueError, match="timezone-aware"):
        await submit(executor)


@pytest.mark.asyncio
async def test_identity_mismatch_fails_before_broker_submission() -> None:
    broker = make_broker()
    executor = StagedBracketExecutor(broker=broker)
    with pytest.raises(BracketValidationError, match="account"):
        await executor.submit(
            make_intent(), owner_id="owner-1", run_id="run-1",
            candidate_id="candidate-1", reservation_id="reservation-1",
            account="WRONG",
        )
    assert broker.submission_attempts == 0


@pytest.mark.asyncio
async def test_submission_failure_cancels_legs_and_fences_without_retry() -> None:
    broker = make_broker(fail_on_role="stop")
    persistence = InMemoryBracketPersistence()
    executor = StagedBracketExecutor(broker=broker, persistence=persistence)
    with pytest.raises(ManualInterventionRequired) as raised:
        await submit(executor)
    assert broker.submission_attempts == 1
    assert all(order.state == "canceled" for order in broker.orders)
    assert persistence.groups[raised.value.group_id].state == "manual_intervention"
    assert executor.groups[0].state == "manual_intervention"
    assert "no retry" in str(raised.value)


@pytest.mark.asyncio
async def test_persistence_failure_never_submits() -> None:
    broker = make_broker()
    executor = StagedBracketExecutor(
        broker=broker, persistence=InMemoryBracketPersistence(fail_persist=True)
    )
    with pytest.raises(ManualInterventionRequired):
        await submit(executor)
    assert broker.submission_attempts == 0
    assert broker.orders == ()


@pytest.mark.asyncio
async def test_partial_fill_protection_and_oca_cancellation() -> None:
    broker = make_broker()
    executor = StagedBracketExecutor(broker=broker)
    result = await submit(executor)
    parent = next(leg for leg in result.legs if leg.leg_type == "parent")
    target = next(leg for leg in result.legs if leg.leg_type == "target")
    stop = next(leg for leg in result.legs if leg.leg_type == "stop")
    await broker.fill_parent(parent.broker_order_id, 4)
    children = {
        order.role: order for order in broker.orders if order.role in {"stop", "target"}
    }
    assert children["stop"].protected_quantity == 4
    assert children["target"].protected_quantity == 4
    await broker.fill_exit(target.broker_order_id)
    states = {order.order_id: order.state for order in broker.orders}
    assert states[target.broker_order_id] == "filled"
    assert states[stop.broker_order_id] == "canceled"
    assert not any(event.kind == "submit" for event in broker.submission_log[3:])


@pytest.mark.asyncio
async def test_restart_unknown_state_blocks_without_repair() -> None:
    broker = make_broker()
    persistence = InMemoryBracketPersistence()
    executor = StagedBracketExecutor(broker=broker, persistence=persistence)
    result = await submit(executor)
    stop = next(leg for leg in result.legs if leg.leg_type == "stop")
    broker.mark_unknown(stop.broker_order_id)
    event_count = len(broker.submission_log)
    report = await executor.reconcile_after_restart()
    assert report.status == "blocked"
    assert report.unknowns == (f"order:{stop.broker_order_id}:unknown",)
    assert next(order for order in broker.orders if order.order_id == stop.broker_order_id).state == "unknown"
    assert len(broker.submission_log) == event_count
    assert persistence.groups[result.group.id].state == "manual_intervention"


@pytest.mark.asyncio
async def test_restart_with_new_executor_recovers_persisted_group_for_fencing() -> None:
    broker = make_broker()
    persistence = InMemoryBracketPersistence()
    first_executor = StagedBracketExecutor(broker=broker, persistence=persistence)
    result = await submit(first_executor)
    stop = next(leg for leg in result.legs if leg.leg_type == "stop")
    broker.mark_unknown(stop.broker_order_id)

    restarted_executor = StagedBracketExecutor(broker=broker, persistence=persistence)
    report = await restarted_executor.reconcile_after_restart()

    assert report.status == "blocked"
    assert persistence.groups[result.group.id].state == "manual_intervention"


@pytest.mark.asyncio
async def test_optional_target_harness_records_contract_mismatch() -> None:
    assert "TradeIntent" in OPTIONAL_TARGET_CONTRACT_MISMATCH
    broker = make_broker()
    with pytest.raises(BracketValidationError, match="required"):
        await submit(StagedBracketExecutor(broker=broker), include_target=False)
    assert broker.submission_attempts == 0
