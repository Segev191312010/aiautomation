"""Recorded TWS callback sequences replayed into the execution engine.

``ScriptedTws`` records what the engine sends but answers order traffic only
through the recordings in tests/fakes/tws_recordings.py, so each test checks
the engine against a whole callback sequence rather than against the fake's
own model of TWS.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from tests.fakes.engine_harness import ACCOUNT, CLIENT_ID, Harness, build
from tests.fakes.fake_tws import FakeTws
from tests.fakes.tws_recordings import (
    BRACKET_FILL,
    MODIFY_ECHO,
    PARTIAL_FILL_CHILDREN_TRACK,
    PARTIAL_THEN_CANCELLED,
    REJECTED_MODIFY,
    RESTART_MID_SEND_CREATED_LEGS,
    RESTART_MID_SEND_NOTHING,
    Placed,
    Recording,
    replay,
)
from tests.test_live_executor_entries import db_rows


CON_ID = 265598


@dataclass
class ScriptedTws(FakeTws):
    """Answers connection and snapshot requests; order feedback comes only from recordings."""

    snapshot: Recording | None = None

    def placeOrder(self, orderId: int, contract: Any, order: Any) -> None:
        if self.fail_place_after is not None and len(self.placed) >= self.fail_place_after:
            raise OSError("socket closed while sending")
        self.placed.append((orderId, order))

    def cancelOrder(self, orderId: int) -> None:
        self.cancelled.append(orderId)

    def reqAllOpenOrders(self) -> None:
        self.requests.append("reqAllOpenOrders")
        if self.snapshot is None:
            super().reqAllOpenOrders()
            return
        placed = placed_from(self, symbol="AAPL")
        replay(self.snapshot, self.wrapper, placed)  # the recording ends every snapshot itself

    def reqExecutions(self, reqId: int, execFilter: Any) -> None:
        if self.snapshot is None:
            super().reqExecutions(reqId, execFilter)

    def reqCompletedOrders(self, apiOnly: bool) -> None:
        if self.snapshot is None:
            super().reqCompletedOrders(apiOnly)

    def reqPositions(self) -> None:
        if self.snapshot is None:
            super().reqPositions()
            return
        self.wrapper.positionEnd()  # the account holds nothing in these scenarios


def placed_from(tws: FakeTws, *, symbol: str) -> Placed:
    orders = {order.orderRef.rsplit(":", 1)[1]: order for _, order in tws.placed if order.orderRef.startswith("at1:")}
    ids = {order.orderRef.rsplit(":", 1)[1]: order_id for order_id, order in tws.placed
           if order.orderRef.startswith("at1:")}
    return Placed(orders=orders, ids=ids, con_id=CON_ID, symbol=symbol, account=ACCOUNT, client_id=CLIENT_ID)


async def scripted(tmp_path: Any, monkeypatch: Any) -> Harness:
    h = await build(tmp_path, monkeypatch, start=False)
    tws = ScriptedTws(wrapper=h.engine.mirror)
    tws.connect()
    h.engine.broker = tws
    h.tws = tws
    await h.engine.start()
    await h.decide("AAPL", con_id=CON_ID)
    await h.engine.tick()
    assert [order.orderRef[-1] for _, order in tws.placed] == ["P", "T", "S"]
    h.events()
    return h


def part(recording: Recording, start: int, stop: int | None = None) -> Recording:
    return Recording(name=recording.name, steps=recording.steps[start:stop])


async def test_recorded_bracket_fill(tmp_path, monkeypatch) -> None:
    h = await scripted(tmp_path, monkeypatch)
    replay(BRACKET_FILL, h.mirror, placed_from(h.tws, symbol="AAPL"))
    await h.engine.tick()

    bracket = h.bracket("AAPL")
    assert (bracket.state, bracket.entry_filled, bracket.exit_filled, bracket.exit_reason) == (
        "closed", 100, 100, "target"
    )
    assert bracket.realized_pnl == Decimal("97.98")  # 100 x 1.00 less 1.00 and 1.02 commissions
    assert {code: leg.broker_status for code, leg in bracket.legs.items()} == {
        "P": "Filled", "T": "Filled", "S": "Cancelled"
    }
    assert db_rows("SELECT exec_id, shares, commission FROM autotrader_exec_fills ORDER BY exec_id") == [
        ("0000e0d5.6705a1b2.01.01", 100, "1.0"), ("0000e0d5.6705a1b3.01.01", 100, "1.02")
    ]
    out = h.events()
    # Filled and exited between two saves: the ledger accepts working -> closed (this recording caught it).
    assert [kind for kind, _ in out] == ["FILL", "EXIT", "CLOSED"]
    assert db_rows("SELECT state, entry_filled, exit_filled FROM autotrader_exec_brackets") == [("closed", 100, 100)]
    assert h.tws.placed[3:] == []  # nothing sent after the placement


async def test_recorded_partial_fill_with_children_tracking_it(tmp_path, monkeypatch) -> None:
    h = await scripted(tmp_path, monkeypatch)
    placed = placed_from(h.tws, symbol="AAPL")
    replay(part(PARTIAL_FILL_CHILDREN_TRACK, 0, 13), h.mirror, placed)
    await h.engine.tick()
    bracket = h.bracket("AAPL")
    assert (bracket.state, bracket.entry_filled, bracket.avg_entry) == ("open", 40, Decimal("49.9800"))

    replay(part(PARTIAL_FILL_CHILDREN_TRACK, 13), h.mirror, placed)
    await h.engine.tick()
    assert (bracket.entry_filled, bracket.avg_entry, bracket.commissions) == (100, Decimal("49.9920"), Decimal("1.35"))
    assert h.tws.placed[3:] == []  # the children already cover the whole entry: no resize
    assert h.critical() == []


async def test_recorded_partial_cancel_resizes_the_children_and_counts_the_echo(tmp_path, monkeypatch) -> None:
    h = await scripted(tmp_path, monkeypatch)
    placed = placed_from(h.tws, symbol="AAPL")
    replay(PARTIAL_THEN_CANCELLED, h.mirror, placed)
    await h.engine.tick()
    bracket = h.bracket("AAPL")
    assert [(order_id, order.totalQuantity, order.transmit) for order_id, order in h.tws.placed[3:]] == [
        (101, 40, True), (102, 40, True)
    ]
    assert bracket.resized_qty == 0  # sent, not yet echoed

    replay(MODIFY_ECHO, h.mirror, placed)
    h.clock.advance(2)
    await h.engine.tick()
    assert bracket.resized_qty == 40
    assert len(h.tws.placed) == 5  # echoed: never sent again
    assert h.critical() == []


async def test_recorded_rejected_modify_is_resent_then_critical(tmp_path, monkeypatch) -> None:
    h = await scripted(tmp_path, monkeypatch)
    placed = placed_from(h.tws, symbol="AAPL")
    replay(PARTIAL_THEN_CANCELLED, h.mirror, placed)
    for _ in range(6):
        await h.engine.tick()
        replay(REJECTED_MODIFY, h.mirror, placed)  # TWS refuses every attempt
        h.clock.advance(2)

    assert [order_id for order_id, _ in h.tws.placed[3:]] == [101, 102] * 4
    assert h.bracket("AAPL").resized_qty == 0
    criticals = h.critical()
    assert criticals.count("TWS 101 321 Error validating request.-'bW' : cause - The order quantity cannot be"
                           " changed") == 6
    assert "AAPL: TWS did not confirm the resize of target 101 (40 sh @ 51.00) after 4 attempts — act in TWS" in (
        criticals
    )


async def crashed_mid_send(tmp_path: Any, monkeypatch: Any, snapshot: Recording) -> tuple[Harness, str]:
    h = await build(tmp_path, monkeypatch, start=False)
    tws = ScriptedTws(wrapper=h.engine.mirror, fail_place_after=2)
    tws.connect()
    h.engine.broker = tws
    h.tws = tws
    await h.engine.start()
    decision = await h.decide("AAPL", con_id=CON_ID)
    await h.engine.tick()  # P and T created at TWS; the socket dies before the transmitting stop
    assert [order_id for order_id, _ in tws.placed] == [100, 101]
    tws.fail_place_after = None
    tws.snapshot = snapshot
    h.events()
    restarted = await h.restart()
    return restarted, decision


async def test_recorded_restart_mid_send_that_tws_never_saw(tmp_path, monkeypatch) -> None:
    h, decision = await crashed_mid_send(tmp_path, monkeypatch, RESTART_MID_SEND_NOTHING)

    assert (await h.engine.ledger.outbox_row(decision)).state == "unknown"
    assert h.bracket("AAPL").state == "unknown"
    assert [order_id for order_id, _ in h.tws.placed] == [100, 101]  # never resent
    assert h.critical()[0].startswith("AAPL: entry was being sent when the engine stopped and IBKR has no order")


async def test_recorded_restart_mid_send_with_the_created_legs_reported(tmp_path, monkeypatch) -> None:
    h, decision = await crashed_mid_send(tmp_path, monkeypatch, RESTART_MID_SEND_CREATED_LEGS)

    bracket = h.bracket("AAPL")
    assert (await h.engine.ledger.outbox_row(decision)).state == "sent"
    assert (bracket.state, bracket.attention_reason) == ("needs_attention", "legs S not at IBKR after a restart mid-send")
    assert h.tws.cancelled == [100]  # the entry could fill without its protective stop
    assert [order_id for order_id, _ in h.tws.placed] == [100, 101]
