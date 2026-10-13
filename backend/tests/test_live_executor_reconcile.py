"""Execution engine reconciliation: at start, on reconnect and every two minutes.

The comparison is scoped to the engine's ``at1:`` orderRefs. A mismatch pauses
new entries and raises CRITICAL; exits are never paused; the owner's orders
and positions are never the engine's business.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from tests.fakes.engine_harness import CLIENT_ID, Clock, Harness, build, ny
from tests.test_live_executor_entries import db_rows, skips


HELD = 300


async def sent_bracket(h: Harness, symbol: str, con_id: int, *, filled: int = 0) -> Any:
    await h.decide(symbol, con_id=con_id)
    await h.engine.tick()
    bracket = h.bracket(symbol)
    if filled:
        h.tws.fill(bracket.order_id("P"), filled, "50.00")
        await h.engine.tick()
    return bracket


def stray(h: Harness, *, order_id: int = 500, ref: str = "at1:0123456789ab:P", client_id: int = CLIENT_ID) -> None:
    h.tws.add_foreign_order(client_id=client_id, order_id=order_id, con_id=7300, symbol="STRAY", action="BUY",
                            quantity=1, order_ref=ref)


async def test_reconciliation_runs_at_start_and_every_two_minutes(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    assert [h.tws.requests.count(name) for name in ("reqAllOpenOrders", "reqExecutions", "reqCompletedOrders")] == [
        1, 1, 1
    ]
    h.clock.advance(119)
    await h.engine.tick()
    assert h.tws.requests.count("reqExecutions") == 1
    h.clock.advance(1)
    await h.engine.tick()
    assert h.tws.requests.count("reqExecutions") == 2
    assert h.tws.requests.count("reqCompletedOrders") == 2


async def test_startup_reconciliation_adopts_brackets_and_flags_what_ibkr_does_not_report(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    ghost = await sent_bracket(h, "GHST", 200)
    await sent_bracket(h, "HELD", HELD, filled=100)
    done = await sent_bracket(h, "DONE", 500, filled=100)
    h.tws.fill(done.order_id("T"), 100, "51.00")
    stop = h.tws.orders[(CLIENT_ID, done.order_id("S"))]
    stop.status = "PreSubmitted"  # OCA never cancelled DONE's stop
    await h.engine.tick()
    await sent_bracket(h, "WORK", 400)  # DONE's slot is free again
    for leg in ghost.legs.values():  # TWS lost every order of GHST
        del h.tws.orders[(CLIENT_ID, leg.order_id)]
    h.tws.add_foreign_order(client_id=CLIENT_ID, order_id=99, con_id=9900, symbol="SYM99", action="BUY",
                            quantity=5)
    h.tws.restart_tws()
    h.events()

    restarted = await h.restart()

    states = {symbol: restarted.bracket(symbol).state for symbol in ("GHST", "HELD", "WORK", "DONE")}
    assert states == {"GHST": "needs_attention", "HELD": "open", "WORK": "working", "DONE": "closed"}
    assert restarted.bracket("GHST").attention_cleared_at is not None  # it never filled: no slot
    assert restarted.critical() == [
        f"DONE: order {done.order_id('S')} is still live at IBKR for a closed bracket — check TWS",
        "GHST: bracket not found at IBKR — check TWS",
        "order 99 (SYM99) is live at IBKR but not in the execution ledger — SYM99 blocked for new entries; check TWS",
        "GHST: entry order 100 is not reported by IBKR — check TWS",
        "GHST: target order 101 is not reported by IBKR — check TWS",
        "GHST: stop order 102 is not reported by IBKR — check TWS",
        "reconciliation mismatch (missing_order) — new entries paused; exits continue",
    ]
    assert {99, *(leg.order_id for leg in ghost.legs.values())} <= restarted.mirror.own_order_ids
    await restarted.decide("NEWX", con_id=4931)
    await restarted.engine.tick()
    assert skips(restarted) == ["NEWX (MOMO_HOD_V2): entries paused by a reconciliation mismatch"]
    assert len(h.tws.placed) == 12  # nothing was re-sent


async def test_an_unknown_engine_order_pauses_entries_with_an_alert_until_it_is_gone(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    stray(h)
    h.clock.advance(121)
    await h.engine.tick()
    assert h.critical() == [
        "STRAY: order 500 tagged at1:0123456789ab:P is working at IBKR but is not a managed engine order — check TWS",
        "reconciliation mismatch (unknown_order) — new entries paused; exits continue",
    ]
    await h.decide("AAPL", con_id=265598)
    await h.engine.tick()
    assert h.tws.placed == []
    assert skips(h) == ["AAPL (MOMO_HOD_V2): entries paused by a reconciliation mismatch"]

    h.tws.cancelOrder(500)  # the owner cancels it in TWS
    h.clock.advance(121)
    await h.engine.tick()
    assert ("INFO", "reconciliation clean — new entries resumed") in h.events()
    await h.decide("TSLA", con_id=76792991)
    await h.engine.tick()
    assert len(h.tws.placed) == 3
    kinds = [row[0] for row in db_rows("SELECT kind FROM autotrader_exec_events ORDER BY seq")]
    assert {"reconcile_mismatch", "entries_paused", "entries_resumed"} <= set(kinds)


async def test_the_owners_orders_and_holdings_are_outside_the_reconciliation(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.tws.add_foreign_order(client_id=0, order_id=0, con_id=265598, symbol="AAPL", action="SELL", quantity=5)
    h.tws.add_foreign_order(client_id=CLIENT_ID, order_id=60, con_id=76792991, symbol="TSLA", action="BUY",
                            quantity=5, order_ref="manual:" + "c" * 32)
    h.tws.add_foreign_order(client_id=5, order_id=1, con_id=4815747, symbol="NVDA", action="BUY", quantity=5,
                            order_ref="other-app")
    h.tws.positions[265598] = Decimal("300")  # the owner's long
    await h.engine.start()
    h.clock.advance(121)
    await h.engine.tick()

    assert not h.engine.findings and h.critical() == []


async def test_a_position_mismatch_pauses_entries_after_the_grace(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await sent_bracket(h, "HELD", HELD, filled=100)
    h.tws.move_position(HELD, -40)  # someone sold 40 of the bot's shares in TWS
    h.clock.advance(121)
    await h.engine.tick()
    assert not h.engine.findings  # first sighting: a position update may trail its fill
    h.clock.advance(121)
    await h.engine.tick()

    assert [finding.code for finding in h.engine.findings.values()] == ["position_mismatch"]
    assert "HELD: account holds 60 sh, the execution ledger expects 100 — check TWS" in h.critical()


async def test_an_engine_execution_the_ledger_cannot_attribute_pauses_entries(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    h = await build(tmp_path, monkeypatch)
    h.mirror.execDetails(-1, SimpleNamespace(conId=7300, symbol="STRAY"), SimpleNamespace(
        execId="0000e0d5.deadbeef.01.01", orderId=501, shares=5.0, price=10.0, clientId=CLIENT_ID,
        acctNumber="DU1234567", orderRef="at1:0123456789ab:P", time="20261013  10:00:05", side="BOT", permId=7,
    ))
    h.clock.advance(121)
    await h.engine.tick()

    assert [finding.code for finding in h.engine.findings.values()] == ["unknown_execution"]


async def test_a_snapshot_tws_does_not_answer_pauses_entries_but_not_exits(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await sent_bracket(h, "WORK", 400)
    h.tws.answer_snapshots = False
    h.clock.advance(11 * 60)  # also past the entry's 10-minute TTL
    await h.engine.tick()

    assert [finding.code for finding in h.engine.findings.values()] == ["snapshot_unavailable"]
    assert h.tws.cancelled == [100]  # the stale entry was still cancelled
    h.tws.answer_snapshots = True
    h.clock.advance(121)
    await h.engine.tick()
    assert not h.engine.findings


async def test_a_stale_engine_order_of_an_earlier_session_is_a_mismatch(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, clock=Clock(ny(10, 0, 5, day=date(2026, 10, 12))),
                    session_date=date(2026, 10, 12))
    done = await sent_bracket(h, "DONE", 500, filled=100)
    h.tws.fill(done.order_id("T"), 100, "51.00")
    await h.engine.tick()
    stop = h.tws.orders[(CLIENT_ID, done.order_id("S"))]
    stop.status = "PreSubmitted"  # yesterday's GTC stop is still live today
    next_day = await build_next_session(h, date(2026, 10, 13))

    assert [finding.code for finding in next_day.engine.findings.values()] == ["stale_order"]
    assert next_day.critical()[0].startswith(f"DONE: order {done.order_id('S')} tagged at1:{done.id}:S")


async def build_next_session(h: Harness, session_date: date) -> Harness:
    from tests.fakes.decision_ledger import DecisionSeeder
    from tests.fakes.engine_harness import make_engine

    clock = Clock(ny(9, 30, 5, day=session_date))
    engine = make_engine(h.tws, clock, h.lines, session_date=session_date)
    h.tws.attach(engine.mirror)
    h.tws.restart_tws()
    h.tws.connect()
    h.lines.clear()
    harness = Harness(engine=engine, tws=h.tws, clock=clock, seeder=DecisionSeeder(session_date), lines=h.lines,
                      session_date=session_date)
    await engine.start()
    return harness


# ---- a restart in the middle of sending a bracket -------------------------------------------------


async def test_a_bracket_ibkr_received_before_a_crash_is_adopted_and_never_resent(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    decision = await h.decide("AAPL", con_id=265598)

    async def crash(*args: Any, **kwargs: Any) -> None:
        raise SystemExit("killed after placeOrder, before the outbox row was closed")

    h.engine.ledger.mark_sent = crash  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        await h.engine.tick()
    assert h.placed_ids() == [100, 101, 102]
    assert db_rows("SELECT state FROM autotrader_exec_outbox") == [("sending",)]

    restarted = await h.restart()
    for _ in range(2):
        await restarted.engine.tick()

    assert h.placed_ids() == [100, 101, 102]
    assert (await restarted.engine.ledger.outbox_row(decision)).state == "sent"
    assert restarted.bracket("AAPL").state == "working"
    assert db_rows("SELECT state FROM autotrader_exec_risk_reservations") == [("consumed",)]


async def test_a_bracket_cut_off_mid_send_has_its_unprotected_parent_cancelled(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("AAPL", con_id=265598)
    h.tws.fail_place_after = 2  # the socket dies before the stop (the only transmitting leg) is sent
    await h.engine.tick()
    assert h.placed_ids() == [100, 101]
    assert h.critical()[0].startswith("AAPL: placing the bracket failed (OSError: socket closed while sending)")
    assert [finding.code for finding in h.engine.findings.values()] == ["send_failed"]

    h.tws.fail_place_after = None
    h.tws.report_untransmitted = True  # assumption: TWS reports the created, untransmitted legs
    restarted = await h.restart()

    bracket = restarted.bracket("AAPL")
    assert (bracket.state, bracket.attention_reason) == ("needs_attention", "legs S not at IBKR after a restart mid-send")
    assert h.tws.cancelled == [100]  # the entry could have filled without its stop
    assert h.placed_ids() == [100, 101]  # never re-sent


async def test_a_bracket_ibkr_never_saw_is_unknown_and_never_resent(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    decision = await h.decide("AAPL", con_id=265598)
    h.tws.fail_place_after = 0
    await h.engine.tick()
    assert h.tws.placed == []
    assert h.critical()[0].startswith("AAPL: placing the bracket failed")

    h.tws.fail_place_after = None
    restarted = await h.restart()
    await restarted.decide("AAPL", con_id=265598)
    await restarted.engine.tick()

    assert h.tws.placed == []  # never resent, and its symbol stays blocked for the session
    assert (await restarted.engine.ledger.outbox_row(decision)).state == "unknown"
    assert restarted.bracket("AAPL").state == "unknown"
    assert db_rows("SELECT state FROM autotrader_exec_risk_reservations") == [("released",)]
    assert db_rows("SELECT DISTINCT state FROM autotrader_exec_legs") == [("unknown",)]
    criticals = restarted.critical()
    assert criticals[0] == ("AAPL: entry was being sent when the engine stopped and IBKR has no order for it —"
                            " marked unknown and never resent; check TWS")
    restarted.clock.advance(121)
    await restarted.engine.tick()
    assert not restarted.engine.findings  # nothing left at IBKR: entries resume after one clean pass
