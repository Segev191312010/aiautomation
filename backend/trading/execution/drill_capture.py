"""``autotraderctl execution drill-capture --out <json>``: what a real TWS reports, read-only.

The engine's fill rules were written from the ibapi 9.81 source, not from a
live TWS (docs/PAPER_EXECUTION_STATUS.md, the drill checklist). This command
connects as its own client id, sends only read requests and records, raw,
what TWS answers, so the drill can settle:

* how ``reqCompletedOrders(apiOnly=False)`` reports TWS GUI orders: the raw
  ``filledQuantity`` of a filled, a partly filled then cancelled and an
  unfilled cancelled sell, ``completedTime``'s format and zone, ``permId``,
  and whether Inactive orders are listed (R-2, R-3);
* whether ``reqExecutions`` with an empty filter returns the GUI's
  executions: a positive fill signal (the drill checklist);
* how long a ``reqPositions`` answer trails a ``Filled`` report, and the
  latency of the engine's four-part snapshot;
* what TWS sends on error 1101 and whether a renewed ``reqPositions``
  answers again;
* that a second connection on the same client id is refused (error 326).

It never places, modifies or cancels anything: its broker protocol has no
such method and a source test keeps it that way. It refuses to run inside a
supervised trading day or next to the PAPER bridge.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Protocol

from trading.execution.live_executor import EXEC_REQUEST_ID, execution_filter, running_bridge_processes
from trading.execution.tws_state import DATA_LOST_CODE, TwsMirror


# Its own TWS client id: no supervised child, the bridge (18), the assistant (21), the recorder (28)
# or the scanner probe (29) uses it; a test derives the same from their modules.
DRILL_CAPTURE_CLIENT_ID = 30
DUPLICATE_CLIENT_CODE = 326  # "Unable to connect as the client id is already in use"
SNAPSHOT_TIMEOUT_S = 15.0
POSITION_LAG_WAIT_S = 20.0
EXIT_OK = 0
EXIT_REFUSED = 78
EXIT_RESTART = 75
UTC = timezone.utc


class ReadOnlyBroker(Protocol):
    """The only requests the capture sends: nothing here changes an order."""

    def isConnected(self) -> bool: ...
    def reqAllOpenOrders(self) -> None: ...
    def reqCompletedOrders(self, apiOnly: bool) -> None: ...
    def reqExecutions(self, reqId: int, execFilter: Any) -> None: ...
    def reqPositions(self) -> None: ...
    def cancelPositions(self) -> None: ...
    def reqAccountSummary(self, reqId: int, groupName: str, tags: str) -> None: ...
    def cancelAccountSummary(self, reqId: int) -> None: ...


class CaptureTransport(Protocol):
    broker: ReadOnlyBroker
    mirror: TwsMirror

    async def connect(self) -> None: ...
    def disconnect(self) -> None: ...


@dataclass
class Recorder:
    """Every raw callback the capture is interested in, in arrival order, with the capture clock."""

    started: float = field(default_factory=time.monotonic)
    completed: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    data_lost_at: list[float] = field(default_factory=list)

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 3)


def _raw(value: Any) -> Any:
    """A JSON-safe copy of what ibapi decoded: numbers and text exactly as received."""
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return repr(value)


def _order_fields(order: Any, *names: str) -> dict[str, Any]:
    return {name: _raw(getattr(order, name, None)) for name in names}


class RecordingMirror(TwsMirror):
    """The engine's mirror plus a raw log of the callbacks the drill checklist asks about."""

    def __init__(self, client_id: int, recorder: Recorder) -> None:
        super().__init__(client_id)
        self.recorder = recorder

    def completedOrder(self, contract: Any, order: Any, orderState: Any) -> None:
        self.recorder.completed.append({
            "at": self.recorder.elapsed(),
            "symbol": _raw(getattr(contract, "symbol", None)), "conId": _raw(getattr(contract, "conId", None)),
            **_order_fields(order, "permId", "orderId", "clientId", "action", "orderType", "totalQuantity",
                            "filledQuantity", "orderRef", "account", "tif"),
            "status": _raw(getattr(orderState, "status", None)),
            "completedStatus": _raw(getattr(orderState, "completedStatus", None)),
            "completedTime": _raw(getattr(orderState, "completedTime", None)),
        })
        super().completedOrder(contract, order, orderState)

    def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:
        self.recorder.open_orders.append({
            "at": self.recorder.elapsed(), "orderId": _raw(orderId),
            "symbol": _raw(getattr(contract, "symbol", None)),
            **_order_fields(order, "permId", "clientId", "action", "orderType", "totalQuantity",
                            "filledQuantity", "orderRef", "account"),
            "status": _raw(getattr(orderState, "status", None)),
        })
        super().openOrder(orderId, contract, order, orderState)

    def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:
        self.recorder.executions.append({
            "at": self.recorder.elapsed(), "reqId": _raw(reqId),
            "symbol": _raw(getattr(contract, "symbol", None)),
            **_order_fields(execution, "execId", "orderId", "permId", "clientId", "side", "shares", "cumQty",
                            "price", "time", "orderRef", "acctNumber"),
        })
        super().execDetails(reqId, contract, execution)

    def position(self, account: str, contract: Any, position: Any, avgCost: Any) -> None:
        self.recorder.positions.append({
            "at": self.recorder.elapsed(), "symbol": _raw(getattr(contract, "symbol", None)),
            "conId": _raw(getattr(contract, "conId", None)), "position": _raw(position),
        })
        super().position(account, contract, position, avgCost)

    def error(self, reqId: int, errorCode: Any, errorString: Any, *args: Any) -> None:
        self.recorder.errors.append({"at": self.recorder.elapsed(), "reqId": _raw(reqId), "code": _raw(errorCode),
                                     "text": _raw(errorString)})
        try:
            if int(errorCode) == DATA_LOST_CODE:
                self.recorder.data_lost_at.append(self.recorder.elapsed())
        except (TypeError, ValueError):
            pass
        super().error(reqId, errorCode, errorString, *args)


