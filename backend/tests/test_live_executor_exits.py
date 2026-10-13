"""Execution engine exits: fills, exit sizing, modifies, the flatten and its fallback.

Every test named after a bridge review finding (H1, H2, M1, M2, N2-N7, L1,
L3, L5, L8) ports that lesson from scripts_local/paper_bridge.py and must
keep failing on the behaviour the finding described.
"""
from __future__ import annotations

import copy
from datetime import date, datetime
from decimal import Decimal
import itertools
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fakes.engine_harness import ACCOUNT, CLIENT_ID, Clock, Harness, build, ny, stepping
from trading.execution.live_executor import (
    EXIT_OK,
    LATE_FLATTEN_DISCOUNT,
    FillSnapshot,
    leg_ticket,
    plan_exit,
    stock,
)


HELD = 300


async def open_bracket(h: Harness, symbol: str = "HELD", con_id: int = HELD, *, quantity: int = 100,
                       filled: int | None = None, price: str = "50.00", **intent: Any) -> Any:
    """A bracket sent through the engine whose entry filled (all of it unless ``filled``)."""
    await h.decide(symbol, con_id=con_id, quantity=quantity, **intent)
    await h.engine.tick()
    bracket = h.bracket(symbol)
    h.tws.fill(bracket.order_id("P"), quantity if filled is None else filled, price)
    await h.engine.tick()
    h.events()
    return bracket


def modifies(h: Harness, *, after: int = 3) -> list[tuple[int, Any]]:
    return h.tws.placed[after:]


def snap(parent: int, target: int, stop: int, recorded: int, position: int | None) -> FillSnapshot:
    return FillSnapshot(parent=parent, target=target, stop=stop, recorded_exits=recorded,
                        position=None if position is None else Decimal(position))


# ---- the one-snapshot exit rule (pure) -----------------------------------------------------


@pytest.mark.parametrize(("snapshot", "leg", "expected"), [
    # A leg's own fills never feed its total: IBKR keeps 60 working for the 60 still held.
    (snap(100, 40, 0, 40, 60), "T", (100, 60, None, "")),
    (snap(100, 40, 0, 40, 60), "S", (60, 60, None, "")),
    # H1: 30 more target fills landed after the refresh; v3 of the bridge sent 70 + the stale 60 = 130.
    (snap(100, 70, 0, 40, 30), "T", (100, 30, None, "")),
    # Exits the snapshot cannot attribute (executions missing after a restart) shrink both legs.
    (snap(100, 0, 0, 40, 60), "T", (60, 60, None, "")),
    (snap(100, 0, 0, 40, 60), "S", (60, 60, None, "")),
    # H2: the owner sold 30 by hand in TWS; a flat or short account gets nothing.
    (snap(100, 40, 0, 40, 30), "T", (70, 30, 60, "")),
    (snap(100, 40, 0, 40, 0), "T", (40, 0, 60, "")),
    (snap(100, 40, 0, 40, -5), "S", (0, 0, 60, "")),
    (snap(60, 40, 30, 40, 20), "T", (0, 0, None, "exit fills 70 exceed entry fills 60")),
    (snap(100, 40, 0, 40, None), "T", (0, 0, None, "account position unknown")),
])
def test_exit_totals_follow_the_one_snapshot_rule(snapshot: FillSnapshot, leg: str, expected: tuple[Any, ...]) -> None:
    plan = plan_exit(snapshot, leg)  # type: ignore[arg-type]
    assert (plan.total, plan.working, plan.capped_from, plan.problem) == expected


def test_no_snapshot_yields_an_exit_above_entry_fills_or_the_account_position() -> None:
    values = range(5)
    for parent, target, stop, recorded in itertools.product(values, repeat=4):
        for position in (None, -1, *values):
            for leg in ("T", "S"):
                plan = plan_exit(snap(parent, target, stop, recorded, position), leg)  # type: ignore[arg-type]
                own, other = (target, stop) if leg == "T" else (stop, target)
                if max(recorded, target + stop) > parent or position is None:
                    assert plan.problem and plan.working == 0
                    continue
                assert not plan.problem
                assert own <= plan.total <= parent - other
                assert plan.working == plan.total - own <= max(0, position)


# ---- fills -----------------------------------------------------------------------------


async def test_fills_are_deduplicated_by_exec_id_across_replays_and_restarts(tmp_path, monkeypatch) -> None:
    from tests.test_live_executor_entries import db_rows

    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    for _ in range(2):  # every reconciliation replays the day's executions
        h.clock.advance(121)
        await h.engine.tick()
    assert db_rows("SELECT COUNT(*), SUM(shares) FROM autotrader_exec_fills") == [(1, 100)]
    assert (bracket.entry_filled, bracket.avg_entry) == (100, Decimal("50.0000"))

    restarted = await h.restart()
    again = restarted.bracket("HELD")
    await restarted.engine.tick()
    assert (again.entry_filled, again.avg_entry, again.state) == (100, Decimal("50.0000"), "open")
    assert db_rows("SELECT COUNT(*) FROM autotrader_exec_fills") == [(1,)]


async def test_fills_never_decrease_when_executions_go_missing_after_a_tws_restart(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h, filled=60)
    h.tws.executions.clear()  # TWS restarted and forgot the day's executions
    h.tws.restart_tws()
    restarted = await h.restart()
    await restarted.engine.tick()

    assert restarted.bracket("HELD").entry_filled == 60 == bracket.entry_filled


