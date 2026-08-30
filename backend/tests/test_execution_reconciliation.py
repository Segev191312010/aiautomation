from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from trading.execution.reconciliation import (
    BrokerEvent,
    BrokerEventNormalizationError,
    BracketExpectation,
    analyze_reconciliation,
    normalize_broker_event,
)


NOW = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)


def expected(*, with_target: bool = True) -> BracketExpectation:
    return BracketExpectation(
        group_id="group-1",
        parent_order_id=100,
        stop_order_id=101,
        target_order_id=102 if with_target else None,
        quantity=10,
        oca_group="oca-1",
    )


def event(
    order_id: int,
    state: str,
    *,
    event_id: str | None = None,
    leg_type: str | None = None,
    quantity: int = 10,
    filled_quantity: int = 0,
    oca_group: str | None = None,
    sequence: int | None = None,
    event_time: datetime = NOW,
) -> BrokerEvent:
    return BrokerEvent(
        event_id=event_id or f"event-{order_id}-{state}-{sequence}",
        group_id="group-1",
        order_id=order_id,
        leg_type=leg_type,
        state=state,
        quantity=quantity,
        filled_quantity=filled_quantity,
        oca_group=oca_group,
        sequence=sequence,
        event_time=event_time,
    )


def clear_events() -> tuple[BrokerEvent, ...]:
    return (
        event(100, "accepted", leg_type="parent", sequence=1),
        event(101, "accepted", leg_type="stop", oca_group="oca-1", sequence=1),
        event(102, "accepted", leg_type="target", oca_group="oca-1", sequence=1),
    )


def test_normalize_broker_fields_and_unknown_state_without_sdk():
    normalized = normalize_broker_event(
        {
            "eventId": "ibkr-1",
            "groupId": "group-1",
            "orderId": "101",
            "status": "PreSubmitted",
            "totalQuantity": "10",
            "filled": "0",
            "ocaGroup": "oca-1",
            "timestamp": "2026-08-27T14:00:00Z",
            "seq": 4,
        }
    )
    unknown = normalize_broker_event(
        {
            "event_id": "ibkr-2",
            "group_id": "group-1",
            "order_id": 101,
            "state": "BrokerInventedState",
            "quantity": 10,
            "event_time": NOW,
        }
    )

    assert normalized.state == "submitted"
    assert normalized.order_id == 101
    assert normalized.event_time.tzinfo == timezone.utc
    assert len(normalized.fingerprint()) == 64
    assert unknown.state == "unknown"
    assert unknown.raw_state == "BrokerInventedState"


def test_clean_bracket_reconciliation_is_not_blocking():
    report = analyze_reconciliation(expected(), clear_events())

    assert report.status == "clear"
    assert not report.blocking
    assert not report.should_fence
    assert report.findings == ()
    assert report.as_contract_report(paper_account="DU123", client_id=7).status == "clear"


def test_missing_and_unknown_order_states_block_restart():
    report = analyze_reconciliation(
        expected(),
        (
            event(100, "accepted", leg_type="parent", sequence=1),
            {
                "event_id": "event-101-mystery-2",
                "group_id": "group-1",
                "order_id": 101,
                "state": "mystery",
                "leg_type": "stop",
                "quantity": 10,
                "oca_group": "oca-1",
                "sequence": 2,
                "event_time": NOW,
            },
        ),
    )

    codes = {finding.code for finding in report.findings}
    assert report.should_fence
    assert "unknown_state" in codes
    assert "missing_order" in codes
    assert 102 in {finding.order_id for finding in report.findings}


def test_duplicate_and_out_of_order_events_are_reported():
    accepted = event(100, "accepted", leg_type="parent", sequence=2)
    report = analyze_reconciliation(
        expected(with_target=False),
        (
            accepted,
            accepted,
            event(100, "submitted", leg_type="parent", sequence=1),
            event(101, "accepted", leg_type="stop", oca_group="oca-1", sequence=1),
        ),
    )

    codes = [finding.code for finding in report.findings]
    assert "duplicate_event" in codes
    assert "out_of_order" in codes
    assert report.should_fence


def test_partial_parent_fill_without_protection_blocks():
    report = analyze_reconciliation(
        expected(),
        (
            event(100, "partial", leg_type="parent", filled_quantity=4, sequence=1),
            event(101, "accepted", leg_type="stop", quantity=3, oca_group="oca-1", sequence=2),
            event(102, "accepted", leg_type="target", quantity=10, oca_group="oca-1", sequence=2),
        ),
    )

    findings = [finding for finding in report.findings if finding.code == "partial_fill_unprotected"]
    assert report.should_fence
    assert len(findings) == 1
    assert findings[0].order_id == 101


def test_oca_mismatch_and_unexpected_order_block():
    report = analyze_reconciliation(
        expected(),
        (
            event(100, "accepted", leg_type="parent", sequence=1),
            event(101, "accepted", leg_type="stop", oca_group="wrong-oca", sequence=2),
            event(102, "accepted", leg_type="target", oca_group="oca-1", sequence=2),
            event(999, "accepted", sequence=3),
        ),
    )

    assert report.should_fence
    assert any(finding.code == "oca_inconsistent" for finding in report.findings)
    assert any(finding.code == "unexpected_order" for finding in report.findings)


def test_oca_sibling_ending_while_other_remains_active_blocks():
    report = analyze_reconciliation(
        expected(),
        (
            event(100, "accepted", leg_type="parent", sequence=1),
            event(101, "canceled", leg_type="stop", oca_group="oca-1", sequence=2),
            event(102, "accepted", leg_type="target", oca_group="oca-1", sequence=2),
        ),
    )

    assert report.should_fence
    assert any(finding.code == "oca_inconsistent" for finding in report.findings)


def test_optional_target_does_not_create_missing_target_finding():
    report = analyze_reconciliation(
        expected(with_target=False),
        (
            event(100, "accepted", leg_type="parent", sequence=1),
            event(101, "accepted", leg_type="stop", oca_group="oca-1", sequence=2),
        ),
    )

    assert report.status == "clear"


def test_malformed_event_is_a_blocking_finding():
    with pytest.raises(BrokerEventNormalizationError):
        normalize_broker_event({"order_id": 100})

    report = analyze_reconciliation(expected(), ({"order_id": 100},))

    assert report.should_fence
    assert any(finding.code == "malformed_event" for finding in report.findings)


def test_event_and_bracket_models_reject_invalid_fill_or_duplicate_ids():
    with pytest.raises(ValidationError):
        event(100, "partial", quantity=2, filled_quantity=3)
    with pytest.raises(ValidationError):
        BracketExpectation(
            group_id="group-1",
            parent_order_id=100,
            stop_order_id=100,
            quantity=10,
            oca_group="oca-1",
        )


def test_timestamp_regression_is_out_of_order_even_without_sequence():
    report = analyze_reconciliation(
        expected(with_target=False),
        (
            event(100, "submitted", leg_type="parent", event_time=NOW + timedelta(seconds=1)),
            event(100, "accepted", leg_type="parent", event_time=NOW),
            event(101, "accepted", leg_type="stop", oca_group="oca-1"),
        ),
    )

    assert report.should_fence
    assert any(finding.code == "out_of_order" for finding in report.findings)