async def _wait(event: Any, timeout: float, *, poll: float = 0.01) -> float | None:
    """Seconds until ``event`` is set, or None after ``timeout``."""
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while not event.is_set():
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(poll)
    return round(time.monotonic() - started, 3)


async def four_part_snapshot(broker: ReadOnlyBroker, mirror: TwsMirror, client_id: int,
                             *, timeout: float = SNAPSHOT_TIMEOUT_S) -> dict[str, Any]:
    """The engine's snapshot (open orders, own executions, every completed order, positions) and its latency."""
    for event in (mirror.open_orders_done, mirror.exec_done, mirror.completed_done, mirror.positions_done):
        event.clear()
    started = time.monotonic()
    broker.reqAllOpenOrders()
    broker.reqExecutions(EXEC_REQUEST_ID, execution_filter(client_id))
    broker.reqCompletedOrders(False)
    broker.cancelPositions()
    broker.reqPositions()
    parts = {}
    for name, event in (("open_orders", mirror.open_orders_done), ("executions", mirror.exec_done),
                        ("completed_orders", mirror.completed_done), ("positions", mirror.positions_done)):
        remaining = max(0.0, timeout - (time.monotonic() - started))
        parts[name] = await _wait(event, remaining)
    return {"parts_seconds": parts, "total_seconds": round(time.monotonic() - started, 3),
            "complete": all(value is not None for value in parts.values())}


