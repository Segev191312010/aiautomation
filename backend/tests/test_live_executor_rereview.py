"""Regression tests for the execution engine's re-review at 3579e92 (READY-FOR-DRILL, R-1 to R-6).

Each test is a reviewer probe with the fixed expectation, named after its
finding; each fails on the behaviour the finding described.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from tests.fakes.engine_harness import CLIENT_ID, Clock, Harness, build, ny, stepping
from tests.test_live_executor_review import HELD, open_bracket, owner_sells, sells


def without_baseline(h: Harness, bracket: Any) -> None:
    """A bracket staged before entry baselines existed: the completion time alone decides."""
    h.engine.entry_baselines.pop(bracket.id)


# ---- R-2: a completed sell's filled quantity of 0 is unknown, never "nothing sold" ---------------------


@pytest.mark.parametrize(("status", "filled", "engine_sells"), [
    ("Filled", 0, []),  # the probe: filledQuantity 0.0 on a Filled order made the engine sell 100 again
    ("Cancelled", 0, []),  # 0 may be an empty field: the whole order may have sold
    ("Cancelled", 40, [(103, "SELL", "MKT", 60)]),  # a reported partial fill counts as reported
])
async def test_r2_a_completed_sell_without_a_fill_quantity_counts_its_whole_size(
    tmp_path, monkeypatch, status: str, filled: int, engine_sells: list[tuple[int, str, str, int]]
) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    sale = owner_sells(h, 100, status=status)
    sale.filled = filled  # what TWS reports as filledQuantity; the position does not show any of it yet
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sells(h) == engine_sells


def test_r2_completed_sell_shares() -> None:
    from decimal import Decimal

    from trading.execution.live_executor import completed_sell_shares

    hundred = Decimal("100")
    assert completed_sell_shares("Filled", hundred, Decimal("0")) == hundred
    assert completed_sell_shares("Filled", hundred, None) == hundred
    assert completed_sell_shares("Filled", hundred, Decimal("40")) == hundred
    assert completed_sell_shares("Cancelled", hundred, Decimal("0")) == hundred
    assert completed_sell_shares("Cancelled", hundred, None) == hundred
    assert completed_sell_shares("Cancelled", hundred, Decimal("40")) == Decimal("40")


# ---- R-3: only other orders since the entry are reserved ---------------------------------------------------


@pytest.mark.parametrize("baseline", [True, False], ids=["baseline", "completion-time"])
async def test_r3_the_engines_own_earlier_bracket_is_never_another_orders_sale(
    tmp_path, monkeypatch, baseline: bool
) -> None:
    h = await build(tmp_path, monkeypatch)
    first = await open_bracket(h)
    h.tws.fill(first.order_id("T"), 100, "51.00")  # bracket A exits at its target
    await h.engine.tick()
    assert first.state == "closed"
    h.clock.advance(30)  # a new HELD setup 30 s later: re-entry in the same symbol
    second = await open_bracket(h)
    if not baseline:
        without_baseline(h, second)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    # The probe counted A's target sell as another order's unshown sale: no flatten, held overnight.
    assert [(oid, kind, qty) for oid, _, kind, qty in sells(h, after=6)] == [(second.order_id("T"), "LMT", 100)]
    assert second.flatten_state == "repricing"


@pytest.mark.parametrize("restart", [False, True], ids=["running", "after-a-restart"])
async def test_r3_an_owner_sale_before_the_entry_with_a_zone_less_time_reserves_nothing(
    tmp_path, monkeypatch, restart: bool
) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.clock.value = ny(9, 41)
    owner_sells(h, 100, status="Filled").completed_at = "20261013 09:41:00"  # no zone: unreadable
    h.clock.value = ny(10, 0, 5)
    await h.engine.start()
    await open_bracket(h)
    if restart:  # the baseline is kept with the bracket in the ledger
        h = await h.restart()
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sells(h) == [(101, "SELL", "LMT", 100)]  # the entry baseline saw it completed before the entry


async def test_r3_without_a_baseline_an_unreadable_time_still_counts_as_after_the_entry(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch, start=False)
    h.clock.value = ny(9, 41)
    owner_sells(h, 100, status="Filled").completed_at = "20261013 09:41:00"
    h.clock.value = ny(10, 0, 5)
    await h.engine.start()
    bracket = await open_bracket(h)
    without_baseline(h, bracket)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.clock.value = ny(15, 45, 1)
    h.events()
    await h.engine.tick()

    # In doubt the shares stay reserved: no sell, the stop stays live, and the owner is told.
    assert sells(h) == [] and bracket.flatten_state == "skipped"
    assert any("sold by other orders that the position does not show yet" in text for text in h.critical())


# ---- R-1: a missing snapshot never ends a flatten for good -------------------------------------------------


async def test_r1_a_flatten_without_a_fresh_snapshot_waits_and_sells_once_tws_answers(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.answer_snapshots = False
    h.clock.value = ny(15, 45, 1)
    h.events()
    for _ in range(3):
        await h.engine.tick()
        h.clock.advance(20)
    assert sells(h) == [] and bracket.flatten_state == "none"  # nothing sent, nothing given up
    assert h.critical().count("HELD: flatten waits for a fresh TWS snapshot — nothing sent; tried again every"
                              " tick until 15:50 ET") == 1

    h.tws.answer_snapshots = True  # TWS answers again at 15:46: the same attempt goes on, once
    await h.engine.tick()
    await h.engine.tick()
    assert sells(h) == [(101, "SELL", "LMT", 100)] and bracket.flatten_state == "repriced"


async def test_r1_a_flatten_start_that_never_gets_a_snapshot_fails_at_close_minus_10_minutes(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.answer_snapshots = False
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    h.clock.value = ny(15, 50)
    h.tws.answer_snapshots = True  # too late: no try after the deadline
    h.events()
    await h.engine.tick()

    assert sells(h) == [] and bracket.flatten_state == "failed"
    assert "HELD: flatten not sent — no fresh TWS snapshot by 15:50 ET; act in TWS" in h.critical()
    for _ in range(3):  # a recorded attempt is never started again [N5]
        h.clock.advance(30)
        await h.engine.tick()
    assert sells(h) == []


async def cancels_confirmed_without_a_snapshot(h: Harness) -> Any:
    """The fallback's cancels are confirmed while TWS stops answering snapshots."""
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])  # the target is gone: the flatten needs the fallback
    h.tws.confirm_cancels = False
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert bracket.flatten_state == "cancelling" and h.tws.cancelled == [102]
    h.tws.confirm_pending_cancels()
    h.tws.answer_snapshots = False
    h.events()
    return bracket