async def test_l5_fills_are_scoped_to_the_session_account_and_engine_orders(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("HELD", con_id=HELD)
    await h.engine.tick()
    bracket = h.bracket("HELD")
    ref = f"at1:{bracket.id}:P"
    today, yesterday = "20261013  10:00:05", "20261012  15:59:00"
    for exec_id, shares, account, order_ref, when, client in (
        ("y.01", 50, ACCOUNT, "", yesterday, CLIENT_ID),  # 9.81 replays 24 h: yesterday's order 100 (reset ids)
        ("o.01", 50, "DU999999", ref, today, CLIENT_ID),  # another account
        ("m.01", 50, ACCOUNT, "manual:" + "b" * 32, today, CLIENT_ID),  # an owner order on the engine's client
        ("c.01", 50, ACCOUNT, ref, today, 0),  # the owner's TWS trade
        ("t.01", 60, ACCOUNT, ref, today, CLIENT_ID),  # the engine's own fill
    ):
        h.mirror.execDetails(-1, SimpleNamespace(conId=HELD, symbol="HELD"), SimpleNamespace(
            execId=exec_id, orderId=100, shares=float(shares), price=50.0, clientId=client, acctNumber=account,
            orderRef=order_ref, time=when, side="BOT", permId=1,
        ))
    await h.engine.tick()

    assert (bracket.entry_filled, bracket.avg_entry, bracket.state) == (60, Decimal("50.0000"), "open")


async def test_l5_a_corrected_execution_replaces_the_original_and_overfills_are_capped(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("HELD", con_id=HELD)
    await h.engine.tick()
    bracket = h.bracket("HELD")
    ref = f"at1:{bracket.id}:P"

    def execution(exec_id: str, shares: int) -> None:
        h.mirror.execDetails(-1, SimpleNamespace(conId=HELD), SimpleNamespace(
            execId=exec_id, orderId=100, shares=float(shares), price=50.0, clientId=CLIENT_ID, acctNumber=ACCOUNT,
            orderRef=ref, time="20261013  10:00:05", side="BOT", permId=1,
        ))

    h.events()
    execution("0000e0d5.6576fd38.01.01", 60)
    execution("0000e0d5.6576fd38.01.02", 40)  # IBKR's correction
    execution("0000e0d5.6576fd38.01.01", 60)  # the original, replayed
    await h.engine.tick()
    assert bracket.entry_filled == 40

    execution("0000e0d5.6576fd39.01.01", 100)
    await h.engine.tick()
    await h.engine.tick()
    assert bracket.entry_filled == 100  # never more than the order's 100
    out = h.events()
    assert ("INFO", "execution 0000e0d5.6576fd38.01.02 corrects order 100: 60 -> 40 sh") in out
    assert [text for kind, text in out if kind == "CRITICAL"] == [
        "HELD: executions for order 100 add up to 140 sh, more than its 100 — counted as 100; check TWS"
    ]


async def test_commission_reports_count_even_when_they_arrive_after_the_fill(tmp_path, monkeypatch) -> None:
    from tests.test_live_executor_entries import db_rows

    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    execution = h.tws.fill(101, 100, "51.00", commission=None)
    await h.engine.tick()
    assert bracket.realized_pnl == Decimal("99.00")  # the entry's 1.00 is known, the exit's not yet

    late = SimpleNamespace(execId=execution.execId, commission=1.25, currency="USD", realizedPNL=1.7976931348623157e308)
    h.mirror.commissionReport(late)
    unset = SimpleNamespace(execId="unrelated.01", commission=1.7976931348623157e308, currency="USD")
    h.mirror.commissionReport(unset)  # 9.81's unset double is no commission at all
    h.clock.advance(1)
    await h.engine.tick()

    assert db_rows("SELECT commission FROM autotrader_exec_fills WHERE exec_id=?", (execution.execId,)) == [("1.25",)]
    assert bracket.commissions == Decimal("2.25")
    assert bracket.realized_pnl == Decimal("97.75")  # the late cost reaches the daily budget too
    assert "unrelated.01" not in h.mirror.commissions


# ---- H1 / H2: exits never exceed fills or the account position --------------------------------


@pytest.mark.parametrize("position_update", [True, False], ids=["position-updated", "position-stale"])
async def test_h1_flatten_quantity_comes_from_one_snapshot_taken_after_the_quote_wait(
    tmp_path, monkeypatch, position_update: bool
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.fill(101, 40, "50.50")
    await h.engine.tick()
    assert (bracket.exit_filled, bracket.remaining) == (40, 60)
    h.clock.value = ny(15, 45, 1)

    def quote_while_the_target_fills(request_id: int, contract: Any, *args: Any) -> None:
        h.tws.position_subscribed = position_update
        h.tws.fill(101, 30, "49.90")  # lands during the quote wait
        h.tws.position_subscribed = True
        h.mirror.tickPrice(request_id, 1, 49.80, None)

    h.tws.reqMktData = quote_while_the_target_fills  # type: ignore[method-assign]
    await h.engine.flatten()

    # The bridge's v3 sent 130 (70 target fills + the stale 60): IBKR would have sold 60 of the 30 held.
    [(order_id, sent)] = modifies(h)
    assert (order_id, sent.totalQuantity, sent.transmit) == (101, 100, True)
    assert sent.lmtPrice == pytest.approx(49.55)  # 49.80 x 0.995
    h.tws.fill(101, 30, "49.60")  # IBKR sells exactly the 30 held
    await h.engine.tick()
    assert (bracket.state, bracket.exit_filled, bracket.net_held) == ("closed", 100, 0)
    h.engine.verify_flat()
    out = h.events()
    assert ("FLATTEN", "HELD: target repriced to 49.55 for 30 sh (bid 49.8)") in out
    assert ("FLAT", "no open engine positions") in out
    assert [text for kind, text in out if kind == "CRITICAL"] == []


async def test_h1_exit_fills_above_entry_fills_block_every_exit_and_are_never_reported_flat(
    tmp_path, monkeypatch
) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h, filled=60)
    h.tws.fill(101, 40, "50.50")
    await h.engine.tick()
    h.clock.value = ny(15, 45, 1)

    def quote_while_the_stop_fills(request_id: int, contract: Any, *args: Any) -> None:
        h.tws.fill(102, 30, "49.00")
        h.mirror.tickPrice(request_id, 1, 50.00, None)

    h.tws.reqMktData = quote_while_the_stop_fills  # type: ignore[method-assign]
    h.events()
    await h.engine.flatten()
    assert modifies(h) == []  # no reprice and no fallback sell: the account may already be short

    await h.engine.tick()
    assert (bracket.state, bracket.net_held, bracket.flatten_state) == ("needs_attention", -10, "failed")
    h.engine.verify_flat()
    criticals = [text for kind, text in h.events() if kind == "CRITICAL"]
    assert criticals[:2] == [
        "HELD: flatten of target 101 not sent — exit fills 70 exceed entry fills 60; act in TWS",
        "HELD: exits 70 sh exceed entry fills 60 sh — the account may be short 10 sh; act in TWS",
    ]
    assert criticals[-1] == "HELD: exits exceed entry fills by 10 sh — the account may be short; act in TWS"
    assert modifies(h) == []


@pytest.mark.parametrize("owner_sold", [70, 100])
async def test_h2_exits_never_exceed_the_account_position_and_1550_only_alerts(
    tmp_path, monkeypatch, owner_sold: int
) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    left = 100 - owner_sold
    # Before 15:45 the owner sold by hand in TWS (client 0): not the engine's fill, but the account's shares.
    h.tws.add_foreign_order(client_id=0, order_id=7, con_id=HELD, symbol="HELD", action="SELL", quantity=owner_sold)
    h.tws.fill(7, owner_sold, "50.10", client_id=0)
    h.tws.quotes[HELD] = {"bid": 50.20}
    clock = Clock(ny(15, 44, 30))
    restarted = await h.restart(start=False, clock=clock)
    restarted.engine.pause = stepping(clock, 30)
    restarted.events()

    assert await restarted.engine.run() == EXIT_OK

    reprices = modifies(h)
    if left:
        # One reprice at 15:45, limited to the account's shares; the bridge's v3 sold 100 and repriced again.
        [(order_id, sent)] = reprices
        assert (order_id, sent.totalQuantity) == (101, left)
        outcome = f"limited to {left} sh"
    else:
        assert reprices == []
        outcome = "not sent"
    assert restarted.critical() == [
        f"HELD: account holds {left} sh but the bracket expects 100 — flatten of target 101 {outcome}; act in TWS",
        f"HELD: account holds {left} sh but the bracket expects 100 — its exit orders could sell shares the"
        " account no longer holds; check TWS",
        "HELD: still open after flatten (100 sh) — act in TWS",  # 15:46
        f"HELD: account holds {left} sh, the execution ledger expects 100 — check TWS",  # the 2-minute reconciliation
        "reconciliation mismatch (position_mismatch) — new entries paused; exits continue",
        "HELD: still open after flatten (100 sh) — act in TWS",  # 15:50: alert only
        "HELD: still open after flatten (100 sh) — act in TWS",  # 15:58
    ]
    assert h.tws.cancelled == []  # never a fallback sell next to the owner's manual close
    assert clock.value >= ny(15, 58)


async def test_h2_a_bracket_is_repriced_at_most_once_whoever_asks(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.clock.value = ny(15, 45, 1)

    assert await h.engine.flatten_bracket(bracket) is True
    assert await h.engine.flatten_bracket(bracket) is False  # e.g. a later checkpoint or an owner request
    await h.engine.flatten(LATE_FLATTEN_DISCOUNT)
    assert [order_id for order_id, _ in modifies(h)] == [101]


async def test_h2_an_account_holding_less_than_its_bracket_is_critical_after_a_grace(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    h.tws.position_subscribed = False  # the entry fills; its position update trails
    await open_bracket(h)
    h.clock.advance(10)
    await h.engine.tick()
    h.tws.position_subscribed = True
    h.tws.move_position(HELD, 0)  # the update arrives
    await h.engine.tick()
    assert h.critical() == []  # a position update that trails its fill is not an alarm

    h.tws.move_position(HELD, -100)  # the owner sells all 100 in TWS
    for _ in range(3):
        await h.engine.tick()
        h.clock.advance(10)
    assert h.critical()[0] == (
        "HELD: account holds 0 sh but the bracket expects 100 — its exit orders could sell shares the account"
        " no longer holds; check TWS"
    )


# ---- M1: a failing step never blocks order management --------------------------------------------


async def test_m1_a_failing_decision_intake_never_blocks_the_flatten(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.00}
    h.clock.value = ny(15, 45, 5)
    reads: list[int] = []

    async def locked_ledger() -> list[Any]:
        reads.append(1)
        if len(reads) <= 2:
            raise sqlite3.OperationalError("database is locked")
        return []

    h.engine.decisions = locked_ledger
    for _ in range(3):
        await h.engine.tick()

    # The bridge's v3 raised out of the tick before the flatten and never flattened.
    assert [order_id for order_id, _ in modifies(h)] == [101]
    out = h.events()
    assert ("FLATTEN", "HELD: target repriced to 49.75 for 100 sh (bid 50.0)") in out
    assert [text for kind, text in out if kind == "CRITICAL"] == [
        "decision step failed (OperationalError: database is locked) — no new entries from it until it"
        " recovers; open brackets are still managed"
    ]
    assert ("INFO", "decision step recovered after 2 failed attempts") in out


async def test_m1_a_failing_ledger_write_never_blocks_the_flatten(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.00}
    h.clock.value = ny(15, 45, 5)

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    h.engine.ledger.save_bracket = broken  # type: ignore[method-assign]
    h.engine.ledger.append_event = broken  # type: ignore[method-assign]
    await h.engine.tick()

    assert [order_id for order_id, _ in modifies(h)] == [101]
    assert bracket.flatten_state == "repricing"
    assert any("bracket state not saved to the execution ledger" in text for text in h.critical())


# ---- M2 / N7: a dead, partly filled entry resizes its exits ---------------------------------------


async def dead_partial_entry(h: Harness, *, filled: int = 40, position: int | None = None) -> Any:
    bracket = await open_bracket(h, filled=filled)
    h.tws.cancelOrder(100)  # the entry is cancelled with 40 of 100 filled; the children stay
    if position is not None:
        h.tws.move_position(HELD, position - filled)
    return bracket


async def test_m2_resize_uses_the_dead_parents_fresh_fill_count(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h, filled=40)
    h.tws.move_position(HELD, 10)  # 10 more shares filled; only the status report knows them yet
    h.mirror.orderStatus(100, "Cancelled", 50.0, 0.0, 50.0, 1, 0, 0.0, CLIENT_ID, "", 0.0)
    h.events()
    h.engine.resize_children(bracket)

    # The bridge's v3 sized both legs to the stale 40 and never looked again.
    assert [(oid, sent.orderType, sent.totalQuantity) for oid, sent in modifies(h)] == [(101, "LMT", 50),
                                                                                        (102, "STP", 50)]
    assert bracket.resized_qty == 50
    assert [text for kind, text in h.events() if kind == "RESIZE"] == ["HELD: exits set to the 50 filled shares"]


async def test_m2_a_resize_is_redone_when_the_fill_count_or_the_position_changes(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await dead_partial_entry(h)
    await h.engine.tick()
    assert [(oid, sent.totalQuantity) for oid, sent in modifies(h)] == [(101, 40), (102, 40)]
    assert bracket.resized_qty == 40

    # A late status report: the dead parent had filled 50, but the account still shows 40.
    h.mirror.orderStatus(100, "Cancelled", 50.0, 0.0, 50.0, 1, 0, 0.0, CLIENT_ID, "", 0.0)
    await h.engine.tick()
    assert len(modifies(h)) == 2 and bracket.resized_qty == 40  # capped totals equal the last ones sent

    h.tws.move_position(HELD, 10)  # the position catches up
    await h.engine.tick()
    assert [(oid, sent.totalQuantity) for oid, sent in modifies(h)][2:] == [(101, 50), (102, 50)]
    assert bracket.resized_qty == 50
    await h.engine.tick()
    assert len(modifies(h)) == 4
    out = h.events()
    assert [text for kind, text in out if kind == "RESIZE"] == [
        "HELD: exits set to the 40 filled shares", "HELD: exits set to the 50 filled shares",
    ]
    assert [text for kind, text in out if kind == "CRITICAL"] == []  # N4: within the grace, a notice only


async def test_m2_dead_exit_legs_are_critical_and_never_reported_as_resized(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h, filled=40)
    h.tws.confirm_cancels = True
    for order_id in (101, 102, 100):
        h.tws._cancel(h.tws.orders[(CLIENT_ID, order_id)])
    h.events()
    await h.engine.tick()
    await h.engine.tick()

    assert modifies(h) == [] and bracket.state == "open"
    out = h.events()
    assert not [text for kind, text in out if kind == "RESIZE"]  # v3 logged RESIZE for two dead legs
    assert [text for kind, text in out if kind == "CRITICAL"] == [
        "HELD: target order 101 is not live (Cancelled) — 40 sh without a target; act in TWS",
        "HELD: stop order 102 is not live (Cancelled) — 40 sh unprotected; act in TWS",
    ]


async def test_m2_an_open_position_without_a_live_stop_is_critical_once(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 102)])  # the stop vanished (cancelled by hand in TWS)
    h.events()
    for _ in range(3):
        await h.engine.tick()

    assert h.critical() == ["HELD: stop order 102 is not live (Cancelled) — 100 sh unprotected; act in TWS"]


async def test_n7_a_cancelled_parent_tws_no_longer_reports_counts_as_dead(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch, clock=Clock(ny(10, 0, 5)))
    bracket = await open_bracket(h, filled=40)
    h.clock.advance(11 * 60)
    await h.engine.tick()  # the 10-minute TTL cancels the partly filled entry
    assert bracket.parent_cancel_requested
    del h.tws.orders[(CLIENT_ID, 100)]  # a restarted TWS reports neither the parent nor its completion
    h.tws.modify_mode = "silent"
    restarted = await h.restart()
    h.tws.modify_mode = "accept"
    restarted.engine.pending_modifies.clear()
    await restarted.engine.tick()

    assert [(oid, sent.totalQuantity) for oid, sent in h.tws.placed[3:]] == [(101, 40), (102, 40)]
    assert restarted.bracket("HELD").resized_qty == 40


# ---- N2 / L1 / L8: modifies count only once echoed; TWS errors by severity -------------------------


async def test_n2_every_error_on_an_own_order_is_critical_unless_informational(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    rejections = (201, 320, 321, 10268, 10269, 10270, 434, 383, 163, 136, 10318, 110, 2161)
    notices = (202, 399, 2109, 2148, 10311)
    for code in rejections + notices:
        h.mirror.error(101, code, f"text {code}")
    h.mirror.error(77, 321, "not one of ours")

    assert h.events() == (
        [("CRITICAL", f"TWS 101 {code} text {code}") for code in rejections]
        + [("INFO", f"TWS 101 {code} text {code}") for code in notices]
        + [("INFO", "TWS 77 321 not one of ours")]
    )


async def test_l1_rejections_of_restored_legs_are_critical(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    restarted = await h.restart()  # a restart: the legs come from the ledger
    restarted.events()
    for order_id, code, message in (
        (101, 201, "Order rejected - reason: test"),
        (101, 10006, "Missing parent order"),
        (100, 10147, "OrderId 100 that needs to be cancelled is not found."),
        (102, 161, "Cancel attempted when order is not in a cancellable state."),
        (77, 201, "Order rejected - reason: not ours"),
    ):
        restarted.mirror.error(order_id, code, message)

    assert restarted.events() == [
        ("CRITICAL", "TWS 101 201 Order rejected - reason: test"),
        ("CRITICAL", "TWS 101 10006 Missing parent order"),
        ("CRITICAL", "TWS 100 10147 OrderId 100 that needs to be cancelled is not found."),
        ("CRITICAL", "TWS 102 161 Cancel attempted when order is not in a cancellable state."),
        ("INFO", "TWS 77 201 Order rejected - reason: not ours"),
    ]


async def test_l8_tws_codes_are_logged_by_severity(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    h.events()
    for code, message in (
        (2110, "Connectivity between Trader Workstation and server is broken."),
        (2103, "Market data farm connection is broken:usfarm"),
        (2105, "HMDS data farm connection is broken:ushmds"),
        (2157, "Sec-def data farm connection is broken:secdefil"),
        (2104, "Market data farm connection is OK:usfarm"),
        (2106, "HMDS data farm connection is OK:ushmds"),
        (1100, "Connectivity between IB and Trader Workstation has been lost."),
    ):
        h.mirror.error(-1, code, message)

    assert h.events() == [
        ("CRITICAL", "TWS -1 2110 Connectivity between Trader Workstation and server is broken."),
        ("INFO", "TWS -1 2103 Market data farm connection is broken:usfarm"),
        ("INFO", "TWS -1 2105 HMDS data farm connection is broken:ushmds"),
        ("INFO", "TWS -1 2157 Sec-def data farm connection is broken:secdefil"),
        ("CRITICAL", "TWS -1 1100 Connectivity between IB and Trader Workstation has been lost."),
    ]


async def test_n2_a_resize_counts_as_done_only_once_tws_echoes_it(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await dead_partial_entry(h)
    h.tws.modify_mode = "silent"  # TWS has not echoed yet
    await h.engine.tick()
    assert [(oid, sent.totalQuantity) for oid, sent in modifies(h)] == [(101, 40), (102, 40)]
    assert bracket.resized_qty == 0  # sent, not yet confirmed

    h.tws.modify_mode = "accept"
    for order_id, sent in list(modifies(h)):  # the openOrder echo arrives after the tick
        h.tws.modify(h.tws.orders[(CLIENT_ID, order_id)], sent)
    h.clock.advance(2)
    await h.engine.tick()
    assert len(modifies(h)) == 2  # echoed: never sent again
    assert bracket.resized_qty == 40
    assert h.critical() == []


@pytest.mark.parametrize("mode", ["reject", "echo_then_reject"])
async def test_n2_a_rejected_resize_is_resent_three_times_then_critical(tmp_path, monkeypatch, mode: str) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await dead_partial_entry(h)
    h.tws.modify_mode = mode
    for _ in range(10):
        await h.engine.tick()
        h.clock.advance(2)

    # The bridge's v4 logged the 321 as INFO, marked the resize done and never looked again.
    assert [order_id for order_id, _ in modifies(h)] == [101, 102] * 4
    assert bracket.resized_qty == 0
    out = h.critical()
    assert out.count("TWS 101 321 Error validating request") == 4
    assert out[-2:] == [
        "HELD: TWS did not confirm the resize of target 101 (40 sh @ 51.00) after 4 attempts — act in TWS",
        "HELD: TWS did not confirm the resize of stop 102 (40 sh @ 49.50) after 4 attempts — act in TWS",
    ]


async def test_n2_a_live_exit_leg_on_a_finished_bracket_is_critical_once(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws.fill(101, 100, "51.00")
    stop = h.tws.orders[(CLIENT_ID, 102)]
    stop.status = "PreSubmitted"  # OCA never cancelled the stop
    h.tws.status_only(stop)
    h.events()
    await h.engine.tick()
    h.clock.advance(10)
    await h.engine.tick()
    assert h.critical() == []  # an OCA cancel may trail the fill by a moment

    h.clock.advance(10)
    await h.engine.tick()
    await h.decide("HELD", con_id=HELD)
    await h.engine.tick()
    out = h.events()
    assert [text for kind, text in out if kind == "CRITICAL"] == [
        "HELD: order 102 is still live at IBKR for a closed bracket — check TWS"
    ]
    assert ("SKIP", "HELD (MOMO_HOD_V2): symbol has a live order at IBKR the engine does not manage") in out


# ---- N3: a modify is encoded exactly like the placement TWS accepted ---------------------------------


def encode_place_order(order_id: int, contract: Any, order: Any) -> list[str]:
    """The fields the real ibapi 9.81 placeOrder writes to the TWS socket."""
    from ibapi.client import EClient
    from ibapi.server_versions import MAX_CLIENT_VER
    from ibapi.wrapper import EWrapper

    class Socket:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def isConnected(self) -> bool:
            return True

        def sendMsg(self, message: bytes) -> int:
            self.sent.append(message)
            return len(message)

    client = EClient(EWrapper())
    client.conn = Socket()
    client.serverVersion_ = MAX_CLIENT_VER  # what 9.81 negotiates with any current TWS
    client.connState = EClient.CONNECTED
    client.placeOrder(order_id, contract, order)
    assert client.conn.sent, "the 9.81 encoder refused the order"
    return client.conn.sent[-1][4:].decode().split("\0")[:-1]


@pytest.mark.parametrize("code", ["T", "S"])
async def test_n3_modifies_encode_exactly_like_the_placement_tws_accepted(tmp_path, monkeypatch, code: str) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    leg = bracket.legs[code]
    placement = leg_ticket(leg, ACCOUNT)  # exactly what the engine sent at entry
    assert (h.tws.placed[1 if code == "T" else 2][1].orderRef, placement.orderRef) == (leg.order_ref,) * 2
    contract = stock(HELD, "NASDAQ")

    modified = h.engine.exit_order(bracket, code, total=60, price=Decimal("49.94"))

    reported = h.mirror.order_objects[leg.order_id]  # the fake echoes 9.81 decoder quirks (eTradeOnly, ...)
    assert reported.eTradeOnly is True and reported.ocaType == 3
    expected = copy.copy(placement)
    expected.totalQuantity, expected.ocaGroup, expected.ocaType, expected.transmit = 60, reported.ocaGroup, 3, True
    setattr(expected, "lmtPrice" if code == "T" else "auxPrice", 49.94)
    assert encode_place_order(leg.order_id, contract, modified) == encode_place_order(leg.order_id, contract, expected)
    assert len(encode_place_order(leg.order_id, contract, modified)) == len(
        encode_place_order(leg.order_id, contract, placement)
    )
    assert modified.eTradeOnly is False and reported.lmtPrice != 49.94  # the cache is never mutated


async def test_a_modification_never_rewrites_an_order_of_another_shape(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    reshaped = copy.copy(h.mirror.order_objects[101])
    reshaped.action, reshaped.orderType = "BUY", "MKT"  # e.g. changed by hand in TWS
    h.mirror.order_objects[101] = reshaped
    h.clock.value = ny(15, 45, 1)
    await h.engine.flatten()

    assert modifies(h) == [] and bracket.flatten_state == "failed"
    assert h.critical() == [
        "HELD: target 101 at IBKR is BUY MKT, not the engine's SELL LMT — not modified; act in TWS"
    ]


async def test_n4_a_capped_modify_alarms_only_when_the_gap_outlasts_the_grace(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    h.tws.position_subscribed = False
    bracket = await dead_partial_entry(h)  # the position update still trails the 40-share fill
    await h.engine.tick()
    assert modifies(h) == []  # capped to nothing while the account still shows 0
    h.clock.advance(2)
    h.tws.position_subscribed = True
    h.tws.move_position(HELD, 0)
    await h.engine.tick()

    # The bridge's v4 raised two CRITICALs for a position update that was a moment late.
    assert [(oid, sent.totalQuantity) for oid, sent in modifies(h)] == [(101, 40), (102, 40)]
    assert h.critical() == [] and bracket.resized_qty == 40


async def test_n4_a_persisting_gap_still_alarms_once_the_grace_is_over(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await dead_partial_entry(h, position=10)  # the owner sold 30 of the 40 by hand
    for _ in range(7):  # 0-12 s
        await h.engine.tick()
        h.clock.advance(2)
    assert [(oid, sent.totalQuantity) for oid, sent in modifies(h)] == [(101, 10), (102, 10)]
    assert h.critical() == []
    for _ in range(3):  # 14-18 s
        await h.engine.tick()
        h.clock.advance(2)
    assert h.critical()[:2] == [
        "HELD: account holds 10 sh but the bracket expects 40 — resize of target 101 limited to 10 sh; act in TWS",
        "HELD: account holds 10 sh but the bracket expects 40 — resize of stop 102 limited to 10 sh; act in TWS",
    ]
    assert len(modifies(h)) == 2  # capped totals TWS already shows are never re-sent


# ---- L3: a cancel lost while disconnected is sent again ---------------------------------------------


async def test_l3_a_parent_cancel_lost_while_disconnected_is_resent_after_a_restart(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await h.decide("WORK", con_id=HELD)
    await h.engine.tick()
    h.clock.advance(11 * 60)
    original = h.tws.cancelOrder
    h.tws.cancelOrder = lambda order_id: None  # type: ignore[method-assign]  # 9.81 drops it silently
    await h.engine.tick()
    assert h.bracket("WORK").parent_cancel_requested
    h.tws.cancelOrder = original  # type: ignore[method-assign]
    restarted = await h.restart()
    assert restarted.bracket("WORK").parent_cancel_requested is False
    await restarted.engine.tick()
    await restarted.engine.tick()

    assert h.tws.cancelled == [100]
    assert restarted.bracket("WORK").state == "cancelled"
    out = h.events()
    assert ("INFO", "entry WORK: still working at IBKR after an earlier cancel request — the cancel will be"
            " re-sent") in out
    assert ("CANCEL", "entry WORK: not filled in 10 min (filled 0/100)") in out


# ---- the session-end flatten -------------------------------------------------------------------------


async def test_flatten_reprices_the_target_once_and_later_checkpoints_only_alert(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws.fill(101, 40, "50.50")
    h.tws.quotes[HELD] = {"bid": 50.20}
    clock = Clock(ny(15, 44, 30))
    restarted = await h.restart(start=False, clock=clock)
    restarted.engine.pause = stepping(clock, 30)
    restarted.events()

    assert await restarted.engine.run() == EXIT_OK

    out = restarted.events()
    assert [text for kind, text in out if kind == "CRITICAL"] == ["HELD: still open after flatten (60 sh) — act in TWS"] * 3
    # One reprice of the target's own order id at 15:45; 15:46, 15:50 and 15:58 only alert.
    [(order_id, first)] = modifies(h)
    assert (order_id, first.action, first.totalQuantity, first.transmit, first.parentId) == (101, "SELL", 100, True, 100)
    assert first.lmtPrice == pytest.approx(49.94)  # 50.20 x 0.995
    assert restarted.bracket("HELD").flatten_state == "repriced"
    assert h.tws.cancelled == []
    assert clock.value >= ny(15, 58)
    assert ("INFO", "15:45 ET: flattening all engine positions") in out
    assert ("INFO", "session end: realized P&L 18.00") in out  # 40 x 0.50 less two 1.00 commissions


async def test_half_day_checkpoints_follow_the_1300_close(tmp_path, monkeypatch) -> None:
    from trading.execution.live_executor import session_times

    half_day = date(2026, 11, 27)
    times = session_times(half_day)
    assert (times.closes_at, times.last_entry, times.flatten, times.verify, times.stop) == tuple(
        datetime(2026, 11, 27, hour, minute, tzinfo=ny(0, 0).tzinfo) for hour, minute in
        ((13, 0), (12, 30), (12, 45), (12, 50), (12, 55))
    )
    assert session_times(date(2026, 12, 24)).flatten == ny(12, 45, day=date(2026, 12, 24))
    assert session_times(date(2026, 10, 13)).flatten == ny(15, 45)

    h = await build(tmp_path, monkeypatch, session_date=half_day)
    await open_bracket(h)
    clock = Clock(ny(12, 44, 30, day=half_day))
    restarted = await h.restart(start=False, clock=clock)
    restarted.engine.pause = stepping(clock, 30)
    late = await restarted.decide("LATE", con_id=7100, created=ny(12, 44, 30, day=half_day))
    restarted.events()

    assert await restarted.engine.run() == EXIT_OK

    assert [order_id for order_id, _ in modifies(h)] == [101]  # the 12:45 flatten
    out = restarted.events()
    assert ("INFO", "12:45 ET: flattening all engine positions") in out
    assert [text for kind, text in out if kind == "CRITICAL"] == ["HELD: still open after flatten (100 sh) — act in TWS"] * 3
    assert (await restarted.engine.ledger.outbox_row(late)).reason == "after 12:30 — no new entries"


async def test_n5_a_flatten_is_never_started_again_after_a_restart(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.clock.value = ny(15, 45, 1)

    def broken_quote(request_id: int, contract: Any, *args: Any) -> None:
        raise OSError("socket closed")  # the attempt dies before any order is sent

    h.tws.reqMktData = broken_quote  # type: ignore[method-assign]
    await h.engine.tick()
    assert bracket.flatten_attempted and modifies(h) == []
    assert any(text.startswith("HELD: flatten failed (OSError: socket closed)") for text in h.critical())

    del h.tws.reqMktData  # a quote exists again by the time the engine is restarted
    h.tws.quotes[HELD] = {"bid": 50.00}
    restarted = await h.restart(clock=Clock(ny(15, 47)))
    await restarted.engine.tick()

    assert modifies(h) == []  # the bridge's v4 repriced automatically after the restart
    assert restarted.critical() == ["HELD: still open after flatten (100 sh) — act in TWS"]


# ---- the flatten fallback: cancel both children, confirm, then one market sell ------------------------


async def test_flatten_falls_back_when_the_target_is_not_live(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])  # the target is gone (rejected or cancelled in TWS)
    h.clock.value = ny(15, 45, 1)
    h.events()
    await h.engine.tick()

    assert h.tws.cancelled == [102]  # the stop, only once its target is known dead
    [(order_id, sell)] = modifies(h)
    assert (order_id, sell.action, sell.orderType, sell.totalQuantity, sell.tif, sell.transmit) == (
        103, "SELL", "MKT", 100, "DAY", True
    )
    assert (sell.orderRef, sell.parentId, sell.account) == (f"at1:{bracket.id}:F", 0, ACCOUNT)
    assert bracket.flatten_state == "selling"
    h.tws.fill(103, 100, "49.90")
    await h.engine.tick()
    assert (bracket.state, bracket.exit_reason) == ("closed", "flatten")
    out = h.events()
    assert ("FLATTEN", "HELD: fallback market sell of 100 sh (order 103; ledger 100 sh, account 100 sh)") in out


async def test_flatten_falls_back_when_no_quote_can_price_the_reprice(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws.quotes[HELD] = {}  # no live bid and no screener snapshot
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert sorted(h.tws.cancelled) == [101, 102]
    assert [(oid, sent.orderType, sent.totalQuantity) for oid, sent in modifies(h)] == [(103, "MKT", 100)]


async def test_flatten_fallback_never_sells_until_both_cancels_are_confirmed(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {}
    h.tws.confirm_cancels = False  # TWS leaves both children PendingCancel
    h.clock.value = ny(15, 45, 1)
    h.events()
    for _ in range(20):
        await h.engine.tick()
        h.clock.advance(2)

    assert modifies(h) == []  # a sell while a child may still fill could sell twice
    assert bracket.flatten_state == "failed"
    assert ("HELD: TWS did not confirm cancelling the target and stop within 30s — no market sell sent; act in TWS"
            in h.critical(clear=False))
    h.tws.confirm_pending_cancels()  # confirmed too late: still no automatic sell
    await h.engine.tick()
    assert modifies(h) == []


async def test_flatten_never_sells_after_a_manual_close(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.move_position(HELD, -100)  # the owner closed the position by hand
    h.clock.value = ny(15, 45, 1)
    h.clock.advance(20)  # longer ago than the grace
    for _ in range(3):
        await h.engine.tick()
        h.clock.advance(5)

    assert modifies(h) == [] and h.tws.cancelled == []
    assert bracket.flatten_state == "skipped"


async def test_the_fallback_sell_leaves_the_owners_working_sells_their_shares(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    # The owner is closing by hand: a working SELL for all 100 in TWS (client 0).
    h.tws.add_foreign_order(client_id=0, order_id=0, con_id=HELD, symbol="HELD", action="SELL", quantity=100)
    h.clock.value = ny(15, 45, 1)
    h.events()
    await h.engine.tick()

    assert modifies(h) == []  # never a second sell racing the owner's close
    assert h.tws.cancelled == []  # and the protective stop is never cancelled for nothing
    assert bracket.flatten_state == "skipped"
    assert h.critical() == [
        "HELD: account holds 100 sh with 100 reserved by working sells — no market sell for the 100 sh the engine"
        " expects (its stop stays live); act in TWS if not flat"
    ]


async def test_the_fallback_sell_is_capped_by_the_live_position(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    h.tws.move_position(HELD, -30)  # the owner already sold 30 by hand
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()

    assert [(oid, sent.orderType, sent.totalQuantity) for oid, sent in modifies(h)] == [(103, "MKT", 70)]


async def test_an_unconfirmed_reprice_is_resent_at_the_same_price_then_falls_back(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.modify_mode = "silent"
    h.clock.value = ny(15, 45, 1)
    h.events()
    for _ in range(7):
        await h.engine.tick()
        h.clock.advance(2)

    reprices = [(oid, sent.orderType, sent.totalQuantity, sent.lmtPrice) for oid, sent in modifies(h)]
    assert reprices[:4] == [(101, "LMT", 100, pytest.approx(49.94))] * 4  # one quote, one price
    assert sorted(h.tws.cancelled) == [101, 102]
    assert reprices[4:] == [(103, "MKT", 100, pytest.approx(h.tws.placed[-1][1].lmtPrice))]
    assert bracket.flatten_state == "selling"
    assert ("HELD: TWS did not confirm the flatten of target 101 (100 sh @ 49.94) after 4 attempts — act in TWS"
            in h.critical())


async def test_a_fallback_in_progress_never_sells_twice_across_a_restart(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert [oid for oid, _ in modifies(h)] == [103] and bracket.flatten_state == "selling"

    restarted = await h.restart(clock=Clock(ny(15, 46)))
    for _ in range(3):
        await restarted.engine.tick()
    assert [oid for oid, _ in modifies(h)] == [103]
    h.tws.fill(103, 100, "49.90")
    await restarted.engine.tick()
    assert restarted.bracket("HELD").state == "closed"


async def test_a_restart_during_the_fallback_cancels_sends_them_again_then_sells_once(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws._cancel(h.tws.orders[(CLIENT_ID, 101)])
    original = h.tws.cancelOrder
    h.tws.cancelOrder = lambda order_id: None  # type: ignore[method-assign]  # the process dies before TWS hears it
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert bracket.flatten_state == "cancelling" and modifies(h) == []
    h.tws.cancelOrder = original  # type: ignore[method-assign]

    restarted = await h.restart(clock=Clock(ny(15, 45, 20)))
    await restarted.engine.tick()

    assert h.tws.cancelled == [102]
    assert [(oid, sent.orderType, sent.totalQuantity) for oid, sent in modifies(h)] == [(103, "MKT", 100)]
    for _ in range(3):
        await restarted.engine.tick()
    assert len(modifies(h)) == 1  # one market sell, ever


async def test_a_reprice_dropped_after_a_manual_close_is_never_followed_by_a_sell(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    bracket = await open_bracket(h)
    h.tws.quotes[HELD] = {"bid": 50.20}
    h.tws.modify_mode = "silent"
    h.clock.value = ny(15, 45, 1)
    await h.engine.tick()
    assert bracket.flatten_state == "repricing"
    h.tws.move_position(HELD, -100)  # the owner closed it by hand before TWS confirmed the reprice
    for _ in range(4):
        h.clock.advance(2)
        await h.engine.tick()

    assert bracket.flatten_state == "failed"
    assert [oid for oid, _ in modifies(h)] == [101] and h.tws.cancelled == []


async def test_exits_are_never_paused_by_a_reconciliation_mismatch(tmp_path, monkeypatch) -> None:
    h = await build(tmp_path, monkeypatch)
    await open_bracket(h)
    await h.decide("WORK", con_id=7200)
    await h.engine.tick()
    # An engine-tagged order the ledger does not know: entries pause.
    h.tws.add_foreign_order(client_id=CLIENT_ID, order_id=500, con_id=7300, symbol="STRAY", action="BUY",
                            quantity=1, order_ref="at1:0123456789ab:P")
    h.clock.advance(121)
    await h.engine.tick()
    assert h.engine.findings
    h.clock.value = ny(15, 45, 1)
    h.tws.quotes[HELD] = {"bid": 50.00}
    await h.engine.tick()

    assert 103 in h.tws.cancelled  # the WORK entry is still cancelled at its cutoff
    assert (101, 100) in [(oid, sent.totalQuantity) for oid, sent in h.tws.placed[6:]]  # the flatten still runs