async def capture(transport: CaptureTransport, *, client_id: int, recorder: Recorder,
                  second_connection: Callable[[], CaptureTransport] | None = None,
                  position_lag_wait: float = POSITION_LAG_WAIT_S,
                  snapshot_timeout: float = SNAPSHOT_TIMEOUT_S) -> dict[str, Any]:
    """Run every read-only probe once connected; the report is raw callbacks plus what they show."""
    broker, mirror = transport.broker, transport.mirror
    report: dict[str, Any] = {"client_id": client_id, "ibapi_requests_only": True}
    accounts_in = await _wait(mirror.accounts_event, snapshot_timeout)
    report["managed_accounts"] = {"seconds": accounts_in, "count": len(mirror.accounts)}
    report["snapshot"] = await four_part_snapshot(broker, mirror, client_id, timeout=snapshot_timeout)

    # Completed orders of every client, raw: filledQuantity, completedTime, permId, Inactive listed or not.
    completed = list(recorder.completed)
    report["completed_orders"] = {
        "count": len(completed),
        "statuses": sorted({str(row["status"]) for row in completed}),
        "inactive_listed": any(row["status"] == "Inactive" for row in completed),
        "order_id_decoded": any(row.get("orderId") not in (0, None) for row in completed),
        "client_id_decoded": any(row.get("clientId") not in (0, None) for row in completed),
        "perm_id_present": all(row.get("permId") not in (0, None) for row in completed) if completed else None,
        "filled_quantity_values": sorted({repr(row["filledQuantity"]) for row in completed}),
        "completed_time_samples": sorted({str(row["completedTime"]) for row in completed})[:10],
        "rows": completed,
    }

    # Executions with an empty filter: does TWS return the GUI's (client 0) executions too?
    mirror.exec_done.clear()
    before = len(recorder.executions)
    broker.reqExecutions(EXEC_REQUEST_ID + 1, execution_filter(0))
    answered = await _wait(mirror.exec_done, snapshot_timeout)
    unfiltered = recorder.executions[before:]
    report["executions_empty_filter"] = {
        "seconds": answered, "count": len(unfiltered),
        "client_ids": sorted({str(row.get("clientId")) for row in unfiltered}),
        "gui_executions_returned": any(row.get("clientId") in (0, "0") for row in unfiltered),
        "rows": unfiltered,
    }

    # Positions lag: after the snapshot, how long until TWS reports positions again after cancel/req.
    mirror.positions_done.clear()
    broker.cancelPositions()
    started = time.monotonic()
    broker.reqPositions()
    report["positions_renewal"] = {"seconds": await _wait(mirror.positions_done, position_lag_wait),
                                   "count": len(recorder.positions)}

    # 1101 (connectivity restored, data lost) during the capture, and whether a renewed reqPositions answers.
    report["data_lost_1101"] = {"seen_at": list(recorder.data_lost_at), "renewal_seconds": None}
    if mirror.data_lost.is_set():
        mirror.data_lost.clear()
        mirror.positions_done.clear()
        broker.cancelPositions()
        broker.reqPositions()
        report["data_lost_1101"]["renewal_seconds"] = await _wait(mirror.positions_done, position_lag_wait)

    # A second connection on the same client id must be refused (326).
    report["duplicate_client_id"] = {"attempted": False}
    if second_connection is not None:
        other = second_connection()
        refused: str | None = None
        try:
            await other.connect()
        except Exception as exc:  # noqa: BLE001 - the refusal is the finding
            refused = f"{type(exc).__name__}: {exc}"
        finally:
            other.disconnect()
        # TWS answers the second connection on its own wrapper (or on the first's, if it is the same object).
        seen = list(recorder.errors)
        other_recorder = getattr(other.mirror, "recorder", None)
        if isinstance(other_recorder, Recorder) and other_recorder is not recorder:
            seen += other_recorder.errors
        codes = [row["code"] for row in seen if row["code"] in (DUPLICATE_CLIENT_CODE, str(DUPLICATE_CLIENT_CODE))]
        report["duplicate_client_id"] = {"attempted": True, "connect_error": refused,
                                         "error_326_seen": bool(codes), "first_still_connected": broker.isConnected()}

    report["errors"] = list(recorder.errors)
    report["positions"] = list(recorder.positions)
    report["open_orders"] = list(recorder.open_orders)
    return report