async def test_r1_the_fallback_sell_waits_for_a_fresh_snapshot_and_its_shares_are_flagged_at_once(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await cancels_confirmed_without_a_snapshot(h)
    h.clock.advance(2)
    await h.engine.tick()

    # The probe: 'failed', stop and target cancelled, no sell even after TWS answered again.
    assert sells(h) == [] and bracket.flatten_state == "cancelling"
    assert h.critical() == [
        "HELD: fallback market sell not sent yet (TWS did not report a fresh snapshot) — target and stop are"
        " cancelled; tried again every tick until 16:00 ET; act in TWS if it persists",
        "HELD: 100 sh UNPROTECTED — the flatten fallback cancelled target and stop and no market sell is working;"
        " act in TWS",
    ]
    h.tws.answer_snapshots = True
    h.clock.advance(2)
    await h.engine.tick()
    assert sells(h) == [(103, "SELL", "MKT", 100)]
    await h.engine.tick()
    assert len(sells(h)) == 1


async def test_r1_a_fallback_sell_that_never_gets_a_snapshot_fails_at_the_close(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await cancels_confirmed_without_a_snapshot(h)
    h.clock.value = ny(15, 55)
    h.engine.pause = stepping(h.clock, 30)

    assert await h.engine.finish() == 0

    assert sells(h) == [] and bracket.flatten_state == "failed"
    assert h.clock.value >= ny(16, 0)  # tried until the close, past the 15:58 final alert
    assert ("HELD: fallback market sell not sent by the 16:00 ET close (TWS did not report a fresh snapshot) —"
            " 100 sh without target or stop; act in TWS") in h.critical()


async def test_r1_a_rejected_fallback_sell_leaves_the_shares_flagged_unprotected(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert sells(h) == [(103, "SELL", "MKT", 100)]
    sell = h.tws.orders[(CLIENT_ID, 103)]
    sell.status = "Cancelled"  # TWS rejected the market sell
    h.tws.status_only(sell)
    h.events()
    await h.engine.tick()

    assert ("HELD: 100 sh UNPROTECTED — the flatten fallback cancelled target and stop and no market sell is"
            " working; act in TWS") in h.critical()


# ---- R-4: a restart after the stop time finishes or flags a fallback in flight ---------------------------------


async def fallback_cut_off_before_its_sell(h: Harness) -> Any:
    """Target and stop cancelled by the fallback; the engine stops before its market sell."""
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])

    async def locked(client_id: int) -> int:
        raise sqlite3.OperationalError("database is locked")

    h.engine.ledger.max_order_id = locked  # type: ignore[method-assign]
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert bracket.flatten_state == "cancelling" and h.tws.cancelled == [102] and sells(h) == []
    return bracket


async def test_r4_a_restart_after_the_stop_time_finishes_the_fallback_sell_before_the_close(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    await fallback_cut_off_before_its_sell(h)
    clock = Clock(ny(15, 56))
    restarted = await h.restart(start=False, clock=clock)
    restarted.engine.pause = stepping(clock, 2)
    restarted.events()

    assert await restarted.engine.run() == 0

    assert sells(h) == [(103, "SELL", "MKT", 100)]  # the probe sent nothing and only said "still open"
    assert ("CRITICAL", "HELD: 100 sh UNPROTECTED — the flatten fallback was cancelling target and stop when the"
            " engine stopped and its market sell was never sent; act in TWS") in restarted.events()
    assert clock.value < ny(16, 0)


async def test_r4_after_the_close_a_fallback_in_flight_is_only_flagged(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await fallback_cut_off_before_its_sell(h)
    restarted = await h.restart(start=False, clock=Clock(ny(16, 5)))
    restarted.events()
    requests = len(h.tws.requests)

    assert await restarted.engine.run() == 0

    assert sells(h) == [] and len(h.tws.requests) == requests  # nothing is sent after the close
    assert restarted.critical() == [
        "HELD: 100 sh UNPROTECTED — the flatten fallback was cancelling target and stop when the engine stopped"
        " and its market sell was never sent; act in TWS"
    ]
