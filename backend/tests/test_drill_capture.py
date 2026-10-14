"""The drill capture (autotraderctl execution drill-capture): read-only, on its own client id, never in a session.

TWS is never contacted: the capture runs over the fake TWS through its
transport interface.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re

import pytest

from tests.fakes.engine_harness import ACCOUNT, ny
from tests.fakes.fake_tws import FakeTws
from trading.execution import drill_capture
from trading.execution.drill_capture import (
    DRILL_CAPTURE_CLIENT_ID,
    ReadOnlyBroker,
    Recorder,
    RecordingMirror,
    capture,
    capture_refusal,
)


BACKEND = Path(__file__).resolve().parents[1]
HELD = 300
EVENING = datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)  # 19:30 ET, after the supervised day


class FakeCaptureTransport:
    def __init__(self, tws: FakeTws, recorder: Recorder, *, refuse: bool = False) -> None:
        self.tws = tws
        self.broker = tws
        self.mirror = RecordingMirror(DRILL_CAPTURE_CLIENT_ID, recorder)
        self.refuse = refuse
        self.connected = False

    async def connect(self) -> None:
        if self.refuse:  # TWS answers a duplicate client id with error 326 and drops the socket
            self.mirror.error(-1, 326, "Unable to connect as the client id is already in use.")
            raise ConnectionRefusedError("client id already in use")
        self.tws.attach(self.mirror)
        self.tws.connect()
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False


def gui_orders(tws: FakeTws) -> None:
    """What the drill checklist asks about: filled, partly filled then cancelled, unfilled cancelled, Inactive."""
    tws.add_foreign_order(client_id=0, order_id=1, con_id=HELD, symbol="HELD", action="SELL", quantity=100,
                          status="Filled")
    partial = tws.add_foreign_order(client_id=0, order_id=2, con_id=HELD, symbol="HELD", action="SELL", quantity=50,
                                    status="Cancelled")
    partial.filled = 20
    tws.add_foreign_order(client_id=0, order_id=3, con_id=HELD, symbol="HELD", action="SELL", quantity=30,
                          status="Cancelled")
    tws.add_foreign_order(client_id=0, order_id=4, con_id=HELD, symbol="HELD", action="BUY", quantity=10,
                          status="Inactive")
    tws.add_foreign_order(client_id=0, order_id=5, con_id=HELD, symbol="HELD", action="SELL", quantity=100)
    if tws.wrapper is None:
        tws.wrapper = RecordingMirror(DRILL_CAPTURE_CLIENT_ID, Recorder())  # a listener for the fill's callbacks
    tws.fill(5, 40, "50.00", client_id=0)  # a GUI execution: returned by an empty execution filter?


def test_the_capture_has_its_own_client_id() -> None:
    from trading.operations.scanner_probe import PROBE_CLIENT_ID, child_client_ids

    assert DRILL_CAPTURE_CLIENT_ID not in child_client_ids(17, 18)
    assert DRILL_CAPTURE_CLIENT_ID not in (0, PROBE_CLIENT_ID)


def test_the_capture_never_places_modifies_or_cancels_an_order() -> None:
    source = (BACKEND / "trading" / "execution" / "drill_capture.py").read_text()
    for forbidden in ("placeOrder", "cancelOrder", "reqGlobalCancel", "modify", "exerciseOptions"):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    protocol = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ReadOnlyBroker")
    requests = {node.name for node in protocol.body if isinstance(node, ast.FunctionDef)}
    assert requests == {"isConnected", "reqAllOpenOrders", "reqCompletedOrders", "reqExecutions", "reqPositions",
                        "cancelPositions", "reqAccountSummary", "cancelAccountSummary"}
    assert ReadOnlyBroker is not None


async def test_the_capture_records_raw_completed_orders_executions_positions_and_latency() -> None:
    tws = FakeTws(wrapper=None)
    gui_orders(tws)
    tws.positions[HELD] = 100
    recorder = Recorder()
    transport = FakeCaptureTransport(tws, recorder)
    await transport.connect()
    duplicates: list[FakeCaptureTransport] = []

    def second() -> FakeCaptureTransport:
        duplicates.append(FakeCaptureTransport(tws, recorder, refuse=True))
        return duplicates[-1]

    report = await capture(transport, client_id=DRILL_CAPTURE_CLIENT_ID, recorder=recorder, second_connection=second,
                           position_lag_wait=0.2, snapshot_timeout=0.5)
    transport.disconnect()

    assert report["snapshot"]["complete"] and set(report["snapshot"]["parts_seconds"]) == {
        "open_orders", "executions", "completed_orders", "positions"}
    completed = report["completed_orders"]
    assert completed["count"] == 4 and completed["inactive_listed"] is True
    assert completed["order_id_decoded"] is False and completed["client_id_decoded"] is False  # as 9.81 decodes
    assert completed["perm_id_present"] is True
    assert set(completed["filled_quantity_values"]) == {"100.0", "20.0", "0.0"}
    assert all(sample.endswith(" America/New_York") for sample in completed["completed_time_samples"])
    assert {row["filledQuantity"] for row in completed["rows"] if row["status"] == "Cancelled"} == {20.0, 0.0}
    assert report["executions_empty_filter"]["gui_executions_returned"] is True
    assert report["executions_empty_filter"]["client_ids"] == ["0"]
    assert report["positions_renewal"]["seconds"] is not None and report["positions"]
    assert report["duplicate_client_id"] == {"attempted": True, "connect_error": "ConnectionRefusedError: client id"
                                             " already in use", "error_326_seen": True, "first_still_connected": True}
    assert report["data_lost_1101"] == {"seen_at": [], "renewal_seconds": None}
    assert tws.placed == [] and tws.cancelled == []  # read-only, whatever TWS reported
    json.dumps(report, default=str)  # the whole report is JSON


async def test_a_1101_during_the_capture_is_recorded_with_its_renewal() -> None:
    tws = FakeTws(wrapper=None)
    tws.positions[HELD] = 100
    recorder = Recorder()
    transport = FakeCaptureTransport(tws, recorder)
    await transport.connect()
    original = tws.reqAllOpenOrders

    def open_orders_then_1101() -> None:
        original()
        transport.mirror.error(-1, 1101, "Connectivity between IB and TWS has been restored - data lost.")

    tws.reqAllOpenOrders = open_orders_then_1101  # type: ignore[method-assign]

    report = await capture(transport, client_id=DRILL_CAPTURE_CLIENT_ID, recorder=recorder,
                           position_lag_wait=0.2, snapshot_timeout=0.5)

    assert len(report["data_lost_1101"]["seen_at"]) == 1
    assert report["data_lost_1101"]["renewal_seconds"] is not None  # a renewed reqPositions answered
    assert tws.requests.count("reqPositions") >= 3  # the snapshot, the lag probe and the 1101 renewal
    assert [row["code"] for row in report["errors"]] == [1101]


def test_the_capture_refuses_inside_a_trading_day_next_to_the_bridge_or_on_a_childs_id() -> None:
    inside = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)  # 12:00 ET on a session day
    assert (capture_refusal(now=inside, bridge_processes=list) or "").startswith(
        "now is inside the 2026-10-13 trading day")
    assert capture_refusal(now=EVENING, bridge_processes=lambda: [(4242, "python paper_bridge.py")]) == (
        "the PAPER bridge is running (pid 4242)")
    assert "belongs to a supervised child" in (capture_refusal(now=EVENING, bridge_processes=list, client_id=18)
                                               or "")
    assert "belongs to a supervised child" in (capture_refusal(now=EVENING, bridge_processes=list, client_id=29)
                                               or "")
    assert capture_refusal(now=EVENING, bridge_processes=list) is None
    assert capture_refusal(now=datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc), bridge_processes=list) is None


def test_main_writes_the_report_or_refuses_before_connecting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(drill_capture, "running_bridge_processes", list)
    out = tmp_path / "capture" / "drill.json"
    connects: list[int] = []

    def factory(client_id: int) -> FakeCaptureTransport:
        connects.append(client_id)
        tws = FakeTws(wrapper=None)
        gui_orders(tws)
        return FakeCaptureTransport(tws, Recorder(), refuse=len(connects) > 1)

    inside = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)
    assert drill_capture.main(out, now=inside, transport_factory=factory) == 78
    assert connects == [] and not out.exists()  # refused inside the trading day, before any connection

    monkeypatch.setattr(drill_capture, "POSITION_LAG_WAIT_S", 0.2)
    monkeypatch.setattr(drill_capture, "SNAPSHOT_TIMEOUT_S", 0.5)
    assert drill_capture.main(out, now=EVENING, transport_factory=factory) == 0
    assert connects == [DRILL_CAPTURE_CLIENT_ID, DRILL_CAPTURE_CLIENT_ID]
    report = json.loads(out.read_text())
    assert report["client_id"] == DRILL_CAPTURE_CLIENT_ID and report["completed_orders"]["count"] == 4
    assert report["duplicate_client_id"]["error_326_seen"] is True
    assert report["captured_at"] == EVENING.isoformat()
    assert not re.search(r"\bDU\d{5,}\b", out.read_text().replace(ACCOUNT, ""))  # only the fake account


def test_the_cli_command_requires_out_and_runs_the_capture(tmp_path, monkeypatch, capsys) -> None:
    from trading.cli import main

    calls: list[Path] = []
    monkeypatch.setattr(drill_capture, "main", lambda out, **kwargs: calls.append(out) or 0)
    with pytest.raises(SystemExit) as error:
        main(["execution", "drill-capture"])
    assert error.value.code == 2 and "--out" in capsys.readouterr().err
    assert main(["execution", "drill-capture", "--out", str(tmp_path / "drill.json")]) == 0
    assert calls == [tmp_path / "drill.json"]


def test_the_drill_date_and_the_capture_are_outside_the_engines_digest() -> None:
    from trading.execution.promotion import ENGINE_SOURCE_FILES

    assert "trading/execution/drill_capture.py" not in ENGINE_SOURCE_FILES  # the capture never sends orders
    assert ny(10, 0).date() == date(2026, 10, 13)