def capture_refusal(*, now: datetime, bridge_processes: Callable[[], list[tuple[int, str]]] = running_bridge_processes,
                    client_id: int = DRILL_CAPTURE_CLIENT_ID) -> str | None:
    """Why the capture must not run now: inside a trading day, next to the bridge, or on a child's id."""
    from trading.operations.db_admin import trading_day_at
    from trading.operations.scanner_probe import PROBE_CLIENT_ID, child_client_ids

    day = trading_day_at(now)
    if day is not None:
        return f"now is inside {day.describe()}: the capture never runs during a session"
    try:
        processes = bridge_processes()
    except Exception as exc:  # noqa: BLE001 - unreadable counts as running
        return f"the process table is unreadable ({type(exc).__name__}: {exc})"
    if processes:
        return f"the PAPER bridge is running (pid {processes[0][0]})"
    try:
        from trading.operations.settings import load_shadow_runtime_config

        config = load_shadow_runtime_config()
        taken = child_client_ids(config.data_client_id, config.order_client_id)
    except Exception as exc:  # noqa: BLE001 - without the configuration the documented ids apply
        from trading.operations.scanner_probe import DOCUMENTED_DATA_CLIENT_ID, DOCUMENTED_ORDER_CLIENT_ID

        taken = child_client_ids(DOCUMENTED_DATA_CLIENT_ID, DOCUMENTED_ORDER_CLIENT_ID)
        del exc
    if client_id in taken or client_id == PROBE_CLIENT_ID:
        return f"client id {client_id} belongs to a supervised child, the bridge, the assistant or the probe"
    return None


def main(out: Path, *, now: datetime | None = None,
         transport_factory: Callable[[int], CaptureTransport] | None = None) -> int:
    """``autotraderctl execution drill-capture --out <json>``."""
    moment = now or datetime.now(UTC)
    refusal = capture_refusal(now=moment)
    if refusal is not None:
        print(f"error: drill capture refused: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    recorder = Recorder()
    factory = transport_factory or _ibapi_transport
    transport = factory(DRILL_CAPTURE_CLIENT_ID)
    if isinstance(transport.mirror, RecordingMirror):
        transport.mirror.recorder = recorder

    async def run() -> dict[str, Any]:
        await transport.connect()
        try:
            return await capture(transport, client_id=DRILL_CAPTURE_CLIENT_ID, recorder=recorder,
                                 second_connection=lambda: factory(DRILL_CAPTURE_CLIENT_ID))
        finally:
            transport.disconnect()

    try:
        report = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - TWS unreachable or the handshake refused: transient
        print(f"error: drill capture failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_RESTART
    report["captured_at"] = moment.isoformat()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(f"drill capture written to {out}")
    return EXIT_OK


def _ibapi_transport(client_id: int) -> CaptureTransport:
    """The official ibapi client on the capture's own id, with the runner's handshake watchdog."""
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    from trading.execution.runner import IbapiTransport, RunnerConfig, runner_config

    class RecordingWrapper(RecordingMirror, EWrapper):
        """The recording mirror with ibapi's no-op defaults for every other callback."""

    class CaptureIbapiTransport(IbapiTransport):
        def __init__(self, config: RunnerConfig) -> None:
            super().__init__(config)
            self.mirror = RecordingWrapper(config.client_id, Recorder())
            self.client = EClient(self.mirror)
            self.broker = self.client

    config = runner_config()
    return CaptureIbapiTransport(RunnerConfig(account=config.account, client_id=client_id, host=config.host,
                                              port=config.port))


__all__ = [
    "DRILL_CAPTURE_CLIENT_ID",
    "DUPLICATE_CLIENT_CODE",
    "CaptureTransport",
    "ReadOnlyBroker",
    "Recorder",
    "RecordingMirror",
    "capture",
    "capture_refusal",
    "four_part_snapshot",
    "main",
]
